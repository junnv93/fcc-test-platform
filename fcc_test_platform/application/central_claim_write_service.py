"""Platform claim write service (FE-P3-write, 2026-05-27).

``ClaimWriteService`` is the application boundary between the platform driving
adapter (``PlatformApiAdapter``) and the central claim ledger
(``CentralClaimWritePort``). It owns the *decision* logic that turns an atomic
ledger write into a stable platform outcome:

1. **boundary validation** — ``project_id`` / ``claim_id`` must be well-formed
   uuids and required fields non-empty; a bad value raises ``ValueError`` (→ 400)
   instead of reaching PostgreSQL as an opaque 5xx.
2. **acquire conflict** — the port's conditional insert returns the holding claim
   when the condition is already locked. Same operator ⇒ idempotent re-acquire
   (returns the existing claim, no duplicate ledger row); a *different* operator
   ⇒ ``ClaimConflictError`` (→ 409). This is the enforcement the FE-P3 read-only
   visualization could not provide.
3. **release pairing** — a release/expire must reference a still-open ``acquired``
   claim_id (FE-P0a ingestion_contract Rule
   ``claim_events_append_only_and_acquire_release_pairing``). The port returns
   ``None`` when no open claim matches (never acquired, or already closed) →
   ``ClaimPairingError`` (→ 409).

``condition_hash`` is passed through verbatim from the caller (the local
measurement path is the single hashing site) — never recomputed here.

dependency-free of infrastructure / FastAPI / SQL — only the domain port +
stdlib ``uuid`` / ``datetime``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from fcc_test_kernel.application.central_contract.envelope_helpers import (
    optional_text,
    require_uuid,
    text,
)
from fcc_test_platform.domain.ports.output.central_claim_write_port import CentralClaimWritePort


__all__ = [
    'ClaimConflictError',
    'ClaimPairingError',
    'ClaimWriteService',
]


class ClaimConflictError(RuntimeError):
    """A condition is already actively claimed by a *different* operator (→ 409).

    Not a ``ValueError`` — the request is well-formed; the central state simply
    forbids a second concurrent acquire. The cross-engineer duplicate-prevention
    guarantee is exactly this rejection.
    """


class ClaimPairingError(RuntimeError):
    """A release/expire references no still-open acquired claim (→ 409).

    Covers both "claim_id never acquired" and "already released/expired" — in
    either case appending another release would corrupt the append-only pairing
    invariant, so it is rejected loud rather than written.
    """


class ClaimWriteService:
    def __init__(
        self,
        write_port: CentralClaimWritePort,
        *,
        clock: Optional[Callable[[], str]] = None,
        id_factory: Optional[Callable[[], str]] = None,
    ) -> None:
        self._write = write_port
        self._clock = clock or _utcnow_iso
        self._id_factory = id_factory or _uuid4_str

    def acquire(
        self,
        project_id: str,
        *,
        technology: str,
        condition_hash: str,
        operator: str,
        session_id: Optional[str] = None,
        reason: Optional[str] = None,
        expires_at: Optional[str] = None,
        actor_subject: Optional[str] = None,
    ) -> dict:
        """Acquire a measurement lock for ``(project_id, condition_hash)``.

        Returns the ``ClaimEventEnvelope`` of the held claim. Raises
        ``ClaimConflictError`` when a *different* operator already holds it;
        re-acquiring one's own held claim is idempotent (returns the existing
        claim without a duplicate ledger row).

        FE-P8 (2026-05-28): ``actor_subject`` is the authenticated principal
        subject (the platform adapter derives it from the authz context, not
        the request body). When provided, an audit ``claim.acquired`` row is
        built and passed to the adapter — joining the SAME transaction as the
        claim INSERT. ``operator`` (the lock-holder identity) and
        ``actor_subject`` (the authenticated caller) are normally identical
        under the FE-P8 operator-provenance rule, but the model preserves the
        distinction so a future delegated path (admin acquires on behalf of
        an engineer) audits the actor without confusing the lock holder.
        """
        pid = require_uuid(project_id, 'project_id')
        tech = _require_text(technology, 'technology')
        cond = _require_text(condition_hash, 'condition_hash')
        op = _require_text(operator, 'operator')
        now = self._clock()
        record = {
            'id': self._id_factory(),
            'claim_id': self._id_factory(),
            'project_id': pid,
            'technology': tech,
            'condition_hash': cond,
            'operator': op,
            'action': 'acquired',
            'reason': _opt_text(reason),
            'occurred_at': now,
            'expires_at': _opt_text(expires_at),
            'session_id': _opt_text(session_id),
            'created_at': now,
        }
        audit_record = _audit_for_claim(
            event_type='claim.acquired',
            project_id=pid,
            claim_id=record['claim_id'],
            actor_subject=actor_subject or op,
            event_id=self._id_factory(),
            occurred_at=now,
            detail={'technology': tech, 'condition_hash': cond, 'operator': op},
        )
        contended = self._write.acquire_claim_if_unclaimed(record, audit_record)
        if contended is None:
            return _envelope(record, default_action='acquired')
        if _norm_operator(contended.get('operator')) == _norm_operator(op):
            # Idempotent re-acquire by the same operator — return the held claim.
            return _envelope(contended, default_action='acquired')
        raise ClaimConflictError(
            f"condition {cond!r} is already claimed by "
            f"{contended.get('operator')!r} (claim {contended.get('claim_id')!r})"
        )

    def release(
        self,
        project_id: str,
        claim_id: str,
        *,
        operator: Optional[str] = None,
        reason: Optional[str] = None,
        action: str = 'released',
        actor_subject: Optional[str] = None,
    ) -> dict:
        """Release (or expire) an open claim.

        ``action`` is ``'released'`` (operator-initiated) or ``'expired'``
        (sweeper). Returns the release event envelope; raises
        ``ClaimPairingError`` when no still-open claim with ``claim_id`` exists.

        FE-P8 (2026-05-28): ``actor_subject`` derives from the authenticated
        principal; audit row joins the release INSERT in the same transaction.
        """
        pid = require_uuid(project_id, 'project_id')
        cid = require_uuid(claim_id, 'claim_id')
        if action not in ('released', 'expired'):
            raise ValueError(f"action must be 'released' or 'expired', got {action!r}")
        now = self._clock()
        audit_event_type = 'claim.expired' if action == 'expired' else 'claim.released'
        audit_record = _audit_for_claim(
            event_type=audit_event_type,
            project_id=pid,
            claim_id=cid,
            actor_subject=actor_subject or operator or 'system',
            event_id=self._id_factory(),
            occurred_at=now,
            detail={'reason': _opt_text(reason)},
        )
        released = self._write.release_open_claim(
            pid,
            cid,
            event_id=self._id_factory(),
            operator=_opt_text(operator),
            reason=_opt_text(reason),
            occurred_at=now,
            created_at=now,
            action=action,
            audit_record=audit_record,
        )
        if released is None:
            raise ClaimPairingError(
                f'no open claim {claim_id!r} to {action} '
                '(never acquired, or already released/expired)'
            )
        return _envelope(released, default_action=action)


# ── helpers ────────────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    """Timezone-aware UTC ISO-8601 timestamp (seconds resolution) for occurred_at
    / created_at. A single SSOT clock so acquire/release stamps are consistent."""
    return datetime.now(timezone.utc).isoformat()


def _uuid4_str() -> str:
    return str(uuid.uuid4())


def _envelope(row: dict, *, default_action: str) -> dict:
    """Stable ``ClaimEventEnvelope`` (the OpenAPI contract + TS client shape).

    Works for both a freshly-built acquire/release record and a raw
    ``active_claims`` view row (idempotent re-acquire), which lacks
    ``action``/``reason`` — those default to ``default_action`` / ``None``.
    """
    return {
        'project_id': text(row.get('project_id')),
        'claim_id': text(row.get('claim_id')),
        'technology': text(row.get('technology')),
        'condition_hash': text(row.get('condition_hash')),
        'operator': text(row.get('operator')),
        'action': text(row.get('action')) or default_action,
        'occurred_at': text(row.get('occurred_at')),
        'expires_at': optional_text(row.get('expires_at')),
        'session_id': optional_text(row.get('session_id')),
        'reason': optional_text(row.get('reason')),
    }


def _require_text(value: object, label: str) -> str:
    """Required input field — strip + reject empty (write-input concern, distinct
    from the shared output ``text``)."""
    cleaned = '' if value is None else str(value).strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _opt_text(value: object) -> Optional[str]:
    """Optional input field — strip; empty-after-strip ⇒ None. Deliberately NOT
    the shared ``optional_text`` (which is an output pass-through, no strip)."""
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _norm_operator(value: object) -> str:
    """Case/space-insensitive operator identity for idempotent re-acquire."""
    return ' '.join(str(value or '').split()).upper()


def _audit_for_claim(
    *,
    event_type: str,
    project_id: str,
    claim_id: str,
    actor_subject: str,
    event_id: str,
    occurred_at: str,
    detail: dict,
) -> dict:
    """Build the audit_events row for a claim mutation (FE-P8).

    ``detail_json`` is a JSON-serialized snapshot of the user-visible context
    (technology, operator, reason) so the audit row reconstructs the event
    without re-joining claim_events.
    """
    import json
    return {
        'id': event_id,
        'event_type': event_type,
        'project_id': project_id,
        'actor_subject': actor_subject,
        'target_user_subject': None,
        'target_claim_id': claim_id,
        'role_key': None,
        'detail_json': json.dumps(detail, ensure_ascii=False, sort_keys=True),
        'occurred_at': occurred_at,
        'created_at': occurred_at,
    }
