"""Central PostgreSQL read adapter (FE-P0d, 2026-05-27).

``PostgresCentralReadAdapter`` implements ``CentralReadPort`` against the central
read model — the ``coverage_by_condition_hash`` materialized view and the
``active_claims`` view (docs/platform/central_db_schema.v1.json SSOT). It is the
single query path for project-wide coverage (FE-P2) + active claims (FE-P3).

Design (mirrors ``PostgresCentralIdResolver`` / ``PostgresIngestionWriter``):

- **injected ``connection_factory``** (``() -> DbConnection``). The concrete
  psycopg connection is built lazily by the composition root; this module never
  imports a PostgreSQL driver (frozen-exe safe — enforced by
  ``tests/test_platform_read_api_fe_p0d.py``).
- **read-only**: only ``SELECT`` statements. No INSERT/UPDATE/DELETE/REFRESH —
  the materialized view is refreshed by the ingestion writer (FE-P0c Rule 4),
  never by a reader.
- **``%s`` paramstyle** (psycopg). project_id is bound as a parameter (no string
  interpolation) so the uuid filter is injection-safe.
- **loud-fail**: a connection/query failure raises ``CentralReadError`` (never a
  silently-empty list that would mask a backend outage as "nothing measured").
- column SSOT: ``COVERAGE_COLUMNS`` / ``ACTIVE_CLAIM_COLUMNS`` mirror the view
  SELECT aliases; the contract test cross-checks them against the schema JSON so
  a renamed central column is caught at CI.
"""
from __future__ import annotations

from typing import Callable, NamedTuple, Optional, Sequence

from fcc_test_kernel.application.central_contract.pagination import CursorValueDomain
from fcc_test_platform.domain.ports.output.central_read_port import CentralReadError
from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'ACTIVE_CLAIMS_QUERY_SQL',
    'ACTIVE_CLAIMS_QUERY_SQL_ALL',
    'ACTIVE_CLAIMS_QUERY_SQL_ALL_BY_TECH',
    'ACTIVE_CLAIMS_QUERY_SQL_BY_TECH',
    'ACTIVE_CLAIMS_QUERY_SQL_PAGED',
    'ACTIVE_CLAIMS_QUERY_SQL_PAGED_BY_TECH',
    'ACTIVE_CLAIMS_VIEW',
    'ACTIVE_CLAIM_COLUMNS',
    'ACTIVE_CLAIM_KEYSET',
    'ACTIVE_CLAIM_KEYSET_DOMAINS',
    'COVERAGE_COLUMNS',
    'COVERAGE_KEYSET',
    'COVERAGE_KEYSET_DOMAINS',
    'COVERAGE_QUERY_SQL',
    'COVERAGE_QUERY_SQL_ALL',
    'COVERAGE_QUERY_SQL_ALL_BY_TECH',
    'COVERAGE_QUERY_SQL_BY_TECH',
    'COVERAGE_QUERY_SQL_PAGED',
    'COVERAGE_QUERY_SQL_PAGED_BY_TECH',
    'COVERAGE_VIEW',
    'SYNC_STATUS_COLUMNS',
    'SYNC_STATUS_QUERY_SQL',
    'REPORT_SESSION_COLUMNS',
    'REPORT_SESSIONS_QUERY_SQL',
    'PostgresCentralReadAdapter',
]


COVERAGE_VIEW = 'coverage_by_condition_hash'
ACTIVE_CLAIMS_VIEW = 'active_claims'

# Output columns of each central view (= SELECT aliases in
# docs/platform/central_db_schema.v1.json). Order defines the row→dict mapping.
COVERAGE_COLUMNS: tuple[str, ...] = (
    'project_id',
    'technology',
    'condition_hash',
    'latest_session_id',
    'latest_operator',
    'latest_measured_at',
    'latest_verdict',
    'latest_attempt_number',
    'attempt_count',
    # FE-P3 duplicate-quality (2026-05-27): distinct session/operator counts over
    # the partition so a single engineer's re-measure (distinct_operator_count=1)
    # is distinguishable from a cross-engineer duplicate (distinct_operator_count>1).
    'distinct_session_count',
    'distinct_operator_count',
)
ACTIVE_CLAIM_COLUMNS: tuple[str, ...] = (
    'project_id',
    'claim_id',
    'technology',
    'condition_hash',
    'operator',
    'occurred_at',
    'expires_at',
    'session_id',
)


# Keyset (cursor) columns per view — the stable total order the cursor resumes
# from. Coverage uses the unique index (project_id, technology, condition_hash);
# claims add claim_id (unique per result row) as a tiebreaker so occurred_at
# ties stay deterministic. These define the cursor arity + which envelope fields
# the service encodes into next_cursor.
COVERAGE_KEYSET: tuple[str, ...] = ('technology', 'condition_hash')
ACTIVE_CLAIM_KEYSET: tuple[str, ...] = ('occurred_at', 'claim_id')

