"""Test plan authoring domain models — Phase D.0 (ADR-0009 D-6 / ADR-0010 D-10).

**provider-agnostic 공통 value object**. Phase B generator + Phase C validator (둘
다 unlicensed provider service) 가 본 generic 모델을 공유해 service→service 결합을
끊는다 (validator 가 더 이상 generator 를 import 하지 않음). persistence (Phase D.1+)
/ API (Phase E) 는 본 모델 위에 올라간다.

**파일명 결정 (Codex ratify 대상, 2026-05-31)**: ADR-0009 Architecture Summary 는
`domain/models/test_plan.py` 를 지정했으나, 그 경로는 **이미 `TestPlanSnapshot`**
(TestRunner→GUI 경계 DTO, Excel 런타임 스냅샷, `tests/test_test_plan_page_invariants.py`
가 봉인하는 load-bearing 모듈) 이 선점했다. 둘은 같은 "test plan" 단어를 쓰지만
다른 bounded context 다 — `TestPlanSnapshot` = 런타임 Excel 스냅샷, 본 모듈 =
capability-driven **authoring/generation** 도메인. 관심사 혼합 회피를 위해 별도
파일 `test_plan_authoring.py` 로 분리한다. (대안: 추후 `TestPlanSnapshot` 을
`test_plan_snapshot.py` 로 rename 해 `test_plan.py` 를 비우는 별도 sprint — GUI
어댑터/invariant 영향이라 Phase D.0 scope 밖.)

**위치 결정** (ADR-0009 Architecture Summary): `TestPlanRow` / `RowOrigin` /
`ValidationIssue` 는 어느 provider 든 동일한 generic 개념이라 `domain/models/`
(provider-agnostic) 에 둔다 — `domain/services/unlicensed/` 가 아니다.
provider-specific row extension 은 향후 `domain/models/unlicensed/*` (ADR-0010 D-10).

**purity**: stdlib (dataclass / enum / typing) only. domain.services / infrastructure
/ application / pandas / pyvisa / openpyxl / PySide6 import 0. models 는 services 에
의존하지 않는다 (의존 방향: service → model).

**Protocol 미도입** (Codex 지시, Phase D.0): row 다형성은 manual / imported row
타입이 실제로 생길 때 (Phase D.1+) 판단. 지금은 concrete value object 로 닫는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RowOrigin(str, Enum):
    """Test plan row 의 출처 (ADR-0009 D-6) — immutable provenance.

    GENERATED: TestPlanGenerator 가 scope revision 에서 생산.
    MANUAL: 운영자가 추가 (matrix 에서 derive 불가).
    IMPORTED: Excel import 경로 (cell 단위 추가 분류).
    """
    GENERATED = 'generated'
    MANUAL = 'manual'
    IMPORTED = 'imported'


class DerivedRowKind(str, Enum):
    """Derived (non-execution) test-plan row 분류 — 명시적 식별자 SSOT (2026-06-06).

    Derived row 는 lab/report 의 계산·합산·결과기록을 위해 Test Plan 에 존재하지만
    **직접 DUT 실행 행이 아니다**. 실행 선택(active-row filter)은 이 행들을 기존
    runtime marker 로 제외한다 (`TestSessionService.is_active_modulation` 의 `_AFH`
    토큰 / `is_active_antenna` 의 `ALL1+ALL2` 안테나). 본 enum 은 그 derived 성격을
    **행 데이터에서 명시적으로** 식별하게 해 downstream 소비자가 modulation/antenna
    문자열을 substring 파싱하지 않아도 되게 한다 (authoring↔execution 분리).

    - `AFH_OCCUPANCY`: BT Occupancy 의 `_AFH` companion (time-of-occupancy 계산 행).
      materialize 시 modulation cell 이 `<packet>_AFH` 로 채워져 active filter 가 제외.
    - `ANTENNA_SUM`: Dual 안테나 power 의 `ALL1+ALL2` 합산/집계 행 (제3의 물리 포트
      스위치가 아니라 계산 행). antenna cell 이 `ALL1+ALL2` 라 active filter 가 제외.

    `None` (필드 미설정) = 직접 실행 행 (direct execution row).
    """
    AFH_OCCUPANCY = 'afh_occupancy'
    ANTENNA_SUM = 'antenna_sum'


@dataclass(frozen=True)
class TestPlanRow:
    """draft / version 의 한 행 — 공통 provenance value object (ADR-0009 D-6).

    `capability_path`: `(technology, sub_family, band, channel_ref_id, bandwidth)`
    capability 좌표 (channel-level). `origin` 이 GENERATED/MANUAL/IMPORTED 를 구분.
    `scope_revision`: GENERATED 시 출처 scope revision (MANUAL/IMPORTED 는 None 가능).

    `condition_hash`: **measurement condition identity (ADR-0005 stable hash)** 전용.
    `None` = 아직 materialize 전 (Phase D 에서 cell 값 + `measurement_history.
    compute_stable_hash_for_field_set` SSOT 로 채워짐). **generation-time identity 는
    여기 두지 않는다** — generated candidate row 의 식별자는
    `GeneratedTestPlanRow.generation_key` (preview/diff/regeneration tracking 용,
    ADR-0005 condition_hash 와 별개 의미). 두 identity 를 한 필드로 뭉개지 않기 위해
    `condition_hash` 는 Optional 로 두고 materialize 전에는 None 으로 남긴다.

    generator 의 `GeneratedTestPlanRow` 는 본 클래스의 thin 특수화이고, validator 는
    본 generic 타입에 의존한다 (service→service 결합 없음).
    """

    capability_path: tuple[str, ...]
    origin: RowOrigin
    scope_revision: Optional[int]
    condition_hash: Optional[str] = None
    # Authoring structural fields (manual-row-structural-fields, 2026-06-03,
    # Codex Option A) — promoted from GeneratedTestPlanRow to the base so
    # generated / manual / imported rows can all carry them. These are a partial
    # subset of the condition field set; Power and the rest stay deferred (the
    # full materialization input is a separate, re-keying-gated sprint).
    test_type: Optional[str] = None
    antenna: Optional[str] = None
    mode_family: Optional[str] = None
    tone: Optional[str] = None
    location: Optional[str] = None
    # Display packet override (bt-packet-schema, 2026-06-06) — None = 미지정(기존
    # 동작 유지). BT BDR sub_family(`BR`)는 DH1/DH3/DH5 packet 을 모두 공유하므로
    # capability_path 의 sub_family 만으로는 표시 packet 을 구분할 수 없다. 이 필드가
    # 설정되면 display 렌더러가 modulation 을 이 packet 토큰으로 우선 표시하고
    # (없으면 sub_family→base modulation fallback), generation_key 가 이 값을 포함해
    # 같은 sub_family 의 서로 다른 packet row identity 가 충돌하지 않게 한다
    # (authoring↔execution 분리 — packet 은 표시/식별 토큰이지 capability 좌표 아님).
    packet: Optional[str] = None
    # Derived-row provenance (bt-derived-rows, 2026-06-06) — None = 직접 실행 행.
    # 명시적 분류라 downstream 소비자가 modulation/antenna 텍스트를 파싱하지 않아도
    # derived(_AFH / ALL1+ALL2) 행을 식별/제외할 수 있다 (authoring↔execution 분리).
    derived_kind: Optional[DerivedRowKind] = None


class ValidationSeverity(str, Enum):
    """validation issue 심각도. ERROR = publish gate 차단 후보, WARNING = 운영자 결정."""
    ERROR = 'error'
    WARNING = 'warning'


class ValidationIssueType(str, Enum):
    """validation issue 종류 (Phase C 구현 범위 — generic)."""
    DUPLICATE_CONDITION = 'duplicate_condition'                # D-9 #1 / #5 — stable condition hash
    DUPLICATE_GENERATION_KEY = 'duplicate_generation_key'      # generation-time candidate identity 중복
    OUT_OF_SCOPE_CAPABILITY = 'out_of_scope_capability'        # D-9 #3
    REQUIRED_FIELD_MISSING = 'required_field_missing'
    # draft 의 저장된 generation metadata snapshot(생성 당시 선택 조건 + summary)이
    # 재적재된 row 집계와 불일치 — 데이터 drift/손상 신호 (bt-draft-generation-metadata,
    # 2026-06-07). draft-level issue (특정 row 가 아님 → capability_path None).
    GENERATION_SUMMARY_MISMATCH = 'generation_summary_mismatch'


@dataclass(frozen=True)
class ValidationIssue:
    """validation 결과 한 건 — immutable value object (row mutate 아님).

    `condition_hash` / `capability_path` 는 issue 가 가리키는 row 식별 정보.
    """

    issue_type: ValidationIssueType
    severity: ValidationSeverity
    message: str
    condition_hash: Optional[str] = None
    capability_path: Optional[tuple[str, ...]] = None
