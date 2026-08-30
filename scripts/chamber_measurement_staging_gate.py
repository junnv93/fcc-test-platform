"""Chamber real-measurement staging gate (offline, no-fake hardware).

Closure plan P2 deliverable. Two subcommands, both fully offline:

* ``--template`` — write a blank, structurally-valid evidence skeleton for an
  operator to fill during a real staging attempt. The skeleton uses the
  ``<FILL-ME>`` placeholder / zero / false defaults, so it is structurally
  valid but **fails the M1 pass criteria** — an unfilled template can never be
  mistaken for a real run.

* ``--validate <bundle>`` — validate an operator-filled bundle's *structure*
  against the schema SSOT. Add ``--require-pass`` to also enforce the full M1
  gate: the bundle must be ``mode='recorded'`` (a filled-in template left in
  ``mode='template'`` is rejected) AND meet every M1 pass criterion (>= 2
  distinct machines, real analyzer + Appium, SCPI/Appium proof-of-life
  (analyzer *IDN? + Appium session + DUT driven), measurement POST success,
  in_use transition, progress via API/WS or documented fallback, complete
  timeline, completion / controlled failure with correlated ids, node
  restart/partition captured or not-run, and a 3+ node fleet partition captured
  or honestly not-run). Exit code is non-zero when the requested gate fails.

This gate NEVER contacts hardware and NEVER asserts hardware ran. It checks
shape and self-consistency only; the truth of a recorded run is supplied by the
operator pasting real, cross-referenced API/WS/log identifiers (see
``docs/operations/chamber-real-measurement-staging-runbook.md``).

Vocabulary + validation SSOT:
``application.platform.chamber_measurement_staging_evidence``.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
for path in (PROJECT_ROOT, SRC_ROOT):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from fcc_test_platform.application.chamber_measurement_staging_evidence import (  # noqa: E402
    MIN_EXPECTED_FLEET_SIZE,
    SCHEMA_VERSION,
    TEMPLATE_PLACEHOLDER,
    chamber_measurement_staging_errors,
    measurement_staging_pass_summary,
    measurement_staging_passes_m1,
    measurement_staging_unmet_pass_criteria,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Chamber real-measurement staging evidence gate.')
    parser.add_argument('--output', help='Path to write a template skeleton (with --template).')
    parser.add_argument('--validate', dest='bundle', help='Path to an evidence bundle to validate.')
    parser.add_argument('--template', action='store_true',
                        help='Write a blank evidence skeleton to --output (never a pass).')
    parser.add_argument('--require-pass', action='store_true',
                        help='With --validate: fail (exit 1) unless all M1 pass criteria are met.')
    parser.add_argument('--evidence-id', default=None)
    args = parser.parse_args(argv)

    if args.template:
        if not args.output:
            _emit_error('--template requires --output')
            return 2
        return _write_template(Path(args.output), evidence_id=args.evidence_id)

    if args.bundle:
        return _validate_bundle(Path(args.bundle), require_pass=args.require_pass)

    _emit_error('choose one of --template or --validate <bundle>')
    return 2


def build_template(*, evidence_id: str | None = None,
                   clock: Callable[[], datetime] | None = None) -> dict:
    """A blank, structurally-valid skeleton that fails every M1 pass criterion.

    Pure (no IO) so the builder is unit-testable; ``clock`` is injectable for
    determinism. Recorded values are the unfilled placeholder / zero / false so
    the bundle cannot pass — only a real, operator-filled run can.
    """
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    stamp = now.isoformat()
    p = TEMPLATE_PLACEHOLDER
    return {
        'schema_version': SCHEMA_VERSION,
        'evidence_id': (evidence_id or f'measurement-staging-{stamp}').strip(),
        'collected_at': stamp,
        'mode': 'template',
        'central_stack_version': p,
        'machines': [
            {'role': 'central-hub', 'identity': p, 'address': p, 'os': p},
            {'role': 'chamber-node', 'identity': p, 'address': p, 'os': p},
        ],
        'chamber': {
            'chamber_id': p,
            'token_binding_state': 'unknown',
            'node_pc_identity': p,
        },
        'equipment': {
            'analyzer_resource': p,
            'appium_device_id': p,
            # SCPI/Appium proof-of-life — filled from the real run (never a
            # claim that a sweep produced a value; shape-checked only).
            'analyzer_idn': p,
            'appium_session_id': p,
            'dut_model': p,
            'dut_driven': False,
        },
        'published_plan_id': p,
        'measurement': {
            'job_id': p,
            'session_id': p,
            'start': {'endpoint': 'POST /platform/chambers/{chamber_id}/measurements',
                      'http_status': 0},
            'chamber_in_use_observed': False,
            'progress': {'via_api': False, 'via_ws': False, 'fallback': ''},
            'outcome': '',
        },
        'timeline': [
            {'phase': 'start', 'at': p},
            {'phase': 'progress', 'at': p},
            {'phase': 'completion', 'at': p},
        ],
        'correlation': {'api': p, 'ws': p, 'log': p},
        'restart_partition': {
            'node_restart': 'not_run',
            'network_partition': 'not_run',
            'notes': p,
        },
        # 3+ node fleet split (split-brain). Defaults to an honest not_run so the
        # skeleton is structurally valid; the operator sets observed only if the
        # partition was actually exercised on a 3+ node fleet.
        'fleet_partition': {
            'state': 'not_run',
            'expected_fleet_size': MIN_EXPECTED_FLEET_SIZE,
            'notes': p,
        },
    }


def _write_template(output: Path, *, evidence_id: str | None) -> int:
    manifest = build_template(evidence_id=evidence_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    errors = chamber_measurement_staging_errors(manifest)
    unmet = measurement_staging_unmet_pass_criteria(manifest)
    print(json.dumps({
        'wrote_template': str(output),
        'structurally_valid': not errors,
        'passes_m1': not unmet,
        'unmet_pass_criteria': [c.key for c in unmet],
        'note': 'template is a skeleton — fill it during a real staging run; '
                'it intentionally fails the M1 gate until filled.',
    }, sort_keys=True, indent=2))
    # A template must be structurally valid; it must NOT pass M1.
    return 0 if (not errors and unmet) else 1


def _validate_bundle(bundle: Path, *, require_pass: bool) -> int:
    try:
        manifest = json.loads(bundle.read_text(encoding='utf-8'))
    except FileNotFoundError:
        _emit_error(f'bundle not found: {bundle}')
        return 2
    except json.JSONDecodeError as exc:
        _emit_error(f'bundle is not valid JSON: {exc}')
        return 2

    errors = [i.to_dict() for i in chamber_measurement_staging_errors(manifest)]
    summary = measurement_staging_pass_summary(manifest)
    unmet = [c.key for c in summary if not c.met]
    structurally_valid = not errors
    mode = manifest.get('mode') if isinstance(manifest, dict) else None
    recorded_mode = mode == 'recorded'
    # passes_m1 is the FULL gate (structurally valid AND recorded mode AND no
    # unmet criteria); all three requirements live in the evidence SSOT, not
    # here, so the reported passes_m1 flag can never disagree with the exit code.
    passes_m1 = measurement_staging_passes_m1(manifest)

    print(json.dumps({
        'bundle': str(bundle),
        'mode': mode,
        'recorded_mode': recorded_mode,
        'structurally_valid': structurally_valid,
        'structural_issues': errors,
        'unmet_pass_criteria': unmet,
        'passes_m1': passes_m1,
        'pass_criteria': [c.to_dict() for c in summary],
    }, sort_keys=True, indent=2))

    if not structurally_valid:
        return 1
    if require_pass and not passes_m1:
        return 1
    return 0


def _emit_error(message: str) -> None:
    print(json.dumps({'ok': False, 'error': message}, sort_keys=True, indent=2),
          file=sys.stderr)


if __name__ == '__main__':
    raise SystemExit(main())
