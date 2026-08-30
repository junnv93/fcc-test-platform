"""Central PostgreSQL audit-ledger write adapter (FE-P8, 2026-05-28).

``PostgresCentralAuditWriteAdapter`` implements ``CentralAuditWritePort`` as a
transactional primitive: ``append_event_in_transaction(cursor, record)``
INSERTs one row into ``audit_events`` using the *caller's* open cursor — so
the audit row joins whatever transaction the primary write opened.

This is the critical atomicity contract: a successful claim acquire / release
or membership assign / revoke is durable iff its audit row is. A failed
audit INSERT raises ``AuditWriteError``; the caller propagates that to roll
the primary write back. There is no separate-transaction audit emit (which
could leak a primary-without-audit on a transient audit failure).

Append-only: only ``INSERT`` is emitted. The ``audit_events`` schema (see
``docs/platform/central_db_schema.v1.json``) also marks the table append-only
and adds a CHECK constraint on ``event_type`` for defense-in-depth.

The adapter takes no ``connection_factory`` — it does not own a transaction,
it joins one. (The schema/audit-only fields it owns: column SSOT + INSERT
SQL.) Composing it into the write adapters is the composition root's job.
"""
from __future__ import annotations

from typing import Mapping

from domain.ports.output.central_audit_write_port import AuditWriteError


__all__ = [
    'AUDIT_EVENTS_TABLE',
    'AUDIT_EVENT_COLUMNS',
    'INSERT_AUDIT_EVENT_SQL',
    'PostgresCentralAuditWriteAdapter',
]


AUDIT_EVENTS_TABLE = 'audit_events'


#: Audit row columns (docs/platform/central_db_schema.v1.json audit_events).
#: Order defines the INSERT column list + positional params.
AUDIT_EVENT_COLUMNS: tuple[str, ...] = (
    'id',
    'event_type',
    'project_id',
    'actor_subject',
    'target_user_subject',
    'target_claim_id',
    'role_key',
    'detail_json',
    'occurred_at',
    'created_at',
)


def _build_insert(table: str, columns: tuple[str, ...]) -> str:
    column_sql = ', '.join(f'"{column}"' for column in columns)
    placeholders = ', '.join(['%s'] * len(columns))
    return f'INSERT INTO "{table}" ({column_sql}) VALUES ({placeholders})'


INSERT_AUDIT_EVENT_SQL = _build_insert(AUDIT_EVENTS_TABLE, AUDIT_EVENT_COLUMNS)


class PostgresCentralAuditWriteAdapter:
    """``CentralAuditWritePort`` — transactional INSERT using caller's cursor.

    Holds no connection state: each ``append_event_in_transaction(cursor, ...)``
    runs the bound INSERT on the provided cursor. The caller owns the
    transaction lifetime.
    """

    def append_event_in_transaction(self, cursor: object, record: Mapping) -> None:
        values = tuple(record.get(column) for column in AUDIT_EVENT_COLUMNS)
        try:
            execute = getattr(cursor, 'execute')
        except AttributeError as exc:  # noqa: BLE001 — wrap as loud AuditWriteError
            raise AuditWriteError(
                f'audit cursor lacks execute(): {type(cursor).__name__}'
            ) from exc
        try:
            execute(INSERT_AUDIT_EVENT_SQL, values)
        except Exception as exc:  # noqa: BLE001 — wrap as loud AuditWriteError
            raise AuditWriteError(f'central audit INSERT failed: {exc}') from exc
