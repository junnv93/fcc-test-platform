"""Tests for the repository-local frontend production build gate."""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'frontend_build_gate.py'
WEB = ROOT / 'apps' / 'web'

sys.path.insert(0, str(ROOT / 'scripts'))
import frontend_build_gate as gate  # noqa: E402


def _run_real_gate() -> subprocess.CompletedProcess[str]:
    # ⚠️ This helper used to ask "is fnm installed?" and fall back to the ambient
    # interpreter when it was not -- so on a box carrying a different version
    # manager it ran the real gate under whatever Node happened to be default,
    # and the resulting engine rejection read as a gate defect.  It now asks the
    # module that owns runtime selection, which is the same question the
    # provisioning script asks.
    bin_dir = gate.find_satisfying_node_bin()
    if bin_dir is None:
        pytest.skip(
            'no installed Node satisfies apps/web engines.node on this box; '
            'install one (nvm/fnm) to exercise the real production gate'
        )
    child_env = dict(os.environ)
    child_env['PATH'] = f'{bin_dir}{os.pathsep}{child_env.get("PATH", "")}'
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        env=child_env,
    )


def _skip_only_for_missing_tool(result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode != 0 and 'MISSING_TOOL:' in result.stderr:
        pytest.skip(f'explicit missing frontend tool: {result.stderr.strip()}')


def test_gate_resolves_repo_and_invokes_shell_free_npm_build() -> None:
    source = SCRIPT.read_text(encoding='utf-8')
    tree = ast.parse(source)
    assert 'Path(__file__).resolve().parents[1]' in source
    assert 'WEB_ROOT = REPO_ROOT / \'apps\' / \'web\'' in source
    assert 'npm_executable, \'run\', \'build\'' in source
    assert sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.keyword)
        and node.arg == 'shell'
        and isinstance(node.value, ast.Constant)
        and node.value.value is False
    ) >= 2


def test_build_failure_is_nonzero_and_actionable(capsys: pytest.CaptureFixture[str]) -> None:
    failed = subprocess.CompletedProcess(
        args=['npm', 'run', 'build'], returncode=23, stdout='', stderr='vite failed'
    )
    with patch.object(gate.subprocess, 'run', return_value=failed) as run:
        assert gate._run_build('npm') == 23
    captured = capsys.readouterr()
    assert 'FRONTEND_BUILD_GATE_ERROR:' in captured.err
    assert 'npm run build failed' in captured.err
    assert run.call_args.kwargs['cwd'] == gate.WEB_ROOT
    assert run.call_args.kwargs['shell'] is False
    assert run.call_args.args[0] == ['npm', 'run', 'build']


def test_missing_tool_is_explicitly_classified_not_as_build_success(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def missing(_name: str, *, label: str) -> str:
        raise gate.GateFailure(f'{label} unavailable', missing_tool=True)

    with patch.object(gate, '_find_executable', side_effect=missing):
        assert gate.main() == 2
    assert capsys.readouterr().err.startswith('MISSING_TOOL:')


def test_wrong_node_version_is_not_an_explicit_skip(
    capsys: pytest.CaptureFixture[str],
) -> None:
    probes = [
        subprocess.CompletedProcess(
            args=['node', '--version'], returncode=0, stdout='v24.14.0\n', stderr=''
        ),
        subprocess.CompletedProcess(
            args=['npm', '--version'], returncode=0, stdout='11.11.1\n', stderr=''
        ),
    ]
    with (
        patch.object(gate, '_find_executable', side_effect=['node', 'npm']),
        patch.object(gate, '_run_capture', side_effect=probes),
    ):
        assert gate.main() == 2
    error = capsys.readouterr().err
    assert error.startswith('FRONTEND_BUILD_GATE_ERROR:')
    assert 'MISSING_TOOL:' not in error


def test_real_gate_runs_the_actual_production_build() -> None:
    result = _run_real_gate()
    _skip_only_for_missing_tool(result)
    assert result.returncode == 0, result.stderr or result.stdout
    combined = f'{result.stdout}\n{result.stderr}'.lower()
    assert 'vite' in combined
    assert (WEB / 'dist' / 'index.html').is_file()


# ── The runtime-selection axis ────────────────────────────────────────────────
# Every check below exists because "the pinned Node is declared" and "the pinned
# Node is what actually runs" were the same value on every box that happened to
# carry fnm, and different values on the first box that did not.


def test_required_major_is_derived_from_package_json_not_a_literal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert gate.main([gate.PRINT_NODE_MAJOR_FLAG]) == 0
    assert capsys.readouterr().out.strip() == '22'

    # Non-vacuity: move the declaration and the answer must move with it. A test
    # that only asserts '22' passes just as well against a hardcoded '22'.
    moved = tmp_path / 'package.json'
    moved.write_text(json.dumps({'engines': {'node': '>=99.1 <100', 'npm': '>=10.9'}}))
    with patch.object(gate, 'PACKAGE_JSON', moved):
        assert gate.main([gate.PRINT_NODE_MAJOR_FLAG]) == 0
        assert capsys.readouterr().out.strip() == '99'


def test_check_engines_stops_before_the_build() -> None:
    def exploding_build(_npm: str) -> int:
        raise AssertionError('--check-engines must not run the production build')

    with patch.object(gate, '_find_executable', return_value='/usr/bin/true'), \
            patch.object(gate, '_check_engine', return_value=None), \
            patch.object(gate, '_run_build', side_effect=exploding_build):
        assert gate.main([gate.CHECK_ENGINES_FLAG]) == 0
        # Non-vacuity: the same stubs *do* reach the build without the flag.
        with pytest.raises(AssertionError):
            gate.main([])


def test_unsupported_argument_is_rejected_rather_than_ignored(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert gate.main(['--not-a-flag']) == 2
    assert "'--not-a-flag'" in capsys.readouterr().err


def test_a_library_call_never_reads_process_argv() -> None:
    # `main()` with no argument means "no arguments", never "read sys.argv".
    # Under pytest sys.argv carries the runner's own flags, and reading it there
    # let one process's arguments leak into a library call -- observed as this
    # module's own regression on 2026-08-30.
    with patch.object(sys, 'argv', ['pytest', '-q', '--tb=short']), \
            patch.object(gate, '_find_executable', return_value='/usr/bin/true'), \
            patch.object(gate, '_check_engine', return_value=None), \
            patch.object(gate, '_run_build', return_value=0):
        assert gate.main() == 0


def test_selection_verifies_each_candidate_instead_of_trusting_a_manager(
    tmp_path: Path,
) -> None:
    unusable = tmp_path / 'unusable' / 'bin'
    unusable.mkdir(parents=True)

    real_candidates = gate._candidate_node_bins

    def candidates(major: int):
        yield 'bogus', unusable
        yield from real_candidates(major)

    with patch.object(gate, '_candidate_node_bins', candidates):
        chosen = gate.find_satisfying_node_bin()
    # A directory with no node in it must never be selected, however early it is
    # offered; selection is decided by probing, not by candidate order.
    assert chosen != unusable

    # Non-vacuity: when every candidate is unusable the answer is None, not the
    # first one offered.
    with patch.object(gate, '_candidate_node_bins', lambda _major: iter([('bogus', unusable)])):
        assert gate.find_satisfying_node_bin() is None


# ⚠️ `test_provision_script_delegates_selection_and_pins_no_literal_version` 는 2026-08-31 에 `fcc-test-platform` 으로 옮겼다 — 소비하는
#    대상(apps/web · packages/api-artifacts)이 그 레포에 있다. 여기 두면 영구 red 다.

