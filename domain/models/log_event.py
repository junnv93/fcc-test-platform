# domain/models/log_event.py
"""Structured log entry — domain value object.

Sprint GUI-AR-5 (2026-05-24) — moved out of
``infrastructure/logging/log_handler.py`` so that the GUI/Web/Headless surfaces
can subscribe to a ``LogEventPort`` (defined in
``domain/ports/output/log_event_port.py``) without crossing the hexagonal
boundary into infrastructure.

Domain purity contract:
- frozen ``dataclass`` — value semantics, hashable.
- No imports from ``infrastructure``, ``PySide6``, ``pyvisa``, ``openpyxl``,
  ``pandas``, ``asyncio``, ``anyio``, ``fastapi`` — verified by
  ``TestDomainPurity``.
- ``extract_topic`` is a pure helper that strips the canonical
  ``'test_automation.'`` logger prefix — exposed at module level so adapters
  can construct a :class:`LogEntry` without re-implementing the rule.
"""
from __future__ import annotations

from dataclasses import dataclass


__all__ = ['LogEntry', 'LOGGER_PREFIX', 'LOGGER_ROOT', 'extract_topic']


# ── Canonical logger name SSOT — also re-exported by ``logger_config`` ─────
LOGGER_ROOT: str = 'test_automation'
LOGGER_PREFIX: str = f'{LOGGER_ROOT}.'


@dataclass(frozen=True)
class LogEntry:
    """One application-level log record.

    Attributes
    ----------
    timestamp:
        Unix epoch seconds (``logging.LogRecord.created``).
    level:
        Numeric level (e.g. ``logging.INFO`` = 20).
    level_name:
        ``"INFO"`` / ``"WARNING"`` / ... — pre-formatted to avoid the
        consumer importing ``logging`` for level-name lookup.
    topic:
        Logger name with ``'test_automation.'`` prefix stripped. ``''`` for
        the root logger; ``'measurement.obw'`` for a deep child.
    message:
        Fully formatted line as produced by the active ``logging.Formatter``.
    request_id:
        OBS-0 correlation field — surfaced from ``record.request_id`` when
        :class:`application.common.correlation.CorrelatedLoggerAdapter` is
        active. ``''`` when no HTTP/WS request context is bound.
    connection_id:
        OBS-0 correlation field — surfaced from ``record.connection_id`` for
        WS sessions. ``''`` when no connection context is bound.
    extra:
        Tuple of ``(key, value)`` pairs for any non-reserved attributes
        attached to the underlying :class:`logging.LogRecord` (e.g. via
        ``logger.info('msg', extra={'attempt': 3, 'success': True})``).
        ``tuple`` (not ``dict``) preserves both frozen semantics and
        hashability — the GUI log panel relies on :class:`LogEntry` being
        usable inside ``set`` for dedup.

        P1-4 (2026-05-25) — typed values preserved. Whitelist of
        JSON-native types (``str`` / ``int`` / ``float`` / ``bool``) flows
        through verbatim so backends with typed indexing (OTel attributes,
        Datadog measures, ELK long/double) ingest numeric/boolean fields
        with their original type. ``None`` is dropped at the handler.
        Non-whitelist types (datetime, Path, custom objects) fall back to
        ``str(value)`` via the ``_coerce_json_safe`` SSOT.
    trace_id:
        P1-1 (2026-05-25) — W3C Trace Context ``trace-id`` (32 hex chars).
        Distributed tracing의 first-class field per OTel Log Data Model.
        ``''`` when no traceparent context is bound.
    span_id:
        P1-1 (2026-05-25) — W3C Trace Context ``parent-id`` / current span
        id (16 hex chars). ``''`` when no traceparent context is bound.
    """

    timestamp: float
    level: int
    level_name: str
    topic: str
    message: str
    request_id: str = ''
    connection_id: str = ''
    # P1-4 (2026-05-25): Union value type — int/float/bool 보존.
    # All four whitelist types are hashable so the frozen+hashable contract
    # (GUI panel set-dedup) is preserved.
    extra: tuple[tuple[str, "str | int | float | bool"], ...] = ()
    trace_id: str = ''
    span_id: str = ''


def extract_topic(name: str) -> str:
    """Strip the canonical ``'test_automation.'`` prefix from a logger name.

    >>> extract_topic('test_automation')
    ''
    >>> extract_topic('test_automation.measurement')
    'measurement'
    >>> extract_topic('other.logger')
    'other.logger'
    """
    if name == LOGGER_ROOT:
        return ''
    if name.startswith(LOGGER_PREFIX):
        return name[len(LOGGER_PREFIX):]
    return name
