"""Structured JSON log formatter — OBS-0 (2026-05-25).

Serialises :class:`logging.LogRecord` instances into single-line JSON suitable
for ingestion by Loki, Datadog, ELK, or any OTel log collector. Pairs with
:class:`application.common.correlation.CorrelatedLoggerAdapter` (Deep #1)
so every record automatically carries ``request_id`` / ``connection_id``
when an HTTP middleware or WS handler bound them via
:py:func:`application.common.correlation.bind_request_id` /
:py:func:`application.common.correlation.bind_connection_id`.

Design contract
---------------
- **stdlib only** — ``json.dumps`` from the standard library. No
  ``python-json-logger`` / ``structlog`` / ``orjson`` dependency (project
  rule: dependency-free).
- **OTel-compatible field names** — ``timestamp`` / ``level`` / ``logger`` /
  ``topic`` / ``message`` / ``request_id`` / ``connection_id`` map directly
  to the OpenTelemetry Log Data Model and to Datadog log attributes.
- **Reserved attrs SSOT** — :data:`_RESERVED_LOG_RECORD_ATTRS` is the single
  source of truth for the set of attributes Python's logging module itself
  adds to every :class:`LogRecord`. Custom ``extra={...}`` keys are recovered
  by taking the set difference. Imported by
  :mod:`infrastructure.logging.log_handler` so the GUI deque and the JSON
  sink agree on what counts as user-supplied metadata.
- **Typed extras** — JSON-native ``extra`` values (``str`` / ``int`` /
  ``float`` / ``bool`` / bounded ``list`` / ``dict``) are preserved for OTel
  typed attributes. Unknown objects fall back to ``str(value)`` so logging
  never raises on diagnostic metadata.
- **No PII leakage** — message-level scrubbing is the caller's responsibility
  (we never touch ``record.msg`` / args; we only surface what
  ``logging.Formatter`` would emit anyway).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Final

from domain.models.log_event import extract_topic


__all__ = [
    'StructuredJsonFormatter',
    '_RESERVED_LOG_RECORD_ATTRS',
    '_coerce_json_safe',
]


# ── Reserved attribute names — single source of truth ──────────────────────
#
# Built by inspecting a fresh ``logging.LogRecord`` created with empty args
# at module import time. Mirrors the ``python-json-logger`` RESERVED_ATTRS
# pattern but derived dynamically from the stdlib (no hardcoded list to
# drift if Python adds new fields). The extra entries — ``message``,
# ``asctime`` — are populated by :py:meth:`logging.Formatter.format` /
# :py:meth:`LogRecord.getMessage` after construction; we list them so the
# difference set treats them as reserved too.
def _build_reserved_attrs() -> frozenset[str]:
    probe = logging.LogRecord(
        name='_probe', level=logging.INFO, pathname='', lineno=0,
        msg='', args=(), exc_info=None,
    )
    base = set(vars(probe).keys())
    # ``message`` is added by ``LogRecord.getMessage()``; ``asctime`` by
    # ``logging.Formatter.format()`` when ``%(asctime)s`` is referenced.
    base.update({'message', 'asctime'})
    return frozenset(base)


_RESERVED_LOG_RECORD_ATTRS: Final[frozenset[str]] = _build_reserved_attrs()
_MAX_COLLECTION_DEPTH: Final[int] = 4


def _coerce_json_safe(value: object, *, _depth: int = 0) -> object:
    """Coerce ``value`` into a JSON-native type while preserving int/float/bool.

    P1-4 (2026-05-25) — OTel typed attribute spec compliance. The previous
    implementation forced ``str(value)`` on every non-``None`` value which
    eliminated the chance of numeric/boolean filtering in the backend
    (``attempts > 5`` queries impossible against ``"5"`` strings).

    Whitelist of JSON-native types (``str`` / ``int`` / ``float`` / ``bool``)
    is preserved verbatim — ``json.dumps`` will emit them as ``"x"`` / ``5``
    / ``5.2`` / ``true``. Any other type (``datetime``, ``Path``, custom
    objects) falls back to ``str(value)`` so the JSON line stays valid;
    silent corruption is preferred over throwing inside the logger.

    ``None`` returns ``None`` (callers decide whether to skip or emit
    ``null``).

    Order matters: ``isinstance(value, bool)`` MUST run before
    ``isinstance(value, int)`` because Python evaluates ``True == 1`` —
    without the bool branch a ``True`` would silently serialise as ``1``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        if _depth >= _MAX_COLLECTION_DEPTH:
            return str(value)
        return [
            _coerce_json_safe(item, _depth=_depth + 1)
            for item in value
            if item is not None
        ]
    if isinstance(value, dict):
        if _depth >= _MAX_COLLECTION_DEPTH:
            return str(value)
        return {
            str(key): _coerce_json_safe(item, _depth=_depth + 1)
            for key, item in value.items()
            if item is not None
        }
    return str(value)


