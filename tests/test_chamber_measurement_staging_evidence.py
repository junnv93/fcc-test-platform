"""M1 real measurement-path staging gate (closure plan P2).

Seals the offline evidence vocabulary SSOT, JSON-Schema <-> Python parity, the
no-fake-hardware guards (template can never pass; recorded forbids the unfilled
placeholder), and the gate CLI behavior. No hardware, no network — fully
deterministic and offline, mirroring the P1 token-evidence test contract.
"""
from __future__ import annotations

import ast
import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
for path in (ROOT, SRC):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402

from fcc_test_platform.application.chamber_measurement_staging_evidence import (  # noqa: E402
    EVIDENCE_MODES,
    MEASUREMENT_OUTCOMES,
    MIN_EXPECTED_FLEET_SIZE,
    MIN_PHYSICAL_MACHINES,
    SCENARIO_STATES,
    SCHEMA_VERSION,
    TEMPLATE_PLACEHOLDER,
    TIMELINE_PHASES,
    TOKEN_BINDING_STATES,
    chamber_measurement_staging_errors,
    is_measurement_staging_evidence_valid,
    measurement_staging_pass_summary,
    measurement_staging_passes_m1,
    measurement_staging_unmet_pass_criteria,
)

import scripts.chamber_measurement_staging_gate as gate  # noqa: E402

_FIXED_CLOCK = lambda: datetime.datetime(  # noqa: E731
    2026, 6, 20, tzinfo=datetime.timezone.utc)

SCHEMA_PATH = ROOT / 'docs' / 'platform' / 'chamber_measurement_staging.schema.json'
EVIDENCE_MODULE = resolve_repo_artifact(__file__, 'src/application/platform/chamber_measurement_staging_evidence.py')


def _recorded_pass_manifest() -> dict:
    """A fully-filled recorded bundle that meets every M1 criterion."""
    return {
        'schema_version': SCHEMA_VERSION,
        'evidence_id': 'measurement-staging-attempt-1',
        'collected_at': '2026-06-20T06:00:00+00:00',
        'mode': 'recorded',
        'central_stack_version': 'central@86814c01',
        'machines': [
            {'role': 'central-hub', 'identity': 'pc-a', 'address': '172.30.1.2', 'os': 'Win11/WSL'},
            {'role': 'chamber-node', 'identity': 'pc-b', 'address': '172.30.1.99', 'os': 'Win11'},
        ],
        'chamber': {
            'chamber_id': 'staging-chamber-a',
            'token_binding_state': 'bound',
            'node_pc_identity': 'pc-b',
        },
        'equipment': {
            'analyzer_resource': 'TCPIP0::172.30.1.50::inst0::INSTR',
            'appium_device_id': 'emulator-5554',
            'analyzer_idn': 'Keysight Technologies,N9020A,MY12345678,A.20.16',
            'appium_session_id': 'appium-sess-0f1e2d',
            'dut_model': 'SM-S911N',
            'dut_driven': True,
        },
        'published_plan_id': 'plan-123',
        'measurement': {
            'job_id': 'job-9',
            'session_id': 'sess-9',
            'start': {'endpoint': 'POST /platform/chambers/staging-chamber-a/measurements',
                      'http_status': 202},
            'chamber_in_use_observed': True,
            'progress': {'via_api': True, 'via_ws': True, 'fallback': ''},
            'outcome': 'completed',
        },
        'timeline': [
            {'phase': 'start', 'at': '2026-06-20T06:00:01+00:00'},
            {'phase': 'progress', 'at': '2026-06-20T06:00:30+00:00'},
            {'phase': 'completion', 'at': '2026-06-20T06:05:00+00:00'},
        ],
        'correlation': {'api': 'trace-abc', 'ws': 'conn-7', 'log': 'sess-9'},
        'restart_partition': {
            'node_restart': 'observed',
            'network_partition': 'observed',
            'notes': 'restart + firewall partition both exercised',
        },
        # 3+ node fleet partition honestly declared not_run (only 2 PCs on hand):
        # not_run is a passing-with-honesty state, so the canonical pass bundle
        # exercises the blocked-by-equipment path rather than faking observed.
        'fleet_partition': {
            'state': 'not_run',
            'expected_fleet_size': 3,
            'notes': 'only 2 staging PCs available; 3+ node split-brain deferred '
                     '(blocked-by-equipment)',
        },
    }


