"""Shared envelope / boundary-validation helpers for the platform surface
(FE-P3-write follow-up, 2026-05-28).

``central_read_service`` and ``central_claim_write_service`` both turn raw central
rows into stable API envelopes and validate uuid/text boundaries. These primitives
were duplicated across the two services; this is their single definition site so a
change to (e.g.) uuid canonicalization applies to both reads and writes.

dependency-free: stdlib ``uuid`` / ``datetime`` only (no infrastructure / FastAPI /
SQL imports — ``application.platform`` purity, sealed by
tests/test_platform_read_api_fe_p0d.py).
"""
from __future__ import annotations

from datetime import datetime, timezone
import uuid
from typing import Mapping, Optional


__all__ = [
    'apply_flat_merge_patch',
    'int_or_zero',
    'optional_int',
    'optional_text',
    'parse_timestamp',
    'require_uuid',
    'text',
]


def apply_flat_merge_patch(
    current: Optional[Mapping[str, object]],
    patch: Mapping[str, object],
) -> dict[str, str]:
    """Apply RFC 7396 JSON Merge Patch semantics to a FLAT string map.

    Three rules, and they are the whole reason this shape exists:

    * a key **absent** from ``patch`` is left as it is,
    * a key whose patch value is ``None`` is **removed**,
    * any other key is **set** to its patch value (as text).

    Standard reference: RFC 7396 (https://www.rfc-editor.org/rfc/rfc7396) §2.
    Restricted to one level because the documents this serves are flat
    ``key -> address`` maps; the recursive branch of the RFC has no member here
    and implementing it would invite nested documents nobody validates.

    Why per-key rather than whole-document replacement: it is what allows two
    people to edit two different fields of the same document concurrently
    without either silently discarding the other's write. That property only
    holds if the caller sends the keys it actually changed — a client that
    echoes every field it rendered has reconstructed whole-document replacement
    and reintroduced the lost update.

    Pure: no I/O, no clock, no SQL. The caller is responsible for performing the
    read and the write inside one transaction; merge semantics alone cannot make
    a read-modify-write atomic.
    """
    merged: dict[str, str] = {
        str(key): str(value)
        for key, value in (current or {}).items()
        if value is not None
    }
    for key, value in patch.items():
        name = str(key)
        if value is None:
            merged.pop(name, None)
        else:
            merged[name] = str(value)
    return merged


def require_uuid(value: object, label: str) -> str:
    """Canonicalize a uuid string at the API boundary (→ 400 on malformed).

    Returns the 36-char hyphenated lowercase canonical form so a "uuid-ish"
    string cannot smuggle SQL past the parameterized query. Raises ``ValueError``
    (mapped to HTTP 400 by the driving adapter) for empty/malformed input.
    """
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    try:
        return str(uuid.UUID(cleaned))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f'{label} is not a valid uuid: {value!r}') from exc


def text(value: object) -> str:
    """Coerce to a non-null string ('' for None) — for required envelope fields."""
    return '' if value is None else str(value)


def optional_text(value: object) -> Optional[str]:
    """Pass through None, else stringify — for nullable envelope fields."""
    return None if value is None else str(value)


def parse_timestamp(value: object) -> Optional[datetime]:
    """Tolerant parse of a central ledger timestamp into an aware UTC datetime.

    **Single definition site** for "what does a central timestamp column look
    like" (2026-07-30). Every platform read surface answers that question the
    same way, so it is answered once here: coverage/claim freshness, chamber
    heartbeat age, RBAC membership expiry, and the metrics collector all delegate.
    A second copy would let one read surface drift into reporting a different
    clock than another for the same ledger row.

    Accepts ISO-8601 (``2026-05-27T00:00:00[+00:00]`` / trailing ``Z``), a
    space-separated ``YYYY-MM-DD HH:MM:SS`` (what several drivers yield for a
    ``timestamp`` column), or a bare ``YYYY-MM-DD``. A naive value is assumed
    UTC — central stores UTC and a naive/aware mix would raise on subtraction.

    Returns ``None`` (→ "unknown", never "now") for empty/unparseable input and
    **never raises**: a freshness probe must not 500 because one ledger row
    carries an odd timestamp format.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    candidate = raw.replace('Z', '+00:00') if raw.endswith('Z') else raw
    for attempt in (candidate, candidate.replace(' ', 'T', 1), candidate[:10]):
        try:
            parsed = datetime.fromisoformat(attempt)
        except ValueError:
            continue
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None


def int_or_zero(value: object) -> int:
    """Coerce a count column to int (0 for None/empty)."""
    if value is None or value == '':
        return 0
    return int(value)


def optional_int(value: object) -> Optional[int]:
    """Coerce a nullable integer column (None for None/empty)."""
    if value is None or value == '':
        return None
    return int(value)
