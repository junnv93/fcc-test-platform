# domain/models/notification_event.py
"""User-facing notification entry — domain value object.

Sprint GUI-PG-3 (2026-05-25) — parallel to ``domain/models/log_event.py`` but
scoped to **user-visible** notifications (the sidebar notification panel and
the future Web notification feed). Logs are a developer-facing firehose;
notifications are a curated, severity-tagged stream that is also persisted to
the session DB so a process restart can rehydrate the user's view.

Why severity is a first-class field
-----------------------------------
The previous code path injected an ``'[ERROR]'`` prefix into the message body
and parsed it back in ``_refresh_notification_colors`` via
``item.text().startswith('[ERROR]')``. That created a single-direction
dependency that the renderer could not enforce — adding a new severity meant
extending both the prefix vocabulary *and* the reverse-parse branches. By
promoting severity to a first-class field on this dataclass, every renderer
(GUI panel, future Web feed, DB persistence) reads the same canonical value
and prefix injection becomes unnecessary.

Domain purity contract
----------------------
- ``frozen=True`` — value semantics, hashable so ``set`` dedup is safe.
- No imports from ``infrastructure``, ``PySide6``, ``pyvisa``, ``openpyxl``,
  ``pandas``, ``asyncio``, ``anyio``, ``fastapi``, ``sqlalchemy`` — verified
  by ``TestDomainPurity``.
- ``Literal`` type for severity gives static checkers a closed enumeration
  without dragging an ``enum`` import (keeps the module to stdlib typing only).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


__all__ = [
    'NotificationSeverity',
    'NotificationEntry',
    'SEVERITY_INFO',
    'SEVERITY_WARNING',
    'SEVERITY_ERROR',
    'VALID_SEVERITIES',
]


# Canonical severity tokens — SSOT used by sidebar palette mapping, DB column
# CHECK constraint, and Web fan-out. Strings (not enum) so JSON serialization
# stays trivial and stdlib-only.
SEVERITY_INFO: str = 'INFO'
SEVERITY_WARNING: str = 'WARNING'
SEVERITY_ERROR: str = 'ERROR'

# Frozen set used by the DB store + bus to reject invalid input at the
# boundary. Importers should treat this as read-only.
VALID_SEVERITIES: frozenset = frozenset({SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_ERROR})


NotificationSeverity = Literal['INFO', 'WARNING', 'ERROR']


@dataclass(frozen=True)
class NotificationEntry:
    """One user-visible notification record.

    Attributes
    ----------
    timestamp:
        Unix epoch seconds — same convention as
        :class:`domain.models.log_event.LogEntry.timestamp`.
    severity:
        ``'INFO'`` / ``'WARNING'`` / ``'ERROR'`` (canonical, uppercase).
        Reject everything else at the ``InMemoryNotificationBus.publish``
        boundary so the renderer can rely on a closed set.
    message:
        Human-readable notification body. Must **not** carry severity
        prefixes (``'[ERROR] ...'`` etc.) — the renderer pulls severity from
        the dedicated field above.
    source:
        Optional originator label (``'orchestrator'`` / ``'measurement'`` /
        ``'device'`` / ...). Empty string when not relevant. Useful for
        the future Web feed's filter dropdown.
    noti_type:
        Optional Excel-sheet ``Notification`` row type (``'WLAN'`` /
        ``'BLE'`` / ``'BT'`` / ``'Test Failed'`` / ...) that the
        :class:`FileIOManager` outbox path attached to this entry. Empty
        when the entry didn't originate from the test-fail flow.
    """

    timestamp: float
    severity: NotificationSeverity
    message: str
    source: str = ''
    noti_type: str = ''