def _filled_template_manifest() -> dict:
    """A bundle whose every measurement field is filled with real values but
    which is still labelled ``mode='template'`` — meets all 8 measurement
    criteria yet must NOT clear the recorded-mode gate."""
    manifest = _recorded_pass_manifest()
    manifest['mode'] = 'template'
    return manifest


class TestTemplateBuilder(unittest.TestCase):
    def test_template_is_structurally_valid(self):
        manifest = gate.build_template(clock=_FIXED_CLOCK)
        self.assertTrue(is_measurement_staging_evidence_valid(manifest),
                        chamber_measurement_staging_errors(manifest))
        self.assertEqual(manifest['mode'], 'template')

    def test_template_never_passes_m1(self):
        # No-fake core guarantee: an unfilled skeleton cannot pass.
        manifest = gate.build_template(clock=_FIXED_CLOCK)
        unmet = measurement_staging_unmet_pass_criteria(manifest)
        self.assertTrue(unmet)
        keys = {c.key for c in unmet}
        self.assertIn('real_analyzer_and_appium', keys)
        self.assertIn('measurement_post_succeeds', keys)

    def test_template_has_two_machine_slots(self):
        manifest = gate.build_template(clock=_FIXED_CLOCK)
        self.assertGreaterEqual(len(manifest['machines']), MIN_PHYSICAL_MACHINES)


class TestRecordedPass(unittest.TestCase):
    def test_recorded_pass_manifest_is_valid_and_passes(self):
        manifest = _recorded_pass_manifest()
        self.assertTrue(is_measurement_staging_evidence_valid(manifest),
                        chamber_measurement_staging_errors(manifest))
        self.assertEqual(measurement_staging_unmet_pass_criteria(manifest), [])

    def test_summary_covers_all_ten_criteria(self):
        summary = measurement_staging_pass_summary(_recorded_pass_manifest())
        self.assertEqual(len(summary), 10)
        self.assertTrue(all(c.met for c in summary))
        keys = {c.key for c in summary}
        self.assertIn('timeline_phases_complete', keys)
        self.assertIn('measurement_path_proof', keys)
        self.assertIn('fleet_partition_captured_or_not_run', keys)

    def test_recorded_pass_manifest_clears_full_gate(self):
        self.assertTrue(measurement_staging_passes_m1(_recorded_pass_manifest()))


class TestRecordedModeGate(unittest.TestCase):
    """--require-pass must not clear a non-recorded bundle (no false pass)."""

    def test_filled_template_meets_all_criteria(self):
        # The fixture is genuinely "fully filled": it has zero unmet criteria.
        manifest = _filled_template_manifest()
        self.assertEqual(measurement_staging_unmet_pass_criteria(manifest), [])

    def test_filled_template_fails_full_gate(self):
        # ... yet it must NOT clear the M1 gate, because mode != 'recorded'.
        manifest = _filled_template_manifest()
        self.assertFalse(measurement_staging_passes_m1(manifest))

    def test_recorded_required_for_gate(self):
        manifest = _recorded_pass_manifest()
        manifest['mode'] = 'template'
        self.assertFalse(measurement_staging_passes_m1(manifest))

    def test_filled_template_is_structurally_valid(self):
        # Structure-only validation still passes — only the gate rejects it.
        self.assertTrue(
            is_measurement_staging_evidence_valid(_filled_template_manifest()))


