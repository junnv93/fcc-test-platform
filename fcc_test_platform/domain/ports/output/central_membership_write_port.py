"""Output port — central project_membership writer (FE-P8, 2026-05-28).

Membership management mutates ``project_membership`` (assign UPSERTs on the
unique composite key; revoke deletes) and MUST audit every change in the same
DB transaction so a successful mutation is durable iff its audit trail is.

The Port exposes two operations, each pairing the primary mutation with an
``audit_event`` record provided by the service layer. The adapter binds both
INSERTs into one transaction body (SERIALIZABLE on PostgreSQL, mirroring the
``central_claim_write`` discipline).

Membership is mutable state (not append-only) — the audit ledger is the
append-only twin that preserves the change history.

dependency-free: stdlib typing only (no infrastructure / pyvisa / openpyxl /
pandas / PySide6 / fastapi / sqlalchemy / psycopg imports).
"""
from __future__ import annotations

from typing import Mapping, Optional, Protocol, runtime_checkable


__all__ = [
    'MembershipWriteError',
    'CentralMembershipWritePort',
]


class MembershipWriteError(RuntimeError):
    """Raised when a membership write fails at infrastructure level (→ 503).

    Loud-fail; never a silent no-op that would leave the audit/primary write
    split (mutation succeeded but audit failed, or vice-versa).
    """


@runtime_checkable
class CentralMembershipWritePort(Protocol):
    """Mutate project_membership + emit audit, atomically."""

    def assign_role_with_audit(
        self,
        membership_record: Mapping,
        audit_record: Mapping,
    ) -> dict:
        """UPSERT the membership row, then INSERT the audit row, both atomic.

        ``membership_record`` provides ``id`` / ``project_id`` / ``user_id`` /
        ``role_key`` / ``assigned_at`` / ``expires_at`` / ``created_at``. On
        conflict with the unique (project_id, user_id, role_key) composite,
        the existing row's ``expires_at`` is updated to the new value and the
        ``id`` / ``created_at`` are preserved.

        ``audit_record`` carries the ``membership.assigned`` audit event with
        ``actor_subject``, target identity, etc. The adapter writes both rows
        in one transaction; a failure on either rolls the whole change back.

        Returns the resulting membership row dict (post-upsert).
        """
        ...

    def revoke_role_with_audit(
        self,
        project_id: str,
        user_id: str,
        role_key: str,
        audit_record: Mapping,
    ) -> Optional[dict]:
        """DELETE the membership row, then INSERT the audit, both atomic.

        Returns the deleted row dict (the pre-DELETE state) so the service can
        echo it in the API response. Returns ``None`` when no membership
        matches the (project, user, role) triple — the caller (service)
        translates that to a 404 ``MembershipNotFoundError`` WITHOUT emitting
        the audit row (don't audit a no-op).
        """
        ...
