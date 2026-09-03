"""Application service for Phase 6 progress ingest (P6-B.3).

``ProgressIngestService`` turns a published plan's conditions into denominator
rows:

    published conditions (raw fields + verbatim condition_hash)
      → build_expectation_atom (pure domain: price × bucket × snapshot)
      → CentralProgressWritePort.write_expectations (atomic upsert by natural key)
      → IngestReport (insert/update counts + priced/unpriced/unbucketable tallies)

The service is reader-agnostic and DB-agnostic — it takes already-extracted
condition rows and a write port, so it stays unit-testable without disk or a real
PostgreSQL. The catalog (provider default minutes) is passed in; the snapshot is
frozen by ``build_expectation_atom`` (D-P6-SNAP).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from fcc_test_platform.domain.models.progress_time_catalog import PricingStatus, StandardTimeCatalog
from fcc_test_platform.domain.ports.output.central_progress_write_port import (
    CentralProgressWritePort,
    ExpectationWriteSummary,
)
from fcc_test_platform.domain.services.progress_expectation import build_expectation_atom


__all__ = ['PublishedConditionRow', 'IngestReport', 'ProgressIngestService']


@dataclass(frozen=True)
class PublishedConditionRow:
    """One published-plan condition to ingest (raw fields + verbatim hash)."""

    condition_hash: str
    technology: str
    band: str
    raw_test_type: str


@dataclass(frozen=True)
class IngestReport:
    write_summary: ExpectationWriteSummary
    condition_count: int
    priced_count: int
    unpriced_count: int
    unbucketable_count: int


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProgressIngestService:
    def __init__(
        self,
        write_port: CentralProgressWritePort,
        *,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        self._write_port = write_port
        self._clock = clock or _utc_now_iso

    def ingest_published_plan(
        self,
        *,
        project_id: str,
        plan_id: str,
        provider_id: str,
        progress_area: str,
        conditions: Sequence[PublishedConditionRow],
        catalog: StandardTimeCatalog,
        plan_published_at: Optional[str] = None,
    ) -> IngestReport:
        atoms = [
            build_expectation_atom(
                project_id=project_id,
                plan_id=plan_id,
                provider_id=provider_id,
                progress_area=progress_area,
                condition_hash=row.condition_hash,
                technology=row.technology,
                band=row.band,
                raw_test_type=row.raw_test_type,
                catalog=catalog,
                plan_published_at=plan_published_at,
            )
            for row in conditions
        ]
        summary = self._write_port.write_expectations(atoms, now=self._clock())
        priced = sum(1 for a in atoms if a.pricing_status is PricingStatus.PRICED)
        unbucketable = sum(1 for a in atoms if a.progress_bucket_id is None)
        return IngestReport(
            write_summary=summary,
            condition_count=len(atoms),
            priced_count=priced,
            unpriced_count=len(atoms) - priced,
            unbucketable_count=unbucketable,
        )
