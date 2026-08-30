"""중앙 플랫폼 계약 — 경로/질의 파라미터 사전.

두 표는 operationId 가 아니라 **파라미터 이름**으로 키잉되므로 표면에 속하지 않는다.
(operationId 로 키잉되는 ``OPERATION_QUERY``/``RESPONSE_HEADERS`` 는 표면 모듈이 갖는다.)
"""
from __future__ import annotations

from application.central_contract.api_vocabulary import (
    _CATALOG_FAMILY_VALUES,
    _REFERENCE_SCOPE_KIND_VALUES,
    _REVISION_STATE_VALUES,
)
from application.central_contract.pagination import MAX_PAGE_SIZE

# Every ``{name}`` token in PLATFORM_API_ROUTES MUST have an entry here. The
# central views key project state on a uuid ``project_id`` column, so the path
# param is a uuid-format string (not the local integer/text ids of the headless
# surface) — the OpenAPI builder emits the exact type instead of a heuristic.
PLATFORM_API_PATH_PARAMS: dict[str, dict] = {
    'project_id': {'type': 'string', 'format': 'uuid', 'minLength': 1},
    'sample_id': {'type': 'string', 'format': 'uuid', 'minLength': 1},
    'template': {'type': 'string', 'enum': ['pm-status', 'rf-data']},
    # WEB-PROVIDER-UI-0 — provider identity (e.g. 'fcc-unlicensed-conducted'),
    # a free string (NOT a uuid) keyed in the provider registry.
    'provider_id': {'type': 'string', 'minLength': 1},
    # Measurement identity is an opaque provider-produced condition hash.
    'condition_hash': {'type': 'string', 'minLength': 1},
    # FE-P3-write release route — the claim_id of the acquired event to release
    # (the central claim_events.claim_id uuid that groups acquire/release).
    'claim_id': {'type': 'string', 'format': 'uuid', 'minLength': 1},
    # 멀티챔버 P5 — chamber natural key (e.g. 'chamber-a'), a free string keyed in
    # the central chamber_nodes registry (NOT a uuid).
    'chamber_id': {'type': 'string', 'minLength': 1},
    # Wave 3 — a central reference_revisions.id (uuid, DB-owned default).
    'revision_id': {'type': 'string', 'format': 'uuid', 'minLength': 1},
    # 성적서 §6 장비목록 — a central test_equipment_lists.id (uuid).
    'equipment_list_id': {'type': 'string', 'format': 'uuid', 'minLength': 1},
    # plot-dual-custody ① — a central artifact_custody_snapshots.id (uuid,
    # DB-owned default). The node's natural key (chamber_id + provider_session_id)
    # is NOT used in the drilldown path: it would put a node-chosen string in a
    # project-scoped URL, and the list response already hands the client a
    # server-owned id.
    'snapshot_id': {'type': 'string', 'format': 'uuid', 'minLength': 1},
}


# Keyset-pagination + facet query params (FE-P0d + Phase B technology facet).
# ``limit``/``maximum``/``default`` are derived from the pagination SSOT (no
# duplicated magic number); ``cursor`` is an opaque token. ``technology`` is an
# optional server-side facet filter (Phase B) — the dedup dashboard narrows the
# 16k+ conditions to a single technology the operator already knows, so the
# relevant subset is reachable without paging the whole project. The value is a
# free string (the central view's ``technology`` column is data-derived — no
# hardcoded enum here or in the UI). The OpenAPI builder emits all of these as
# ``in: query`` so the generated TS client is fully typed.
# ``limit`` is OPTIONAL with no default: omitting it returns all rows (backward
# compatible with the shipped dashboard); supplying it (1..MAX_PAGE_SIZE) enables
# keyset pagination. DEFAULT_PAGE_SIZE is the in-bounds clamp ceiling reference,
# not a server-applied default. ``technology`` omitted ⇒ no filter (all techs).
PLATFORM_API_QUERY_PARAMS: dict[str, dict] = {
    'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_PAGE_SIZE},
    'cursor': {'type': 'string'},
    'technology': {'type': 'string', 'minLength': 1},
    # Phase G — optional edition token feeding the derived report_number of a
    # report-header citation. Omitted ⇒ citation has no report_number (the SN /
    # firmware / applicant meta is still assembled).
    'edition': {'type': 'string', 'minLength': 1},
    # Optional immutable session cut for report citation. Omitted preserves the
    # current project citation behavior.
    'session_id': {'type': 'string', 'format': 'uuid', 'minLength': 1},
    # project-status-visibility — project directory status filter. Mirrors the
    # sealed projects.status domain (+ 'all'); omitted ⇒ the route defaults to
    # 'active' (in-progress projects).
    'status': {'type': 'string', 'enum': ['active', 'completed', 'deleted', 'all']},
    # W3 백엔드 — 프로젝트 디렉터리 서버측 검색. 대소문자 무관 부분일치로
    # ``project_directory_query.PROJECT_SEARCH_COLUMNS`` SSOT (관리번호 포함) 를
    # 훑는다. 값 도메인을 열거하지 않는 이유: 관리번호/모델명/고객사는 데이터
    # 파생이라 서버가 알고 있는 enum 이 없다. 생략/공백 ⇒ 필터 없음.
    'q': {'type': 'string', 'minLength': 1},
    # Wave 3 — reference-catalog facets. family / scope_kind / state enumerate
    # from the domain enums (see _CATALOG_FAMILY_VALUES above) because those ARE
    # closed vocabularies; scope_id is data-derived (a room id or a project id)
    # so it stays a free string, exactly like ``technology``.
    'family': {'type': 'string', 'enum': _CATALOG_FAMILY_VALUES},
    'scope_kind': {'type': 'string', 'enum': _REFERENCE_SCOPE_KIND_VALUES},
    'scope_id': {'type': 'string', 'minLength': 1},
    'provider_id': {'type': 'string', 'minLength': 1},
    'state': {'type': 'string', 'enum': _REVISION_STATE_VALUES},
    # The project currently running in the room a bundle is fetched for. Named
    # ``scope_project_id`` rather than ``project_id`` so it can never be confused
    # with the path parameter of the project-scoped routes — the bundle route is
    # keyed by chamber, and this narrows the PROJECT-scoped families inside it.
    # Omitted ⇒ only the ROOM-scoped families are delivered.
    'scope_project_id': {'type': 'string', 'format': 'uuid', 'minLength': 1},
    # Conditional-fetch token. Echo the previous bundle_etag and the server
    # answers ``unchanged: true`` with no rows, so a node that is already current
    # costs one small round trip instead of a full re-download.
    'bundle_etag': {'type': 'string', 'minLength': 1},
    # Web sample inventory filters. ``status`` is shared with project routes;
    # the schema builder applies the narrower sample subset to sample operations.
    'project_id': {'type': 'string', 'format': 'uuid', 'minLength': 1},
    'team': {'type': 'string', 'minLength': 1},
    'as_of': {'type': 'string', 'format': 'date-time'},
    'after': {'type': 'string'},
    'include_deleted': {'type': 'boolean', 'default': False},
}
