"""Phase 6 progress publish glue — published-plan row → (technology, band) tokens.

The progress denominator ingest (``ProgressIngestService``) prices + buckets each
published condition with the *provider* taxonomy (``progress_bucket_id``). That
bucket SSOT keys on the **workbook** technology/band vocabulary
(``BT``/``BLE``/``DTS``/``NII``/``11ax`` + ``UNII-1``/``UNII-2A``/``UNII-5(LPI)`` …).
A published ``TestPlanRow`` carries a ``capability_path``
``(technology, sub_family, band, channel_ref_id, bandwidth)`` whose tokens differ:

- ``capability_path[0]`` is the ``TechType`` value — ``'WLAN'`` for every Wi-Fi
  row — yet ``progress_bucket_id`` expects the *spread / PHY-generation* token
  (``DTS``/``NII``/``11ax``). So the WLAN technology token is **derived from the
  sub_family + band**, never the raw TechType.
- the UNII sub-band number lives ONLY in the ``channel_ref_id`` band segment
  (``WLAN|UNII-1|20MHz|Lower`` → ``UNII-1``), parsed via the ``channel_ref_id``
  grammar SSOT (``parse_channel_ref_id``). ``capability_path[2]`` is only
  ``'5G'``/``'6G'`` (BandType) — it has no UNII sub-number — and the display
  renderer collapses to that same ``'5G'``/``'6G'`` (its ``WLAN-UNII-`` dash
  probe never matches the pipe-delimited id), so neither is usable for UNII
  bucketing. The band segment can be ``;``-combined power classes
  (``UNII-5(LPI);UNII-5(SP);UNII-5(VLP)``); the first token
  (``band_tokens()[0]``) is the bucket-recognized form (the bucket SSOT drops
  the power-class suffix, so ``UNII-5(LPI)`` and ``UNII-5(SP)`` share ``unii_5``).

``condition_hash`` is carried **verbatim** by the application caller (never
recomputed here — sealed by ``test_p6_2_condition_hash_join_key_seal.py``); this
module owns only the (technology, band) derivation.

Pure domain — ``domain.models`` + ``domain.services`` + stdlib only. No
infrastructure / pandas / openpyxl / pyvisa / PySide6.
"""
from __future__ import annotations

from typing import Tuple

from domain.models.enums import BandType, SpreadType, TechType
from domain.models.test_plan_authoring import TestPlanRow
from domain.services.unlicensed.channel_ref_identity import parse_channel_ref_id
from domain.services.unlicensed.wlan_import_capability_policy import (
    band_type_for_workbook_band,
)

__all__ = ['progress_condition_tokens']


# Next-generation Wi-Fi PHY tokens. ``progress_bucket_id`` collapses BOTH into the
# single ``_11ax`` next-gen bucket via its ``startswith('11')`` predicate (the
# Status sheet groups 11ax and 11be under one next-gen row), so the only invariant
# this glue must honour is that a next-gen technology token starts with ``'11'``.
# These appear inside the ``DtsSubFamily`` / ``UniiSubFamily`` values
# (``'802.11ax_2.4'``, ``'802.11be_5'`` …) as the load-bearing generation marker.
_WLAN_NEXT_GEN_PHY_TOKENS: Tuple[str, ...] = ('11ax', '11be')

# capability_path indices — D-5 order
# (technology, sub_family, band, channel_ref_id, bandwidth).
_PATH_TECHNOLOGY = 0
_PATH_SUB_FAMILY = 1
_PATH_CHANNEL = 3


def _wlan_band_token(channel_ref_id: str) -> str:
    """WLAN band token (workbook vocabulary) parsed from the ``channel_ref_id``.

    The UNII sub-band number is carried ONLY in the channel_ref_id band segment.
    ``band_tokens()[0]`` is the first power-class variant (``UNII-5(LPI)`` of a
    ``;``-combined segment) — the form ``band_type_for_workbook_band`` /
    ``progress_bucket_id`` recognize. Returns ``''`` for a non-grammar id
    (legacy/fixture) → unbucketable, surfaced rather than mis-bucketed.
    """
    identity = parse_channel_ref_id(channel_ref_id)
    if identity is None:
        return ''
    tokens = identity.band_tokens()
    return tokens[0] if tokens else ''


def _wlan_technology_token(sub_family_value: str, band_token: str) -> str:
    """WLAN ``Technology`` token for ``progress_bucket_id``.

    Next-gen first (so 11ax/11be 2.4 GHz rows reach the ``_11ax`` bucket via the
    ``'11'`` prefix), then classic spread by band family — ``DTS`` for 2.4 GHz
    (§15.247), ``NII`` for UNII (§15.407, incl. ``802.11a_6`` classic 6 GHz).
    Band-unrecognized rows fall through to ``NII``; they are unbucketable anyway
    (``progress_bucket_id`` returns ``None`` on an unknown band), so the token is
    immaterial and never mis-buckets.
    """
    sub_family = sub_family_value or ''
    for token in _WLAN_NEXT_GEN_PHY_TOKENS:
        if token in sub_family:
            return token
    if band_type_for_workbook_band(band_token) is BandType.BAND_2_4G:
        return SpreadType.DTS.value
    return SpreadType.NII.value


def progress_condition_tokens(row: TestPlanRow) -> Tuple[str, str]:
    """Derive the ``(technology, band)`` tokens a published row contributes.

    The tokens are exactly what ``progress_bucket_id(technology=…, band=…)``
    consumes:

    - BT/BLE → ``capability_path[0]`` verbatim (``'BT'``/``'BLE'`` — the bucket
      keys on technology alone, band irrelevant, returned ``''``).
    - WLAN → spread/PHY-generation token from sub_family + band, and the band
      token parsed from the ``channel_ref_id`` (``UNII-1`` / ``UNII-5(LPI)`` /
      ``2.4GHz``) — the only place the UNII sub-band survives.
    """
    path = row.capability_path
    technology = path[_PATH_TECHNOLOGY] if path else ''
    if technology != TechType.WLAN.value:
        return technology, ''
    sub_family = path[_PATH_SUB_FAMILY] if len(path) > _PATH_SUB_FAMILY else ''
    channel_ref_id = path[_PATH_CHANNEL] if len(path) > _PATH_CHANNEL else ''
    band = _wlan_band_token(channel_ref_id or '')
    technology = _wlan_technology_token(sub_family or '', band)
    return technology, band
