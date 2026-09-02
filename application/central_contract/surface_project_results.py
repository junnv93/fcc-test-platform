"""중앙 플랫폼 계약 — 측정 결과 표면 — claim 원장 · 결과 선정 · 프로젝트 참조값.

``api_contracts`` facade 가 이 모듈의 표를 병합해 ``PLATFORM_API_*`` 로 재노출한다.
모듈 경계는 **표 종류가 아니라 operation 표면**이다 — git 이력 실측에서 계약 변경
커밋의 92%(52 중 48)가 정확히 한 표면만 만졌고, 표 종류로 자르면 엔드포인트 하나
추가가 항상 다섯 표를 동시에 만진다(네 표가 같은 operationId 키로 병렬 배열돼 있다).
"""
from __future__ import annotations

from application.central_contract.api_operation_factory import _CLAIM_CONFLICT_409, _operation
from application.central_contract.api_vocabulary import PLATFORM_NEXT_CURSOR_HEADER

#: 이 모듈이 소유하는 route path prefix. 분할은 **선언이 아니라 판정 대상**이다 —
#: ``tests/test_central_contract_decomposition_axis.py`` 가 prefix 집합이 쌍마다
#: 서로소이고 ``PLATFORM_API_ROUTES`` 의 모든 경로를 덮는지, 그리고 각 operation 이
#: 자기 경로의 **최장 일치** prefix 를 소유한 모듈에 물리적으로 선언돼 있는지 파생
#: 판정한다. 새 엔드포인트가 어느 모듈로 가야 하는지 사람이 기억하지 않는다.
SURFACE_PREFIXES: tuple[str, ...] = (
    '/platform/projects/{project_id}/claims',
    '/platform/projects/{project_id}/providers',
    '/platform/projects/{project_id}/project-result-references',
)


ROUTES: dict[str, tuple[str, str]] = {
    'list_project_claims': ('GET', '/platform/projects/{project_id}/claims'),
    'list_project_result_selections': (
        'GET', '/platform/projects/{project_id}/providers/{provider_id}/result-selections',
    ),
    'list_project_result_attempts': (
        'GET', '/platform/projects/{project_id}/providers/{provider_id}/conditions/{condition_hash}/attempts',
    ),
    'select_project_result': (
        'POST', '/platform/projects/{project_id}/providers/{provider_id}/conditions/{condition_hash}/selection-events',
    ),
    'clear_project_result_selection': (
        'DELETE', '/platform/projects/{project_id}/providers/{provider_id}/conditions/{condition_hash}/selection-events',
    ),
    'list_project_result_references': (
        'GET', '/platform/projects/{project_id}/project-result-references',
    ),
    'create_project_result_reference': (
        'POST', '/platform/projects/{project_id}/project-result-references',
    ),
    'retire_project_result_reference': (
        'POST', '/platform/projects/{project_id}/project-result-references/{revision_id}/retire',
    ),
    # plan-delivery (2026-09-02) — 게시된 계획을 중앙이 «안다» 고 만드는 유일한 쓰기.
    #
    # ⚠️ 이것은 진행률 분모를 채우는 일이면서 **동시에** 「그 계획을 아는가」의 답이다.
    # ``build_measurement_snapshot`` 이 ``published_plan_expectation`` 을 조회해
    # ``published_plan_id is unknown`` 을 판정하기 때문이다. 그래서 이 표가 비어 있는
    # 동안에는 브라우저로 발행한 계획으로 **측정을 시작할 수 없다**(실측 2026-09-01:
    # ``400``). 채우는 경로는 지금까지 중앙 PostgreSQL 직결 하나였고, 계획을 저작하는
    # headless 는 그 tier 에 닿을 수 없다(compose 가 그렇게 갈라 둔다).
    #
    # 경로가 (project, provider) 복합 스코프인 것은 그 둘이 expectation 자연키의
    # 구성요소이기 때문이고, 형태는 형제 ``.../providers/{provider_id}/...`` 를 그대로
    # 따른다.
    'ingest_published_plan_expectation': (
        'POST', '/platform/projects/{project_id}/providers/{provider_id}/published-plans',
    ),
    # FE-P3-write — acquire/release on the central append-only claim ledger. The
    # acquire POST shares the /claims path with the list GET (one OpenAPI path
    # item, two methods); release adds the {claim_id} segment.
    'acquire_project_claim': ('POST', '/platform/projects/{project_id}/claims'),
    'release_project_claim': (
        'POST', '/platform/projects/{project_id}/claims/{claim_id}/release',
    ),
}


