"""Keyset (cursor) pagination SSOT for the platform read surface (FE-P0d).

The platform read model can return many rows per project (16k+ test items ×
technologies). Offset/LIMIT pagination degrades (O(n) skip) and is unstable
under concurrent ingestion. This module is the SSOT for **keyset** pagination:
an opaque cursor encodes the last row's sort key, and the read adapter resumes
with an index-backed ``WHERE (sort_cols) > (:cursor)`` range scan — O(log n +
page), stable while new attempts land.

The cursor is opaque base64url(JSON) so clients treat it as a token, never
couple to internal sort columns. A malformed/forged cursor is a loud
``CursorError`` (→ 400) — never silently ignored (that would skip rows).

**"Malformed" includes the value domain** (부채 청산 M3, 2026-07-30). Shape
validation alone (arity + "all strings") let a forged cursor such as
``["not-a-time", "not-a-uuid"]`` through to the database, where it bound against
``timestamptz``/``uuid`` keyset columns and PostgreSQL raised
``invalid input syntax`` — surfaced to the caller as **503 UPSTREAM_UNAVAILABLE**,
i.e. a client-forged token reported as a central outage. Live PostgreSQL 16
confirms both sqlstates (``22007`` timestamp, ``22P02`` uuid); see
``scripts/platform_keyset_cursor_live_proof.py``. Each keyset therefore declares
its column value domains and this module rejects out-of-domain values at the
boundary, where the answer is 400.

dependency-free: stdlib only (``application.platform`` / ``application.common``
purity). ``parse_timestamp`` is the shared boundary-parsing SSOT in the same
layer — "what is a valid central timestamp string" is answered in exactly one
place, whether the string arrives in a ledger row or in a cursor.
"""
from __future__ import annotations

import base64
from enum import Enum
import json
import uuid as _uuid
from typing import Callable, Optional, Sequence

from application.central_contract.envelope_helpers import parse_timestamp


__all__ = [
    'DEFAULT_PAGE_SIZE',
    'MAX_PAGE_SIZE',
    'CursorError',
    'CursorValueDomain',
    'clamp_limit',
    'decode_cursor',
    'encode_cursor',
]


#: Page-size policy SSOT. The OpenAPI query-param schema (api_contracts) derives
#: its ``default``/``maximum`` from these — no duplicated magic number.
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1000


class CursorError(ValueError):
    """Malformed/forged pagination cursor — loud (→ 400), never silently dropped.

    Silently ignoring a bad cursor would restart at page 1, making a client that
    thinks it is paging forward silently re-read / skip rows.
    """


class CursorValueDomain(Enum):
    """What a cursor position *is*, so the boundary can reject what the column
    cannot hold.

    The member set is deliberately the set of keyset column types that actually
    exist in the central read model (schema SSOT
    ``docs/platform/central_db_schema.v1.json``): ``text``, ``uuid``,
    ``timestamp``, and the small integer/nullability domains needed by explicit
    keyset orderings. ``NULLABLE_TIMESTAMP`` is distinct from ``TIMESTAMP``:
    JSON ``null`` is the SQL NULL position; an empty string is never a NULL
    sentinel. Adding a keyset over another type means adding its domain here —
    that is the point. A silent fall-through to "any string" is what turned a
    forged cursor into a 503.
    """

    TEXT = 'text'
    UUID = 'uuid'
    TIMESTAMP = 'timestamp'
    NULLABLE_TIMESTAMP = 'nullable_timestamp'
    INTEGER = 'integer'


def _validate_text(value: str) -> None:
    """Any string is a legal ``text`` cursor position — the query binds it as a
    parameter, so there is nothing to reject (an unmatched value simply yields an
    empty page, which is a correct answer, not an error)."""


def _validate_uuid(value: str) -> None:
    try:
        _uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise CursorError('invalid pagination cursor: not a uuid key') from exc


def _validate_timestamp(value: str) -> None:
    if parse_timestamp(value) is None:
        raise CursorError('invalid pagination cursor: not a timestamp key')


