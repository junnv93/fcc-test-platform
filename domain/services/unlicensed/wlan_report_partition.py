"""WLAN report-partition SSOT — Test Plan Band → 보고 단위 분할 도메인 정책.

운영자가 WLAN test plan 을 저작할 때 Test Plan ``Band`` 컬럼은 세 개의 **report 단위**
로 묶인다:

  - ``2.4GHz``          → DTS report unit
  - ``UNII-1~4`` (5GHz) → UNII 저대역 report partition (``enums.FIVE_GHZ_BANDS``)
  - ``UNII-5~8`` (6GHz) → UNII 고대역 report partition (``enums.SIX_GHZ_BANDS``)

밴드 멤버십 SSOT 는 ``enums.FIVE_GHZ_BANDS`` / ``enums.SIX_GHZ_BANDS`` 이며, 본 모듈은
그로부터 운영자-facing 표시 순서(번호 → 문자 접미 → 전력 클래스)와 partition 매핑만
파생한다. 인라인 UNII 밴드 리터럴 컬렉션은 금지(``TestLayerPurity::test_no_inline_unii_lists``)
— 모든 UNII 토큰은 enums SSOT 에서 온다.

이 partition 은 Test Plan Generator 의 사용자-facing report 단위 어휘다. reporting bounded
context 의 ``ReportTech`` (단일 UNII) 와는 별개 — generator 는 UNII 를 5GHz/6GHz 두
report partition 으로 더 세분한다(운영자가 UNII-1~4 와 UNII-5~8 을 별도 산출물로 다룸).

## purity

domain 레이어 — stdlib(``re`` / ``enum``) only. infrastructure / application / pyvisa /
openpyxl / pandas / PySide6 import 0(도메인 순수).
"""
from __future__ import annotations

import re
from enum import Enum

from domain.models.enums import FIVE_GHZ_BANDS, SIX_GHZ_BANDS

__all__ = [
    'WLAN_2_4GHZ_BAND',
    'WLAN_FIVE_GHZ_BANDS_ORDERED',
    'WLAN_SIX_GHZ_BANDS_ORDERED',
    'WLAN_UNII_BANDS_ORDERED',
    'WlanReportPartition',
    'bands_for_partition',
    'partition_for_band',
    'partition_label',
]


WLAN_2_4GHZ_BAND = '2.4GHz'


class WlanReportPartition(str, Enum):
    """WLAN Test Plan 의 report 단위 partition (사용자-facing 어휘, 3-멤버 SSOT)."""

    DTS_2_4GHZ = '2.4GHz'
    UNII_LOW_5GHZ = 'UNII-1~4'
    UNII_HIGH_6GHZ = 'UNII-5~8'


# enums frozenset 은 순서가 없으므로 운영자-facing 표시 순서를 결정론적으로 복원한다
# (번호 → 문자 접미 → 전력 클래스). reporting/wlan_workbook 의 기존 정렬과 동일 의미.
_POWER_CLASS_ORDER: dict[str, int] = {'': 0, 'LPI': 1, 'SP': 2, 'VLP': 3}


def _unii_sort_key(band: str) -> tuple[int, str, int]:
    match = re.match(r'UNII-(\d+)([A-Z]*)(?:\((LPI|SP|VLP)\))?$', band)
    if match is None:  # pragma: no cover - SSOT 토큰 형상 보증
        raise ValueError(f'Unexpected UNII band token: {band!r}')
    number, letter, power_class = match.groups()
    return (int(number), letter, _POWER_CLASS_ORDER[power_class or ''])


#: UNII-1~4 (5GHz) 결정론적 순서 — ``enums.FIVE_GHZ_BANDS`` 파생.
WLAN_FIVE_GHZ_BANDS_ORDERED: tuple[str, ...] = tuple(
    sorted(FIVE_GHZ_BANDS, key=_unii_sort_key)
)
#: UNII-5~8 (6GHz) 결정론적 순서 — ``enums.SIX_GHZ_BANDS`` 파생.
WLAN_SIX_GHZ_BANDS_ORDERED: tuple[str, ...] = tuple(
    sorted(SIX_GHZ_BANDS, key=_unii_sort_key)
)
#: 전체 UNII 밴드 결정론적 순서 (5GHz 저대역 → 6GHz 고대역). workbook-axis SSOT 가 위임.
WLAN_UNII_BANDS_ORDERED: tuple[str, ...] = (
    WLAN_FIVE_GHZ_BANDS_ORDERED + WLAN_SIX_GHZ_BANDS_ORDERED
)


_BANDS_BY_PARTITION: dict[WlanReportPartition, tuple[str, ...]] = {
    WlanReportPartition.DTS_2_4GHZ: (WLAN_2_4GHZ_BAND,),
    WlanReportPartition.UNII_LOW_5GHZ: WLAN_FIVE_GHZ_BANDS_ORDERED,
    WlanReportPartition.UNII_HIGH_6GHZ: WLAN_SIX_GHZ_BANDS_ORDERED,
}

_PARTITION_BY_BAND: dict[str, WlanReportPartition] = {
    band: partition
    for partition, bands in _BANDS_BY_PARTITION.items()
    for band in bands
}


def partition_for_band(band: str) -> WlanReportPartition:
    """workbook Band 토큰 → 소속 report partition (모든 Band 가 정확히 한 partition)."""
    try:
        return _PARTITION_BY_BAND[band]
    except KeyError:
        raise ValueError(f'Unknown WLAN Band token: {band!r}') from None


def bands_for_partition(partition: WlanReportPartition) -> tuple[str, ...]:
    """report partition → 결정론적 순서의 소속 Band 토큰 튜플."""
    return _BANDS_BY_PARTITION[partition]


def partition_label(partition: WlanReportPartition) -> str:
    """partition 의 사용자-facing 표시 라벨(Test Plan 어휘 = enum value)."""
    return partition.value
