"""Output port — central audit ledger writer (FE-P8, 2026-05-28).

Every platform write (claim acquire/release + membership assign/revoke) MUST
append an audit row in the SAME DB transaction as the primary mutation. The
write adapters therefore take an injected ``CentralAuditWritePort`` whose
``append_event_in_transaction`` runs inside the open cursor — a failed audit
INSERT rolls the primary back, never a silent unaudited mutation.

The port surface is deliberately the transactional primitive (cursor-coupled),
not a free-standing "best-effort" emit, because security audit cannot tolerate
durability skew between the fact and its trail.

Append-only by construction: the only verb exposed is INSERT. The
``audit_events`` schema also forbids UPDATE/DELETE of historical rows (see
``docs/platform/central_db_schema.v1.json``).

dependency-free: stdlib typing only (no infrastructure / pyvisa / openpyxl /
pandas / PySide6 / fastapi / sqlalchemy / psycopg imports).
"""
from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable


__all__ = [
    'AuditWriteError',
    'CentralAuditWritePort',
]


class AuditWriteError(RuntimeError):
    """Raised when an audit append fails — loud, surfaces as 503.

    A silently-dropped audit row would let a privileged operator mutate the
    central state with no recorded actor — a security regression. Loud so the
    primary write rolls back and the operator retries.
    """


@runtime_checkable
class CentralAuditWritePort(Protocol):
    """Append rows to the central ``audit_events`` ledger atomically."""

    def append_event_in_transaction(self, cursor: object, record: Mapping) -> None:
        """INSERT one audit row using an existing transaction cursor.

        ``cursor`` is the open DB cursor from the primary write's transaction
        (a duck-typed object exposing ``execute(sql, params)``). The implementation
        runs the INSERT and returns; commit/rollback is the caller's responsibility
        — the audit INSERT joins whatever atomic boundary the primary write
        already opened. A failure raises ``AuditWriteError`` so the caller can
        roll the primary back.

        ``record`` keys mirror the ``audit_events`` columns (see schema). All
        non-required columns may be ``None`` — only ``event_type`` / ``actor_subject``
        / ``occurred_at`` / ``id`` / ``created_at`` are required.
        """
        ...
