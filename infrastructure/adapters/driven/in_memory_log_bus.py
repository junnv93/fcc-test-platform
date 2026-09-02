"""In-process pub/sub bus implementing :class:`LogEventPort`.

Sprint GUI-AR-5 (2026-05-24) — parallels
:class:`infrastructure.adapters.driven.in_memory_event_bus.InMemoryEventBus`
but tuned for the application-wide logging fan-out:

- Synchronous, sync-callback delivery (no asyncio loop ownership) — the
  callback runs inside ``publish``'s call chain on the publisher's thread.
- Subscribers register a ``Callable[[LogEntry], None]``; they decide how to
  marshal to their own threading context (the GUI subscriber wraps the
  callback in a Qt signal whose default ``QueuedConnection`` marshals to
  the main GUI thread).
- ``Subscription`` handle returned by ``subscribe`` carries ``cancel()``
  — idempotent, called by adapters in ``stop()``.

Thread-safety: the subscription set is guarded by a single ``RLock``.
Callbacks are dispatched **outside** the lock so a slow/blocking callback
cannot delay other subscribers from being registered/cancelled.

PySide6/asyncio/anyio/fastapi import is forbidden inside this module
(verified by ``TestLayerPurity``).
"""
from __future__ import annotations

import logging
import threading
from typing import List, Set

from domain.models.log_event import LOGGER_PREFIX, LogEntry
from domain.ports.output.log_event_port import (
    LogEventCallback,
    LogEventPort,
    Subscription,
)


__all__ = ['InMemoryLogBus']


# Use ``logging.getLogger`` directly (not the ``LoggingSystem`` singleton):
# this module is imported during ``LoggingSystem._setup()``, so re-entering
# the singleton's DCL ``Lock`` would deadlock. Child loggers under the
# canonical root inherit handlers once ``_setup()`` finishes. ``LOGGER_PREFIX``
# is the SSOT from ``domain.models.log_event`` — no string literal here.
_LOGGER = logging.getLogger(LOGGER_PREFIX + 'log_event_bus')


class _Subscription:
    """Cancellable handle paired with a registered callback."""

    __slots__ = ('_bus', '_callback', '_cancelled')

    def __init__(self, bus: 'InMemoryLogBus', callback: LogEventCallback) -> None:
        self._bus = bus
        self._callback = callback
        self._cancelled = False

    def cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        self._bus._unregister(self)

    @property
    def callback(self) -> LogEventCallback:
        return self._callback

    @property
    def cancelled(self) -> bool:
        return self._cancelled


class InMemoryLogBus:
    """Synchronous fan-out :class:`LogEventPort` implementation.

    ``publish(entry)`` invokes every active subscriber's callback on the
    calling thread. Exceptions raised by individual callbacks are swallowed
    (logged) so one buggy subscriber cannot starve the rest.
    """

    def __init__(self) -> None:
        self._subs: Set[_Subscription] = set()
        self._lock = threading.RLock()
        self._disposed = False
        self._total_callback_errors = 0

    # ------------------------------------------------------------------
    # LogEventPort surface
    # ------------------------------------------------------------------

    def publish(self, entry: LogEntry) -> None:
        if self._disposed:
            return
        # Snapshot under lock so unsubscribe during dispatch is safe.
        with self._lock:
            snapshot: List[_Subscription] = [s for s in self._subs if not s.cancelled]
        for sub in snapshot:
            try:
                sub.callback(entry)
            except Exception as exc:  # pragma: no cover — defensive
                # Never propagate — log_handler.emit() would call
                # handleError, but here we're already past the handler.
                self._total_callback_errors += 1
                _LOGGER.warning(
                    'InMemoryLogBus: subscriber callback raised (%s) — '
                    'cumulative errors=%d',
                    exc, self._total_callback_errors,
                )

    def subscribe(self, callback: LogEventCallback) -> Subscription:
        if self._disposed:
            raise RuntimeError('InMemoryLogBus is disposed')
        sub = _Subscription(self, callback)
        with self._lock:
            self._subs.add(sub)
        return sub

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        with self._lock:
            for sub in list(self._subs):
                sub._cancelled = True
            self._subs.clear()

    # ------------------------------------------------------------------
    # Observability (operations + tests)
    # ------------------------------------------------------------------

    def subscriber_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._subs if not s.cancelled)

    def total_callback_errors(self) -> int:
        return self._total_callback_errors

    # ------------------------------------------------------------------
    # Internal — used by ``_Subscription.cancel``
    # ------------------------------------------------------------------

    def _unregister(self, sub: _Subscription) -> None:
        with self._lock:
            self._subs.discard(sub)
