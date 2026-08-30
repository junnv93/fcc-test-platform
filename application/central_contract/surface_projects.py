"""중앙 플랫폼 계약 — 프로젝트 레코드 · 진행/커버리지 롤업 · 멤버십.

``api_contracts`` facade 가 이 모듈의 표를 병합해 ``PLATFORM_API_*`` 로 재노출한다.
모듈 경계는 **표 종류가 아니라 operation 표면**이다 — git 이력 실측에서 계약 변경
커밋의 92%(52 중 48)가 정확히 한 표면만 만졌고, 표 종류로 자르면 엔드포인트 하나
추가가 항상 다섯 표를 동시에 만진다(네 표가 같은 operationId 키로 병렬 배열돼 있다).
"""
from __future__ import annotations

from application.central_contract.api_operation_factory import (
    _CLAIM_CONFLICT_409,
    _PROJECT_NOT_FOUND_404,
    _operation,
)
from application.central_contract.api_vocabulary import (
    PLATFORM_NEXT_CURSOR_HEADER,
    _UPDATE_PROJECT_PROPERTIES,
)
from fcc_test_contracts.common.access_policy import API_PERMISSION_AUTHENTICATED

#: 이 모듈이 소유하는 route path prefix. 분할은 **선언이 아니라 판정 대상**이다 —
#: ``tests/test_central_contract_decomposition_axis.py`` 가 prefix 집합이 쌍마다
#: 서로소이고 ``PLATFORM_API_ROUTES`` 의 모든 경로를 덮는지, 그리고 각 operation 이
#: 자기 경로의 **최장 일치** prefix 를 소유한 모듈에 물리적으로 선언돼 있는지 파생
#: 판정한다. 새 엔드포인트가 어느 모듈로 가야 하는지 사람이 기억하지 않는다.
SURFACE_PREFIXES: tuple[str, ...] = (
    '/platform/projects',
)


# 이 표면의 operation 만 참조하는 에러 응답 조각. 둘 이상의 표면이 참조하게 되면
# ``api_operation_factory`` 로 올라가야 하고, 그 판정도 파생 검사가 한다.
_MEMBERSHIP_404 = (
    'Membership target not found — unknown user_subject (assign) or '
    'no current (user, role) assignment (revoke).'
)

# W3 백엔드 — project identifier already taken (create + metadata edit both).
_PROJECT_IDENTIFIER_CONFLICT_409 = (
    'Project identifier conflict — the submitted 관리번호 (management_number) or '
    'model name is already used by another project. The problem body carries '
    'code=PROJECT_IDENTIFIER_CONFLICT and params.field naming the offending '
    'input, so the client can highlight exactly that field.'
)


ROUTES: dict[str, tuple[str, str]] = {
    # Phase 1 (2026-06-22) — 프로젝트 진입층. collection(list GET + create POST)은
    # /platform/projects 한 path item 공유(chambers GET+POST 동형); detail 은
    # {project_id} segment. 기존 /platform/projects/{project_id}/... 서브리소스와
    # 경로 깊이가 달라 충돌 없음.
    'list_projects': ('GET', '/platform/projects'),
    'create_project': ('POST', '/platform/projects'),
    'get_project': ('GET', '/platform/projects/{project_id}'),
    # W3 백엔드 — 성적서 표지 메타 부분 편집. detail GET 과 같은 path item 을 공유하는
    # PATCH(부분 갱신의 표준 method — PUT 은 전체 치환 semantics 라 "보내지 않은 필드
    # 무변경"을 표현할 수 없다). status 는 여기 없다: 상태 전이는 아래 complete/reopen
    # 액션 서브리소스가 SSOT 이고, model_name 은 정체성(ADR-0005 재키잉)이라 비범위.
    'update_project': ('PATCH', '/platform/projects/{project_id}'),
    'get_project_coverage': ('GET', '/platform/projects/{project_id}/coverage'),
    # FE-SYNC — central-data freshness for the project (read-only).
    'get_project_sync_status': ('GET', '/platform/projects/{project_id}/sync-status'),
    # Phase 6 — time-weighted progress rollup per (area, bucket) (read-only).
    'get_project_progress': ('GET', '/platform/projects/{project_id}/progress'),
    # FE-P8 membership — list (read), assign (admin write), revoke (admin write).
    # Assign and revoke share the same parent path with the list GET (one
    # OpenAPI path item, two methods); revoke is a POST sibling that takes the
    # target (user_subject, role_key) in the body so the URL stays opaque to
    # IdP subject formats (emails, ids, etc.) — path-encoding them is awkward
    # in trusted-header / OIDC environments and would force url-encoding rules
    # into the contract.
    'list_project_memberships': ('GET', '/platform/projects/{project_id}/memberships'),
    'assign_project_membership': ('POST', '/platform/projects/{project_id}/memberships'),
    'revoke_project_membership': (
        'POST', '/platform/projects/{project_id}/memberships/revoke',
    ),
    # project-status-visibility — explicit lifecycle action sub-resources (the
    # repo convention for state writes — cf. release / revoke / publish / archive
    # — over a generic PATCH ?status=). The target status is pinned by the path,
    # so there is no client-supplied status value to validate.
    'complete_project': ('POST', '/platform/projects/{project_id}/complete'),
    'reopen_project': ('POST', '/platform/projects/{project_id}/reopen'),
}


