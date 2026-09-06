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

from fcc_test_kernel.domain.models.enums import MeasurementType

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
# 삭제됨 (2026-09-06) — GodObject 추출 threshold 감시 장치 155줄
# ════════════════════════════════════════════════════════════════════════════
#
# 여기에는 `EXTRACTION_HELPER_LINES_TYPICAL` · `EXTRACTION_TARGET_MODULE_LINES` ·
# `EXTRACTION_THRESHOLD_GROWTH_RATIO` · `_EXTRACTION_MONITOR_FILES` ·
# `_EXTRACTION_MONITOR_SNAPSHOTS` · `_baseline_lookup()` 이 있었다.
#
# ── 왜 지웠나: 대상이 떠났다 (전부 실측 2026-09-06) ──────────────────────────
# ① `_baseline_lookup()` 이 읽던 SSOT 가 이 레포에 없다. 그 함수 본문의
#    `TestGodObjectGuard._BASELINES` / `._INFRA_BASELINES` 에서 `TestGodObjectGuard`
#    는 이 레포 어디에도 정의되지 않는다 — ruff F821 2건이 그것이었다.
# ② 감시 대상 6개 모듈이 **전부 이 레포에 없다**: `test_runner_init.py`,
#    `infrastructure/adapters/driven/sqlite_database_adapter.py`,
#    `keystrings/keystring_base.py`, `keystrings/BLE_keystring.py`,
#    `application/headless/api_contracts.py`,
#    `reporting/infrastructure/adapters/ble_fcc_docx_patcher.py`.
#    이 레포에는 `src/` 트리 자체가 없다.
# ③ 장치 전체가 **닫힌 죽은 고리**였다. .py 441개 AST 전수 조사 결과, 위 여섯 이름은
#    서로만 참조하고 바깥 소비자가 0개다. 주석이 약속한
#    `test_monitor_covers_top_baselines_systemwide` 도 이 레포에 없다.
#
# ── 그래서 「지운다」가 「잃는다」가 아닌 이유 ────────────────────────────────
# 이 장치는 모노레포 `tests/test_architecture_conformance.py` 에 **소비자와 함께**
# 온전히 살아 있다 (`TestGodObjectGuard` + `_baseline_lookup` 을 실제로 부르는
# 검사 3건). 감시 대상 6개 모듈도 그쪽 트리에 있다. 즉 이사는 이미 끝났고,
# 여기 남아 있던 것은 소비자만 잘려 나간 잔해다. 대상이 없는 곳에서 이것을
# 「되살리는」 방법은 없다 — 없는 모듈의 baseline 을 지키는 봉인은 정의상 공허하다.
#
# ⚠️ 이 레포에 `src/` 가 생기고 저 모듈들이 따라오는 날, 복원처는 잔해가 아니라
#    **모노레포의 온전한 판**이다. 그래서 위치를 여기 이름으로 적어 둔다.



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
