from __future__ import annotations

import unittest

from fcc_test_platform.application.central_result_selection_service import CentralResultSelectionService
from domain.ports.output.central_result_selection_port import SelectionRevisionConflictError


class FakeSelectionPort:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.calls: list[dict] = []
        self.append_calls: list[dict] = []

    def list_effective_results(self, project_id, provider_id, *, limit, cursor=None):
        self.calls.append({'op': 'effective', 'project_id': project_id, 'provider_id': provider_id})
        return {'items': [], 'next_cursor': None}

    def list_attempts(self, project_id, provider_id, condition_hash, *, limit, cursor=None):
        self.calls.append({'op': 'attempts', 'project_id': project_id, 'provider_id': provider_id, 'condition_hash': condition_hash})
        return {'items': [], 'next_cursor': None}

    def append_selection_event(self, **record):
        self.append_calls.append(record)
        current = self.events[-1]['revision'] if self.events else 0
        if current != record['expected_revision']:
            raise SelectionRevisionConflictError('stale')
        event = {
            'id': record['event_id'],
            'project_id': record['project_id'],
            'provider_id': record['provider_id'],
            'condition_hash': record['condition_hash'],
            'action': record['action'],
            'attempt_id': record['attempt_id'],
            'revision': current + 1,
            'expected_revision': current,
            'actor_subject': record['actor_subject'],
            # The persistence adapter, not the application service, owns this
            # value. A fake DB response keeps the service test honest about
            # the boundary without introducing an application clock.
            'occurred_at': 'db-clock',
            'reason': record['reason'],
        }
        self.events.append(event)
        return event

    def selected_source(self, project_id, provider_id, condition_hash):
        return self.events[-1] if self.events else None


class CentralResultSelectionServiceTests(unittest.TestCase):
    def test_scope_is_forwarded_on_both_reads(self) -> None:
        port = FakeSelectionPort()
        service = CentralResultSelectionService(port)
        service.list_effective_results('project-a', 'provider-a', limit=10)
        service.list_attempts('project-a', 'provider-a', 'condition-a', limit=10)
        self.assertEqual(port.calls[0]['provider_id'], 'provider-a')
        self.assertEqual(port.calls[1]['condition_hash'], 'condition-a')

    def test_select_then_clear_is_append_only_and_revisioned(self) -> None:
        port = FakeSelectionPort()
        service = CentralResultSelectionService(
            port,
            event_id_factory=iter(['event-1', 'event-2']).__next__,
        )
        selected = service.select(
            'project-a', 'provider-a', 'condition-a', attempt_id='attempt-1',
            expected_revision=0, actor_subject='operator-a',
        )
        cleared = service.clear(
            'project-a', 'provider-a', 'condition-a', expected_revision=1,
            actor_subject='operator-a', reason='recheck',
        )
        self.assertEqual(selected['revision'], 1)
        self.assertEqual(cleared['revision'], 2)
        self.assertEqual([event['action'] for event in port.events], ['selected', 'cleared'])

    def test_stale_revision_does_not_create_an_event(self) -> None:
        port = FakeSelectionPort()
        service = CentralResultSelectionService(
            port,
            event_id_factory=iter(['event-1', 'event-2']).__next__,
        )
        service.select(
            'project-a', 'provider-a', 'condition-a', attempt_id='attempt-1',
            expected_revision=0, actor_subject='operator-a',
        )
        with self.assertRaises(SelectionRevisionConflictError):
            service.clear(
                'project-a', 'provider-a', 'condition-a', expected_revision=0,
                actor_subject='operator-b',
            )
        self.assertEqual(len(port.events), 1)

    def test_event_time_is_not_supplied_or_selectable_by_service(self) -> None:
        port = FakeSelectionPort()
        service = CentralResultSelectionService(
            port,
            event_id_factory=iter(['event-1']).__next__,
        )

        event = service.select(
            'project-a', 'provider-a', 'condition-a', attempt_id='attempt-1',
            expected_revision=0, actor_subject='operator-a',
        )

        self.assertNotIn('occurred_at', port.append_calls[0])
        self.assertEqual(event['occurred_at'], 'db-clock')
        with self.assertRaises(TypeError):
            service.clear(
                'project-a', 'provider-a', 'condition-a', expected_revision=1,
                actor_subject='operator-a', occurred_at='caller-time',
            )


if __name__ == '__main__':
    unittest.main()
