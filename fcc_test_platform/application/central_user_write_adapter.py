"""Central PostgreSQL users JIT-provisioning write adapter."""
from __future__ import annotations

from typing import Callable, Mapping, Optional, TypeVar

from fcc_test_contracts.common.identity import canonical_issuer
from fcc_test_platform.application.central_db_surfaces import (
    RowConnection,
    RowCursor,
)
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


_T = TypeVar('_T')


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

        def _txn(cursor: RowCursor) -> dict:
            cursor.execute(UPSERT_USER_SQL, values)
            row = cursor.fetchone()
            if row is None:
                raise UserWriteError('central users upsert returned no row')
            return dict(zip(_RETURNING_COLUMNS, row, strict=True))

        result = self._in_transaction(_txn)
        assert result is not None
        return result

    # ⚠️ 옛 선언은 ``Callable[[object], …]`` 이었다 — 「본문에 **아무거나** 넘긴다」는
    #    뜻이고, 그것은 거짓이다: 이 러너는 언제나 **커서**를 넘긴다. 그 거짓이
    #    본문의 인자를 ``RowCursor`` 로 적는 순간 드러난다(Callable 은 인자에 대해
    #    **반변**이므로 커서를 받는 본문은 object 를 받는 자리에 못 들어간다).
    #    반환도 마찬가지다 — 「넘긴 것을 그대로 돌려준다」가 이 함수가 하는 일이다.
    def _in_transaction(self, body: Callable[[RowCursor], _T]) -> _T:
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


def _safe_rollback(connection: RowConnection) -> None:
    rollback = getattr(connection, 'rollback', None)
    if callable(rollback):
        try:
            rollback()
        except Exception:  # noqa: BLE001
            pass
