"""Capability taxonomy — Phase A SSOT (ADR-0009 D-3 / D-11 / D-15 / D-18).

본 모듈은 layered capability vocabulary 의 enum SSOT 다:

- Technology → SubFamily → Modulation → Band → Bandwidth → Channel → TestType
- CapabilityLifecycle = active / deprecated / retired (D-15)
- RegionCode = FCC/CE/MIC/KCC (D-18 reference data 의 region)

`domain/models/enums.py::TechType`, `BandType`, `UniiSubBand` 는 그대로 import
해 재사용한다 (재정의 금지).

`SubFamily` typed union (P1-2 cleanup, 2026-05-31): `object | None` 같은 약한
타입은 영구 폐기. technology 가 결정하는 sub_family enum class lookup 은
`SUB_FAMILY_BY_TECHNOLOGY` 매핑을 통해 validation 한다.
"""
from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Mapping, Union

from .enums import TechType


# ─── Bluetooth Classic sub-families (BR/EDR) ───────────────────────────────


class BtSubFamily(str, Enum):
    """BT Classic sub-family — BR (1-slot basic rate) + EDR slot 변종."""
    BR = 'BR'
    EDR_2DH1 = 'EDR_2DH1'
    EDR_2DH3 = 'EDR_2DH3'
    EDR_2DH5 = 'EDR_2DH5'
    EDR_3DH1 = 'EDR_3DH1'
    EDR_3DH3 = 'EDR_3DH3'
    EDR_3DH5 = 'EDR_3DH5'


# ─── BLE sub-families + PHY ────────────────────────────────────────────────


class BleSubFamily(str, Enum):
    """BLE sub-family — 1M / 2M / CODED variants."""
    LE_1M = 'LE_1M'
    LE_2M = 'LE_2M'
    LE_CODED_125K = 'LE_CODED_125K'
    LE_CODED_500K = 'LE_CODED_500K'


class BleModulationPhy(str, Enum):
    """BLE PHY — sub-family 와 1:1 정합 (LE_1M↔PHY_1M, LE_2M↔PHY_2M, ...)."""
    PHY_1M = 'PHY_1M'
    PHY_2M = 'PHY_2M'
    PHY_CODED_S2 = 'PHY_CODED_S2'
    PHY_CODED_S8 = 'PHY_CODED_S8'


# ─── DTS (2.4 GHz Wi-Fi) sub-families ──────────────────────────────────────


class DtsSubFamily(str, Enum):
    """DTS = 2.4 GHz Wi-Fi sub-family.

    802.11b 는 DSSS/CCK PHY 로 802.11g(OFDM) 와 측정/리포트 identity 가 다르므로 별도
    sub_family (같은 §15.247 규제 bucket 이어도 분리). 연대순(b→g→n→ax→be) 정의.
    """
    IEEE_802_11B = '802.11b'
    IEEE_802_11G = '802.11g'
    IEEE_802_11N_2_4 = '802.11n_2.4'
    IEEE_802_11AX_2_4 = '802.11ax_2.4'
    IEEE_802_11BE_2_4 = '802.11be_2.4'


# ─── UNII (5/6 GHz Wi-Fi) sub-families ─────────────────────────────────────


class UniiSubFamily(str, Enum):
    """UNII = 5 GHz / 6 GHz Wi-Fi sub-family.

    6 GHz(UNII-5~8)는 11a·11ax·11be 로 측정한다(시험원 도메인 확정 2026-06-22):
    11ax/11be 는 ax_6/be_6, 802.11a(legacy 20 MHz OFDM)는 802.11a_6(별 멤버, ax_5/ax_6
    관례 동형). 11n/11ac 는 6 GHz 미측정(5 GHz 전용 유지).
    """
    IEEE_802_11A = '802.11a'
    IEEE_802_11N_5 = '802.11n_5'
    IEEE_802_11AC = '802.11ac'
    IEEE_802_11AX_5 = '802.11ax_5'
    IEEE_802_11BE_5 = '802.11be_5'
    IEEE_802_11AX_6 = '802.11ax_6'
    IEEE_802_11BE_6 = '802.11be_6'
    IEEE_802_11A_6 = '802.11a_6'