# Value domain of each keyset column (부채 청산 M3, 2026-07-30). A cursor is a
# tuple of *strings*, but the column it binds against is not always text: the
# claims keyset resumes from ``claim_events.occurred_at timestamptz`` +
# ``claim_id uuid``. PostgreSQL infers those types from context and rejects a
# value that cannot be one — as a 22007/22P02 error, which the adapter must
# report as a backend failure (503). Declaring the domains lets the boundary
# answer 400 instead, which is the truth: the *client's* token is malformed.
# Positionally parallel to the keyset tuples above; the arity/type correspondence
# is cross-checked against the schema SSOT by tests/test_platform_read_api_fe_p0d.py.
COVERAGE_KEYSET_DOMAINS: tuple[CursorValueDomain, ...] = (
    CursorValueDomain.TEXT,       # measurement_attempts.technology      text
    CursorValueDomain.TEXT,       # measurement_attempts.condition_hash  text
)
ACTIVE_CLAIM_KEYSET_DOMAINS: tuple[CursorValueDomain, ...] = (
    CursorValueDomain.TIMESTAMP,  # claim_events.occurred_at  timestamp
    CursorValueDomain.UUID,       # claim_events.claim_id     uuid
)


def _select_by_project(view: str, columns: tuple[str, ...], order_by: tuple[str, ...],
                       *, descending: bool = False, keyset: bool = False,
                       limited: bool = True, extra_filters: tuple[str, ...] = ()) -> str:
    """Build a deterministic SELECT, optionally keyset-paginated / limited / faceted.

    ``SELECT <cols> FROM <view> WHERE project_id = %s [AND "<facet>" = %s ...]
    [AND (<keys>) >|< (%s..)] ORDER BY <keys> [DESC] [LIMIT %s]``. Columns/view/
    keys/facets are quoted identifiers; project_id, facet values, cursor values,
    and the limit are all ``%s`` parameters (never interpolated). Read-only by
    construction — the only verb this helper emits is ``SELECT``. Row-value
    comparison ``(a, b) > (%s, %s)`` is SQL-standard (PostgreSQL + SQLite ≥ 3.15)
    and resumes from the cursor in index order. ``limited=False`` omits LIMIT for
    the backward-compatible unbounded read (no ``limit`` query param ⇒ all rows,
    like before FE-P0d pagination — so the already-shipped FE-P2 dashboard is
    unaffected). ``extra_filters`` appends equality facet filters (Phase B
    ``technology``) — each is an indexed-column ``= %s`` bound parameter, placed
    before the keyset comparison so the param order stays
    ``(project_id, *facets, *cursor, limit)``.
    """
    select_cols = ', '.join(f'"{column}"' for column in columns)
    direction = ' DESC' if descending else ''
    order_clause = ', '.join(f'"{column}"{direction}' for column in order_by)
    where = '"project_id" = %s'
    for column in extra_filters:
        where += f' AND "{column}" = %s'
    if keyset:
        key_cols = ', '.join(f'"{column}"' for column in order_by)
        placeholders = ', '.join(['%s'] * len(order_by))
        comparison = '<' if descending else '>'
        where += f' AND ({key_cols}) {comparison} ({placeholders})'
    limit_clause = ' LIMIT %s' if limited else ''
    return (
        f'SELECT {select_cols} FROM "{view}" '
        f'WHERE {where} ORDER BY {order_clause}{limit_clause}'
    )


# Unbounded (no LIMIT) — backward-compatible default when no ``limit`` is given.
COVERAGE_QUERY_SQL_ALL = _select_by_project(
    COVERAGE_VIEW, COVERAGE_COLUMNS, COVERAGE_KEYSET, limited=False,
)
ACTIVE_CLAIMS_QUERY_SQL_ALL = _select_by_project(
    ACTIVE_CLAIMS_VIEW, ACTIVE_CLAIM_COLUMNS, ACTIVE_CLAIM_KEYSET,
    descending=True, limited=False,
)
# First page (LIMIT, no cursor) + subsequent page (LIMIT + keyset cursor).
COVERAGE_QUERY_SQL = _select_by_project(COVERAGE_VIEW, COVERAGE_COLUMNS, COVERAGE_KEYSET)
COVERAGE_QUERY_SQL_PAGED = _select_by_project(
    COVERAGE_VIEW, COVERAGE_COLUMNS, COVERAGE_KEYSET, keyset=True,
)
ACTIVE_CLAIMS_QUERY_SQL = _select_by_project(
    ACTIVE_CLAIMS_VIEW, ACTIVE_CLAIM_COLUMNS, ACTIVE_CLAIM_KEYSET, descending=True,
)
ACTIVE_CLAIMS_QUERY_SQL_PAGED = _select_by_project(
    ACTIVE_CLAIMS_VIEW, ACTIVE_CLAIM_COLUMNS, ACTIVE_CLAIM_KEYSET,
    descending=True, keyset=True,
)

