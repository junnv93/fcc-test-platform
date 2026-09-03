"""Central PostgreSQL users JIT-provisioning write adapter."""
from __future__ import annotations

from typing import Callable, Mapping, Optional

from fcc_test_contracts.common.identity import canonical_issuer
from fcc_test_platform.domain.ports.output.central_user_write_port import UserWriteError
from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'PostgresCentralUserWriteAdapter',
    'UPSERT_USER_SQL',
    'USER_INSERT_COLUMNS',
]


USER_INSERT_COLUMNS: tuple[str, ...] = (
    'id',
    'issuer',
    'subject',
    'display_name',
    'email',
    'enabled',
    'created_at',
    'updated_at',
)

UPSERT_USER_SQL = (
    'INSERT INTO "users" ("id", "issuer", "subject", "display_name", "email", '
    '"enabled", "created_at", "updated_at") VALUES (%s, %s, %s, %s, %s, %s, %s, %s) '
    'ON CONFLICT ("issuer", "subject") DO UPDATE SET '
    'display_name = COALESCE(NULLIF(EXCLUDED."display_name", \'\'), "users"."display_name"), '
    'email = COALESCE(NULLIF(EXCLUDED."email", \'\'), "users"."email"), '
    'updated_at = EXCLUDED."updated_at" '
    'RETURNING "id", "issuer", "subject", "display_name", "email", "enabled"'
)
_RETURNING_COLUMNS: tuple[str, ...] = (
    'id', 'issuer', 'subject', 'display_name', 'email', 'enabled',
)


class PostgresCentralUserWriteAdapter:
    """``CentralUserWritePort`` — idempotent users upsert on issuer+subject."""

    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def ensure_user(self, user_record: Mapping) -> dict:
        materialized = dict(user_record)
        materialized['issuer'] = canonical_issuer(materialized.get('issuer'))
        values = tuple(materialized.get(column) for column in USER_INSERT_COLUMNS)

        def _txn(cursor) -> dict:
            cursor.execute(UPSERT_USER_SQL, values)
            row = cursor.fetchone()
            if row is None:
                raise UserWriteError('central users upsert returned no row')
            return dict(zip(_RETURNING_COLUMNS, row))

        result = self._in_transaction(_txn)
        assert result is not None
        return result

    def _in_transaction(self, body: Callable[[object], Optional[dict]]) -> Optional[dict]:
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001
            raise UserWriteError(f'central users write connection failed: {exc}') from exc
        try:
            cursor = connection.cursor()
            try:
                result = body(cursor)
            finally:
                cursor.close()
            connection.commit()
            return result
        except UserWriteError:
            _safe_rollback(connection)
            raise
        except Exception as exc:  # noqa: BLE001
            _safe_rollback(connection)
            raise UserWriteError(f'central users write failed: {exc}') from exc
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()


def _safe_rollback(connection) -> None:
    rollback = getattr(connection, 'rollback', None)
    if callable(rollback):
        try:
            rollback()
        except Exception:  # noqa: BLE001
            pass