def _validate_nullable_timestamp(value: Optional[str]) -> None:
    if value is not None:
        _validate_timestamp(value)


def _validate_integer(value: str) -> None:
    if not value or (value[0] == '-' and not value[1:]) or not value.lstrip('-').isdigit():
        raise CursorError('invalid pagination cursor: not an integer key')


#: domain → validator. Raises ``CursorError`` (→ 400); never leaks the offending
#: value into the message (a forged token is attacker-controlled input and the
#: problem-details ``detail`` is client-visible).
_DOMAIN_VALIDATORS: dict[CursorValueDomain, Callable[[Optional[str]], None]] = {
    CursorValueDomain.TEXT: _validate_text,
    CursorValueDomain.UUID: _validate_uuid,
    CursorValueDomain.TIMESTAMP: _validate_timestamp,
    CursorValueDomain.NULLABLE_TIMESTAMP: _validate_nullable_timestamp,
    CursorValueDomain.INTEGER: _validate_integer,
}


def clamp_limit(limit: int | None) -> int:
    """Clamp a requested page size into ``[1, MAX_PAGE_SIZE]`` (default when None).

    Industry-standard bounding (Stripe/GitHub style): an out-of-range request is
    clamped rather than rejected, but a non-integer is a loud client error.
    """
    if limit is None:
        return DEFAULT_PAGE_SIZE
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise CursorError(f'limit must be an integer, got {limit!r}') from exc
    if value < 1:
        return 1
    return min(value, MAX_PAGE_SIZE)


def encode_cursor(keyset: Sequence[Optional[str]]) -> str:
    """Encode a sort-key tuple into an opaque base64url token.

    ``None`` is preserved as JSON ``null``. It is intentionally not converted
    to ``''``: nullable SQL positions need to remain distinguishable from a
    timestamp value and from an invalid empty string.
    """
    raw = json.dumps(
        [None if value is None else str(value) for value in keyset],
        separators=(',', ':'),
    ).encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii')


def decode_cursor(
    token: str, *, arity: int,
    domains: Optional[Sequence[CursorValueDomain]] = None,
) -> tuple[Optional[str], ...]:
    """Decode an opaque cursor back into a sort-key tuple of ``arity`` strings.

    Raises ``CursorError`` on any malformed/forged token (bad base64, bad JSON,
    wrong shape) so the API returns 400 instead of silently restarting.

    ``domains`` declares the value domain of each keyset column. When given, a
    value the column's type cannot hold is rejected **here** (400) instead of
    reaching PostgreSQL and coming back as a 503. ``NULLABLE_TIMESTAMP`` accepts
    JSON ``null`` and a valid timestamp, but not the empty string. Omitting
    ``domains`` keeps the historic all-``TEXT`` behaviour — legal only for a
    keyset whose columns really are all text; every non-text keyset in this
    codebase passes its domains.
    """
    try:
        raw = base64.urlsafe_b64decode(token.encode('ascii'))
        values = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 — any decode failure is a bad cursor
        raise CursorError('malformed pagination cursor') from exc
    if not isinstance(values, list) or len(values) != arity:
        raise CursorError(
            f'invalid pagination cursor shape (expected {arity} keys)'
        )
    if domains is not None:
        if len(domains) != arity:
            # Wiring bug, not client input — a keyset whose declared domains do
            # not line up with its columns would validate the wrong positions.
            raise ValueError(
                f'cursor domains arity {len(domains)} != keyset arity {arity}'
            )
        for value, domain in zip(values, domains):
            if value is None:
                if domain is not CursorValueDomain.NULLABLE_TIMESTAMP:
                    raise CursorError(
                        'invalid pagination cursor: null is not allowed for this key'
                    )
                continue
            if not isinstance(value, str):
                raise CursorError('invalid pagination cursor: key must be a string or null')
            _DOMAIN_VALIDATORS[domain](value)
    elif not all(isinstance(value, str) for value in values):
        raise CursorError(
            f'invalid pagination cursor shape (expected {arity} string keys)'
        )
    return tuple(values)
