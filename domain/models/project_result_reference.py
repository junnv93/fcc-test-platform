"""Provider-neutral project-result reference identities and envelopes.

Payload and schema tokens are intentionally opaque. This module contains no
provider taxonomy and no result interpretation; provider adapters validate and
produce the payload before it crosses the platform boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Optional


__all__ = [
    'REFERENCE_SESSION_SNAPSHOT_SCHEMA_VERSION',
    'REFERENCE_SESSION_SNAPSHOT_MAX_BYTES',
    'ProjectResultReferenceIdentity',
    'ProjectResultReferenceEnvelope',
    'build_reference_session_snapshot_json',
    'validate_reference_session_snapshot_json',
    'canonical_payload_hash',
]


# This is a transport envelope, not a provider payload schema.  Keeping the
# token in the provider-neutral domain module lets Platform and Session share
# the exact boundary contract without either layer importing the other.
REFERENCE_SESSION_SNAPSHOT_SCHEMA_VERSION = 'fcc.project-result-reference-session.v1'
REFERENCE_SESSION_SNAPSHOT_MAX_BYTES = 256 * 1024

_SNAPSHOT_PIN_FIELDS = (
    'revision_id',
    'revision_number',
    'producer_provider_id',
    'reference_type',
    'schema_version',
    'source_selection_event_id',
    'source_attempt_id',
    'source_session_id',
    'content_sha256',
)


def _snapshot_pin(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project one central revision row onto the provider-free pin shape."""
    if not isinstance(row, Mapping):
        raise ValueError('reference snapshot pin must be an object')
    revision_id = str(row.get('revision_id') or row.get('id') or '').strip()
    producer_provider_id = str(row.get('producer_provider_id') or '').strip()
    reference_type = str(row.get('reference_type') or '').strip()
    schema_version = str(row.get('schema_version') or '').strip()
    source_selection_event_id = str(
        row.get('source_selection_event_id') or row.get('selection_event_id') or ''
    ).strip()
    source_attempt_id = str(row.get('source_attempt_id') or row.get('attempt_id') or '').strip()
    source_session_id = str(row.get('source_session_id') or row.get('session_id') or '').strip()
    content_sha256 = str(row.get('content_sha256') or '').lower().strip()
    try:
        revision_number = int(row.get('revision_number'))
    except (TypeError, ValueError) as exc:
        raise ValueError('reference snapshot revision_number must be positive') from exc
    if revision_number < 1:
        raise ValueError('reference snapshot revision_number must be positive')
    for name, value in (
        ('revision_id', revision_id),
        ('producer_provider_id', producer_provider_id),
        ('reference_type', reference_type),
        ('schema_version', schema_version),
        ('source_selection_event_id', source_selection_event_id),
        ('source_attempt_id', source_attempt_id),
        ('source_session_id', source_session_id),
    ):
        if not value:
            raise ValueError(f'reference snapshot {name} is required')
    if len(content_sha256) != 64 or any(c not in '0123456789abcdef' for c in content_sha256):
        raise ValueError('reference snapshot content_sha256 must be a SHA-256 hex digest')
    return {
        'revision_id': revision_id,
        'revision_number': revision_number,
        'producer_provider_id': producer_provider_id,
        'reference_type': reference_type,
        'schema_version': schema_version,
        'source_selection_event_id': source_selection_event_id,
        'source_attempt_id': source_attempt_id,
        'source_session_id': source_session_id,
        'content_sha256': content_sha256,
    }


def _snapshot_document(project_id: str, references: list[Mapping[str, Any]]) -> dict[str, Any]:
    project = str(project_id or '').strip()
    if not project:
        raise ValueError('reference snapshot project_id is required')
    pins = [_snapshot_pin(row) for row in references]
    if len({pin['revision_id'] for pin in pins}) != len(pins):
        raise ValueError('reference snapshot revision ids must be unique')
    # Revision number is the stable catalog order; the id tie-break keeps the
    # bytes deterministic even for malformed/imported ledgers with equal
    # revision numbers.
    pins.sort(key=lambda pin: (pin['revision_number'], pin['revision_id']))
    return {
        'schema_version': REFERENCE_SESSION_SNAPSHOT_SCHEMA_VERSION,
        'project_id': project,
        'references': pins,
    }


