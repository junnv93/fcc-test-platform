"""Phase 6 progress expectation assembly — provider ingest computation (D-P6-PROV).

Composes the pure-domain pricing (``progress_pricing``) and bucketing
(``progress_bucket``) services into a single ``ProgressExpectationAtom`` per
published-plan condition. This is the *provider*-owned derivation the platform
must never do: it resolves the progress bucket, prices the test type against the
catalog, and freezes the minutes as a snapshot (catalog_version stamped).

The ``condition_hash`` is carried **verbatim** (the stable published-plan hash —
``test_p6_2_condition_hash_join_key_seal.py``); it is never recomputed here.

Pure domain — ``domain.models`` + ``domain.services`` only.
"""
from __future__ import annotations

from typing import Optional

from domain.models.progress_expectation import ProgressExpectationAtom
from domain.models.progress_time_catalog import StandardTimeCatalog
from domain.services.progress_bucket import progress_bucket_id
from domain.services.progress_pricing import price_test_type


def build_expectation_atom(
    *,
    project_id: str,
    plan_id: str,
    provider_id: str,
    progress_area: str,
    condition_hash: str,
    technology,
    band,
    raw_test_type,
    catalog: StandardTimeCatalog,
    plan_published_at: Optional[str] = None,
) -> ProgressExpectationAtom:
    """Assemble one expectation atom from a published-plan condition.

    ``technology``/``band``/``raw_test_type`` are the condition's raw fields.
    ``condition_hash`` is the verbatim stable hash (join key). ``catalog`` is the
    provider's standard-time catalog; its ``version`` is snapshotted onto the atom
    so a later catalog edit never changes this already-ingested denominator.
    ``plan_published_at`` is the originating plan's publish time (ISO-8601),
    carried for the read-side latest-wins window (``None`` = untracked, sorts
    oldest).
    """
    priced = price_test_type(raw_test_type, catalog)
    bucket = progress_bucket_id(technology=technology, band=band)
    return ProgressExpectationAtom(
        project_id=project_id,
        plan_id=plan_id,
        provider_id=provider_id,
        progress_area=progress_area,
        condition_hash=condition_hash,
        coverage_technology=(str(technology).strip() if technology is not None else ''),
        raw_test_type=str(raw_test_type),
        test_type_canonical=priced.canonical,
        progress_bucket_id=bucket,
        planned_minutes_snapshot=priced.planned_minutes,
        catalog_version=catalog.version,
        pricing_status=priced.pricing_status,
        plan_published_at=plan_published_at,
    )
