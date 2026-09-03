"""In-process chamber progress fan-out broadcaster (멀티챔버 P7/B4, 2026-06-18).

중앙 진행 릴레이(ADR-0015 Option B — 노드 push 릴레이)의 fan-out 엔진. 중앙 Platform
API 의 heartbeat ingest 경로가 :class:`ChamberProgressEvent` 를 :meth:`publish` 하면,
중앙 WebSocket fan-out 엔드포인트(``/platform/chambers/events``)가 :meth:`subscribe`
로 구독해 웹 브라우저에게 재발행한다.

:class:`infrastructure.adapters.driven.in_memory_event_bus.InMemoryEventBus` 의 검증된
교차-스레드 안전 + bounded drop-oldest backpressure 패턴을 **복제**한다. 세션 이벤트
버스는 ``SessionEvent`` 전용(노드 측정 로그/진행/알림)이라 직접 재사용할 수 없다 —
챔버 진행 릴레이는 별개 도메인 이벤트(:class:`ChamberProgressEvent`)를 운반하며, 세션
WS 봉인(``test_ws_*``)을 흔들지 않도록 자체 모듈로 분리해 독립 봉인한다. 두 버스의
구조적 동형성은 의도적이며 각자 invariant 로 봉인된다(``publish`` 가 동일 정책 — 같은
*사실/상수* 가 아니라 같은 *메커니즘*).

Backpressure 정책: 구독자마다 bounded ``asyncio.Queue``. 가득 차면 가장 오래된 이벤트를
**명시적으로 drop** + 누적 drop 카운트 WARNING(silent drop 금지 — 운영자 가시성).

신규 outbound HTTP/WS 의존 0 — stdlib ``asyncio``/``threading`` 만(ADR-0015 Option B).
PySide6/FastAPI/httpx/aiohttp import 0.
"""
from __future__ import annotations

import asyncio
import threading
from typing import AsyncIterator, Optional

from fcc_test_kernel.domain.models.chamber_node import ChamberProgressEvent
from fcc_test_kernel.logger_config import get_logger


__all__ = ['ChamberProgressBroadcaster', 'DEFAULT_PROGRESS_BUFFER_SIZE']


_LOGGER = get_logger('chamber_progress_broadcaster')


#: 구독자별 진행 이벤트 버퍼 기본 크기 SSOT. 진행 이벤트는 상태 스냅샷(누적 아님)이라
#: 과거 이벤트 손실이 무해(최신만 유의미) — drop-oldest 가 정확히 이 의도를 구현한다.
#: in_use 챔버 progress 는 저빈도(heartbeat/측정 단위)라 작은 버퍼로 충분.
DEFAULT_PROGRESS_BUFFER_SIZE = 64


class _Subscription:
    """단일 구독 채널 — bounded asyncio queue + drop 카운터."""

    __slots__ = ('queue', 'dropped', 'closed')

    def __init__(self, buffer_size: int) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=buffer_size)
        self.dropped: int = 0
        self.closed: bool = False

    def offer(self, event: ChamberProgressEvent) -> None:
        """가득 차면 drop-oldest. 반드시 버스의 asyncio 루프에서 호출."""
        if self.closed:
            return
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.dropped += 1
            _LOGGER.warning(
                'ChamberProgressBroadcaster: dropped oldest progress event '
                '(subscription drop count=%d)',
                self.dropped,
            )
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1
            _LOGGER.warning(
                'ChamberProgressBroadcaster: queue still full after drop '
                '(drop count=%d)',
                self.dropped,
            )

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        # 대기 중인 get() 을 sentinel None 으로 깨움(소비자는 None 을 end-of-stream 으로 해석).
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.queue.put_nowait(None)


class ChamberProgressBroadcaster:
    """진행 이벤트 fan-out in-process 버스.

    Thread-safety: 구독 집합/큐 변경은 모두 버스의 asyncio 루프에서
    ``call_soon_threadsafe`` 로 수행(요청/측정 스레드 어디서든 :meth:`publish` 안전).
    :class:`domain.ports.output.chamber_progress_broadcast_port.ChamberProgressBroadcastPort`
    를 구조적으로 충족(publish 표면) + WS 핸들러가 직접 소비할 async :meth:`subscribe`.
    """

    def __init__(self, buffer_size: Optional[int] = None) -> None:
        if buffer_size is None:
            buffer_size = DEFAULT_PROGRESS_BUFFER_SIZE
        if buffer_size <= 0:
            raise ValueError('buffer_size must be positive')
        self._buffer_size = buffer_size
        self._subs: set[_Subscription] = set()
        self._lock = threading.RLock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._disposed = False
        self._total_dropped = 0

    # ── synchronous publish (thread-safe) ────────────────────────────────────

    def publish(self, event: ChamberProgressEvent) -> None:
        if self._disposed:
            return
        loop = self._loop
        if loop is None:
            # 아직 구독자 없음 — 조용히 drop(유일한 합법적 silent drop: 아직 아무도
            # 관측하지 않은 producer. 권위 SSOT 는 C1 heartbeat ledger 다).
            return
        if loop.is_closed():
            return
        loop.call_soon_threadsafe(self._dispatch, event)

    def _dispatch(self, event: ChamberProgressEvent) -> None:
        with self._lock:
            subs = tuple(self._subs)
        for sub in subs:
            dropped_before = sub.dropped
            sub.offer(event)
            if sub.dropped > dropped_before:
                self._total_dropped += sub.dropped - dropped_before

    # ── async subscribe ──────────────────────────────────────────────────────

    def subscribe(self) -> 'AsyncIterator[ChamberProgressEvent]':
        """새 구독에 대한 async iterator + 컨텍스트 매니저 반환."""
        return _SubscriptionScope(self)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        loop = self._loop
        if loop is None or loop.is_closed():
            with self._lock:
                self._subs.clear()
            return
        loop.call_soon_threadsafe(self._close_all)

    def _close_all(self) -> None:
        with self._lock:
            subs = tuple(self._subs)
            self._subs.clear()
        for sub in subs:
            sub.close()

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def total_dropped(self) -> int:
        """backpressure 로 drop 된 누적 이벤트 수(전 구독자 합)."""
        return self._total_dropped

    # ── subscription registration (async context) ────────────────────────────

    def _register(self) -> _Subscription:
        if self._disposed:
            raise RuntimeError('ChamberProgressBroadcaster is disposed')
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        elif self._loop is not asyncio.get_running_loop():
            raise RuntimeError(
                'ChamberProgressBroadcaster is already bound to a different asyncio loop'
            )
        sub = _Subscription(self._buffer_size)
        with self._lock:
            self._subs.add(sub)
        return sub

    def _unregister(self, sub: _Subscription) -> None:
        with self._lock:
            self._subs.discard(sub)
        sub.close()


class _SubscriptionScope:
    """구독에 대한 async 컨텍스트 매니저 + async iterator."""

    def __init__(self, bus: ChamberProgressBroadcaster) -> None:
        self._bus = bus
        self._sub: Optional[_Subscription] = None

    async def __aenter__(self) -> '_SubscriptionScope':
        self._sub = self._bus._register()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._sub is not None:
            self._bus._unregister(self._sub)
            self._sub = None

    def __aiter__(self) -> '_SubscriptionScope':
        if self._sub is None:
            # ``async with`` 없이 ``async for ev in bus.subscribe():`` 허용.
            self._sub = self._bus._register()
        return self

    async def __anext__(self) -> ChamberProgressEvent:
        if self._sub is None:
            raise StopAsyncIteration
        item = await self._sub.queue.get()
        if item is None:  # sentinel — close 신호
            raise StopAsyncIteration
        return item
