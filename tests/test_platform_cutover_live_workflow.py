import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from scripts.platform_cutover_bundle import EVIDENCE_FILENAMES
from scripts.platform_cutover_live_workflow import (
    CommandResult,
    build_values_template,
    build_workflow_template,
    render_workflow_config,
    run_workflow,
    validate_config,
)
from scripts.platform_cutover_workflow_hints import suggested_command

from fcc_test_contracts.common.tree_artifacts import resolve_dependency_artifact


project_root = Path(__file__).parent.parent


class TestPlatformCutoverLiveWorkflow(unittest.TestCase):
    def test_default_suggested_commands_match_existing_cli_entrypoints(self):
        required_flags = {
            'hardware_smoke': {'--db-path', '--job-id', '--output'},
            'db_migration': {'--dsn', '--output', '--applied-by', '--require-valid'},
            'ingestion_execution': {
                '--dsn',
                '--plan',
                '--provider-id',
                '--session-id',
                '--database-name',
                '--evidence-id',
                '--output',
            },
            'artifact_sync': {
                '--source-root',
                '--destination-root',
                '--source-root-id',
                '--destination-root-id',
                '--provider-id',
                '--session-id',
                '--sync-job-id',
                '--records-json',
                '--output',
                '--require-valid',
            },
            'db_only_report_reconstruction': {
                '--dsn',
                '--provider-id',
                '--session-id',
                '--report-run-id',
                '--migration-evidence-id',
                '--ingestion-batch-id',
                '--output-dir',
                '--reviewed-by',
                '--output',
            },
            'backup_restore_drill': {
                '--restore-point-id',
                '--database-name',
                '--db-backup-file',
                '--source-root',
                '--backup-root',
                '--restore-root',
                '--output',
                '--require-valid',
            },
            'identity_policy': {'--manifest', '--output', '--require-valid'},
            'service_deployment': {
                '--provider-id',
                '--host-id',
                '--deployed-at',
                '--service-manager',
                '--start-mode',
                '--log-collector',
                '--log-retention-days',
                '--alert-channel',
                '--alert-target',
                '--alert-interval-seconds',
                '--output',
                '--require-valid',
            },
            'idp_deployment': {
                '--evidence-id',
                '--provider-key',
                '--issuer',
                '--client-id',
                '--redirect-uri',
                '--scope',
                '--output',
                '--require-valid',
            },
            'rbac_assignment': {'--dsn', '--output', '--evidence-id', '--require-valid'},
            'extraction_package': {
                '--target-root',
                '--output',
                '--evidence-id',
                # One repeatable flag now, derived from the manifest's extraction
                # targets rather than one hand-added flag per lane.
                '--target-ref',
                '--require-valid',
            },
            'performance_smoke': {
                '--target-base-url',
                '--benchmark-id',
                '--iterations',
                '--workers',
                '--max-p95-ms',
                '--output',
            },
            'frontend_deployment': {
                '--evidence-id',
                '--app-url',
                '--backend-base-url',
                '--hosting-provider',
                '--build-version',
                '--build-root',
                '--environment-name',
                '--output',
                '--require-valid',
            },
            'frontend_browser_qa': {
                '--app-url',
                '--provider-api-url',
                '--evidence-id',
                '--output',
                '--browser',
                '--auth-flow-verified',
                '--require-valid',
            },
        }
        for evidence_key, filename in EVIDENCE_FILENAMES.items():
            with self.subTest(evidence_key=evidence_key):
                command = suggested_command(evidence_key, f'artifacts/cutover/evidence/{filename}')
                self.assertEqual(command[0], 'python')
                self.assertTrue((project_root / command[1]).is_file(), command)
                self.assertIn(f'artifacts/cutover/evidence/{filename}', command)
                self.assertTrue(required_flags[evidence_key].issubset(set(command)))

    def test_checked_in_workflow_example_uses_current_suggested_commands(self):
        example = json.loads(
            (project_root / 'docs' / 'examples' / 'cutover_live_workflow.example.json')
            .read_text(encoding='utf-8')
        )

        for step in example['steps']:
            evidence_key = step['evidence_key']
            expected = suggested_command(
                evidence_key,
                f"artifacts/cutover/evidence/{EVIDENCE_FILENAMES[evidence_key]}",
            )
            self.assertEqual(step['suggested_command'], expected, evidence_key)

    def test_validate_config_rejects_unknown_key_and_output_mismatch(self):
        config = _config(Path('artifacts/cutover'))
        config['steps'][0]['evidence_key'] = 'unknown'
        config['steps'][0]['output'] = 'wrong.json'
        config['steps'][0]['timeout_seconds'] = 0
        config['steps'][0]['retries'] = -1
        config['steps'][0]['retry_backoff_seconds'] = -0.1
        config['steps'][0]['skip_if_output_exists'] = 'yes'
        config['steps'][0]['env'] = ['not-an-object']
        config['stop_on_failure'] = 'yes'

        codes = {issue['code'] for issue in validate_config(config)}

        self.assertIn('unknown_evidence_key', codes)
        self.assertIn('invalid_timeout', codes)
        self.assertIn('invalid_retries', codes)
        self.assertIn('invalid_retry_backoff', codes)
        self.assertIn('workflow_config_legacy_skip_if_output_exists', codes)
        self.assertIn('invalid_env', codes)
        self.assertIn('invalid_stop_on_failure', codes)

    def test_run_executes_command_with_retry_and_then_audits_evidence_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _config(root / 'evidence')
            calls = []

            def runner(command, cwd, timeout):
                calls.append((command, cwd, timeout))
                output_path = root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']
                output_path.parent.mkdir(parents=True, exist_ok=True)
                _write_valid_ingestion_manifest(output_path)
                return CommandResult(returncode=0, stdout='ok', stderr='')

            summary = run_workflow(config, execute=True, command_runner=runner)

        self.assertTrue(summary['config_valid'])
        self.assertEqual(len(summary['config_sha256']), 64)
        self.assertTrue(summary['workflow_succeeded'])
        self.assertEqual(summary['step_counts']['total'], 1)
        self.assertEqual(summary['step_counts']['succeeded'], 1)
        self.assertEqual(summary['step_counts']['output_exists'], 1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(summary['steps'][0]['output_exists'])
        expected_output = json.dumps(_valid_ingestion_manifest()).encode('utf-8')
        self.assertEqual(summary['steps'][0]['output_size_bytes'], len(expected_output))
        self.assertEqual(summary['steps'][0]['output_sha256'], hashlib.sha256(expected_output).hexdigest())
        self.assertFalse(summary['completion_audit']['complete'])
        self.assertEqual(summary['completion_audit']['decision'], 'not_complete')
        self.assertFalse(summary['bundle_output']['written'])
        self.assertEqual(summary['bundle_output']['reason'], 'completion_audit_not_complete')
        self.assertEqual(
            summary['completion_audit']['workflow_hints']['ingestion_execution']['step_id'],
            'live-ingestion',
        )

    def test_run_retries_failed_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(Path(tmpdir) / 'evidence')
            config['steps'][0]['retry_backoff_seconds'] = 0.25
            attempts = []
            sleeps = []

            def runner(command, cwd, timeout):
                attempts.append(command)
                return CommandResult(returncode=1, stdout='', stderr='temporary failure')

            summary = run_workflow(config, execute=True, command_runner=runner, sleeper=sleeps.append)

        self.assertFalse(summary['workflow_succeeded'])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(sleeps, [0.25])
        self.assertEqual(summary['steps'][0]['retry_backoff_seconds'], 0.25)
        self.assertEqual(summary['steps'][0]['attempts'][1]['returncode'], 1)

    def test_run_stops_downstream_steps_after_failure_when_configured(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(Path(tmpdir) / 'evidence')
            config['stop_on_failure'] = True
            config['steps'][0]['retries'] = 0
            config['steps'].append({
                'id': 'hardware-smoke',
                'evidence_key': 'hardware_smoke',
                'output': EVIDENCE_FILENAMES['hardware_smoke'],
                'command': [
                    sys.executable,
                    '-c',
                    'print("hardware")',
                    '--output',
                    str(Path(tmpdir) / 'evidence' / EVIDENCE_FILENAMES['hardware_smoke']),
                ],
                'timeout_seconds': 5,
                'retries': 0,
            })
            calls = []

            def runner(command, cwd, timeout):
                calls.append(command)
                return CommandResult(returncode=1, stderr='failed')

            summary = run_workflow(config, execute=True, command_runner=runner)

        self.assertFalse(summary['workflow_succeeded'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(summary['step_counts']['failed'], 1)
        self.assertEqual(summary['step_counts']['skipped'], 1)
        self.assertFalse(summary['steps'][0]['succeeded'])
        self.assertTrue(summary['steps'][1]['skipped'])
        self.assertEqual(summary['steps'][1]['skip_reason'], 'previous_step_failed')

    def test_run_does_not_treat_existing_output_as_success_without_receipt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _config(root / 'evidence')
            config['steps'][0]['reuse_verified_output'] = True
            config['steps'][0]['retries'] = 0
            output_path = root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text('{}', encoding='utf-8')
            calls = []

            def runner(command, cwd, timeout):
                calls.append(command)
                return CommandResult(returncode=1)

            summary = run_workflow(config, execute=True, command_runner=runner)

        self.assertEqual(len(calls), 1)
        self.assertFalse(summary['workflow_succeeded'])
        self.assertFalse(summary['steps'][0]['succeeded'])
        self.assertFalse(summary['steps'][0]['skipped'])
        self.assertIn('receipt_missing', {issue['code'] for issue in summary['steps'][0]['diagnostics']})
        self.assertEqual(summary['steps'][0]['output_sha256'], hashlib.sha256(b'{}').hexdigest())

    def test_exit_zero_noop_cannot_launder_valid_stale_output_into_receipt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _config(root / 'evidence')
            config['steps'][0]['reuse_verified_output'] = True
            output_path = root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']
            _write_valid_ingestion_manifest(output_path)
            original_bytes = output_path.read_bytes()
            calls = []

            def no_op_collector(command, cwd, timeout):
                calls.append(command)
                return CommandResult(returncode=0, stdout='collector did nothing')

            summary = run_workflow(config, execute=True, command_runner=no_op_collector)
            receipt_path = Path(summary['steps'][0]['receipt_path'])

            self.assertEqual(len(calls), 1)
            self.assertTrue(summary['config_valid'])
            self.assertFalse(summary['workflow_succeeded'])
            self.assertFalse(summary['steps'][0]['succeeded'])
            self.assertFalse(summary['steps'][0]['receipt_written'])
            self.assertFalse(receipt_path.exists())
            self.assertIn(
                'collector_output_not_created',
                {issue['code'] for issue in summary['steps'][0]['diagnostics']},
            )
            self.assertEqual(output_path.read_bytes(), original_bytes)

    def test_first_quarantine_replace_failure_stops_before_collector_and_receipt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _config(root / 'evidence')
            output_path = root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']
            _write_valid_ingestion_manifest(output_path)
            original_bytes = output_path.read_bytes()
            replace_calls = []
            collector_calls = []
            real_replace = __import__('os').replace

            def fail_first_replace(source, destination):
                replace_calls.append((source, destination))
                if len(replace_calls) == 1:
                    raise OSError('transient quarantine failure')
                return real_replace(source, destination)

            def collector(command, cwd, timeout):
                collector_calls.append(command)
                return CommandResult(returncode=0)

            with mock.patch('scripts.platform_cutover_live_workflow.os.replace', side_effect=fail_first_replace):
                summary = run_workflow(config, execute=True, command_runner=collector)

            receipt_path = Path(summary['steps'][0]['receipt_path'])
            step = summary['steps'][0]
            remaining_bytes = output_path.read_bytes()

        self.assertEqual(len(replace_calls), 1)
        self.assertEqual(collector_calls, [])
        self.assertFalse(summary['workflow_succeeded'])
        self.assertFalse(step['succeeded'])
        self.assertFalse(step['collector_invoked'])
        self.assertFalse(step['receipt_written'])
        self.assertFalse(receipt_path.exists())
        self.assertEqual(remaining_bytes, original_bytes)
        self.assertIn('quarantine_failed', {issue['code'] for issue in step['diagnostics']})

    def test_run_reuses_matching_receipt_without_invoking_collector(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _config(root / 'evidence')
            config['steps'][0]['reuse_verified_output'] = True
            calls = []

            def collector(command, cwd, timeout):
                calls.append(command)
                _write_valid_ingestion_manifest(
                    root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']
                )
                return CommandResult(returncode=0, stdout='collected')

            first = run_workflow(
                config,
                execute=True,
                command_runner=collector,
                receipt_clock=lambda: '2026-08-23T00:00:00Z',
            )
            receipt_path = Path(first['steps'][0]['receipt_path'])
            receipt_before = receipt_path.read_text(encoding='utf-8')

            def should_not_run(command, cwd, timeout):
                raise AssertionError('verified receipt should avoid collector invocation')

            second = run_workflow(
                config,
                execute=True,
                command_runner=should_not_run,
                receipt_clock=lambda: '2026-08-23T00:01:00Z',
            )

            self.assertTrue(first['workflow_succeeded'])
            self.assertTrue(second['workflow_succeeded'])
            self.assertEqual(len(calls), 1)
            self.assertTrue(second['steps'][0]['reused_verified_output'])
            self.assertEqual(second['steps'][0]['resume_status'], 'reused_verified_output')
            self.assertFalse(second['steps'][0]['collector_invoked'])
            self.assertFalse(second['steps'][0]['receipt_written'])
            self.assertEqual(receipt_path.read_text(encoding='utf-8'), receipt_before)
            self.assertFalse(second['completion_audit']['complete'])

    def test_invalid_collector_output_blocks_receipt_creation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _config(root / 'evidence')

            def collector(command, cwd, timeout):
                output = root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text('{}', encoding='utf-8')
                return CommandResult(returncode=0)

            summary = run_workflow(config, execute=True, command_runner=collector)

            self.assertFalse(summary['workflow_succeeded'])
            self.assertTrue(summary['steps'][0]['command_succeeded'])
            self.assertFalse(summary['steps'][0]['validation_passed'])
            self.assertFalse(summary['steps'][0]['receipt_written'])
            self.assertFalse(Path(summary['steps'][0]['receipt_path']).exists())
            self.assertIn('invalid_schema_version', {issue['code'] for issue in summary['steps'][0]['diagnostics']})

    def test_corrupt_receipt_cannot_reuse_and_is_reported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _config(root / 'evidence')
            config['steps'][0]['reuse_verified_output'] = True

            def first_collector(command, cwd, timeout):
                _write_valid_ingestion_manifest(
                    root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']
                )
                return CommandResult(returncode=0)

            first = run_workflow(config, execute=True, command_runner=first_collector)
            Path(first['steps'][0]['receipt_path']).write_text('{"truncated":', encoding='utf-8')
            calls = []

            def second_collector(command, cwd, timeout):
                calls.append(command)
                _write_valid_ingestion_manifest(
                    root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']
                )
                return CommandResult(returncode=0)

            second = run_workflow(config, execute=True, command_runner=second_collector)

            self.assertEqual(len(calls), 1)
            self.assertTrue(second['workflow_succeeded'])
            self.assertIn('receipt_corrupt', {issue['code'] for issue in second['steps'][0]['diagnostics']})
            self.assertFalse(second['steps'][0]['reused_verified_output'])

    def test_run_treats_missing_evidence_output_as_step_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(Path(tmpdir) / 'evidence')

            summary = run_workflow(
                config,
                execute=True,
                command_runner=lambda command, cwd, timeout: CommandResult(returncode=0, stdout='ok'),
            )

        self.assertFalse(summary['workflow_succeeded'])
        self.assertTrue(summary['steps'][0]['command_succeeded'])
        self.assertFalse(summary['steps'][0]['output_exists'])
        self.assertIsNone(summary['steps'][0]['output_size_bytes'])
        self.assertEqual(summary['steps'][0]['output_sha256'], '')
        self.assertFalse(summary['steps'][0]['succeeded'])

    def test_run_summary_redacts_sensitive_command_values_and_output_tails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            secret_dsn = 'postgresql://platform:secret-password@db.internal/live'
            secret_env = 'CLIENT_SECRET=super-secret-client-value'
            bearer_token = 'live-bearer-token-value'
            client_secret = 'live-client-secret-value'
            config = _config(root / 'evidence')
            config['steps'][0]['command'] = [
                sys.executable,
                '-c',
                'print("collect")',
                '--dsn',
                secret_dsn,
                '--environment-variable',
                secret_env,
                '--bearer-token',
                bearer_token,
                f'--client-secret={client_secret}',
                '--output',
                str(root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']),
            ]
            config['steps'][0]['suggested_command'] = [
                'python',
                'scripts/platform_frontend_browser_qa.py',
                '--bearer-token',
                bearer_token,
                f'--client-secret={client_secret}',
                '--output',
                str(root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']),
            ]

            def runner(command, cwd, timeout):
                output_path = root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']
                output_path.parent.mkdir(parents=True, exist_ok=True)
                _write_valid_ingestion_manifest(output_path)
                return CommandResult(
                    returncode=0,
                    stdout=f'connected to {secret_dsn} with {bearer_token}',
                    stderr=f'using {secret_dsn} and {client_secret}',
                )

            summary = run_workflow(config, execute=True, command_runner=runner)

        step = summary['steps'][0]
        self.assertTrue(step['succeeded'])
        self.assertNotIn(secret_dsn, json.dumps(summary))
        self.assertNotIn(secret_env, json.dumps(summary))
        self.assertNotIn(bearer_token, json.dumps(summary))
        self.assertNotIn(client_secret, json.dumps(summary))
        self.assertEqual(step['command'][4], '<redacted>')
        self.assertEqual(step['command'][6], 'CLIENT_SECRET=<redacted>')
        self.assertEqual(step['command'][8], '<redacted>')
        self.assertEqual(step['command'][9], '--client-secret=<redacted>')
        self.assertEqual(step['suggested_command'][3], '<redacted>')
        self.assertEqual(step['suggested_command'][4], '--client-secret=<redacted>')
        workflow_hint = summary['completion_audit']['workflow_hints']['ingestion_execution']
        self.assertEqual(workflow_hint['suggested_command'][3], '<redacted>')
        self.assertEqual(workflow_hint['suggested_command'][4], '--client-secret=<redacted>')
        self.assertIn('<redacted>', step['attempts'][0]['stdout_tail'])
        self.assertIn('<redacted>', step['attempts'][0]['stderr_tail'])

    def test_run_passes_step_env_and_redacts_secret_env_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            secret_token = 'env-token-value'
            config = _config(root / 'evidence')
            config['steps'][0]['env'] = {
                'FCC_HEADLESS_AUTH_TOKEN': secret_token,
                'FCC_HEADLESS_PROVIDER_ID': 'fcc-unlicensed-conducted',
            }
            calls = []

            def runner(command, cwd, timeout, env):
                calls.append(env)
                output_path = root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']
                output_path.parent.mkdir(parents=True, exist_ok=True)
                _write_valid_ingestion_manifest(output_path)
                return CommandResult(returncode=0, stdout=f'token {secret_token}', stderr='')

            summary = run_workflow(config, execute=True, command_runner=runner)

        self.assertTrue(summary['steps'][0]['succeeded'])
        self.assertEqual(calls[0]['FCC_HEADLESS_AUTH_TOKEN'], secret_token)
        self.assertEqual(calls[0]['FCC_HEADLESS_PROVIDER_ID'], 'fcc-unlicensed-conducted')
        self.assertEqual(summary['steps'][0]['env']['FCC_HEADLESS_AUTH_TOKEN'], '<redacted>')
        self.assertEqual(summary['steps'][0]['env']['FCC_HEADLESS_PROVIDER_ID'], 'fcc-unlicensed-conducted')
        self.assertNotIn(secret_token, json.dumps(summary))

    def test_run_requires_both_context_paths_before_invoking_a_collector(self):
        config = _config(Path('artifacts/cutover/evidence'))
        config.pop('central_db_schema')
        config.pop('extraction_manifest')
        calls = []

        summary = run_workflow(
            config,
            execute=True,
            command_runner=lambda command, cwd, timeout: calls.append(command) or CommandResult(0),
        )

        self.assertFalse(summary['config_valid'])
        self.assertEqual(calls, [])
        codes = {issue['code'] for issue in summary['config_errors']}
        self.assertIn('missing_required_context', codes)
        self.assertEqual(summary['validation_context_issues'][0]['code'], 'missing_required_context')

    def test_context_loader_failures_are_opaque_in_plan_and_run_summaries(self):
        hostile_failures = (
            'postgresql://user:dsn-password@private.example/live',
            'https://alice:url-password@private.example/callback',
            '/var/lib/fcc/token-like/bearer-token-value.json',
            'ARBITRARY_CONTEXT_EXCEPTION_SENTINEL',
        )

        for context_name in ('central_db_schema', 'extraction_manifest'):
            for failure_text in hostile_failures:
                with self.subTest(context_name=context_name, failure_text=failure_text):
                    with tempfile.TemporaryDirectory() as tmpdir:
                        config = _config(Path(tmpdir) / 'evidence')
                        config[context_name] = failure_text

                        def failing_loader(_path, message=failure_text):
                            raise RuntimeError(message)

                        for execute in (False, True):
                            summary = run_workflow(
                                config,
                                execute=execute,
                                json_loader=failing_loader,
                                command_runner=lambda command, cwd, timeout: CommandResult(0),
                            )
                            serialized = json.dumps(summary, sort_keys=True)
                            self.assertIn('context_read_error', serialized)
                            self.assertNotIn(failure_text, serialized)
                            self.assertNotIn('dsn-password', serialized)
                            self.assertNotIn('url-password', serialized)
                            self.assertNotIn('bearer-token-value', serialized)
                            self.assertNotIn('ARBITRARY_CONTEXT_EXCEPTION_SENTINEL', serialized)

    def test_changed_retry_config_does_not_reuse_a_receipt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _config(root / 'evidence')
            config['steps'][0]['reuse_verified_output'] = True
            calls = []

            def collector(command, cwd, timeout):
                calls.append(command)
                _write_valid_ingestion_manifest(
                    root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']
                )
                return CommandResult(0)

            first = run_workflow(config, execute=True, command_runner=collector)
            changed = json.loads(json.dumps(config))
            changed['steps'][0]['retry_backoff_seconds'] = 120
            second = run_workflow(changed, execute=True, command_runner=collector)

        self.assertTrue(first['workflow_succeeded'])
        self.assertTrue(second['workflow_succeeded'])
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(first['config_sha256'], second['config_sha256'])
        self.assertFalse(second['steps'][0]['reused_verified_output'])

    def test_changed_context_digest_does_not_reuse_a_receipt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            central_copy = root / 'central-schema.json'
            extraction_copy = root / 'extraction-manifest.json'
            central_source = project_root / 'docs' / 'platform' / 'central_db_schema.v1.json'
            extraction_source = resolve_dependency_artifact(
                'docs/api/headless_contract_extraction_manifest.v1.json'
            )
            central_copy.write_text(central_source.read_text(encoding='utf-8'), encoding='utf-8')
            extraction_copy.write_text(extraction_source.read_text(encoding='utf-8'), encoding='utf-8')
            config = _config(root / 'evidence')
            config.update({
                'central_db_schema': str(central_copy),
                'extraction_manifest': str(extraction_copy),
            })
            config['steps'][0]['reuse_verified_output'] = True
            calls = []

            def collector(command, cwd, timeout):
                calls.append(command)
                _write_valid_ingestion_manifest(
                    root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']
                )
                return CommandResult(0)

            first = run_workflow(config, execute=True, command_runner=collector)
            central_copy.write_text(
                central_copy.read_text(encoding='utf-8') + '\n',
                encoding='utf-8',
            )
            second = run_workflow(config, execute=True, command_runner=collector)

        self.assertTrue(first['workflow_succeeded'])
        self.assertTrue(second['workflow_succeeded'])
        self.assertEqual(len(calls), 2)
        self.assertFalse(second['steps'][0]['reused_verified_output'])

    def test_run_rejects_unresolved_template_placeholders_without_executing(self):
        config = build_workflow_template(
            evidence_root='artifacts/cutover/evidence',
            provider_id='fcc-unlicensed-conducted',
            cutover_candidate_id='cutover-test',
            evaluated_at='2026-05-15T13:30:00+09:00',
            collected_at='2026-05-15T13:30:00+09:00',
            central_db_schema='docs/platform/central_db_schema.v1.json',
            extraction_manifest='docs/api/headless_contract_extraction_manifest.v1.json',
        )
        calls = []

        def runner(command, cwd, timeout):
            calls.append(command)
            return CommandResult(returncode=0)

        summary = run_workflow(config, execute=True, command_runner=runner)

        self.assertFalse(summary['config_valid'])
        self.assertFalse(summary['workflow_succeeded'])
        self.assertEqual(calls, [])
        self.assertIn('unresolved_placeholder', {issue['code'] for issue in summary['config_errors']})

    def test_plan_reports_execution_preflight_issues_without_failing_config(self):
        config = build_workflow_template(
            evidence_root='artifacts/cutover/evidence',
            provider_id='fcc-unlicensed-conducted',
            cutover_candidate_id='cutover-test',
            evaluated_at='2026-05-15T13:30:00+09:00',
            collected_at='2026-05-15T13:30:00+09:00',
            central_db_schema='docs/platform/central_db_schema.v1.json',
            extraction_manifest='docs/api/headless_contract_extraction_manifest.v1.json',
        )

        summary = run_workflow(config, execute=False)

        self.assertTrue(summary['config_valid'])
        self.assertTrue(summary['workflow_succeeded'])
        codes = {issue['code'] for issue in summary['execution_preflight_issues']}
        self.assertIn('unresolved_placeholder', codes)
        self.assertIn('missing_output_argument', codes)

    def test_run_rejects_suggested_command_placeholder_values_after_copy(self):
        config = _config(Path('artifacts/cutover/evidence'))
        config['steps'][0]['command'] = [
            'python',
            'scripts/platform_ingestion_execution_evidence.py',
            'execute',
            '--dsn',
            '<postgres-dsn>',
            '--output',
            EVIDENCE_FILENAMES['ingestion_execution'],
        ]

        summary = run_workflow(config, execute=True, command_runner=lambda command, cwd, timeout: CommandResult(0))

        self.assertFalse(summary['config_valid'])
        self.assertIn('unresolved_placeholder', {issue['code'] for issue in summary['config_errors']})

    def test_run_rejects_command_missing_expected_output_path_before_execution(self):
        config = _config(Path('artifacts/cutover/evidence'))
        config['steps'][0]['command'] = [sys.executable, '-c', 'print("collect")']
        calls = []

        def runner(command, cwd, timeout):
            calls.append(command)
            return CommandResult(0)

        summary = run_workflow(config, execute=True, command_runner=runner)

        self.assertFalse(summary['config_valid'])
        self.assertEqual(calls, [])
        self.assertIn('missing_output_argument', {issue['code'] for issue in summary['config_errors']})

    def test_cli_plan_writes_summary_without_executing_commands(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / 'workflow.json'
            summary_path = root / 'summary.json'
            config = _config(root / 'evidence')
            config['steps'][0]['suggested_command'] = [
                sys.executable,
                'scripts/platform_ingestion_execution_evidence.py',
                'execute',
            ]
            config_path.write_text(json.dumps(config), encoding='utf-8')

            completed = subprocess.run(
                [
                    sys.executable,
                    'scripts/platform_cutover_live_workflow.py',
                    'plan',
                    '--config', str(config_path),
                    '--summary-output', str(summary_path),
                ],
                cwd=str(project_root),
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )

            summary = json.loads(summary_path.read_text(encoding='utf-8'))

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertEqual(summary['mode'], 'plan')
        self.assertFalse(summary['bundle_output']['written'])
        self.assertEqual(summary['bundle_output']['reason'], 'plan_mode')
        self.assertFalse(summary['completion_audit']['complete'])
        self.assertEqual(
            summary['steps'][0]['suggested_command'][1],
            'scripts/platform_ingestion_execution_evidence.py',
        )
        self.assertIn('ingestion_execution', summary['completion_audit']['workflow_hints'])

    def test_template_covers_all_cutover_evidence_keys_and_is_config_valid(self):
        config = build_workflow_template(
            evidence_root='artifacts/cutover/evidence',
            provider_id='fcc-unlicensed-conducted',
            cutover_candidate_id='cutover-test',
            evaluated_at='2026-05-15T13:30:00+09:00',
            collected_at='2026-05-15T13:30:00+09:00',
            central_db_schema='docs/platform/central_db_schema.v1.json',
            extraction_manifest='docs/api/headless_contract_extraction_manifest.v1.json',
        )

        self.assertEqual(validate_config(config), [])
        self.assertEqual(
            {step['evidence_key'] for step in config['steps']},
            set(EVIDENCE_FILENAMES),
        )
        self.assertEqual(
            {step['output'] for step in config['steps']},
            set(EVIDENCE_FILENAMES.values()),
        )
        self.assertEqual(config['bundle_output'], 'artifacts/cutover/final_cutover_bundle.json')
        self.assertTrue(config['stop_on_failure'])
        self.assertEqual({step['command'][0] for step in config['steps']}, {'python'})
        self.assertEqual({step['reuse_verified_output'] for step in config['steps']}, {True})
        self.assertEqual({step['retry_backoff_seconds'] for step in config['steps']}, {0})
        by_key = {step['evidence_key']: step for step in config['steps']}
        self.assertEqual(by_key['db_migration']['suggested_command'][1], 'scripts/platform_db_migration_runner.py')
        self.assertIn('artifacts/cutover/evidence/db_migration.json', by_key['db_migration']['suggested_command'])
        self.assertEqual(by_key['hardware_smoke']['suggested_command'][1], 'scripts/headless_hardware_smoke_evidence.py')
        self.assertIn('artifacts/cutover/evidence/hardware_smoke.json', by_key['hardware_smoke']['suggested_command'])
        self.assertEqual(by_key['performance_smoke']['suggested_command'][1], 'scripts/headless_api_benchmark.py')
        self.assertIn('artifacts/cutover/evidence/performance_smoke.json', by_key['performance_smoke']['suggested_command'])
        self.assertEqual(by_key['artifact_sync']['suggested_command'][1], 'scripts/platform_artifact_sync_worker.py')
        self.assertEqual(by_key['backup_restore_drill']['suggested_command'][1], 'scripts/platform_backup_restore_runner.py')
        self.assertEqual(by_key['identity_policy']['suggested_command'][1], 'scripts/platform_identity_policy.py')
        self.assertEqual(by_key['rbac_assignment']['suggested_command'][1], 'scripts/platform_rbac_assignment_collect.py')
        self.assertEqual(by_key['service_deployment']['suggested_command'][1], 'scripts/provider_service_deployment_evidence.py')
        self.assertIn('artifacts/cutover/evidence/provider_service_deployment.json', by_key['service_deployment']['suggested_command'])
        self.assertEqual(by_key['idp_deployment']['suggested_command'][1], 'scripts/platform_idp_deployment_collect.py')
        self.assertIn('artifacts/cutover/evidence/idp_deployment.json', by_key['idp_deployment']['suggested_command'])
        self.assertEqual(by_key['frontend_deployment']['suggested_command'][1], 'scripts/platform_frontend_deployment_collect.py')
        self.assertIn('artifacts/cutover/evidence/frontend_deployment.json', by_key['frontend_deployment']['suggested_command'])
        self.assertEqual(by_key['frontend_browser_qa']['suggested_command'][1], 'scripts/platform_frontend_browser_qa.py')
        self.assertIn('artifacts/cutover/evidence/frontend_browser_qa.json', by_key['frontend_browser_qa']['suggested_command'])

    def test_full_template_reports_complete_required_evidence_coverage(self):
        config = build_workflow_template(
            evidence_root='artifacts/cutover/evidence',
            provider_id='fcc-unlicensed-conducted',
            cutover_candidate_id='cutover-test',
            evaluated_at='2026-05-15T13:30:00+09:00',
            collected_at='2026-05-15T13:30:00+09:00',
            central_db_schema='docs/platform/central_db_schema.v1.json',
            extraction_manifest='docs/api/headless_contract_extraction_manifest.v1.json',
        )

        summary = run_workflow(config, execute=False)
        coverage = summary['required_evidence_coverage']

        self.assertTrue(coverage['complete'])
        self.assertEqual(coverage['missing_required_evidence_steps'], [])
        self.assertEqual(coverage['required_count'], len(EVIDENCE_FILENAMES))
        self.assertEqual(coverage['covered_count'], len(EVIDENCE_FILENAMES))
        self.assertEqual(coverage['diagnostics'], [])

    def test_plan_reports_missing_required_evidence_steps(self):
        config = build_workflow_template(
            evidence_root='artifacts/cutover/evidence',
            provider_id='fcc-unlicensed-conducted',
            cutover_candidate_id='cutover-test',
            evaluated_at='2026-05-15T13:30:00+09:00',
            collected_at='2026-05-15T13:30:00+09:00',
            central_db_schema='docs/platform/central_db_schema.v1.json',
            extraction_manifest='docs/api/headless_contract_extraction_manifest.v1.json',
        )
        partial = dict(config)
        partial['steps'] = [
            step for step in config['steps']
            if step.get('evidence_key') not in {'performance_smoke', 'frontend_browser_qa'}
        ]

        summary = run_workflow(partial, execute=False)
        coverage = summary['required_evidence_coverage']

        # A partial workflow stays structurally valid (per-step), but cutover
        # completeness must be reported as incomplete.
        self.assertTrue(summary['config_valid'])
        self.assertFalse(coverage['complete'])
        self.assertEqual(
            set(coverage['missing_required_evidence_steps']),
            {'performance_smoke', 'frontend_browser_qa'},
        )
        diagnostic_codes = {d['code'] for d in coverage['diagnostics']}
        self.assertEqual(diagnostic_codes, {'missing_required_evidence_step'})
        self.assertEqual(
            {d['evidence_key'] for d in coverage['diagnostics']},
            {'performance_smoke', 'frontend_browser_qa'},
        )

    def test_cli_template_writes_config_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'workflow.example.json'
            completed = subprocess.run(
                [
                    sys.executable,
                    'scripts/platform_cutover_live_workflow.py',
                    'template',
                    '--cutover-candidate-id', 'cutover-test',
                    '--output', str(output),
                ],
                cwd=str(project_root),
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
            config = json.loads(output.read_text(encoding='utf-8'))

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertEqual(validate_config(config), [])
        self.assertEqual(len(config['steps']), len(EVIDENCE_FILENAMES))

    def test_render_promotes_suggested_command_and_replaces_values(self):
        config = _config(Path('artifacts/cutover/evidence'))
        config['steps'][0]['suggested_command'] = [
            'python',
            'scripts/platform_ingestion_execution_evidence.py',
            'execute',
            '--dsn',
            '<postgres-dsn>',
            '--output',
            'artifacts/cutover/evidence/ingestion_execution.json',
        ]

        rendered, issues = render_workflow_config(config, values={'postgres-dsn': 'postgresql://platform'})

        self.assertEqual(issues, [])
        self.assertEqual(rendered['steps'][0]['command'][1], 'scripts/platform_ingestion_execution_evidence.py')
        self.assertEqual(rendered['steps'][0]['command'][4], 'postgresql://platform')

    def test_render_reports_unresolved_values(self):
        config = _config(Path('artifacts/cutover/evidence'))
        config['steps'][0]['suggested_command'] = [
            'python',
            'scripts/platform_ingestion_execution_evidence.py',
            'execute',
            '--dsn',
            '<postgres-dsn>',
        ]

        _rendered, issues = render_workflow_config(config, values={})

        self.assertIn('unresolved_placeholder', {issue['code'] for issue in issues})

    def test_render_reports_missing_output_argument(self):
        config = _config(Path('artifacts/cutover/evidence'))
        config['steps'][0]['suggested_command'] = [
            'python',
            'scripts/platform_ingestion_execution_evidence.py',
            'execute',
            '--dsn',
            '<postgres-dsn>',
        ]

        _rendered, issues = render_workflow_config(config, values={'postgres-dsn': 'postgresql://platform'})

        self.assertIn('missing_output_argument', {issue['code'] for issue in issues})

    def test_render_accepts_equals_style_and_absolute_output_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _config(root / 'evidence')
            absolute_output = root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']
            config['steps'][0]['suggested_command'] = [
                'python',
                'scripts/platform_ingestion_execution_evidence.py',
                'execute',
                f'--output={absolute_output}',
            ]

            _rendered, issues = render_workflow_config(config, values={})

        self.assertEqual(issues, [])

    def test_values_template_extracts_placeholder_tokens(self):
        config = build_workflow_template(
            evidence_root='artifacts/cutover/evidence',
            provider_id='fcc-unlicensed-conducted',
            cutover_candidate_id='cutover-test',
            evaluated_at='<ISO-8601 timestamp>',
            collected_at='<ISO-8601 timestamp>',
            central_db_schema='docs/platform/central_db_schema.v1.json',
            extraction_manifest='docs/api/headless_contract_extraction_manifest.v1.json',
        )

        values = build_values_template(config)

        self.assertEqual(values['postgres-dsn'], '<postgres-dsn>')
        self.assertEqual(values['lab-headless-db-path'], '<lab-headless-db-path>')
        self.assertEqual(values['lab-host-id'], '<lab-host-id>')
        self.assertEqual(values['deployed-frontend-app-url'], '<deployed-frontend-app-url>')
        self.assertEqual(values['idp-issuer-url'], '<idp-issuer-url>')
        self.assertIn('ISO-8601 timestamp', values)

    def test_cli_render_writes_runnable_config_when_values_are_complete(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _config(root / 'evidence')
            config['steps'][0]['suggested_command'] = [
                sys.executable,
                '-c',
                'print("<message>")',
                str(root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']),
            ]
            config_path = root / 'workflow.json'
            values_path = root / 'values.json'
            output_path = root / 'rendered.json'
            config_path.write_text(json.dumps(config), encoding='utf-8')
            values_path.write_text(json.dumps({'message': 'collect'}), encoding='utf-8')

            completed = subprocess.run(
                [
                    sys.executable,
                    'scripts/platform_cutover_live_workflow.py',
                    'render',
                    '--config', str(config_path),
                    '--values', str(values_path),
                    '--output', str(output_path),
                ],
                cwd=str(project_root),
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
            rendered = json.loads(output_path.read_text(encoding='utf-8'))

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertEqual(rendered['steps'][0]['command'][2], 'print("collect")')
        self.assertEqual(
            rendered['steps'][0]['command'][3],
            str(root / 'evidence' / EVIDENCE_FILENAMES['ingestion_execution']),
        )

    def test_cli_values_template_writes_required_values_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / 'workflow.json'
            output_path = root / 'values.json'
            config = build_workflow_template(
                evidence_root='artifacts/cutover/evidence',
                provider_id='fcc-unlicensed-conducted',
                cutover_candidate_id='cutover-test',
                evaluated_at='<ISO-8601 timestamp>',
                collected_at='<ISO-8601 timestamp>',
                central_db_schema='docs/platform/central_db_schema.v1.json',
                extraction_manifest='docs/api/headless_contract_extraction_manifest.v1.json',
            )
            config_path.write_text(json.dumps(config), encoding='utf-8')

            completed = subprocess.run(
                [
                    sys.executable,
                    'scripts/platform_cutover_live_workflow.py',
                    'values-template',
                    '--config', str(config_path),
                    '--output', str(output_path),
                ],
                cwd=str(project_root),
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
            values = json.loads(output_path.read_text(encoding='utf-8'))

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIn('postgres-dsn', values)
        self.assertIn('deployed-platform-base-url', values)


def _config(evidence_root: Path) -> dict:
    return {
        'schema_version': 2,
        'evidence_root': str(evidence_root),
        'provider_id': 'fcc-unlicensed-conducted',
        'cutover_candidate_id': 'cutover-test',
        'evaluated_at': '2026-05-15T13:00:00+09:00',
        'collected_at': '2026-05-15T13:00:00+09:00',
        'central_db_schema': str(project_root / 'docs' / 'platform' / 'central_db_schema.v1.json'),
        'extraction_manifest': str(resolve_dependency_artifact('docs/api/headless_contract_extraction_manifest.v1.json')),
        'bundle_output': str(evidence_root.parent / 'final_cutover_bundle.json'),
        'steps': [{
            'id': 'live-ingestion',
            'evidence_key': 'ingestion_execution',
            'output': EVIDENCE_FILENAMES['ingestion_execution'],
            'command': [
                sys.executable,
                '-c',
                'print("collect")',
                '--output',
                str(evidence_root / EVIDENCE_FILENAMES['ingestion_execution']),
            ],
            'timeout_seconds': 5,
            'retries': 1,
            'reuse_verified_output': False,
        }],
    }


def _valid_ingestion_manifest() -> dict:
    return {
        'schema_version': 1,
        'evidence_id': 'ingest-exec-1',
        'collected_at': '2026-05-15T19:00:00+09:00',
        'provider_id': 'fcc-unlicensed-conducted',
        'session_id': 'session-uuid',
        'database_name': 'fcc_platform',
        'writer_adapter': 'postgres',
        'plan_sha256': 'a' * 64,
        'executed': True,
        'transaction_committed': True,
        'transaction_rolled_back': False,
        'attempts': 1,
        'attempted_steps': 2,
        'applied_steps': 2,
        'errors': [],
        'retry_errors': [],
        'steps': [
            {
                'order': 1,
                'target_table': 'measurement_results',
                'operation': 'upsert',
                'idempotency_key': ['provider-uuid', 'result-1'],
                'affected_rows': 1,
            },
            {
                'order': 2,
                'target_table': 'artifacts',
                'operation': 'upsert',
                'idempotency_key': ['provider-uuid', 'plot.png'],
                'affected_rows': 1,
            },
        ],
    }


def _write_valid_ingestion_manifest(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_valid_ingestion_manifest()), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
