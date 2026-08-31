# ⚠️ 2026-08-31: 이 파일은 모노레포 `tests/test_architecture_conformance.py` 에서 갈라져 왔다. 남은 것은
#    소비 대상이 이 레포에 있는 단위(TestFrontendSealInvariantMarkerCoverage)뿐이고,
#    나머지 형제 검사와 그것들만 쓰던 import 는 저쪽에 남았다.
"""
아키텍처 불변식 검증 테스트 (S13-T3)

이 테스트들은 헥사고날 아키텍처의 핵심 규칙이 침해되지 않았는지
정적 분석(AST)과 런타임 검사로 지속적으로 확인합니다.

검증 범위:
  1. Domain 순수성 — domain/ 내 금지 라이브러리 import 없음
  2. Port Protocol — @runtime_checkable, execute 메서드 존재
  3. bootstrap_for_test() 불변식 — 6 keys, 각 Port 충족
  4. MeasurementType 정합성 — 13개, 중복 없음, 값 포맷

이 테스트가 실패하면:
  - Domain 레이어에 인프라 의존이 침투했거나
  - Port 계약이 깨졌거나
  - bootstrap 조립이 불완전합니다.
  즉시 회귀로 간주하고 수정해야 합니다.
"""

import ast
import sys
import unittest
from functools import lru_cache
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))
sys.path.insert(0, str(Path(__file__).parent))


@lru_cache(maxsize=128)
def _cached_read_text(path: str) -> str:
    """파일 읽기 캐시 — 동일 경로 반복 읽기 최적화 (Sprint 112 Phase 5a)."""
    return Path(path).read_text(encoding='utf-8')

from domain.models.enums import MeasurementType

# 도메인 계층에서 금지된 외부 라이브러리 (I/O, UI, 인프라)
_DOMAIN_FORBIDDEN = frozenset({
    'pyvisa', 'pandas', 'openpyxl', 'PySide6', 'PySide2',
    'selenium', 'appium', 'flask', 'fastapi', 'sqlalchemy',
    'requests', 'serial', 'numpy',
})


# ===========================================================================
# 헬퍼: AST 기반 import 추출
# ===========================================================================

def _collect_top_level_imports(src_path: Path) -> set[str]:
    """Python 파일에서 최상위 import 패키지 이름을 수집한다."""
    try:
        tree = ast.parse(src_path.read_text(encoding='utf-8'))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    packages = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                packages.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:  # 절대 import만
                packages.add(node.module.split('.')[0])
    return packages


def _find_domain_files() -> list[Path]:
    """src/domain/ 아래 모든 .py 파일을 반환한다."""
    domain_dir = project_root / 'src' / 'domain'
    return list(domain_dir.rglob('*.py'))


# ===========================================================================
# Domain 순수성 — 금지 import 없음
# ===========================================================================



# ===========================================================================
# Output Port Protocol 불변식
# ===========================================================================



# ===========================================================================
# bootstrap_for_test() 불변식
# ===========================================================================



# ===========================================================================
# MeasurementType 정합성
# ===========================================================================



# ===========================================================================
# Sprint 26~29 아키텍처 불변식 (S29-T2)
# ===========================================================================





# ===========================================================================
# Sprint 31 아키텍처 불변식
# ===========================================================================



# ===========================================================================
# Sprint 31 (Phase 1+2) 아키텍처 불변식 — TestOrchestrator 추출
# ===========================================================================



# ===========================================================================
# Sprint 32 아키텍처 불변식
# ===========================================================================



# ===========================================================================
# Sprint 36 아키텍처 불변식 — Strategy dead code 제거 검증
# ===========================================================================



# ===========================================================================
# Sprint 33 아키텍처 불변식 — InstrumentPort 실관통 Phase 1 검증
# ===========================================================================



# ===========================================================================
# Trace SSOT 아키텍처 불변식 — trace_utils 리팩토링 검증
# ===========================================================================



# ===========================================================================
# Sprint 32 불변식 — BT_keystring bare logging 사용 금지
# ===========================================================================

