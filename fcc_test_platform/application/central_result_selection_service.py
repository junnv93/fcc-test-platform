"""Provider-neutral project result selection application service."""
from __future__ import annotations

from typing import Callable, Mapping, Optional
from uuid import uuid4

from domain.ports.output.central_result_selection_port import (
    CentralResultSelectionPort,
    SelectionCandidateNotFoundError,
    SelectionCrossScopeError,
    SelectionRevisionConflictError,
)


__all__ = ['CentralResultSelectionService']


_MAX_REASON_LENGTH = 500


class CentralResultSelectionService:
    """Use the central ledger without importing provider taxonomy.

    The service owns boundary validation and trusted audit values. The adapter
    owns the serializable transaction and source-chain checks, so a caller can
    never implement a second CAS path in an API handler.
    """

    def __init__(
        self,
        port: CentralResultSelectionPort,
        *,
        event_id_factory: Optional[Callable[[], str]] = None,
    ) -> None:
        self._port = port
        self._event_id_factory = event_id_factory or (lambda: str(uuid4()))

    def list_effective_results(
        self,
        project_id: str,
        provider_id: str,
        *,
        limit: int,
        cursor: Optional[str] = None,
    ) -> Mapping:
        self._require_identity(project_id, provider_id)
        self._require_page_limit(limit)
        return self._port.list_effective_results(
            project_id, provider_id, limit=limit, cursor=cursor,
        )

    def list_attempts(
        self,
        project_id: str,
        provider_id: str,
        condition_hash: str,
        *,
        limit: int,
        cursor: Optional[str] = None,
    ) -> Mapping:
        self._require_identity(project_id, provider_id)
        if not str(condition_hash or '').strip():
            raise ValueError('condition_hash is required')
        self._require_page_limit(limit)
        return self._port.list_attempts(
            project_id, provider_id, condition_hash,
            limit=limit, cursor=cursor,
        )

    def select(
        self,
        project_id: str,
        provider_id: str,
        condition_hash: str,
        *,
        attempt_id: str,
        expected_revision: int,
        actor_subject: str,
        reason: Optional[str] = None,
    ) -> Mapping:
        return self._append(
            project_id, provider_id, condition_hash,
            action='selected', attempt_id=attempt_id,
            expected_revision=expected_revision, actor_subject=actor_subject,
            reason=reason,
        )

    def clear(
        self,
        project_id: str,
        provider_id: str,
        condition_hash: str,
        *,
        expected_revision: int,
        actor_subject: str,
        reason: Optional[str] = None,
    ) -> Mapping:
        return self._append(
            project_id, provider_id, condition_hash,
            action='cleared', attempt_id=None,
            expected_revision=expected_revision, actor_subject=actor_subject,
            reason=reason,
        )

    def _append(
        self,
        project_id: str,
        provider_id: str,
        condition_hash: str,
        *,
        action: str,
        attempt_id: Optional[str],
        expected_revision: int,
        actor_subject: str,
        reason: Optional[str],
    ) -> Mapping:
        self._require_identity(project_id, provider_id)
        if not str(condition_hash or '').strip():
            raise ValueError('condition_hash is required')
        if action not in {'selected', 'cleared'}:
            raise ValueError('action must be selected or cleared')
        if action == 'selected' and not str(attempt_id or '').strip():
            raise ValueError('attempt_id is required for selected')
        if action == 'cleared' and attempt_id is not None:
            raise ValueError('attempt_id is forbidden for cleared')
        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise ValueError('expected_revision must be a non-negative integer')
        actor = str(actor_subject or '').strip()
        if not actor or actor == 'anonymous':
            raise ValueError('authenticated actor is required')
        bounded_reason = None if reason is None else str(reason).strip()
        if bounded_reason and len(bounded_reason) > _MAX_REASON_LENGTH:
            raise ValueError('reason exceeds maximum length')
        return self._port.append_selection_event(
            project_id=project_id,
            provider_id=provider_id,
            condition_hash=condition_hash,
            action=action,
            attempt_id=attempt_id,
            expected_revision=expected_revision,
            actor_subject=actor,
            reason=bounded_reason or None,
            event_id=self._event_id_factory(),
        )

    @staticmethod
    def _require_identity(project_id: str, provider_id: str) -> None:
        if not str(project_id or '').strip():
            raise ValueError('project_id is required')
        if not str(provider_id or '').strip():
            raise ValueError('provider_id is required')

    @staticmethod
    def _require_page_limit(limit: int) -> None:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError('limit must be between 1 and 1000')
