"""Provider-neutral immutable project-result reference service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Mapping, Optional, Sequence, Union
from uuid import uuid4

from domain.models.project_result_reference import (
    REFERENCE_SESSION_SNAPSHOT_SCHEMA_VERSION,
    build_reference_session_snapshot_json,
    canonical_payload_hash,
)
from domain.ports.output.central_result_selection_port import (
    CentralResultSelectionPort,
)
from domain.ports.output.central_project_reference_port import (
    CentralProjectReferencePort,
    ReferenceHashMismatchError,
    ReferenceIncompatibleError,
    ReferenceNotFoundError,
    ReferenceScopeMismatchError,
    ReferenceSourceMismatchError,
)
from domain.ports.output.project_result_reference_provider_port import (
    ProjectResultReferenceProviderError,
    ProjectResultReferenceProviderPort,
)


ProviderResolver = Union[
    Mapping[str, ProjectResultReferenceProviderPort],
    Callable[[str], Optional[ProjectResultReferenceProviderPort]],
]


__all__ = [
    'CentralProjectReferenceService',
    'ProviderResolver',
    'canonical_payload_hash',
]


class CentralProjectReferenceService:
    """Publish opaque references from a server-resolved selected attempt.

    The HTTP request carries only the provider natural key, condition hash and
    an optional bounded reason.  This service resolves the current selection
    through the central selection port, then delegates result interpretation to
    an injected provider adapter.  No provider implementation is imported by
    the platform application.
    """

    def __init__(
        self,
        port: CentralProjectReferencePort,
        *,
        selection_port: Optional[CentralResultSelectionPort] = None,
        provider_resolver: Optional[ProviderResolver] = None,
        clock: Optional[Callable[[], datetime]] = None,
        revision_id_factory: Optional[Callable[[], str]] = None,
    ) -> None:
        self._port = port
        self._selection_port = selection_port
        self._provider_resolver = provider_resolver
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._revision_id_factory = revision_id_factory or (lambda: str(uuid4()))

    def list_references(
        self,
        project_id: str,
        *,
        producer_provider_id: Optional[str] = None,
        state: Optional[str] = None,
        limit: int,
        cursor: Optional[str] = None,
    ) -> Mapping:
        self._require_project(project_id)
        if not 1 <= limit <= 1000:
            raise ValueError('limit must be between 1 and 1000')
        if state is not None and state not in {'published', 'retired'}:
            raise ValueError('state must be published or retired')
        return self._port.list_references(
            project_id,
            producer_provider_id=producer_provider_id,
            state=state,
            limit=limit,
            cursor=cursor,
        )

    def publish(
        self,
        *,
        project_id: str,
        provider_id: str,
        condition_hash: str,
        actor_subject: str,
        reason: Optional[str] = None,
    ) -> Mapping:
        self._require_project(project_id)
        for name, value in (
            ('provider_id', provider_id),
            ('condition_hash', condition_hash),
            ('actor_subject', actor_subject),
        ):
            if not str(value or '').strip() or str(value).strip() == 'anonymous':
                raise ValueError(f'{name} is required')
        bounded_reason = None if reason is None else str(reason).strip()
        if bounded_reason is not None and len(bounded_reason) > 500:
            raise ValueError('reason must be at most 500 characters')

        if self._selection_port is None:
            raise ReferenceSourceMismatchError(
                'selected source resolver is not configured'
            )
        source = self._selection_port.selected_source(
            project_id, provider_id, condition_hash
        )
        if source is None:
            raise ReferenceNotFoundError(
                'no current selected result exists for the requested scope'
            )
        self._require_source_scope(
            source, project_id=project_id, provider_id=provider_id,
            condition_hash=condition_hash,
        )
        provider = self._resolve_provider(provider_id)
        try:
            envelope = provider.export(source)
        except (ProjectResultReferenceProviderError, ValueError) as exc:
            raise ReferenceIncompatibleError(
                f'provider could not export a compatible reference: {exc}'
            ) from exc
        if not isinstance(envelope, Mapping):
            raise ReferenceIncompatibleError('provider reference envelope must be an object')

        reference_type = str(envelope.get('reference_type') or '').strip()
        schema_version = str(envelope.get('schema_version') or '').strip()
        if not reference_type or not schema_version:
            raise ReferenceIncompatibleError(
                'provider reference type and schema version are required'
            )
        if not provider.accepts(reference_type, schema_version):
            raise ReferenceIncompatibleError(
                'provider rejected its exported reference type/schema'
            )
        if str(envelope.get('provider_id') or '').strip() != str(provider_id).strip():
            raise ReferenceIncompatibleError(
                'provider envelope identity does not match the requested provider'
            )

        payload = envelope.get('payload')
        # ``result`` is accepted only as an adapter compatibility bridge for
        # older provider implementations; it is never read from the request.
        if payload is None:
            payload = envelope.get('result')
        if not isinstance(payload, Mapping):
            raise ReferenceIncompatibleError('provider reference payload must be an object')
        payload = dict(payload)
        supplied_hash = str(envelope.get('content_sha256') or '').lower().strip()
        if len(supplied_hash) != 64 or any(
            character not in '0123456789abcdef' for character in supplied_hash
        ):
            raise ReferenceIncompatibleError('provider reference hash is not sha256')
        if canonical_payload_hash(payload) != supplied_hash:
            raise ReferenceHashMismatchError(
                'provider reference hash does not match its payload'
            )

        exported_attempt_id = envelope.get('attempt_id')
        if exported_attempt_id is not None and str(exported_attempt_id) != str(
            source['attempt_id']
        ):
            raise ReferenceScopeMismatchError(
                'provider reference attempt is outside the selected source'
            )
        return self._port.publish_reference({
            'id': self._revision_id_factory(),
            'project_id': project_id,
            'producer_provider_id': str(provider_id).strip(),
            'reference_type': reference_type,
            'schema_version': schema_version,
            'condition_hash': condition_hash,
            'source_selection_event_id': source['selection_event_id'],
            'source_attempt_id': source['attempt_id'],
            'source_session_id': source['session_id'],
            'source_sample_id': source.get('sample_id'),
            'source_chamber_id': source.get('chamber_id'),
            'payload_json': payload,
            'content_sha256': supplied_hash,
            'state': 'published',
            'created_by': str(actor_subject).strip(),
            'created_at': self._clock().astimezone(timezone.utc).isoformat(),
            # The current central table has no publication-reason column. Keep
            # the bounded value at the application/port boundary so a future
            # audit sink can persist it without allowing the browser to author
            # provenance or payload fields.
            'publication_reason': bounded_reason,
        })

    def retire(
        self,
        revision_id: str,
        *,
        actor_subject: str,
        reason: str,
        occurred_at: Optional[str] = None,
    ) -> Mapping:
        if not str(revision_id or '').strip():
            raise ValueError('revision_id is required')
        if not str(actor_subject or '').strip() or actor_subject == 'anonymous':
            raise ValueError('authenticated actor is required')
        bounded_reason = str(reason or '').strip()
        if not bounded_reason or len(bounded_reason) > 500:
            raise ValueError('reason is required and must be at most 500 characters')
        return self._port.retire_reference(
            revision_id=revision_id,
            actor_subject=str(actor_subject).strip(),
            occurred_at=occurred_at or self._clock().astimezone(timezone.utc).isoformat(),
            reason=bounded_reason,
        )

    def resolve(
        self,
        *,
        project_id: str,
        consumer_provider_id: str,
        revision_id: str,
        reference_type: str,
        schema_version: str,
    ) -> Mapping:
        self._require_project(project_id)
        if not consumer_provider_id or not reference_type or not schema_version:
            raise ValueError('consumer/provider reference identity is required')
        if self._provider_resolver is not None:
            provider = self._resolve_provider(consumer_provider_id)
            if not provider.accepts(reference_type, schema_version):
                raise ReferenceIncompatibleError(
                    'consumer provider does not accept the reference type/schema'
                )
        return self._port.resolve_reference(
            project_id=project_id,
            consumer_provider_id=consumer_provider_id,
            revision_id=revision_id,
            reference_type=reference_type,
            schema_version=schema_version,
        )

    def build_session_reference_snapshot(
        self,
        *,
        project_id: str,
        consumer_provider_id: str,
        requests: Sequence[Mapping],
    ) -> tuple[str, str]:
        """Resolve published revisions and return an opaque canonical snapshot.

        The request contains only revision identity (id/type/schema).  Hashes,
        source ids, producer ids, and payload are read from the central
        revision and projected into the session envelope here.  The resulting
        JSON is the one canonical byte string that the chamber/session path
        transports; no downstream layer performs another JSON normalization.
        """
        self._require_project(project_id)
        if not str(consumer_provider_id or '').strip():
            raise ValueError('consumer_provider_id is required')
        if not isinstance(requests, Sequence) or isinstance(requests, (str, bytes)):
            raise ValueError('reference requests must be a list')
        records: list[Mapping] = []
        forbidden = {
            'content_sha256', 'source_selection_event_id', 'source_attempt_id',
            'source_session_id', 'producer_provider_id', 'payload', 'payload_json',
        }
        for request in requests:
            if not isinstance(request, Mapping):
                raise ValueError('reference request must be an object')
            if forbidden.intersection(request):
                raise ValueError('reference request contains server-owned provenance')
            revision_id = str(request.get('revision_id') or '').strip()
            reference_type = str(request.get('reference_type') or '').strip()
            schema_version = str(request.get('schema_version') or '').strip()
            if not revision_id or not reference_type or not schema_version:
                raise ValueError('reference request requires revision_id, reference_type, schema_version')
            resolved = self.resolve(
                project_id=project_id,
                consumer_provider_id=consumer_provider_id,
                revision_id=revision_id,
                reference_type=reference_type,
                schema_version=schema_version,
            )
            records.append({
                'revision_id': resolved.get('revision_id') or resolved.get('id'),
                'revision_number': resolved.get('revision_number'),
                'producer_provider_id': resolved.get('producer_provider_id'),
                'reference_type': resolved.get('reference_type'),
                'schema_version': resolved.get('schema_version'),
                'source_selection_event_id': resolved.get('source_selection_event_id'),
                'source_attempt_id': resolved.get('source_attempt_id'),
                'source_session_id': resolved.get('source_session_id'),
                'content_sha256': resolved.get('content_sha256'),
            })
        snapshot = build_reference_session_snapshot_json(project_id, records)
        return snapshot, REFERENCE_SESSION_SNAPSHOT_SCHEMA_VERSION

    @staticmethod
    def _require_project(project_id: str) -> None:
        if not str(project_id or '').strip():
            raise ValueError('project_id is required')

    def _resolve_provider(self, provider_id: str) -> ProjectResultReferenceProviderPort:
        resolver = self._provider_resolver
        provider = None
        if isinstance(resolver, Mapping):
            provider = resolver.get(provider_id)
        elif resolver is not None:
            provider = resolver(provider_id)
        if provider is None:
            raise ReferenceIncompatibleError(
                f'no reference provider adapter is registered for {provider_id!r}'
            )
        if str(getattr(provider, 'provider_id', '')).strip() != str(provider_id).strip():
            raise ReferenceIncompatibleError(
                'reference provider adapter identity does not match the request'
            )
        return provider

    @staticmethod
    def _require_source_scope(
        source: Mapping,
        *,
        project_id: str,
        provider_id: str,
        condition_hash: str,
    ) -> None:
        # Keep this validation at the application boundary as a second line of
        # defence for fakes and alternate adapters.  PostgreSQL supplies this
        # exact row from the event→attempt→session→provider join; an event-only
        # four-column mapping is not a publishable source.
        required = (
            'selection_event_id', 'selection_action', 'selection_revision',
            'attempt_id', 'project_id', 'provider_id', 'condition_hash',
            'session_id', 'status', 'attempt_number', 'result_json',
        )
        if any(not str(source.get(field) or '').strip() for field in required):
            raise ReferenceSourceMismatchError(
                'selected source is missing event, attempt, session, or result provenance'
            )
        if source.get('selection_action') != 'selected':
            raise ReferenceSourceMismatchError('selected source event is not selected')
        if str(source.get('status') or '').lower() != 'completed':
            raise ReferenceSourceMismatchError('selected source attempt is not completed')
        try:
            if int(source['selection_revision']) < 1 or int(source['attempt_number']) < 1:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise ReferenceSourceMismatchError(
                'selected source event/attempt revision is invalid'
            ) from exc
        if str(source.get('project_id')) != str(project_id):
            raise ReferenceScopeMismatchError('selected source project does not match')
        if str(source.get('provider_id')) != str(provider_id):
            raise ReferenceScopeMismatchError('selected source provider does not match')
        if str(source.get('condition_hash')) != str(condition_hash):
            raise ReferenceScopeMismatchError('selected source condition does not match')
