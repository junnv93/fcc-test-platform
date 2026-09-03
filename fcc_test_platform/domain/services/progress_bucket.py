"""Phase 6 progress bucket derivation — provider-owned (band + standard → bucket).

The official time-weighted progress is rolled up per **progress bucket** (the
operator workbook's Status sheet rows: ``BT``, ``BLE``, ``DTS``, ``DTS(11ax)``,
``UNII 1``..``UNII 8`` and their ``(11ax)`` variants). ADR-0010 forbids the
platform from deriving this taxonomy — the *provider* computes the
``progress_bucket_id`` for each measurement condition at ingest time (D-P6-PROV).
This module is that provider-owned derivation.

Derivation rule (reusing existing domain SSOT — no new band/technology vocab):
  - ``Technology`` BT / BLE → ``bt`` / ``ble`` (``TechType``).
  - WLAN (``Technology`` ∈ {DTS, NII} spread, or an ``11*`` PHY token — the same
    ``_is_wlan_technology`` idiom used by the import mapper):
      * frequency family + UNII number come from ``Band`` via the existing
        ``band_type_for_workbook_band`` SSOT + the raw ``UNII-N`` band token
        (2.4 GHz → ``dts``; ``UNII-2A`` → ``unii_2a``; ``UNII-5(LPI)`` → ``unii_5``);
      * generation suffix ``_11ax`` when the ``Technology`` token is a next-gen
        PHY (``technology.startswith('11')`` — the established predicate; the
        workbook groups 11ax *and* 11be under the single next-gen bucket, matching
        the binary classic-vs-(11ax) split of the Status sheet).
  - Anything else (unknown technology, unmappable band) → ``None``: the condition
    is unbucketable and must be surfaced, never guessed into a wrong bucket.

DFS / RSDB are operator-workbook progress categories with **no measurement
condition** in the Test Plan (radar-detection / simultaneous-dual-band are
separate benches), so they are intentionally out of scope here — measurement-
driven progress cannot fabricate buckets it never measures.

Pure domain — ``domain.models`` + ``domain.services.unlicensed`` band SSOT +
stdlib only. No infrastructure / pandas / openpyxl / PySide6.
"""
from __future__ import annotations

from typing import Optional

from fcc_test_kernel.domain.models.enums import (
    FIVE_GHZ_BANDS,
    SIX_GHZ_BANDS,
    BandType,
    SpreadType,
    TechType,
)
from fcc_test_kernel.domain.services.unlicensed.wlan_import_capability_policy import (
    band_type_for_workbook_band,
)

#: progress_bucket_id vocabulary owned by this module (the only place these
#: tokens are defined). UNII bucket ids are *derived* from the band SSOT below —
#: never an inline UNII list.
BUCKET_BT = 'bt'
BUCKET_BLE = 'ble'
BUCKET_DTS = 'dts'
_UNII_BUCKET_PREFIX = 'unii_'
_NEXT_GEN_SUFFIX = '_11ax'

#: Prefix of the ``UNII-*`` workbook band tokens (matches ``UniiSubBand`` /
#: ``FIVE_GHZ_BANDS`` / ``SIX_GHZ_BANDS`` value format — not a fresh vocabulary).
_UNII_BAND_PREFIX = 'UNII-'


def _is_wlan_technology(technology: str) -> bool:
    """Whether a ``Technology`` token is WLAN (DTS/NII spread or 11* PHY).

    Mirrors ``test_plan_import_mapping._is_wlan_technology`` using the
    ``SpreadType`` enum values (no inline 'DTS'/'NII' literals).
    """
    return (
        technology in {SpreadType.DTS.value, SpreadType.NII.value}
        or technology.startswith('11')
    )


def _unii_segment(band_token: str) -> str:
    """``UNII-2A`` → ``2a``; ``UNII-5(LPI)`` → ``5`` (power class dropped).

    The band token is assumed validated as a UNII band (member of
    FIVE/SIX_GHZ_BANDS). Sub-band letters (2A/2C) are preserved; power-class
    suffixes (LPI/SP/VLP) are dropped so 5/6/7/8 collapse to one bucket each,
    matching the Status sheet's ``UNII 5``..``UNII 8`` rows.
    """
    core = band_token[len(_UNII_BAND_PREFIX):]
    core = core.split('(', 1)[0]
    return core.strip().lower()


def _unii_bucket_for_band(band_token: str) -> str:
    return _UNII_BUCKET_PREFIX + _unii_segment(band_token)


def progress_bucket_id(*, technology, band) -> Optional[str]:
    """Derive the ``progress_bucket_id`` for one measurement condition.

    ``None`` when the condition is unbucketable (unknown technology, or a WLAN
    row whose Band is not a recognized 2.4 GHz / UNII band) — callers surface it
    rather than mis-bucketing.
    """
    tech = (str(technology).strip() if technology is not None else '')
    if tech == TechType.BT.value:
        return BUCKET_BT
    if tech == TechType.BLE.value:
        return BUCKET_BLE
    if not _is_wlan_technology(tech):
        return None

    band_type = band_type_for_workbook_band(band)
    if band_type is None:
        return None
    if band_type is BandType.BAND_2_4G:
        family = BUCKET_DTS
    else:
        family = _unii_bucket_for_band(str(band).strip())

    suffix = _NEXT_GEN_SUFFIX if tech.startswith('11') else ''
    return family + suffix


def all_progress_bucket_ids() -> frozenset[str]:
    """Every bucket id this provider can emit — derived from the band SSOT.

    BT/BLE + DTS + one bucket per distinct UNII number (from FIVE/SIX_GHZ_BANDS),
    each in classic and ``_11ax`` next-gen variants. No hardcoded 30-item list —
    adding a UNII band to the enum SSOT extends this set automatically.
    """
    families = {BUCKET_DTS}
    for token in FIVE_GHZ_BANDS | SIX_GHZ_BANDS:
        families.add(_unii_bucket_for_band(token))
    ids = {BUCKET_BT, BUCKET_BLE}
    for family in families:
        ids.add(family)
        ids.add(family + _NEXT_GEN_SUFFIX)
    return frozenset(ids)