class TestStructuralValidityGate(unittest.TestCase):
    """The full gate must reject a structurally-invalid recorded bundle, even
    when the broken field is NOT a pass criterion (Codex: passes_m1 must not
    ignore structural errors / false-pass on a half-filled recorded bundle)."""

    def test_placeholder_in_non_criterion_field_is_structural_error(self):
        manifest = _recorded_pass_manifest()
        manifest['central_stack_version'] = TEMPLATE_PLACEHOLDER
        codes = {i.code for i in chamber_measurement_staging_errors(manifest)}
        self.assertIn('placeholder_in_recorded', codes)

    def test_placeholder_in_non_criterion_field_meets_all_pass_criteria(self):
        # The placeholder is in a non-pass-criterion field, so every measurement
        # pass criterion is still met — proving the gate must rely on structural
        # validity, not just the pass-criteria list.
        manifest = _recorded_pass_manifest()
        manifest['central_stack_version'] = TEMPLATE_PLACEHOLDER
        self.assertEqual(measurement_staging_unmet_pass_criteria(manifest), [])

    def test_placeholder_in_non_criterion_field_fails_full_gate(self):
        manifest = _recorded_pass_manifest()
        manifest['central_stack_version'] = TEMPLATE_PLACEHOLDER
        self.assertFalse(measurement_staging_passes_m1(manifest))


def _cross_linked_machines() -> list[dict]:
    """pc-a/10.0.0.1, pc-a/10.0.0.2, pc-b/10.0.0.1 — distinct ids=2 and distinct
    addresses=2, but a single identity<->address connected cluster = one PC."""
    return [
        {'role': 'central-hub', 'identity': 'pc-a', 'address': '10.0.0.1', 'os': 'Win11'},
        {'role': 'chamber-node', 'identity': 'pc-a', 'address': '10.0.0.2', 'os': 'Win11'},
        {'role': 'chamber-node', 'identity': 'pc-b', 'address': '10.0.0.1', 'os': 'Win11'},
    ]


class TestCrossLinkedMachines(unittest.TestCase):
    """Overlapping identity/address rows must not inflate the machine count."""

    def test_cross_linked_rows_fail_two_physical_machines(self):
        manifest = _recorded_pass_manifest()
        manifest['machines'] = _cross_linked_machines()
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertIn('two_physical_machines', unmet)

    def test_cross_linked_rows_fail_full_gate(self):
        manifest = _recorded_pass_manifest()
        manifest['machines'] = _cross_linked_machines()
        self.assertFalse(measurement_staging_passes_m1(manifest))

    def test_cross_linked_rows_are_structurally_valid(self):
        # Three well-formed rows are structurally valid; only the distinctness
        # pass criterion rejects them.
        manifest = _recorded_pass_manifest()
        manifest['machines'] = _cross_linked_machines()
        self.assertTrue(is_measurement_staging_evidence_valid(manifest))

    def test_two_independent_machines_still_pass(self):
        # Regression guard: the legitimate disjoint case must still count as 2.
        manifest = _recorded_pass_manifest()
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertNotIn('two_physical_machines', unmet)


