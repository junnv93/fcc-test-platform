# log_handler.py
"""In-memory logging handler — drives the GUI log panel and any
:class:`LogEventPort` subscribers (Sprint GUI-AR-5, 2026-05-24).

Two responsibilities, single owner:

1. **History buffer** — bounded ``deque`` of recent :class:`LogEntry` values
   for retrieval via :py:meth:`get_logs` / :py:meth:`get_entries`. Used by
   the GUI log panel during initial population and full re-renders (filter
   changes).
2. **Push channel** — when an optional :class:`LogEventPort` is provided,
   every successful ``emit()`` also publishes the entry. The GUI subscribes
   via :class:`GuiLogAdapter`; future Web/CLI consumers can plug in their
   own subscribers without touching this module.

Domain types live in :mod:`domain.models.log_event` — this module imports
the value object and the topic-extraction rule rather than redefining them
(post-GUI-AR-5 SSOT).
"""
import logging
from collections import deque
from threading import Lock
from typing import Optional, Set

from domain.models.log_event import LogEntry, extract_topic
from domain.ports.output.log_event_port import LogEventPort
from infrastructure.logging.json_formatter import (
    _RESERVED_LOG_RECORD_ATTRS,
    _coerce_json_safe,
)


_MAX_LOG_ENTRIES = 10000  # L-10: 무한 증가 방지


class InMemoryLogHandler(logging.Handler):
    """``logging.Handler`` that stores entries in a bounded deque and
    optionally publishes each entry to a :class:`LogEventPort`.

    Parameters
    ----------
    event_port:
        Optional outbound port. When supplied, ``emit()`` publishes the
        constructed :class:`LogEntry` to all current subscribers in addition
        to appending it to the deque. ``None`` keeps legacy poll-based
        behaviour (history-buffer only).
    """

    def __init__(self, event_port: Optional[LogEventPort] = None):
        super().__init__()
        self._entries: deque = deque(maxlen=_MAX_LOG_ENTRIES)
        self._lock = Lock()
        self._event_port = event_port

    @property
    def event_port(self) -> Optional[LogEventPort]:
        return self._event_port

    def set_event_port(self, event_port: Optional[LogEventPort]) -> None:
        """Late binding for composition roots that build the bus after the
        logging singleton initialises. Not used in normal code paths — the
        port is normally injected via ``__init__``.
        """
        self._event_port = event_port

    def emit(self, record):
        try:
            msg = self.format(record)
            # OBS-0 + P1-1 (2026-05-25): surface correlation fields + W3C
            # trace context + non-reserved ``extra`` keys onto the LogEntry.
            # Reserved-attr filtering uses the SSOT exposed by
            # ``json_formatter`` so the GUI deque and the structured JSON
            # sink agree on what counts as user metadata.
            request_id = getattr(record, 'request_id', '') or ''
            connection_id = getattr(record, 'connection_id', '') or ''
            trace_id = getattr(record, 'trace_id', '') or ''
            span_id = getattr(record, 'span_id', '') or ''
            # P1-4 (2026-05-25): typed extra serialization — preserve
            # JSON-native int/float/bool while falling back to ``str(value)``
            # for arbitrary objects via the SSOT ``_coerce_json_safe``.
            extras: list[tuple[str, object]] = []
            _promoted_keys = (
                'request_id', 'connection_id', 'trace_id', 'span_id',
            )
            for key, value in vars(record).items():
                if key in _RESERVED_LOG_RECORD_ATTRS:
                    continue
                if key in _promoted_keys:
                    continue
                if value is None:
                    continue
                extras.append((key, _coerce_json_safe(value)))
            entry = LogEntry(
                timestamp=record.created,
                level=record.levelno,
                level_name=record.levelname,
                topic=extract_topic(record.name),
                message=msg,
                request_id=request_id if isinstance(request_id, str) else str(request_id),
                connection_id=(
                    connection_id if isinstance(connection_id, str) else str(connection_id)
                ),
                extra=tuple(extras),
                trace_id=trace_id if isinstance(trace_id, str) else str(trace_id),
                span_id=span_id if isinstance(span_id, str) else str(span_id),
            )
            with self._lock:
                self._entries.append(entry)
            port = self._event_port
            if port is not None:
                # Publish OUTSIDE the deque lock to avoid contention between
                # storage and subscriber callbacks. Subscribers may run on
                # the publisher's thread — they are responsible for any
                # thread marshalling.
                try:
                    port.publish(entry)
                except Exception:  # pragma: no cover — never crash the logger
                    # logging.Handler.handleError uses the same fallback,
                    # so we mirror that contract here for the push channel.
                    self.handleError(record)
        except Exception:
            self.handleError(record)

    def get_logs(self) -> list:
        """Backward-compatible API — returns list of formatted message strings."""
        with self._lock:
            return [e.message for e in self._entries]

    def get_entries(
        self,
        level_min: int = 0,
        topics: Optional[Set[str]] = None,
        limit: int = 1000,
    ) -> list:
        """Filtered retrieval of LogEntry objects.

        Args:
            level_min: minimum log level (e.g. logging.WARNING). 0 = all.
            topics:    set of topic prefixes to include (prefix-match).
                       None = all topics.
            limit:     maximum number of entries to return (newest first).
                       0 = no limit.

        Returns:
            list[LogEntry] — newest entries up to limit.
        """
        with self._lock:
            entries = list(self._entries)

        if level_min > 0:
            entries = [e for e in entries if e.level >= level_min]

        if topics is not None:
            entries = [
                e for e in entries
                if any(
                    e.topic == t or e.topic.startswith(t + '.')
                    for t in topics
                )
            ]

        if limit > 0:
            entries = entries[-limit:]

        return entries

    def clear(self):
        with self._lock:
            self._entries.clear()

    def __len__(self):
        with self._lock:
            return len(self._entries)
