"""Concrete in-memory ``CentralIdResolverPort`` (FE-P0c Phase B).

The Port contract (``CentralIdResolverPort``) and its loud-fail error
(``CentralIdResolutionError``) moved to ``domain/ports/output/central_id_resolver_port``
in FE-P0c WIRE (2026-05-26) — the hexagonal-correct home for a driven-port
abstraction, alongside its sibling ``central_backend_sync_port``. They are
re-exported here so existing callers
(``from fcc_test_platform.application.headless.central_id_resolver import CentralIdResolverPort``)
keep working unchanged.

This module owns the *concrete* in-memory resolver used by fakes/tests. The
production resolver (deterministic uuid5 + ``projects`` lookup) lives in
``application/headless/postgres_central_id_resolver``.
"""
from __future__ import annotations

from typing import Optional

from fcc_test_platform.domain.ports.output.central_id_resolver_port import (
    CentralIdResolutionError,
    CentralIdResolverPort,
    ModelProjectResolution,
)


__all__ = [
    'CentralIdResolutionError',
    'CentralIdResolverPort',
    'InMemoryCentralIdResolver',
    'ModelProjectResolution',
]


class InMemoryCentralIdResolver:
    """Concrete in-memory resolver — used by fakes/tests + ingestion worker
    composition root before a Postgres lookup adapter is introduced.

    Both mappings are populated by the composition root from the local DB
    (``test_sessions.id`` / ``test_sessions.project_id``) and the central
    discovery query (``SELECT id FROM projects WHERE project_code = ?``).
    """

    def __init__(
        self,
        *,
        session_uuid_by_local_id: Optional[dict[int, str]] = None,
        project_uuid_by_code: Optional[dict[str, str]] = None,
        project_uuid_by_model_number: Optional[dict[str, str]] = None,
        ambiguous_model_numbers: Optional[frozenset] = None,
    ) -> None:
        self._session_uuid = dict(session_uuid_by_local_id or {})
        self._project_uuid = dict(project_uuid_by_code or {})
        # Model → project is a *separate* mapping from code → project: a model
        # number is not a project code, and conflating them would let a test
        # pass with the wrong key shape.
        self._project_uuid_by_model = dict(project_uuid_by_model_number or {})
        self._ambiguous_models = frozenset(ambiguous_model_numbers or ())

    def register_session(
        self, local_session_id: int, central_session_uuid: str, *, chamber_id: str = '',
    ) -> None:
        if not central_session_uuid:
            raise CentralIdResolutionError(
                f'central_session_uuid is required (local={local_session_id})'
            )
        key = int(local_session_id)
        if chamber_id:
            self._session_uuid[(str(chamber_id), key)] = str(central_session_uuid)
        else:
            self._session_uuid[key] = str(central_session_uuid)

    def register_project(self, local_project_code: str, central_project_uuid: str) -> None:
        if not local_project_code or not central_project_uuid:
            raise CentralIdResolutionError(
                f'project mapping requires non-empty code+uuid '
                f'(code={local_project_code!r}, uuid={central_project_uuid!r})'
            )
        self._project_uuid[str(local_project_code)] = str(central_project_uuid)

    def resolve_session_uuid(
        self,
        local_session_id: int,
        *,
        chamber_id: Optional[str] = None,
        target_identity: Optional[str] = None,
    ) -> str:
        # This resolver serves pre-registered mappings (tests and the local
        # in-memory path); the target scope is part of the lookup key only when
        # a caller registered one, so existing registrations keep resolving.
        target = str(target_identity or '').strip()
        try:
            key = int(local_session_id)
            if target:
                scoped = self._session_uuid.get((str(chamber_id or ''), target, key))
                if scoped is not None:
                    return scoped
            if chamber_id:
                return self._session_uuid[(str(chamber_id), key)]
            return self._session_uuid[key]
        except (KeyError, TypeError, ValueError) as exc:
            raise CentralIdResolutionError(
                f'no central session uuid registered for chamber_id={chamber_id!r} '
                f'target_identity={target_identity!r} '
                f'local session_id={local_session_id!r}'
            ) from exc

    def resolve_project_uuid(self, local_project_id: Optional[str]) -> Optional[str]:
        if local_project_id is None or local_project_id == '':
            return None
        try:
            return self._project_uuid[str(local_project_id)]
        except KeyError as exc:
            raise CentralIdResolutionError(
                f'no central project uuid registered for project_code={local_project_id!r}'
            ) from exc

    def resolve_project_by_model_number(
        self, model_number: Optional[str]
    ) -> ModelProjectResolution:
        token = str(model_number or '').strip()
        if not token:
            return ModelProjectResolution(reason='model_number is empty')
        if token in self._ambiguous_models:
            return ModelProjectResolution(
                reason=f'model_number={token!r} maps to more than one central project'
            )
        resolved = self._project_uuid_by_model.get(token)
        if not resolved:
            return ModelProjectResolution(
                reason=f'model_number={token!r} is not registered centrally'
            )
        return ModelProjectResolution(project_uuid=str(resolved))
