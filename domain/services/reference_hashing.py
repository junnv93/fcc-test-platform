"""Deterministic, typed canonical hashing for workbook reference evidence."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import unicodedata
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Iterable, Mapping, Optional


def _canonical_decimal(value: Decimal) -> str:
    if not value.is_finite():
        return str(value)
    normalized = value.normalize()
    if normalized == Decimal('-0'):
        normalized = Decimal(0)
    return format(normalized, 'f')


def canonicalize(value: Any) -> Any:
    """Convert supported values into a stable JSON-compatible typed tree.

    Numbers share one canonical representation so Excel's integer/float
    boundary does not create a false semantic change.  Strings are Unicode NFC
    and line-ending normalized.  Bytes are represented by size and digest, not
    by their contents.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        return {'type': 'bool', 'value': value}
    if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        decimal_value = Decimal(value)
        return {'type': 'number', 'value': _canonical_decimal(decimal_value)}
    if isinstance(value, float):
        if math.isnan(value):
            return {'type': 'float', 'value': 'NaN'}
        if math.isinf(value):
            return {'type': 'float', 'value': 'Infinity' if value > 0 else '-Infinity'}
        try:
            decimal_value = Decimal(str(value))
        except InvalidOperation as exc:
            raise TypeError(f'unsupported float value: {value!r}') from exc
        return {'type': 'number', 'value': _canonical_decimal(decimal_value)}
    if isinstance(value, datetime):
        return {'type': 'datetime', 'value': value.isoformat()}
    if isinstance(value, date):
        return {'type': 'date', 'value': value.isoformat()}
    if isinstance(value, time):
        return {'type': 'time', 'value': value.isoformat()}
    if isinstance(value, str):
        normalized = unicodedata.normalize('NFC', value)
        normalized = normalized.replace('\r\n', '\n').replace('\r', '\n')
        return {'type': 'string', 'value': normalized}
    if isinstance(value, bytes):
        return {
            'type': 'bytes',
            'size': len(value),
            'sha256': hashlib.sha256(value).hexdigest(),
        }
    if isinstance(value, Enum):
        return {
            'type': 'enum',
            'class': f'{type(value).__module__}.{type(value).__qualname__}',
            'value': canonicalize(value.value),
        }
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            'type': 'dataclass',
            'class': f'{type(value).__module__}.{type(value).__qualname__}',
            'fields': {
                field.name: canonicalize(getattr(value, field.name))
                for field in dataclasses.fields(value)
            },
        }
    if isinstance(value, Mapping):
        entries = []
        for key, item in value.items():
            entries.append((str(key), canonicalize(item)))
        entries.sort(key=lambda pair: pair[0])
        return {
            'type': 'mapping',
            'items': [{'key': key, 'value': item} for key, item in entries],
        }
    if isinstance(value, (list, tuple)):
        return {
            'type': 'sequence',
            'items': [canonicalize(item) for item in value],
        }
    raise TypeError(f'unsupported value for canonical hashing: {type(value)!r}')


def canonical_json(value: Any) -> str:
    """Return canonical UTF-8 JSON text for a supported value tree."""

    return json.dumps(
        canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False,
    )


def canonical_semantic_hash(value: Any) -> str:
    """Hash the typed canonical representation of ``value``."""

    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def hash_canonical_records(records: Iterable[Any]) -> str:
    """Hash a sequence with record boundaries preserved.

    A newline separator is part of the stream framing.  It prevents adjacent
    records from becoming ambiguous while keeping memory bounded for large
    worksheets.
    """

    digest = hashlib.sha256()
    for record in records:
        digest.update(canonical_json(record).encode('utf-8'))
        digest.update(b'\n')
    return digest.hexdigest()


def hash_sheet_records(
    *,
    dimension: str,
    max_row: int,
    max_column: int,
    records: Iterable[Mapping[str, Any]],
) -> tuple[str, str]:
    """Hash raw and semantic record streams without retaining cell contents.

    Each input record must contain ``raw`` and ``semantic`` mappings.  The
    parser uses the raw projection for type/formula provenance and the semantic
    projection for value meaning; both include coordinates.
    """

    prefix = {
        'record_type': 'worksheet_dimensions',
        'dimension': dimension,
        'max_row': max_row,
        'max_column': max_column,
    }
    raw_digest = hashlib.sha256()
    semantic_digest = hashlib.sha256()
    prefix_json = canonical_json(prefix).encode('utf-8') + b'\n'
    raw_digest.update(prefix_json)
    semantic_digest.update(prefix_json)
    for record in records:
        if 'raw' not in record or 'semantic' not in record:
            raise ValueError('sheet hash record requires raw and semantic projections')
        raw_digest.update(canonical_json(record['raw']).encode('utf-8'))
        raw_digest.update(b'\n')
        semantic_digest.update(canonical_json(record['semantic']).encode('utf-8'))
        semantic_digest.update(b'\n')
    return raw_digest.hexdigest(), semantic_digest.hexdigest()


def build_snapshot_id(
    *,
    source_identity: str,
    source_sha256: str,
    parser_version: str,
    schema_version: str,
    sheet_fingerprints: Iterable[Mapping[str, Any]] = (),
) -> str:
    """Build a content-addressed candidate snapshot identity."""

    return canonical_semantic_hash(
        {
            'kind': 'workbook_reference_snapshot',
            'source_identity': source_identity,
            'source_sha256': source_sha256,
            'parser_version': parser_version,
            'schema_version': schema_version,
            'sheet_fingerprints': list(sheet_fingerprints),
        }
    )


def build_manifest_hash(snapshot: Any) -> str:
    """Hash the safe snapshot manifest returned by the domain model."""

    manifest = dict(snapshot.to_manifest_dict())
    # The digest is stored in the manifest itself; hash the canonical form with
    # that self-reference blank so recomputation is deterministic.
    manifest['manifest_sha256'] = ''
    return canonical_semantic_hash(manifest)


def build_reference_etag(revision: Any) -> str:
    """Build an optimistic-concurrency ETag from typed catalog content."""

    return canonical_semantic_hash(revision.etag_payload())


def build_reference_entry_hash(entry: Any) -> str:
    """Build a redaction-safe semantic hash for one catalog entry.

    Source coordinates are intentionally excluded: a moved source row is
    reported as provenance drift by the diff projection, while this digest
    remains the canonical hash of the typed reference meaning.  The payload is
    consumed only inside the store to calculate a digest and is never returned
    by the administration read boundary.
    """

    if hasattr(entry, 'to_dict'):
        values = dict(entry.to_dict())
    elif isinstance(entry, Mapping):
        values = dict(entry)
    else:
        raise TypeError('reference entry hash input must be a mapping or typed entry')
    return canonical_semantic_hash({
        'identity_key': values.get('identity_key'),
        'payload': values.get('payload'),
        'test_condition_ids': values.get('test_condition_ids', ()),
        'effective_from': values.get('effective_from'),
        'effective_to': values.get('effective_to'),
    })


__all__ = [
    'build_reference_entry_hash',
    'build_manifest_hash',
    'build_reference_etag',
    'build_snapshot_id',
    'canonical_json',
    'canonical_semantic_hash',
    'canonicalize',
    'hash_canonical_records',
    'hash_sheet_records',
]
