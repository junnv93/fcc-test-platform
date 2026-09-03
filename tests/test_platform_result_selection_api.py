from __future__ import annotations

from fcc_test_contracts.common.access_policy import ApiAccessPolicy, ApiPrincipal
from fcc_test_kernel.application.central_contract.api_contracts import (
    PLATFORM_API_OPERATIONS,
    PLATFORM_API_PERMISSIONS,
    PLATFORM_API_ROUTES,
    PLATFORM_API_SCHEMAS,
)
from fcc_test_platform.application.api_schema import build_platform_openapi_schema
from fcc_test_platform.application.central_result_selection_adapter import (
    SELECTED_SOURCE_COLUMNS,
    SELECTED_SOURCE_QUERY_SQL,
    PostgresCentralResultSelectionAdapter,
)
from fcc_test_platform.domain.ports.output.central_result_selection_port import (
    SelectionBackendError,
    SelectionRevisionConflictError,
)
from fcc_test_platform.api.platform_routes import (
    PlatformApiAdapter,
    create_platform_app,
)


PROJECT_ID = 'project-1'
PROVIDER_ID = 'provider-natural'
CONDITION_HASH = 'condition-1'


class _Cursor:
    def __init__(self, row: tuple):
        self.row = row
        self.calls: list[tuple[str, tuple]] = []
        self._rows: list[tuple] = []
        self.description = None

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        if 'FROM "providers"' in sql:
            self._rows = [('provider-uuid',)]
        else:
            self._rows = [self.row]

    def fetchall(self):
        return self._rows

    def close(self):
        return None


class _Connection:
    def __init__(self, row: tuple):
        self.cursor_instance = _Cursor(row)
        self.committed = False
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def close(self):
        self.closed = True


class _FakeSelectionService:
    def __init__(self):
        self.calls: list[tuple] = []
        self.raise_stale = False

    def list_effective_results(self, project_id, provider_id, *, limit, cursor=None):
        self.calls.append(('effective', project_id, provider_id, limit, cursor))
        return {'items': [{'condition_hash': CONDITION_HASH}], 'next_cursor': 'next-page'}

    def list_attempts(self, project_id, provider_id, condition_hash, *, limit, cursor=None):
        self.calls.append(('attempts', project_id, provider_id, condition_hash, limit, cursor))
        return {'items': [{'attempt_id': 'attempt-1'}], 'next_cursor': None}

    def select(
        self, project_id, provider_id, condition_hash, *, attempt_id,
        expected_revision, actor_subject, reason=None,
    ):
        self.calls.append((
            'select', project_id, provider_id, condition_hash, attempt_id,
            expected_revision, actor_subject, reason,
        ))
        if self.raise_stale:
            raise SelectionRevisionConflictError('stale selection revision')
        return {'action': 'selected', 'revision': expected_revision + 1}

    def clear(
        self, project_id, provider_id, condition_hash, *, expected_revision,
        actor_subject, reason=None,
    ):
        self.calls.append((
            'clear', project_id, provider_id, condition_hash,
            expected_revision, actor_subject, reason,
        ))
        return {'action': 'cleared', 'revision': expected_revision + 1}


class _FakeReferenceService:
    def __init__(self):
        self.calls: list[tuple] = []

    def list_references(self, project_id, *, producer_provider_id=None, state=None, limit, cursor=None):
        self.calls.append(('list', project_id, producer_provider_id, state, limit, cursor))
        return {'items': [{'revision_id': 'revision-1'}], 'next_cursor': None}

    def publish(self, *, project_id, provider_id, condition_hash, reason, actor_subject):
        self.calls.append(('publish', project_id, provider_id, condition_hash, reason, actor_subject))
        return {'revision_id': 'revision-1', 'state': 'published'}

    def retire(self, revision_id, *, actor_subject, reason):
        self.calls.append(('retire', revision_id, actor_subject, reason))
        return {'revision_id': revision_id, 'state': 'retired'}


def _adapter(selection=None, references=None):
    principal = ApiPrincipal.from_permissions(
        'operator-1',
        ['platform:read', 'platform:claim', 'platform:reference-write'],
    )
    return PlatformApiAdapter(
        read_service=object(),
        access_policy=ApiAccessPolicy(PLATFORM_API_OPERATIONS),
        principal=principal,
        result_selection_service=selection or _FakeSelectionService(),
        project_result_reference_service=references or _FakeReferenceService(),
    )


