"""Platform central chamber read service (멀티챔버 P2, 2026-06-15).

``CentralChamberReadService`` is the application boundary between the platform
driving adapter (``PlatformApiAdapter``) and the central chamber read model
(``CentralChamberReadPort``). It:

1. reads the verbatim ``chamber_availability`` view rows (reported_status /
   last_heartbeat_at / ttl exposed raw — the view never computes OFFLINE);
2. derives each chamber's effective availability (IDLE/IN_USE/OFFLINE) against an
   **injected clock** via :func:`domain.models.chamber_node.derive_chamber_status`
   — the SAME injected-clock pattern ``CentralReadService`` uses for ``expires_at``
   so availability tests stay deterministic (no DB-side ``now()``).
3. converts rows into stable platform envelopes (the shape the OpenAPI contract +
   generated TS client expect).

dependency-free of infrastructure / FastAPI / SQL — only the domain port/model +
stdlib ``datetime``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable, Optional

from fcc_test_kernel.application.central_contract.envelope_helpers import optional_text, parse_timestamp, text
from fcc_test_kernel.domain.models.chamber_node import (
    ChamberAvailability,
    ChamberNodeStatus,
    ChamberProgress,
    DEFAULT_HEARTBEAT_TTL_SECONDS,
    derive_chamber_status,
    derive_unavailable_reason,
    redact_error_message,
)
from fcc_test_platform.domain.ports.output.central_chamber_read_port import CentralChamberReadPort
from fcc_test_kernel.domain.services.chamber_mode_policy import judge_chamber_mode


__all__ = ['CentralChamberReadService']


class CentralChamberReadService:
    def __init__(
        self,
        read_port: CentralChamberReadPort,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._read = read_port
        # Injectable for deterministic availability tests; defaults to wall-clock
        # UTC. The API host clock derives OFFLINE — adequate for a small registry.
        self._clock = clock or _utcnow

    def chamber_availability(self) -> dict:
        """Availability of every registered chamber (Phase 6 dashboard source).

        Returns ``{'items': [ChamberAvailabilityEnvelope, ...], 'server_time': str}``.
        Each envelope carries the verbatim heartbeat fields PLUS the derived
        ``status`` (idle/in_use/offline) computed against ``server_time``.
        """
        now = self._clock()
        rows = self._read.read_chamber_availability()
        return {
            'items': [_availability_envelope(row, now) for row in rows],
            'server_time': now.isoformat(),
        }

    def chamber_nodes(self) -> dict:
        """Raw chamber registry rows (identity + base_url + ttl).

        Returns ``{'items': [ChamberNodeEnvelope, ...]}``. Used where only the
        registry (not the live availability) is needed.
        """
        rows = self._read.read_chamber_nodes()
        return {'items': [_node_envelope(row) for row in rows]}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_bool(value: object) -> bool:
    """Coerce a registry ``enabled`` column to bool (SQLite returns 0/1)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ('1', 'true', 't', 'yes')


