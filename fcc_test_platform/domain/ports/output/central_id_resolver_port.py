"""Output port — local int/text identifier → central uuid resolution.

Local SQLite uses integer primary keys for sessions and text codes for projects;
central PostgreSQL uses uuid for both. This is the dependency-free contract for
that translation — concrete adapters (in-memory fake, production Postgres
lookup + deterministic uuid5) implement the Port.

The resolver is invoked at the boundary where outbox payloads (local ints) are
converted into ingestion envelopes (central uuids). Resolution failures surface
as ``CentralIdResolutionError`` (a loud ``ValueError``) so silent uuid-from-int
coercion never occurs.

Placement (FE-P0c WIRE, 2026-05-26): this Port lives in ``domain/ports/output``
alongside its sibling ``central_backend_sync_port.py`` — the hexagonal-correct
home for a driven-port abstraction (``TestProtocolPlacement`` enforces that all
``Protocol`` definitions live under ``domain/ports``). The concrete
``InMemoryCentralIdResolver`` stays in the application layer
(``application/headless/central_id_resolver``), which re-exports these symbols
for backwards compatibility.

dependency-free: stdlib typing only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


__all__ = [
    'CentralIdResolutionError',
    'CentralIdResolverPort',
    'ModelProjectResolution',
]


class CentralIdResolutionError(ValueError):
    """Raised when local→central id resolution fails — loud, never silent.

    Silent coercion (e.g., ``str(local_int)`` as the central uuid) would
    silently break FK integrity downstream. Always raise loud so caller chains
    surface the missing lookup.
    """


@dataclass(frozen=True)
class ModelProjectResolution:
    """Outcome of resolving a measured model number to a central project.

    Three states, and the third is why this is a value object rather than an
    ``Optional[str]``:

    - **resolved** — ``project_uuid`` is set, ``reason`` empty.
    - **not registered** — the model has no central project yet. The measurement
      facts are still true, so this is NOT an error (see the Port method).
    - **ambiguous** — the model name maps to more than one project. Central
      ``device_models`` carries no UNIQUE index on ``model_name``, so this is
      reachable, and picking one would put *another project's* equipment on the
      report. Never guess.

    ``reason`` exists because the two unresolved states need different operator
    actions (register the model vs. disambiguate the model name), and a bare
    ``None`` cannot say which.
    """

    project_uuid: Optional[str] = None
    reason: str = ''

    @property
    def is_resolved(self) -> bool:
        return bool(self.project_uuid)


@runtime_checkable
class CentralIdResolverPort(Protocol):
    """Resolve local identifiers to central uuids."""

    def resolve_session_uuid(
        self,
        local_session_id: int,
        *,
        chamber_id: Optional[str] = None,
        target_identity: Optional[str] = None,
    ) -> str:
        """Return the central session uuid for a local id and chamber scope.

        ``chamber_id=None`` preserves the legacy provider-scoped identity for
        existing callers. New chamber transports must pass their non-empty
        chamber identity so two chambers cannot share a UUID.

        ``target_identity`` scopes the uuid to one measurement target. It is
        required for the same reason ``chamber_id`` is: a chamber PC keeps one
        measurement database per target and each numbers its sessions from 1,
        so without it two devices measured on one chamber share a uuid.
        ``None``/empty preserves the pre-existing identity space.
        """
        ...

    def resolve_project_uuid(self, local_project_id: Optional[str]) -> Optional[str]:
        """Return the central project uuid for a local project code, or ``None``
        if the input is ``None``/empty (project_id is nullable in central
        ``measurement_attempts``/``measurement_results``).
        """
        ...

    def resolve_project_by_model_number(
        self, model_number: Optional[str]
    ) -> ModelProjectResolution:
        """Resolve the measured *model number* to a central project uuid.

        **Why this exists.** A measurement session knows its target as
        ``(model_number, sample_no)`` and deliberately carries no project code —
        ``MeasurementTargetIdentity`` omits it because model numbers are globally
        unique, and issuing a central project reference at measurement time would
        tie the offline measurement loop to central availability. So the project
        axis has to be recovered somewhere, and central is the only place that
        can answer it (``device_models.model_name → project_id``, ADR-0017
        "Project = Model 1:1").

        **Why it does not raise.** Unlike :meth:`resolve_project_uuid` — whose
        caller passed a project code and therefore asserted the project exists —
        this method is asked about a model that may legitimately not be
        registered centrally yet. Measurement facts are true independently of
        project registration, so a miss must not hold the result sync hostage:
        it returns an unresolved :class:`ModelProjectResolution` and the caller
        surfaces it loudly while still committing the measurements.
        """
        ...