# Phase B technology facet — same three forms with an added ``AND "technology" =
# %s`` filter. Pre-built (not assembled at call time) so the SELECT-only AST
# guard covers them like the unfiltered constants.
_TECH_FILTER: tuple[str, ...] = ('technology',)
COVERAGE_QUERY_SQL_ALL_BY_TECH = _select_by_project(
    COVERAGE_VIEW, COVERAGE_COLUMNS, COVERAGE_KEYSET,
    extra_filters=_TECH_FILTER, limited=False,
)
COVERAGE_QUERY_SQL_BY_TECH = _select_by_project(
    COVERAGE_VIEW, COVERAGE_COLUMNS, COVERAGE_KEYSET, extra_filters=_TECH_FILTER,
)
COVERAGE_QUERY_SQL_PAGED_BY_TECH = _select_by_project(
    COVERAGE_VIEW, COVERAGE_COLUMNS, COVERAGE_KEYSET,
    extra_filters=_TECH_FILTER, keyset=True,
)
ACTIVE_CLAIMS_QUERY_SQL_ALL_BY_TECH = _select_by_project(
    ACTIVE_CLAIMS_VIEW, ACTIVE_CLAIM_COLUMNS, ACTIVE_CLAIM_KEYSET,
    descending=True, extra_filters=_TECH_FILTER, limited=False,
)
ACTIVE_CLAIMS_QUERY_SQL_BY_TECH = _select_by_project(
    ACTIVE_CLAIMS_VIEW, ACTIVE_CLAIM_COLUMNS, ACTIVE_CLAIM_KEYSET,
    descending=True, extra_filters=_TECH_FILTER,
)
ACTIVE_CLAIMS_QUERY_SQL_PAGED_BY_TECH = _select_by_project(
    ACTIVE_CLAIMS_VIEW, ACTIVE_CLAIM_COLUMNS, ACTIVE_CLAIM_KEYSET,
    descending=True, extra_filters=_TECH_FILTER, keyset=True,
)


# FE-SYNC sync-status (2026-05-27) — central-data freshness for one project.
# Scalar subqueries (no FROM) over the two views: newest central measurement
# timestamp + measured-condition count + active-claim count. SELECT-only; runs on
# PostgreSQL + SQLite alike (bare ``SELECT (subquery), ...`` is portable).
SYNC_STATUS_COLUMNS: tuple[str, ...] = (
    'last_ingested_at',
    'condition_count',
    'active_claim_count',
)
SYNC_STATUS_QUERY_SQL = (
    'SELECT '
    f'(SELECT MAX("latest_measured_at") FROM "{COVERAGE_VIEW}" WHERE "project_id" = %s) '
    'AS last_ingested_at, '
    f'(SELECT COUNT(*) FROM "{COVERAGE_VIEW}" WHERE "project_id" = %s) '
    'AS condition_count, '
    f'(SELECT COUNT(*) FROM "{ACTIVE_CLAIMS_VIEW}" WHERE "project_id" = %s) '
    'AS active_claim_count'
)

REPORT_SESSION_COLUMNS: tuple[str, ...] = (
    'project_id',
    'session_id',
    'provider_session_id',
    'technology',
    'condition_hash',
    'latest_measured_at',
    'latest_verdict',
    'latest_attempt_number',
    'node_id',
    'node_name',
    'node_base_url',
)

# Node routing SSOT (P5-C, Codex-review durability fix): a reportable session's
# node is resolved through the DURABLE ``test_sessions.provider_id -> providers``
# edge — both columns are written once at ingestion and never mutate off a
# completed session. We deliberately do NOT join ``chamber_availability`` (the
# "latest heartbeat per chamber" live projection): its ``session_id`` reflects
# what each chamber is doing *now*, so an INNER JOIN on it would silently drop a
# still-reportable completed session the moment the chamber idles (session_id ->
# NULL) or starts the next session. Sealed by ``test_platform_report_sessions_p5c
# .py`` (schema-SSOT SQLite fixture, idle/NULL + moved-heartbeat scenarios).
REPORT_SESSIONS_QUERY_SQL = (
    'SELECT '
    'c."project_id", '
    'c."latest_session_id" AS session_id, '
    's."provider_session_id", '
    'c."technology", '
    'c."condition_hash", '
    'c."latest_measured_at", '
    'c."latest_verdict", '
    'c."latest_attempt_number", '
    'p."provider_id" AS node_id, '
    'p."product_line" AS node_name, '
    'p."base_url" AS node_base_url '
    f'FROM "{COVERAGE_VIEW}" c '
    'JOIN "test_sessions" s ON s."id" = c."latest_session_id" '
    'JOIN "providers" p ON p."id" = s."provider_id" '
    'WHERE c."project_id" = %s '
    'ORDER BY c."latest_measured_at" DESC, c."technology", c."condition_hash"'
)


