"""중앙 플랫폼 계약 — 성적서 · §6 시험장비 목록 · 인용.

``api_contracts`` facade 가 이 모듈의 표를 병합해 ``PLATFORM_API_*`` 로 재노출한다.
모듈 경계는 **표 종류가 아니라 operation 표면**이다 — git 이력 실측에서 계약 변경
커밋의 92%(52 중 48)가 정확히 한 표면만 만졌고, 표 종류로 자르면 엔드포인트 하나
추가가 항상 다섯 표를 동시에 만진다(네 표가 같은 operationId 키로 병렬 배열돼 있다).
"""
from __future__ import annotations

from application.central_contract.api_operation_factory import _operation
from application.central_contract.api_vocabulary import (
    _EQUIPMENT_ITEM_TEXT_FIELDS,
    _EQUIPMENT_ITEM_TYPE_VALUES,
    _EQUIPMENT_LIST_STATUS_VALUES,
    _EQUIPMENT_TEST_ITEM_VALUES,
)

#: 이 모듈이 소유하는 route path prefix. 분할은 **선언이 아니라 판정 대상**이다 —
#: ``tests/test_central_contract_decomposition_axis.py`` 가 prefix 집합이 쌍마다
#: 서로소이고 ``PLATFORM_API_ROUTES`` 의 모든 경로를 덮는지, 그리고 각 operation 이
#: 자기 경로의 **최장 일치** prefix 를 소유한 모듈에 물리적으로 선언돼 있는지 파생
#: 판정한다. 새 엔드포인트가 어느 모듈로 가야 하는지 사람이 기억하지 않는다.
SURFACE_PREFIXES: tuple[str, ...] = (
    '/platform/projects/{project_id}/reports',
    '/platform/projects/{project_id}/report-sessions',
    '/platform/projects/{project_id}/report-citation',
    '/platform/projects/{project_id}/equipment-lists',
)


# 이 표면의 operation 만 참조하는 에러 응답 조각. 둘 이상의 표면이 참조하게 되면
# ``api_operation_factory`` 로 올라가야 하고, 그 판정도 파생 검사가 한다.
# Phase G — report create conflict / unknown project on the report surface.
_REPORT_EDITION_CONFLICT_409 = (
    'Report edition conflict — a test_reports row with the same '
    '(project_id, edition) already exists. Editions are unique within a project.'
)

_REPORT_PROJECT_NOT_FOUND_404 = (
    'Unknown project_id — no central projects row to attach the report to / cite.'
)

# 성적서 §6 장비목록 — error responses (SSOT).
_EQUIPMENT_LIST_PROJECT_NOT_FOUND_404 = (
    'Unknown project_id — no central projects row owns this equipment list.'
)

_EQUIPMENT_LIST_NOT_FOUND_404 = (
    'Unknown equipment_list_id for this project. A list that belongs to another '
    'project is reported as 404 rather than 403 — a 403 would leak the fact that '
    'the id exists.'
)

_EQUIPMENT_LIST_DUPLICATE_409 = (
    'An equipment list already exists for this test_item_key. Uniqueness is '
    'scoped by two partial unique indexes: (project_id, test_item_key) while the '
    'list is not yet attached to a report, and (test_report_id, test_item_key) '
    'once it is — the same model can have several report editions whose '
    'equipment differs.'
)

_EQUIPMENT_LIST_FROZEN_409 = (
    'The equipment list is confirmed and cannot be edited. A confirmed list is '
    'the snapshot the report was rendered from; editing it would change an '
    'already-issued report when it is regenerated.'
)

_EQUIPMENT_LIST_NOT_ATTACHABLE_409 = (
    'The equipment list cannot be attached to a report — it is confirmed (its '
    'snapshot is already frozen), or it is already attached to another edition. '
    'A list belongs to exactly one report edition; per-edition equipment lists are '
    'separate rows, not a moved one.'
)

