"""
tests/conftest.py — pytest 공통 설정 (Sprint 30 ~ Sprint 80)

Sprint 30: src/ 디렉토리를 sys.path에 추가하여 어느 디렉토리에서 pytest를 실행해도
모든 테스트 파일이 올바르게 모듈을 import할 수 있도록 중앙화합니다.

Sprint 32: collect_ignore로 하드웨어 전용 파일 자동 제외.
pytest_collection_modifyitems로 unit/integration/hardware 마커 자동 분류.
--ignore 플래그 없이 `python -m pytest ../tests/ -q` 만으로 전체 실행 가능.

Sprint 80: make_ctx fixture 추가 — WorkflowContext 팩토리 SSOT.
  각 workflow 테스트 파일의 로컬 _make_ctx() 함수를 대체한다.
  DB 마이그레이션 Phase 2에서 db_adapter 필드가 WorkflowContext에 추가되면
  defaults에 FakeDatabaseAdapter() 추가만으로 전 테스트 호환.
"""
import sys
import pytest
from pathlib import Path

# tests-percentile-ssot-migration Phase 11 (2026-05-23, 2차 자평) — 4 path 등재
# loop 통합 (옛 4 회 ``if X not in sys.path`` 반복 DRY 위반 영구 폐기). scripts/
# 등재는 Phase 7 (1차 자평) 이 도입 — 모든 테스트가 `from benchmark_harness import
# percentile` 직접 사용. 각 테스트 모듈의 boilerplate 폐기 + conftest 자체 DRY 통합.
_PROJECT_ROOT = Path(__file__).parent.parent
for _path in (
    str(_PROJECT_ROOT),
    str(_PROJECT_ROOT / 'src'),
    str(Path(__file__).parent),
    str(_PROJECT_ROOT / 'scripts'),
):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Deployment config inherited from the shell is quarantined before collection.
# The mechanism, and why it is a `tests/support/` module rather than code in this
# file, are documented in `tests/support/ambient_env.py` — importing it here is
# what makes it run early enough.
from support.ambient_env import (  # noqa: E402,F401
    AMBIENT_CONFIG_ENV_NAMES,
    AMBIENT_CONFIG_ENV_PREFIXES,
    QUARANTINED,
    ambient_config_env,
    scrub_ambient_config_env,
)

# 하드웨어 필수 파일 — import 시점에 실패하므로 수집 자체를 건너뜀
collect_ignore = ['test_correction_real_device.py']

# 통합 테스트 모듈명 집합 (tempfile, 실제 Excel I/O 사용)
_INTEGRATION_TESTS = {
    # Sprint 102: test_wal_integration, test_wal_json_fix, test_wal_error_recovery,
    #             test_batch_optimization 삭제됨 (WAL/batch_save 레거시 제거)
    'test_operation',
    'test_fixed_workflow',
    'test_device_transfer_debug',
    'test_correction2_debug',
    'test_correction_cache',
    'test_correction_system',
    'test_correction_caching',
    'test_integration_workflows',    # Sprint 49: Workflow 통합 테스트
}

# 하드웨어 필수 모듈명 집합 (Appium, 실제 기기, 실제 분석기)
_HARDWARE_TESTS = {
    'test_appium_server',
    'test_wlan_workflow',
    'test_correction_real_device',
}


# NOTE (gate-and-deploy-path-parity, 2026-08-01) — the ``bench`` marker is NOT
# auto-assigned here. It used to be, from a class-name substring test
# (``'Latency' in name or 'Bench' in name``), which failed in three directions
# at once: it missed ``TestWalLockContentionBudget`` (says "Budget"), it could
# not see a module-level timed test at all (no class to key on), and it dragged
# load-independent seals like ``TestBenchScriptsCallable`` out of the routine
# lane. A substring test over names is a hand-maintained allowlist that happens
# to live in a regex. Tests now declare ``@pytest.mark.bench`` themselves, and
# ``tests/test_bench_marker_autoassign.py`` fails when a test whose verdict
# depends on wall-clock time does not carry one. Do not reintroduce name-based
# classification here — that file asserts this module never adds the marker.


