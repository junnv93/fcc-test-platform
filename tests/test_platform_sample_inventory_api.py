from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from fcc_test_contracts.common.access_policy import ApiAccessPolicy, ApiPrincipal
from application.central_contract.api_contracts import (
    PLATFORM_API_OPERATIONS,
    PLATFORM_API_OPERATION_QUERY_OVERRIDES,
    PLATFORM_API_PERMISSIONS,
    PLATFORM_API_ROUTES,
)
from fcc_test_platform.application.api_schema import build_platform_openapi_schema
from fcc_test_platform.application.sample_inventory_export_service import SampleInventoryExportResult
from fcc_test_platform.api.platform_routes import (
    PlatformApiAdapter,
    PlatformAuthorizationError,
    api_error_status,
    create_platform_app,
)


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_OPERATIONS = (
    'list_sample_inventory', 'create_sample', 'get_sample', 'patch_sample',
    'change_sample_status', 'delete_sample', 'hard_delete_sample',
    'list_sample_history', 'export_sample_inventory',
)


class _FakeInventoryService:
    def __init__(self):
        self.calls = []

    def list_samples(self, **kwargs):
        self.calls.append(('list', kwargs))
        return {'items': [], 'next_cursor': None}

    def create_sample(self, project_id, payload, *, actor_subject):
        self.calls.append(('create', project_id, payload, actor_subject))
        return {'sample_id': 'sample-1', 'project_id': project_id, 'row_version': 1}

    def get_sample(self, project_id, sample_id, *, as_of=None):
        self.calls.append(('get', project_id, sample_id, as_of))
        return {'sample_id': sample_id, 'project_id': project_id}

    def patch_sample(self, project_id, sample_id, payload, *, expected_version, actor_subject):
        self.calls.append(('patch', project_id, sample_id, payload, expected_version, actor_subject))
        return {'sample_id': sample_id, 'row_version': expected_version + 1}

    def change_status(self, project_id, sample_id, status, *, expected_version, actor_subject):
        self.calls.append(('status', project_id, sample_id, status, expected_version, actor_subject))
        return {'sample_id': sample_id, 'status': status}

    def soft_delete(self, project_id, sample_id, *, expected_version, actor_subject):
        self.calls.append(('delete', project_id, sample_id, expected_version, actor_subject))
        return {'sample_id': sample_id, 'status': 'deleted'}

    def hard_delete(self, sample_id, *, actor_subject):
        self.calls.append(('hard-delete', sample_id, actor_subject))
        return {'sample_id': sample_id, 'hard_deleted': True}

    def list_history(self, project_id, sample_id, *, after=None, limit=100):
        self.calls.append(('history', project_id, sample_id, after, limit))
        return {'items': [], 'next_cursor': None}


class _FakeExportService:
    def __init__(self):
        self.calls = []

    def export(self, project_id, template, **kwargs):
        self.calls.append((project_id, template, kwargs))
        return SampleInventoryExportResult(
            content=b'xlsx', filename='model.xlsx', template=template, sample_ids=(),
        )


def _adapter(principal=None, inventory=None, export=None):
    return PlatformApiAdapter(
        read_service=object(),
        access_policy=ApiAccessPolicy(PLATFORM_API_OPERATIONS),
        principal=principal,
        sample_inventory_service=inventory or _FakeInventoryService(),
        sample_export_service=export or _FakeExportService(),
    )


def test_sample_operation_contract_has_routes_permissions_and_no_import_paths():
    schema = build_platform_openapi_schema(None)
    paths = schema['paths']
    expected = {
        'list_sample_inventory': ('GET', '/platform/sample-inventory'),
        'create_sample': ('POST', '/platform/projects/{project_id}/samples'),
        'get_sample': ('GET', '/platform/projects/{project_id}/samples/{sample_id}'),
        'patch_sample': ('PATCH', '/platform/projects/{project_id}/samples/{sample_id}'),
        'change_sample_status': ('POST', '/platform/projects/{project_id}/samples/{sample_id}/status'),
        'delete_sample': ('DELETE', '/platform/projects/{project_id}/samples/{sample_id}'),
        'hard_delete_sample': ('DELETE', '/platform/system/sample-inventory/{sample_id}'),
        'list_sample_history': ('GET', '/platform/projects/{project_id}/samples/{sample_id}/history'),
        'export_sample_inventory': ('GET', '/platform/projects/{project_id}/sample-inventory/exports/{template}'),
    }
    for operation, route in expected.items():
        assert PLATFORM_API_ROUTES[operation] == route
        method, path = route
        assert paths[path][method.lower()]['operationId'] == operation
        assert paths[path][method.lower()]['x-fcc-permission'] == PLATFORM_API_PERMISSIONS[operation]
        assert PLATFORM_API_OPERATIONS[operation]['permission'] == PLATFORM_API_PERMISSIONS[operation]
    assert not any('import' in path for path in paths)
    assert PLATFORM_API_OPERATION_QUERY_OVERRIDES['list_sample_inventory']['status']['enum'] == [
        'active', 'deleted', 'all',
    ]
    export = paths[expected['export_sample_inventory'][1]]['get']
    assert 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' in export['responses']['200']['content']

    artifact = json.loads((ROOT / 'docs/api/platform-api.openapi.json').read_text())
    assert artifact == schema