def test_selected_source_is_full_authoritative_event_attempt_session_provider_join():
    source = {
        'selection_event_id': 'event-1',
        'selection_action': 'selected',
        'selection_revision': 4,
        'attempt_id': 'attempt-1',
        'project_id': PROJECT_ID,
        'provider_id': PROVIDER_ID,
        'condition_hash': CONDITION_HASH,
        'session_id': 'session-1',
        'provider_session_id': 'provider-session-1',
        'sample_id': 'sample-1',
        'chamber_id': 'chamber-1',
        'operator': 'operator-1',
        'measured_at': '2026-08-26T00:00:00Z',
        'created_at': '2026-08-26T00:00:01Z',
        'verdict': 'pass',
        'status': 'completed',
        'attempt_number': 2,
        'result_json': {'reading': 42},
        'provenance_json': {'source': 'provider'},
        'test_name': 'test-1',
        'technology': 'conducted',
        'margin': '3.0',
        'run_id': 'run-1',
        'idempotency_key': 'idem-1',
        'recorded_by': 'operator-1',
    }
    connection = _Connection(tuple(source[column] for column in SELECTED_SOURCE_COLUMNS))
    result = PostgresCentralResultSelectionAdapter(lambda: connection).selected_source(
        PROJECT_ID, PROVIDER_ID, CONDITION_HASH,
    )

    assert result == source
    assert set(result) == set(SELECTED_SOURCE_COLUMNS)
    assert connection.cursor_instance.calls[0][1] == (PROVIDER_ID,)
    assert connection.cursor_instance.calls[1][1] == (
        PROJECT_ID, 'provider-uuid', CONDITION_HASH,
    )
    assert 'project_result_selection_events' in SELECTED_SOURCE_QUERY_SQL
    assert 'measurement_attempts' in SELECTED_SOURCE_QUERY_SQL
    assert 'test_sessions' in SELECTED_SOURCE_QUERY_SQL
    assert 'providers' in SELECTED_SOURCE_QUERY_SQL
    assert 'NOT EXISTS' in SELECTED_SOURCE_QUERY_SQL
    assert "e.\"action\" = 'selected'" in SELECTED_SOURCE_QUERY_SQL
    assert "a.\"status\" = 'completed'" in SELECTED_SOURCE_QUERY_SQL
    assert 'OFFSET' not in SELECTED_SOURCE_QUERY_SQL.upper()


def test_selected_source_rejects_legacy_four_column_event_only_rows():
    connection = _Connection(('event-1', 'selected', 'attempt-1', 4))

    import pytest

    with pytest.raises(SelectionBackendError, match='full event-attempt-session'):
        PostgresCentralResultSelectionAdapter(lambda: connection).selected_source(
            PROJECT_ID, PROVIDER_ID, CONDITION_HASH,
        )


