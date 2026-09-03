"""Central PostgreSQL membership write adapter (FE-P8, 2026-05-28).

``PostgresCentralMembershipWriteAdapter`` implements
``CentralMembershipWritePort`` against the central ``project_membership`` table.
Assign UPSERTs on the unique composite key (project_id, user_id, role_key);
revoke deletes by the same triple. Each primary mutation is paired in the
SAME transaction with an audit INSERT via the injected
``CentralAuditWritePort`` so the change + its audit trail are durable
together — a failed audit rolls the membership write back.

Design (mirrors ``PostgresCentralClaimWriteAdapter``):

- **injected ``connection_factory``** (``() -> DbConnection``).
- **SERIALIZABLE best-effort** on PostgreSQL; SQLite test fixture serializes
  via its single-connection model.
- **``%s`` paramstyle** (psycopg) for both INSERT and DELETE.
- **loud-fail**: a connection/query failure raises ``MembershipWriteError``.
- **atomic with audit**: assign/revoke both bind the audit INSERT inside the
  same ``_in_transaction`` body, before commit. The audit_writer is required
  (no silent no-audit path).
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional

from fcc_test_platform.application.central_rbac_read_adapter import (
    MEMBERSHIP_COLUMNS,
    PROJECT_MEMBERSHIP_TABLE,
    USERS_TABLE,
)
from fcc_test_platform.domain.ports.output.central_audit_write_port import CentralAuditWritePort
from fcc_test_platform.domain.ports.output.central_membership_write_port import MembershipWriteError
from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'DELETE_MEMBERSHIP_SQL',
    'MEMBERSHIP_INSERT_COLUMNS',
    'SELECT_MEMBERSHIP_WITH_SUBJECT_SQL',
    'UPSERT_MEMBERSHIP_SQL',
    'PostgresCentralMembershipWriteAdapter',
]


# Order defines the INSERT column list + positional params. The unique index
# (project_id, user_id, role_key) drives the UPSERT conflict target.
MEMBERSHIP_INSERT_COLUMNS: tuple[str, ...] = (
    'id',
    'project_id',
    'user_id',
    'role_key',
    # Phase D (2026-06-23) — 시험원 하위 team(RF/SAR) 분류 라벨(권한 직교, nullable).
    'team',
    'assigned_at',
    'expires_at',
    'created_at',
)


def _build_upsert() -> str:
    column_sql = ', '.join(f'"{column}"' for column in MEMBERSHIP_INSERT_COLUMNS)
    placeholders = ', '.join(['%s'] * len(MEMBERSHIP_INSERT_COLUMNS))
    return (
        f'INSERT INTO "{PROJECT_MEMBERSHIP_TABLE}" ({column_sql}) VALUES ({placeholders}) '
        f'ON CONFLICT ("project_id", "user_id", "role_key") DO UPDATE SET '
        f'"expires_at" = EXCLUDED."expires_at", "assigned_at" = EXCLUDED."assigned_at", '
        f'"team" = EXCLUDED."team"'
    )


UPSERT_MEMBERSHIP_SQL = _build_upsert()


DELETE_MEMBERSHIP_SQL = (
    f'DELETE FROM "{PROJECT_MEMBERSHIP_TABLE}" '
    f'WHERE "project_id" = %s AND "user_id" = %s AND "role_key" = %s'
)


#: Read-back of the post-UPSERT row joined to users (so the API response
#: includes the user_subject without a separate query).
SELECT_MEMBERSHIP_WITH_SUBJECT_SQL = (
    'SELECT "pm"."project_id", "u"."issuer" AS "user_issuer", '
    '"u"."subject" AS "user_subject", "pm"."role_key", '
    '"pm"."assigned_at", "pm"."expires_at", "pm"."team" '
    f'FROM "{PROJECT_MEMBERSHIP_TABLE}" pm '
    f'JOIN "{USERS_TABLE}" u ON u."id" = pm."user_id" '
    'WHERE "pm"."project_id" = %s AND "pm"."user_id" = %s AND "pm"."role_key" = %s'
)


class PostgresCentralMembershipWriteAdapter:
    """``CentralMembershipWritePort`` — UPSERT + DELETE + atomic audit."""

    def __init__(
        self,
        connection_factory: Callable[[], DbConnection],
        audit_writer: CentralAuditWritePort,
    ) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        if audit_writer is None:
            raise ValueError(
                'audit_writer is required — silently-unaudited membership '
                'mutations are forbidden (FE-P8 audit atomicity)'
            )
        self._connection_factory = connection_factory
        self._audit = audit_writer

    def assign_role_with_audit(
        self,
        membership_record: Mapping,
        audit_record: Mapping,
    ) -> dict:
        project_id = membership_record['project_id']
        user_id = membership_record['user_id']
        role_key = membership_record['role_key']
        upsert_values = tuple(
            membership_record.get(column) for column in MEMBERSHIP_INSERT_COLUMNS
        )

        def _txn(cursor) -> dict:
            cursor.execute(UPSERT_MEMBERSHIP_SQL, upsert_values)
            # Audit INSERT joins the same transaction — atomic with the UPSERT.
            self._audit.append_event_in_transaction(cursor, audit_record)
            cursor.execute(
                SELECT_MEMBERSHIP_WITH_SUBJECT_SQL,
                (project_id, user_id, role_key),
            )
            rows = list(cursor.fetchall())
            if not rows:
                # UPSERT-then-SELECT in the same transaction MUST find the row;
                # a missing row points at an inconsistent DB state and merits
                # loud failure.
                raise MembershipWriteError(
                    'membership UPSERT did not produce a readable row — '
                    'central state inconsistent'
                )
            return dict(zip(MEMBERSHIP_COLUMNS, rows[0]))

        return self._in_transaction(_txn)

    def revoke_role_with_audit(
        self,
        project_id: str,
        user_id: str,
        role_key: str,
        audit_record: Mapping,
    ) -> Optional[dict]:
        def _txn(cursor) -> Optional[dict]:
            # Read the row first so the response can echo the pre-delete state.
            cursor.execute(
                SELECT_MEMBERSHIP_WITH_SUBJECT_SQL,
                (project_id, user_id, role_key),
            )
            rows = list(cursor.fetchall())
            if not rows:
                # No matching membership — caller will raise MembershipNotFoundError
                # WITHOUT emitting the audit (don't audit a no-op revoke).
                return None
            existing = dict(zip(MEMBERSHIP_COLUMNS, rows[0]))
            cursor.execute(DELETE_MEMBERSHIP_SQL, (project_id, user_id, role_key))
            self._audit.append_event_in_transaction(cursor, audit_record)
            return existing

        return self._in_transaction(_txn)

    def _in_transaction(self, body: Callable[[object], Optional[dict]]) -> Optional[dict]:
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001
            raise MembershipWriteError(
                f'central membership write connection failed: {exc}'
            ) from exc
        try:
            cursor = connection.cursor()
            try:
                _set_serializable_best_effort(cursor)
                result = body(cursor)
            finally:
                cursor.close()
            connection.commit()
            return result
        except MembershipWriteError:
            _safe_rollback(connection)
            raise
        except Exception as exc:  # noqa: BLE001
            _safe_rollback(connection)
            raise MembershipWriteError(
                f'central membership write failed: {exc}'
            ) from exc
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()


def _set_serializable_best_effort(cursor) -> None:
    """Mirror of ``central_claim_write_adapter._set_serializable_best_effort``.

    PostgreSQL: SERIALIZABLE makes concurrent UPSERT-then-audit safe (the loser
    of a (project_id, user_id, role_key) collision aborts on commit with
    SQLSTATE 40001 — the caller retries logically via re-request).
    SQLite test fixture: rejects ``SET TRANSACTION``; swallowed because the
    single-connection model already serializes writes.
    """
    try:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE', ())
    except Exception:  # noqa: BLE001 — backend without SET TRANSACTION
        pass


def _safe_rollback(connection) -> None:
    rollback = getattr(connection, 'rollback', None)
    if callable(rollback):
        try:
            rollback()
        except Exception:  # noqa: BLE001 — never mask the original error
            pass