# Per-module cache for the ``gui`` auto-marker (one classification per module).
_gui_module_cache: dict[str, bool] = {}


def _module_is_gui(module) -> bool:
    """Auto-tag GUI test modules so they can be split out of the routine run.

    **Why this split exists (2026-05-26).** Tests that create a ``QApplication``
    / QWidgets and pump a *real* Qt event loop (``processEvents`` / a shown
    QProgressDialog, e.g. ``test_progress_dialog_coordinator_gui_ar6``) crash the
    whole process with a fatal Windows access violation (exit code 139) once the
    full suite has accumulated enough native pressure from the heavy non-GUI
    tests (``test_platform_*`` etc.). The crash surfaces ~70% into the run, so the
    regression never finishes, and ``-p no:faulthandler`` hides the diagnostic.
    Removing one offending test only hops the crash to the next Qt event-pump
    test — so the fix is to run all GUI tests as their own pass (``-m gui``),
    where they pass reliably, and exclude them from the routine bulk baseline
    (``-m "not hardware and not bench and not gui"``).

    Detection is by *namespace membership*: a module is GUI iff one of its module
    globals is an object imported from ``PySide6`` (a QApplication/widget/Signal
    etc.). This auto-classifies new GUI tests with no hand-maintained list, and
    correctly does NOT match AST-only architecture/purity tests that merely
    reference the *string* ``'PySide6'`` (they import no Qt object).
    """
    name = getattr(module, '__name__', '')
    cached = _gui_module_cache.get(name)
    if cached is not None:
        return cached
    # (1) naming convention — every ``*_gui_*`` module is a GUI test, even when it
    # imports PySide6 lazily inside a fixture (namespace check below would miss it).
    result = '_gui_' in name
    if not result:
        # (2) namespace membership — a module global imported from PySide6 (a
        # QApplication/widget/Signal). Catches GUI tests that don't follow the
        # naming convention (test_saving_progress_dialog, test_qt_signal_event_bridge,
        # test_session_select_dialog, ...) while NOT matching AST-only architecture
        # tests that merely reference the string 'PySide6'.
        for value in vars(module).values():
            owner = getattr(value, '__module__', None) or getattr(value, '__name__', None)
            if isinstance(owner, str) and owner.startswith('PySide6'):
                result = True
                break
    _gui_module_cache[name] = result
    return result


#: Provider-shaped fixtures, loaded by presence rather than by import.
#:
#: This file is the floor of what a delivered suite must resolve — pytest loads
#: it whether or not anything imports it — so an import here is an import every
#: lane inherits. ``fcc-test-contracts`` is dependency-free by contract and that
#: verdict is a real separate-interpreter import of the delivered tree, so three
#: fixtures reaching PySide6 / ``infrastructure.correction`` / ``domain.ports``
#: were what kept that lane from carrying its own suite.
#:
#: Registered as a **string**, which is also why the delivery closure does not
#: drag it into a box: that closure reads AST imports, and a plugin name is not
#: one. See ``tests/support/harness_provider.py``.
_PROVIDER_HARNESS_PLUGIN = 'support.harness_provider'


def pytest_configure(config):
    """Register the provider harness when this tree actually carries it.

    Presence, never ``try/except ImportError``. Absence is a *delivery fact* —
    a dependency-free lane ships without it on purpose. An ImportError raised
    *inside* the plugin is a defect, and one ``except`` cannot tell those apart:
    it would swallow the second in order to tolerate the first and leave the
    monorepo silently missing 19 test modules' worth of fixtures. Same argument
    ``contract_cli.sibling_module`` already makes about guessing at import
    failures.

    ``pytest_plugins`` cannot express this: pytest rejects it in a non-rootdir
    conftest, and ``tests/`` is not the rootdir here.
    """
    if (Path(__file__).parent / 'support' / 'harness_provider.py').is_file():
        config.pluginmanager.import_plugin(_PROVIDER_HARNESS_PLUGIN)


