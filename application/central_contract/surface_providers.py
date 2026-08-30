"""중앙 플랫폼 계약 — provider 레지스트리 + UI descriptor 프록시.

``api_contracts`` facade 가 이 모듈의 표를 병합해 ``PLATFORM_API_*`` 로 재노출한다.
모듈 경계는 **표 종류가 아니라 operation 표면**이다 — git 이력 실측에서 계약 변경
커밋의 92%(52 중 48)가 정확히 한 표면만 만졌고, 표 종류로 자르면 엔드포인트 하나
추가가 항상 다섯 표를 동시에 만진다(네 표가 같은 operationId 키로 병렬 배열돼 있다).
"""
from __future__ import annotations

from application.central_contract.api_operation_factory import _operation
from fcc_test_contracts.common.provider_ui_descriptor_schema import PROVIDER_UI_DESCRIPTOR_SCHEMAS

#: 이 모듈이 소유하는 route path prefix. 분할은 **선언이 아니라 판정 대상**이다 —
#: ``tests/test_central_contract_decomposition_axis.py`` 가 prefix 집합이 쌍마다
#: 서로소이고 ``PLATFORM_API_ROUTES`` 의 모든 경로를 덮는지, 그리고 각 operation 이
#: 자기 경로의 **최장 일치** prefix 를 소유한 모듈에 물리적으로 선언돼 있는지 파생
#: 판정한다. 새 엔드포인트가 어느 모듈로 가야 하는지 사람이 기억하지 않는다.
SURFACE_PREFIXES: tuple[str, ...] = (
    '/platform/providers',
)


ROUTES: dict[str, tuple[str, str]] = {
    # WEB-PROVIDER-UI-0 — provider list (collection GET) + UI descriptor proxy
    # (detail GET). The list shares the /platform/providers parent path with the
    # {provider_id}/ui-descriptor detail (one path item each, no conflict —
    # mirrors /platform/projects list+detail).
    'list_providers': ('GET', '/platform/providers'),
    'get_provider_ui_descriptor': (
        'GET', '/platform/providers/{provider_id}/ui-descriptor',
    ),
}


PERMISSIONS: dict[str, str] = {
    # WEB-PROVIDER-UI-0 — list providers + proxy a provider's UI descriptor
    # (read). The platform serves both from a registry; it never imports provider
    # internals. Both share platform:read → no new grantable token (bijection
    # unchanged).
    'list_providers': 'platform:read',
    'get_provider_ui_descriptor': 'platform:read',
}


SCHEMAS: dict[str, dict] = {
    # WEB-PROVIDER-UI-0 — provider list envelope. A selectable summary (id +
    # label + descriptor version), NOT the full UI descriptor (that is the detail
    # ProviderUiDescriptor schema). Lets the frontend build a backend-driven
    # picker instead of a hardcoded provider list.
    'ProviderSummaryList': {
        'type': 'array',
        'items': {'$ref': '#/schemas/ProviderSummary'},
    },
    'ProviderSummary': {
        'type': 'object',
        'required': ['provider_id', 'display_name', 'ui_version'],
        'properties': {
            'provider_id': {'type': 'string'},
            'display_name': {'type': 'string'},
            'ui_version': {'type': 'integer'},
        },
        'additionalProperties': False,
    },
    # WEB-PROVIDER-UI-0 — shared provider UI descriptor component schemas
    # (neutral SSOT in application.common; headless merges the same shapes).
    **PROVIDER_UI_DESCRIPTOR_SCHEMAS,
}


OPERATIONS: dict[str, dict] = {
    'list_providers': _operation(
        request=None,
        response='ProviderSummaryList',
        permission=PERMISSIONS['list_providers'],
    ),
    'get_provider_ui_descriptor': _operation(
        request=None,
        response='ProviderUiDescriptor',
        permission=PERMISSIONS['get_provider_ui_descriptor'],
    ),
}