# ─── Channel bandwidth ─────────────────────────────────────────────────────


class ChannelBandwidth(str, Enum):
    """OFDM / BLE channel bandwidth."""
    BW_1M = '1M'
    BW_2M = '2M'
    BW_20M = '20M'
    BW_40M = '40M'
    BW_80M = '80M'
    BW_160M = '160M'
    BW_320M = '320M'


# ─── Capability lifecycle (D-15) ───────────────────────────────────────────


class CapabilityLifecycle(str, Enum):
    """ADR-0009 D-15. capability node 의 evolution 상태.

    - ACTIVE: new scope 에 선택 가능
    - DEPRECATED: 이미 scope 에 있으면 유지, 신규 선택 시 warn + audit
    - RETIRED: 신규 선택 차단 + 기존 선택 auto-soft-delete 대상
    """
    ACTIVE = 'active'
    DEPRECATED = 'deprecated'
    RETIRED = 'retired'


# ─── Region code (D-18 Frequency Table seed 의 region) ─────────────────────


class RegionCode(str, Enum):
    """규제 region code — Frequency Table seed 의 region 컬럼과 정합."""
    FCC = 'FCC'
    CE = 'CE'
    MIC = 'MIC'
    KCC = 'KCC'


# ─── SubFamily typed union (P1-2) ──────────────────────────────────────────


SubFamily = Union[BleSubFamily, BtSubFamily, DtsSubFamily, UniiSubFamily]
"""typed union — `object | None` 같은 약한 타입의 영구 폐기 (P1-2 cleanup)."""


# ─── SubFamily SSOT (cascade fix A, 2026-05-31) ────────────────────────────
#
# honest self-audit 결과: 이전 Phase A cleanup 은 `SUB_FAMILY_BY_TECHNOLOGY`
# (technology→class) 와 `_ALLOWED_BANDS_BY_SUB_FAMILY_CLASS`
# (class→bands, capability_definition.py 내) 를 두 dict 로 분리해 SSOT 분산
# 위험. 같은 conceptual relationship (어떤 sub_family 가 어떤 technology +
# band 와 호환되는지) 인데 두 곳에 정의되면 Phase A.2/A.3/A.4 확장 시 drift
# 누적. 단일 SSOT (`SUB_FAMILY_SPECS` tuple) 채택 — 2 mapping 은 derived.
#
# Codex 권장 (2026-05-31): tuple 형태가 ordering 명시 + 가독성 양쪽에서 dict
# 보다 명료. `SubFamilySpec` frozen value object 가 각 entry 의 typed shape.
from dataclasses import dataclass  # noqa: E402 (after Enum import order)
from .enums import BandType  # noqa: E402


@dataclass(frozen=True)
class SubFamilySpec:
    """한 sub_family enum class 의 typed spec — `SUB_FAMILY_SPECS` 의 entry.

    매트릭스 / scope / NodeId invariant 가 모두 이 dataclass 의 세 필드를
    참조한다.
    """

    sub_family_class: type[Enum]
    technology: TechType
    allowed_bands: frozenset[BandType]


SUB_FAMILY_SPECS: tuple[SubFamilySpec, ...] = (
    SubFamilySpec(
        sub_family_class=BleSubFamily,
        technology=TechType.BLE,
        allowed_bands=frozenset({BandType.BAND_2_4G}),
    ),
    SubFamilySpec(
        sub_family_class=BtSubFamily,
        technology=TechType.BT,
        allowed_bands=frozenset({BandType.BAND_2_4G}),
    ),
    SubFamilySpec(
        sub_family_class=DtsSubFamily,
        technology=TechType.WLAN,
        allowed_bands=frozenset({BandType.BAND_2_4G}),
    ),
    SubFamilySpec(
        sub_family_class=UniiSubFamily,
        technology=TechType.WLAN,
        allowed_bands=frozenset({BandType.BAND_5G, BandType.BAND_6G}),
    ),
)
"""Single SSOT — sub_family class 별 (technology, allowed bands) spec.

새 sub_family enum class 추가 시 본 tuple 에 entry 추가. 두 derived mapping
은 자동 동기. SSOT 분산 위험 0.
"""