class TestNoFakeGuards(unittest.TestCase):
    def test_recorded_rejects_placeholder(self):
        manifest = _recorded_pass_manifest()
        manifest['equipment']['analyzer_resource'] = TEMPLATE_PLACEHOLDER
        codes = {i.code for i in chamber_measurement_staging_errors(manifest)}
        self.assertIn('placeholder_in_recorded', codes)

    def test_single_machine_fails_pass(self):
        manifest = _recorded_pass_manifest()
        manifest['machines'] = manifest['machines'][:1]
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertIn('two_physical_machines', unmet)

    def test_duplicate_identity_machines_fail_pass(self):
        # Two rows with the SAME identity describe one PC, not two.
        manifest = _recorded_pass_manifest()
        manifest['machines'][1]['identity'] = manifest['machines'][0]['identity']
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertIn('two_physical_machines', unmet)

    def test_duplicate_address_machines_fail_pass(self):
        # Two rows with the SAME address describe one network endpoint, not two.
        manifest = _recorded_pass_manifest()
        manifest['machines'][1]['address'] = manifest['machines'][0]['address']
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertIn('two_physical_machines', unmet)

    def test_missing_appium_fails_pass(self):
        manifest = _recorded_pass_manifest()
        manifest['equipment']['appium_device_id'] = ''
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertIn('real_analyzer_and_appium', unmet)

    def test_non_2xx_start_fails_pass(self):
        manifest = _recorded_pass_manifest()
        manifest['measurement']['start']['http_status'] = 500
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertIn('measurement_post_succeeds', unmet)

    def test_not_in_use_fails_pass(self):
        manifest = _recorded_pass_manifest()
        manifest['measurement']['chamber_in_use_observed'] = False
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertIn('chamber_in_use_transition', unmet)

    def test_progress_fallback_satisfies_when_documented(self):
        manifest = _recorded_pass_manifest()
        manifest['measurement']['progress'] = {
            'via_api': False, 'via_ws': False, 'fallback': 'WS blocked; API poll 2s'}
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertNotIn('progress_api_ws_or_fallback', unmet)

    def test_progress_empty_fails_pass(self):
        manifest = _recorded_pass_manifest()
        manifest['measurement']['progress'] = {'via_api': False, 'via_ws': False, 'fallback': ''}
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertIn('progress_api_ws_or_fallback', unmet)

    def test_progress_api_only_without_fallback_fails_pass(self):
        # API-only without a documented fallback must fail: need BOTH or fallback.
        manifest = _recorded_pass_manifest()
        manifest['measurement']['progress'] = {'via_api': True, 'via_ws': False, 'fallback': ''}
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertIn('progress_api_ws_or_fallback', unmet)

    def test_progress_ws_only_without_fallback_fails_pass(self):
        manifest = _recorded_pass_manifest()
        manifest['measurement']['progress'] = {'via_api': False, 'via_ws': True, 'fallback': ''}
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertIn('progress_api_ws_or_fallback', unmet)

    def test_progress_api_only_with_fallback_passes(self):
        manifest = _recorded_pass_manifest()
        manifest['measurement']['progress'] = {
            'via_api': True, 'via_ws': False, 'fallback': 'WS blocked by firewall; API poll 2s'}
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertNotIn('progress_api_ws_or_fallback', unmet)

    def test_missing_timeline_phase_fails_pass(self):
        # Dropping the completion timeline entry must fail the M1 gate.
        manifest = _recorded_pass_manifest()
        manifest['timeline'] = [e for e in manifest['timeline'] if e['phase'] != 'completion']
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertIn('timeline_phases_complete', unmet)

    def test_placeholder_timeline_timestamp_fails_pass(self):
        # A phase present but with an unfilled timestamp does not count.
        manifest = _recorded_pass_manifest()
        for entry in manifest['timeline']:
            if entry['phase'] == 'progress':
                entry['at'] = TEMPLATE_PLACEHOLDER
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertIn('timeline_phases_complete', unmet)

    def test_controlled_failure_is_an_accepted_outcome(self):
        manifest = _recorded_pass_manifest()
        manifest['measurement']['outcome'] = 'controlled_failure'
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertNotIn('completion_with_correlated_ids', unmet)

    def test_completion_without_correlation_fails(self):
        manifest = _recorded_pass_manifest()
        manifest['correlation'] = {'api': '', 'ws': '', 'log': ''}
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        # correlation is required text → structural error AND pass-criteria miss.
        self.assertIn('completion_with_correlated_ids', unmet)


class TestMeasurementPathProof(unittest.TestCase):
    """SCPI/Appium proof-of-life: operator-attested shape only, never a claim
    that a sweep produced a value."""

    def test_recorded_pass_meets_proof_criterion(self):
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(_recorded_pass_manifest())}
        self.assertNotIn('measurement_path_proof', unmet)

    def test_missing_analyzer_idn_fails_pass(self):
        manifest = _recorded_pass_manifest()
        manifest['equipment']['analyzer_idn'] = ''
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertIn('measurement_path_proof', unmet)

    def test_missing_appium_session_id_fails_pass(self):
        manifest = _recorded_pass_manifest()
        manifest['equipment']['appium_session_id'] = ''
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertIn('measurement_path_proof', unmet)

    def test_dut_not_driven_fails_pass(self):
        manifest = _recorded_pass_manifest()
        manifest['equipment']['dut_driven'] = False
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertIn('measurement_path_proof', unmet)

    def test_placeholder_proof_field_rejected_in_recorded(self):
        manifest = _recorded_pass_manifest()
        manifest['equipment']['analyzer_idn'] = TEMPLATE_PLACEHOLDER
        codes = {i.code for i in chamber_measurement_staging_errors(manifest)}
        self.assertIn('placeholder_in_recorded', codes)

    def test_non_bool_dut_driven_is_structural_error(self):
        manifest = _recorded_pass_manifest()
        manifest['equipment']['dut_driven'] = 'yes'
        codes = {i.code for i in chamber_measurement_staging_errors(manifest)}
        self.assertIn('invalid_dut_driven', codes)

    def test_missing_proof_field_is_structural_error(self):
        manifest = _recorded_pass_manifest()
        del manifest['equipment']['dut_model']
        codes = {i.code for i in chamber_measurement_staging_errors(manifest)}
        self.assertIn('missing_required_field', codes)


