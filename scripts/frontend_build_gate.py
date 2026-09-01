"""Run the repository-local production frontend build.

This gate is intentionally a Python entrypoint so the backend verification
lanes can invoke the same command without relying on a CI-only workflow.  It
resolves the repository from this file, checks the ``apps/web`` package engine
contract with the active Node/npm pair, and then executes the real
``npm run build`` without a shell.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = REPO_ROOT / 'apps' / 'web'
PACKAGE_JSON = WEB_ROOT / 'package.json'
NODE_MODULES = WEB_ROOT / 'node_modules'

_VERSION_RE = re.compile(r'(?<!\d)v?(\d+)(?:\.(\d+))?(?:\.(\d+))?')
_ENGINE_RE = re.compile(r'(>=|<)\s*(\d+(?:\.\d+){0,2})')


class GateFailure(RuntimeError):
    """A diagnosable gate failure, optionally caused by a missing tool."""

    def __init__(self, message: str, *, missing_tool: bool = False) -> None:
        super().__init__(message)
        self.missing_tool = missing_tool


def _version_tuple(raw: str, *, label: str) -> tuple[int, int, int]:
    match = _VERSION_RE.search(raw.strip())
    if match is None:
        raise GateFailure(
            f'{label} reported an unreadable version {raw!r}; '
            f'install a supported {label} and retry'
        )
    return tuple(int(part or 0) for part in match.groups())


def _engine_bounds(requirement: object, *, label: str) -> tuple[
    tuple[int, int, int], tuple[int, int, int] | None
]:
    if not isinstance(requirement, str):
        raise GateFailure(
            f'{PACKAGE_JSON} must declare a string engines.{label} requirement'
        )
    matches = _ENGINE_RE.findall(requirement)
    lower: tuple[int, int, int] | None = None
    upper: tuple[int, int, int] | None = None
    for operator, version in matches:
        parsed = _version_tuple(version, label=f'engines.{label}')
        if operator == '>=':
            lower = parsed
        elif operator == '<':
            upper = parsed
    if lower is None:
        raise GateFailure(
            f'unsupported engines.{label} requirement {requirement!r}; '
            'expected an explicit >= lower bound'
        )
    return lower, upper


def _load_engine_bounds() -> tuple[
    tuple[int, int, int], tuple[int, int, int] | None,
    tuple[int, int, int], tuple[int, int, int] | None,
]:
    try:
        payload = json.loads(PACKAGE_JSON.read_text(encoding='utf-8'))
    except OSError as exc:
        raise GateFailure(f'cannot read {PACKAGE_JSON}: {exc}') from exc
    except json.JSONDecodeError as exc:
        raise GateFailure(f'{PACKAGE_JSON} is not valid JSON: {exc}') from exc

    engines = payload.get('engines') if isinstance(payload, dict) else None
    if not isinstance(engines, dict):
        raise GateFailure(f'{PACKAGE_JSON} must declare an engines object')
    node_bounds = _engine_bounds(engines.get('node'), label='node')
    npm_bounds = _engine_bounds(engines.get('npm'), label='npm')
    return (*node_bounds, *npm_bounds)


def _find_executable(name: str, *, label: str) -> str:
    executable = shutil.which(name)
    if executable is None and name == 'npm':
        # Windows exposes npm as npm.cmd; keeping the fallback here preserves
        # the same shell-free invocation on both platforms.
        executable = shutil.which('npm.cmd')
    if executable is None:
        raise GateFailure(
            f'{label} executable is missing from PATH; activate a Node that '
            f'satisfies {PACKAGE_JSON} engines and retry '
            '(scripts/provision_review_worktree.sh selects one automatically)',
            missing_tool=True,
        )
    return executable


def _run_capture(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=WEB_ROOT,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise GateFailure(
            f'could not execute {command[0]!r}: {exc}', missing_tool=True
        ) from exc


def _check_engine(
    executable: str,
    *,
    label: str,
    bounds: tuple[tuple[int, int, int], tuple[int, int, int] | None],
) -> None:
    result = _run_capture([executable, '--version'])
    raw_version = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        raise GateFailure(
            f'{label} version probe failed with exit {result.returncode}: '
            f'{raw_version or "no version output"}'
        )
    actual = _version_tuple(raw_version, label=label)
    lower, upper = bounds
    if actual < lower or (upper is not None and actual >= upper):
        upper_text = f' and < {".".join(map(str, upper))}' if upper else ''
        # The remediation names the *derived* major, never a literal: a hint that
        # hardcodes a version is a second declaration that goes stale silently,
        # and one that names a single version manager is wrong on any box that
        # has a different one (fnm vs nvm) -- which is exactly how a wrong
        # runtime gets used while the operator believes the pin was honoured.
        raise GateFailure(
            f'{label} {raw_version} is outside {PACKAGE_JSON} engines.{label} '
            f'>= {".".join(map(str, lower))}{upper_text}; '
            f'activate {label} {lower[0]} (fnm exec --using={lower[0]}, '
            f'nvm use {lower[0]}, or your version manager) and retry'
        )


PRINT_SATISFYING_NODE_BIN_FLAG = '--print-satisfying-node-bin'


def _candidate_node_bins(required_major: int):
    """Yield (label, bin_dir) for every Node this box could plausibly activate.

    Version managers come before the ambient PATH: an explicit pin should win
    over whatever the box happens to expose.  Nothing here decides anything --
    each candidate is still verified against the engine bounds by the caller.
    """
    fnm = shutil.which('fnm')
    if fnm is not None:
        probe = _run_capture(
            [fnm, 'exec', f'--using={required_major}', 'node', '-p', 'process.execPath']
        )
        if probe.returncode == 0 and probe.stdout.strip():
            yield 'fnm', Path(probe.stdout.strip()).parent

    nvm_root = Path(os.environ.get('NVM_DIR') or (Path.home() / '.nvm')) / 'versions' / 'node'
    if nvm_root.is_dir():
        installed = [
            entry for entry in nvm_root.iterdir()
            if entry.is_dir() and entry.name.startswith(f'v{required_major}.')
        ]
        # Newest patch first, compared numerically: a name sort puts v22.9 above
        # v22.23 and would hand back a Node that fails the `>=22.13` lower bound.
        for entry in sorted(
            installed, key=lambda e: _version_tuple(e.name, label='Node'), reverse=True
        ):
            yield 'nvm', entry / 'bin'

    ambient = shutil.which('node')
    if ambient is not None:
        yield 'PATH', Path(ambient).parent


def find_satisfying_node_bin() -> Path | None:
    """Return a bin directory whose node *and* npm satisfy ``engines``.

    This is the single owner of runtime *selection*, for the same reason
    ``_load_engine_bounds`` is the single owner of the version contract.  Callers
    that need a Node -- the provisioning script, the tests that exercise the real
    gate -- ask here instead of each deciding for itself which version manager
    this box is supposed to have.  Asking "is fnm installed?" is a proxy, and a
    proxy agrees with the fact it stands for right up until the box carries a
    different manager; then every lane runs under the wrong major while the
    operator believes the pin was honoured.
    """
    node_lower, node_upper, npm_lower, npm_upper = _load_engine_bounds()
    for _label, bin_dir in _candidate_node_bins(node_lower[0]):
        # ⚠️ Probe the candidate directory **alone**. Appending the ambient
        #    PATH here made an empty candidate pass on somebody else's node:
        #    `shutil.which` fell through to the system install, the version
        #    check ran against *that* binary, and this function then returned
        #    a bin directory with no node in it. The caller prepends what it
        #    gets to PATH, so the box silently runs the ambient runtime while
        #    the operator believes the pin was honoured — which is the exact
        #    failure this function's docstring exists to prevent, reached
        #    through the probe rather than around it.
        #    Nothing is lost by dropping the fallback: the ambient PATH is
        #    already yielded as its own concrete candidate ('PATH', …), so a
        #    box with only a system node still resolves — it just resolves as
        #    that candidate, under its own name.
        #    Measured 2026-08-31: green on a box whose system node fails
        #    `engines`, red on one where it passes. Same code, same test.
        search_path = str(bin_dir)
        node = shutil.which('node', path=search_path)
        npm = shutil.which('npm', path=search_path) or shutil.which('npm.cmd', path=search_path)
        if node is None or npm is None:
            continue
        try:
            _check_engine(node, label='Node', bounds=(node_lower, node_upper))
            _check_engine(npm, label='npm', bounds=(npm_lower, npm_upper))
        except GateFailure:
            continue
        return bin_dir
    return None


def _run_build(npm_executable: str) -> int:
    command = [npm_executable, 'run', 'build']
    try:
        result = subprocess.run(
            command,
            cwd=WEB_ROOT,
            shell=False,
            check=False,
        )
    except OSError as exc:
        print(
            f'FRONTEND_BUILD_GATE_ERROR: could not execute npm run build: {exc}',
            file=sys.stderr,
        )
        return 1

    if result.returncode == 0:
        return 0
    print(
        'FRONTEND_BUILD_GATE_ERROR: npm run build failed '
        f'with exit {result.returncode}; inspect the TypeScript/Vite output above',
        file=sys.stderr,
    )
    return result.returncode if result.returncode > 0 else 1


CHECK_ENGINES_FLAG = '--check-engines'
PRINT_NODE_MAJOR_FLAG = '--print-node-major'


def main(argv: Sequence[str] | None = None) -> int:
    # Two doors that stop short of the build, so a caller that must *choose* a
    # Node runtime can ask this module rather than re-parse `engines` itself.
    # The version contract has exactly one owner (apps/web/package.json) and one
    # reader (`_load_engine_bounds`); a second parser in a shell script would be
    # a second declaration, and the drift would surface as lanes silently running
    # under the wrong runtime -- the failure this repository already documents as
    # "looks like a code defect".
    # ⚠️ `argv is None` means "no arguments", never "read sys.argv".  This module
    # is imported and called as `main()` by the tests, and there sys.argv carries
    # pytest's own flags -- reading it would let one process's arguments leak into
    # a library call.  Only the __main__ entrypoint below knows about sys.argv.
    args = list(argv or ())
    unknown = [arg for arg in args if arg not in {
        CHECK_ENGINES_FLAG, PRINT_NODE_MAJOR_FLAG, PRINT_SATISFYING_NODE_BIN_FLAG,
    }]
    if unknown:
        print(
            'FRONTEND_BUILD_GATE_ERROR: unsupported argument(s) '
            + ', '.join(repr(arg) for arg in unknown)
            + f'; expected none, {CHECK_ENGINES_FLAG}, {PRINT_NODE_MAJOR_FLAG}, '
            f'or {PRINT_SATISFYING_NODE_BIN_FLAG}',
            file=sys.stderr,
        )
        return 2
    try:
        if not REPO_ROOT.is_dir() or not WEB_ROOT.is_dir():
            raise GateFailure(
                f'repository frontend root is missing: expected {WEB_ROOT}'
            )
        node_lower, node_upper, npm_lower, npm_upper = _load_engine_bounds()
        if PRINT_SATISFYING_NODE_BIN_FLAG in args:
            bin_dir = find_satisfying_node_bin()
            if bin_dir is None:
                raise GateFailure(
                    'no available Node satisfies '
                    f'{PACKAGE_JSON} engines (tried fnm, nvm, and PATH); '
                    f'install Node {node_lower[0]} and retry'
                )
            print(bin_dir)
            return 0
        if PRINT_NODE_MAJOR_FLAG in args:
            # Deliberately answered before any executable lookup: the caller uses
            # this to *select* an interpreter, so requiring one on PATH first
            # would make the answer depend on the thing it is used to fix.
            print(node_lower[0])
            return 0
        node = _find_executable('node', label='Node')
        npm = _find_executable('npm', label='npm')
        _check_engine(node, label='Node', bounds=(node_lower, node_upper))
        _check_engine(npm, label='npm', bounds=(npm_lower, npm_upper))
        if CHECK_ENGINES_FLAG in args:
            return 0
        if not NODE_MODULES.is_dir():
            raise GateFailure(
                f'{NODE_MODULES} is missing; run `npm ci` in apps/web '
                'before rerunning the build gate',
                missing_tool=True,
            )
    except GateFailure as exc:
        prefix = 'MISSING_TOOL: ' if exc.missing_tool else 'FRONTEND_BUILD_GATE_ERROR: '
        print(prefix + str(exc), file=sys.stderr)
        return 2

    return _run_build(npm)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