PERMISSIONS: dict[str, str] = {
    'list_project_claims': 'platform:read',
    'list_project_result_selections': 'platform:read',
    'list_project_result_attempts': 'platform:read',
    'select_project_result': 'platform:claim',
    'clear_project_result_selection': 'platform:claim',
    'list_project_result_references': 'platform:read',
    'create_project_result_reference': 'platform:reference-write',
    'retire_project_result_reference': 'platform:reference-write',
    'acquire_project_claim': 'platform:claim',
    'release_project_claim': 'platform:claim',
    # 게시한 사람이 곧 이 쓰기의 행위자다 — 시험원이 계획을 발행하는 그 요청의
    # 자격으로 중앙에 알린다. 측정 시작(``start_chamber_measurement``)과 같은
    # engineer 티어를 재사용한다: 새 grantable 토큰은 중앙 ``rbac_role_grants``
    # 동일성(권한 − node-scoped == grant)을 깨서 스코프 밖 스키마 변경을 부른다.
    'ingest_published_plan_expectation': 'platform:claim',
}


OPERATION_QUERY: dict[str, tuple[str, ...]] = {
    'list_project_claims': ('limit', 'cursor', 'technology'),
    'list_project_result_selections': ('limit', 'cursor'),
    'list_project_result_attempts': ('limit', 'cursor'),
    'list_project_result_references': ('provider_id', 'state', 'limit', 'cursor'),
}


OPERATION_QUERY_OVERRIDES: dict[str, dict[str, dict]] = {
    'list_project_result_references': {
        'state': {'type': 'string', 'enum': ['published', 'retired']},
    },
}


RESPONSE_HEADERS: dict[str, dict] = {
    'list_project_claims': {
        PLATFORM_NEXT_CURSOR_HEADER: {
            'description': (
                'Opaque keyset cursor for the next page. Absent on the last page '
                'or an unbounded (no-limit) read. Pass it back as ?cursor= to continue.'
            ),
            'schema': {'type': 'string'},
        },
    },
    'list_project_result_selections': {
        PLATFORM_NEXT_CURSOR_HEADER: {
            'description': 'Opaque cursor for the next provider-scoped result page.',
            'schema': {'type': 'string'},
        },
    },
    'list_project_result_attempts': {
        PLATFORM_NEXT_CURSOR_HEADER: {
            'description': 'Opaque cursor for the next deterministic attempt page.',
            'schema': {'type': 'string'},
        },
    },
    'list_project_result_references': {
        PLATFORM_NEXT_CURSOR_HEADER: {
            'description': 'Opaque cursor for the next reference revision page.',
            'schema': {'type': 'string'},
        },
    },
}


