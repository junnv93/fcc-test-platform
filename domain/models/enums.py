# domain/models/enums.py
"""
도메인 열거형 — 외부 의존 없는 순수 상수 정의 (S7-T1).

이 파일이 'WLAN', 'BLE', 'BT', '2.4G' 등 문자열의
단일 출처(SSOT)입니다.

여러 파일에 흩어진 하드코딩 문자열을 이 열거형으로 대체하면:
  - 오타를 컴파일 타임(IDE 자동완성)에 잡을 수 있습니다.
  - 값 변경 시 한 곳만 수정하면 됩니다.

NOTE: 기존 코드는 여전히 문자열 리터럴을 사용합니다.
      이 파일은 새 코드가 참조할 SSOT로 먼저 정의하고,
      이후 스프린트에서 기존 코드를 점진적으로 마이그레이션합니다.
"""
from __future__ import annotations
import string
from enum import Enum


class TechType(str, Enum):
    """
    무선 기술 타입.

    str 상속으로 열거형 값을 기존 문자열 비교에 그대로 사용 가능:
        TechType.WLAN == 'WLAN'  # True
    """
    WLAN = 'WLAN'
    BLE = 'BLE'
    BT = 'BT'


class BandType(str, Enum):
    """
    주파수 대역 타입 (WLAN/BLE 기준).

    str 상속으로 기존 문자열 비교와 호환됩니다.
    """
    BAND_2_4G = '2.4G'
    BAND_5G = '5G'
    BAND_6G = '6G'


class UniiSubBand(str, Enum):
    """
    UNII (Unlicensed National Information Infrastructure) 하위 대역.

    FCC Part 15 기준:
      UNII-1  : 5.150~5.250 GHz (indoor)
      UNII-2A : 5.250~5.350 GHz
      UNII-2C : 5.470~5.725 GHz
      UNII-3  : 5.725~5.850 GHz
      UNII-4  : 5.850~5.925 GHz
    """
    UNII_1 = 'UNII-1'
    UNII_2A = 'UNII-2A'
    UNII_2C = 'UNII-2C'
    UNII_3 = 'UNII-3'
    UNII_4 = 'UNII-4'


# ===========================================================================
# UNII 밴드 그룹 frozenset — 모든 소비자의 인라인 UNII 리스트 SSOT (Sprint 79)
# ===========================================================================

#: 5GHz UNII 대역 전체 (스위치 포트 매핑, WLAN 밴드 선택 기준)
FIVE_GHZ_BANDS: frozenset[str] = frozenset({
    'UNII-1', 'UNII-2A', 'UNII-2C', 'UNII-3', 'UNII-4',
})

#: 5GHz UNII 대역 (DCCF 업데이트 그룹화용)
FIVE_GHZ_CORE_BANDS: frozenset[str] = frozenset({
    'UNII-1', 'UNII-2A', 'UNII-2C', 'UNII-3', 'UNII-4',
})

#: 6GHz UNII 대역 전체
SIX_GHZ_BANDS: frozenset[str] = frozenset({
    'UNII-5(LPI)', 'UNII-5(SP)', 'UNII-5(VLP)',
    'UNII-6(LPI)', 'UNII-6(VLP)',
    'UNII-7(LPI)', 'UNII-7(SP)', 'UNII-7(VLP)',
    'UNII-8(LPI)', 'UNII-8(VLP)',
})

#: 안테나 게인 보정이 필요한 밴드 (UNII-4 + 6GHz 전체)
GAIN_BANDS: frozenset[str] = frozenset({
    'UNII-4',
    'UNII-5(LPI)', 'UNII-5(SP)', 'UNII-5(VLP)',
    'UNII-6(LPI)', 'UNII-6(VLP)',
    'UNII-7(LPI)', 'UNII-7(SP)', 'UNII-7(VLP)',
    'UNII-8(LPI)', 'UNII-8(VLP)',
})


class AntennaIdentifier(str, Enum):
    """안테나 식별자 SSOT (psd-gain-band-policy P2-b, 2026-05-29).

    `Col.ANTENNA` 값이 가지는 5 토큰 — `power_judgment._ANTENNA_SEARCH_KEY_MAP`
    의 magic string dict 키를 enum 격상.

    `str` 상속으로 `Col.ANTENNA` 값과의 동치 비교 호환:
        AntennaIdentifier.ALL1 == 'ALL1'  # True
        AntennaIdentifier.MIMO_ALL == 'ALL1+ALL2'  # True

    - ANT1/ANT2: 단일 안테나
    - ALL1/ALL2: 단일 안테나 (테스트 운영자 표기 alias)
    - MIMO_ALL: ALL1+ALL2 MIMO 결합 식별자
    """
    ANT1 = 'ANT1'
    ANT2 = 'ANT2'
    ALL1 = 'ALL1'
    ALL2 = 'ALL2'
    MIMO_ALL = 'ALL1+ALL2'


