"""Output port — central claim ledger writer (FE-P3-write, 2026-05-27).

The platform claim-lock UX (FE-P3) must *enforce* cross-engineer duplicate
prevention, not merely visualize it. That requires WRITING to the central
``claim_events`` append-only ledger — acquiring a lock before measuring a
condition, releasing it after. This is the dependency-free contract for that
writer; the production adapter
(``application/platform/central_claim_write_adapter.PostgresCentralClaimWriteAdapter``)
appends to the central ``claim_events`` table and reads the ``active_claims``
view through an injected ``DbConnection`` factory.

Append-only by construction (FE-P0a ingestion_contract Rule
``claim_events_append_only_and_acquire_release_pairing``): the Port exposes only
``*_claim`` append operations. Historical rows are NEVER updated — a release is a
new ``released`` row referencing the prior ``acquired`` claim_id. acquire/release
pairing is validated in the application service (``ClaimWriteService``), not by a
DB CHECK constraint (pairing is a temporal property — a CHECK would block
legitimate expiry sweepers).

Atomicity: ``acquire_claim_if_unclaimed`` performs the "is this condition already
claimed?" check + the conditional INSERT in a SINGLE transaction (SERIALIZABLE on
PostgreSQL) so two engineers cannot both observe "free" and both acquire — the
loser's commit fails and retries into a conflict. ``release_open_claim`` likewise
checks-and-appends atomically so a double-release is rejected.

dependency-free: stdlib typing only (no infrastructure / pyvisa / openpyxl /
pandas / PySide6 / fastapi / sqlalchemy / psycopg imports).
"""
from __future__ import annotations

from typing import Mapping, Optional, Protocol, runtime_checkable


__all__ = [
    'ClaimWriteError',
    'CentralClaimWritePort',
]


class ClaimWriteError(RuntimeError):
    """Raised when a central claim write fails at the infrastructure level —
    loud (→ 503), never a silent no-op.

    A silently-dropped acquire would let two engineers measure the same
    condition while the UI shows no lock; a silently-dropped release would leave
    a phantom lock blocking the rightful next measurer. Always raise loud so the
    API surfaces a 5xx and the operator retries rather than proceeding on a false
    belief about the lock state.
    """


@runtime_checkable
class CentralClaimWritePort(Protocol):
    """Append acquire/release events to the central ``claim_events`` ledger."""

    def acquire_claim_if_unclaimed(
        self,
        record: Mapping,
        audit_record: Optional[Mapping] = None,
    ) -> Optional[dict]:
        """Atomically acquire a claim only if the condition is not already held.

        Within a single (SERIALIZABLE) transaction: look up the open claim for
        ``(record['project_id'], record['condition_hash'])`` via ``active_claims``;
        if one is currently held, return that holding claim's row dict WITHOUT
        inserting; otherwise INSERT ``record`` as an ``acquired`` event and return
        ``None`` (meaning "inserted — the condition was free").

        FE-P8 (2026-05-28): when the adapter is wired with an audit writer
        AND ``audit_record`` is provided, the audit ``claim.acquired`` row
        joins the same transaction — atomic with the claim INSERT. The audit
        is emitted ONLY when the INSERT succeeds (contended return path
        skips audit because no state changed).

        The caller (``ClaimWriteService``) decides what a contended return means:
        same operator ⇒ idempotent re-acquire; different operator ⇒ conflict.
        """
        ...

    def release_open_claim(
        self,
        project_id: str,
        claim_id: str,
        *,
        event_id: str,
        operator: Optional[str],
        reason: Optional[str],
        occurred_at: str,
        created_at: str,
        action: str = 'released',
        audit_record: Optional[Mapping] = None,
    ) -> Optional[dict]:
        """Atomically append a release/expire event for an OPEN claim.

        Within a single transaction: look up the still-open claim
        ``(project_id, claim_id)`` via ``active_claims``; if none is open (the
        claim_id never acquired, or already released/expired) return ``None`` so
        the service raises a loud pairing error; otherwise INSERT a new
        ``action`` row copying technology/condition_hash/session_id from the open
        claim and return the released claim's row dict.
        """
        ...
