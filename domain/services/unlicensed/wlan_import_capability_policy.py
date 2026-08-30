"""WLAN(DTS/UNII) Excel import 어휘 → capability 추론 정책 (provider domain, pure).

Excel Test Plan 의 워크북 어휘(`Technology` + `Band` + `Modulation`)를 capability
taxonomy 의 `BandType` + `sub_family` 로 역매핑하는 SSOT. WLAN 생성기(display row)가
capability → 워크북 토큰을 *생산*하는 것의 **역방향**이며, 분류 기준 집합은 기존
도메인 SSOT(`enums.FIVE_GHZ_BANDS`/`SIX_GHZ_BANDS`, `wlan_report_partition.WLAN_2_4GHZ_BAND`)
를 재사용한다(신규 Band 어휘 정의 금지).

순수성: stdlib + domain.models 만. infrastructure/pandas/openpyxl/PySide6 import 0.

설계(2026-06-22, roadmap §11 비준):
- **분야는 Technology 단독이 아니라 Band 의존**(11ax/11be 가 2.4G→DTS / 5G·6G→UNII).
  따라서 sub_family 추론 키 = `(modulation_token, BandType)` 이고, BandType 는 Band 토큰을
  `enums.FIVE_GHZ_BANDS`/`SIX_GHZ_BANDS` membership 으로 분류해 얻는다.
- capability 좌표 값은 모두 enum 멤버 참조(문자열 리터럴 capability 값 금지).
- 매핑 부재(예: legacy 802.11a/n/ac @ 6GHz, 또는 미지원 변조)는 **None 반환 → caller 가
  blocking issue 로 처리**(추측 매핑 금지 — 틀린 플랜 데이터 방지).
"""
from __future__ import annotations

from typing import Mapping, Optional, Tuple

from domain.models.capability_taxonomy import DtsSubFamily, SubFamily, UniiSubFamily
from domain.models.enums import FIVE_GHZ_BANDS, SIX_GHZ_BANDS, BandType
from domain.services.unlicensed.wlan_report_partition import WLAN_2_4GHZ_BAND


def band_type_for_workbook_band(band_token: Optional[str]) -> Optional[BandType]:
    """워크북 `Band` 셀 → `BandType`(미분류 시 None).

    분류 기준은 기존 SSOT 재사용: `WLAN_2_4GHZ_BAND`('2.4GHz') 또는 capability band
    '2.4G' → 2.4G; `FIVE_GHZ_BANDS`(UNII-1~4) → 5G; `SIX_GHZ_BANDS`(UNII-5~8, 전력클래스
    포함) → 6G. 전력클래스(LPI/SP/VLP)·서브밴드(2A/2C)는 토큰 원형 그대로 membership 판정
    (측정조건이지 sub_family 좌표 아님 → 정규화 불필요, 6G 분류만 결정).
    """
    token = (band_token or '').strip()
    if token in {WLAN_2_4GHZ_BAND, BandType.BAND_2_4G.value}:
        return BandType.BAND_2_4G
    if token in FIVE_GHZ_BANDS:
        return BandType.BAND_5G
    if token in SIX_GHZ_BANDS:
        return BandType.BAND_6G
    return None


# (modulation 토큰, BandType) → sub_family 추론 SSOT. capability 좌표는 enum 멤버 참조.
# Band 의존 분기: 같은 변조라도 2.4G→DTS / 5G·6G→UNII (regulatory subpart 분리).
_WLAN_SUB_FAMILY_BY_MOD_BAND: Mapping[Tuple[str, BandType], SubFamily] = {
    # DTS (2.4 GHz, §15.247). 802.11b = DSSS/CCK (≠802.11g OFDM) — 별도 sub_family.
    ('802.11b', BandType.BAND_2_4G): DtsSubFamily.IEEE_802_11B,
    ('802.11g', BandType.BAND_2_4G): DtsSubFamily.IEEE_802_11G,
    ('802.11n', BandType.BAND_2_4G): DtsSubFamily.IEEE_802_11N_2_4,
    ('802.11ax', BandType.BAND_2_4G): DtsSubFamily.IEEE_802_11AX_2_4,
    ('802.11be', BandType.BAND_2_4G): DtsSubFamily.IEEE_802_11BE_2_4,
    # UNII 5 GHz (§15.407 / Subpart E)
    ('802.11a', BandType.BAND_5G): UniiSubFamily.IEEE_802_11A,
    ('802.11n', BandType.BAND_5G): UniiSubFamily.IEEE_802_11N_5,
    ('802.11ac', BandType.BAND_5G): UniiSubFamily.IEEE_802_11AC,
    ('802.11ax', BandType.BAND_5G): UniiSubFamily.IEEE_802_11AX_5,
    ('802.11be', BandType.BAND_5G): UniiSubFamily.IEEE_802_11BE_5,
    # UNII 6 GHz — 시험원 도메인 확정(2026-06-22): 6 GHz 측정 변조 = 11a/11ax/11be.
    # 802.11a(legacy 20 MHz OFDM)는 802.11a_6 별 멤버. 11n/11ac 는 6 GHz 미측정(미등재).
    ('802.11a', BandType.BAND_6G): UniiSubFamily.IEEE_802_11A_6,
    ('802.11ax', BandType.BAND_6G): UniiSubFamily.IEEE_802_11AX_6,
    ('802.11be', BandType.BAND_6G): UniiSubFamily.IEEE_802_11BE_6,
}


def wlan_sub_family(
    modulation_token: Optional[str],
    band_type: Optional[BandType],
) -> Optional[SubFamily]:
    """`(Modulation, BandType)` → sub_family enum(미지원 조합 None → caller issue).

    None 사례: band_type 미분류 / 미지원 변조 / 미정의 조합(legacy @ 6GHz, 802.11b
    taxonomy 미확장 등). caller 는 None 을 추측 매핑하지 말고 blocking issue 로 남긴다.
    """
    if band_type is None:
        return None
    key = ((modulation_token or '').strip(), band_type)
    return _WLAN_SUB_FAMILY_BY_MOD_BAND.get(key)
