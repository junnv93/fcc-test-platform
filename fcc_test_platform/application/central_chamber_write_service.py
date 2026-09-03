"""Platform chamber write service (멀티챔버 P2, 2026-06-15).

``CentralChamberWriteService`` is the application boundary between the platform
driving adapter (``PlatformApiAdapter``) and the central chamber registry +
heartbeat ledger (``CentralChamberWritePort``). It owns the *decision* logic that
turns a registry upsert / heartbeat append into a stable platform outcome:

1. **boundary validation** — required text fields (chamber_id / name / base_url)
   are non-empty; a bad value raises ``ValueError`` (→ 400) instead of reaching
   PostgreSQL as an opaque 5xx.
2. **heartbeat status integrity** — the reported status is parsed through the
   domain :class:`Heartbeat` value object, which rejects OFFLINE (OFFLINE is a
   derived state, never reported) and rejects unknown tokens — so the ledger only
   ever stores idle/in_use (mirrors the schema CHECK at the application boundary).

``heartbeat_ttl_seconds`` defaults to the domain ``DEFAULT_HEARTBEAT_TTL_SECONDS``
SSOT (no magic number here).

dependency-free of infrastructure / FastAPI / SQL — only the domain port/model +
stdlib ``uuid`` / ``datetime`` / ``json``.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Callable, Mapping, Optional

from fcc_test_kernel.application.central_contract.envelope_helpers import text
from fcc_test_kernel.domain.models.chamber_node import (
    DEFAULT_HEARTBEAT_TTL_SECONDS,
    ChamberNode,
    ChamberNodeStatus,
    ChamberProgress,
    Heartbeat,
    redact_error_message,
)
from domain.ports.output.central_chamber_write_port import CentralChamberWritePort


__all__ = ['CentralChamberWriteService']


class CentralChamberWriteService:
    def __init__(
        self,
        write_port: CentralChamberWritePort,
        *,
        clock: Optional[Callable[[], str]] = None,
        id_factory: Optional[Callable[[], str]] = None,
    ) -> None:
        self._write = write_port
        self._clock = clock or _utcnow_iso
        self._id_factory = id_factory or _uuid4_str

    def register(
        self,
        *,
        chamber_id: str,
        name: str,
        base_url: str,
        enabled: bool = True,
        heartbeat_ttl_seconds: Optional[int] = None,
        artifact_storage_root: Optional[str] = None,
    ) -> dict:
        """Register (or refresh) a chamber node in the central registry.

        Returns the ``ChamberNodeEnvelope`` of the written row. Idempotent on
        ``chamber_id`` — re-registering refreshes name/base_url/ttl.

        ``artifact_storage_root`` is **fill-only** here: a node self-registers on
        every boot and never knows where it is supposed to write, so omitting it
        must not blank what an operator configured. Explicit edit and clear live
        in ``set_artifact_storage_root``.
        """
        cid = _require_text(chamber_id, 'chamber_id')
        node = ChamberNode(
            chamber_id=cid,
            name=_require_text(name, 'name'),
            base_url=_require_text(base_url, 'base_url'),
            enabled=bool(enabled),
            heartbeat_ttl_seconds=_resolve_ttl(heartbeat_ttl_seconds),
            artifact_storage_root=_opt_text(artifact_storage_root),
        )
        now = self._clock()
        record = {
            'id': self._id_factory(),
            'chamber_id': node.chamber_id,
            'name': node.name,
            'base_url': node.base_url,
            'enabled': node.enabled,
            'heartbeat_ttl_seconds': node.heartbeat_ttl_seconds,
            'artifact_storage_root': node.artifact_storage_root,
            'created_at': now,
            'updated_at': now,
        }
        written = self._write.register_chamber(record)
        return _node_envelope(written or record)

    def set_artifact_storage_root(
        self, *, chamber_id: str, artifact_storage_root: Optional[str],
    ) -> dict:
        """Set (or clear) where this chamber's PC writes measurement plots.

        Unlike registration this writes the value **verbatim**, ``None`` included:
        the operator is stating what the value should be, and "no root configured"
        is a legitimate answer (the node then falls back to the workbook cell,
        which is today's behaviour).

        The value is not validated against a shape here. Classification lives in
        the node's ``storage_root_policy`` and it deliberately refuses to
        hardcode server names — a central allow-list would be wrong for the same
        reason: it goes stale the moment the company adds a file server, and it
        goes stale silently.
        """
        cid = _require_text(chamber_id, 'chamber_id')
        written = self._write.update_chamber_storage_root(
            cid,
            artifact_storage_root=_opt_text(artifact_storage_root),
            updated_at=self._clock(),
        )
        return _node_envelope(written or {})

    def set_web_session_approval(
        self, *, chamber_id: str, accepts_web_sessions: Optional[bool],
    ) -> dict:
        """운영자가 이 챔버의 **웹 세션 승인**을 선언한다 (챔버 모드 축, 2026-08-16).

        3-상태를 **verbatim** 으로 쓴다 — ``None`` 은 판정 철회(*"아무도 결정하지 않음"*)
        이고 명시적 ``False`` 와 다르다. 형제 ``set_artifact_storage_root`` 가 ``None`` 을
        "루트 없음"이라는 정당한 답으로 쓰는 것과 같은 규율이다.

        ⚠️ **이것은 승인 축이지 실현 축이 아니다.** 노드가 실제로 리스너를 열었는지는
        heartbeat 가 답하고, 둘의 **불일치가 곧 신호**다(``chamber_mode_policy``).
        운영자가 여기서 true 를 적는다고 그 챔버가 웹으로 도는 것은 아니다.

        ⚠️ **게이트가 아니다.** false 로 적어도 이 값 때문에 거부되는 측정·세션·유입은
        없다 — 시범 단계에 과하다(운영자 판정 2026-08-16).
        """
        cid = _require_text(chamber_id, 'chamber_id')
        written = self._write.update_chamber_web_session_approval(
            cid,
            accepts_web_sessions=_opt_bool(accepts_web_sessions),
            updated_at=self._clock(),
        )
        return _node_envelope(written or {})

    def get_settings(self, chamber_id: str) -> dict:
        """What this chamber node must be configured with (node-scoped read)."""
        cid = _require_text(chamber_id, 'chamber_id')
        row = self._write.read_chamber_settings(cid)
        return {
            'chamber_id': _opt_text(row.get('chamber_id')) or cid,
            'artifact_storage_root': _opt_text(row.get('artifact_storage_root')),
            'equipment_config': dict(row.get('equipment_config') or {}),
        }

    def get_equipment_config(self, chamber_id: str) -> dict:
        """This chamber's instrument connection settings (operator-scoped read)."""
        cid = _require_text(chamber_id, 'chamber_id')
        row = self._write.read_chamber_equipment_config(cid)
        return _equipment_config_envelope(row, cid)

    def patch_equipment_config(
        self, *, chamber_id: str, equipment_config: Mapping,
    ) -> dict:
        """Merge an operator's edits into this chamber's instrument settings.

        The patch is per key — absent leaves a key alone, ``None`` deletes it,
        a string sets it (RFC 7396 restricted to one level). The merge itself is
        performed by the write port inside a single locked transaction; doing it
        here would put a read and a write in two transactions and lose one of
        two concurrent edits without any error.

        Values are validated at this boundary rather than deferred to the
        database. Without it a wrong-typed value becomes a driver error and is
        reported as a 503 — telling the operator the backend is down when the
        real answer is that their request was malformed.
        """
        cid = _require_text(chamber_id, 'chamber_id')
        patch = _require_equipment_patch(equipment_config)
        written = self._write.patch_chamber_equipment_config(
            cid, patch=patch, updated_at=self._clock(),
        )
        return _equipment_config_envelope(written, cid)

    def heartbeat(
        self,
        *,
        chamber_id: str,
        reported_status: str,
        session_id: Optional[str] = None,
        expires_at: Optional[str] = None,
        detail: Optional[dict] = None,
        progress: Optional[dict] = None,
        last_error: Optional[str] = None,
    ) -> dict:
        """Append a heartbeat to the central ledger.

        ``reported_status`` must be ``idle`` or ``in_use`` — OFFLINE (or any
        unknown token) raises ``ValueError`` (→ 400) via the domain
        :class:`Heartbeat` validation. ``progress`` is the node's measurement
        progress snapshot; it is carried ONLY on ``in_use`` heartbeats — passing
        progress with an idle heartbeat raises ``ValueError`` (→ 400) via the
        domain invariant (idle nodes have no running measurement). Persisted as
        the verbatim ``progress_json`` ledger column so the availability VIEW can
        expose every chamber's live progress in a single read (C1).

        ``last_error`` (M2) is the node's latest operational error message. It is
        OPTIONAL on any status (an error is orthogonal to progress) and is
        redacted via the domain SSOT (URLs/tokens/paths/device ids stripped) and
        persisted as the ``last_error_json`` ``{message, occurred_at}`` payload
        BEFORE it reaches the ledger — no secret/identifier leaks downstream.
        Returns a ``ChamberHeartbeatAck`` envelope.
        """
        cid = _require_text(chamber_id, 'chamber_id')
        status = _parse_reported_status(reported_status)
        # ChamberProgress SSOT normalizes the raw dict (empty/None → None so an
        # idle heartbeat with no progress passes the domain invariant cleanly).
        progress_value = ChamberProgress.from_raw(progress)
        # M2 — redact secrets/identifiers at the write boundary (domain SSOT) so
        # the ledger only ever stores a safe, one-line message.
        redacted_error = redact_error_message(last_error)
        now = self._clock()
        # Domain validation: Heartbeat.__post_init__ rejects OFFLINE (derived-only)
        # AND progress-on-non-in_use (C1 invariant). occurred_at is derived from the
        # SAME injected clock as the stored record below — parsing ``now`` (the
        # clock's ISO-8601 value) keeps the validation timestamp and the persisted
        # ``occurred_at`` on a single clock, never the wall clock (Codex P2 follow-up
        # — no ``datetime.now()`` divergence).
        Heartbeat(
            chamber_id=cid,
            reported_status=status,
            occurred_at=_parse_clock_dt(now),
            session_id=_opt_text(session_id),
            progress=progress_value,
        )
        record = {
            'id': self._id_factory(),
            'chamber_id': cid,
            'reported_status': status.value,
            'session_id': _opt_text(session_id),
            'occurred_at': now,
            'expires_at': _opt_text(expires_at),
            'detail_json': json.dumps(detail, ensure_ascii=False, sort_keys=True) if detail else None,
            'progress_json': (
                json.dumps(progress_value.as_dict(), ensure_ascii=False, sort_keys=True)
                if progress_value is not None else None
            ),
            'last_error_json': (
                json.dumps(
                    {'message': redacted_error, 'occurred_at': now},
                    ensure_ascii=False, sort_keys=True,
                )
                if redacted_error is not None else None
            ),
            'created_at': now,
        }
        self._write.append_heartbeat(record)
        return {
            'chamber_id': cid,
            'reported_status': status.value,
            'occurred_at': now,
            'session_id': _opt_text(session_id),
        }


# ── helpers ────────────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    """Timezone-aware UTC ISO-8601 timestamp (seconds resolution) — SSOT clock."""
    return datetime.now(timezone.utc).isoformat()


def _parse_clock_dt(value: str) -> datetime:
    """Parse the injected clock's ISO-8601 value back into a ``datetime``.

    The clock SSOT (:func:`_utcnow_iso`, and every test-injected clock) emits
    ``datetime.isoformat()``; parsing it here lets the domain ``Heartbeat``
    validation share the SAME timestamp as the persisted ``occurred_at`` instead
    of reading a divergent wall clock. The clock contract guarantees an ISO-8601
    string, so :meth:`datetime.fromisoformat` round-trips it exactly."""
    return datetime.fromisoformat(value)


def _uuid4_str() -> str:
    return str(uuid.uuid4())


def _resolve_ttl(value: Optional[int]) -> int:
    if value is None:
        return DEFAULT_HEARTBEAT_TTL_SECONDS
    ttl = int(value)
    if ttl <= 0:
        raise ValueError('heartbeat_ttl_seconds must be a positive integer')
    return ttl


def _parse_reported_status(value: object) -> ChamberNodeStatus:
    """Parse the request token into a domain status (loud on unknown).

    Accepts only the enum value tokens (``idle``/``in_use``/``offline``); an
    unknown token raises ``ValueError`` (→ 400). OFFLINE parses here but the
    domain ``Heartbeat`` rejects it downstream (derived-only state)."""
    token = '' if value is None else str(value).strip()
    if not token:
        raise ValueError('reported_status is required (idle | in_use)')
    try:
        return ChamberNodeStatus(token)
    except ValueError as exc:
        raise ValueError(
            f'reported_status must be one of idle/in_use, got {value!r}'
        ) from exc


def _require_text(value: object, label: str) -> str:
    cleaned = '' if value is None else str(value).strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _opt_text(value: object) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _require_equipment_patch(value: object) -> dict:
    """Validate the per-key patch at the boundary (→ 400, never a 503).

    Keys must be non-empty text; values must be text or ``None`` (delete). The
    platform does not know which keys are meaningful — that is the provider's
    descriptor's business — so this checks shape only and never membership.
    Rejecting an unknown key here would put provider vocabulary in the shared
    lane, which is precisely what this axis is built to avoid.
    """
    if not isinstance(value, Mapping):
        raise ValueError('equipment_config must be an object')
    patch: dict = {}
    for key, item in value.items():
        name = str(key).strip()
        if not name:
            raise ValueError('equipment_config keys must be non-empty')
        if item is None:
            patch[name] = None
        elif isinstance(item, str):
            patch[name] = item.strip()
        else:
            raise ValueError(
                f'equipment_config[{name!r}] must be a string or null'
            )
    return patch


def _equipment_config_envelope(row: Mapping, chamber_id: str) -> dict:
    return {
        'chamber_id': _opt_text(row.get('chamber_id')) or chamber_id,
        'equipment_config': dict(row.get('equipment_config') or {}),
        'updated_at': _opt_text(row.get('updated_at')),
    }


def _opt_bool(value: object) -> Optional[bool]:
    """3-상태 boolean — ``None`` 이 살아남는다 (형제 ``_opt_text`` 와 같은 역할).

    ⚠️ ``bool(value)`` 로 접으면 안 된다. ``enabled`` 는 NOT NULL 이라 위에서 그렇게
    해도 옳지만, 승인 칸은 nullable 이고 ``None`` 은 **판정 부재**라는 값이다.
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
    return None


def _node_envelope(row: dict) -> dict:
    return {
        'chamber_id': text(row.get('chamber_id')),
        'name': text(row.get('name')),
        'base_url': text(row.get('base_url')),
        'enabled': bool(row.get('enabled')),
        'heartbeat_ttl_seconds': _resolve_ttl(row.get('heartbeat_ttl_seconds')),
        'artifact_storage_root': _opt_text(row.get('artifact_storage_root')),
        # 챔버 모드 축 — 3-상태 그대로 나른다. ⚠️ ``bool(...)`` 로 접으면 *"아무도
        # 판정하지 않았다"* 가 *"미승인"* 이 되고, 그 둘은 운영자가 할 일이 다르다.
        'accepts_web_sessions': _opt_bool(row.get('accepts_web_sessions')),
    }
