"""Central PostgreSQL test-report write adapter (Phase G, 2026-06-23).

``PostgresCentralReportWriteAdapter`` implements ``CentralReportWritePort``
against the central ``test_reports`` table. ``create_report`` inserts one row,
race-safe on the ``(project_id, edition)`` natural key via
``ON CONFLICT … DO NOTHING RETURNING id``: a duplicate edition returns ``None``
(the service maps it to a 409 conflict) rather than raising or silently
overwriting.

Design (mirrors ``PostgresCentralProjectWriteAdapter``):

- **injected ``connection_factory``** (``() -> RowConnection``).
- **``%s`` paramstyle** (psycopg).
- **loud-fail**: a connection/query failure raises ``CentralReportError``.
- single transaction; commit on success, rollback on failure.
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional, TypeVar

from fcc_test_platform.application.central_db_surfaces import (
    RowConnection,
    RowCursor,
)
from fcc_test_platform.domain.ports.output.central_report_port import CentralReportError
from fcc_test_platform.application.central_db_surfaces import RowConnection


__all__ = [
    'REPORT_INSERT_COLUMNS',
    'INSERT_REPORT_SQL',
    'PostgresCentralReportWriteAdapter',
]


REPORT_INSERT_COLUMNS: tuple[str, ...] = (
    'id',
    'project_id',
    'edition',
    'date_of_issue',
    'date_tested_start',
    'date_tested_end',
    'prepared_by',
    'prepared_site',
    'rev_history_json',
    'created_at',
    'updated_at',
)


def _build_insert(table: str, columns: tuple[str, ...]) -> str:
    column_sql = ', '.join(f'"{column}"' for column in columns)
    placeholders = ', '.join(['%s'] * len(columns))
    # ON CONFLICT on the (project_id, edition) unique natural key → DO NOTHING so
    # a duplicate edition is a no-op insert; RETURNING id yields zero rows on
    # conflict (the adapter maps no-row to None → service 409).
    return (
        f'INSERT INTO "{table}" ({column_sql}) VALUES ({placeholders}) '
        f'ON CONFLICT ("project_id", "edition") DO NOTHING RETURNING "id"'
    )


INSERT_REPORT_SQL = _build_insert('test_reports', REPORT_INSERT_COLUMNS)


_T = TypeVar('_T')


class PostgresCentralReportWriteAdapter:
    """``CentralReportWritePort`` — race-safe single-row insert."""

    def __init__(self, connection_factory: Callable[[], RowConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def create_report(self, report_record: Mapping) -> Optional[dict]:
        values = tuple(report_record.get(column) for column in REPORT_INSERT_COLUMNS)

        def _txn(cursor: RowCursor) -> Optional[dict]:
            cursor.execute(INSERT_REPORT_SQL, values)
            rows = list(cursor.fetchall())
            if not rows:
                # (project_id, edition) already exists — conflict, no insert.
                return None
            return {
                'report_id': report_record.get('id'),
                'edition': report_record.get('edition'),
            }

        return self._in_transaction(_txn)

    # ⚠️ 옛 선언은 ``Callable[[object], …]`` 이었다 — 「본문에 **아무거나** 넘긴다」는
    #    뜻이고, 그것은 거짓이다: 이 러너는 언제나 **커서**를 넘긴다. 그 거짓이
    #    본문의 인자를 ``RowCursor`` 로 적는 순간 드러난다(Callable 은 인자에 대해
    #    **반변**이므로 커서를 받는 본문은 object 를 받는 자리에 못 들어간다).
    #    반환도 마찬가지다 — 「넘긴 것을 그대로 돌려준다」가 이 함수가 하는 일이다.
    def _in_transaction(self, body: Callable[[RowCursor], _T]) -> _T:
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001
            raise CentralReportError(
                f'central report write connection failed: {exc}'
            ) from exc
        try:
            cursor = connection.cursor()
            try:
                result = body(cursor)
            finally:
                cursor.close()
            connection.commit()
            return result
        except CentralReportError:
            _safe_rollback(connection)
            raise
        except Exception as exc:  # noqa: BLE001
            _safe_rollback(connection)
            raise CentralReportError(f'central report write failed: {exc}') from exc
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()


def _safe_rollback(connection: RowConnection) -> None:
    rollback = getattr(connection, 'rollback', None)
    if callable(rollback):
        try:
            rollback()
        except Exception:  # noqa: BLE001 — never mask the original error
            pass