SCHEMAS: dict[str, dict] = {
    # plan-delivery (2026-09-02) — 게시 계획의 «잴 것» 을 중앙 어휘로 옮긴 것.
    #
    # ⚠️ **provider 어휘를 담지 않는다.** 게시 row 는 ``mode_family``/``tone``/
    # ``antenna``/``packet``/``capability_path`` 처럼 이 provider 의 말을 나르는데,
    # 그것을 중앙에 실으면 provider 가 늘 때마다 중앙 마이그레이션이 필요해진다
    # (CLAUDE.md §Chamber Equipment Config SSOT 가 이름 붙인 형태). 여기 있는 넷은
    # ``published_plan_expectation`` 이 이미 쓰는 중립 토큰뿐이고, 계획의 **바이트**는
    # 저작한 상자에 남는다 — 챔버는 그것을 headless 표면에서 직접 읽는다.
    'PublishedPlanCondition': {
        'type': 'object',
        'required': ['condition_hash', 'technology', 'band', 'raw_test_type'],
        'properties': {
            # publish 시점에 확정된 stable hash — **재계산하지 않고 verbatim** 나른다.
            # 진행률 join 축이 바로 이 값이다.
            'condition_hash': {'type': 'string'},
            'technology': {'type': 'string'},
            'band': {'type': 'string'},
            'raw_test_type': {'type': 'string'},
        },
        'additionalProperties': False,
    },
    'IngestPublishedPlanRequest': {
        'type': 'object',
        'required': ['plan_id', 'conditions'],
        'properties': {
            'plan_id': {'type': 'string', 'minLength': 1},
            # ISO-8601. 읽기 축이 (project, provider) 당 MAX(plan_published_at) 의
            # 계획만 롤업하므로(migration 006), 이 값이 없으면 그 계획은 «가장 오래된»
            # 것으로 취급된다 — 누락은 조용한 0%가 아니라 조용한 뒤처짐이다.
            'plan_published_at': {'type': 'string', 'nullable': True},
            'conditions': {
                'type': 'array',
                'items': {'$ref': '#/schemas/PublishedPlanCondition'},
            },
        },
        'additionalProperties': False,
    },
    'IngestPublishedPlanResult': {
        'type': 'object',
        'required': ['plan_id', 'conditions', 'inserted', 'updated'],
        'properties': {
            'plan_id': {'type': 'string'},
            'conditions': {'type': 'integer'},
            'inserted': {'type': 'integer'},
            'updated': {'type': 'integer'},
            # 가격이 붙은 조건 수와 붙지 않은 수를 **따로** 답한다. 분모(개수)는
            # 카탈로그 없이도 완전하고 ETA 만 없는 것이므로, 그 둘을 한 숫자로 접으면
            # 「계획을 모른다」와 「예상 시간을 모른다」가 구분되지 않는다.
            'priced': {'type': 'integer'},
            'unpriced': {'type': 'integer'},
            'unbucketable': {'type': 'integer'},
            'catalog_version': {'type': 'integer', 'nullable': True},
        },
        'additionalProperties': False,
    },
    'ActiveClaimList': {
        'type': 'array',
        'items': {'$ref': '#/schemas/ActiveClaimEnvelope'},
    },
    'ActiveClaimEnvelope': {
        'type': 'object',
        'required': ['project_id', 'claim_id', 'condition_hash'],
        'properties': {
            'project_id': {'type': 'string'},
            'claim_id': {'type': 'string'},
            'technology': {'type': 'string'},
            'condition_hash': {'type': 'string'},
            'operator': {'type': 'string'},
            'occurred_at': {'type': 'string'},
            'expires_at': {'type': 'string', 'nullable': True},
            'session_id': {'type': 'string'},
        },
        'additionalProperties': False,
    },
    # FE-P3-write request/response envelopes. Acquire requires the trio that
    # identifies the lock target (technology/condition_hash/operator); release
    # carries only optional provenance (the claim_id is a path param). The
    # response is the resulting ledger event for both.
    'AcquireClaimRequest': {
        'type': 'object',
        'required': ['technology', 'condition_hash', 'operator'],
        'properties': {
            'technology': {'type': 'string', 'minLength': 1},
            'condition_hash': {'type': 'string', 'minLength': 1},
            'operator': {'type': 'string', 'minLength': 1},
            'session_id': {'type': 'string', 'nullable': True},
            'reason': {'type': 'string', 'nullable': True},
            'expires_at': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
    'ReleaseClaimRequest': {
        'type': 'object',
        'properties': {
            'operator': {'type': 'string', 'nullable': True},
            'reason': {'type': 'string', 'nullable': True},
            'action': {'type': 'string', 'enum': ['released', 'expired']},
        },
        'additionalProperties': False,
    },
    'ClaimEventEnvelope': {
        'type': 'object',
        'required': ['project_id', 'claim_id', 'condition_hash', 'action'],
        'properties': {
            'project_id': {'type': 'string'},
            'claim_id': {'type': 'string'},
            'technology': {'type': 'string'},
            'condition_hash': {'type': 'string'},
            'operator': {'type': 'string'},
            'action': {'type': 'string'},
            'occurred_at': {'type': 'string'},
            'expires_at': {'type': 'string', 'nullable': True},
            'session_id': {'type': 'string', 'nullable': True},
            'reason': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
    # Cross-session result selection. All result payload/provenance fields are
    # opaque provider-owned objects; the platform owns only the identity and
    # selection ledger facts.
    'ResultSelectionList': {
        'type': 'array',
        'items': {'$ref': '#/schemas/ResultSelectionEnvelope'},
    },
    'ResultSelectionEnvelope': {
        'type': 'object',
        'required': [
            'project_id', 'provider_id', 'condition_hash', 'attempt_id',
            'session_id', 'status', 'selection_source', 'selection_revision',
        ],
        'properties': {
            'project_id': {'type': 'string'},
            'provider_id': {'type': 'string'},
            'condition_hash': {'type': 'string'},
            'attempt_id': {'type': 'string'},
            'session_id': {'type': 'string'},
            'provider_session_id': {'type': 'string', 'nullable': True},
            'sample_id': {'type': 'string', 'nullable': True},
            'chamber_id': {'type': 'string', 'nullable': True},
            'operator': {'type': 'string', 'nullable': True},
            'measured_at': {'type': 'string', 'nullable': True},
            'created_at': {'type': 'string'},
            'verdict': {'type': 'string', 'nullable': True},
            'status': {'type': 'string'},
            'attempt_number': {'type': 'integer', 'nullable': True},
            'result_json': {'type': 'object', 'additionalProperties': True, 'nullable': True},
            'provenance_json': {'type': 'object', 'additionalProperties': True, 'nullable': True},
            'selection_source': {'type': 'string', 'enum': ['latest', 'manual']},
            'selected_attempt_id': {'type': 'string', 'nullable': True},
            'selection_revision': {'type': 'integer', 'minimum': 0},
        },
        'additionalProperties': False,
    },
    'MeasurementAttemptList': {
        'type': 'array',
        'items': {'$ref': '#/schemas/MeasurementAttemptEnvelope'},
    },
    'MeasurementAttemptEnvelope': {
        'type': 'object',
        'required': ['attempt_id', 'project_id', 'provider_id', 'condition_hash', 'status'],
        'properties': {
            'attempt_id': {'type': 'string'},
            'project_id': {'type': 'string'},
            'provider_id': {'type': 'string'},
            'condition_hash': {'type': 'string'},
            'session_id': {'type': 'string'},
            'provider_session_id': {'type': 'string', 'nullable': True},
            'sample_id': {'type': 'string', 'nullable': True},
            'chamber_id': {'type': 'string', 'nullable': True},
            'operator': {'type': 'string', 'nullable': True},
            'measured_at': {'type': 'string', 'nullable': True},
            'created_at': {'type': 'string'},
            'verdict': {'type': 'string', 'nullable': True},
            'status': {'type': 'string'},
            'attempt_number': {'type': 'integer', 'nullable': True},
            'result_json': {'type': 'object', 'additionalProperties': True, 'nullable': True},
            'provenance_json': {'type': 'object', 'additionalProperties': True, 'nullable': True},
        },
        'additionalProperties': False,
    },
    'SelectionEventRequest': {
        'type': 'object',
        'required': ['expected_revision'],
        'properties': {
            'attempt_id': {'type': 'string', 'nullable': True},
            'expected_revision': {'type': 'integer', 'minimum': 0},
            'reason': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
    'SelectionEventEnvelope': {
        'type': 'object',
        'required': [
            'id', 'project_id', 'provider_id', 'condition_hash', 'action',
            'revision', 'expected_revision', 'actor_subject', 'occurred_at',
        ],
        'properties': {
            'id': {'type': 'string'},
            'project_id': {'type': 'string'},
            'provider_id': {'type': 'string'},
            'condition_hash': {'type': 'string'},
            'action': {'type': 'string', 'enum': ['selected', 'cleared']},
            'attempt_id': {'type': 'string', 'nullable': True},
            'revision': {'type': 'integer', 'minimum': 1},
            'expected_revision': {'type': 'integer', 'minimum': 0},
            'actor_subject': {'type': 'string'},
            'occurred_at': {'type': 'string'},
            'reason': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
    'ProjectResultReferenceList': {
        'type': 'array',
        'items': {'$ref': '#/schemas/ProjectResultReferenceEnvelope'},
    },
    'ProjectResultReferenceEnvelope': {
        'type': 'object',
        'required': [
            'revision_id', 'project_id', 'producer_provider_id', 'reference_type',
            'schema_version', 'source_selection_event_id', 'source_attempt_id',
            'source_session_id', 'payload', 'content_sha256', 'state',
            'revision_number', 'created_by', 'created_at',
        ],
        'properties': {
            'revision_id': {'type': 'string'},
            'project_id': {'type': 'string'},
            'producer_provider_id': {'type': 'string'},
            'reference_type': {'type': 'string'},
            'schema_version': {'type': 'string'},
            'source_selection_event_id': {'type': 'string'},
            'source_attempt_id': {'type': 'string'},
            'source_session_id': {'type': 'string'},
            'source_sample_id': {'type': 'string', 'nullable': True},
            'source_chamber_id': {'type': 'string', 'nullable': True},
            'payload': {'type': 'object', 'additionalProperties': True},
            'content_sha256': {'type': 'string'},
            'state': {'type': 'string', 'enum': ['published', 'retired']},
            'revision_number': {'type': 'integer', 'minimum': 1},
            'created_by': {'type': 'string'},
            'created_at': {'type': 'string'},
            'retired_by': {'type': 'string', 'nullable': True},
            'retired_at': {'type': 'string', 'nullable': True},
            'retirement_reason': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
    'CreateProjectResultReferenceRequest': {
        'type': 'object',
        'required': ['provider_id', 'condition_hash'],
        'properties': {
            # Natural provider key + exact condition are the only source
            # selection intent the browser may submit.  The server resolves the
            # current selected event/attempt/session and the provider adapter
            # produces the opaque type/schema/payload/hash.
            'provider_id': {'type': 'string', 'minLength': 1},
            'condition_hash': {'type': 'string', 'minLength': 1},
            'reason': {'type': 'string', 'minLength': 1, 'maxLength': 500},
        },
        'additionalProperties': False,
    },
    'RetireProjectResultReferenceRequest': {
        'type': 'object',
        'required': ['reason'],
        'properties': {'reason': {'type': 'string', 'minLength': 1}},
        'additionalProperties': False,
    },
}


OPERATIONS: dict[str, dict] = {
    'list_project_claims': _operation(
        request=None,
        response='ActiveClaimList',
        permission=PERMISSIONS['list_project_claims'],
    ),
    'acquire_project_claim': _operation(
        request='AcquireClaimRequest',
        response='ClaimEventEnvelope',
        permission=PERMISSIONS['acquire_project_claim'],
        error_responses={'409': _CLAIM_CONFLICT_409},
    ),
    'release_project_claim': _operation(
        request='ReleaseClaimRequest',
        response='ClaimEventEnvelope',
        permission=PERMISSIONS['release_project_claim'],
        error_responses={'409': _CLAIM_CONFLICT_409},
    ),
    'list_project_result_selections': _operation(
        request=None,
        response='ResultSelectionList',
        permission=PERMISSIONS['list_project_result_selections'],
    ),
    'list_project_result_attempts': _operation(
        request=None,
        response='MeasurementAttemptList',
        permission=PERMISSIONS['list_project_result_attempts'],
    ),
    'select_project_result': _operation(
        request='SelectionEventRequest',
        response='SelectionEventEnvelope',
        permission=PERMISSIONS['select_project_result'],
        error_responses={'404': 'The attempt is not in the exact project/provider/condition scope.', '409': 'The selection revision is stale.'},
    ),
    'clear_project_result_selection': _operation(
        request='SelectionEventRequest',
        response='SelectionEventEnvelope',
        permission=PERMISSIONS['clear_project_result_selection'],
        error_responses={'409': 'The selection revision is stale.'},
    ),
    'list_project_result_references': _operation(
        request=None,
        response='ProjectResultReferenceList',
        permission=PERMISSIONS['list_project_result_references'],
    ),
    'create_project_result_reference': _operation(
        request='CreateProjectResultReferenceRequest',
        response='ProjectResultReferenceEnvelope',
        permission=PERMISSIONS['create_project_result_reference'],
        error_responses={
            '404': 'The current selected source was not found.',
            '409': 'The reference revision is retired or otherwise unavailable.',
            '400': 'The provider-authored reference envelope is incompatible or has an invalid hash.',
            '422': 'The publication request contains invalid or server-owned fields.',
        },
    ),
    'ingest_published_plan_expectation': _operation(
        request='IngestPublishedPlanRequest',
        response='IngestPublishedPlanResult',
        permission=PERMISSIONS['ingest_published_plan_expectation'],
        error_responses={
            '404': 'The project or the provider does not exist centrally.',
            '422': 'The ingest envelope is malformed or carries no conditions.',
        },
    ),
    'retire_project_result_reference': _operation(
        request='RetireProjectResultReferenceRequest',
        response='ProjectResultReferenceEnvelope',
        permission=PERMISSIONS['retire_project_result_reference'],
        error_responses={'404': 'The published reference revision was not found.'},
    ),
}