@pytest.fixture(autouse=True)
def _isolate_trace_sampler_contextvar():
    """``TRACEPARENT_SAMPLER_CTX`` 누수 격리 (테스트 간 결정론).

    근본 원인 — ``install_sampler_from_env()`` 는 token reset 없이
    ``TRACEPARENT_SAMPLER_CTX`` 를 영구 ``set`` 한다. 이는 **프로덕션에서 의도된 설계**
    (app-boot 1회 영구 설정 — ``trace_sampler.py`` 주석 SSOT)이므로 그 함수를 token
    기반으로 바꾸면 프로덕션 시멘틱이 깨진다. 그러나 ``session_api_app.create_app`` /
    ``headless_api_app.create_app`` 이 이를 호출하므로, ``create_app`` 을 부르는 (멀티-app)
    FastAPI 어댑터 테스트 수십 개가 이 ContextVar 를 오염시킨다. 정리 책임이 분산되어
    개별 tearDown 으로는 못 막으므로 pytest-wide autouse 격리가 업계 표준 정공이다.
    오염 시 후속 ``test_trace_sampler_p1_b`` (``current_sampler() == DEFAULT_SAMPLER``
    기대)가 전체 회귀에서만 비결정적으로 실패한다 (개별 실행은 통과). default 가 ``None``
    (``current_sampler`` 가 ``None`` → ``DEFAULT_SAMPLER`` 해석)이라 ``None`` 복원이 곧
    default 복원이다.

    ``PARENT_SAMPLED_CTX`` 는 의도적으로 reset 하지 않는다 — 유일한 set 경로
    (``correlation.bind_trace_context``)가 context manager finally 에서 token 기반
    ``reset`` 을 항상 수행하므로 누수원이 없다 (검증: ``PARENT_SAMPLED_CTX.set`` grep =
    correlation.py 단일 token-기반 사이트). 방어적 reset 은 불필요해 제거.
    """
    yield
    try:
        from fcc_test_contracts.common.trace_sampler import TRACEPARENT_SAMPLER_CTX
        TRACEPARENT_SAMPLER_CTX.set(None)
    except Exception:
        pass


#: ``invariant`` marker 자동 부착 SSOT — 파일명 substring 토큰.
#:
#: architecture / hygiene / SSOT / contract-drift 가드 (장비·무거운 측정 로직
#: 없이 dataclass/AST/파일시스템/frontmatter 검사 위주)를 한 marker 로 묶어
#: ubuntu CI 경량 게이트 ``pytest -m "invariant and not hardware and not gui
#: and not bench"`` 대상으로 만든다 (.github/workflows/backend-invariants.yml).
#: 측정/워크플로 테스트(test_psd / test_duty / test_occupancy …)는 토큰에 걸리지
#: 않는다 — specific token 만 등재. 새 invariant 파일은 명명 규칙(``*_invariants``
#: / ``*_conformance`` / ``*_hygiene`` / ``*_drift`` …)만 따르면 자동 포함된다.
_INVARIANT_FILENAME_TOKENS = (
    'invariant',          # *_invariants, impact_tests_invariant_coverage, invariant_skill_mapping
    'conformance',        # architecture_conformance, sprint*_architecture_invariants
    'hygiene',            # documentation_hygiene, repository_hygiene
    'drift',              # invariant_skill_mapping_drift, composition_wiring_drift
    'render_generators',  # render_generators_sync (generator SSOT freshness)
    'runbook',            # headless_api_runtime_runbook
    'trace_sampler',      # trace_sampler_p1_b/c (W3C sampler SSOT)
    'wal_checkpoint',     # wal_checkpoint_durability (raw sqlite3 ratchet + PRAGMA SSOT)
    'oidc',               # oidc_principal_resolver
    'structured_logging', # structured_logging_system
    # ─── apps/web frontend seals (fe-data-layer-robustness M7, 2026-07-19) ───
    # 10 backend-only pytest files seal the `apps/web` React surface (query-key
    # SSOT / architecture conformance / i18n parity / auth robustness / optimistic
    # mutation / phase scaffolds). Only ONE of them (`*_conformance`) matched a
    # token before this change, so the other nine never ran in the ubuntu CI
    # lightweight lane `-m "invariant and not hardware and not gui and not bench"`
    # — a frontend regression could land green. These three tokens cover the
    # `test_frontend_* / test_fe_phase* / test_apps_web_*` naming families, and
    # `test_architecture_conformance.py::TestFrontendSealInvariantMarkerCoverage`
    # is the meta invariant that fails when a file in those families stops being
    # collected — so the hole cannot silently reopen under a new naming scheme.
    'frontend',           # test_frontend_{query_key_ssot,i18n_parity,auth_robustness,…}
    'fe_phase',           # test_fe_phase1_ui_foundation, test_fe_phase2_*
    'apps_web',           # test_apps_web_{scaffold,auth_scaffold}
    # ─── `*_axis` seal family (2026-08-19) ───
    # Waves that seal a single decision axis have been naming their file after
    # the axis (`test_chamber_equipment_config_axis`, `test_plot_storage_root_axis`,
    # `test_repository_artifact_depth_axis`, `test_chamber_storage_root_axis`).
    # None of those matched a token, so four architecture seals never ran in the
    # ubuntu CI lightweight lane — the SAME hole the frontend families hit above,
    # reopened under a new naming habit. Renaming the files would have fixed
    # today's four and left the next `*_axis.py` out again, so the FAMILY is
    # registered instead. `TestAxisSealInvariantMarkerCoverage` in
    # test_architecture_conformance.py fails if a file in this family stops
    # being collected.
    'axis',               # test_{chamber_equipment_config,plot_storage_root,…}_axis
    # ─── claim-governance guards (2026-08-27, round 4b) ───
    # A fifth independent reviewer measured that `tests/test_supervisor_preflight.py`
    # — the gate that judges whether a claim declared a path before editing it —
    # matched NO token, so `-m invariant` deselected it, and it is not in the
    # impact subset a claim edit derives either. Both halves of that are the same
    # hole the frontend and `*_axis` families hit above: the gate existed, ran
    # green when somebody ran it by hand, and no lane selected it. A red landed
    # under a 17-item self-audit because of it. `test_central_postgres_preflight`
    # joins on the same token and is lane-safe (3 passed, 0.16s, no DSN needed).
    'preflight',          # test_{supervisor,central_postgres}_preflight
)