def _custom_extras(record: logging.LogRecord) -> dict[str, object]:
    """Return the set difference of ``record.__dict__`` and reserved attrs.

    P1-4 (2026-05-25): values pass through :func:`_coerce_json_safe` so
    JSON-native types stay typed (no ``str()`` coercion). Keys whose values
    are ``None`` are still dropped — ``None`` carries no diagnostic signal
    and clutters the JSON output.
    """
    out: dict[str, object] = {}
    for key, value in vars(record).items():
        if key in _RESERVED_LOG_RECORD_ATTRS:
            continue
        if value is None:
            continue
        out[key] = _coerce_json_safe(value)
    return out


class StructuredJsonFormatter(logging.Formatter):
    """``logging.Formatter`` subclass producing one JSON object per record.

    Field schema (always present, ``''`` when unset)::

        {
          "timestamp": "2026-05-25T07:12:43.521000+00:00",  # ISO 8601, UTC
          "level":     "INFO",
          "level_no":  20,
          "logger":    "test_automation.measurement.obw",
          "topic":     "measurement.obw",                    # prefix-stripped
          "message":   "OBW measurement started — channel 6",
          "request_id":    "abc-123",                        # from extra
          "connection_id": "ws-7",                            # from extra
          ...any custom extras keys merged at the top level...
        }

    The formatter also serialises ``exc_info`` (under ``"exception"``) and
    ``stack_info`` (under ``"stack"``) when present — both are full text
    blobs as produced by the base formatter.
    """

    def __init__(self) -> None:
        super().__init__()

    def format(self, record: logging.LogRecord) -> str:
        # ``record.getMessage()`` returns the formatted message after %-args
        # substitution. Use it instead of ``self.formatMessage(record)`` so
        # that the JSON ``message`` field stays free of asctime/level
        # decoration which the plain text formatter adds.
        rendered_message = record.getMessage()

        timestamp_iso = (
            datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec='microseconds')
        )

        payload: dict[str, object] = {
            'timestamp': timestamp_iso,
            'level': record.levelname,
            'level_no': record.levelno,
            'logger': record.name,
            'topic': extract_topic(record.name),
            'message': rendered_message,
        }

        # OBS-0 + P1-1 correlation: surface the canonical fields explicitly
        # so the backend can index them without a wildcard search through
        # extras. CorrelatedLoggerAdapter writes all four via
        # ``record.extra``; we read whatever is on the record (string-coerced)
        # so direct callers that pass ``extra={'request_id': ...,
        # 'trace_id': ...}`` also work.
        request_id = getattr(record, 'request_id', '') or ''
        connection_id = getattr(record, 'connection_id', '') or ''
        trace_id = getattr(record, 'trace_id', '') or ''
        span_id = getattr(record, 'span_id', '') or ''
        payload['request_id'] = (
            request_id if isinstance(request_id, str) else str(request_id)
        )
        payload['connection_id'] = (
            connection_id if isinstance(connection_id, str) else str(connection_id)
        )
        # P1-1 (2026-05-25) — W3C Trace Context fields. OTel Log Data Model
        # treats these as standardized top-level attributes — never nested
        # under ``attributes:{}``.
        payload['trace_id'] = (
            trace_id if isinstance(trace_id, str) else str(trace_id)
        )
        payload['span_id'] = (
            span_id if isinstance(span_id, str) else str(span_id)
        )

        # Merge custom extras at the top level (OTel log data model style —
        # attributes are first-class, not nested under "attributes":{}).
        # ``request_id`` / ``connection_id`` / ``trace_id`` / ``span_id``
        # already promoted above; the difference set still includes them but
        # the merge order ensures the explicit promotion wins. We pop them
        # defensively to avoid double-write surprises in tests.
        extras = _custom_extras(record)
        extras.pop('request_id', None)
        extras.pop('connection_id', None)
        extras.pop('trace_id', None)
        extras.pop('span_id', None)
        for key, value in extras.items():
            # Don't let custom extras shadow reserved JSON fields. If a
            # caller passes ``extra={'message': ...}`` the LogRecord's
            # ``__init__`` would have rejected it anyway, but be explicit.
            if key in payload:
                continue
            payload[key] = value

        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        if record.stack_info:
            payload['stack'] = self.formatStack(record.stack_info)

        # ``ensure_ascii=False`` lets Korean / non-ASCII messages stay
        # human-readable in the JSON line. ``separators`` shaves a few bytes
        # vs. the default ``", "`` / ``": "``.
        return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