class TestFleetPartition(unittest.TestCase):
    """3+ node fleet partition (split-brain). not_run is an honest
    blocked-by-equipment pass; it must never be faked into observed."""

    def test_not_run_is_a_passing_with_honesty_state(self):
        # The canonical pass fixture declares fleet_partition not_run and still
        # clears the full gate — blocked-by-equipment is never a fake pass.
        manifest = _recorded_pass_manifest()
        self.assertEqual(manifest['fleet_partition']['state'], 'not_run')
        self.assertTrue(measurement_staging_passes_m1(manifest))

    def test_observed_also_passes(self):
        manifest = _recorded_pass_manifest()
        manifest['fleet_partition']['state'] = 'observed'
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertNotIn('fleet_partition_captured_or_not_run', unmet)

    def test_missing_state_fails_pass_and_structure(self):
        manifest = _recorded_pass_manifest()
        manifest['fleet_partition']['state'] = ''
        unmet = {c.key for c in measurement_staging_unmet_pass_criteria(manifest)}
        self.assertIn('fleet_partition_captured_or_not_run', unmet)
        codes = {i.code for i in chamber_measurement_staging_errors(manifest)}
        self.assertIn('missing_required_field', codes)

    def test_invalid_state_rejected(self):
        manifest = _recorded_pass_manifest()
        manifest['fleet_partition']['state'] = 'partial'
        codes = {i.code for i in chamber_measurement_staging_errors(manifest)}
        self.assertIn('invalid_scenario_state', codes)

    def test_missing_block_is_structural_error(self):
        manifest = _recorded_pass_manifest()
        del manifest['fleet_partition']
        codes = {i.code for i in chamber_measurement_staging_errors(manifest)}
        self.assertIn('missing_required_field', codes)

    def test_empty_notes_rejected(self):
        # Silent omission of the reason (esp. for not_run) is rejected.
        manifest = _recorded_pass_manifest()
        manifest['fleet_partition']['notes'] = ''
        codes = {i.code for i in chamber_measurement_staging_errors(manifest)}
        self.assertIn('missing_required_field', codes)

    def test_non_int_expected_size_rejected(self):
        manifest = _recorded_pass_manifest()
        manifest['fleet_partition']['expected_fleet_size'] = '3'
        codes = {i.code for i in chamber_measurement_staging_errors(manifest)}
        self.assertIn('invalid_fleet_size', codes)

    def test_below_minimum_expected_size_rejected(self):
        manifest = _recorded_pass_manifest()
        manifest['fleet_partition']['expected_fleet_size'] = MIN_EXPECTED_FLEET_SIZE - 1
        codes = {i.code for i in chamber_measurement_staging_errors(manifest)}
        self.assertIn('invalid_fleet_size', codes)

    def test_bool_expected_size_rejected(self):
        # bool is an int subclass — must not slip through the int check.
        manifest = _recorded_pass_manifest()
        manifest['fleet_partition']['expected_fleet_size'] = True
        codes = {i.code for i in chamber_measurement_staging_errors(manifest)}
        self.assertIn('invalid_fleet_size', codes)


