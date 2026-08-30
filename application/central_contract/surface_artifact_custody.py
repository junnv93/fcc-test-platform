"""중앙 플랫폼 계약 — 플롯 이중 보관 (프로젝트 조회 측 + 챔버 보고 측).

``api_contracts`` facade 가 이 모듈의 표를 병합해 ``PLATFORM_API_*`` 로 재노출한다.
모듈 경계는 **표 종류가 아니라 operation 표면**이다 — git 이력 실측에서 계약 변경
커밋의 92%(52 중 48)가 정확히 한 표면만 만졌고, 표 종류로 자르면 엔드포인트 하나
추가가 항상 다섯 표를 동시에 만진다(네 표가 같은 operationId 키로 병렬 배열돼 있다).
"""
from __future__ import annotations

from application.central_contract.api_operation_factory import (
    _CHAMBER_NOT_FOUND_404,
    _operation,
)
from application.central_contract.api_vocabulary import (
    ARTIFACT_CUSTODY_FINDING_STATUSES,
    ARTIFACT_CUSTODY_REPORT_SCHEMA_VERSION,
    ARTIFACT_CUSTODY_STATUSES,
)

#: 이 모듈이 소유하는 route path prefix. 분할은 **선언이 아니라 판정 대상**이다 —
#: ``tests/test_central_contract_decomposition_axis.py`` 가 prefix 집합이 쌍마다
#: 서로소이고 ``PLATFORM_API_ROUTES`` 의 모든 경로를 덮는지, 그리고 각 operation 이
#: 자기 경로의 **최장 일치** prefix 를 소유한 모듈에 물리적으로 선언돼 있는지 파생
#: 판정한다. 새 엔드포인트가 어느 모듈로 가야 하는지 사람이 기억하지 않는다.
SURFACE_PREFIXES: tuple[str, ...] = (
    '/platform/projects/{project_id}/artifact-custody',
    '/platform/chambers/{chamber_id}/artifact-custody-reports',
)


ROUTES: dict[str, tuple[str, str]] = {
    # plot-dual-custody ① — 보관 판정 수신은 노드 아래(머신 토큰이 경로 chamber_id 에
    # 바인딩), 조회는 프로젝트 아래(시험원이 묻는 질문이 "이 프로젝트/이 성적서를 낼 수
    # 있나"이므로). 두 축의 경로가 다른 것은 **소유자가 다르기 때문**이다 — 판정을
    # 만드는 것은 챔버이고 판정을 소비하는 것은 프로젝트다.
    'push_artifact_custody_report': (
        'POST', '/platform/chambers/{chamber_id}/artifact-custody-reports',
    ),
    'get_project_artifact_custody': (
        'GET', '/platform/projects/{project_id}/artifact-custody',
    ),
    'get_artifact_custody_snapshot': (
        'GET', '/platform/projects/{project_id}/artifact-custody/{snapshot_id}',
    ),
}


PERMISSIONS: dict[str, str] = {
    # plot-dual-custody ① (2026-08-09) — 플롯 원본 보관 현황.
    #
    # **쓰기는 노드-스코프다.** 중앙은 회사 파일서버도 챔버 PC 로컬 디스크도 열 수
    # 없으므로 보관 여부를 **판정할 수 없다** — 판정은 증거가 있는 노드에서만 나온다.
    # 그래서 이 축은 heartbeat / result-ingestion / reference-bundle 과 같은
    # platform:chamber 머신 토큰이고 경로 chamber_id 에 바인딩된다.
    #
    # **읽기는 platform:read 다.** 보관 현황은 프로젝트 상태의 일부(이 성적서를 낼 수
    # 있나)이고 coverage / claims / progress 와 같은 뷰어 티어다.
    #
    # 신규 grantable 토큰 0 — rbac_role_grants ↔ permissions.ts ↔ Keycloak realm
    # bijection 무변경.
    'push_artifact_custody_report': 'platform:chamber',
    'get_project_artifact_custody': 'platform:read',
    'get_artifact_custody_snapshot': 'platform:read',
}