def _has_bare_logging_calls(src_path: Path) -> list[str]:
    """
    Python 파일에서 'logging.<method>()' 형태의 bare 모듈 호출을 찾는다.
    예: logging.warning(...), logging.info(...) — 이는 import 없이 사용하면 NameError.
    Returns: [(node_type, line)] 리스트
    """
    try:
        tree = ast.parse(src_path.read_text(encoding='utf-8'))
    except (SyntaxError, UnicodeDecodeError):
        return []

    violations = []
    for node in ast.walk(tree):
        # logging.method(args) 형태: Attribute(value=Name(id='logging'), ...)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == 'logging'
        ):
            violations.append(f"line {node.lineno}: logging.{node.func.attr}()")
    return violations




# ===========================================================================
# Sprint 33 불변식 — DeviceSessionManager Driver 생명주기 SSOT
# ===========================================================================







# ===========================================================================
# Sprint A: power_judgment.py SSOT 불변식
# ===========================================================================





# ===========================================================================
# Sprint 37 불변식 — Phase 5
# ===========================================================================





















# ===========================================================================
# Structured Logging Overhaul 불변식
# ===========================================================================



# ===========================================================================
# Sprint B 불변식 — InstrumentPort 메서드 승격 검증
# ===========================================================================






    # Phase 5 WAL H-08 불변식은 Sprint 102에서 excel_wal_manager.py 삭제로 제거됨


# ===========================================================================
# Sprint A (file_path 제거 + BT_call 정리) 아키텍처 불변식
# ===========================================================================



# ===========================================================================
# Sprint 50 (Switchbox shared InstrumentPort) 아키텍처 불변식
# ===========================================================================



# ===========================================================================
# Sprint 51 (BTTesterPort + Dead Code + SSOT) 아키텍처 불변식
# ===========================================================================



# Sprint 52 (BT_call pyvisa 제거 + Bootstrap 완성) 아키텍처 불변식


# Sprint 53 (측정 모듈 SSOT — 상수/시그니처 정정) 아키텍처 불변식


# Sprint 53 UX — Bootstrap 비동기화 + GUI 진행 표시 아키텍처 불변식


# ===========================================================================
# Sprint 54 (테스트 시작 파이프라인 성능 최적화) 아키텍처 불변식
# ===========================================================================





# ===========================================================================
# Sprint 55 아키텍처 불변식 — 연결 폴백 추상화 검증
# ===========================================================================



# ===========================================================================
# Sprint 55 (Switchbox 고정 IP) 아키텍처 불변식
# ===========================================================================








    # 4+5. zip_sheet_patcher.py / batch_saver 불변식은 Sprint 102에서 해당 파일 삭제로 제거됨






# ===========================================================================
# Sprint 63: Notification 아키텍처 불변식
# ===========================================================================



# ===========================================================================
# Sprint 63: CrashGuard 아키텍처 불변식
# ===========================================================================



# ===========================================================================
# Sprint 64: ScrollRefactor 아키텍처 불변식
# ===========================================================================



# ===========================================================================
# Sprint 66: Keystring UI 제어 성능 최적화 아키텍처 불변식
# ===========================================================================









# ===========================================================================
# Sprint 67: scroll_and_find_by_xpath By.ID 최적화 불변식
# ===========================================================================



# ===========================================================================
# Sprint 67: WLANConfigurator 스피너 캐시 불변식
# ===========================================================================






# ===========================================================================
# Sprint 72 Bugfix: ET 폴백 제거 + openpyxl 이중 검증 불변식
# ===========================================================================



# ===========================================================================
# Sprint 69: StaleElementReferenceException 근본 해결 불변식
# ===========================================================================



# ===========================================================================
# Sprint 70 불변식 — Teams 알람 시스템 근본 개선
# ===========================================================================











# ===========================================================================
# Sprint 79 — 아키텍처 가드레일 (God Object / Protocol / Layer 순수성)
# ===========================================================================

def _collect_all_imports(src_path: Path) -> set[str]:
    """Python 파일에서 모든 import 패키지 이름을 수집한다 (함수 내 지연 import 포함)."""
    try:
        tree = ast.parse(src_path.read_text(encoding='utf-8'))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    packages = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                packages.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                packages.add(node.module.split('.')[0])
    return packages