def test_adapter_delegates_all_inventory_operations_and_uses_verified_actor():
    inventory = _FakeInventoryService()
    export = _FakeExportService()
    principal = ApiPrincipal.from_permissions(
        'user-1', ['platform:read', 'platform:sample-write', 'platform:sample-hard-delete'],
    )
    adapter = _adapter(principal, inventory, export)

    adapter.list_sample_inventory(project_id='p', team='RF', status='active', as_of='2026-01-01T00:00:00Z')
    adapter.create_sample('p', {'sample_number': 'S'})
    adapter.get_sample('p', 's', as_of='2026-01-01T00:00:00Z')
    adapter.patch_sample('p', 's', {'expected_version': 2, 'note': 'n'})
    adapter.change_sample_status('p', 's', {'status': 'deleted', 'expected_version': 3})
    adapter.delete_sample('p', 's', {'expected_version': 4})
    adapter.hard_delete_sample('s')
    adapter.list_sample_history('p', 's', after='cursor', limit=5)
    adapter.export_sample_inventory('p', 'pm-status', team='PM', status='active')

    assert inventory.calls[0] == ('list', {
        'project_id': 'p', 'team': 'RF', 'status': 'active',
        'as_of': '2026-01-01T00:00:00Z', 'after': None, 'limit': 100,
        'include_deleted': False,
    })
    assert inventory.calls[1][-1] == 'user-1'
    assert inventory.calls[3][-2:] == (2, 'user-1')
    assert inventory.calls[5][-1] == 'user-1'
    assert export.calls[0][2] == {
        'team': 'PM', 'status': 'active', 'as_of': None, 'include_deleted': False,
    }


def test_sample_mutations_require_whole_record_permission_and_hard_delete_is_distinct():
    inventory = _FakeInventoryService()
    viewer = _adapter(ApiPrincipal.from_permissions('viewer', ['platform:read']), inventory)
    with pytest.raises(PlatformAuthorizationError):
        viewer.create_sample('p', {'sample_number': 'S'})
    with pytest.raises(PlatformAuthorizationError):
        viewer.hard_delete_sample('s')

    writer = _adapter(
        ApiPrincipal.from_permissions('writer', ['platform:sample-write']), inventory,
    )
    writer.create_sample('p', {'sample_number': 'S'})
    with pytest.raises(PlatformAuthorizationError):
        writer.hard_delete_sample('s')

    assert api_error_status(PlatformAuthorizationError('denied')) == 403


def test_http_hard_delete_is_system_admin_only_and_denials_are_structured(caplog):
    """Exercise the real FastAPI route, not only the adapter method.

    Project-scoped roles may edit the inventory but cannot cross the global
    physical-delete boundary. Every denial must be observable with the actor,
    operation, and request correlation while keeping the sample identifier and
    bearer material out of the log message.
    """
    from fastapi.testclient import TestClient

    sample_id = '44444444-4444-4444-8444-444444444444'
    correlation = 'corr-sample-hard-delete'
    denied_roles = (
        ('viewer', ['platform:read']),
        ('project-pm', ['platform:read', 'platform:sample-write']),
        ('project-engineer', ['platform:read', 'platform:sample-write']),
        ('project-admin', ['platform:read', 'platform:sample-write', 'platform:admin']),
    )

    for role, permissions in denied_roles:
        caplog.clear()
        inventory = _FakeInventoryService()
        adapter = _adapter(
            ApiPrincipal.from_permissions(f'actor-{role}', permissions), inventory,
        )
        with TestClient(
            create_platform_app(adapter, rate_limit_policy=None),
            raise_server_exceptions=False,
        ) as client:
            with caplog.at_level(logging.WARNING):
                response = client.delete(
                    f'/platform/system/sample-inventory/{sample_id}',
                    headers={
                        'X-Request-Id': correlation,
                        'Authorization': 'Bearer synthetic-secret-token',
                    },
                )

        assert response.status_code == 403, (role, response.text)
        assert inventory.calls == []
        event = next(
            record for record in caplog.records
            if (
                record.getMessage() == 'security_event sample hard-delete denied'
                and getattr(record, 'actor_subject', None) == f'actor-{role}'
                and getattr(record, 'operation', None) == 'hard_delete_sample'
                and getattr(record, 'correlation_id', None) == correlation
            )
        )
        assert event.actor_subject == f'actor-{role}'
        assert event.operation == 'hard_delete_sample'
        assert event.correlation_id == correlation
        rendered = event.getMessage()
        assert sample_id not in rendered
        assert 'synthetic-secret-token' not in rendered
        event_fields = vars(event)
        standard_log_fields = {
            'name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 'filename',
            'module', 'exc_info', 'exc_text', 'stack_info', 'lineno', 'funcName',
            'created', 'msecs', 'relativeCreated', 'thread', 'threadName',
            'processName', 'process',
            # Runtime/pytest convenience fields are not security-event payload.
            # Actor, operation, and correlation remain asserted separately.
            'asctime', 'message', 'taskName',
        }
        assert set(event_fields) - standard_log_fields - {
            'actor_subject', 'operation', 'correlation_id',
        } == set()
        assert sample_id not in repr(event_fields)
        assert 'synthetic-secret-token' not in repr(event_fields)

    inventory = _FakeInventoryService()
    adapter = _adapter(
        ApiPrincipal.from_permissions(
            'actor-system-admin', ['platform:sample-hard-delete'],
        ),
        inventory,
    )
    with TestClient(
        create_platform_app(adapter, rate_limit_policy=None),
        raise_server_exceptions=False,
    ) as client:
        response = client.delete(f'/platform/system/sample-inventory/{sample_id}')

    assert response.status_code == 200
    assert response.json() == {'sample_id': sample_id, 'hard_deleted': True}
    assert inventory.calls == [('hard-delete', sample_id, 'actor-system-admin')]