def build_reference_session_snapshot_json(
    project_id: str, references: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> str:
    """Canonicalize the Platform→Session reference snapshot exactly once."""
    document = _snapshot_document(project_id, list(references))
    encoded = json.dumps(
        document, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
    )
    if len(encoded.encode('utf-8')) > REFERENCE_SESSION_SNAPSHOT_MAX_BYTES:
        raise ValueError('reference snapshot exceeds the maximum size')
    return encoded


def validate_reference_session_snapshot_json(
    value: str, *, project_id: Optional[str] = None,
    schema_version: Optional[str] = None,
) -> str:
    """Validate canonical snapshot bytes without rewriting them."""
    if not isinstance(value, str) or not value:
        raise ValueError('reference snapshot JSON is required')
    if len(value.encode('utf-8')) > REFERENCE_SESSION_SNAPSHOT_MAX_BYTES:
        raise ValueError('reference snapshot exceeds the maximum size')
    try:
        document = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError('reference snapshot must be valid JSON') from exc
    if not isinstance(document, Mapping):
        raise ValueError('reference snapshot must be an object')
    if document.get('schema_version') != REFERENCE_SESSION_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError('unsupported reference snapshot schema version')
    if schema_version is not None and schema_version != document.get('schema_version'):
        raise ValueError('reference snapshot schema version mismatch')
    if project_id is not None and str(document.get('project_id') or '') != str(project_id):
        raise ValueError('reference snapshot project identity mismatch')
    raw_references = document.get('references')
    if not isinstance(raw_references, list):
        raise ValueError('reference snapshot references must be a list')
    canonical = build_reference_session_snapshot_json(
        str(document.get('project_id') or ''), raw_references,
    )
    if canonical != value:
        raise ValueError('reference snapshot JSON is not canonical')
    return value


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    """Hash one provider-authored JSON object without interpreting it."""
    if not isinstance(payload, Mapping):
        raise ValueError('payload must be an object')
    encoded = json.dumps(
        payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ProjectResultReferenceIdentity:
    project_id: str
    producer_provider_id: str
    reference_type: str
    schema_version: str
    revision_number: int

    def __post_init__(self) -> None:
        for name in (
            'project_id', 'producer_provider_id', 'reference_type', 'schema_version',
        ):
            if not str(getattr(self, name) or '').strip():
                raise ValueError(f'{name} is required')
        if self.revision_number < 1:
            raise ValueError('revision_number must be positive')

    def to_dict(self) -> dict[str, Any]:
        return {
            'project_id': self.project_id,
            'producer_provider_id': self.producer_provider_id,
            'reference_type': self.reference_type,
            'schema_version': self.schema_version,
            'revision_number': self.revision_number,
        }


@dataclass(frozen=True)
class ProjectResultReferenceEnvelope:
    revision_id: str
    identity: ProjectResultReferenceIdentity
    source_selection_event_id: str
    source_attempt_id: str
    source_session_id: str
    source_sample_id: Optional[str]
    source_chamber_id: Optional[str]
    payload: Mapping[str, Any]
    content_sha256: str
    state: str
    created_by: str
    created_at: str
    retired_by: Optional[str] = None
    retired_at: Optional[str] = None
    retirement_reason: Optional[str] = None

    def __post_init__(self) -> None:
        for name in (
            'revision_id', 'source_selection_event_id', 'source_attempt_id',
            'source_session_id', 'content_sha256', 'state', 'created_by', 'created_at',
        ):
            if not str(getattr(self, name) or '').strip():
                raise ValueError(f'{name} is required')
        if self.state not in {'published', 'retired'}:
            raise ValueError('state must be published or retired')
        if not isinstance(self.payload, Mapping):
            raise ValueError('payload must be an object')

    def to_dict(self) -> dict[str, Any]:
        return {
            'revision_id': self.revision_id,
            **self.identity.to_dict(),
            'source_selection_event_id': self.source_selection_event_id,
            'source_attempt_id': self.source_attempt_id,
            'source_session_id': self.source_session_id,
            'source_sample_id': self.source_sample_id,
            'source_chamber_id': self.source_chamber_id,
            'payload': dict(self.payload),
            'content_sha256': self.content_sha256,
            'state': self.state,
            'created_by': self.created_by,
            'created_at': self.created_at,
            'retired_by': self.retired_by,
            'retired_at': self.retired_at,
            'retirement_reason': self.retirement_reason,
        }
