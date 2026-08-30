"""Phase 6 progress pricing — raw test label → canonical type → planned minutes.

The official project progress is time-weighted (planned minutes). This service
turns a raw Excel ``Col.TEST`` label into a *priced* atom: it canonicalizes the
label to a ``MeasurementType`` (reusing the existing ``normalize_dispatch_token``
SSOT — no duplicated normalization rule) and looks its minutes up in a provider
``StandardTimeCatalog``.

D-P6-CANON: ``raw_test_type -> test_type_canonical`` is the single load-bearing
canonical step — everything downstream (catalog lookup, snapshot, progress view)
keys on it. We deliberately delegate the normalization to
``normalize_dispatch_token`` (``domain.models.enums``) rather than re-deriving it,
so the progress canonical and the measurement-dispatch canonical can never drift.

Pure domain — ``domain.models`` + stdlib only.
"""
from __future__ import annotations

from typing import Optional

from domain.models.enums import MeasurementType, normalize_dispatch_token
from domain.models.progress_time_catalog import (
    PricedTestType,
    PricingStatus,
    StandardTimeCatalog,
)


def canonical_test_type(raw_test_type) -> Optional[MeasurementType]:
    """Canonicalize a raw ``Col.TEST`` label to a ``MeasurementType``.

    Reuses ``normalize_dispatch_token`` (the existing label→dispatch-token SSOT)
    then validates membership. Returns ``None`` when the normalized token is not
    a known measurement type — the caller treats that as ``UNPRICED`` rather than
    guessing or hardcoding a fallback minute value.
    """
    token = normalize_dispatch_token(raw_test_type)
    try:
        return MeasurementType(token)
    except ValueError:
        return None


def price_test_type(
    raw_test_type,
    catalog: StandardTimeCatalog,
) -> PricedTestType:
    """Price one raw test label against the catalog.

    ``UNPRICED`` (planned_minutes ``None``) when the label is not a known
    measurement type OR the canonical type is absent from the catalog — in both
    cases its minutes must be surfaced as ``unpriced_minutes`` downstream, never
    folded into the official percent as zero.
    """
    canonical = canonical_test_type(raw_test_type)
    if canonical is None:
        return PricedTestType(
            raw_test_type=str(raw_test_type),
            canonical=None,
            planned_minutes=None,
            pricing_status=PricingStatus.UNPRICED,
        )
    minutes = catalog.minutes_for(canonical)
    if minutes is None:
        return PricedTestType(
            raw_test_type=str(raw_test_type),
            canonical=canonical,
            planned_minutes=None,
            pricing_status=PricingStatus.UNPRICED,
        )
    return PricedTestType(
        raw_test_type=str(raw_test_type),
        canonical=canonical,
        planned_minutes=minutes,
        pricing_status=PricingStatus.PRICED,
    )
