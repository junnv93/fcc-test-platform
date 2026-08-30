"""Central PostgreSQL test-report write adapter (Phase G, 2026-06-23).

``PostgresCentralReportWriteAdapter`` implements ``CentralReportWritePort``
against the central ``test_reports`` table. ``create_report`` inserts one row,
race-safe on the ``(project_id, edition)`` natural key via
``ON CONFLICT … DO NOTHING RETURNING id``: a duplicate edition returns ``None``
(the service maps it to a 409 conflict) rather than raising or silently
overwriting.

Design (mirrors ``PostgresCentralProjectWriteAdapter``):

- **injected ``connection_factory``** (``() -> DbConnection``).
- **``%s`` paramstyle** (psycopg).
- **loud-fail**: a connection/query failure raises ``CentralReportError``.
- single transaction; commit on success, rollback on failure.
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional

from domain.ports.output.central_report_port import CentralReportError
from domain.ports.output.platform_database_port import DbConnection


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


class PostgresCentralReportWriteAdapter:
    """``CentralReportWritePort`` — race-safe single-row insert."""

    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def create_report(self, report_record: Mapping) -> Optional[dict]:
        values = tuple(report_record.get(column) for column in REPORT_INSERT_COLUMNS)

        def _txn(cursor) -> Optional[dict]:
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

    def _in_transaction(self, body: Callable[[object], Optional[dict]]) -> Optional[dict]:
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


def _safe_rollback(connection) -> None:
    rollback = getattr(connection, 'rollback', None)
    if callable(rollback):
        try:
            rollback()
        except Exception:  # noqa: BLE001 — never mask the original error
            pass