def _has_unii_literal_collection(src_path: Path) -> bool:
    """파일에 'UNII-' 문자열을 포함하는 리스트/튜플/셋 리터럴이 있으면 True 반환."""
    try:
        tree = ast.parse(src_path.read_text(encoding='utf-8'))
    except (SyntaxError, UnicodeDecodeError):
        return False

    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for elt in node.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    if elt.value.startswith('UNII-'):
                        return True
    return False




# ════════════════════════════════════════════════════════════════════════════
# 자기 audit cascade 잔여 #12 정공 (2026-05-29) — GodObject self-enforcing
# extraction threshold (ADR-0001 PARAMETER_OBJECT_REVISIT_THRESHOLD 패턴 격상)
# ════════════════════════════════════════════════════════════════════════════
#
# 옛 `TestGodObjectGuard` 는 baseline 줄 수 (단순 magic 정수) 가 ratchet up
# 되는 것을 막지 않음 — 매 sprint 가 +50 줄을 baseline 에 누적하면 silent 비대
# 화. 사용자 추궁 또는 review-architecture 발견 전까지 추출 sprint trigger 없
# 음 (cascade fragility 형태).
#
# 본 sealed invariant 가 `tests/test_composition_wiring_drift_high2.py::
# TestParameterObjectRevisitThreshold` 패턴을 GodObject 영역에 격상 적용:
#   - growth_ratio 가 도메인 정합 산식 (initial baseline × 20% growth) 으로
#     derived constant 생성 (magic literal 0).
#   - 본 sprint 가 monitor 추가하는 module (`test_runner_init.py`, baseline 510)
#     이 threshold (612) 도달 시 본 invariant FAIL → 별 sprint 추출 강제.
#   - meaningful headroom 가드 (≥ 1 sprint window) — trivially-true 회피.
#
# 향후 모듈 추가는 `_EXTRACTION_MONITOR` dict 에 등재만 하면 자동 적용.



# ── ADR-0001 cost-model 격상 (자기 audit 추궁 P1-4 정공) ────────────────
#
# 옛 본 모듈은 `EXTRACTION_THRESHOLD_GROWTH_RATIO=1.20` 을 magic 으로 두고
# docstring 만으로 derived 라 주장 → ADR-0001 패턴의 진짜 격상 미달성.
#
# 본 산식 격상 — ADR-0001 의 cost-model crossover 패턴을 GodObject 영역에
# 정합 적용:
#
#   1. EXTRACTION_HELPER_LINES_TYPICAL: 본 프로젝트 실측 helper 분리 단위
#      (test_runner_init.py 의 `_audit_*_workflow_path` hook, file_naming 같은
#      추출 helper 평균 ~50줄). 실측 anchor — 향후 별 sprint 실측 데이터로
#      refine 가능 (ADR-0001 §Limitations L-1 패턴).
#
#   2. EXTRACTION_TARGET_MODULE_LINES: GodObject 추출 후 모듈 권장 크기 상한.
#      SOLID SRP + 본 프로젝트 baseline 통계: median baseline ~250줄
#      (test_runner_core 293 / test_runner_run 226 / sidebar_runtime ~200 등).
#
#   3. EXTRACTION_THRESHOLD_GROWTH_RATIO = 1 + (helper / target) = 1 + (50/250)
#      = 1.20. 의미: 모듈이 target 크기 (~250줄) 의 helper 1개 분리 비용
#      (50줄) 만큼 growth 한 시점 = extraction "self-pays" crossover.
#
# 본 산식이 magic 1.20 보다 도메인 정합 — 변경 시 두 component 중 하나의
# 도메인 추정이 변경된 것 (구조적 의미 보존).

#: 본 프로젝트 helper 모듈 분리 평균 크기 (실측 anchor — 별 sprint refine 가능).
#:
#: 측정 근거 (2026-05-29):
#:   - test_runner_init.py γ-#1 `_audit_ant_gain_workflow_path` 함수: ~50줄
#:   - test_runner_init.py γ-#3 `_audit_lookup_sheets_workflow_path`: ~60줄
#:   - screenshot_utils._io_path / atomic_write helper 등 평균 ~40-60줄
#:   → median ≈ 50줄
EXTRACTION_HELPER_LINES_TYPICAL: int = 50

