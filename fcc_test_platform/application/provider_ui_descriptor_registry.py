"""Platform provider UI descriptor registry (WEB-PROVIDER-UI-0, 2026-05-29).

Platform-owned, dependency-light. The platform proxy serves provider UI
descriptors out of this registry — a deterministic ``provider_id -> descriptor``
mapping wired at the composition root. The platform service/route layer depends
ONLY on this registry, never on provider internals (measurements / workflows) or
the provider descriptor *builder*; the composition root is the single place that
imports ``build_unlicensed_ui_descriptor`` and registers its output here. This
keeps the platform a schema-driven renderer (repo-split ADR boundary).

stdlib-only (``copy`` / ``typing`` / ``Mapping``) — no FastAPI / Pydantic / SQL / psycopg.
"""
from __future__ import annotations

import copy
from typing import Mapping


__all__ = [
    'ProviderUiDescriptorNotFound',
    'ProviderUiDescriptorRegistry',
]


class ProviderUiDescriptorNotFound(Exception):
    """Raised when no descriptor is registered for a provider_id (→ HTTP 404)."""


class ProviderUiDescriptorRegistry:
    """Read-only ``provider_id -> descriptor dict`` lookup.

    Descriptors are plain dicts (the provider builder's ``to_dict()`` output) so
    the registry stays decoupled from the provider DTO classes. Both ingest and
    ``get`` take a **deep** defensive copy (``copy.deepcopy``) — a shallow
    ``dict(...)`` would share nested ``features`` / ``*_tables`` list/dict objects,
    so a caller (or the source mapping owner) could mutate the registered
    descriptor through those nested references. Deep copy on ingest isolates the
    registry from later mutation of the source mapping; deep copy on ``get``
    isolates the stored descriptor from the consumer.
    """

    def __init__(self, descriptors: Mapping[str, dict]) -> None:
        # Deterministic ordering SSOT: sort by ``provider_id`` once at
        # construction so ``provider_ids()`` and ``summaries()`` present a stable
        # list that does NOT depend on however the composition root happened to
        # insert entries (a picker's option order must be reproducible across
        # process restarts and provider-registration reorderings). Both accessors
        # iterate this same ordered dict, so they can never drift apart.
        self._descriptors: dict[str, dict] = {
            str(provider_id): copy.deepcopy(dict(descriptor))
            for provider_id, descriptor in sorted(
                descriptors.items(), key=lambda item: str(item[0])
            )
        }

    def get(self, provider_id: str) -> dict:
        try:
            return copy.deepcopy(self._descriptors[provider_id])
        except KeyError as exc:
            raise ProviderUiDescriptorNotFound(
                f'no UI descriptor registered for provider {provider_id!r}'
            ) from exc

    def provider_ids(self) -> tuple[str, ...]:
        """Registered provider ids, sorted by ``provider_id`` (deterministic —
        matches ``summaries`` ordering)."""
        return tuple(self._descriptors)

    def summaries(self) -> list[dict]:
        """Projection for the provider list endpoint — the identity/label fields a
        picker needs (``provider_id`` / ``display_name`` / ``ui_version``), never
        the full descriptor payload. Deterministic order — sorted by
        ``provider_id`` at construction (matches ``provider_ids``), so an empty
        registry yields ``[]`` and a multi-provider registry always lists in the
        same reproducible order. ``display_name`` falls back to the id so a
        malformed descriptor still yields a selectable, non-empty label;
        ``ui_version`` falls back to ``0``."""
        return [
            {
                'provider_id': provider_id,
                'display_name': str(descriptor.get('display_name') or provider_id),
                'ui_version': int(descriptor.get('ui_version') or 0),
            }
            for provider_id, descriptor in self._descriptors.items()
        ]
