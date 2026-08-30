"""ChannelRef id 문법 SSOT (도메인 — pure).

`ChannelRef.id` 는 `frequency_table` 한 행의 안정 식별자이며 문법은
``{technology}|{band}|{bandwidth}|{channel}`` 다(예: ``WLAN|UNII-5(LPI);UNII-5(SP);
UNII-5(VLP)|20MHz|Lower``). 이 문법은 **도메인 개념**(ChannelRef 정체성)이고 infra
(`DbChannelRegistry`)는 이를 persist 할 뿐이다 — 따라서 compose(생산)와 parse(소비)를
도메인 SSOT 로 둔다. `DbChannelRegistry._channel_id`(compose)와 import 매퍼(parse+match)가
모두 본 SSOT 에 위임해 grammar drift 를 차단한다.

순수성: stdlib only. infrastructure/pandas/openpyxl/PySide6 import 0.

설계 근거(실 frequency_table ground truth, 2026-06-22):
- band 세그먼트는 ``;``-combined 가능: WLAN 전력클래스(``UNII-5(LPI);UNII-5(SP);
  UNII-5(VLP)``)나 BT/BLE modulation(``BDR;EDR`` / ``1Mbps;125k_coded;500k_coded``)이
  한 행에 결합됨. 따라서 band 매칭은 **token-set membership**(equality 아님).
- channel 세그먼트는 숫자(BT/BLE/2.4GHz) 또는 위치 토큰(WLAN ``Lower``/``Mid``/``Upper``/
  ``Straddle``/``Mid2``). 매칭 정규화는 caller(`channel_identity.canonical_channel`)가
  숫자는 정규화·위치 토큰은 보존하므로 그 SSOT 에 위임.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

# ChannelRef id 세그먼트 구분자 + band 토큰 내부 구분자 SSOT (하드코딩 '|'/';' 금지).
CHANNEL_REF_ID_DELIMITER = '|'
BAND_TOKEN_DELIMITER = ';'
_SEGMENT_COUNT = 4


@dataclass(frozen=True)
class ChannelRefIdentity:
    """`ChannelRef.id` 를 4 세그먼트로 분해한 typed view (raw 토큰 보존)."""

    technology: str
    band: str
    bandwidth: str
    channel: str

    def band_tokens(self) -> List[str]:
        """``;``-combined band → 공백 제거 토큰 리스트(빈 토큰 제외)."""
        return [
            tok.strip()
            for tok in self.band.split(BAND_TOKEN_DELIMITER)
            if tok.strip()
        ]


def compose_channel_ref_id(technology, band, bandwidth, channel) -> str:
    """``{technology.value}|{band}|{bandwidth}|{channel}`` 안정 id 생성 SSOT.

    ``DbChannelRegistry._channel_id`` 가 위임(byte-identical). ``band``/``bandwidth``/
    ``channel`` 은 frequency_table raw 값(None/int 가능 — f-string 형식 보존).
    """
    return (
        f"{technology.value}{CHANNEL_REF_ID_DELIMITER}{band}"
        f"{CHANNEL_REF_ID_DELIMITER}{bandwidth}{CHANNEL_REF_ID_DELIMITER}{channel}"
    )


def parse_channel_ref_id(channel_ref_id: Optional[str]) -> Optional[ChannelRefIdentity]:
    """``ChannelRef.id`` → ``ChannelRefIdentity`` (정확히 4 세그먼트 아니면 None).

    None 반환 = 본 문법으로 생산되지 않은 id(legacy/fixture) — caller 는 match 실패로
    처리(silent 아님: 매칭 0건 → import issue).
    """
    if not channel_ref_id:
        return None
    parts = channel_ref_id.split(CHANNEL_REF_ID_DELIMITER)
    if len(parts) != _SEGMENT_COUNT:
        return None
    technology, band, bandwidth, channel = parts
    return ChannelRefIdentity(
        technology=technology,
        band=band,
        bandwidth=bandwidth,
        channel=channel,
    )
