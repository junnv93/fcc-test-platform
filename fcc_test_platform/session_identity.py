"""Shared identity policy for provider-local sessions and result facts.

The central database receives provider-local integer/text identifiers from more
than one chamber.  ``provider_id`` alone is therefore not a sufficient scope:
two chambers may legitimately issue the same local session or result id.  This
module is the single application-level policy for normalizing the chamber scope
and deriving deterministic names for those identities.

The legacy sentinel is deliberately reserved for pre-multichamber rows and
callers that do not carry a chamber identity.  Its UUID name format remains the
pre-existing ``provider_id:local_session_id`` format so existing central rows
continue to converge after the migration.  New chamber-scoped rows use a
canonical JSON name, avoiding delimiter ambiguity without embedding provider
implementation details in the database schema.
"""
from __future__ import annotations

import json
import re
from typing import Any

from domain.services.central_session_identity import (
    measurement_target_key,
    provider_session_natural_key,
)


__all__ = [
    'CHAMBER_ID_MAX_LENGTH',
    'LEGACY_CHAMBER_ID',
    'measurement_target_key',  # re-export; defined in domain
    'normalize_chamber_id',
    'provider_result_identity',
    'provider_session_natural_key',  # re-export; defined in domain
    'session_uuid_name',
]


LEGACY_CHAMBER_ID = '__fcc_legacy__'
CHAMBER_ID_MAX_LENGTH = 256
_CONTROL_CHARACTERS = re.compile(r'[\x00-\x1f\x7f]')


def normalize_chamber_id(chamber_id: Any, *, allow_legacy: bool = True) -> str:
    """Return a validated chamber identity or the migration sentinel.

    Empty chamber values are only accepted for compatibility paths.  The HTTP
    result-ingestion contract validates a non-empty wire chamber before it
    reaches this function, so a missing value cannot silently become legacy at
    that security boundary.
    """
    value = str(chamber_id or '').strip()
    if not value:
        if allow_legacy:
            return LEGACY_CHAMBER_ID
        raise ValueError('chamber_id is required for chamber-scoped identity')
    if len(value) > CHAMBER_ID_MAX_LENGTH:
        raise ValueError('chamber_id exceeds the central identity length limit')
    if _CONTROL_CHARACTERS.search(value):
        raise ValueError('chamber_id contains a control character')
    if value == LEGACY_CHAMBER_ID and not allow_legacy:
        raise ValueError('legacy chamber identity is not valid for this boundary')
    return value


def session_uuid_name(
    provider_id: str,
    chamber_id: Any,
    local_session_id: int,
    target_identity: Any = '',
) -> str:
    """Return the deterministic UUIDv5 name for a central session.

    ``target_identity`` scopes the name to one measurement target (see
    ``measurement_target_key``).  When it is absent the name is byte-identical
    to what this function has always returned, so a deployment that never
    carried model/sample keeps its existing central identities.

    When it is present the name **changes** — that is the fix, not a regression.
    Preserving the legacy shape unconditionally would leave the collision intact
    precisely where it bites hardest, because callers without a chamber identity
    take the legacy branch.
    """
    provider = str(provider_id or '').strip()
    if not provider:
        raise ValueError('provider_id is required for session identity')
    local_id = int(local_session_id)
    chamber = normalize_chamber_id(chamber_id)
    target = str(target_identity or '').strip()
    if chamber == LEGACY_CHAMBER_ID and not target:
        # Preserve the original identity space for already-migrated legacy rows.
        return f'{provider}:{local_id}'
    payload = {
        'chamber_id': chamber,
        'local_session_id': local_id,
        'provider_id': provider,
    }
    if target:
        # Canonical JSON rather than a delimiter join: the target key and the
        # provider id are both free text, and this module already chose JSON to
        # avoid exactly that ambiguity.
        payload['measurement_target'] = target
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(',', ':'),
    )


def provider_result_identity(provider_result_id: Any, chamber_id: Any) -> str:
    """Scope an opaque provider result id to a chamber when present.

    Legacy result IDs remain byte-identical.  New IDs are canonical JSON so a
    chamber containing punctuation cannot collide with another chamber or with
    a delimiter-based legacy value.
    """
    result_id = str(provider_result_id or '').strip()
    if not result_id:
        raise ValueError('provider_result_id is required for result identity')
    chamber = normalize_chamber_id(chamber_id)
    if chamber == LEGACY_CHAMBER_ID:
        return result_id
    return json.dumps(
        {'chamber_id': chamber, 'provider_result_id': result_id},
        ensure_ascii=True,
        sort_keys=True,
        separators=(',', ':'),
    )