PERMISSIONS: dict[str, str] = {
    # Phase 1 (2026-06-22) — 프로젝트 진입층. ADR-0017 D3 인가 경계:
    # - list 는 호출자 subject 로 self-scoped(멤버 프로젝트만 반환)이므로
    #   'authenticated'. platform:read 로 게이트하면 멤버십이 아직 없는 신규 시험원이
    #   자기 (빈) 목록조차 못 보는 진입 chicken-and-egg 가 생긴다(platform:read 는
    #   project-membership 파생 grant). create 와 동일한 self-scoped 인가-클래스.
    # - detail 은 특정 프로젝트를 읽으므로 platform:read(멤버십 union — 멤버만 상세).
    # - create 는 project-scoped 밖 GLOBAL 연산(신규 프로젝트엔 멤버십이 아직 없음)이라
    #   'authenticated'. 생성자에게 서비스가 project_admin 멤버십을 자동 부여(D3).
    # 'authenticated' 는 grantable 토큰 아님(rbac_role_grants / permissions.ts 미등재,
    # parity universe 에서 'public' 동형 제외).
    'list_projects': API_PERMISSION_AUTHENTICATED,
    # Read-open (project-status-visibility) — the project directory + one
    # project's basic detail are visible to ANY authenticated principal (not
    # membership-scoped). Operational/membership reads below
    # (coverage/claims/progress/memberships/reports) stay platform:read.
    'get_project': API_PERMISSION_AUTHENTICATED,
    'create_project': API_PERMISSION_AUTHENTICATED,
    'get_project_coverage': 'platform:read',
    'get_project_sync_status': 'platform:read',
    # Phase 6 (2026-06-23) — time-weighted progress rollup read. Shares
    # platform:read (a project member reads their progress dashboard); no new
    # grantable token, so the rbac_role_grants bijection is unchanged.
    'get_project_progress': 'platform:read',
    # FE-P8 — membership read + admin write. Read shares platform:read so the
    # FE-P2 dashboard can show the project's RBAC roster without minting a
    # separate viewer token; write is gated by a distinct platform:admin so
    # role assignment never leaks down to engineer/viewer tokens.
    'list_project_memberships': 'platform:read',
    'assign_project_membership': 'platform:admin',
    'revoke_project_membership': 'platform:admin',
    # project-status-visibility — project completion lifecycle. Marking a project
    # completed / reopening it is a project-management act (same tier as
    # membership writes / report issuance), so it reuses platform:admin — no new
    # grantable token, the rbac_role_grants bijection is unchanged.
    'complete_project': 'platform:admin',
    'reopen_project': 'platform:admin',
    # W3 백엔드 — 성적서 표지 메타 부분 편집(PATCH). 프로젝트 속성 쓰기는 이미
    # admin 축에 있으므로(complete/reopen 과 동일 클래스) 기존 platform:admin 을
    # 미러링한다 — 새 정책이 아니고 신규 grantable 토큰도 0이라 rbac_role_grants ↔
    # permissions.ts ↔ Keycloak realm bijection 이 무변경이다.
    'update_project': 'platform:admin',
}