#: GodObject 추출 후 모듈 권장 target 크기 (SOLID SRP 정합).
#:
#: 측정 근거 (2026-05-29 `_BASELINES` median):
#:   - test_runner_core.py: 293
#:   - test_runner_run.py: 226
#:   - headless_test_runner.py: 200
#:   → median ≈ 250 (200~300 범위)
EXTRACTION_TARGET_MODULE_LINES: int = 250

#: GodObject 추출 sprint trigger threshold growth ratio (**cost-model derived**).
#:
#: 산식 (magic literal 0):
#:   ratio = 1.0 + (HELPER_LINES_TYPICAL / TARGET_MODULE_LINES)
#:         = 1.0 + (50 / 250) = 1.20
#:
#: 의미: 모듈 크기가 baseline × 1.20 도달 = "helper 1개 분리하면 target 크기
#: 회복" 시점 = extraction cost 가 향후 maintenance cost 보다 작아지는 crossover.
#:
#: ADR-0001 § Self-Enforcing Guard 의 cost-model 패턴 정합 격상 (옛 magic 1.20
#: docstring 주장에서 derived 산식으로 진짜 격상 — 자기 audit P1-4 정공,
#: 2026-05-29). 두 component 변경 시 ratio 자동 재계산.
EXTRACTION_THRESHOLD_GROWTH_RATIO: float = 1.0 + (
    EXTRACTION_HELPER_LINES_TYPICAL / EXTRACTION_TARGET_MODULE_LINES
)

#: Monitored modules — file_path 만 등재. initial_baseline 은
#: `_baseline_lookup()` 로 `_BASELINES` ∪ `_INFRA_BASELINES` 에서 자동 derived
#: (자기 audit P0-2 정공 — 옛 dict-based 중복 magic 폐기, cross-file SSOT 통합).
#:
#: snapshot semantics: monitor 등재 시점의 baseline 을 anchor 로 보존하기 위해
#: `_EXTRACTION_MONITOR_SNAPSHOTS` 에서 immutable 값을 별도 유지. 본 frozenset
#: 은 "어떤 모듈을 monitor 할까" 정책 SSOT, snapshot 은 "언제부터 monitor
#: 시작했나" 의 historical anchor.
#:
#: 시스템 전반 확장 (자기 audit P1-5 정공) — test_runner_init 외 큰 baseline
#: 모듈도 monitor 활성:
_EXTRACTION_MONITOR_FILES: frozenset[str] = frozenset({
    # 자기 audit cascade #12 — ~50줄/sprint 성장 중 (γ-#1 ant gain / γ-#3
    # workflow path audit 누적). baseline 510 → threshold 612.
    'test_runner_init.py',
    # 자기 audit P1-5 — 1205 큰 baseline + dccf-cross-bw-share 등 지속 성장.
    'infrastructure/adapters/driven/sqlite_database_adapter.py',
    # 자기 audit P1-5 — Appium 원시함수 SSOT 810 + BLE/BT keystring 변경
    # 빈도 높음.
    'keystrings/keystring_base.py',
    # 자기 audit P1-5 시스템 전반 강제 (test_monitor_covers_top_baselines_
    # systemwide invariant surface) — 800+ 모든 baseline 등재:
    'keystrings/BLE_keystring.py',                                # 850
    'application/headless/api_contracts.py',                      # 895
    'reporting/infrastructure/adapters/ble_fcc_docx_patcher.py',  # 835
})

#: Immutable snapshots — monitor 등재 시점의 baseline 값 anchor (drift 가드).
#:
#: 본 dict 의 값은 **별 sprint extraction 완료 후에만** 갱신 (current baseline
#: 이 ratchet down 된 경우). 일반 sprint 가 본 값을 current baseline 에 맞춰
#: 갱신하면 trigger 가 self-defeat → invariant 가 이를 차단.
_EXTRACTION_MONITOR_SNAPSHOTS: dict[str, int] = {
    'test_runner_init.py': 510,
    'infrastructure/adapters/driven/sqlite_database_adapter.py': 1205,
    'keystrings/keystring_base.py': 810,
    'keystrings/BLE_keystring.py': 885,
    'application/headless/api_contracts.py': 895,
    'reporting/infrastructure/adapters/ble_fcc_docx_patcher.py': 835,
}


