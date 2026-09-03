"""Provider-owned project-result reference adapters.

The central platform stores only the opaque envelope produced here.  This
module is deliberately below the platform/application boundary: Duty/DCCF
meaning and the compatibility vocabulary belong to the unlicensed provider,
while a second provider can implement the same small protocol independently.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from fcc_test_kernel.domain.models.project_result_reference import canonical_payload_hash

__all__ = [
    'CONDUCTED_DUTY_REFERENCE_TYPE',
    'CONDUCTED_DUTY_SCHEMA_VERSION',
    'ConductedDutyReferenceAdapter',
]


# Provider-owned tokens.  They must not be imported by the platform package.
CONDUCTED_DUTY_REFERENCE_TYPE = 'unlicensed.conducted-duty'
CONDUCTED_DUTY_SCHEMA_VERSION = 'fcc.unlicensed.conducted-duty.v1'


class ConductedDutyReferenceAdapter:
    """Export a completed Conducted Duty attempt without recalculating it."""

    # This is the provider registry's natural key, not a central UUID and not
    # the internal product shorthand used by older measurement callers.
    provider_id = 'fcc-unlicensed-conducted'
    reference_type = CONDUCTED_DUTY_REFERENCE_TYPE
    schema_version = CONDUCTED_DUTY_SCHEMA_VERSION

    def export(self, attempt: Mapping[str, Any]) -> dict[str, Any]:
        status = str(attempt.get('status') or '').lower()
        if status != 'completed':
            raise ValueError('only completed attempts can become references')
        payload = attempt.get('result_json')
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError) as exc:
                raise ValueError('attempt result_json is not valid JSON') from exc
        if not isinstance(payload, Mapping):
            raise ValueError('attempt result_json must be an object')
        # The adapter preserves provider bytes/fields; it does not derive a
        # replacement DCCF or infer a result from another condition.
        if 'dccf' not in payload and 'DCCF' not in payload:
            raise ValueError('completed Duty result has no DCCF value')
        return {
            'provider_id': self.provider_id,
            'reference_type': self.reference_type,
            'schema_version': self.schema_version,
            'condition_hash': str(attempt.get('condition_hash') or ''),
            'attempt_id': str(attempt.get('attempt_id') or attempt.get('id') or ''),
            'payload': dict(payload),
            'content_sha256': canonical_payload_hash(payload),
        }

    def accepts(self, reference_type: str, schema_version: str) -> bool:
        return (reference_type, schema_version) == (
            self.reference_type, self.schema_version,
        )