class TestStructuralValidation(unittest.TestCase):
    def test_missing_top_level_field_rejected(self):
        manifest = _recorded_pass_manifest()
        del manifest['equipment']
        codes = {i.code for i in chamber_measurement_staging_errors(manifest)}
        self.assertIn('missing_required_field', codes)

    def test_invalid_token_binding_state_rejected(self):
        manifest = _recorded_pass_manifest()
        manifest['chamber']['token_binding_state'] = 'bogus'
        codes = {i.code for i in chamber_measurement_staging_errors(manifest)}
        self.assertIn('invalid_token_binding_state', codes)

    def test_invalid_timeline_phase_rejected(self):
        manifest = _recorded_pass_manifest()
        manifest['timeline'][0]['phase'] = 'teardown'
        codes = {i.code for i in chamber_measurement_staging_errors(manifest)}
        self.assertIn('invalid_timeline_phase', codes)

    def test_bool_http_status_rejected(self):
        manifest = _recorded_pass_manifest()
        manifest['measurement']['start']['http_status'] = True
        codes = {i.code for i in chamber_measurement_staging_errors(manifest)}
        self.assertIn('invalid_http_status', codes)


class TestSchemaParity(unittest.TestCase):
    """The committed JSON Schema vocabulary must equal the Python SSOT."""

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        self.props = self.schema['properties']

    def test_mode_enum_matches(self):
        self.assertEqual(set(self.props['mode']['enum']), set(EVIDENCE_MODES))

    def test_token_binding_enum_matches(self):
        chamber = self.props['chamber']['properties']
        self.assertEqual(set(chamber['token_binding_state']['enum']), set(TOKEN_BINDING_STATES))

    def test_outcome_enum_matches_plus_empty(self):
        outcome = self.props['measurement']['properties']['outcome']['enum']
        self.assertEqual(set(outcome), set(MEASUREMENT_OUTCOMES) | {''})

    def test_timeline_phase_enum_matches(self):
        phase = self.props['timeline']['items']['properties']['phase']['enum']
        self.assertEqual(set(phase), set(TIMELINE_PHASES))

    def test_scenario_state_enum_matches(self):
        rp = self.props['restart_partition']['properties']
        self.assertEqual(set(rp['node_restart']['enum']), set(SCENARIO_STATES))
        self.assertEqual(set(rp['network_partition']['enum']), set(SCENARIO_STATES))

    def test_fleet_partition_state_enum_matches(self):
        fp = self.props['fleet_partition']['properties']
        self.assertEqual(set(fp['state']['enum']), set(SCENARIO_STATES))

    def test_fleet_partition_expected_size_minimum_matches(self):
        fp = self.props['fleet_partition']['properties']
        self.assertEqual(fp['expected_fleet_size']['minimum'], MIN_EXPECTED_FLEET_SIZE)

    def test_schema_version_const_matches(self):
        self.assertEqual(self.props['schema_version']['const'], SCHEMA_VERSION)

    def test_equipment_required_covers_proof_of_life(self):
        equipment = self.props['equipment']
        for field in ('analyzer_idn', 'appium_session_id', 'dut_model', 'dut_driven'):
            self.assertIn(field, equipment['required'])
            self.assertIn(field, equipment['properties'])

    def test_fleet_partition_is_top_level_required(self):
        self.assertIn('fleet_partition', self.schema['required'])


class TestModulePurity(unittest.TestCase):
    """Evidence SSOT stays dependency-free (frozen-exe safe)."""

    _FORBIDDEN = {'psycopg', 'psycopg2', 'asyncpg', 'pyvisa', 'openpyxl',
                  'pandas', 'PySide6', 'fastapi', 'sqlalchemy'}

    def test_module_dependency_free(self):
        tree = ast.parse(EVIDENCE_MODULE.read_text(encoding='utf-8'))
        names: set = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        offenders = {n for n in names if n.split('.')[0] in self._FORBIDDEN}
        self.assertEqual(set(), offenders)