def _baseline_lookup(rel_path: str) -> int | None:
    """`_BASELINES` ∪ `_INFRA_BASELINES` 통합 lookup — 중복 magic 0 (P0-2 정공).

    `_EXTRACTION_MONITOR_FILES` 의 file_path 가 두 baseline dict 중 어느 쪽에
    있든 자동 매핑. cross-file SSOT 봉인.
    """
    baselines = TestGodObjectGuard._BASELINES
    infra = TestGodObjectGuard._INFRA_BASELINES
    if rel_path in baselines:
        return baselines[rel_path]
    if rel_path in infra:
        return infra[rel_path]
    return None




















# ===========================================================================
# Reporting Domain 순수성 — Sprint 116 (FCC 레포트 자동화)
# ===========================================================================

_REPORTING_DOMAIN_FORBIDDEN = _DOMAIN_FORBIDDEN | frozenset({'docx', 'docxtpl'})


def _find_reporting_domain_files() -> list[Path]:
    """src/reporting/domain/ 아래 모든 .py 파일을 반환한다."""
    domain_dir = project_root / 'src' / 'reporting' / 'domain'
    return list(domain_dir.rglob('*.py'))




# ===========================================================================
# Verdict SSOT — judge_margin 단일 정의 불변식
# ===========================================================================



# ===========================================================================
# Decimal-format SSOT (cross-tech preservation of template decimal places)
# ===========================================================================



# ===========================================================================
# Prime-required-value-cells eager-mark routine removed
# ===========================================================================



# ===========================================================================
# 9.2 OBW: Limit column never written by the patcher
# ===========================================================================



# ===========================================================================
# Cross-Tech Cell-Role Adoption — 2026-05-18 contract
# ===========================================================================




# ===========================================================================
# Reporting Condition Field Immutability — exec-plan 2026-05-17 §3.1
# ===========================================================================





# ===========================================================================
# Sprint F-2 — Session API (Phase 2) invariants
# ===========================================================================


















# ===========================================================================
# P0-3 — 측정 모듈 unit 명시 기록 AST 가드 (2026-05-25)
# ===========================================================================







# ===========================================================================
# Workflow SSOT — Cross-session Git Index Safety (2026-05-23)
# ===========================================================================






# ---------------------------------------------------------------------------
# Frontend seal ↔ ``invariant`` marker coverage (fe-data-layer-robustness M7,
# 2026-07-19)
# ---------------------------------------------------------------------------


