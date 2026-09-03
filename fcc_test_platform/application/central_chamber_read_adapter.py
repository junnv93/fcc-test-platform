"""Central PostgreSQL chamber read adapter (멀티챔버 P2, 2026-06-15).

``PostgresCentralChamberReadAdapter`` implements ``CentralChamberReadPort`` against
the central chamber registry + availability projection — the ``chamber_nodes``
table and the ``chamber_availability`` view (docs/platform/central_db_schema.v1.json
SSOT). It is the single query path for the chamber availability dashboard (Phase 6)
and the measurement proxy (Phase 5).

Design (mirrors ``PostgresCentralReadAdapter``):

- **injected ``connection_factory``** (``() -> DbConnection``). The concrete psycopg
  connection is built lazily by the composition root; this module imports no
  PostgreSQL driver (frozen-exe safe — enforced by ``tests/test_platform_chamber_api_p2.py``).
- **read-only**: only ``SELECT`` statements. No write verb — the registry is written
  by the write adapter, heartbeats are appended to the ledger, never by a reader.
- **loud-fail**: a connection/query failure raises ``CentralChamberReadError`` (never
  a silently-empty list that would mask a backend outage as "no chambers / all
  OFFLINE" and dangerously blank the dashboard).
- column SSOT: ``CHAMBER_NODE_COLUMNS`` / ``CHAMBER_AVAILABILITY_COLUMNS`` mirror the
  table columns + the view SELECT aliases; the contract test cross-checks them against
  the schema JSON so a renamed central column is caught at CI.
- chambers are GLOBAL infrastructure (not project-scoped) so there is no project_id
  filter — every read returns all registered chambers ordered by ``chamber_id``.
"""
from __future__ import annotations

from typing import Callable

from fcc_test_platform.domain.ports.output.central_chamber_read_port import CentralChamberReadError
from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'CHAMBER_AVAILABILITY_COLUMNS',
    'CHAMBER_AVAILABILITY_VIEW',
    'CHAMBER_AVAILABILITY_QUERY_SQL',
    'CHAMBER_NODES_QUERY_SQL',
    'CHAMBER_NODES_TABLE',
    'CHAMBER_NODE_COLUMNS',
    'PostgresCentralChamberReadAdapter',
]


CHAMBER_NODES_TABLE = 'chamber_nodes'
CHAMBER_AVAILABILITY_VIEW = 'chamber_availability'

# Registry columns the read surface exposes (identity + connection address +
# per-chamber TTL). The table also carries id/created_at/updated_at which the
# availability dashboard does not need — projected away here.
CHAMBER_NODE_COLUMNS: tuple[str, ...] = (
    'chamber_id',
    'name',
    'base_url',
    'enabled',
    'heartbeat_ttl_seconds',
)

# Output columns of the chamber_availability view (= SELECT aliases in
# docs/platform/central_db_schema.v1.json views.chamber_availability). Order
# defines the row→dict mapping. reported_status / last_heartbeat_at are verbatim
# (NULL for a zero-heartbeat chamber); OFFLINE is derived by the read service.
CHAMBER_AVAILABILITY_COLUMNS: tuple[str, ...] = (
    'chamber_id',
    'name',
    'base_url',
    'enabled',
    'heartbeat_ttl_seconds',
    'reported_status',
    'last_heartbeat_at',
    'heartbeat_expires_at',
    'session_id',
    # C1 heartbeat-carried progress — verbatim latest-heartbeat progress snapshot
    # (in_use only). The read service parses + gates exposure on derived status.
    'progress_json',
    # M2 diagnostics — verbatim latest-heartbeat redacted error payload
    # ({message, occurred_at}). The read service parses it + derives unavailable_reason.
    'last_error_json',
    # 챔버 모드 축 (2026-08-16) — **승인** 절반. 3-상태(NULL/true/false)이고 NULL 은
    # '아무도 판정하지 않았다'다. 실현 절반은 위 heartbeat 파생이고, 판정(대조)은
    # 읽기 서비스가 ``chamber_mode_policy`` 에 위임한다.
    #
    # ⚠️ **자기 테이블 형제(heartbeat_ttl_seconds) 옆이 아니라 맨 끝인 것은 취향이
    # 아니다.** 이 튜플의 순서는 뷰 SELECT 별칭 순서를 그대로 미러해야 하는데, 그 뷰는
    # PostgreSQL `CREATE OR REPLACE VIEW` 로 갱신되고 그것은 기존 뷰의 컬럼을 **중간에
    # 삽입하거나 재배열할 수 없다**(실측 PG 16.14: 중간 삽입은 `cannot change name of
    # view column`, 끝 추가는 성공). 형제 옆에 두면 증분 마이그레이션이 이미 배포된
    # 모든 중앙 DB 에서 실패한다.
    'accepts_web_sessions',
)


def _select_all(source: str, columns: tuple[str, ...]) -> str:
    """``SELECT <quoted cols> FROM <source> ORDER BY chamber_id`` — read-only,
    no parameters (chambers are global, not project-scoped). Deterministic order."""
    select_cols = ', '.join(f'"{column}"' for column in columns)
    return f'SELECT {select_cols} FROM "{source}" ORDER BY "chamber_id"'


CHAMBER_NODES_QUERY_SQL = _select_all(CHAMBER_NODES_TABLE, CHAMBER_NODE_COLUMNS)
CHAMBER_AVAILABILITY_QUERY_SQL = _select_all(
    CHAMBER_AVAILABILITY_VIEW, CHAMBER_AVAILABILITY_COLUMNS,
)


class PostgresCentralChamberReadAdapter:
    """``CentralChamberReadPort`` over a central PostgreSQL connection factory."""

    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def read_chamber_nodes(self) -> list[dict]:
        return self._query(CHAMBER_NODES_QUERY_SQL, CHAMBER_NODE_COLUMNS)

    def read_chamber_availability(self) -> list[dict]:
        return self._query(CHAMBER_AVAILABILITY_QUERY_SQL, CHAMBER_AVAILABILITY_COLUMNS)

    def _query(self, statement: str, columns: tuple[str, ...]) -> list[dict]:
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001 — wrap as loud CentralChamberReadError
            raise CentralChamberReadError(
                f'central chamber read connection failed: {exc}'
            ) from exc
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(statement, ())
                rows = list(cursor.fetchall())
            finally:
                cursor.close()
        except Exception as exc:  # noqa: BLE001 — wrap as loud CentralChamberReadError
            raise CentralChamberReadError(
                f'central chamber read query failed: {exc}'
            ) from exc
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()
        return [dict(zip(columns, row)) for row in rows]
