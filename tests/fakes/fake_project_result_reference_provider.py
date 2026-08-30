"""Independent fake provider for the generic reference contract tests."""
from __future__ import annotations

from typing import Any, Mapping

from domain.models.project_result_reference import canonical_payload_hash


class FakeRadiatedReferenceAdapter:
    """A second provider with a deliberately unrelated envelope vocabulary."""

    provider_id = 'fake-radiated'
    reference_type = 'fake.radiated-result'
    schema_version = 'fake.radiated-result.v1'

    def export(self, attempt: Mapping[str, Any]) -> dict[str, Any]:
        if str(attempt.get('status') or '').lower() != 'completed':
            raise ValueError('only completed attempts can become references')
        payload = attempt.get('result_json')
        if not isinstance(payload, Mapping):
            raise ValueError('fake radiated result must be an object')
        payload = dict(payload)
        return {
            'provider_id': self.provider_id,
            'reference_type': self.reference_type,
            'schema_version': self.schema_version,
            'condition_hash': str(attempt.get('condition_hash') or ''),
            'attempt_id': str(attempt.get('attempt_id') or attempt.get('id') or ''),
            'payload': payload,
            'content_sha256': canonical_payload_hash(payload),
        }

    def accepts(self, reference_type: str, schema_version: str) -> bool:
        return (reference_type, schema_version) == (
            self.reference_type,
            self.schema_version,
        )
