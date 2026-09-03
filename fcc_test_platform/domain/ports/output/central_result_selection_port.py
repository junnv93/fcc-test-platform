"""Generic central port for project-result selection and reference writes.

The port deliberately exposes provider/result envelopes as mappings. Provider
taxonomy, formulas, and compatibility rules are owned by the producing or
consuming provider and never become part of the platform port.
"""
from __future__ import annotations

from typing import Any, Mapping, NotRequired, Optional, Protocol, TypedDict


class SelectedSource(TypedDict):
    """Authoritative selected-attempt envelope shared by all result consumers.

    The values are intentionally provider-neutral, but the provider identifier
    at this boundary is its public natural key.  The PostgreSQL adapter is the
    only place that resolves that key to the central UUID.  Optional fields are
    nullable database facts (for example a session may not have a sample or a
    provider session key); they are still present in the production row shape
    so a fake cannot accidentally hide a missing provenance join.
    """

    selection_event_id: str
    selection_action: str
    selection_revision: int
    attempt_id: str
    project_id: str
    provider_id: str
    condition_hash: str
    session_id: str
    status: str
    attempt_number: int
    result_json: Any
    provenance_json: NotRequired[Any]
    test_name: NotRequired[str]
    technology: NotRequired[str]
    operator: NotRequired[Optional[str]]
    measured_at: NotRequired[Any]
    created_at: NotRequired[Any]
    verdict: NotRequired[Optional[str]]
    margin: NotRequired[Optional[str]]
    run_id: NotRequired[Optional[str]]
    idempotency_key: NotRequired[Optional[str]]
    recorded_by: NotRequired[Optional[str]]
    provider_session_id: NotRequired[Optional[str]]
    sample_id: NotRequired[Optional[str]]
    chamber_id: NotRequired[Optional[str]]


__all__ = [
    'CentralResultSelectionError',
    'SelectionBackendError',
    'SelectionCandidateNotFoundError',
    'SelectionCrossScopeError',
    'SelectionProviderNotFoundError',
    'SelectionRevisionConflictError',
    'SelectedSource',
    'CentralResultSelectionPort',
]


class CentralResultSelectionError(RuntimeError):
    """Base error for the central result-selection boundary."""


class SelectionBackendError(CentralResultSelectionError):
    """The central store could not complete the requested operation."""


class SelectionCandidateNotFoundError(CentralResultSelectionError):
    """The requested attempt is not an eligible candidate in the partition."""


class SelectionCrossScopeError(CentralResultSelectionError):
    """The attempt exists, but under another project/provider/condition scope."""


class SelectionProviderNotFoundError(CentralResultSelectionError):
    """The requested natural provider key is unknown or disabled centrally."""


class SelectionRevisionConflictError(CentralResultSelectionError):
    """The append-only selection revision changed since the caller read it."""


class CentralResultSelectionPort(Protocol):
    """Application-facing persistence contract for the selection ledger."""

    def list_effective_results(
        self,
        project_id: str,
        provider_id: str,
        *,
        limit: int,
        cursor: Optional[str] = None,
    ) -> Mapping:
        """Return one effective result per exact provider condition partition."""

    def list_attempts(
        self,
        project_id: str,
        provider_id: str,
        condition_hash: str,
        *,
        limit: int,
        cursor: Optional[str] = None,
    ) -> Mapping:
        """Return eligible attempts in the central deterministic recency order."""

    def append_selection_event(
        self,
        *,
        project_id: str,
        provider_id: str,
        condition_hash: str,
        action: str,
        attempt_id: Optional[str],
        expected_revision: int,
        actor_subject: str,
        reason: Optional[str],
        event_id: str,
    ) -> Mapping:
        """Validate and append one selection/clear event atomically."""

    def selected_source(
        self,
        project_id: str,
        provider_id: str,
        condition_hash: str,
    ) -> Optional[SelectedSource]:
        """Return the effective completed source with full export provenance."""
