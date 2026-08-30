"""Platform read API contracts (FE-P0d, 2026-05-27).

Dependency-free DTO/route/schema SSOT for the central read surface
(``/platform/...``) — the FE-P2 project-coverage dashboard and FE-P3 claim-lock
UX read through this contract. Mirrors the ``application.headless.api_contracts``
shape (routes/operations/schemas/permissions/path-params/descriptions) so the
shared OpenAPI builder + codegen chain treat both surfaces uniformly.

These contracts can move to a future ``fcc-test-contracts`` package without
pulling FastAPI / Pydantic / SQL / psycopg with them.

Read surface ownership: coverage source = central ``coverage_by_condition_hash``;
claim source = central ``active_claims``; ``condition_hash`` is the local-propagated
identity (never recomputed centrally). ``{project_id}`` is the central project
uuid — the views key on the uuid ``project_id`` column.

DECOMPOSITION (2026-08-29) — 이 모듈은 **facade** 다: 조립과 재노출만 한다.
route/permission/schema/operation 선언은 ``surface_*`` 모듈이 갖고, 표면 횡단
어휘는 ``api_vocabulary``/``api_parameters``/``api_operation_factory``/
``api_request_validation`` 이 갖는다. 공개 이름은 **하나도 바뀌지 않았다** —
이 파일을 읽는 56개 import 사이트가 그대로 동작하는 것이 분해의 조건이었다.
경계가 「표 종류」가 아니라 「operation 표면」인 이유는
``.claude/exec-plans/completed/2026-08-29-central-contract-decomposition.md`` §1 이 갖는다.
"""
from __future__ import annotations

from application.central_contract.api_parameters import (
    PLATFORM_API_PATH_PARAMS,
    PLATFORM_API_QUERY_PARAMS,
)
from application.central_contract.api_request_validation import (
    ProjectResultReferenceRequestUnprocessableError,
    validate_project_result_reference_request,
)
from application.central_contract.api_surfaces import merge_surface_table
from application.central_contract.api_vocabulary import (
    ARTIFACT_CUSTODY_FINDING_STATUSES,
    ARTIFACT_CUSTODY_REPORT_SCHEMA_VERSION,
    ARTIFACT_CUSTODY_STATUSES,
    CENTRAL_SYNC_READINESS_CODE_VALUES,
    CENTRAL_SYNC_READY_CODE,
    CHAMBER_RESULT_INGESTION_SCHEMA_VERSION,
    PLATFORM_API_COMPATIBILITY_MAJOR,
    PLATFORM_API_CONTRACT_VERSION,
    PLATFORM_API_PERMISSION_DESCRIPTIONS,
    PLATFORM_API_TITLE,
    PLATFORM_INTERNAL_RBAC_ROUTES,
    PLATFORM_NEXT_CURSOR_HEADER,
)

__all__ = [
    'PLATFORM_API_COMPATIBILITY_MAJOR',
    'PLATFORM_API_CONTRACT_VERSION',
    'PLATFORM_API_OPERATIONS',
    'PLATFORM_API_OPERATION_QUERY',
    'PLATFORM_API_OPERATION_QUERY_OVERRIDES',
    'PLATFORM_API_PATH_PARAMS',
    'PLATFORM_API_PERMISSIONS',
    'PLATFORM_API_PERMISSION_DESCRIPTIONS',
    'PLATFORM_API_QUERY_PARAMS',
    'PLATFORM_API_RESPONSE_HEADERS',
    'PLATFORM_API_ROUTES',
    'PLATFORM_INTERNAL_RBAC_ROUTES',
    'PLATFORM_API_SCHEMAS',
    'PLATFORM_API_TITLE',
    'PLATFORM_NEXT_CURSOR_HEADER',
    'CHAMBER_RESULT_INGESTION_SCHEMA_VERSION',
    'CENTRAL_SYNC_READINESS_CODE_VALUES',
    'CENTRAL_SYNC_READY_CODE',
    'ARTIFACT_CUSTODY_REPORT_SCHEMA_VERSION',
    'ARTIFACT_CUSTODY_STATUSES',
    'ARTIFACT_CUSTODY_FINDING_STATUSES',
    'ProjectResultReferenceRequestUnprocessableError',
    'validate_project_result_reference_request',
]


PLATFORM_API_ROUTES: dict[str, tuple[str, str]] = merge_surface_table('ROUTES')


# Permissions. FE-P2 (coverage) and FE-P3 read (claims) are reads of the central
# read model, gated by ``platform:read``. FE-P3-write (acquire/release) MUTATES the
# central claim ledger, so it is gated by a *separate* ``platform:claim`` token —
# a viewer can see coverage/locks without being able to acquire/release them. RBAC
# seed wiring (roles → token) is FE-P8 scope.
PLATFORM_API_PERMISSIONS: dict[str, str] = merge_surface_table('PERMISSIONS')


# operationId → accepted query params. Both read operations are keyset-paginated
# and accept the optional ``technology`` facet filter (coverage narrows the
# matrix; claims fetch only that technology's locks for the overlay).
PLATFORM_API_OPERATION_QUERY: dict[str, tuple[str, ...]] = merge_surface_table(
    'OPERATION_QUERY',
)


# ``status`` is shared by the project directory and sample inventory at the
# wire level, but the two resources have different closed sets. Keep the
# operation-specific narrowing here so generated clients cannot send the
# project-only ``completed`` value to a sample route.
PLATFORM_API_OPERATION_QUERY_OVERRIDES: dict[str, dict[str, dict]] = merge_surface_table(
    'OPERATION_QUERY_OVERRIDES',
)


# operationId → response headers. The paginated reads return the next-page cursor
# in PLATFORM_NEXT_CURSOR_HEADER (body stays a plain array).
PLATFORM_API_RESPONSE_HEADERS: dict[str, dict] = merge_surface_table('RESPONSE_HEADERS')


# Envelope shapes mirror the central view output columns (see
# docs/platform/central_db_schema.v1.json materialized_views/views). The read
# adapter contract test cross-checks every property against the view SELECT
# alias so a renamed column cannot silently drift the contract.
PLATFORM_API_SCHEMAS: dict[str, dict] = merge_surface_table('SCHEMAS')


PLATFORM_API_OPERATIONS: dict[str, dict] = merge_surface_table('OPERATIONS')