def _derive_sub_family_by_technology() -> Mapping[TechType, tuple[type[Enum], ...]]:
    """SUB_FAMILY_SPECS 에서 technology→class 매핑 자동 도출."""
    by_tech: dict[TechType, list[type[Enum]]] = {}
    for spec in SUB_FAMILY_SPECS:
        by_tech.setdefault(spec.technology, []).append(spec.sub_family_class)
    return MappingProxyType(
        {tech: tuple(classes) for tech, classes in by_tech.items()},
    )


def _derive_allowed_bands_by_sub_family_class() -> Mapping[type[Enum], frozenset[BandType]]:
    """SUB_FAMILY_SPECS 에서 class→bands 매핑 자동 도출."""
    return MappingProxyType(
        {spec.sub_family_class: spec.allowed_bands for spec in SUB_FAMILY_SPECS},
    )


SUB_FAMILY_BY_TECHNOLOGY: Mapping[TechType, tuple[type[Enum], ...]] = \
    _derive_sub_family_by_technology()
"""DERIVED — technology → 허용 SubFamily enum class 들. CapabilityNodeId 검증용.

SUB_FAMILY_SPECS 의 derived 라 별 갱신 불필요.
"""


ALLOWED_BANDS_BY_SUB_FAMILY_CLASS: Mapping[type[Enum], frozenset[BandType]] = \
    _derive_allowed_bands_by_sub_family_class()
"""DERIVED — sub_family class → 허용 BandType 집합. CapabilityNodeId 검증용.

SUB_FAMILY_SPECS 의 derived 라 별 갱신 불필요. 이전 cleanup 의
`_ALLOWED_BANDS_BY_SUB_FAMILY_CLASS` (definition.py 내) 폐기 + 본 derived 사용.
"""


# ─── Regulatory bucket ordering SSOT (Caveat #1, Codex 2026-05-31) ─────────
#
# capability matrix / test plan node ordering 은 regulatory/test section 기준이라
# BLE < BT < DTS < UNII 순서가 Phase B golden snapshot 의 장기 계약이 된다.
#
# 이전 (RC-1) 구현은 이 ordering rank 를 `SUB_FAMILY_SPECS` tuple 순서에서
# derive 했다. 하지만 `SUB_FAMILY_SPECS` 의 본래 의도는 sub_family class →
# (technology, allowed bands) 매핑 SSOT 다. 거기에 ordering rank 의미를 얹으면
# dual-purpose coupling 이 생긴다 — 누군가 무관한 이유 (예: 가독성, 신규 class
# 삽입 위치) 로 `SUB_FAMILY_SPECS` 를 재정렬하면 정렬 계약이 조용히 바뀐다.
#
# 본 tuple 은 "어떤 순서로 정렬하는가" 를 명시한 별도 SSOT 다. 하드코딩 rank
# dict (`{Ble:0, Bt:1, ...}`) 가 아니라 순서 자체를 단일 지점에 선언하고 rank 는
# enumerate 로 파생한다. `SUB_FAMILY_SPECS` 와 class set 은 동일해야 하지만
# (drift gate invariant — test_capability_matrix_architecture.py) 의도가 분리돼
# 있어 한쪽을 바꿔도 다른 쪽 의미를 침범하지 않는다. 새 SubFamily class 추가
# 시 SUB_FAMILY_SPECS 와 본 tuple 양쪽에 등재해야 한다 (invariant 가 강제).
REGULATORY_BUCKET_ORDER: tuple[type[Enum], ...] = (
    BleSubFamily,
    BtSubFamily,
    DtsSubFamily,
    UniiSubFamily,
)
"""regulatory bucket 정렬 순서 SSOT — BLE < BT < DTS < UNII. capability matrix
node ordering 의 최우선 sort field (rank 는 본 tuple 의 enumerate index).
SUB_FAMILY_SPECS 와 class set 동일 (invariant 봉인) 이나 의도 (정렬 vs
technology/band 매핑) 는 분리 — dual-purpose coupling 폐기 (Caveat #1)."""
