"""Ingest → progress broadcast hook (멀티챔버 P7/B4, 2026-06-18).

C1 heartbeat ingest 의 **실시간 확장**(ADR-0015 Option B) 봉인 — ``PlatformApiAdapter
.push_chamber_heartbeat`` 가 in_use progress heartbeat 를 받으면 broadcaster 로
``ChamberProgressEvent`` 를 publish 한다:

  - in_use + progress → 정확히 1건 publish (chamber_id/progress/session_id/occurred_at).
  - idle(progress 없음) → publish 0 (도메인 invariant 가 idle+progress 자체를 차단).
  - 빈 progress({}) → 정규화 None → publish 0.
  - broadcaster 미주입(None) → no-op (기존 C1 ingest 경로 무변경 — fallback 보존).
  - broadcast 실패는 ingest ack 를 깨지 않는다(best-effort fan-out).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _REPO_ROOT / 'src'
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from fcc_test_platform.application.central_chamber_write_service import (  # noqa: E402
    CentralChamberWriteService,
)
from domain.models.chamber_node import ChamberProgressEvent  # noqa: E402
from fcc_test_platform.api.platform_routes import (  # noqa: E402
    PlatformApiAdapter,
)


_FIXED_NOW = '2026-06-18T01:02:03+00:00'


class _RecordingBroadcaster:
    def __init__(self) -> None:
        self.events: list[ChamberProgressEvent] = []

    def publish(self, event: ChamberProgressEvent) -> None:
        self.events.append(event)


class _ExplodingBroadcaster:
    def publish(self, event):  # noqa: ANN001
        raise RuntimeError('fan-out backend down')


class _FakeWritePort:
    """append_heartbeat / register_chamber recorder (CentralChamberWritePort shape)."""

    def __init__(self) -> None:
        self.heartbeats: list[dict] = []

    def append_heartbeat(self, record: dict) -> None:
        self.heartbeats.append(record)

    def register_chamber(self, record: dict) -> dict:
        return record


def _adapter(broadcaster, read_service=None):
    write_service = CentralChamberWriteService(
        _FakeWritePort(), clock=lambda: _FIXED_NOW, id_factory=lambda: 'id-1',
    )
    # access_policy=None → authorize is a no-op (node-token path tested elsewhere).
    return PlatformApiAdapter(
        read_service=read_service,
        access_policy=None,
        chamber_write_service=write_service,
        progress_broadcaster=broadcaster,
    )


class TestIngestBroadcast(unittest.TestCase):
    def test_in_use_progress_publishes_event(self):
        bus = _RecordingBroadcaster()
        adapter = _adapter(bus)
        ack = adapter.push_chamber_heartbeat({
            'chamber_id': 'chA',
            'reported_status': 'in_use',
            'session_id': 'sess-9',
            'progress': {'is_running': True, 'completed': 4, 'total': 10, 'ratio': 0.4},
        })
        self.assertEqual(ack['reported_status'], 'in_use')
        self.assertEqual(len(bus.events), 1)
        ev = bus.events[0]
        self.assertEqual(ev.chamber_id, 'chA')
        self.assertEqual(ev.session_id, 'sess-9')
        self.assertEqual(ev.occurred_at, _FIXED_NOW)
        self.assertEqual(ev.progress.completed, 4)
        self.assertEqual(ev.as_wire()['kind'], 'chamber_progress')

    def test_idle_heartbeat_does_not_publish(self):
        bus = _RecordingBroadcaster()
        adapter = _adapter(bus)
        adapter.push_chamber_heartbeat({'chamber_id': 'chA', 'reported_status': 'idle'})
        self.assertEqual(bus.events, [])

    def test_empty_progress_does_not_publish(self):
        bus = _RecordingBroadcaster()
        adapter = _adapter(bus)
        # in_use with empty progress dict → ChamberProgress.from_raw → None → no publish.
        adapter.push_chamber_heartbeat({
            'chamber_id': 'chA', 'reported_status': 'in_use', 'progress': {},
        })
        self.assertEqual(bus.events, [])

    def test_no_broadcaster_is_noop(self):
        adapter = _adapter(None)
        ack = adapter.push_chamber_heartbeat({
            'chamber_id': 'chA', 'reported_status': 'in_use',
            'progress': {'is_running': True, 'completed': 1, 'total': 2, 'ratio': 0.5},
        })
        # No broadcaster → ingest still succeeds (C1 path unchanged).
        self.assertEqual(ack['chamber_id'], 'chA')

    def test_broadcast_failure_does_not_break_ingest(self):
        adapter = _adapter(_ExplodingBroadcaster())
        ack = adapter.push_chamber_heartbeat({
            'chamber_id': 'chA', 'reported_status': 'in_use',
            'progress': {'is_running': True, 'completed': 1, 'total': 2, 'ratio': 0.5},
        })
        # fan-out exploded but the ack is still returned (best-effort fan-out).
        self.assertEqual(ack['reported_status'], 'in_use')

    def test_idle_with_progress_is_rejected_before_broadcast(self):
        # Domain invariant: idle + progress → ValueError (→ 400), never broadcast.
        bus = _RecordingBroadcaster()
        adapter = _adapter(bus)
        with self.assertRaises(ValueError):
            adapter.push_chamber_heartbeat({
                'chamber_id': 'chA', 'reported_status': 'idle',
                'progress': {'is_running': True, 'completed': 1, 'total': 2, 'ratio': 0.5},
            })
        self.assertEqual(bus.events, [])


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