class _ReadVariants(NamedTuple):
    """The six SQL forms for one view: {unbounded, first-page, keyset-page} ×
    {no facet, technology facet}. ``_plan_read`` selects one per call."""

    all_: str
    first: str
    paged: str
    all_by_tech: str
    first_by_tech: str
    paged_by_tech: str


_COVERAGE_VARIANTS = _ReadVariants(
    COVERAGE_QUERY_SQL_ALL, COVERAGE_QUERY_SQL, COVERAGE_QUERY_SQL_PAGED,
    COVERAGE_QUERY_SQL_ALL_BY_TECH, COVERAGE_QUERY_SQL_BY_TECH,
    COVERAGE_QUERY_SQL_PAGED_BY_TECH,
)
_ACTIVE_CLAIMS_VARIANTS = _ReadVariants(
    ACTIVE_CLAIMS_QUERY_SQL_ALL, ACTIVE_CLAIMS_QUERY_SQL, ACTIVE_CLAIMS_QUERY_SQL_PAGED,
    ACTIVE_CLAIMS_QUERY_SQL_ALL_BY_TECH, ACTIVE_CLAIMS_QUERY_SQL_BY_TECH,
    ACTIVE_CLAIMS_QUERY_SQL_PAGED_BY_TECH,
)


def _plan_read(
    variants: _ReadVariants,
    project_id: str,
    technology: Optional[str],
    limit: Optional[int],
    after: Optional[Sequence[str]],
) -> tuple[str, tuple]:
    """Select the SQL variant + build positional params for one read.

    Param order always mirrors the WHERE clause:
    ``(project_id, [technology], [*cursor], [limit])``. ``technology`` falsy ⇒
    unfiltered variants; ``limit`` None ⇒ unbounded; ``after`` None ⇒ first page.
    """
    if technology:
        base: tuple = (project_id, technology)
        if limit is None:
            return variants.all_by_tech, base
        if after is None:
            return variants.first_by_tech, (*base, limit)
        return variants.paged_by_tech, (*base, *after, limit)
    base = (project_id,)
    if limit is None:
        return variants.all_, base
    if after is None:
        return variants.first, (*base, limit)
    return variants.paged, (*base, *after, limit)


class PostgresCentralReadAdapter:
    """``CentralReadPort`` over a central PostgreSQL connection factory."""

    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def read_project_coverage(
        self, project_id: str, *, technology: Optional[str] = None,
        limit: Optional[int] = None, after: Optional[Sequence[str]] = None,
    ) -> list[dict]:
        sql, params = _plan_read(_COVERAGE_VARIANTS, project_id, technology, limit, after)
        return self._query(sql, COVERAGE_COLUMNS, params)

    def read_active_claims(
        self, project_id: str, *, technology: Optional[str] = None,
        limit: Optional[int] = None, after: Optional[Sequence[str]] = None,
    ) -> list[dict]:
        sql, params = _plan_read(_ACTIVE_CLAIMS_VARIANTS, project_id, technology, limit, after)
        return self._query(sql, ACTIVE_CLAIM_COLUMNS, params)

    def read_sync_status(self, project_id: str) -> dict:
        rows = self._query(SYNC_STATUS_QUERY_SQL, SYNC_STATUS_COLUMNS,
                           (project_id, project_id, project_id))
        # The aggregate query always returns exactly one row (scalar subqueries).
        return rows[0] if rows else {column: None for column in SYNC_STATUS_COLUMNS}

    def read_project_report_sessions(self, project_id: str) -> list[dict]:
        return self._query(REPORT_SESSIONS_QUERY_SQL, REPORT_SESSION_COLUMNS, (project_id,))

    def _query(self, statement: str, columns: tuple[str, ...], params: tuple) -> list[dict]:
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001 — wrap as loud CentralReadError
            raise CentralReadError(f'central read connection failed: {exc}') from exc
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(statement, params)
                rows = list(cursor.fetchall())
            finally:
                cursor.close()
        except Exception as exc:  # noqa: BLE001 — wrap as loud CentralReadError
            raise CentralReadError(f'central read query failed: {exc}') from exc
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()
        return [dict(zip(columns, row)) for row in rows]
