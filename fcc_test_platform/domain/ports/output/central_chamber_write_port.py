"""Output port — central chamber registry / heartbeat writer (멀티챔버 P1).

챔버 노드는 (1) 자신을 중앙 레지스트리에 등록하고(``register_chamber``), (2) 주기적
heartbeat 를 중앙 ``chamber_heartbeat_events`` append-only ledger 에 적재한다
(``append_heartbeat``). 이 패턴은 ``CentralClaimWritePort`` 의 append-only ledger
계약을 챔버 가용성에 적용한 것이다.

Append-only by construction (schema ``chamber_heartbeat_events.append_only=true``):
heartbeat 는 INSERT 전용이며 과거 행을 갱신하지 않는다 — OFFLINE 은 저장되지 않고
heartbeat *부재/경과* 로 read-service 가 파생한다. ``register_chamber`` 만 레지스트리
upsert(가변)이다 (``providers``/``project_membership`` 와 동형).

생산 어댑터는 Phase 2 (``application/platform/central_chamber_write_adapter``)가
중앙 ``chamber_nodes``/``chamber_heartbeat_events`` 에 ``DbConnection`` factory 로
쓴다.

dependency-free: stdlib typing only (infrastructure / pyvisa / openpyxl / pandas /
PySide6 / fastapi / sqlalchemy / psycopg import 0).
"""
from __future__ import annotations

from typing import Mapping, Optional, Protocol, runtime_checkable


__all__ = [
    'ChamberNotFoundError',
    'ChamberWriteError',
    'CentralChamberWritePort',
]


class ChamberWriteError(RuntimeError):
    """Raised when a central chamber write fails at the infrastructure level —
    loud (→ 503), never a silent no-op.

    조용히 누락된 heartbeat 는 살아 있는 챔버를 OFFLINE 으로 보이게 해 측정 프록시가
    가용 노드를 거부하게 만들고, 조용히 누락된 등록은 챔버를 영영 보이지 않게 한다.
    항상 loud 로 올려 API 가 5xx 를 표면화하고 노드가 재시도하게 한다.

    **서버 장애 전용이다** — 클라이언트가 미등록 ``chamber_id`` 를 보낸 것은 여기 속하지
    않는다(:class:`ChamberNotFoundError` → 404). 둘을 섞으면 운영자가 "먼저 등록하라"는
    사실 앞에서 서버를 의심하며 시간을 버린다.
    """


class ChamberNotFoundError(LookupError):
    """대상 ``chamber_id`` 가 중앙 ``chamber_nodes`` 레지스트리에 없음 — 404 로 표면화.

    **정의 site 는 여기 하나다** (부채 청산 M2, 2026-07-30). 원래 application 계층
    ``chamber_measurement_service`` 안에 있었으나, ``append_heartbeat`` 를 구현하는
    어댑터도 같은 사실을 올려야 하므로 **포트가 자기 실패 모드를 선언**하는 자리로
    옮겼다 — 포트 계약에 없는 예외 타입을 어댑터가 던지면 그건 선언되지 않은 계약이다.
    ``chamber_measurement_service`` 는 기존 소비자 하위호환을 위해 re-export 한다
    (두 번째 클래스 정의 금지 — ``isinstance`` 기반 404 매핑이 조용히 갈라진다).

    이 사실의 판정은 **드라이버 메시지 문자열이 아니라 타입**으로 표현된다: 조건부
    ``INSERT ... WHERE EXISTS`` 가 0행을 쓰면(``DbCursor.rowcount == 0``) 부모 행이
    없다는 뜻이고, 어댑터가 그것을 이 예외로 승격한다.
    """


@runtime_checkable
class CentralChamberWritePort(Protocol):
    """Register chambers + append heartbeats to the central ledger."""

    def register_chamber(self, record: Mapping) -> dict:
        """챔버 노드를 중앙 ``chamber_nodes`` 레지스트리에 upsert 하고 그 행을 반환한다.

        ``record`` 는 ``chamber_id``(자연 키, unique) + ``name`` + ``base_url`` +
        ``enabled`` + ``heartbeat_ttl_seconds`` 를 담는다. 같은 ``chamber_id`` 재등록
        시 주소/이름을 갱신한다 (레지스트리는 ledger 가 아니라 가변 테이블).
        """
        ...

    def append_heartbeat(self, record: Mapping) -> None:
        """``chamber_heartbeat_events`` ledger 에 heartbeat 1건을 INSERT(append-only).

        ``record`` 의 ``reported_status`` 는 ``idle``/``in_use`` 만 허용한다(스키마
        CHECK + 도메인 :class:`Heartbeat` 검증). 과거 행을 절대 갱신하지 않는다 —
        최신 heartbeat 는 새 행으로 적재되고 ``chamber_availability`` VIEW 가
        chamber 별 최신 1건을 투영한다.

        **실패 모드는 둘로 갈린다** (부채 청산 M2): ``chamber_id`` 가 레지스트리에
        없으면 :class:`ChamberNotFoundError`(→ 404, 클라이언트가 고칠 수 있는 사실),
        연결/트랜잭션 실패는 :class:`ChamberWriteError`(→ 503, 서버 장애). 구현체는
        이 둘을 섞어서는 안 된다.
        """
        ...

    # ── 노드 설정 축 ───────────────────────────────────────────────────────────
    #
    # ⚠️ 2026-09-06 추가. 이 다섯은 **이미 서비스가 부르고 어댑터가 구현하고** 있었는데
    #    포트에는 선언돼 있지 않았다. 즉 이 프로토콜은 「등록 + heartbeat」 둘만
    #    약속하면서 실제로는 일곱을 요구했다 — 그 사이의 다섯은 **다른 구현체가
    #    빠뜨려도 타입 검사를 통과하고 런타임에 죽는** 자리였다.
    #    (mypy 가 `central_chamber_write_service.py` 에서 attr-defined 5건으로 짚었다.)
    #
    #    시그니처는 발명한 것이 아니라 서비스의 호출부에서 그대로 읽었다 —
    #    `_opt_text(...) -> Optional[str]` · `_opt_bool(...) -> Optional[bool]` ·
    #    `_require_equipment_patch(...) -> Mapping`.

    def update_chamber_storage_root(
        self, chamber_id: str, *, artifact_storage_root: Optional[str], updated_at: str,
    ) -> dict:
        """이 챔버의 산출물 저장 루트를 갱신하고 갱신된 행을 반환한다."""
        ...

    def update_chamber_web_session_approval(
        self, chamber_id: str, *, accepts_web_sessions: Optional[bool], updated_at: str,
    ) -> dict:
        """이 챔버가 웹 세션을 받는지 여부를 갱신하고 갱신된 행을 반환한다."""
        ...

    def read_chamber_settings(self, chamber_id: str) -> dict:
        """노드 범위 설정 1행. 알 수 없는 챔버는 «빈 답이 아니라» 404 로 갈린다."""
        ...

    def read_chamber_equipment_config(self, chamber_id: str) -> dict:
        """이 챔버의 계측기 연결 설정 1행."""
        ...

    def patch_chamber_equipment_config(
        self, chamber_id: str, *, patch: Mapping, updated_at: str,
    ) -> dict:
        """계측기 연결 설정을 부분 갱신하고 갱신된 행을 반환한다."""
        ...