# P3 헥사고날 정공 (2026-05-29) — `ExcelAntennaAlias` 는 `infrastructure/excel/
# antenna_alias.py` 로 이전. Excel 양식 어휘는 인프라 concept 이며 도메인 모델
# 에 위치하면 domain-infrastructure 경계 위반. cross-file SSOT invariant
# (`tests/test_excel_antenna_alias_cross_file_ssot.py`) 가 도메인 string literal
# (power_judgment._ANT*_GAIN_SEARCH_KEY) 과 인프라 enum 동기 봉인.


class SpreadType(str, Enum):
    """
    대역 확산 방식 (WLAN DSS/NII 구분).

    DTS: Direct Sequence Spread Spectrum (2.4 GHz)
    NII: Narrowband Intentional Interference — UNII 대역용
    """
    DTS = 'DTS'
    NII = 'NII'


class MeasurementType(str, Enum):
    """
    측정 타입 식별자 SSOT (S11-T1).

    값은 MeasurementRegistry 디스패치 키와 일치하는 lowercase_underscore 형식입니다.
    라벨→토큰 정규화는 `normalize_dispatch_token()` SSOT 가 수행(인라인 중복 금지).

    MeasurementRegistry 등록 예:
        registry.register(MeasurementType.OBW, strategy)

    현재 지원 측정 타입 (13개):
        OBW, OBW_IC, IBE, DUTY, PK_POWER, AV_POWER,
        CBE, CSE, SEPARATION, NUMBER, OCCUPANCY, CHANNEL_POWER, PSD
    """
    OBW           = 'obw'
    OBW_IC        = 'obw_ic'
    IBE           = 'ibe'
    DUTY          = 'duty'
    PK_POWER      = 'pk_power'
    AV_POWER      = 'av_power'
    CBE           = 'cbe'
    CSE           = 'cse'
    SEPARATION    = 'separation'
    NUMBER        = 'number'
    OCCUPANCY     = 'occupancy'
    CHANNEL_POWER = 'channel_power'
    PSD           = 'psd'


def normalize_dispatch_token(value) -> str:
    """`Col.TEST` display 라벨 → `MeasurementType` 디스패치 토큰 정규화 **SSOT**.

    `MeasurementRegistry.dispatch_workflow` 가 키로 쓰는 `lowercase_underscore`
    토큰(= `MeasurementType.*` 값 형식)으로 변환한다. 본 정규화 규칙은 과거
    `test_orchestrator` dispatch 와 `burst_timing_key_policy.is_duty_test_type`
    에 인라인으로 중복돼 drift 위험이 있었다 — 이 함수가 단일 소유점이다.

    `str()` 강제 + `strip()` 으로 None/공백 패딩 라벨(예 운영자가 Excel 에서 남긴
    trailing space)에 견고하다. 측정 어휘에 속한 정상 라벨(`'PSD'`/`'Av power'`)은
    공백이 없으므로 결과 토큰이 불변이다. 순수(stdlib only) — 도메인 계층 안전.
    """
    return str(value).strip().lower().replace(' ', '_')


#: `normalize_dispatch_token` 의 `.strip()` 의미를 **비-Python 경로**(예 SQL
#: `trim(x, chars)`)가 재현할 때 참조하는 공백 문자 집합 SSOT.
#:
#: 값은 `string.whitespace` == `' \t\n\r\x0b\x0c'` (space/tab/LF/CR/VT/FF — C 로케일
#: 공백). SQLite `trim(X, Y)` 의 두 번째 인자(제거할 문자 집합)로 그대로 바인딩해
#: `str.strip()` 의 양끝 공백 제거를 SQL 안에서 재현한다.
#:
#: ⚠️ **동치 범위 명시 (거짓 byte-equivalence 주장 금지)**: Python `str.strip()`
#:    (인자 없음)은 이 6개 문자 외에 ASCII 분리 제어문자(`\x1c`–`\x1f`)와 Unicode
#:    공백(`\xa0`, ` ` 등)까지 제거하지만, 이들은 Excel 테스트 라벨에 등장하지
#:    않는다. 따라서 SQL 재현 범위를 `string.whitespace` 로 한정하며, 잔여 발산
#:    (제어문자/Unicode 공백)은 *문서화된 경계*다 — 두 경로의 완전 동치를 주장하지
#:    않는다. 단, SQL 이 Duty 로 판정한 라벨은 항상 Python 도 Duty 로 판정하므로
#:    (SQL strip 문자 집합 ⊆ Python strip 집합), 잔여 발산은 SQL 이 *과잉* 우선
#:    순위를 부여하는 방향으로는 발생하지 않는다(보수적 — exotic 공백 패딩 Duty 라벨이
#:    SQL 에서만 우선순위 누락될 수 있을 뿐).
DISPATCH_TOKEN_STRIP_CHARS = string.whitespace