_EQUIPMENT_LIST_NOT_CONFIRMABLE_409 = (
    'The equipment list cannot be confirmed — it is already confirmed, or it has '
    'no items. An empty §6 table is refused at report generation anyway, so it is '
    'refused here rather than one step later.'
)


ROUTES: dict[str, tuple[str, str]] = {
    'list_project_report_sessions': (
        'GET', '/platform/projects/{project_id}/report-sessions',
    ),
    # Phase G (2026-06-23) — test_reports 성적서 surface. collection(list GET +
    # create POST)은 /platform/projects/{project_id}/reports 한 path item 공유
    # (projects collection 동형); citation 은 sibling GET (다른 경로 깊이라 충돌 없음).
    'list_reports': ('GET', '/platform/projects/{project_id}/reports'),
    'create_report': ('POST', '/platform/projects/{project_id}/reports'),
    # 성적서 §6 장비목록 (2026-08-07). list/create 는 한 path item 을 공유한다
    # (projects/reports 컬렉션과 동일 형태). items 는 전량 교체 PUT 하나 —
    # sort_order 가 위치로 정의되므로 행 단위 엔드포인트를 두면 재정렬 계약이
    #하나 더 필요해진다. confirm 은 액션 서브리소스다(동결은 상태 전이).
    'list_test_equipment_lists': ('GET', '/platform/projects/{project_id}/equipment-lists'),
    'create_test_equipment_list': ('POST', '/platform/projects/{project_id}/equipment-lists'),
    'get_test_equipment_list': (
        'GET', '/platform/projects/{project_id}/equipment-lists/{equipment_list_id}',
    ),
    'replace_test_equipment_list_items': (
        'PUT', '/platform/projects/{project_id}/equipment-lists/{equipment_list_id}/items',
    ),
    'confirm_test_equipment_list': (
        'POST', '/platform/projects/{project_id}/equipment-lists/{equipment_list_id}/confirm',
    ),
    'attach_test_equipment_list': (
        'POST', '/platform/projects/{project_id}/equipment-lists/{equipment_list_id}/attach',
    ),
    'get_report_citation': (
        'GET', '/platform/projects/{project_id}/report-citation',
    ),
}


PERMISSIONS: dict[str, str] = {
    'list_project_report_sessions': 'platform:read',
    # Phase G (2026-06-23) — test_reports 성적서 surface. Read(list + header
    # citation) shares platform:read (a project member views the project's reports
    # / cited SN·firmware·report_number) and create is platform:admin (issuing a
    # certificate is a project-management act, same tier as membership writes). No
    # new grantable token → the rbac_role_grants bijection is unchanged.
    'list_reports': 'platform:read',
    'create_report': 'platform:admin',
    'get_report_citation': 'platform:read',
    # 성적서 §6 장비목록 (2026-08-07) — **신규 grantable 토큰 0**.
    # 읽기는 platform:read, 쓰기는 platform:claim 을 재사용한다.
    # platform:claim 은 이미 engineer 티어 mutating 액션의 토큰이고
    # (start_chamber_measurement 선례), rbac_role_grants 상
    # project_engineer·project_admin 만 보유하고 project_viewer·project_pm 은
    # 보유하지 않는다 → "시험원은 편집, 뷰어는 열람"이 그대로 성립한다.
    # platform:admin 을 쓰면 시험원이 자기 목록을 확정하지 못한다.
    # 새 토큰을 만들면 rbac_role_grants ↔ permissions.ts ↔ Keycloak realm
    # bijection 3자 갱신을 강제하므로 별도 wave 의 일이다.
    'list_test_equipment_lists': 'platform:read',
    'get_test_equipment_list': 'platform:read',
    'create_test_equipment_list': 'platform:claim',
    'replace_test_equipment_list_items': 'platform:claim',
    'confirm_test_equipment_list': 'platform:claim',
    'attach_test_equipment_list': 'platform:claim',
}


OPERATION_QUERY: dict[str, tuple[str, ...]] = {
    # Phase G — report-header citation accepts an optional edition (→ report_number).
    'get_report_citation': ('edition', 'session_id'),
}


