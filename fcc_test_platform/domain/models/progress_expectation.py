"""Phase 6 progress expectation atom — the persistable denominator row.

One ``ProgressExpectationAtom`` is the provider-computed (D-P6-PROV), snapshot-
frozen (D-P6-SNAP) denominator unit for the time-weighted progress: a published
plan condition with its bucket/area/canonical/minutes already resolved at ingest
time. The central ``published_plan_expectation`` table (P6-B.2) persists exactly
these fields; the progress view (P6-C) then LEFT JOINs measurement coverage on
``(project_id, provider_id, condition_hash)`` (F1) and sums
``planned_minutes_snapshot`` — no runtime catalog join, no platform-side
taxonomy derivation.

Pure domain — stdlib + ``domain.models`` only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fcc_test_kernel.domain.models.enums import MeasurementType
from fcc_test_platform.domain.models.progress_time_catalog import PricingStatus


@dataclass(frozen=True)
class ProgressExpectationAtom:
    """A single published-plan condition priced + bucketed for progress rollup.

    ``test_type_canonical`` / ``progress_bucket_id`` / ``planned_minutes_snapshot``
    are ``None`` when the condition could not be canonicalized / bucketed / priced
    — captured (not dropped) so the progress view surfaces unpriced/unbucketable
    time instead of silently distorting the percent.
    """

    project_id: str
    plan_id: str
    provider_id: str
    progress_area: str
    condition_hash: str
    coverage_technology: str
    raw_test_type: str
    test_type_canonical: Optional[MeasurementType]
    progress_bucket_id: Optional[str]
    planned_minutes_snapshot: Optional[float]
    catalog_version: int
    pricing_status: PricingStatus
    #: Publish time (ISO-8601) of the plan this condition came from. Carried so the
    #: progress read can keep only the latest plan per (project, provider) — P6L-3
    #: read-side latest-wins. ``None`` for callers that do not track it (sorts
    #: oldest in the window, never regressing a newer plan's denominator).
    plan_published_at: Optional[str] = None

    @property
    def is_priced(self) -> bool:
        return self.pricing_status is PricingStatus.PRICED

    @property
    def is_bucketed(self) -> bool:
        return self.progress_bucket_id is not None
