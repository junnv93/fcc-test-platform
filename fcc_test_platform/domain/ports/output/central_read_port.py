"""Output port — central PostgreSQL read model (FE-P0d, 2026-05-27).

The platform read API (FE-P2 project coverage / FE-P3 claim-lock UX) reads
cross-engineer, project-wide state from the **central** database — never from a
local per-target SQLite file (a local DB only holds one engineer's sessions, so
it is the wrong source for project-wide coverage). This is the dependency-free
contract for that read model; the production adapter
(``application/platform/central_read_adapter.PostgresCentralReadAdapter``) reads
the central ``coverage_by_condition_hash`` materialized view and ``active_claims``
view through an injected ``DbConnection`` factory.

Read-only by construction: the Port exposes only ``read_*`` methods. condition
identity (``condition_hash``) is propagated from local measurement and read back
verbatim — it is never recomputed centrally.

Placement (hexagonal): driven-port abstraction lives under
``domain/ports/output`` alongside ``central_id_resolver_port.py`` /
``platform_ingestion_port.py`` (``TestProtocolPlacement`` enforces all
``Protocol`` definitions live under ``domain/ports``).

dependency-free: stdlib typing only (no infrastructure / pyvisa / openpyxl /
pandas / PySide6 / fastapi / sqlalchemy / psycopg imports).
"""
from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable


__all__ = [
    'CentralReadError',
    'CentralReadPort',
]


class CentralReadError(RuntimeError):
    """Raised when a central read fails — loud, never a silent empty result.

    A silently-empty coverage/claims response would render the FE-P2 dedup UI
    and the FE-P3 lock UX as "nothing measured / nothing claimed", masking a
    backend outage as a (dangerously wrong) clean slate. Always raise loud so
    the API surfaces a 5xx instead of a misleading empty 200.
    """


@runtime_checkable
class CentralReadPort(Protocol):
    """Read project-wide coverage / active claims from the central database."""

    def read_project_coverage(
        self,
        project_id: str,
        *,
        technology: Optional[str] = None,
        limit: Optional[int] = None,
        after: Optional[Sequence[str]] = None,
    ) -> list[dict]:
        """Return ``coverage_by_condition_hash`` rows for one central project uuid.

        One row per (technology, condition_hash) of the project, derived from the
        latest append-only measurement attempt. Empty list when the project has
        no measured conditions (a real, non-error empty).

        ``technology`` (Phase B facet) filters to a single technology when given
        (``None`` ⇒ all). ``limit``/``after`` drive opt-in keyset pagination
        (``None`` ⇒ the backward-compatible unbounded read).
        """
        ...

    def read_plan_conditions(
        self,
        project_id: str,
        *,
        technology: Optional[str] = None,
        limit: Optional[int] = None,
        after: Optional[Sequence[str]] = None,
    ) -> list[dict]:
        """Return ``published_plan_expectation`` rows for one central project uuid.

        One row per planned condition — the DENOMINATOR of progress, listed
        rather than counted. Empty list when the project has no published plan
        (a real, non-error empty).

        Why this exists beside ``read_project_coverage`` (2026-09-09): coverage
        answers "what has been measured" and the progress read answers "how many
        were planned", grouped by ``progress_area``. Neither can name a condition
        that has NOT been measured, because one only holds measured rows and the
        other collapses the plan into counts. An operator asking "what is left in
        this family" therefore got a number and no names. The plan table already
        holds one row per condition; this read stops hiding them.

        ⚠️ It carries ``raw_test_type`` — the test item (POWER / PSD / OBW / CBE
        / CSE / RBE / RSE / Below 1G / ACLine). That is the layer between a mode
        and a condition, and it is what a person schedules by. The finer axes
        that would fully identify one condition (channel, bandwidth, antenna)
        are NOT here: they live on the kernel's ``TestPlanRow`` and stop at plan
        publication, so this read cannot invent them. It returns what the central
        database actually holds and no more.

        Read-only. ``condition_hash`` is the join key back to coverage and
        claims; it is propagated verbatim and never recomputed here.
        """

    def read_active_claims(
        self,
        project_id: str,
        *,
        technology: Optional[str] = None,
        limit: Optional[int] = None,
        after: Optional[Sequence[str]] = None,
    ) -> list[dict]:
        """Return ``active_claims`` rows for one central project uuid.

        The latest still-held claim per (project, condition_hash) — i.e. an
        ``acquired`` claim with no later ``released``/``expired`` event. Same
        ``technology`` / ``limit`` / ``after`` semantics as
        :meth:`read_project_coverage`.
        """
        ...

    def read_sync_status(self, project_id: str) -> dict:
        """Return central-data freshness aggregates for one project uuid (FE-SYNC).

        ``{'last_ingested_at': str | None, 'condition_count': int,
        'active_claim_count': int}`` — the newest central measurement timestamp
        (``MAX(latest_measured_at)`` over the coverage view), the number of
        measured conditions, and the raw count of still-open claim ledger rows.
        The service derives server_time / age / is_stale and re-reads
        ``active_claims`` to split unexpired active claims from expired-open
        claim rows for the public ``active_claim_count`` /
        ``expired_open_claim_count`` envelope fields. The platform surface reads
        the CENTRAL database, so this reports how fresh the central coverage is;
        a station's local outbox (pending-to-sync count) is not observable from
        here (documented limitation — that is a local-surface concern).
        """
        ...

    def read_project_report_sessions(self, project_id: str) -> list[dict]:
        """Return reportable central sessions with node routing metadata.

        Rows are derived by joining the project coverage projection to central
        ``test_sessions`` and the ``providers`` registry — node routing resolves
        through the DURABLE ``test_sessions.provider_id -> providers.base_url``
        edge, never the ``chamber_availability`` live "latest heartbeat per
        chamber" projection (whose ``session_id`` moves off a completed session
        the moment the chamber idles, dropping still-reportable sessions). The
        platform read remains SELECT-only; the caller groups rows into one
        reportable session option per node-local ``provider_session_id``.
        """
        ...
