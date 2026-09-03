"""Provider-owned standard-time catalog — Phase 6 progress (time-weighted) SSOT.

Phase 6 redesign (``.claude/exec-plans/active/2026-06-23-p6-progress-redesign.md``):
the official project progress is **planned-minutes time-weighted**, not condition
count. The per-test-type time weight is a *provider-defined catalog* (D-P6-TIME),
keyed at **test-type granularity** (D-P6-GRAN). This module holds the pure-domain
value objects for that catalog.

Canonicalization is NOT re-implemented here — the catalog key is the existing
``MeasurementType`` dispatch token, reached via the existing
``normalize_dispatch_token`` SSOT (``domain.models.enums``). A raw Excel test
label whose normalized token is not a known ``MeasurementType`` is **unpriced**
(``PricingStatus.UNPRICED``) — it must never be silently counted as 0 minutes in
the official percent (gates: progress view must surface ``unpriced_minutes``).

Pure domain — stdlib + ``domain.models.enums`` only. No infrastructure / pandas /
pyvisa / PySide6 / SQL. The frozen value objects are hashable (tuple-of-pairs
backing, mirroring ``provider_ui_descriptor``'s frozen-dataclass idiom).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional

from fcc_test_kernel.domain.models.enums import MeasurementType


class CatalogSource(str, Enum):
    """Provenance of a catalog entry's minutes value (D-P6-2).

    ``WORKBOOK_SEED`` — derived once from the operator workbook's hand-entered
    minutes (initial seed). ``MANUAL`` — subsequently edited in the app. The
    string values are the persisted ``standard_time_catalog.source`` tokens.
    """

    WORKBOOK_SEED = 'workbook_seed'
    MANUAL = 'manual'


class PricingStatus(str, Enum):
    """Whether a test type's canonical token is covered by the catalog.

    ``PRICED`` — the canonical ``MeasurementType`` has a catalog minutes entry,
    so it contributes to the official percent denominator. ``UNPRICED`` — the raw
    label normalized to an unknown token OR a known token absent from the
    catalog; its planned minutes are surfaced separately as ``unpriced_minutes``
    and excluded from the percent (no fake 0%).
    """

    PRICED = 'priced'
    UNPRICED = 'unpriced'


@dataclass(frozen=True)
class PricedTestType:
    """Result of pricing one raw test label against a catalog.

    ``canonical`` is ``None`` when the raw label is not a known measurement type.
    ``planned_minutes`` is ``None`` exactly when ``pricing_status`` is
    ``UNPRICED`` (the two are kept consistent by ``price_test_type``).
    """

    raw_test_type: str
    canonical: Optional[MeasurementType]
    planned_minutes: Optional[float]
    pricing_status: PricingStatus


@dataclass(frozen=True)
class StandardTimeCatalog:
    """Immutable provider-owned ``MeasurementType -> planned_minutes`` catalog.

    Backed by a tuple of ``(MeasurementType, minutes)`` pairs to stay frozen and
    hashable (a ``Mapping`` field would not be). ``version`` is the catalog
    revision stamped onto each expectation snapshot (D-P6-SNAP) so that editing
    the catalog never retroactively changes already-published progress.
    """

    version: int
    source: CatalogSource
    _entries: tuple[tuple[MeasurementType, float], ...]

    @classmethod
    def from_mapping(
        cls,
        minutes_by_type: Mapping[MeasurementType, float],
        *,
        version: int,
        source: CatalogSource,
    ) -> 'StandardTimeCatalog':
        """Build a catalog from a ``MeasurementType -> minutes`` mapping.

        Entries are stored in a deterministic order (by the enum's dispatch
        token) so equal catalogs are byte-identical regardless of input order.
        Rejects non-positive minutes — a 0/negative time weight is a data error,
        not a valid "free" measurement (would silently vanish from the weighted
        sum). Absent test types are simply ``UNPRICED`` at lookup time.
        """
        normalized: dict[MeasurementType, float] = {}
        for test_type, minutes in minutes_by_type.items():
            if not isinstance(test_type, MeasurementType):
                raise TypeError(
                    f'catalog key must be MeasurementType, got {test_type!r}'
                )
            if minutes is None or float(minutes) <= 0.0:
                raise ValueError(
                    f'planned_minutes for {test_type.value!r} must be > 0, '
                    f'got {minutes!r}'
                )
            normalized[test_type] = float(minutes)
        entries = tuple(
            sorted(normalized.items(), key=lambda kv: kv[0].value)
        )
        return cls(version=version, source=source, _entries=entries)

    def minutes_for(self, test_type: MeasurementType) -> Optional[float]:
        """Catalog minutes for a canonical test type, or ``None`` if absent."""
        for entry_type, minutes in self._entries:
            if entry_type is test_type:
                return minutes
        return None

    def priced_types(self) -> tuple[MeasurementType, ...]:
        """The canonical test types this catalog prices (deterministic order)."""
        return tuple(entry_type for entry_type, _ in self._entries)
