"""Central PostgreSQL test-report read adapter (Phase G, 2026-06-23).

``PostgresCentralReportReadAdapter`` implements ``CentralReportReadPort`` against
the central ``test_reports`` table (docs/platform/central_db_schema.v1.json).

Design (mirrors ``PostgresCentralProjectReadAdapter``):

- **injected ``connection_factory``** (``() -> DbConnection``) — psycopg built
  lazily by the composition root; this module imports no PostgreSQL driver.
- **read-only**: only ``SELECT``.
- **``%s`` paramstyle** (psycopg) — project_id is a bound parameter.
- **loud-fail**: a connection/query failure raises ``CentralReportError``.
- newest report first (``created_at DESC``) so the latest edition heads the list.
"""
from __future__ import annotations

from typing import Callable, Optional

from fcc_test_platform.domain.ports.output.central_report_port import CentralReportError
from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'REPORT_COLUMNS',
    'LIST_REPORTS_SQL',
    'SESSION_SNAPSHOT_COLUMNS',
    'GET_SESSION_SNAPSHOT_SQL',
    'PostgresCentralReportReadAdapter',
]


# Output columns of the list SELECT (= SELECT aliases). Order defines the
# row→dict mapping consumed by CentralReportService. report_number is NOT here
# (it is derived by the service from management_number + edition).
REPORT_COLUMNS: tuple[str, ...] = (
    'report_id',
    'project_id',
    'edition',
    'date_of_issue',
    'date_tested_start',
    'date_tested_end',
    'prepared_by',
    'prepared_site',
    'rev_history_json',
    'created_at',
)
LIST_REPORTS_SQL = (
    'SELECT "r"."id" AS "report_id", "r"."project_id", "r"."edition", '
    '"r"."date_of_issue", "r"."date_tested_start", "r"."date_tested_end", '
    '"r"."prepared_by", "r"."prepared_site", "r"."rev_history_json", '
    '"r"."created_at" '
    'FROM "test_reports" r WHERE "r"."project_id" = %s '
    'ORDER BY "r"."created_at" DESC'
)
SESSION_SNAPSHOT_COLUMNS: tuple[str, ...] = (
    'session_id', 'project_id', 'sample_id', 'session_origin',
    'sample_snapshot_json', 'sample_snapshot_schema_version',
)
GET_SESSION_SNAPSHOT_SQL = (
    'SELECT "id" AS "session_id", "project_id", "sample_id", '
    '"session_origin", "sample_snapshot_json", '
    '"sample_snapshot_schema_version" FROM "test_sessions" '
    'WHERE "id" = %s LIMIT 1'
)


class PostgresCentralReportReadAdapter:
    """``CentralReportReadPort`` over a central PostgreSQL connection factory."""

    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def list_reports(self, project_id: str) -> list[dict]:
        return self._query(LIST_REPORTS_SQL, REPORT_COLUMNS, (project_id,))

    def get_session_snapshot(self, session_id: str) -> Optional[dict]:
        rows = self._query(GET_SESSION_SNAPSHOT_SQL, SESSION_SNAPSHOT_COLUMNS, (session_id,))
        if not rows:
            return None
        row = dict(rows[0])
        for key in ('session_id', 'project_id', 'sample_id'):
            if row.get(key) is not None:
                row[key] = str(row[key])
        return row

    def _query(self, statement: str, columns: tuple[str, ...], params: tuple) -> list[dict]:
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001 — wrap as loud CentralReportError
            raise CentralReportError(f'central report read connection failed: {exc}') from exc
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(statement, params)
                rows = list(cursor.fetchall())
            finally:
                cursor.close()
        except Exception as exc:  # noqa: BLE001
            raise CentralReportError(f'central report read query failed: {exc}') from exc
        finally:
            close: Optional[Callable] = getattr(connection, 'close', None)
            if callable(close):
                close()
        return [dict(zip(columns, row)) for row in rows]
