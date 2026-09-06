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
from fcc_test_kernel.domain.models.chamber_node import ChamberProgressEvent  # noqa: E402
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


class TestTheRelayReadSideIsRealNotDeclared(unittest.TestCase):
    """``_SubscribableProgressBroadcaster`` 가 주석이 아니라 주장이게 하는 봉인.

    🔴 2026-09-06, ``api`` 를 strict 로 켜면서 드러난 사실:
    ``ChamberProgressBroadcastPort`` 는 스스로 「publish-only」라 선언하는데
    ``PlatformApiAdapter`` 는 그 타입으로 받아 놓고 진행 WS 릴레이에서
    ``.subscribe()`` 를 부른다. 즉 **선언된 타입이 실제 계약보다 좁다.**

    포트를 넓히는 것은 포트 주인의 판정이므로 이 웨이브는 api 쪽에서 좁혔다
    (``platform_routes._SubscribableProgressBroadcaster``). 그 좁힘은 「조립
    루트가 실제로 배선하는 객체가 그 모양을 갖는다」에 전적으로 의존하는데,
    ``cast`` 는 아무것도 검사하지 않는다 — 그래서 여기서 검사한다.

    ⚠️ ``isinstance`` 로 메서드 **존재**만 보지 않는다. 라우트가 쓰는 철자
    (``async with b.subscribe() as sub`` → ``async for ev in sub``) 를 그대로
    구동한다. ``__aenter__``/``__aexit__``/``__aiter__``/``__anext__`` 중 하나만
    빠져도 빨개진다.
    """

    def test_the_composition_root_wires_the_subscribable_broadcaster(self):
        # ① 배선되는 것이 정말 그 구상 클래스인가 — 포트만 만족하는 다른 객체가
        #    들어오면 라우트의 좁힘이 런타임에 AttributeError 로 무너진다.
        source = (_REPO_ROOT / 'fcc_test_platform' / 'api_composition.py').read_text()
        self.assertIn('progress_broadcaster = ChamberProgressBroadcaster()', source)
        self.assertIn('progress_broadcaster=progress_broadcaster', source)

    def test_the_wired_broadcaster_speaks_the_relay_spelling(self):
        # ② 라우트가 쓰는 철자를 그대로 구동한다.
        import asyncio

        from fcc_test_platform.infrastructure.adapters.driven.chamber_progress_broadcaster import (
            ChamberProgressBroadcaster,
        )

        bus = ChamberProgressBroadcaster()
        event = ChamberProgressEvent(
            chamber_id='ch-1',
            progress={'percent': 42},
            session_id='s-1',
            occurred_at=_FIXED_NOW,
        )
        seen: list[ChamberProgressEvent] = []

        async def relay():
            # platform_routes 의 WS 핸들러와 같은 철자.
            async with bus.subscribe() as subscription:
                bus.publish(event)
                async for received in subscription:
                    seen.append(received)
                    return

        asyncio.run(asyncio.wait_for(relay(), timeout=5))
        self.assertEqual([e.chamber_id for e in seen], ['ch-1'])
        # 구독은 스코프를 벗어나며 반드시 해제된다 — 누수는 매 publish 마다
        # 죽은 큐를 채운다(핸들러 주석이 명시하는 바로 그 이유).
        self.assertEqual(bus.subscriber_count(), 0)
