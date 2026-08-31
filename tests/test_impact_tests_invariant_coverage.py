# ⚠️ 2026-08-31: 이 파일은 모노레포 `tests/test_impact_tests_invariant_coverage.py` 에서 갈라져 왔다. 남은 것은
#    소비 대상이 이 레포에 있는 단위(TestFrontendConformanceSyntheticRouting)뿐이고,
#    나머지 형제 검사와 그것들만 쓰던 import 는 저쪽에 남았다.
"""Impact-tests.sh × SKILL.md frontmatter coverage invariant.

Sprint invariant-mapping-detector-architecture-upgrade (2026-05-25).

The drift gate ``tests/test_invariant_skill_mapping_drift.py`` enforces
that every invariant is *referenced* by some skill. But "referenced in
a SKILL.md body" is not the same as "auto-routed by ``/verify-implementation``
on a relevant change" — the actual PR-gate routing lives in
``.claude/scripts/impact-tests.sh``.

This invariant closes that loop:

    For every skill that has BOTH ``mapped_invariants:`` AND
    ``trigger_patterns:`` in its frontmatter, every (trigger_pattern,
    mapped_invariant) pair must be covered by impact-tests.sh: feeding
    the trigger pattern into impact-tests.sh produces output that
    includes the mapped invariant.

Backwards-compat: skills without frontmatter ``mapped_invariants`` or
``trigger_patterns`` are skipped (incremental migration). This invariant
only enforces the contract for skills that opted in.

Why this matters: prior sprint ``invariant-mapping-ratchet-batch1``
mapped 7 sprint legacy invariants to ``verify-excel-atomic-save`` /
``verify-dccf-sentinel`` / ``verify-seed-bulk-insert-ssot`` SKILL.md
bodies, but never updated impact-tests.sh — meaning a developer
editing ``src/infrastructure/excel/excel_exporter.py`` would NOT see
``test_sprint98_exporter_invariants.py`` run in their pre-PR
verification. This invariant prevents that gap from recurring.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMPACT_TESTS_SH = ROOT / ".claude" / "scripts" / "impact-tests.sh"
IMPACT_MAP = ROOT / ".claude" / "skills" / "harness" / "references" / "impact-map.md"

sys.path.insert(0, str(ROOT / "scripts"))


# ════════════════════════════════════════════════════════════════════════════
# Bash resolution SSOT (closure retry — Codex bash-resolution root cause)
# ════════════════════════════════════════════════════════════════════════════
#
# Root cause of a prior closure rejection: ``subprocess.run(["bash", ...])``
# resolves ``bash`` via PATH, and on some Windows hosts PATH's first ``bash``
# is the WSL launcher (``C:\Windows\System32\bash.exe``). When WSL has no
# usable distro (or cannot reach the Windows-path script), that launcher
# exits non-zero WITHOUT running impact-tests.sh. Because the routing helper
# previously ignored ``result.returncode``, that execution failure was misread
# as "this trigger routes to nothing" → every mapped invariant looked missing
# → false ``1 failed``.
#
# Fix (within tests/ scope, no production change):
#   1. Resolve a bash that can actually RUN impact-tests.sh by probing
#      candidates (Git Bash well-known install dirs first — robust across WSL
#      flakiness — then PATH, then the bare name). A candidate is accepted only
#      if a known-routing probe returns rc==0 AND emits the expected test.
#   2. Surface a non-zero returncode as a loud RuntimeError instead of an empty
#      set (mirrors the existing ``_run_generator`` guard in
#      tests/test_render_generators_sync.py).

#: A trigger path that MUST route to a known invariant, used to probe that a
#: candidate bash can genuinely execute impact-tests.sh (not merely launch).
_PROBE_TRIGGER = "src/result_writer.py"
_PROBE_EXPECTED = "tests/test_result_writer.py"

_resolved_bash: str | None = None


def _candidate_bash_executables() -> list[str]:
    """Ordered bash candidates: Git Bash dirs → PATH → bare name.

    Git Bash is preferred over a PATH-first WSL launcher because WSL bash is
    environment-dependent (distro presence / Windows-path handling) and is the
    exact failure mode this helper exists to route around. On POSIX hosts the
    Git Bash paths simply do not exist and ``shutil.which('bash')`` /
    ``"bash"`` resolve normally — so the ordering is portable.
    """
    candidates: list[str] = []
    program_bases = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramW6432"),
        os.environ.get("ProgramFiles(x86)"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")
        if os.environ.get("LOCALAPPDATA")
        else None,
    ]
    for base in program_bases:
        if not base:
            continue
        for sub in (r"Git\bin\bash.exe", r"Git\usr\bin\bash.exe"):
            candidates.append(os.path.join(base, sub))
    which = shutil.which("bash")
    if which:
        candidates.append(which)
    candidates.append("bash")  # let subprocess resolve from PATH as last resort

    seen: set[str] = set()
    ordered: list[str] = []
    for cand in candidates:
        key = cand.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(cand)
    return ordered


def _resolve_working_bash() -> str:
    """Return a bash executable proven able to run impact-tests.sh.

    Raises RuntimeError (loud, not a silent empty routing) when no candidate
    can execute the dispatcher — so a broken bash environment fails the gate
    visibly instead of masquerading as "every invariant is unrouted".
    """
    global _resolved_bash
    if _resolved_bash is not None:
        return _resolved_bash

    attempts: list[str] = []
    for bash in _candidate_bash_executables():
        if bash != "bash" and not Path(bash).is_file():
            continue
        try:
            probe = subprocess.run(
                [bash, ".claude/scripts/impact-tests.sh"],
                input=(_PROBE_TRIGGER + "\n").encode("utf-8"),
                capture_output=True,
                cwd=str(ROOT),
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env dependent
            attempts.append(f"{bash}: launch failed ({exc})")
            continue
        out = probe.stdout.decode("utf-8", errors="replace")
        if probe.returncode == 0 and _PROBE_EXPECTED in out:
            _resolved_bash = bash
            return bash
        attempts.append(
            f"{bash}: rc={probe.returncode} "
            f"expected_token_present={_PROBE_EXPECTED in out}"
        )

    raise RuntimeError(
        "No bash able to execute .claude/scripts/impact-tests.sh was found. "
        "Install Git Bash (C:\\Program Files\\Git\\bin\\bash.exe) or put a "
        "working bash on PATH — the WSL launcher in System32 cannot run this "
        "dispatcher when no distro is installed. Probe attempts:\n"
        + "\n".join(f"  {a}" for a in attempts)
    )


def _route_pattern_through_impact_tests(trigger_path: str) -> set[str]:
    """Run impact-tests.sh with the trigger path on stdin, return emitted tests."""
    # Windows cp949 default vs bash utf-8 — force utf-8 and replace on
    # decode errors so the subprocess never returns an empty set due to
    # a stray non-ASCII byte in some other case's echo string.
    # bash on Windows (Git Bash / MSYS2) rejects ``C:\\...`` and ``C:/...``
    # absolute paths alike (exit 127). The portable approach is ``cwd=ROOT``
    # + relative path, which works on POSIX bash too.
    bash = _resolve_working_bash()
    result = subprocess.run(
        [bash, ".claude/scripts/impact-tests.sh"],
        input=(trigger_path + "\n").encode("utf-8"),
        capture_output=True,
        cwd=str(ROOT),
        timeout=20,
    )
    # A non-zero exit means impact-tests.sh did NOT run to completion (e.g. a
    # broken bash launcher). Surface it loudly instead of returning an empty
    # set — an execution failure must never be misread as "no routing", which
    # would make every mapped invariant look spuriously missing.
    if result.returncode != 0:
        raise RuntimeError(
            f"impact-tests.sh exited {result.returncode} for trigger "
            f"{trigger_path!r} using bash={bash!r}. This is an execution "
            "failure, not an empty routing. stderr:\n"
            + result.stderr.decode("utf-8", errors="replace")
        )
    stdout_text = result.stdout.decode("utf-8", errors="replace")
    # impact-tests.sh emits test paths (with optional `::Class` suffix)
    # one per line. The drift-gate audit's grep pattern matches plain
    # `tests/...py`, so we normalize.
    emitted: set[str] = set()
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip ::Class::test suffix if present
        bare = line.split("::", 1)[0]
        emitted.add(bare)
    return emitted




# ════════════════════════════════════════════════════════════════════════════
# Structural guard — catch-all/duplicate shadowing of specific case patterns
# ════════════════════════════════════════════════════════════════════════════
#
# FE-P0b follow-up (자평 #3, 2026-05-26): ``impact-tests.sh`` is a SINGLE
# ``case "$file" in ... esac`` block (bash first-match with ``;;``). If a
# SPECIFIC file pattern (e.g. ``src/result_writer.py``) appears in a clause
# that is preceded by an EARLIER clause whose pattern also matches it (a glob
# like ``src/infrastructure/database/*.py`` OR an exact duplicate), the later
# clause is DEAD — its echoes never fire. This is the exact class of bug that
# made the FE-P0b store routing silently dead (Commit 3 → fixed Commit 5).
#
# This guard detects all such shadowed specific patterns and ratchets against
# a baseline of the 37 PRE-EXISTING shadows (accumulated across prior sprints).
# It does NOT try to fix the pre-existing debt — it prevents NEW shadows and
# allows the baseline to shrink (monotonic-decrease, like the invariant-skill
# mapping drift gate).

_GLOB_CHARS = set('*?[')

#: PRE-EXISTING shadowed specific patterns (scan of impact-tests.sh at
#: 2026-05-26). Each is a specific (non-glob) case pattern shadowed by an
#: earlier glob/duplicate clause. Ratchet policy: monotonic-decrease — never
#: add; remove as the underlying impact-tests.sh structure is de-shadowed.
_SHADOWED_SPECIFIC_BASELINE: frozenset[str] = frozenset({
    'src/application/headless/api_schema.py',
    'src/application/headless/oidc_principal_resolver.py',
    'src/bootstrap.py',
    'src/column_names.py',
    'src/domain/ports/output/artifact_storage_port.py',
    'src/domain/ports/output/database_port.py',
    'src/domain/ports/output/measurement_port.py',
    'src/domain/ports/output/result_write_port.py',
    'src/domain/services/signal_stop_policy.py',
    'src/infrastructure/adapters/driven/sqlite_database_adapter.py',
    'src/infrastructure/adapters/driving/api/headless_routes.py',
    'src/infrastructure/adapters/driving/api/schema_router.py',
    'src/infrastructure/database/measurement_history_store.py',
    'src/infrastructure/database/migrations/006_headless_jobs.py',
    'src/infrastructure/database/migrations/007_headless_job_leases.py',
    'src/infrastructure/database/migrations/008_report_automation.py',
    'src/infrastructure/database/migrations/009_report_automation_leases.py',
    'src/infrastructure/database/migrations/012_result_units.py',
    'src/infrastructure/database/migrations/013_result_sum_unit.py',
    'src/infrastructure/database/result_write_store.py',
    'src/reporting/domain/ports/output/representative_port.py',
    'src/reporting/domain/services/ble_conditions.py',
    'src/reporting/infrastructure/adapters/ble_fcc_docx_obw.py',
    'src/reporting/infrastructure/adapters/docx_section_mode_sync.py',
    'src/result_writer.py',
    'src/test_runner_core.py',
    'src/ui/load_data_controller.py',
    'src/ui/report_controller.py',
    'src/ui/table_manager.py',
    'tests/test_bench_snapshot_history.py',
    'tests/test_benchmark_harness_percentile_ssot.py',
    'tests/test_benchmark_harness_robust_measurement.py',
    'tests/test_composition_wiring_drift_high2.py',
    'tests/test_config_batch_read.py',
    'tests/test_wal_checkpoint_durability.py',
})


def _detect_shadowed_specific_patterns() -> set[str]:
    """Return specific (non-glob) case patterns shadowed by an earlier clause.

    Parses the main ``case`` block of impact-tests.sh: clause labels are
    4-space-indented lines ending with ``)`` that contain a path/glob. For
    each specific pattern, if any pattern in an earlier clause matches it
    (fnmatch for globs, equality for exact duplicates), it is shadowed.
    """
    import fnmatch

    text = IMPACT_TESTS_SH.read_text(encoding="utf-8")
    clauses: list[list[str]] = []
    for raw in text.splitlines():
        s = raw.rstrip()
        if not s.endswith(")"):
            continue
        stripped = s.strip()
        if stripped.startswith(("#", "echo", "case", "esac", ";;", "*")):
            continue
        if (len(s) - len(s.lstrip())) != 4:  # case-label indent in this file
            continue
        label = stripped[:-1]
        if "/" not in label and "*" not in label:
            continue
        clauses.append([p.strip() for p in label.split("|") if p.strip()])

    shadowed: set[str] = set()
    for idx, pats in enumerate(clauses):
        for p in pats:
            if _GLOB_CHARS & set(p):
                continue  # only specific patterns can be silently shadowed
            for earlier in clauses[:idx]:
                if any(
                    (_GLOB_CHARS & set(ep) and fnmatch.fnmatch(p, ep)) or ep == p
                    for ep in earlier
                ):
                    shadowed.add(p)
                    break
    return shadowed




# ════════════════════════════════════════════════════════════════════════════
# Seal — bash execution failure must NOT be misread as empty routing
# ════════════════════════════════════════════════════════════════════════════
#
# Closure-retry seal (Codex bash-resolution root cause). The routing helper
# used to ignore the subprocess returncode, so a bash launcher that exited
# non-zero without running impact-tests.sh (WSL with no usable distro) produced
# an empty set — making every mapped invariant look spuriously unrouted and
# yielding a false ``1 failed``. These tests lock in that a non-zero exit
# surfaces loudly and that bash resolution prefers a probed-working executable.






# ════════════════════════════════════════════════════════════════════════════
# FCC cutover platform purity routing witness
# ════════════════════════════════════════════════════════════════════════════




# ════════════════════════════════════════════════════════════════════════════
# verify-frontend-conformance — SYNTHETIC future-path routing SSOT
# ════════════════════════════════════════════════════════════════════════════
#
# Increment 2 follow-up (fe-conformance-and-design-review, 2026-06-13, Codex
# review iter-02 P0/finding #2). The conformance invariant
# (tests/test_frontend_architecture_conformance.py) scans the WHOLE apps/web/src
# tree via ``SRC_DIR.rglob("*.ts")`` + ``rglob("*.tsx")`` — top-level AND nested.
#
# ``TestImpactTestsMappedInvariantCoverage`` proves routing only for trigger
# patterns that expand to files THAT EXIST TODAY (``ROOT.glob(trigger)`` →
# empty expansion is skipped as "future file"). That is structurally blind to
# the exact gap Codex flagged: a *future* top-level ``apps/web/src/runtime.ts``
# is scanned by the invariant but, before this seal, did not route to it because
# the trigger set only had ``apps/web/src/*.tsx`` + ``apps/web/src/**/*.ts``
# (top-level ``.ts`` uncovered). This class feeds SYNTHETIC paths (existing and
# not-yet-existing) directly through impact-tests.sh and asserts each one routes
# to the conformance invariant — sealing the trigger ↔ scan-scope equivalence
# against drift regardless of the current on-disk tree.


# ⚠️ `TestFrontendConformanceSyntheticRouting` 는 이 레포로 오지 못했다 — 사유는
#    `tests/RETIRED_WITH_THE_FRONTEND.md`(모노레포) §5 참조.



if __name__ == "__main__":
    unittest.main()
