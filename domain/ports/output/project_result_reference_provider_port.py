"""Provider-owned boundary for exporting a selected result reference.

The platform asks a provider adapter to turn one trusted, completed attempt
into an opaque reference envelope.  The platform may validate envelope shape
and integrity, but it does not know the provider's result vocabulary or
recompute a provider result.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol


__all__ = [
    'ProjectResultReferenceProviderError',
    'ProjectResultReferenceProviderNotFoundError',
    'ProjectResultReferenceProviderIncompatibleError',
    'ProjectResultReferenceProviderPort',
]


class ProjectResultReferenceProviderError(RuntimeError):
    """Base error for provider-owned reference export."""


class ProjectResultReferenceProviderNotFoundError(
    ProjectResultReferenceProviderError
):
    """No provider adapter is registered for the requested natural key."""


class ProjectResultReferenceProviderIncompatibleError(
    ProjectResultReferenceProviderError
):
    """The provider cannot consume or export the requested reference shape."""


class ProjectResultReferenceProviderPort(Protocol):
    """Small provider adapter contract used by the platform application."""

    provider_id: str

    def export(self, attempt: Mapping[str, Any]) -> Mapping[str, Any]:
        """Export an opaque provider reference from a trusted attempt."""
        ...

    def accepts(self, reference_type: str, schema_version: str) -> bool:
        """Return whether this provider owns the exact type/schema pair."""
        ...
