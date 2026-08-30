from __future__ import annotations

import unittest

from fcc_test_platform.application.central_project_reference_service import (
    CentralProjectReferenceService,
)
from domain.models.project_result_reference import canonical_payload_hash
from domain.ports.output.central_project_reference_port import (
    ReferenceHashMismatchError,
    ReferenceNotFoundError,
)


class FakeReferencePort:
    def __init__(self) -> None:
        self.published: list[dict] = []
        self.retired: list[dict] = []

    def list_references(
        self, project_id, *, producer_provider_id=None, state=None, limit, cursor=None
    ):
        return {'items': list(self.published), 'next_cursor': None}

    def publish_reference(self, record):
        self.published.append(dict(record))
        return dict(
            record,
            revision_number=len(self.published),
            revision_id=record['id'],
            payload=record['payload_json'],
        )

    def retire_reference(self, **record):
        self.retired.append(record)
        base = self.published[0] if self.published else {
            'revision_id': record['revision_id'],
            'project_id': 'project-a',
        }
        return dict(
            base,
            state='retired',
            retired_by=record['actor_subject'],
            retirement_reason=record['reason'],
        )

    def resolve_reference(self, **record):
        return dict(self.published[0])


class FakeSelectionPort:
    def __init__(self, source=None) -> None:
        self.source = source

    def selected_source(self, project_id, provider_id, condition_hash):
        return self.source


class FakeProvider:
    provider_id = 'provider-a'
    reference_type = 'opaque.result'
    schema_version = 'opaque.v1'

    def __init__(self, *, digest_override=None) -> None:
        self.digest_override = digest_override

    def export(self, attempt):
        payload = {'dccf': '1.25'}
        return {
            'provider_id': self.provider_id,
            'reference_type': self.reference_type,
            'schema_version': self.schema_version,
            'attempt_id': attempt['attempt_id'],
            'payload': payload,
            'content_sha256': self.digest_override or canonical_payload_hash(payload),
        }

    def accepts(self, reference_type, schema_version):
        return (reference_type, schema_version) == (
            self.reference_type,
            self.schema_version,
        )


def selected_source() -> dict:
    return {
        'selection_event_id': 'event-1',
        'selection_action': 'selected',
        'selection_revision': 1,
        'attempt_id': 'attempt-1',
        'project_id': 'project-a',
        'provider_id': 'provider-a',
        'condition_hash': 'condition-a',
        'session_id': 'session-1',
        'provider_session_id': 'provider-session-1',
        'sample_id': 'sample-1',
        'chamber_id': 'chamber-a',
        'operator': 'operator-a',
        'measured_at': '2026-08-25T00:00:00Z',
        'created_at': '2026-08-25T00:00:01Z',
        'verdict': 'Pass',
        'status': 'completed',
        'attempt_number': 1,
        'result_json': {'dccf': '1.25'},
        'provenance_json': {'source': 'measurement'},
        'test_name': 'DUTY',
        'technology': 'WLAN',
        'margin': '1.0',
        'run_id': 'run-1',
        'idempotency_key': 'idem-1',
        'recorded_by': 'operator-a',
    }


class CentralProjectReferenceServiceTests(unittest.TestCase):
    def _service(self, port=None, provider=None, selection=None):
        return CentralProjectReferenceService(
            port or FakeReferencePort(),
            selection_port=selection or FakeSelectionPort(selected_source()),
            provider_resolver={'provider-a': provider or FakeProvider()},
            revision_id_factory=lambda: 'revision-1',
        )

    def test_publish_uses_server_selected_source_and_provider_envelope(self) -> None:
        port = FakeReferencePort()
        service = self._service(port=port)
        result = service.publish(
            project_id='project-a',
            provider_id='provider-a',
            condition_hash='condition-a',
            reason='examiner-approved',
            actor_subject='operator-a',
        )
        record = port.published[0]
        self.assertEqual(result['source_attempt_id'], 'attempt-1')
        self.assertEqual(record['source_selection_event_id'], 'event-1')
        self.assertEqual(record['source_session_id'], 'session-1')
        self.assertEqual(record['payload_json'], {'dccf': '1.25'})
        self.assertEqual(record['content_sha256'], canonical_payload_hash(record['payload_json']))
        self.assertEqual(record['publication_reason'], 'examiner-approved')

    def test_missing_selected_source_is_not_found(self) -> None:
        service = self._service(selection=FakeSelectionPort(None))
        with self.assertRaises(ReferenceNotFoundError):
            service.publish(
                project_id='project-a',
                provider_id='provider-a',
                condition_hash='condition-a',
                actor_subject='operator-a',
            )

    def test_provider_hash_mismatch_is_rejected_before_port_write(self) -> None:
        port = FakeReferencePort()
        service = self._service(
            port=port, provider=FakeProvider(digest_override='0' * 64)
        )
        with self.assertRaises(ReferenceHashMismatchError):
            service.publish(
                project_id='project-a',
                provider_id='provider-a',
                condition_hash='condition-a',
                actor_subject='operator-a',
            )
        self.assertEqual(port.published, [])

    def test_retire_requires_a_reason_and_records_actor(self) -> None:
        port = FakeReferencePort()
        service = CentralProjectReferenceService(port)
        with self.assertRaises(ValueError):
            service.retire('revision-1', actor_subject='operator-a', reason='')
        service.retire('revision-1', actor_subject='operator-a', reason='superseded')
        self.assertEqual(port.retired[0]['actor_subject'], 'operator-a')


if __name__ == '__main__':
    unittest.main()
