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
from fcc_test_kernel.domain.models.published_test_plan import PublishedTestPlan
from fcc_test_kernel.domain.models.test_plan_authoring import TestPlanRow
from fcc_test_kernel.domain.services.progress_publish_glue import progress_condition_tokens


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
        # ⚠️ 커널이 `TestPlanRow.condition_hash` 를 **Optional** 로 두는 것은
        # *"materialize 전에는 None 으로 남긴다"* 는 뜻이다(그 모델의 docstring).
        # 이 모듈의 전제는 「materialized published rows」인데 지금까지 그 전제가
        # **산문으로만** 있었다 — 미materialize 행이 하나 섞이면 `None` 이 진행률
        # ingest 의 조인 키로 조용히 실려 간다(`PublishedConditionRow.condition_hash`
        # 는 `str` 로 선언돼 있고 그 조인 키는 P6.2 봉인의 대상이다).
        # 전제를 실행 가능하게 만든다: 위반은 여기서 죽고, 아래로 내려가지 않는다.
        if row.condition_hash is None:
            raise ValueError(
                'published rows must carry a materialized condition_hash; '
                f'row test_type={row.test_type!r} has none'
            )
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
