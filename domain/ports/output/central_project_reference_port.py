"""Provider-neutral port for immutable project-result reference revisions."""
from __future__ import annotations

from typing import Mapping, Optional, Protocol


__all__ = [
    'CentralProjectReferenceError',
    'ReferenceNotFoundError',
    'ReferenceRetiredError',
    'ReferenceIncompatibleError',
    'ReferenceHashMismatchError',
    'ReferenceSourceMismatchError',
    'ReferenceScopeMismatchError',
    'CentralProjectReferencePort',
]


class CentralProjectReferenceError(RuntimeError):
    """Base error for the generic reference lifecycle boundary."""


class ReferenceNotFoundError(CentralProjectReferenceError):
    """The requested revision or source event does not exist."""


class ReferenceRetiredError(CentralProjectReferenceError):
    """The revision is not available for new use."""


class ReferenceIncompatibleError(CentralProjectReferenceError):
    """Exact provider-declared type/schema compatibility failed."""


class ReferenceHashMismatchError(ReferenceIncompatibleError):
    """The provider envelope hash does not match its opaque payload."""


class ReferenceSourceMismatchError(CentralProjectReferenceError):
    """Reference source identity does not describe one central source chain."""


class ReferenceScopeMismatchError(ReferenceSourceMismatchError):
    """The selected attempt is outside the requested project/provider/condition."""


class CentralProjectReferencePort(Protocol):
    """Persistence contract for generic project-result revisions."""

    def list_references(
        self,
        project_id: str,
        *,
        producer_provider_id: Optional[str] = None,
        state: Optional[str] = None,
        limit: int,
        cursor: Optional[str] = None,
    ) -> Mapping:
        ...

    def publish_reference(self, record: Mapping) -> Mapping:
        """Append one published immutable revision after source validation."""
        ...

    def retire_reference(
        self,
        *,
        revision_id: str,
        actor_subject: str,
        occurred_at: str,
        reason: str,
    ) -> Mapping:
        """Append a retired lifecycle revision; never delete or mutate history."""
        ...

    def resolve_reference(
        self,
        *,
        project_id: str,
        consumer_provider_id: str,
        revision_id: str,
        reference_type: str,
        schema_version: str,
    ) -> Mapping:
        """Resolve one non-retired revision after exact token checks."""
        ...