SCHEMAS: dict[str, dict] = {
    'ArtifactCustodyReportRequest': {
        'type': 'object',
        'required': [
            'schema_version', 'chamber_id', 'provider_id', 'sessions',
        ],
        'properties': {
            'schema_version': {
                'type': 'string', 'const': ARTIFACT_CUSTODY_REPORT_SCHEMA_VERSION,
            },
            'chamber_id': {'type': 'string', 'minLength': 1},
            'provider_id': {'type': 'string', 'minLength': 1},
            'sessions': {
                'type': 'array',
                'minItems': 1,
                'items': {'$ref': '#/schemas/ArtifactCustodySessionReport'},
            },
        },
        'additionalProperties': False,
    },
    'ArtifactCustodySessionReport': {
        'type': 'object',
        'required': ['provider_session_id', 'status', 'counts', 'observed_at'],
        'properties': {
            'provider_session_id': {
                'type': 'string', 'minLength': 1,
                'description': (
                    'The node-local session id verbatim — the same value the result '
                    'ingestion envelope carries, so the central row joins to '
                    'test_sessions on its existing natural key without a second '
                    'identity derivation.'
                ),
            },
            'status': {'type': 'string', 'enum': list(ARTIFACT_CUSTODY_STATUSES)},
            'counts': {
                'type': 'object',
                'description': 'Per-status tallies. Keys are the four custody status tokens.',
                'additionalProperties': {'type': 'integer', 'minimum': 0},
            },
            'observed_at': {
                'type': 'string',
                'description': (
                    'When the NODE opened the storage roots — not when central '
                    'received it. Stored writes are latest-wins on this value so a '
                    'retried stale observation cannot overwrite a newer verdict.'
                ),
            },
            'roots': {
                'type': 'array', 'items': {'type': 'string'},
                'description': (
                    'Storage roots actually opened. Carried so a tester can argue '
                    'with the verdict — a judgement that will not say where it '
                    'looked cannot be rebutted.'
                ),
            },
            'session_label': {'type': 'string'},
            'findings': {
                'type': 'array',
                'items': {'$ref': '#/schemas/ArtifactCustodyFinding'},
                'description': (
                    'Actionable items only (non-verified). The verified tally stays '
                    'in counts; shipping every verified row would replicate ~1,000 '
                    'entries per session for a list nobody can act on.'
                ),
            },
        },
        'additionalProperties': False,
    },
    'ArtifactCustodyFinding': {
        'type': 'object',
        'required': ['relative_path', 'status'],
        'properties': {
            'relative_path': {'type': 'string', 'minLength': 1},
            'status': {
                'type': 'string', 'enum': list(ARTIFACT_CUSTODY_FINDING_STATUSES),
            },
            'artifact_type': {'type': 'string'},
            'expected_sha256': {'type': 'string'},
            'observed_sha256': {'type': 'string'},
            'reason': {'type': 'string'},
        },
        'additionalProperties': False,
    },
    'ArtifactCustodyReportReceipt': {
        'type': 'object',
        'required': ['schema_version', 'chamber_id', 'accepted', 'superseded', 'received_at'],
        'properties': {
            'schema_version': {
                'type': 'string', 'const': ARTIFACT_CUSTODY_REPORT_SCHEMA_VERSION,
            },
            'chamber_id': {'type': 'string'},
            'accepted': {
                'type': 'array', 'items': {'type': 'string'},
                'description': 'provider_session_ids whose snapshot was stored.',
            },
            'superseded': {
                'type': 'array', 'items': {'type': 'string'},
                'description': (
                    'provider_session_ids rejected because central already holds a '
                    'NEWER observation. Reported rather than silently dropped so the '
                    'node can tell "stored" from "arrived out of order".'
                ),
            },
            'received_at': {'type': 'string'},
        },
        'additionalProperties': False,
    },
    # 프로젝트 축 조회 — 요약 + 세션 행. 모든 파생값(상태 토큰·집계·신선도)은 서버가
    # 계산해서 내려준다: 프론트가 재조립하면 규칙이 두 언어로 쪼개져 조용히 드리프트한다
    # (Derived-Value No-Client-Recompute SSOT).
    'ProjectArtifactCustody': {
        'type': 'object',
        'required': [
            'project_id', 'status', 'counts', 'session_count',
            'blocking_session_count', 'unresolved_session_count',
            'missing_snapshot_session_count', 'sessions',
        ],
        'properties': {
            'project_id': {'type': 'string'},
            'status': {
                'type': 'string', 'enum': list(ARTIFACT_CUSTODY_STATUSES),
                'description': (
                    'Worst status across the project. Blocking is never absorbed — '
                    'one MISSING session makes the project MISSING. A ratio would '
                    'read "98% transferred" as a pass, but the 2% missing at audit '
                    'is a 100% problem.'
                ),
            },
            'counts': {
                'type': 'object', 'additionalProperties': {'type': 'integer', 'minimum': 0},
            },
            'session_count': {'type': 'integer', 'minimum': 0},
            'blocking_session_count': {'type': 'integer', 'minimum': 0},
            'unresolved_session_count': {
                'type': 'integer', 'minimum': 0,
                'description': (
                    'Reported custody snapshots that are NOT attributed to this '
                    'project because their session row has no project_id yet. '
                    'Surfaced rather than silently zero: "this project is fine" must '
                    'be distinguishable from "what we can see of this project is fine".'
                ),
            },
            'missing_snapshot_session_count': {
                'type': 'integer', 'minimum': 0,
                'description': (
                    'Project test sessions that have no custody snapshot at all. '
                    'This is distinct from unresolved_session_count, where a '
                    'snapshot exists but its session is not attributed to a project.'
                ),
            },
            'oldest_observed_at': {'type': 'string', 'nullable': True},
            'newest_observed_at': {'type': 'string', 'nullable': True},
            'sessions': {
                'type': 'array', 'items': {'$ref': '#/schemas/ArtifactCustodySessionSummary'},
            },
        },
        'additionalProperties': False,
    },
    'ArtifactCustodySessionSummary': {
        'type': 'object',
        'required': [
            'snapshot_id', 'provider_session_id', 'chamber_id', 'status',
            'counts', 'observed_at', 'is_blocking',
        ],
        'properties': {
            'snapshot_id': {'type': 'string'},
            'provider_session_id': {'type': 'string'},
            'chamber_id': {'type': 'string'},
            'session_label': {'type': 'string', 'nullable': True},
            'status': {'type': 'string', 'enum': list(ARTIFACT_CUSTODY_STATUSES)},
            'counts': {
                'type': 'object', 'additionalProperties': {'type': 'integer', 'minimum': 0},
            },
            'observed_at': {'type': 'string'},
            'reported_at': {'type': 'string', 'nullable': True},
            'is_blocking': {
                'type': 'boolean',
                'description': (
                    'Whether this session would block report issuance. Server-derived '
                    'from the same rule the publish gate uses, so the screen and the '
                    'gate cannot disagree.'
                ),
            },
            'roots': {'type': 'array', 'items': {'type': 'string'}},
        },
        'additionalProperties': False,
    },
    'ArtifactCustodySnapshotDetail': {
        'type': 'object',
        'required': ['snapshot_id', 'provider_session_id', 'chamber_id', 'status', 'counts', 'observed_at', 'findings'],
        'properties': {
            'snapshot_id': {'type': 'string'},
            'provider_session_id': {'type': 'string'},
            'chamber_id': {'type': 'string'},
            'session_label': {'type': 'string', 'nullable': True},
            'status': {'type': 'string', 'enum': list(ARTIFACT_CUSTODY_STATUSES)},
            'counts': {
                'type': 'object', 'additionalProperties': {'type': 'integer', 'minimum': 0},
            },
            'observed_at': {'type': 'string'},
            'reported_at': {'type': 'string', 'nullable': True},
            'roots': {'type': 'array', 'items': {'type': 'string'}},
            'findings': {
                'type': 'array', 'items': {'$ref': '#/schemas/ArtifactCustodyFinding'},
            },
        },
        'additionalProperties': False,
    },
}


OPERATIONS: dict[str, dict] = {
    'push_artifact_custody_report': _operation(
        request='ArtifactCustodyReportRequest',
        response='ArtifactCustodyReportReceipt',
        permission=PERMISSIONS['push_artifact_custody_report'],
        error_responses={
            '400': 'Custody report envelope failed schema or vocabulary validation.',
            '404': _CHAMBER_NOT_FOUND_404,
        },
    ),
    'get_project_artifact_custody': _operation(
        request=None,
        response='ProjectArtifactCustody',
        permission=PERMISSIONS['get_project_artifact_custody'],
        error_responses={
            '404': 'Project not found.',
        },
    ),
    'get_artifact_custody_snapshot': _operation(
        request=None,
        response='ArtifactCustodySnapshotDetail',
        permission=PERMISSIONS['get_artifact_custody_snapshot'],
        error_responses={
            # 프로젝트 귀속을 확인하지 않으면 한 프로젝트의 뷰어가 snapshot_id 만으로
            # 다른 프로젝트의 보관 상세를 읽는다. 미귀속은 404 다(존재를 노출하지 않는다).
            '404': 'Snapshot not found, or it does not belong to this project.',
        },
    ),
}
