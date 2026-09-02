# domain/ports/output/notification_event_port.py
"""NotificationEventPort — outbound port for user-visible notification stream.

Sprint GUI-PG-3 (2026-05-25) — parallels :class:`LogEventPort` so the GUI,
future Web feed, and DB persistence adapter consume one bus without crossing
the hexagonal boundary. Why a separate Port from ``LogEventPort``:

- **Audience**: ``LogEventPort`` carries every ``logging.LogRecord`` (developer
  firehose, 1000+ entries per session). ``NotificationEventPort`` carries
  only curated, severity-tagged notifications meant for the end-user.
- **Persistence**: notification entries are persisted to the session DB
  (``notifications`` table, migration 014). Log entries are not — they are
  written to the rotating JSON sink instead.
- **Severity is first-class**: notifications carry a ``NotificationSeverity``
  literal; logs carry numeric ``logging.INFO``/``WARNING``/``ERROR`` levels
  that may not map 1:1 to the user-facing severity vocabulary.

Delivery model mirrors :class:`LogEventPort` — synchronous callback fan-out.
Rationale identical to GUI-AR-5 (one fewer queue hop than the asyncio bus
pattern; Qt ``QueuedConnection`` handles the GUI marshalling).

Domain purity contract
----------------------
- ``Protocol`` only — no imports from ``infrastructure``, ``PySide6``,
  ``asyncio``, ``fastapi``, ``sqlalchemy``.
- Two related types — :class:`NotificationEntry` (payload) and
  :class:`NotificationSubscription` (cancellable handle) — both pure.
"""
from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

from domain.models.notification_event import NotificationEntry


__all__ = [
    'NotificationEventPort',
    'NotificationSubscription',
    'NotificationEventCallback',
]


NotificationEventCallback = Callable[[NotificationEntry], None]


@runtime_checkable
class NotificationSubscription(Protocol):
    """Cancellable handle returned by :py:meth:`NotificationEventPort.subscribe`.

    ``cancel()`` is idempotent — second call is a no-op. The bus drops the
    callback from its dispatch set on the first call.
    """

    def cancel(self) -> None: ...


@runtime_checkable
class NotificationEventPort(Protocol):
    """Outbound port: synchronous publish + sync-callback subscribe.

    Thread-safety contract:
    - ``publish`` may be called from any thread (TestRunner worker,
      orchestrator, GUI). Implementations must guard subscriber set
      mutation with a lock.
    - ``subscribe`` callback runs **inside** ``publish``'s call chain on
      the publisher's thread — the consumer is responsible for thread
      marshalling (e.g. Qt ``QueuedConnection``).
    - ``cancel`` is idempotent; ``dispose`` makes subsequent ``publish`` a
      no-op (does not raise).
    """

    def publish(self, entry: NotificationEntry) -> None:
        """Synchronous fan-out to all current subscribers."""
        ...

    def subscribe(self, callback: NotificationEventCallback) -> NotificationSubscription:
        """Register ``callback`` until the returned handle is cancelled."""
        ...

    def dispose(self) -> None:
        """Drop all subscriptions and make further ``publish`` a no-op."""
        ...