OPERATION_QUERY: dict[str, tuple[str, ...]] = {
    # project-status-visibility — the project directory accepts an optional
    # status filter (active|completed|all); the route defaults to 'active'.
    # W3 백엔드 — plus server-side search (``q``) + opt-in keyset pagination
    # (``limit``/``cursor``, same SSOT as coverage/claims/memberships). All three
    # omitted ⇒ the pre-W3 unbounded response, byte-identical.
    'list_projects': ('status', 'q', 'limit', 'cursor'),
    'get_project_coverage': ('limit', 'cursor', 'technology'),
    # FE-P8 membership listing — same opt-in keyset pagination as coverage /
    # claims. No technology facet (memberships are project-scoped, not
    # tech-scoped) — would be a meaningless filter.
    'list_project_memberships': ('limit', 'cursor'),
}


RESPONSE_HEADERS: dict[str, dict] = {
    # W3 백엔드 — the project directory joins the keyset-paginated reads.
    'list_projects': {
        PLATFORM_NEXT_CURSOR_HEADER: {
            'description': (
                'Opaque keyset cursor for the next page. Absent on the last page '
                'or an unbounded (no-limit) read. Pass it back as ?cursor= to continue.'
            ),
            'schema': {'type': 'string'},
        },
    },
    'get_project_coverage': {
        PLATFORM_NEXT_CURSOR_HEADER: {
            'description': (
                'Opaque keyset cursor for the next page. Absent on the last page '
                'or an unbounded (no-limit) read. Pass it back as ?cursor= to continue.'
            ),
            'schema': {'type': 'string'},
        },
    },
    'list_project_memberships': {
        PLATFORM_NEXT_CURSOR_HEADER: {
            'description': (
                'Opaque keyset cursor for the next page. Absent on the last page '
                'or an unbounded (no-limit) read. Pass it back as ?cursor= to continue.'
            ),
            'schema': {'type': 'string'},
        },
    },
}