def pytest_collection_modifyitems(items):
    """테스트 항목에 unit/integration/hardware/invariant/gui 마커 자동 부여.

    ``bench`` 는 **여기서 부여하지 않는다** (2026-08-01 변경). 옛 클래스명
    substring 휴리스틱은 ``TestWalLockContentionBudget`` 처럼 이름이 다른
    예산 테스트를 놓치고, 클래스 없는 모듈 레벨 timed 테스트는 원리적으로
    보지 못하며, ``TestBenchScriptsCallable`` 같은 부하 비의존 봉인을 routine
    에서 빼앗았다. 이제 테스트가 ``@pytest.mark.bench`` 를 직접 선언하고,
    ``tests/test_bench_marker_autoassign.py`` 의 검출기가 *선언해야 하는데
    안 한 것*을 실패로 잡는다 — 분류기가 아니라 검출기다.

    ``invariant`` (2026-06-03 추가): architecture/hygiene/SSOT/contract-drift
    가드를 묶어 ubuntu CI 경량 게이트로 분리 (``_INVARIANT_FILENAME_TOKENS``
    SSOT). 전체 회귀에 누적되므로 로컬 3-pass 는 그대로, CI 는 이 marker 만 빠르게.
    """
    for item in items:
        module_name = item.module.__name__.rsplit('.', 1)[-1]
        if module_name in _HARDWARE_TESTS:
            item.add_marker(pytest.mark.hardware)
        elif module_name in _INTEGRATION_TESTS:
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.unit)

        if any(tok in module_name for tok in _INVARIANT_FILENAME_TOKENS):
            item.add_marker(pytest.mark.invariant)

        # ``gui`` (2026-05-26): split real-Qt-event-pump tests out of the routine
        # bulk run — they segfault (exit 139) under accumulated native pressure
        # but pass on their own via ``-m gui``. See ``_module_is_gui``.
        if _module_is_gui(item.module):
            item.add_marker(pytest.mark.gui)
