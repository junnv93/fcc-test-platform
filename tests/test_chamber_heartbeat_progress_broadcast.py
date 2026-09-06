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
from fcc_test_platform.domain.ports.output.chamber_progress_broadcast_port import (  # noqa: E402
    ChamberProgressBroadcastPort,
)


_FIXED_NOW = '2026-06-18T01:02:03+00:00'


class _RecordingBroadcaster:
    """publish 만 «쓰는» 시험을 위한 가짜 — 그래도 Port 전 표면을 갖는다.

    ⚠️ 안 쓰는 두 메서드를 왜 두나: 이 객체는 ``PlatformApiAdapter`` 의
    ``progress_broadcaster``(선언 타입 ``Optional[ChamberProgressBroadcastPort]``)
    자리에 들어간다. 가짜가 선언보다 좁으면 **그 시험은 이미 죽은 계약을 검사하게
    된다** — 실제 배선이 요구하는 것과 다른 것을 통과시키기 때문이다. 아래
    ``TestTheDoublesSatisfyThePort`` 가 그 등호를 잡는다.
    """

    def __init__(self) -> None:
        self.events: list[ChamberProgressEvent] = []
        self.disposed = False

    def publish(self, event: ChamberProgressEvent) -> None:
        self.events.append(event)

    def subscribe(self):
        raise AssertionError('이 가짜는 릴레이 읽기 축에 쓰이지 않는다')

    def dispose(self) -> None:
        self.disposed = True


class _ExplodingBroadcaster:
    def publish(self, event):  # noqa: ANN001
        raise RuntimeError('fan-out backend down')

    def subscribe(self):
        raise AssertionError('이 가짜는 릴레이 읽기 축에 쓰이지 않는다')

    def dispose(self) -> None:
        pass


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


class TestTheWiredBusMatchesTheDeclaredPort(unittest.TestCase):
    """배선되는 버스가 «선언된 Port» 와 같은 것임을 잡는 봉인.

    2026-09-07 — ``ChamberProgressBroadcastPort`` 가 버스 전 표면(publish ·
    subscribe · dispose)을 적게 되면서 ``api`` 쪽의 임시 Protocol 둘과 ``cast``
    는 걷혔다. 남은 위험은 **Port 와 구현이 갈라지는 것**이고, 그것을 잡는 축이
    셋이다 — 아래 세 검사가 그 셋이다.

    ⚠️ 왜 mypy 하나로 끝나지 않나: 구현이 Port 를 만나는 «타입» 지점은 조립
    루트(``api_composition.py``)인데 그 모듈은 strict 집합(domain · infrastructure
    · application · api) **밖**이라 게이트가 부르지 않는다. 그래서 구현 쪽의
    명시 상속이 유일하게 검사되는 자리이고, 그 상속이 살아 있는지는 여기서 본다.
    """

    def test_the_implementation_declares_the_port_explicitly(self):
        # ① 명시 상속이 곧 mypy 의 검사 지점이다. 이 base 를 떼면 구조적 충족만
        #    남는데, 그것을 «타입으로» 확인하는 자리가 이 레포에 없다(위 ⚠️).
        #    실측으로 이빨을 확인했다: 상속이 있는 상태에서 반환형을 옛
        #    ``AsyncIterator`` 로 되돌리면 mypy 가 [override] 로 빨개진다.
        from fcc_test_platform.infrastructure.adapters.driven.chamber_progress_broadcaster import (
            ChamberProgressBroadcaster,
        )

        self.assertIn(
            ChamberProgressBroadcastPort, ChamberProgressBroadcaster.__mro__,
            '구상 버스가 Port 를 명시 상속하지 않는다 — 그 순간 Port 와 구현의 '
            '갈라짐을 검사하는 자리가 이 레포에서 사라진다')

    def test_the_composition_root_wires_the_subscribable_broadcaster(self):
        # ② 배선되는 것이 정말 그 구상 클래스인가. ①이 「그 클래스면 안전하다」를
        #    주고, 이것이 「배선되는 것이 그 클래스다」를 준다. 조립 루트가 타입
        #    검사 밖이므로 이 축은 아직 소스 텍스트로 잰다.
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


class TestTheDoublesSatisfyThePort(unittest.TestCase):
    """가짜가 선언보다 좁으면 그 시험은 죽은 계약을 검사한다 — 이 레인의 관례
    (``assertIsInstance(<구현>, <Port>)``)를 진행 버스에도 적용한다.

    ⚠️ ``runtime_checkable`` 의 ``isinstance`` 는 메서드 **존재**만 본다. 반환
    타입 축은 이것이 아니라 위 ①(명시 상속 + mypy)이 갖는다 — 둘은 다른 축이고
    어느 하나가 다른 하나를 대신하지 못한다.
    """

    def test_the_doubles_are_broadcast_ports(self):
        for double in (_RecordingBroadcaster(), _ExplodingBroadcaster()):
            with self.subTest(double=type(double).__name__):
                self.assertIsInstance(double, ChamberProgressBroadcastPort)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
