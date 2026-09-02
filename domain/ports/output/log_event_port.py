# domain/ports/output/log_event_port.py
"""LogEventPort — outbound port for application-level log streaming.

Sprint GUI-AR-5 (2026-05-24) — parallels :class:`SessionEventPort` but with
two intentional differences:

1. **Payload**: ``LogEntry`` (logger.Handler-sourced) instead of
   ``SessionEvent`` (TestRunner-sourced).
2. **Delivery model**: synchronous callback fan-out instead of asyncio
   queue. Rationale: ``logging.Handler.emit()`` is synchronous and may run
   from any thread; the GUI consumer wraps the callback in Qt signal/slot
   so QueuedConnection marshals to the main thread without an extra
   asyncio loop. Web/CLI consumers can register a callback that pushes into
   their own queue if needed.

Domain purity contract:
- ``Protocol`` only — no imports from ``infrastructure``, ``PySide6``,
  ``asyncio``, ``fastapi``.
- Two related types — :class:`LogEntry` (payload) and :class:`Subscription`
  (cancellable handle) — both pure.

A separate Port (vs. extending :class:`SessionEventPort`) keeps semantic
boundaries clear: one is application-wide logging, the other is a single
TestRunner session's progress feed.
"""
from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

from domain.models.log_event import LogEntry


__all__ = ['LogEventPort', 'Subscription', 'LogEventCallback']


LogEventCallback = Callable[[LogEntry], None]


@runtime_checkable
class Subscription(Protocol):
    """Cancellable handle returned by :py:meth:`LogEventPort.subscribe`.

    ``cancel()`` is idempotent — second call is a no-op.
    """

    def cancel(self) -> None: ...


@runtime_checkable
class LogEventPort(Protocol):
    """Outbound port: synchronous publish + sync-callback subscribe.

    Thread-safety contract:
    - ``publish`` may be called from any thread (including ``logging``'s
      emit thread).
    - ``subscribe`` callback is invoked **inside** ``publish``'s call chain
      from the publisher's thread — the consumer is responsible for any
      thread marshalling (e.g. Qt QueuedConnection).
    - ``cancel`` is idempotent; ``dispose`` makes subsequent ``publish`` a
      no-op.
    """

    def publish(self, entry: LogEntry) -> None:
        """Synchronous fan-out to all current subscribers."""
        ...

    def subscribe(self, callback: LogEventCallback) -> Subscription:
        """Register ``callback`` until the returned handle is cancelled."""
        ...

    def dispose(self) -> None:
        """Drop all subscriptions and make further ``publish`` a no-op."""
        ...