def _coerce_optional_bool(value: object) -> Optional[bool]:
    """3-상태 boolean 해소 — ``None`` 이 살아남는다.

    ⚠️ **형제 ``_coerce_bool`` 을 재사용하면 안 된다.** 그것은 ``None`` 을 ``False`` 로
    접는데(``enabled`` 는 NOT NULL 이라 거기서는 옳다), 여기서 그렇게 하면 *"아무도
    판정하지 않았다"* 가 *"미승인"* 으로 **조용히 바뀐다**. 두 답은 운영자가 할 일이
    다르므로(판정하라 / 아무것도 하지 마라) 그 접힘은 이 축의 존재 이유를 지운다.

    빈 문자열도 ``None`` 이다 — 일부 드라이버가 NULL 을 그렇게 준다.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    token = str(value).strip().lower()
    if not token:
        return None
    if token in ('1', 'true', 't', 'yes'):
        return True
    if token in ('0', 'false', 'f', 'no'):
        return False
    # 알 수 없는 토큰은 '모름'이다 — 지어내지 않는다.
    return None


def _ttl_or_default(value: object) -> int:
    if value is None or value == '':
        return DEFAULT_HEARTBEAT_TTL_SECONDS
    return int(value)


def _parse_status(value: object) -> Optional[ChamberNodeStatus]:
    """Parse the verbatim ``reported_status`` token into a domain enum.

    ``None``/empty (zero-heartbeat chamber) → ``None`` (service derives OFFLINE).
    An unrecognized token also degrades to ``None`` so a freshness probe never
    500s on an odd ledger value (defensive — the schema CHECK already constrains
    writes to idle/in_use).
    """
    if value is None:
        return None
    token = str(value).strip()
    if not token:
        return None
    try:
        return ChamberNodeStatus(token)
    except ValueError:
        return None


def _parse_progress(value: object) -> Optional[ChamberProgress]:
    """Tolerant parse of the verbatim ``progress_json`` column → domain value.

    Accepts a JSON object string (ledger column), an already-decoded dict (some
    drivers return JSONB as dict), or NULL/empty → ``None``. A malformed value
    degrades to ``None`` so a freshness probe never 500s on an odd ledger value
    (defensive — the writer only ever stores ``ChamberProgress.as_dict()`` JSON).
    """
    if value is None:
        return None
    raw = value
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return None
        try:
            raw = json.loads(token)
        except (ValueError, TypeError):
            return None
    if not isinstance(raw, dict):
        return None
    return ChamberProgress.from_raw(raw)


def _parse_error(value: object) -> tuple[Optional[str], Optional[str]]:
    """Tolerant parse of the verbatim ``last_error_json`` column → (message, occurred_at).

    Accepts a JSON object string (``{"message": ..., "occurred_at": ...}``), an
    already-decoded dict, or NULL/empty → ``(None, None)``. A malformed value
    degrades to ``(None, None)`` so a freshness probe never 500s on an odd ledger
    value (defensive — the writer only ever stores the redacted payload). The
    message is re-run through the domain redaction SSOT as defense-in-depth so a
    legacy/unredacted ledger row never leaks secrets to the dashboard.
    """
    if value is None:
        return None, None
    raw = value
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return None, None
        try:
            raw = json.loads(token)
        except (ValueError, TypeError):
            return None, None
    if not isinstance(raw, dict):
        return None, None
    message = redact_error_message(raw.get('message'))
    occurred_at = optional_text(raw.get('occurred_at'))
    if message is None:
        return None, None
    return message, occurred_at


def _availability_envelope(row: dict, now: datetime) -> dict:
    ttl = _ttl_or_default(row.get('heartbeat_ttl_seconds'))
    reported = _parse_status(row.get('reported_status'))
    last_heartbeat = parse_timestamp(row.get('last_heartbeat_at'))
    progress = _parse_progress(row.get('progress_json'))
    last_error, last_error_at = _parse_error(row.get('last_error_json'))
    enabled = _coerce_bool(row.get('enabled'))
    # 챔버 모드 축 (2026-08-16) — **승인**은 중앙 등록부가 선언한 3-상태 사실이고,
    # **실현**은 아래에서 heartbeat 로 파생하는 관측이다. 판정은 둘을 맞추는 순수
    # 도메인 함수 하나가 소유한다.
    accepts_web_sessions = _coerce_optional_bool(row.get('accepts_web_sessions'))
    availability = ChamberAvailability(
        chamber_id=text(row.get('chamber_id')),
        name=text(row.get('name')),
        base_url=text(row.get('base_url')),
        enabled=enabled,
        reported_status=reported,
        last_heartbeat_at=last_heartbeat,
        heartbeat_ttl_seconds=ttl,
        session_id=optional_text(row.get('session_id')),
        progress=progress,
        last_error=last_error,
        last_error_at=last_error_at,
    )
    status = derive_chamber_status(
        reported_status=reported,
        last_heartbeat_at=last_heartbeat,
        now=now,
        ttl_seconds=ttl,
    )
    # M2 diagnostics — why is the chamber not usable? Derived against the SAME
    # injected clock as status, plus enabled. Orthogonal to status (a
    # disabled-but-heartbeating chamber keeps status=idle with reason=disabled),
    # which is exactly why it is not named offline_cause. null ⇒ usable.
    reason = derive_unavailable_reason(
        reported_status=reported,
        last_heartbeat_at=last_heartbeat,
        now=now,
        enabled=enabled,
        ttl_seconds=ttl,
    )
    # progress is exposed ONLY when the chamber is *currently* in_use. A chamber
    # that went OFFLINE mid-measurement still carries its last in_use progress in
    # the ledger, but surfacing that stale snapshot would mislead the dashboard —
    # the single get_chamber_measurement_progress endpoint is the fresh-fallback
    # for that one chamber. idle chambers carry no progress (domain invariant).
    expose_progress = availability.progress if status is ChamberNodeStatus.IN_USE else None
    return {
        'chamber_id': availability.chamber_id,
        'name': availability.name,
        'base_url': availability.base_url,
        'enabled': availability.enabled,
        'heartbeat_ttl_seconds': ttl,
        'reported_status': optional_text(row.get('reported_status')),
        'last_heartbeat_at': optional_text(row.get('last_heartbeat_at')),
        'session_id': optional_text(row.get('session_id')),
        'status': status.value,
        'progress': expose_progress.as_dict() if expose_progress is not None else None,
        # M2 diagnostics overlay — null last_error / last_error_at when the node
        # has not reported an error; unavailable_reason null when the chamber is
        # usable (enabled + fresh + idle/in_use).
        'last_error': availability.last_error,
        'last_error_at': availability.last_error_at,
        'unavailable_reason': reason.value if reason is not None else None,
        # 챔버 모드 축 — 승인(선언, 3-상태)과 대조 결과(파생)를 **나란히** 낸다.
        # 화면은 판정을 재계산하지 않는다(§Derived-Value No-Client-Recompute): 두 값이
        # 갈라지면 게이트가 막는 것과 화면이 보여주는 것이 달라진다.
        #
        # ⚠️ 관측(``observed_serving``)은 **파생 status** 로 판정한다 — ``reported_status``
        # 가 아니다. 노드가 마지막으로 idle 이라고 말한 뒤 죽었으면 그 값은 여전히
        # 'idle' 이고, 그것을 '서빙 중'으로 읽으면 꺼진 노드가 영원히 CONSISTENT 로 보인다.
        'accepts_web_sessions': accepts_web_sessions,
        'mode_verdict': judge_chamber_mode(
            accepts_web_sessions=accepts_web_sessions,
            observed_serving=status is not ChamberNodeStatus.OFFLINE,
        ).value,
    }


def _node_envelope(row: dict) -> dict:
    return {
        'chamber_id': text(row.get('chamber_id')),
        'name': text(row.get('name')),
        'base_url': text(row.get('base_url')),
        'enabled': _coerce_bool(row.get('enabled')),
        'heartbeat_ttl_seconds': _ttl_or_default(row.get('heartbeat_ttl_seconds')),
    }