class TestFrontendSealInvariantMarkerCoverage(unittest.TestCase):
    """``apps/web`` 봉인 pytest 가 CI 경량 레인에서 실제로 수집되는지 봉인.

    **결함 (D7)** — ``apps/web`` 의 React 표면은 backend-only pytest 11 파일로
    봉인돼 있는데, 그중 ``test_frontend_architecture_conformance.py`` 하나만
    ``tests/conftest.py::_INVARIANT_FILENAME_TOKENS`` 의 ``'conformance'`` 토큰에
    걸렸다. 나머지 9 파일(2026-07-19 실측)은 ``invariant`` 마커를 못 받아 ubuntu
    CI 경량 게이트(``pytest -m "invariant and not hardware and not gui and not
    bench"``, ``.github/workflows/backend-invariants.yml``)에서 **아예 수집되지
    않았다**. 프론트 봉인이 CI 에서 안 돌았으니 W1 이 고치는 런타임 결함들도
    다음 PR 에서 조용히 회귀할 수 있었다.

    **정공** — conftest 토큰 SSOT 에 ``frontend`` / ``fe_phase`` / ``apps_web``
    3 토큰을 추가한다. 다만 토큰 추가만으로는 *다음* 프론트 봉인이 다른 명명으로
    들어올 때 같은 구멍이 다시 열리므로, 본 invariant 가 (1) 프론트 봉인 파일을
    파일시스템에서 발견하고 (2) 각 파일이 conftest SSOT 토큰에 실제로 매칭되는지
    단언한다. 판정 로직은 conftest 를 복제하지 않고 **import** 해 SSOT 를 단일
    유지한다.

    본 클래스가 별도 파일이 아니라 여기 사는 이유: 신규 ``tests/test_*.py``
    invariant 파일은 verify-* skill 매핑(``.claude/skills`` / ``.claude/rules``)을
    함께 등록해야 ``TestInvariantSkillMappingDrift`` 가 통과하는데, 그 경로는 본
    세션의 쓰기 범위 밖이다. 이 파일은 이미 매핑돼 있고 ``'conformance'`` 토큰으로
    같은 CI 레인에서 돈다 — 봉인이 스스로를 봉인한다.
    """

    #: 프론트(apps/web) 봉인 pytest 의 명명 패밀리 SSOT. 새 프론트 봉인은 이 접두사
    #: 중 하나를 따르게 하고(그러면 conftest 토큰이 자동으로 잡는다), 따르지 않는
    #: 이름이 필요하면 conftest 토큰과 본 목록을 **함께** 갱신해야 한다.
    FRONTEND_SEAL_PREFIXES = (
        'test_frontend_',
        'test_fe_phase',
        'test_apps_web_',
    )

    #: D7 발견 시점(2026-07-19)의 프론트 봉인 파일 수. ratchet-up 전용 — 파일이
    #: 늘어나는 것은 정상이고, 줄어들면 봉인이 삭제된 것이므로 명시적 갱신을 요구한다.
    FRONTEND_SEAL_FILE_FLOOR = 10

    #: M7 이 추가한 토큰 — 제거되면 프론트 봉인이 다시 CI 에서 빠진다.
    REQUIRED_TOKENS = ('frontend', 'fe_phase', 'apps_web')

    @staticmethod
    def _invariant_tokens():
        from conftest import _INVARIANT_FILENAME_TOKENS

        return _INVARIANT_FILENAME_TOKENS

    @classmethod
    def _frontend_seal_files(cls):
        tests_dir = Path(__file__).parent
        return sorted(
            path
            for path in tests_dir.glob('test_*.py')
            if path.name.startswith(cls.FRONTEND_SEAL_PREFIXES)
        )

    def test_frontend_seal_files_discovered(self):
        found = self._frontend_seal_files()
        self.assertGreaterEqual(
            len(found),
            self.FRONTEND_SEAL_FILE_FLOOR,
            '프론트 봉인 파일이 floor 아래로 줄었다 — 봉인 삭제 여부 확인 후 '
            'FRONTEND_SEAL_FILE_FLOOR 를 명시적으로 갱신하라: '
            f'{[p.name for p in found]}',
        )

    def test_every_frontend_seal_matches_an_invariant_token(self):
        tokens = self._invariant_tokens()
        missing = [
            path.name
            for path in self._frontend_seal_files()
            if not any(token in path.stem for token in tokens)
        ]
        self.assertEqual(
            missing,
            [],
            '프론트 봉인인데 invariant 마커를 못 받는 파일 — CI 경량 레인에서 '
            '수집되지 않는다. tests/conftest.py::_INVARIANT_FILENAME_TOKENS 에 '
            f'토큰을 추가하라: {missing}',
        )

    def test_new_token_families_are_registered_in_conftest(self):
        tokens = self._invariant_tokens()
        for token in self.REQUIRED_TOKENS:
            self.assertIn(
                token,
                tokens,
                f"conftest 토큰 SSOT 에서 '{token}' 이 사라졌다 — 프론트 봉인이 "
                '다시 CI 에서 빠진다 (D7 회귀).',
            )




# ---------------------------------------------------------------------------
# ``occurred_at`` wire format ↔ frontend ordering guard
# (fe-data-layer-robustness M2 supporting seal, 2026-07-19)
# ---------------------------------------------------------------------------




















if __name__ == '__main__':
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.TestLoader().loadTestsFromModule(
        __import__(__name__)
    ))
    sys.exit(0 if result.wasSuccessful() else 1)
