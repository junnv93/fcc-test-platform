"""P6L-3a application glue — PublishedTestPlan → progress denominator conditions.

Bridges the immutable published plan (``PublishedTestPlan``, with materialized
``condition_hash`` per row) to the ``PublishedConditionRow`` rows that
``ProgressIngestService.ingest_published_plan`` prices + buckets. The pure-domain
``progress_condition_tokens`` derives the (technology, band) tokens
``progress_bucket_id`` accepts; this application function only assembles the
ingest DTO around them.

Boundary: application — imports the domain glue + the ingest DTO only (no
infrastructure). ``condition_hash`` is carried **verbatim** from the published
row (the stable join key sealed by ``test_p6_2_condition_hash_join_key_seal.py``)
and ``raw_test_type`` is the published row's ``test_type`` verbatim — neither is
recomputed.
"""
from __future__ import annotations

from typing import List, Sequence

from fcc_test_platform.application.progress_ingest_service import PublishedConditionRow
from domain.models.published_test_plan import PublishedTestPlan
from domain.models.test_plan_authoring import TestPlanRow
from domain.services.progress_publish_glue import progress_condition_tokens


__all__ = ['published_conditions_from_plan', 'published_conditions_from_rows']


def published_conditions_from_rows(
    rows: Sequence[TestPlanRow],
) -> List[PublishedConditionRow]:
    """Map materialized published rows to progress-ingest condition rows.

    One ``PublishedConditionRow`` per row, preserving order. The (technology,
    band) tokens come from the pure-domain glue; ``condition_hash`` and
    ``raw_test_type`` are carried verbatim (never recomputed).
    """
    conditions: List[PublishedConditionRow] = []
    for row in rows:
        technology, band = progress_condition_tokens(row)
        conditions.append(
            PublishedConditionRow(
                condition_hash=row.condition_hash,
                technology=technology,
                band=band,
                raw_test_type=row.test_type or '',
            )
        )
    return conditions


def published_conditions_from_plan(
    plan: PublishedTestPlan,
) -> List[PublishedConditionRow]:
    """Map a published plan's rows to progress-ingest condition rows (rows SSOT)."""
    return published_conditions_from_rows(plan.rows)