SCHEMAS: dict[str, dict] = {
    'ProjectReportSessionList': {
        'type': 'array',
        'items': {'$ref': '#/schemas/ProjectReportSessionEnvelope'},
    },
    'ProjectReportSessionEnvelope': {
        'type': 'object',
        'required': [
            'project_id', 'submit_session_id', 'node_id', 'node_name',
            'node_base_url', 'completed_conditions', 'technologies',
        ],
        'properties': {
            'project_id': {'type': 'string'},
            'submit_session_id': {'type': 'integer', 'minimum': 1},
            'node_id': {'type': 'string'},
            'node_name': {'type': 'string'},
            'node_base_url': {'type': 'string'},
            'latest_measured_at': {'type': 'string', 'nullable': True},
            'latest_verdict': {'type': 'string', 'nullable': True},
            'completed_conditions': {'type': 'integer'},
            'technologies': {'type': 'array', 'items': {'type': 'string'}},
        },
        'additionalProperties': False,
    },
    # Phase G (2026-06-23) — test_reports 성적서 envelopes. report_number is
    # DERIVED (S-{management_number}-{edition}); the create body never carries it.
    'ReportList': {
        'type': 'array',
        'items': {'$ref': '#/schemas/ReportEnvelope'},
    },
    'ReportEnvelope': {
        'type': 'object',
        'required': ['report_id', 'project_id', 'created_at'],
        'properties': {
            'report_id': {'type': 'string'},
            'project_id': {'type': 'string'},
            'edition': {'type': 'string', 'nullable': True},
            # Derived (never stored): S-{management_number}-{edition}; null when the
            # project has no management_number or the row has no edition.
            'report_number': {'type': 'string', 'nullable': True},
            'date_of_issue': {'type': 'string', 'nullable': True},
            'date_tested_start': {'type': 'string', 'nullable': True},
            'date_tested_end': {'type': 'string', 'nullable': True},
            'prepared_by': {'type': 'string', 'nullable': True},
            'prepared_site': {'type': 'string', 'nullable': True},
            'rev_history': {
                'type': 'array', 'nullable': True,
                'items': {'type': 'object', 'additionalProperties': True},
            },
            'created_at': {'type': 'string'},
        },
        'additionalProperties': False,
    },
    'CreateReportRequest': {
        'type': 'object',
        'required': ['edition'],
        'properties': {
            'edition': {'type': 'string', 'minLength': 1},
            'date_of_issue': {'type': 'string', 'nullable': True},
            'date_tested_start': {'type': 'string', 'nullable': True},
            'date_tested_end': {'type': 'string', 'nullable': True},
            'prepared_by': {'type': 'string', 'nullable': True},
            'prepared_site': {'type': 'string', 'nullable': True},
            'rev_history_json': {
                'type': 'array', 'nullable': True,
                'items': {'type': 'object', 'additionalProperties': True},
            },
        },
        'additionalProperties': False,
    },
    # 성적서 §6 장비목록 (2026-08-07) — 프로젝트가 실제로 사용한 장비/시험용
    # 소프트웨어. EMS 가 표준 리스트의 SSOT 이고 여기는 실사용 스냅샷이다.
    # 어휘(item_type/status)와 항목 필드 집합은 도메인 정책에서 파생한다.
    'TestEquipmentListSummary': {
        'type': 'object',
        'required': ['list_id', 'project_id', 'test_item_key', 'status', 'item_count'],
        'properties': {
            'list_id': {'type': 'string'},
            'project_id': {'type': 'string'},
            # 성적서에 붙기 전(시험 ~90% 시점)에는 null — 그 단계의 목록은
            # 프로젝트에만 귀속된다.
            'test_report_id': {'type': 'string', 'nullable': True},
            # 시험항목 = 성적서 한 편(E6~E9). 어휘는 FCC 가 소유한다 — 어떤
            # 측정이 어떤 성적서로 가는지는 ReportTech / tech_partitioner /
            # 템플릿 4파일이 이미 아는 사실이다. EMS 표기('DFS, UNII')를 축으로
            # 삼으면 항목 하나가 성적서 여러 편에 걸친다.
            'test_item_key': {'type': 'string', 'enum': _EQUIPMENT_TEST_ITEM_VALUES},
            'test_item_name': {'type': 'string', 'nullable': True},
            'status': {'type': 'string', 'enum': _EQUIPMENT_LIST_STATUS_VALUES},
            # EMS pull 이 채울 자리 — 지금은 항상 null 이고, pull 어댑터가
            # 붙어도 계약은 바뀌지 않는다.
            'source_profile_key': {'type': 'string', 'nullable': True},
            'source_revision_key': {'type': 'string', 'nullable': True},
            'source_pulled_at': {'type': 'string', 'nullable': True},
            'confirmed_at': {'type': 'string', 'nullable': True},
            'created_at': {'type': 'string', 'nullable': True},
            'updated_at': {'type': 'string', 'nullable': True},
            'item_count': {'type': 'integer'},
        },
        'additionalProperties': False,
    },
    # 목록 응답은 배열이 아니라 봉투다 — 고를 수 있는 시험항목 어휘를 함께
    # 실어 보낸다. 상세 응답이 두 표의 열 순서를 `tables` 로 내려보내는 것과
    # 같은 결정이다: 어휘/열 순서를 프론트가 자기 쪽에 적으면 같은 규칙이
    # TS/Python 두 곳으로 쪼개지고, 그 드리프트는 제출된 성적서에서만 드러난다.
    # (생성된 TS 타입은 타입 레벨 union 이라 런타임 배열을 주지 못한다.)
    'TestEquipmentListCollection': {
        'type': 'object',
        'required': ['lists', 'test_items'],
        'properties': {
            'lists': {
                'type': 'array',
                'items': {'$ref': '#/schemas/TestEquipmentListSummary'},
            },
            'test_items': {
                'type': 'array',
                'items': {'type': 'string', 'enum': _EQUIPMENT_TEST_ITEM_VALUES},
            },
        },
        'additionalProperties': False,
    },
    'TestEquipmentListItem': {
        'type': 'object',
        'required': ['item_id', 'item_type', 'sort_order'],
        'properties': {
            'item_id': {'type': 'string'},
            'item_type': {'type': 'string', 'enum': _EQUIPMENT_ITEM_TYPE_VALUES},
            # 서버가 배열 위치에서 부여한다(응답에는 있고 요청에는 없다).
            'sort_order': {'type': 'integer'},
            **{
                field: {'type': 'string', 'nullable': True}
                for field in _EQUIPMENT_ITEM_TEXT_FIELDS
            },
        },
        'additionalProperties': False,
    },
    # 두 표의 열 순서를 서버가 실어 보낸다 — 프론트/DOCX patcher 가 열 이름을
    # 자기 쪽에 다시 적지 않도록(Derived-Value No-Client-Recompute).
    'TestEquipmentTableSpec': {
        'type': 'object',
        'required': ['item_type', 'columns'],
        'properties': {
            'item_type': {'type': 'string', 'enum': _EQUIPMENT_ITEM_TYPE_VALUES},
            'columns': {'type': 'array', 'items': {'type': 'string'}},
        },
        'additionalProperties': False,
    },
    'TestEquipmentListEnvelope': {
        'type': 'object',
        'required': ['list', 'items', 'tables'],
        'properties': {
            'list': {'$ref': '#/schemas/TestEquipmentListSummary'},
            'items': {
                'type': 'array',
                'items': {'$ref': '#/schemas/TestEquipmentListItem'},
            },
            'tables': {
                'type': 'array',
                'items': {'$ref': '#/schemas/TestEquipmentTableSpec'},
            },
        },
        'additionalProperties': False,
    },
    'CreateTestEquipmentListRequest': {
        'type': 'object',
        'required': ['test_item_key'],
        'properties': {
            'test_item_key': {'type': 'string', 'enum': _EQUIPMENT_TEST_ITEM_VALUES},
            'test_item_name': {'type': 'string', 'nullable': True},
            'test_report_id': {'type': 'string', 'nullable': True},
            'source_profile_key': {'type': 'string', 'nullable': True},
            'source_revision_key': {'type': 'string', 'nullable': True},
            'source_pulled_at': {'type': 'string', 'nullable': True},
        },
        # status 는 요청 필드가 아니다 — 서버가 draft 로 정한다.
        'additionalProperties': False,
    },
    'TestEquipmentListItemInput': {
        'type': 'object',
        'required': ['item_type'],
        'properties': {
            'item_type': {'type': 'string', 'enum': _EQUIPMENT_ITEM_TYPE_VALUES},
            **{
                field: {'type': 'string', 'nullable': True}
                for field in _EQUIPMENT_ITEM_TEXT_FIELDS
            },
        },
        # sort_order 가 **없다** — 배열 위치가 곧 순서이고 서버가 부여한다.
        # 클라이언트가 보내게 두면 순서와 배열이 어긋난 요청을 어느 쪽이 옳은지
        # 판정해야 한다.
        'additionalProperties': False,
    },
    'ReplaceTestEquipmentListItemsRequest': {
        'type': 'object',
        'required': ['items'],
        'properties': {
            'items': {
                'type': 'array',
                'items': {'$ref': '#/schemas/TestEquipmentListItemInput'},
            },
        },
        'additionalProperties': False,
    },
    'ReplaceTestEquipmentListItemsResult': {
        'type': 'object',
        'required': ['list_id', 'item_count'],
        'properties': {
            'list_id': {'type': 'string'},
            'item_count': {'type': 'integer'},
        },
        'additionalProperties': False,
    },
    'AttachTestEquipmentListRequest': {
        'type': 'object',
        'required': ['test_report_id'],
        'properties': {
            'test_report_id': {'type': 'string', 'minLength': 1},
        },
        'additionalProperties': False,
    },
    'AttachTestEquipmentListResult': {
        'type': 'object',
        'required': ['list_id', 'test_report_id'],
        'properties': {
            'list_id': {'type': 'string'},
            'test_report_id': {'type': 'string'},
        },
        'additionalProperties': False,
    },
    'ConfirmTestEquipmentListResult': {
        'type': 'object',
        'required': ['list_id', 'status'],
        'properties': {
            'list_id': {'type': 'string'},
            'status': {'type': 'string', 'enum': _EQUIPMENT_LIST_STATUS_VALUES},
            'confirmed_at': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
    # Report header citation — assembled from project + samples + intakes
    # (report_citation domain SSOT). sample_number is the join key to the local
    # measurement DB ({model}__{sample}.fcc.db).
    'ReportCitationEnvelope': {
        'type': 'object',
        'required': ['project_id', 'samples'],
        'properties': {
            'project_id': {'type': 'string'},
            'report_number': {'type': 'string', 'nullable': True},
            'fcc_id': {'type': 'string', 'nullable': True},
            'management_number': {'type': 'string', 'nullable': True},
            'applicant_name': {'type': 'string', 'nullable': True},
            'applicant_address': {'type': 'string', 'nullable': True},
            'eut_description': {'type': 'string', 'nullable': True},
            'test_standard': {'type': 'string', 'nullable': True},
            'samples': {
                'type': 'array',
                'items': {'$ref': '#/schemas/ReportSampleCitation'},
            },
        },
        'additionalProperties': False,
    },
    'ReportSampleCitation': {
        'type': 'object',
        'properties': {
            'sample_number': {'type': 'string', 'nullable': True},
            'serial_number': {'type': 'string', 'nullable': True},
            'latest_firmware': {
                'nullable': True,
                'allOf': [{'$ref': '#/schemas/FirmwareCitationEnvelope'}],
            },
        },
        'additionalProperties': False,
    },
    'FirmwareCitationEnvelope': {
        'type': 'object',
        'properties': {
            'bl': {'type': 'string', 'nullable': True},
            'ap': {'type': 'string', 'nullable': True},
            'cp': {'type': 'string', 'nullable': True},
            'csc': {'type': 'string', 'nullable': True},
            'rf_cal': {'type': 'string', 'nullable': True},
            'hw_rev': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
}


OPERATIONS: dict[str, dict] = {
    'list_project_report_sessions': _operation(
        request=None,
        response='ProjectReportSessionList',
        permission=PERMISSIONS['list_project_report_sessions'],
    ),
    # Phase G (2026-06-23) — test_reports 성적서 surface. list/citation read +
    # create. report_number is derived, so the create body has none; a duplicate
    # (project_id, edition) → 409, an unknown project → 404.
    'list_reports': _operation(
        request=None,
        response='ReportList',
        permission=PERMISSIONS['list_reports'],
        error_responses={'404': _REPORT_PROJECT_NOT_FOUND_404},
    ),
    'create_report': _operation(
        request='CreateReportRequest',
        response='ReportEnvelope',
        permission=PERMISSIONS['create_report'],
        error_responses={
            '404': _REPORT_PROJECT_NOT_FOUND_404,
            '409': _REPORT_EDITION_CONFLICT_409,
        },
    ),
    'get_report_citation': _operation(
        request=None,
        response='ReportCitationEnvelope',
        permission=PERMISSIONS['get_report_citation'],
        error_responses={'404': _REPORT_PROJECT_NOT_FOUND_404},
    ),
    # 성적서 §6 장비목록 (2026-08-07). 모든 operation 이 **선언된** response
    # 스키마를 갖는다 — 미선언은 인라인 {'type': 'object'} 폴백으로 떨어져
    # bare-fallback ratchet 을 키운다.
    'list_test_equipment_lists': _operation(
        request=None,
        response='TestEquipmentListCollection',
        permission=PERMISSIONS['list_test_equipment_lists'],
        error_responses={'404': _EQUIPMENT_LIST_PROJECT_NOT_FOUND_404},
    ),
    'create_test_equipment_list': _operation(
        request='CreateTestEquipmentListRequest',
        response='TestEquipmentListSummary',
        permission=PERMISSIONS['create_test_equipment_list'],
        error_responses={
            '404': _EQUIPMENT_LIST_PROJECT_NOT_FOUND_404,
            '409': _EQUIPMENT_LIST_DUPLICATE_409,
        },
    ),
    'get_test_equipment_list': _operation(
        request=None,
        response='TestEquipmentListEnvelope',
        permission=PERMISSIONS['get_test_equipment_list'],
        error_responses={'404': _EQUIPMENT_LIST_NOT_FOUND_404},
    ),
    'replace_test_equipment_list_items': _operation(
        request='ReplaceTestEquipmentListItemsRequest',
        response='ReplaceTestEquipmentListItemsResult',
        permission=PERMISSIONS['replace_test_equipment_list_items'],
        error_responses={
            '404': _EQUIPMENT_LIST_NOT_FOUND_404,
            '409': _EQUIPMENT_LIST_FROZEN_409,
        },
    ),
    'attach_test_equipment_list': _operation(
        request='AttachTestEquipmentListRequest',
        response='AttachTestEquipmentListResult',
        permission=PERMISSIONS['attach_test_equipment_list'],
        error_responses={
            '404': _EQUIPMENT_LIST_NOT_FOUND_404,
            '409': _EQUIPMENT_LIST_NOT_ATTACHABLE_409,
        },
    ),
    'confirm_test_equipment_list': _operation(
        request=None,
        response='ConfirmTestEquipmentListResult',
        permission=PERMISSIONS['confirm_test_equipment_list'],
        error_responses={
            '404': _EQUIPMENT_LIST_NOT_FOUND_404,
            '409': _EQUIPMENT_LIST_NOT_CONFIRMABLE_409,
        },
    ),
}
