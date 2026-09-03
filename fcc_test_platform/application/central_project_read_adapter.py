"""Central PostgreSQL project read adapter (Phase 1, 2026-06-22).

``PostgresCentralProjectReadAdapter`` implements ``CentralProjectReadPort``
against the central ``projects`` / ``device_models`` / ``samples`` /
``project_membership`` / ``users`` tables (docs/platform/central_db_schema.v1.json).

Design (mirrors ``PostgresCentralReadAdapter``):

- **injected ``connection_factory``** (``() -> DbConnection``) — psycopg built
  lazily by the composition root; this module imports no PostgreSQL driver.
- **read-only**: only ``SELECT`` statements.
- **``%s`` paramstyle** (psycopg) — subject / project_id are bound parameters
  (never interpolated) so the filter is injection-safe.
- **loud-fail**: a connection/query failure raises ``CentralProjectError``
  (never a silently-empty list masking a backend outage as "no projects").
- **membership-scoped list**: when ``subject`` is given the list joins
  ``project_membership`` ⋈ ``users`` so a tester sees only their member
  projects; ``subject=None`` (allow-insecure dev only) returns all projects.
- 1:1 model overlay (ADR-0017 D1): ``device_models`` is LEFT JOINed (one model
  per project under the create policy) so a project always projects one row.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Callable, Mapping, Optional

from fcc_test_kernel.application.central_contract.pagination import CursorValueDomain
from fcc_test_platform.domain.ports.output.central_sample_inventory_read_port import (
    CentralSampleInventoryReadPort,
)
from fcc_test_platform.domain.ports.output.central_project_port import CentralProjectError
from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection
from fcc_test_platform.domain.services.project_directory_query import (
    PROJECT_DIRECTORY_ORDER_COLUMNS,
    PROJECT_SEARCH_COLUMNS,
    SEARCH_LIKE_ESCAPE_CHAR,
    directory_order_columns,
)


__all__ = [
    'PROJECT_DETAIL_COLUMNS',
    'PROJECT_DIRECTORY_KEYSET_DOMAINS',
    'PROJECT_LIST_COLUMNS',
    'PROJECT_LIST_SQL',
    'PROJECT_LIST_SQL_BY_STATUS',
    'PROJECT_LIST_SQL_VARIANTS',
    'PROJECT_DETAIL_SQL',
    'PROJECT_SAMPLES_COLUMNS',
    'PROJECT_SAMPLES_SQL',
    'PROJECT_INTAKES_COLUMNS',
    'PROJECT_INTAKES_SQL',
    'PostgresCentralProjectReadAdapter',
]


# Output columns of the list/detail SELECTs (= SELECT aliases). Order defines
# the row→dict mapping consumed by CentralProjectService envelopes.
PROJECT_LIST_COLUMNS: tuple[str, ...] = (
    'project_id',
    'project_code',
    'model_name',
    'customer',
    'manufacturer',
    'management_number',
    'status',
    'fcc_grantee_code',
    'applicant_name',
    'applicant_address',
    'eut_description',
    'test_standard',
    'sample_count',
    # W3 백엔드 — keyset 커서의 첫 성분. SELECT 는 예전에도 ORDER BY 용으로 이 컬럼을
    # 실어 보냈지만 출력 튜플에 없어서 ``dict(zip(...))`` 가 버리고 있었다. 이제
    # 서비스가 커서를 만들려면 필요하므로 행에 남긴다 — 단, 응답 envelope 에는
    # 넣지 않는다(넣으면 무-페이지네이션 응답이 기존과 달라져 계약 S11 위반).
    'created_at',
)
PROJECT_DETAIL_COLUMNS: tuple[str, ...] = (
    'project_id',
    'project_code',
    'model_name',
    'customer',
    'manufacturer',
    'management_number',
    'status',
    'fcc_grantee_code',
    'applicant_name',
    'applicant_address',
    'eut_description',
    'test_standard',
    'created_at',
)
PROJECT_SAMPLES_COLUMNS: tuple[str, ...] = (
    'sample_id',
    'sample_code',
    'serial_number',
    'model_id',
    # PM 칸 인벤토리 메타(Phase C) — 순서가 PROJECT_SAMPLES_SQL SELECT alias 와 정확히 일치.
    'sample_number',
    'test_category',
    'label_number',
    'smsn',
    'intake_cert',
    'assigned_team',
    'sender',
    'receiver',
    'received_date',
    'released_date',
)


# The scalar sample-count subquery is per-project. Newest project first.
#
# **DISTINCT 제거 (W3 백엔드)** — 옛 주석은 "다중 역할 멤버십이 만드는 중복 행을
# collapse 한다"였으나 project-status-visibility 이후 이 SELECT 에는 membership
# 조인이 없다(read-open, ``projects ⋈ device_models`` 뿐). 남은 유일한 조인이
# 중복을 만든다면 그 중복 행들은 ``model_name``/``manufacturer`` 값이 서로 달라
# DISTINCT 로 collapse 되지도 않는다 — 즉 **증명 가능한 no-op** 이었다. 그런데
# 비용은 컸다: DISTINCT 는 LIMIT 앞에서 전체 결과 materialize 를 강제해 keyset
# 인덱스 스캔을 무력화한다(실측 — 50k 행 status 필터: DISTINCT 289ms / 인덱스
# 미사용·external merge disk 3.4MB → 제거+인덱스 0.54ms, 계약 M6 증거 A·D).
# 1:1 overlay 는 ADR-0017 D1 이 보증한다.
_PROJECT_LIST_SELECT = (
    'SELECT "p"."id" AS "project_id", "p"."project_code", '
    '"dm"."model_name", "p"."customer", "dm"."manufacturer", '
    '"p"."management_number", "p"."status", '
    '"p"."fcc_grantee_code", "p"."applicant_name", "p"."applicant_address", '
    '"p"."eut_description", "p"."test_standard", '
    '(SELECT COUNT(*) FROM "samples" s WHERE s."project_id" = "p"."id") AS "sample_count", '
    '"p"."created_at" '
    'FROM "projects" p '
    'LEFT JOIN "device_models" dm ON dm."project_id" = "p"."id" '
)

# ── W3 백엔드 — 디렉터리 스케일 (검색 + keyset) ────────────────────────────────
# 페이지 경계가 있는 질의(``LIMIT`` 또는 커서)의 정렬은 최신순 **전순서**
# ``(created_at DESC, id DESC)``. 두 컬럼 모두 DESC 라 커서 비교는 행-값 비교
# ``(created_at, id) < (%s, %s)`` 한 번으로 끝난다(SQL 표준, PostgreSQL + SQLite
# ≥ 3.15). ASC 인덱스가 이 완전 역순을 ``Index Only Scan Backward`` 로 그대로
# 서비스하므로 DESC 인덱스는 불필요(실측 증거 U·V·W).
#
# 경계가 없는 pre-W3 무제한 읽기는 옛 ``ORDER BY "p"."created_at" DESC`` 를 **그대로**
# 유지한다 — tie-breaker 는 페이지 경계에서만 정확성에 기여하는데, 무제한 읽기에
# 붙이면 이득 0 에 기존 클라이언트가 보던 행 순서만 바뀐다(계약 S11). 어느 축을 쓸지는
# 도메인 ``directory_order_columns`` 가 소유하고, 어댑터는 절만 렌더한다.
_ORDER_BY_CLAUSES: Mapping[bool, str] = MappingProxyType({
    paginated: 'ORDER BY ' + ', '.join(
        f'"p"."{column}" DESC'
        for column in directory_order_columns(paginated=paginated)
    )
    for paginated in (False, True)
})
#: Value domain of each project-directory keyset column (부채 청산 M3, 2026-07-30).
#: ``PROJECT_DIRECTORY_ORDER_COLUMNS`` = ``(created_at, id)`` → ``projects.created_at``
#: ``timestamp`` + ``projects.id`` ``uuid``. Same shape (and same latent defect) as the
#: claims keyset: a forged string cursor used to reach PostgreSQL and come back as a
#: 503. Declared here, next to the SQL that binds it, and positionally parallel to the
#: domain-owned column tuple — the correspondence is sealed by
#: tests/test_platform_read_api_fe_p0d.py against the central schema SSOT.
PROJECT_DIRECTORY_KEYSET_DOMAINS: tuple[CursorValueDomain, ...] = (
    CursorValueDomain.TIMESTAMP,  # projects.created_at  timestamp
    CursorValueDomain.UUID,       # projects.id          uuid
)
_KEYSET_PREDICATE = (
    '(' + ', '.join(f'"p"."{column}"' for column in PROJECT_DIRECTORY_ORDER_COLUMNS) + ')'
    + ' < ('
    + ', '.join(['%s'] * len(PROJECT_DIRECTORY_ORDER_COLUMNS))
    + ')'
)
# 대소문자 무관 부분일치. 양변 LOWER (ILIKE 는 PostgreSQL 전용, 맨 LIKE 는 PG↔SQLite
# 대소문자 규약이 정반대) + 명시적 ESCAPE (사용자가 친 %/_ 를 글자로 취급).
# 컬럼명은 SSOT 튜플에서만 오고 값은 전부 %s 바인드 — 보간 경로 0.
_SEARCH_PREDICATE = '(' + ' OR '.join(
    f'LOWER("p"."{column}") LIKE %s ESCAPE \'{SEARCH_LIKE_ESCAPE_CHAR}\''
    for column in PROJECT_SEARCH_COLUMNS
) + ')'


def _build_project_list_sql(
    *, by_status: bool, search: bool, keyset: bool, limited: bool,
) -> str:
    """Assemble one project-directory SELECT variant.

    Param order is fixed by the clause order below —
    ``(status?, *search_pattern × len(PROJECT_SEARCH_COLUMNS), *cursor, limit?)``.
    Read-only by construction: the only verb this helper emits is ``SELECT``.

    ``keyset or limited`` is exactly "this query returns a *page*" — the sole
    condition under which the total-order tie-breaker is required (and the sole
    condition under which changing the order is not a backward-compat break,
    because no pre-W3 caller could produce it).
    """
    predicates: list[str] = []
    if by_status:
        predicates.append('"p"."status" = %s')
    if search:
        predicates.append(_SEARCH_PREDICATE)
    if keyset:
        predicates.append(_KEYSET_PREDICATE)
    where_sql = ('WHERE ' + ' AND '.join(predicates) + ' ') if predicates else ''
    limit_sql = ' LIMIT %s' if limited else ''
    order_sql = _ORDER_BY_CLAUSES[keyset or limited]
    return _PROJECT_LIST_SELECT + where_sql + order_sql + limit_sql


#: (by_status, search, keyset, limited) → SQL. 전 조합을 **import 시점에** 만들어
#: 두고 호출 시에는 고르기만 한다 — 요청마다 SQL 문자열을 조립하지 않으므로
#: "SELECT 만 생성한다"는 성질이 정적으로 확인 가능하다(어댑터 규약).
PROJECT_LIST_SQL_VARIANTS: Mapping[tuple[bool, bool, bool, bool], str] = MappingProxyType({
    (by_status, search, keyset, limited): _build_project_list_sql(
        by_status=by_status, search=search, keyset=keyset, limited=limited,
    )
    for by_status in (False, True)
    for search in (False, True)
    for keyset in (False, True)
    for limited in (False, True)
})

# Backward-compatible names for the two pre-W3 unbounded reads. Read-open by
# status (project-status-visibility): any authenticated caller sees projects
# filtered by the sealed status domain (active|completed) — NOT scoped to the
# caller's membership. A future "my projects" view would add a separate
# membership-joined query; the directory list is intentionally org-wide.
PROJECT_LIST_SQL = PROJECT_LIST_SQL_VARIANTS[(False, False, False, False)]
PROJECT_LIST_SQL_BY_STATUS = PROJECT_LIST_SQL_VARIANTS[(True, False, False, False)]

PROJECT_DETAIL_SQL = (
    'SELECT "p"."id" AS "project_id", "p"."project_code", "dm"."model_name", '
    '"p"."customer", "dm"."manufacturer", "p"."management_number", "p"."status", '
    '"p"."fcc_grantee_code", "p"."applicant_name", "p"."applicant_address", '
    '"p"."eut_description", "p"."test_standard", '
    '"p"."created_at" '
    'FROM "projects" p '
    'LEFT JOIN "device_models" dm ON dm."project_id" = "p"."id" '
    'WHERE "p"."id" = %s LIMIT 1'
)
PROJECT_SAMPLES_SQL = (
    'SELECT "s"."id" AS "sample_id", "s"."sample_code", "s"."serial_number", '
    '"s"."model_id", '
    '"s"."sample_number", "s"."test_category", "s"."label_number", "s"."smsn", '
    '"s"."intake_cert", "s"."assigned_team", "s"."sender", "s"."receiver", '
    '"s"."received_date", "s"."released_date" '
    'FROM "samples" s WHERE "s"."project_id" = %s ORDER BY "s"."created_at"'
)

# Phase F follow-up (2026-06-23) — compact intake read-back. The project detail
# UI + report citation only need each sample's LATEST intake + a history count,
# not the full append-only history (which grows unbounded). One window-function
# query returns at most ONE row per sample (``ROW_NUMBER`` over ``created_at DESC``
# — intake_date is free text/unsortable, ``created_at`` is the reliable recency
# proxy; ``id DESC`` is the deterministic tie-break) carrying the total history
# size via ``COUNT(*) OVER``. The full history never crosses the DB→adapter
# boundary (no N+1, no large payload). ``ROW_NUMBER`` is the already-shipped
# PG≡SQLite-shim idiom (central read views). Read-only.
PROJECT_INTAKES_COLUMNS: tuple[str, ...] = (
    'sample_id', 'sample_intake_id', 'intake_date',
    'bl', 'ap', 'cp', 'csc', 'rf_cal', 'hw_rev', 'note', 'intake_count',
)
PROJECT_INTAKES_SQL = (
    'SELECT "sample_id", "sample_intake_id", "intake_date", '
    '"bl", "ap", "cp", "csc", "rf_cal", "hw_rev", "note", "intake_count" '
    'FROM ('
    'SELECT "i"."sample_id" AS "sample_id", "i"."id" AS "sample_intake_id", '
    '"i"."intake_date", "i"."bl", "i"."ap", "i"."cp", "i"."csc", '
    '"i"."rf_cal", "i"."hw_rev", "i"."note", '
    'ROW_NUMBER() OVER ('
    'PARTITION BY "i"."sample_id" ORDER BY "i"."created_at" DESC, "i"."id" DESC'
    ') AS "rn", '
    'COUNT(*) OVER (PARTITION BY "i"."sample_id") AS "intake_count" '
    'FROM "sample_intakes" i '
    'JOIN "samples" s ON s."id" = "i"."sample_id" '
    'WHERE "s"."project_id" = %s'
    ') ranked '
    'WHERE "rn" = 1 '
    'ORDER BY "sample_id"'
)


class PostgresCentralProjectReadAdapter:
    """``CentralProjectReadPort`` over a central PostgreSQL connection factory."""

    def __init__(
        self,
        connection_factory: Callable[[], DbConnection],
        *,
        sample_inventory_read_port: Optional[CentralSampleInventoryReadPort] = None,
    ) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory
        # Production composition injects the web-inventory read projection so
        # project detail has one samples/intakes authority.  The fallback below
        # remains for isolated legacy/report tests that intentionally construct
        # this adapter without the inventory composition.
        self._sample_inventory_read = sample_inventory_read_port

    def list_projects(
        self,
        *,
        status: Optional[str] = None,
        q: Optional[str] = None,
        limit: Optional[int] = None,
        after: Optional[tuple] = None,
    ) -> list[dict]:
        # status None / 'all' → every project; a sealed-domain value filters.
        by_status = bool(status) and status != 'all'
        # ``q`` arrives already normalized+escaped as a LIKE pattern from the
        # service (domain SSOT ``search_like_pattern``) — the adapter never
        # interprets the raw user term, it only binds the pattern once per
        # searched column.
        search = q is not None
        keyset = after is not None
        limited = limit is not None
        params: tuple = ()
        if by_status:
            params += (status,)
        if search:
            params += (q,) * len(PROJECT_SEARCH_COLUMNS)
        if keyset:
            params += tuple(after)
        if limited:
            params += (limit,)
        statement = PROJECT_LIST_SQL_VARIANTS[(by_status, search, keyset, limited)]
        return self._query(statement, PROJECT_LIST_COLUMNS, params)

    def read_project_detail(self, project_id: str) -> Optional[dict]:
        header = self._query(PROJECT_DETAIL_SQL, PROJECT_DETAIL_COLUMNS, (project_id,))
        if not header:
            return None
        if self._sample_inventory_read is not None:
            samples = self._read_inventory_samples(project_id)
        else:
            samples = self._query(
                PROJECT_SAMPLES_SQL, PROJECT_SAMPLES_COLUMNS, (project_id,),
            )
            # Compatibility fallback for callers that have not adopted the
            # composition root yet. Production wiring uses the inventory port
            # above, so this branch is not a second authoritative runtime path.
            latest_by_sample: dict = {}
            for row in self._query(
                PROJECT_INTAKES_SQL, PROJECT_INTAKES_COLUMNS, (project_id,),
            ):
                latest_by_sample[row['sample_id']] = row
            for sample in samples:
                latest = latest_by_sample.get(sample['sample_id'])
                sample['latest_intake'] = latest
                sample['intake_count'] = int(latest['intake_count']) if latest else 0
        detail = dict(header[0])
        detail['samples'] = samples
        return detail

    def _read_inventory_samples(self, project_id: str) -> list[dict]:
        """Project the inventory SSOT into the older project-detail shape."""
        assert self._sample_inventory_read is not None
        rows: list[dict] = []
        after = None
        while True:
            page = self._sample_inventory_read.list_samples(
                project_id=project_id,
                status=None,
                include_deleted=True,
                after=after,
                limit=500,
            )
            for item in page.get('items') or []:
                latest = item.get('latest_intake')
                latest_envelope = None
                if latest is not None:
                    latest_envelope = {
                        'sample_id': item.get('sample_id'),
                        'sample_intake_id': latest.get('sample_intake_id', latest.get('id')),
                        'intake_date': latest.get('intake_date'),
                        'bl': latest.get('bl'),
                        'ap': latest.get('ap'),
                        'cp': latest.get('cp'),
                        'csc': latest.get('csc'),
                        'rf_cal': latest.get('rf_cal'),
                        'hw_rev': latest.get('hw_rev'),
                        'note': latest.get('note'),
                        'intake_count': item.get('intake_count', 0),
                    }
                rows.append({
                    'sample_id': item.get('sample_id'),
                    'sample_code': item.get('sample_code'),
                    'serial_number': item.get('serial_number'),
                    'model_id': item.get('model_id'),
                    'sample_number': item.get('sample_number'),
                    'test_category': item.get('test_category'),
                    'label_number': item.get('label_number'),
                    'smsn': item.get('smsn'),
                    'intake_cert': item.get('intake_cert'),
                    'assigned_team': item.get('assigned_team'),
                    'sender': item.get('sender'),
                    'receiver': item.get('receiver'),
                    'received_date': item.get('received_date'),
                    'released_date': item.get('released_date'),
                    'latest_intake': latest_envelope,
                    'intake_count': int(item.get('intake_count') or 0),
                })
            after = page.get('next_cursor')
            if not after:
                return rows

    def _query(self, statement: str, columns: tuple[str, ...], params: tuple) -> list[dict]:
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001 — wrap as loud CentralProjectError
            raise CentralProjectError(f'central project read connection failed: {exc}') from exc
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(statement, params)
                rows = list(cursor.fetchall())
            finally:
                cursor.close()
        except Exception as exc:  # noqa: BLE001
            raise CentralProjectError(f'central project read query failed: {exc}') from exc
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()
        return [dict(zip(columns, row)) for row in rows]
