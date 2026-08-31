# ⚠️ 2026-08-31: 이 파일은 모노레포 `tests/test_frontend_build_gate.py` 에서 갈라져 왔다. 남은 것은
#    소비 대상이 이 레포에 있는 단위(test_provision_script_delegates_selection_and_pins_no_literal_version)뿐이고,
#    나머지 형제 검사와 그것들만 쓰던 import 는 저쪽에 남았다.
"""Tests for the repository-local frontend production build gate."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

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












# ── The runtime-selection axis ────────────────────────────────────────────────
# Every check below exists because "the pinned Node is declared" and "the pinned
# Node is what actually runs" were the same value on every box that happened to
# carry fnm, and different values on the first box that did not.












def test_provision_script_delegates_selection_and_pins_no_literal_version() -> None:
    script = (ROOT / 'scripts' / 'provision_review_worktree.sh').read_text(encoding='utf-8')

    assert gate.PRINT_SATISFYING_NODE_BIN_FLAG in script, (
        'the provisioning script must ask the gate for a runtime rather than '
        'searching for version managers itself'
    )

    code_lines = [
        line for line in script.splitlines() if not line.lstrip().startswith('#')
    ]
    offenders = [line for line in code_lines if '--using=' in line]
    assert not offenders, (
        'executable lines must not pin a Node version literal; the contract '
        f'lives in apps/web/package.json. Offenders: {offenders}'
    )

    assert 'REFUSING' in script and 'exit 1' in script, (
        'an unsatisfiable runtime must stop provisioning, not emit a warning '
        'that scrolls past while every lane runs under the wrong major'
    )
