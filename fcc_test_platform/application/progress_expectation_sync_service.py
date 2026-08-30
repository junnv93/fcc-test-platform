"""P6L-3 expectation sync service — local published plans → central denominator.

``ProgressExpectationSyncService.run_once`` is the watermark/derive pull (approach
B): it reads the local published plans (read-only, latest-per-project unsynced),
prices + buckets each condition against the central catalog, writes the central
``published_plan_expectation`` denominator, and watermarks the plan as synced —
but ONLY after the central commit succeeds. A failure (project unresolvable,
catalog un-seeded, central down) is recorded with its reason so the next tick
retries and the read side surfaces it instead of a fake 0%.

The measurement-result sync path (result_outbox / CentralBackendSyncAdapter) is
NOT touched — published plans are immutable + durable, so they are derived, not
enqueued (M3). condition_hash is carried verbatim by the render-glue.

Boundary: application — composes domain ports (sync-state, catalog read, id
resolver) + the ingest service + the render-glue. stdlib logging only (no
infrastructure import).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from fcc_test_platform.application.progress_ingest_service import ProgressIngestService
from fcc_test_platform.application.published_plan_progress_glue import (
    published_conditions_from_rows,
)
from domain.ports.output.central_id_resolver_port import CentralIdResolverPort
from domain.ports.output.central_progress_catalog_read_port import (
    CentralProgressCatalogReadPort,
)
from domain.ports.output.expectation_sync_state_port import ExpectationSyncStatePort


__all__ = ['ExpectationSyncReport', 'ProgressExpectationSyncService']

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExpectationSyncReport:
    """Outcome of one ``run_once`` — plan + condition tallies for logging/return."""

    plans_attempted: int = 0
    plans_synced: int = 0
    plans_failed: int = 0
    conditions: int = 0
    unpriced: int = 0
    unbucketable: int = 0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProgressExpectationSyncService:
    def __init__(
        self,
        *,
        sync_state: ExpectationSyncStatePort,
        catalog_reader: CentralProgressCatalogReadPort,
        id_resolver: CentralIdResolverPort,
        ingest_service: ProgressIngestService,
        provider_id: str,
        progress_area: str,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        if not provider_id or not str(provider_id).strip():
            raise ValueError('provider_id is required for expectation sync')
        if not progress_area or not str(progress_area).strip():
            raise ValueError('progress_area is required for expectation sync')
        self._sync_state = sync_state
        self._catalog_reader = catalog_reader
        self._id_resolver = id_resolver
        self._ingest = ingest_service
        self._provider_id = str(provider_id).strip()
        self._progress_area = str(progress_area).strip()
        self._clock = clock or _utc_now_iso

    def run_once(self) -> ExpectationSyncReport:
        pending = list(self._sync_state.list_pending())
        if not pending:
            return ExpectationSyncReport()

        # One catalog load per run (provider-level). If un-seeded, every plan
        # fails with that reason (retried once seeded) — never a fake denominator.
        catalog = self._catalog_reader.load_catalog(self._provider_id)
        now = self._clock()
        synced = failed = conditions = unpriced = unbucketable = 0

        for plan in pending:
            try:
                if catalog is None:
                    self._sync_state.mark_failed(
                        plan_id=plan.plan_id, project_id=plan.project_id,
                        synced_at=now,
                        error=(
                            'standard_time_catalog not seeded for provider '
                            f'{self._provider_id} — run seed_standard_time_catalog'
                        ),
                    )
                    failed += 1
                    logger.warning(
                        'expectation sync: catalog un-seeded for provider %s; '
                        'plan %s deferred', self._provider_id, plan.plan_id,
                    )
                    continue

                central_project = self._id_resolver.resolve_project_uuid(
                    plan.project_id
                )
                if not central_project:
                    self._sync_state.mark_failed(
                        plan_id=plan.plan_id, project_id=plan.project_id,
                        synced_at=now,
                        error=(
                            f'local project {plan.project_id!r} not resolvable to a '
                            'central project uuid'
                        ),
                    )
                    failed += 1
                    continue

                report = self._ingest.ingest_published_plan(
                    project_id=central_project,
                    plan_id=plan.plan_id,
                    provider_id=self._provider_id,
                    progress_area=self._progress_area,
                    conditions=published_conditions_from_rows(plan.rows),
                    catalog=catalog,
                    plan_published_at=plan.published_at,
                )
                # Watermark ONLY after the central commit (ingest) succeeds.
                self._sync_state.mark_synced(
                    plan_id=plan.plan_id, project_id=plan.project_id,
                    synced_at=now, catalog_version=catalog.version,
                )
                synced += 1
                conditions += report.condition_count
                unpriced += report.unpriced_count
                unbucketable += report.unbucketable_count
                if report.unpriced_count or report.unbucketable_count:
                    # S2 — surface (never silently fold into the percent as zero).
                    logger.info(
                        'expectation sync: plan %s ingested %d conditions '
                        '(%d unpriced, %d unbucketable)',
                        plan.plan_id, report.condition_count,
                        report.unpriced_count, report.unbucketable_count,
                    )
            except Exception as exc:  # noqa: BLE001 — per-plan isolation + retry
                self._sync_state.mark_failed(
                    plan_id=plan.plan_id, project_id=plan.project_id,
                    synced_at=now, error=str(exc),
                )
                failed += 1
                logger.error(
                    'expectation sync failed for plan %s (retained for retry): %s',
                    plan.plan_id, exc,
                )

        return ExpectationSyncReport(
            plans_attempted=len(pending),
            plans_synced=synced,
            plans_failed=failed,
            conditions=conditions,
            unpriced=unpriced,
            unbucketable=unbucketable,
        )