class TestGateCli(unittest.TestCase):
    def test_template_subcommand_writes_valid_non_passing_skeleton(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'template.json'
            rc = gate.main(['--template', '--output', str(out)])
            self.assertEqual(rc, 0)
            manifest = json.loads(out.read_text(encoding='utf-8'))
            self.assertTrue(is_measurement_staging_evidence_valid(manifest))
            self.assertTrue(measurement_staging_unmet_pass_criteria(manifest))

    def test_validate_recorded_pass_with_require_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / 'attempt.json'
            bundle.write_text(json.dumps(_recorded_pass_manifest()), encoding='utf-8')
            self.assertEqual(gate.main(['--require-pass', '--validate', str(bundle)]), 0)

    def test_validate_template_with_require_pass_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'template.json'
            gate.main(['--template', '--output', str(out)])
            # structure-only passes, but the M1 gate must reject the unfilled template.
            self.assertEqual(gate.main(['--validate', str(out)]), 0)
            self.assertEqual(gate.main(['--require-pass', '--validate', str(out)]), 1)

    def test_validate_filled_template_fails_require_pass(self):
        # Issue 1 seal: a fully-filled bundle left in mode='template' is
        # structurally valid and meets every measurement criterion, but
        # --require-pass must still exit non-zero (recorded-mode gate).
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / 'filled-template.json'
            bundle.write_text(json.dumps(_filled_template_manifest()), encoding='utf-8')
            self.assertEqual(gate.main(['--validate', str(bundle)]), 0)
            self.assertEqual(gate.main(['--require-pass', '--validate', str(bundle)]), 1)

    def test_validate_cross_linked_machines_fails_require_pass(self):
        # Issue 2 seal: overlapping identity/address rows must fail --require-pass.
        with tempfile.TemporaryDirectory() as tmp:
            manifest = _recorded_pass_manifest()
            manifest['machines'] = _cross_linked_machines()
            bundle = Path(tmp) / 'cross-linked.json'
            bundle.write_text(json.dumps(manifest), encoding='utf-8')
            self.assertEqual(gate.main(['--validate', str(bundle)]), 0)
            self.assertEqual(gate.main(['--require-pass', '--validate', str(bundle)]), 1)

    def test_validate_placeholder_in_non_criterion_field_fails_require_pass(self):
        # Codex seal: a recorded bundle meeting all 8 criteria but carrying
        # <FILL-ME> in a non-criterion field (central_stack_version) is
        # structurally invalid. --require-pass must exit non-zero AND the
        # reported passes_m1 flag must be false (no false pass).
        with tempfile.TemporaryDirectory() as tmp:
            manifest = _recorded_pass_manifest()
            manifest['central_stack_version'] = TEMPLATE_PLACEHOLDER
            bundle = Path(tmp) / 'placeholder-noncriterion.json'
            bundle.write_text(json.dumps(manifest), encoding='utf-8')
            self.assertEqual(gate.main(['--validate', str(bundle)]), 1)
            self.assertEqual(
                gate.main(['--require-pass', '--validate', str(bundle)]), 1)
            self.assertFalse(measurement_staging_passes_m1(manifest))

    def test_validate_structurally_broken_bundle_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / 'broken.json'
            bundle.write_text(json.dumps({'schema_version': SCHEMA_VERSION}), encoding='utf-8')
            self.assertEqual(gate.main(['--validate', str(bundle)]), 1)

    def test_missing_bundle_returns_usage_error(self):
        self.assertEqual(gate.main(['--validate', str(ROOT / 'no-such-bundle.json')]), 2)

    def test_template_without_output_is_usage_error(self):
        self.assertEqual(gate.main(['--template']), 2)




if __name__ == '__main__':
    unittest.main()


# ⚠️ **`TestRunbookExists` 는 2026-09-03 에 여기서 지웠다 — 중복이었다.**
#
# 추출(2026-08-30)이 이 파일을 두 레인에 복사했는데, 그 클래스가 읽는
# `docs/operations/chamber-real-measurement-staging-runbook.md` 는 **provider
# 저장소에만 있다.** 그래서 이쪽 사본은 배송 이래 red 였고 기준선이 그것을
# 선언된 부채로 지고 있었다.
#
# 이관이 아니라 **중복 제거**다 — provider 저장소의
# `tests/test_chamber_measurement_staging_evidence.py` 에 같은 클래스·같은 이름·
# 같은 단언이 그대로 있고(교차 확인 2026-09-03), 그쪽은 대상 런북을 갖는다.
# 게다가 그쪽 사본이 **더 강하다** — 우리가 지적한 공허성(런북이 게이트 파일
# 이름을 «글자»로만 적으면 그 파일이 사라져도 초록)을 그쪽이 별도 검사로 받았다.
#
# 이 파일의 나머지 12개 클래스는 이 레인에서 의미가 있으므로 남는다.