def test_platform_result_routes_page_cas_and_keep_publish_input_provider_only():
    selection = _FakeSelectionService()
    references = _FakeReferenceService()
    adapter = _adapter(selection, references)

    expected_routes = {
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
    }
    schema = build_platform_openapi_schema(None)
    for operation, route in expected_routes.items():
        assert PLATFORM_API_ROUTES[operation] == route
        method, path = route
        assert schema['paths'][path][method.lower()]['operationId'] == operation
        assert schema['paths'][path][method.lower()]['x-fcc-permission'] == (
            PLATFORM_API_PERMISSIONS[operation]
        )
        assert PLATFORM_API_OPERATIONS[operation]['permission'] == (
            PLATFORM_API_PERMISSIONS[operation]
        )

    reference_request = PLATFORM_API_SCHEMAS['CreateProjectResultReferenceRequest']
    assert reference_request['additionalProperties'] is False
    assert set(reference_request['properties']) == {
        'provider_id', 'condition_hash', 'reason',
    }
    assert 'payload' not in reference_request['properties']
    assert 'content_sha256' not in reference_request['properties']
    assert 'source_attempt_id' not in reference_request['properties']

    from fastapi.testclient import TestClient

    with TestClient(create_platform_app(adapter, rate_limit_policy=None)) as client:
        response = client.get(
            f'/platform/projects/{PROJECT_ID}/providers/{PROVIDER_ID}/result-selections',
            params={'limit': 2, 'cursor': 'cursor-1'},
        )
        assert response.status_code == 200
        assert response.json() == [{'condition_hash': CONDITION_HASH}]
        assert response.headers['X-Next-Cursor'] == 'next-page'

        response = client.post(
            f'/platform/projects/{PROJECT_ID}/providers/{PROVIDER_ID}/conditions/{CONDITION_HASH}/selection-events',
            json={'attempt_id': 'attempt-1', 'expected_revision': 3, 'reason': 'review'},
        )
        assert response.status_code == 200

        response = client.request(
            'DELETE',
            f'/platform/projects/{PROJECT_ID}/providers/{PROVIDER_ID}/conditions/{CONDITION_HASH}/selection-events',
            json={'expected_revision': 4, 'reason': 'clear'},
        )
        assert response.status_code == 200

        response = client.post(
            f'/platform/projects/{PROJECT_ID}/project-result-references',
            json={
                'provider_id': PROVIDER_ID,
                'condition_hash': CONDITION_HASH,
                'reason': 'publish',
            },
        )
        assert response.status_code == 200

        response = client.post(
            f'/platform/projects/{PROJECT_ID}/project-result-references',
            json={
                'provider_id': PROVIDER_ID,
                'condition_hash': CONDITION_HASH,
                'reason': 'publish',
                # These are deliberately hostile client-supplied provenance
                # fields. The driving adapter must never forward them.
                'payload': {'forged': True},
                'content_sha256': 'forged',
                'source_attempt_id': 'forged',
                'session_id': 'forged',
            },
        )
        assert response.status_code == 422
        assert response.headers['content-type'].startswith('application/problem+json')
        assert response.json()['status'] == 422
        assert response.json()['code'] == 'REFERENCE_REQUEST_UNPROCESSABLE'

        response = client.get(
            f'/platform/projects/{PROJECT_ID}/project-result-references',
            params={'provider_id': PROVIDER_ID, 'state': 'published'},
        )
        assert response.status_code == 200

        response = client.post(
            f'/platform/projects/{PROJECT_ID}/project-result-references/revision-1/retire',
            json={'reason': 'superseded'},
        )
        assert response.status_code == 200

    assert selection.calls[0] == (
        'effective', PROJECT_ID, PROVIDER_ID, 2, 'cursor-1',
    )
    assert selection.calls[1] == (
        'select', PROJECT_ID, PROVIDER_ID, CONDITION_HASH, 'attempt-1',
        3, 'operator-1', 'review',
    )
    assert selection.calls[2] == (
        'clear', PROJECT_ID, PROVIDER_ID, CONDITION_HASH, 4, 'operator-1', 'clear',
    )
    assert references.calls == [
        ('publish', PROJECT_ID, PROVIDER_ID, CONDITION_HASH, 'publish', 'operator-1'),
        ('list', PROJECT_ID, PROVIDER_ID, 'published', 100, None),
        ('retire', 'revision-1', 'operator-1', 'superseded'),
    ]


def test_publication_request_rejects_server_owned_provenance_before_service_call():
    references = _FakeReferenceService()
    adapter = _adapter(_FakeSelectionService(), references)

    from fastapi.testclient import TestClient

    with TestClient(create_platform_app(adapter, rate_limit_policy=None)) as client:
        response = client.post(
            f'/platform/projects/{PROJECT_ID}/project-result-references',
            json={
                'provider_id': PROVIDER_ID,
                'condition_hash': CONDITION_HASH,
                'content_sha256': 'forged',
                'source_session_id': 'forged',
            },
        )

    assert response.status_code == 422
    assert response.json()['code'] == 'REFERENCE_REQUEST_UNPROCESSABLE'
    assert references.calls == []


def test_platform_stale_selection_is_rfc9457_conflict_without_new_service_call():
    selection = _FakeSelectionService()
    selection.raise_stale = True
    adapter = _adapter(selection, _FakeReferenceService())

    from fastapi.testclient import TestClient

    with TestClient(
        create_platform_app(adapter, rate_limit_policy=None),
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            f'/platform/projects/{PROJECT_ID}/providers/{PROVIDER_ID}/conditions/{CONDITION_HASH}/selection-events',
            json={'attempt_id': 'attempt-1', 'expected_revision': 3},
        )

    assert response.status_code == 409
    assert response.headers['content-type'].startswith('application/problem+json')
    assert response.json()['status'] == 409
    assert response.json()['code'] == 'CONFLICT'
