# domain/models/chamber_node.py
"""Chamber node registry + heartbeat availability 값 객체 (멀티챔버 P1).

챔버 = 독립 PC 노드. 각 노드는 중앙 허브로 heartbeat 를 push 하고, 중앙은
``chamber_heartbeat_events`` append-only ledger 에 적재한다. 노드의 가용성
(IDLE/IN_USE/OFFLINE)은 ``claim_events→active_claims`` 패턴과 동형으로 최신
heartbeat 에서 파생되며, OFFLINE 은 **저장되지 않고** 마지막 heartbeat 가 TTL 을
경과했는지(또는 heartbeat 전무)로 파생한다 — read-service 가 주입한 clock 기준
(DB-side ``now()`` 금지, ``active_claims``/``project_member_permissions`` 의
``expires_at`` injected-clock 처리와 동형).

본 모듈은 그 파생 *계약* 을 봉인하는 순수 도메인 계층이다. 실제 read service /
adapter / Platform API 는 Phase 2. stdlib only — ``infrastructure``/``pyvisa``/
``openpyxl``/``pandas``/``PySide6`` import 0 (``TestDomainPurity``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


__all__ = [
    'ChamberNodeStatus',
    'UnavailableReason',
    'ChamberProgress',
    'ChamberProgressEvent',
    'ChamberNode',
    'Heartbeat',
    'ChamberAvailability',
    'DEFAULT_HEARTBEAT_TTL_SECONDS',
    'MAX_LAST_ERROR_LENGTH',
    'derive_chamber_status',
    'derive_unavailable_reason',
    'redact_error_message',
]


# 노드가 TTL 초 동안 heartbeat 를 보내지 않으면 OFFLINE 으로 파생한다. 기본값
# SSOT — 챔버별 override 는 ``chamber_nodes.heartbeat_ttl_seconds`` 컬럼이 운반하되
# 미설정/시드 기본은 이 상수에서 파생한다(중앙 schema 에 magic number 미하드코딩).
DEFAULT_HEARTBEAT_TTL_SECONDS = 90


# 노드가 보고하는 ``last_error`` 메시지의 영속/노출 상한 길이 (M2). 운영자 진단
# 한 줄용 — 장황한 스택트레이스/덤프가 ledger 와 대시보드를 오염시키지 않도록
# 도메인 redaction SSOT 가 이 길이로 자른다(매직 넘버 하드코딩 금지).
MAX_LAST_ERROR_LENGTH = 500


class ChamberNodeStatus(Enum):
    """챔버 노드의 가용성 상태.

    IDLE/IN_USE 는 노드가 heartbeat 로 self-report 하는 운영 상태이고, OFFLINE 은
    **저장되지 않는 파생 상태** 다 — 마지막 heartbeat 가 TTL 을 경과(또는 heartbeat
    전무)하면 read-service 가 주입 clock 으로 파생한다.
    """

    IDLE = 'idle'
    IN_USE = 'in_use'
    OFFLINE = 'offline'

    @property
    def is_reportable(self) -> bool:
        """노드가 heartbeat 로 직접 보고할 수 있는 상태인지 (OFFLINE 은 파생 전용)."""
        return self is not ChamberNodeStatus.OFFLINE


class UnavailableReason(Enum):
    """챔버가 사용 불가한 *이유* — 운영자 진단 overlay (M2, 2026-06-20).

    :class:`ChamberNodeStatus` (idle/in_use/offline) 와 **직교(orthogonal)** 한
    별도 축이다. status 는 heartbeat freshness 로만 파생되므로 "왜 못 쓰는가"를
    설명하지 못한다 — 예: ``enabled=false`` 인데도 노드가 heartbeat 를 보내면
    status 는 ``idle`` 이지만 운영자에겐 "관리자가 비활성화함(disabled)" 이 실제
    사용 불가 사유다. 그래서 이 필드는 ``offline_cause`` 가 아니다(챔버가 offline
    이 아닐 수 있으므로 그 이름은 거짓이 된다) — 모든 not-usable 사유를 포괄한다.

    값은 사용 가능(usable)할 때 ``None`` 으로 파생된다(:func:`derive_unavailable_reason`).
    """

    #: 마지막 heartbeat 가 TTL 을 경과 — 노드 다운/네트워크 단절.
    HEARTBEAT_TIMEOUT = 'heartbeat_timeout'
    #: 운영자가 레지스트리에서 비활성화(``enabled=false``). status 와 무관한 overlay.
    DISABLED = 'disabled'
    #: 레지스트리에 등록은 됐으나 heartbeat 가 전무.
    NEVER_SEEN = 'never_seen'
    #: 분류 불가 — fresh heartbeat 이지만 reported_status 가 비정상 토큰 등.
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class ChamberProgress:
    """측정 진행 스냅샷 — heartbeat 가 in_use 일 때 운반하는 진행률 값 객체.

    노드 Session API 의 progress(``is_running``/``completed``/``total``/``ratio``)와
    동형인 도메인 wire shape SSOT. heartbeat 가 **in_use 일 때만** 운반된다 — idle
    노드는 진행 중인 측정이 없으므로 progress 가 없다(:class:`Heartbeat` 불변식).

    이 값 객체가 운반 progress 의 정규화(타입 강제 + 결측 기본값)를 단일 소유한다 —
    중앙 측정 프록시(:mod:`application.platform.chamber_measurement_service`)와
    노드→중앙 heartbeat 양쪽이 같은 SSOT 를 거쳐 ``raw dict ↔ 안정 wire dict`` 를
    왕복한다(C1 heartbeat-carried progress).
    """

    is_running: bool
    completed: int
    total: int
    ratio: float

    @classmethod
    def from_raw(cls, raw: Optional[dict]) -> Optional['ChamberProgress']:
        """raw progress dict → :class:`ChamberProgress` (빈 dict/``None`` → ``None``).

        결측 키는 0/False 기본으로 강제하고, 빈 dict/None 은 "진행 정보 없음"으로
        ``None`` 을 반환한다(idle / zero-heartbeat 챔버는 progress 미운반).

        타입이 깨진 필드 값(예: ``completed='x'``, ``total=[1,2]``, ``ratio={...}``)은
        coercion 이 실패하므로 전체를 "진행 정보 없음"(``None``)으로 degrade 한다 —
        가용성 freshness probe 가 비정상 ledger 값에 500 으로 터지지 않도록 한다.
        이 정규화(타입 강제 + degrade 정책)는 본 도메인 SSOT 가 단일 소유한다(소비
        service 에 field coercion 중복 금지).
        """
        if not isinstance(raw, dict) or not raw:
            return None
        try:
            return cls(
                is_running=bool(raw.get('is_running', False)),
                completed=int(raw.get('completed', 0) or 0),
                total=int(raw.get('total', 0) or 0),
                ratio=float(raw.get('ratio', 0.0) or 0.0),
            )
        except (TypeError, ValueError):
            return None

    @classmethod
    def zero(cls) -> 'ChamberProgress':
        """미진행(zero) 스냅샷 — ``is_running=False`` + 0 카운트."""
        return cls(is_running=False, completed=0, total=0, ratio=0.0)

    def as_dict(self) -> dict:
        """안정 wire dict (노드 progress / 측정 스냅샷 / 가용성 envelope 공통 shape)."""
        return {
            'is_running': self.is_running,
            'completed': self.completed,
            'total': self.total,
            'ratio': self.ratio,
        }


@dataclass(frozen=True)
class ChamberProgressEvent:
    """중앙 진행 릴레이(P7/B4)가 웹으로 fan-out 하는 단일 진행 이벤트 값 객체.

    C1 heartbeat-carried progress 의 **실시간 확장**(ADR-0015 Option B — 노드 push
    릴레이). 노드→중앙 heartbeat 가 in_use progress 를 운반하면(C1), 중앙 ingest 가
    이 이벤트를 :class:`ChamberProgressBroadcastPort` 로 publish 하고 중앙 WS fan-out
    엔드포인트가 웹 구독자에게 재발행한다. 폴링(P5)은 fallback 으로 보존된다.

    ``progress`` 는 항상 in_use(진행 중) 스냅샷이다 — 도메인 :class:`Heartbeat`
    불변식이 idle+progress 를 차단하므로, 이 이벤트가 만들어지는 시점엔 진행 중임이
    보장된다. ``occurred_at`` 은 ingest 가 쓴 ISO-8601 문자열(중앙 clock SSOT)을
    verbatim 운반한다(도메인은 wall clock 미접근).

    순수 도메인 계층 — stdlib only(:class:`ChamberProgress` 재사용). wire shape 는
    :meth:`as_wire` SSOT 가 단일 소유(브로드캐스터/ WS 직렬화가 동일 shape 공유).
    """

    chamber_id: str
    progress: ChamberProgress
    session_id: Optional[str] = None
    occurred_at: Optional[str] = None

    def as_wire(self) -> dict:
        """WS fan-out wire dict — ``{kind, chamber_id, progress, session_id, occurred_at}``.

        ``kind`` 는 웹 client 가 envelope 를 분기하는 토큰 SSOT(노드 Session API WS 의
        ``{kind, payload}`` 와 동형 — 다만 챔버 릴레이는 챔버 식별을 위해 1급 필드 운반).
        """
        return {
            'kind': 'chamber_progress',
            'chamber_id': self.chamber_id,
            'progress': self.progress.as_dict(),
            'session_id': self.session_id,
            'occurred_at': self.occurred_at,
        }


@dataclass(frozen=True)
class ChamberNode:
    """``chamber_nodes`` 레지스트리 행 — 챔버 노드의 정체성 + 연결 주소.

    중앙 허브가 측정 프록시 시 ``base_url`` 로 노드의 Session API 에 forward 한다
    (Phase 5). ``heartbeat_ttl_seconds`` 는 챔버별 offline 판정 TTL — 기본은
    :data:`DEFAULT_HEARTBEAT_TTL_SECONDS`.

    ``artifact_storage_root`` (plot-custody ②) 는 **그 챔버 PC 가 플롯을 써야 할
    곳**이다. 운영자가 웹에서 지정하고 노드가 부트에 pull 한다. ``None`` 이면 노드는
    오늘처럼 워크북 Save Data 'File Structure' 칸을 쓴다 — 기능 상실 0.

    이것이 챔버의 속성인 이유: 저장 위치는 **기계**에 딸린 사실이다. 같은 챔버에서
    누가 무엇을 측정하든 플롯은 같은 곳으로 가야 하고, 워크북 칸은 시험원이 손으로
    적는 자리라 로컬 경로가 들어오기 쉽다(그러면 증거가 챔버 PC 에만 남는다).
    """

    chamber_id: str
    name: str
    base_url: str
    enabled: bool = True
    heartbeat_ttl_seconds: int = DEFAULT_HEARTBEAT_TTL_SECONDS
    artifact_storage_root: Optional[str] = None


@dataclass(frozen=True)
class Heartbeat:
    """``chamber_heartbeat_events`` append-only ledger 의 단일 heartbeat push.

    ``reported_status`` 는 노드가 self-report 하는 운영 상태이므로 IDLE/IN_USE 만
    허용한다 — OFFLINE 은 보고 대상이 아니라 부재/경과로 파생되는 상태다.
    """

    chamber_id: str
    reported_status: ChamberNodeStatus
    occurred_at: datetime
    expires_at: Optional[datetime] = None
    session_id: Optional[str] = None
    progress: Optional[ChamberProgress] = None

    def __post_init__(self) -> None:
        if not self.reported_status.is_reportable:
            raise ValueError(
                'heartbeat reported_status 는 IDLE/IN_USE 만 허용한다 '
                '(OFFLINE 은 저장되지 않는 파생 상태)'
            )
        # progress 는 in_use heartbeat 만 운반한다 — idle 노드는 진행 중인 측정이
        # 없으므로 진행률을 보고할 수 없다(C1 heartbeat-carried progress 불변식).
        if self.progress is not None and self.reported_status is not ChamberNodeStatus.IN_USE:
            raise ValueError(
                'heartbeat progress 는 in_use 상태에서만 운반된다 '
                '(idle 노드는 진행 중인 측정이 없음)'
            )


def derive_chamber_status(
    *,
    reported_status: Optional[ChamberNodeStatus],
    last_heartbeat_at: Optional[datetime],
    now: datetime,
    ttl_seconds: int = DEFAULT_HEARTBEAT_TTL_SECONDS,
) -> ChamberNodeStatus:
    """최신 heartbeat 에서 챔버 가용성을 파생하는 SSOT (순수).

    - heartbeat 전무(``last_heartbeat_at`` None) 또는 reported_status None → OFFLINE
    - ``now - last_heartbeat_at > ttl_seconds`` → OFFLINE (노드 다운/네트워크 단절)
    - 그 외 → 노드가 보고한 ``reported_status`` (IDLE/IN_USE)

    ``now`` 는 read-service 가 주입하는 clock — 도메인은 DB-side ``now()`` 를 쓰지
    않아 가용성 파생이 결정적이다 (테스트 deterministic).
    """
    if last_heartbeat_at is None or reported_status is None:
        return ChamberNodeStatus.OFFLINE
    elapsed = (now - last_heartbeat_at).total_seconds()
    if elapsed > ttl_seconds:
        return ChamberNodeStatus.OFFLINE
    return reported_status


def derive_unavailable_reason(
    *,
    reported_status: Optional[ChamberNodeStatus],
    last_heartbeat_at: Optional[datetime],
    now: datetime,
    enabled: bool,
    ttl_seconds: int = DEFAULT_HEARTBEAT_TTL_SECONDS,
) -> Optional[UnavailableReason]:
    """챔버가 사용 불가한 이유를 파생하는 SSOT (순수) — 사용 가능하면 ``None``.

    :func:`derive_chamber_status` 와 **같은 주입 clock 입력**(reported_status /
    last_heartbeat_at / now / ttl)에 ``enabled`` 한 축을 더해 결정적으로 파생한다.
    이 규칙을 read service / API route / SQL / frontend 에 복제하지 않는다(도메인
    단일 소유). 우선순위:

    1. ``not enabled`` → DISABLED — 운영자의 의도적 비활성화가 최우선 진단 사유.
       status 와 직교하므로 disabled-but-heartbeating 챔버는 status=idle 을 유지한
       채 reason=disabled 가 공존한다(:func:`derive_chamber_status` 는 enabled 미고려).
    2. heartbeat 전무 → NEVER_SEEN.
    3. ``now - last_heartbeat_at > ttl`` → HEARTBEAT_TIMEOUT.
    4. fresh 이지만 reported_status 분류 불가(None) → UNKNOWN.
    5. 그 외(enabled + fresh + idle/in_use) → ``None`` (사용 가능).
    """
    if not enabled:
        return UnavailableReason.DISABLED
    if last_heartbeat_at is None:
        return UnavailableReason.NEVER_SEEN
    elapsed = (now - last_heartbeat_at).total_seconds()
    if elapsed > ttl_seconds:
        return UnavailableReason.HEARTBEAT_TIMEOUT
    if reported_status is None:
        return UnavailableReason.UNKNOWN
    return None


# Redaction 패턴 SSOT — 노드가 보고하는 ``last_error`` 메시지에서 운영 비밀/식별자를
# 제거(중앙 ledger + 대시보드 노출 전). 순서 중요: URL(`://` 포함) → VISA 리소스 →
# Windows/Unix 경로 → key=value 비밀 → 긴 불투명 토큰.
_REDACT_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r'\b[a-z][a-z0-9+.\-]*://\S+', re.IGNORECASE), '[redacted-url]'),
    (re.compile(r'\b(?:GPIB|TCPIP|USB|ASRL)\d*::\S+', re.IGNORECASE), '[redacted-device]'),
    (re.compile(r'[A-Za-z]:\\[^\s]+'), '[redacted-path]'),
    (re.compile(r'(?:/[\w.\-]+){2,}/?'), '[redacted-path]'),
    (re.compile(r'(?i)\b(token|bearer|secret|password|passwd|apikey|api[_-]?key)'
                r'["\']?\s*[=:]\s*\S+'), r'\1=[redacted]'),
    (re.compile(r'\b[A-Za-z0-9_\-]{20,}\b'), '[redacted]'),
)


def redact_error_message(message: Optional[str]) -> Optional[str]:
    """노드 보고 오류 문자열을 redaction + 한 줄 정규화 + 길이 상한 처리 (순수 SSOT).

    URL/VISA 리소스/파일 경로/key=value 비밀/긴 불투명 토큰을 placeholder 로 치환해
    중앙 ledger 영속 *전에* 비밀/식별자 누출을 막는다(plan §P0 redaction 요건).
    공백을 한 줄로 접고 :data:`MAX_LAST_ERROR_LENGTH` 로 자른다. 빈/``None`` → ``None``.
    """
    if message is None:
        return None
    text = ' '.join(str(message).split())
    if not text:
        return None
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    text = ' '.join(text.split())
    if len(text) > MAX_LAST_ERROR_LENGTH:
        text = text[:MAX_LAST_ERROR_LENGTH].rstrip()
    return text or None


@dataclass(frozen=True)
class ChamberAvailability:
    """``chamber_availability`` VIEW 행의 도메인 표현 (파생 전 verbatim + 파생 메서드).

    VIEW 는 ``reported_status`` / ``last_heartbeat_at`` / ttl 을 verbatim 노출하고
    OFFLINE 을 계산하지 않는다 (DB-side ``now()`` 금지). 가용성은
    :meth:`effective_status` 가 주입 clock 으로 파생한다.
    """

    chamber_id: str
    name: str
    base_url: str
    enabled: bool
    reported_status: Optional[ChamberNodeStatus]
    last_heartbeat_at: Optional[datetime]
    heartbeat_ttl_seconds: int = DEFAULT_HEARTBEAT_TTL_SECONDS
    session_id: Optional[str] = None
    # 최신 heartbeat 가 운반한 진행률(in_use 일 때만 비어있지 않음). 가용성 read
    # service 가 verbatim VIEW 의 progress_json 을 파싱해 채운다(노출은 in_use 게이트).
    progress: Optional[ChamberProgress] = None
    # M2 진단 overlay — 노드가 마지막으로 보고한 (redacted) 운영 오류 메시지 + 관측
    # 시각. read service 가 verbatim VIEW 의 last_error_json 을 파싱해 채운다(노드가
    # 보고하지 않으면 None). ``unavailable_reason`` 은 저장하지 않고 파생한다.
    last_error: Optional[str] = None
    last_error_at: Optional[str] = None

    def effective_status(self, now: datetime) -> ChamberNodeStatus:
        """주입 clock ``now`` 기준 가용성 (:func:`derive_chamber_status` 위임 SSOT)."""
        return derive_chamber_status(
            reported_status=self.reported_status,
            last_heartbeat_at=self.last_heartbeat_at,
            now=now,
            ttl_seconds=self.heartbeat_ttl_seconds,
        )

    def unavailable_reason(self, now: datetime) -> Optional[UnavailableReason]:
        """주입 clock ``now`` 기준 사용 불가 사유 (:func:`derive_unavailable_reason` 위임)."""
        return derive_unavailable_reason(
            reported_status=self.reported_status,
            last_heartbeat_at=self.last_heartbeat_at,
            now=now,
            enabled=self.enabled,
            ttl_seconds=self.heartbeat_ttl_seconds,
        )