SCHEMAS: dict[str, dict] = {
    # Phase 1 (2026-06-22) — 프로젝트 진입층 envelopes. ADR-0017 D1 의 1:1 overlay 라
    # 각 프로젝트는 model_name(device_models) 1개를 투영한다. detail 은 sample 목록
    # 동반(D2 — 측정 시점에 채워지므로 신규 프로젝트는 빈 배열).
    'ProjectList': {
        'type': 'array',
        'items': {'$ref': '#/schemas/ProjectEnvelope'},
    },
    'ProjectEnvelope': {
        'type': 'object',
        'required': ['project_id', 'project_code', 'model_name', 'sample_count'],
        'properties': {
            'project_id': {'type': 'string'},
            'project_code': {'type': 'string'},
            'model_name': {'type': 'string'},
            'customer': {'type': 'string', 'nullable': True},
            'manufacturer': {'type': 'string', 'nullable': True},
            'management_number': {'type': 'string', 'nullable': True},
            'status': {'type': 'string', 'nullable': True},
            'fcc_grantee_code': {'type': 'string', 'nullable': True},
            'applicant_name': {'type': 'string', 'nullable': True},
            'applicant_address': {'type': 'string', 'nullable': True},
            'eut_description': {'type': 'string', 'nullable': True},
            'test_standard': {'type': 'string', 'nullable': True},
            'fcc_id': {'type': 'string', 'nullable': True},
            'sample_count': {'type': 'integer'},
        },
        'additionalProperties': False,
    },
    'SampleEnvelope': {
        'type': 'object',
        'required': ['sample_id', 'sample_code', 'latest_intake', 'intake_count'],
        'properties': {
            'sample_id': {'type': 'string'},
            'sample_code': {'type': 'string'},
            'serial_number': {'type': 'string', 'nullable': True},
            'model_id': {'type': 'string', 'nullable': True},
            # PM 칸 인벤토리 메타(Phase C, 전부 text nullable).
            'sample_number': {'type': 'string', 'nullable': True},
            'test_category': {'type': 'string', 'nullable': True},
            'label_number': {'type': 'string', 'nullable': True},
            'smsn': {'type': 'string', 'nullable': True},
            'intake_cert': {'type': 'string', 'nullable': True},
            'assigned_team': {'type': 'string', 'nullable': True},
            'sender': {'type': 'string', 'nullable': True},
            'receiver': {'type': 'string', 'nullable': True},
            'received_date': {'type': 'string', 'nullable': True},
            'released_date': {'type': 'string', 'nullable': True},
            # 시험원 입고 칸 — compact read shape (payload-reduction, 2026-06-23).
            # The full append-only intake history is NOT shipped per project-detail
            # response; only the latest intake (nullable; null when no history) +
            # a total count. The latest selection is the read adapter's SSOT
            # (created_at DESC) — the UI/reporting never page the whole history.
            # Required nullable field — express as ``anyOf: [$ref, {type: null}]``
            # (NOT ``nullable + allOf``, which openapi-typescript materializes as
            # the unusable ``null & SampleIntakeEnvelope`` intersection instead of
            # the intended ``SampleIntakeEnvelope | null`` union).
            'latest_intake': {
                'anyOf': [
                    {'$ref': '#/schemas/SampleIntakeEnvelope'},
                    {'type': 'null'},
                ],
            },
            'intake_count': {'type': 'integer'},
        },
        'additionalProperties': False,
    },
    'SampleIntakeEnvelope': {
        'type': 'object',
        'required': ['sample_intake_id'],
        'properties': {
            'sample_intake_id': {'type': 'string'},
            'intake_date': {'type': 'string', 'nullable': True},
            'bl': {'type': 'string', 'nullable': True},
            'ap': {'type': 'string', 'nullable': True},
            'cp': {'type': 'string', 'nullable': True},
            'csc': {'type': 'string', 'nullable': True},
            'rf_cal': {'type': 'string', 'nullable': True},
            'hw_rev': {'type': 'string', 'nullable': True},
            'note': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
    'ProjectDetailEnvelope': {
        'type': 'object',
        'required': ['project_id', 'project_code', 'model_name', 'samples'],
        'properties': {
            'project_id': {'type': 'string'},
            'project_code': {'type': 'string'},
            'model_name': {'type': 'string'},
            'customer': {'type': 'string', 'nullable': True},
            'manufacturer': {'type': 'string', 'nullable': True},
            'management_number': {'type': 'string', 'nullable': True},
            'status': {'type': 'string', 'nullable': True},
            'fcc_grantee_code': {'type': 'string', 'nullable': True},
            'applicant_name': {'type': 'string', 'nullable': True},
            'applicant_address': {'type': 'string', 'nullable': True},
            'eut_description': {'type': 'string', 'nullable': True},
            'test_standard': {'type': 'string', 'nullable': True},
            'fcc_id': {'type': 'string', 'nullable': True},
            'created_at': {'type': 'string'},
            'samples': {
                'type': 'array',
                'items': {'$ref': '#/schemas/SampleEnvelope'},
            },
        },
        'additionalProperties': False,
    },
    # POST body — model_name 만 필수(ADR-0017 D1, project_code==model name). customer/
    # manufacturer optional. sample 은 측정 시점 지정(Phase 3)이라 생성 body 에 없다.
    'CreateProjectRequest': {
        'type': 'object',
        'required': ['model_name'],
        'properties': {
            'model_name': {'type': 'string', 'minLength': 1},
            'customer': {'type': 'string', 'nullable': True},
            'manufacturer': {'type': 'string', 'nullable': True},
            'management_number': {'type': 'string', 'nullable': True},
            'fcc_grantee_code': {'type': 'string', 'nullable': True},
            'applicant_name': {'type': 'string', 'nullable': True},
            'applicant_address': {'type': 'string', 'nullable': True},
            'eut_description': {'type': 'string', 'nullable': True},
            'test_standard': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
    # PATCH body — 성적서 표지 메타 부분 편집(W3 백엔드). required 없음: 보낸 키만
    # 갱신되고 보내지 않은 키는 무변경이다. 각 필드가 nullable 인 것은 "값 삭제"를
    # 표현하기 위함 — 명시적 null 은 해당 칸을 비운다(COALESCE 머지 아님).
    # additionalProperties: False 라 status / model_name 동봉은 계약 위반으로
    # 드러난다(서버도 loud 400 으로 거부 — 조용한 무시 0).
    'UpdateProjectRequest': {
        'type': 'object',
        'properties': _UPDATE_PROJECT_PROPERTIES,
        'additionalProperties': False,
    },
    'ProjectCoverageList': {
        'type': 'array',
        'items': {'$ref': '#/schemas/CoverageEnvelope'},
    },
    # Phase 6 — time-weighted progress rollup. ``percent`` is null when the bucket
    # has no priced time (no fake 0%/100%); ``progress_bucket_id`` is null for an
    # unbucketable condition group. unpriced/unbucketable surfaced as counts.
    'ProjectProgressList': {
        'type': 'array',
        'items': {'$ref': '#/schemas/ProgressBucketEnvelope'},
    },
    'ProgressBucketEnvelope': {
        'type': 'object',
        'required': [
            'progress_area', 'planned_minutes', 'completed_minutes',
            'total_conditions',
        ],
        'properties': {
            'progress_area': {'type': 'string'},
            'progress_bucket_id': {'type': 'string', 'nullable': True},
            'planned_minutes': {'type': 'number'},
            'completed_minutes': {'type': 'number'},
            'percent': {'type': 'number', 'nullable': True},
            'total_conditions': {'type': 'integer'},
            'priced_conditions': {'type': 'integer'},
            'unpriced_conditions': {'type': 'integer'},
            'unbucketable_conditions': {'type': 'integer'},
        },
        'additionalProperties': False,
    },
    'CoverageEnvelope': {
        'type': 'object',
        # Existing coverage responses remain byte-compatible. Provider-scoped
        # selection is exposed by the new result-selection routes; this legacy
        # coverage envelope does not require an additive field from old readers.
        'required': ['project_id', 'technology', 'condition_hash', 'attempt_count'],
        'properties': {
            'project_id': {'type': 'string'},
            'technology': {'type': 'string'},
            'condition_hash': {'type': 'string'},
            'latest_session_id': {'type': 'string'},
            'latest_operator': {'type': 'string'},
            'latest_measured_at': {'type': 'string'},
            'latest_verdict': {'type': 'string'},
            'latest_attempt_number': {'type': 'integer', 'nullable': True},
            'attempt_count': {'type': 'integer'},
            # FE-P3 duplicate-quality: distinct session / operator counts over the
            # partition. distinct_operator_count > 1 ⇒ a true cross-engineer
            # duplicate; = 1 with attempt_count > 1 ⇒ one engineer's re-measure.
            'distinct_session_count': {'type': 'integer'},
            'distinct_operator_count': {'type': 'integer'},
        },
        'additionalProperties': False,
    },
    # FE-SYNC central freshness. last_ingested_at / age_seconds are null for an
    # empty project (nothing measured). is_stale ⇒ age_seconds > the threshold;
    # the UI softens the duplicate-prevention guarantee when true.
    'SyncStatusEnvelope': {
        'type': 'object',
        'required': [
            'project_id', 'server_time', 'is_stale', 'stale_threshold_seconds',
            'condition_count', 'active_claim_count', 'expired_open_claim_count',
        ],
        'properties': {
            'project_id': {'type': 'string'},
            'server_time': {'type': 'string'},
            'last_ingested_at': {'type': 'string', 'nullable': True},
            'age_seconds': {'type': 'integer', 'nullable': True},
            'is_stale': {'type': 'boolean'},
            'stale_threshold_seconds': {'type': 'integer'},
            'condition_count': {'type': 'integer'},
            'active_claim_count': {'type': 'integer'},
            'expired_open_claim_count': {'type': 'integer'},
        },
        'additionalProperties': False,
    },
    # FE-P8 membership envelope — one row per (project_id, user_subject, role_key)
    # assignment. expires_at NULL ⇒ no expiry. The actor/audit context lives in
    # audit_events; this envelope is just the current assignment fact.
    'MembershipList': {
        'type': 'array',
        'items': {'$ref': '#/schemas/MembershipEnvelope'},
    },
    'MembershipEnvelope': {
        'type': 'object',
        'required': ['project_id', 'user_subject', 'role_key', 'assigned_at'],
        'properties': {
            'project_id': {'type': 'string'},
            # Identity issuer for the (issuer, subject) user key. Present on server
            # responses; optional so subject-based clients (UI optimistic updates)
            # need not synthesize it.
            'user_issuer': {'type': 'string'},
            'user_subject': {'type': 'string'},
            'role_key': {'type': 'string'},
            'assigned_at': {'type': 'string'},
            'expires_at': {'type': 'string', 'nullable': True},
            # Phase D — 시험원 하위 team(RF/SAR) 분류 라벨(권한 직교, nullable).
            'team': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
    'AssignMembershipRequest': {
        'type': 'object',
        'required': ['user_subject', 'role_key'],
        'properties': {
            # Optional: a blank/absent issuer defaults to the legacy issuer so the
            # subject-based UI can assign without knowing the issuer URL.
            'user_issuer': {'type': 'string'},
            'user_subject': {'type': 'string', 'minLength': 1},
            'role_key': {'type': 'string', 'minLength': 1},
            'expires_at': {'type': 'string', 'nullable': True},
            # Phase D — optional 시험원 하위 team(RF/SAR); validated against TEAM_CODES.
            'team': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
    'RevokeMembershipRequest': {
        'type': 'object',
        'required': ['user_subject', 'role_key'],
        'properties': {
            # Optional: a blank/absent issuer defaults to the legacy issuer so the
            # subject-based UI can revoke without knowing the issuer URL.
            'user_issuer': {'type': 'string'},
            'user_subject': {'type': 'string', 'minLength': 1},
            'role_key': {'type': 'string', 'minLength': 1},
        },
        'additionalProperties': False,
    },
}


OPERATIONS: dict[str, dict] = {
    # Phase 1 (2026-06-22) — 프로젝트 진입층. list/detail read + create.
    'list_projects': _operation(
        request=None,
        response='ProjectList',
        permission=PERMISSIONS['list_projects'],
    ),
    'get_project': _operation(
        request=None,
        response='ProjectDetailEnvelope',
        permission=PERMISSIONS['get_project'],
        error_responses={'404': _PROJECT_NOT_FOUND_404},
    ),
    'create_project': _operation(
        request='CreateProjectRequest',
        response='ProjectDetailEnvelope',
        permission=PERMISSIONS['create_project'],
        error_responses={'409': _PROJECT_IDENTIFIER_CONFLICT_409},
    ),
    'update_project': _operation(
        request='UpdateProjectRequest',
        response='ProjectDetailEnvelope',
        permission=PERMISSIONS['update_project'],
        error_responses={
            '404': _PROJECT_NOT_FOUND_404,
            '409': _PROJECT_IDENTIFIER_CONFLICT_409,
        },
    ),
    'get_project_coverage': _operation(
        request=None,
        response='ProjectCoverageList',
        permission=PERMISSIONS['get_project_coverage'],
    ),
    'get_project_sync_status': _operation(
        request=None,
        response='SyncStatusEnvelope',
        permission=PERMISSIONS['get_project_sync_status'],
    ),
    'get_project_progress': _operation(
        request=None,
        response='ProjectProgressList',
        permission=PERMISSIONS['get_project_progress'],
    ),
    'list_project_memberships': _operation(
        request=None,
        response='MembershipList',
        permission=PERMISSIONS['list_project_memberships'],
    ),
    'assign_project_membership': _operation(
        request='AssignMembershipRequest',
        response='MembershipEnvelope',
        permission=PERMISSIONS['assign_project_membership'],
        error_responses={'409': _CLAIM_CONFLICT_409, '404': _MEMBERSHIP_404},
    ),
    'revoke_project_membership': _operation(
        request='RevokeMembershipRequest',
        response='MembershipEnvelope',
        permission=PERMISSIONS['revoke_project_membership'],
        error_responses={'409': _CLAIM_CONFLICT_409, '404': _MEMBERSHIP_404},
    ),
    'complete_project': _operation(
        request=None,
        response='ProjectDetailEnvelope',
        permission=PERMISSIONS['complete_project'],
        error_responses={'404': _PROJECT_NOT_FOUND_404},
    ),
    'reopen_project': _operation(
        request=None,
        response='ProjectDetailEnvelope',
        permission=PERMISSIONS['reopen_project'],
        error_responses={'404': _PROJECT_NOT_FOUND_404},
    ),
}
