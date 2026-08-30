import copy
import inspect
import json
import unittest
from pathlib import Path
import tempfile

from fcc_test_platform.application import platform_cutover_run_receipt as receipt_module
from fcc_test_platform.application.platform_cutover_run_receipt import (
    FileObservation,
    ReceiptBinding,
    atomic_write_receipt,
    build_receipt,
    collector_identity,
    observe_file,
    receipt_mismatches,
    receipt_path_for,
    safe_workflow_fingerprint,
)
from fcc_test_platform.evidence_primitives import sha256_bytes


class TestPlatformCutoverRunReceipt(unittest.TestCase):
    def test_receipt_digest_delegates_to_extraction_owned_primitive(self):
        # Inspect the module pytest actually imported.  A delivered lane moves
        # this module under its package root, so checkout-depth arithmetic would
        # inspect a path that is not present even though the module is importable.
        source = inspect.getsource(receipt_module)

        self.assertNotIn('hashlib', source)
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'ingestion_execution.json'
            payload = b'byte-exact receipt content'
            output.write_bytes(payload)
            self.assertEqual(observe_file(output).sha256, sha256_bytes(payload))

    def test_receipt_binds_candidate_context_catalog_and_output_digest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'ingestion_execution.json'
            output.write_text('{"verified":true}\n', encoding='utf-8')
            observation = observe_file(output)
            binding = _binding()

            receipt = build_receipt(
                binding=binding,
                observation=observation,
                collected_at='2026-08-23T00:00:00Z',
            )

            self.assertEqual(receipt_mismatches(receipt, binding=binding, observation=observation), ())
            self.assertEqual(receipt['output_size_bytes'], observation.size_bytes)
            self.assertEqual(receipt['output_sha256'], observation.sha256)
            self.assertEqual(receipt['collector_identity'], collector_identity('ingestion_execution'))
            self.assertNotIn('password', json.dumps(receipt).lower())

    def test_every_binding_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'ingestion_execution.json'
            output.write_bytes(b'valid-output')
            observation = observe_file(output)
            binding = _binding()
            receipt = build_receipt(binding=binding, observation=observation, collected_at='2026-08-23T00:00:00Z')

            mutations = {
                'provider_id': 'other-provider',
                'cutover_candidate_id': 'other-candidate',
                'workflow_fingerprint': 'other-fingerprint',
                'central_db_schema_sha256': 'b' * 64,
                'extraction_manifest_sha256': 'c' * 64,
                'evidence_key': 'hardware_smoke',
                'canonical_filename': 'renamed.json',
                'collector_identity': 'other-collector',
                'output_size_bytes': observation.size_bytes + 1,
                'output_sha256': 'd' * 64,
                'receipt_schema_version': 99,
            }
            for field, value in mutations.items():
                with self.subTest(field=field):
                    hostile = dict(receipt)
                    hostile[field] = value
                    mismatches = receipt_mismatches(hostile, binding=binding, observation=observation)
                    self.assertTrue(mismatches)
                    self.assertTrue(any(item['code'].startswith('receipt_') for item in mismatches))

            hostile = dict(receipt)
            hostile.pop('collected_at')
            self.assertIn(
                'receipt_collected_at_missing',
                {item['code'] for item in receipt_mismatches(hostile, binding=binding, observation=observation)},
            )

    def test_changed_bytes_same_filename_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'ingestion_execution.json'
            output.write_bytes(b'123456')
            binding = _binding()
            receipt = build_receipt(binding=binding, observation=observe_file(output), collected_at='2026-08-23T00:00:00Z')
            output.write_bytes(b'654321')

            codes = {
                item['code']
                for item in receipt_mismatches(receipt, binding=binding, observation=observe_file(output))
            }
            self.assertIn('receipt_output_sha256_mismatch', codes)

    def test_atomic_write_replaces_complete_receipts_without_temp_debris(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'ingestion_execution.json'
            output.write_bytes(b'output')
            receipt_path = receipt_path_for(output)
            receipt = build_receipt(binding=_binding(), observation=observe_file(output), collected_at='2026-08-23T00:00:00Z')

            atomic_write_receipt(receipt_path, receipt)
            self.assertEqual(json.loads(receipt_path.read_text(encoding='utf-8')), receipt)
            self.assertEqual(list(Path(tmpdir).glob('*.tmp')), [])

            replacement = dict(receipt)
            replacement['collected_at'] = '2026-08-23T00:01:00Z'
            atomic_write_receipt(receipt_path, replacement)
            self.assertEqual(json.loads(receipt_path.read_text(encoding='utf-8')), replacement)

    def test_missing_output_cannot_create_receipt(self):
        with self.assertRaises(ValueError):
            build_receipt(
                binding=_binding(),
                observation=FileObservation(False, None, None, None),
                collected_at='2026-08-23T00:00:00Z',
            )

    def test_secret_changes_do_not_change_safe_fingerprint_or_persist_values(self):
        base = {
            'schema_version': 2,
            'provider_id': 'fcc-unlicensed-conducted',
            'cutover_candidate_id': 'cutover-test',
            'stop_on_failure': True,
            'steps': [{
                'evidence_key': 'ingestion_execution',
                'output': 'ingestion_execution.json',
                'command': [
                    'python',
                    'scripts/platform_ingestion_execution_evidence.py',
                    '--dsn', 'postgresql://user:low-entropy@db.example/live',
                    '--client-secret=first-secret',
                    '--output', 'ingestion_execution.json',
                ],
                'env': {
                    'FCC_HEADLESS_AUTH_TOKEN': 'first-token',
                    'FCC_HEADLESS_PROVIDER_ID': 'fcc-unlicensed-conducted',
                },
            }],
        }
        changed = copy.deepcopy(base)
        changed['steps'][0]['command'][3] = 'postgresql://user:another-low-entropy@db.example/live'
        changed['steps'][0]['command'][4] = '--client-secret=second-secret'
        changed['steps'][0]['env']['FCC_HEADLESS_AUTH_TOKEN'] = 'second-token'

        self.assertEqual(safe_workflow_fingerprint(base), safe_workflow_fingerprint(changed))
        self.assertNotIn('first-secret', safe_workflow_fingerprint(base))
        self.assertNotIn('first-token', safe_workflow_fingerprint(base))
        self.assertNotIn('low-entropy', safe_workflow_fingerprint(base))

    def test_unlabelled_unknown_url_and_environment_values_do_not_influence_fingerprint(self):
        base = {
            'schema_version': 2,
            'provider_id': 'fcc-unlicensed-conducted',
            'cutover_candidate_id': 'cutover-test',
            'steps': [{
                'evidence_key': 'ingestion_execution',
                'output': 'ingestion_execution.json',
                'command': [
                    'python',
                    'scripts/platform_ingestion_execution_evidence.py',
                    '--unknown-option', 'hunter2',
                    'positional-secret',
                    '--backend-base-url', 'https://secret.example/live',
                    '--output', 'ingestion_execution.json',
                ],
                'env': {'UNCLASSIFIED_VALUE': 'env-secret'},
            }],
        }
        changed = copy.deepcopy(base)
        changed['steps'][0]['command'][3] = 'another-password'
        changed['steps'][0]['command'][4] = 'different-positional'
        changed['steps'][0]['command'][6] = 'https://other.example/live'
        changed['steps'][0]['env']['UNCLASSIFIED_VALUE'] = 'other-env-secret'

        self.assertEqual(safe_workflow_fingerprint(base), safe_workflow_fingerprint(changed))
        fingerprint = safe_workflow_fingerprint(base)
        for secret in ('hunter2', 'positional-secret', 'https://secret.example/live', 'env-secret'):
            self.assertNotIn(secret, fingerprint)

    def test_cwd_dictionary_values_do_not_influence_safe_fingerprint(self):
        base = {
            'schema_version': 2,
            'provider_id': 'fcc-unlicensed-conducted',
            'cutover_candidate_id': 'cutover-test',
            'steps': [{
                'evidence_key': 'ingestion_execution',
                'output': 'ingestion_execution.json',
                'command': ['python', 'scripts/platform_ingestion_execution_evidence.py', 'execute'],
            }],
        }
        dictionary = (
            '/run/secrets/token-alpha',
            '/run/secrets/token-beta',
            '/var/lib/fcc/credentials/one',
            'C:\\secrets\\token-gamma',
        )

        fingerprints = set()
        for cwd in dictionary:
            candidate = copy.deepcopy(base)
            candidate['steps'][0]['cwd'] = cwd
            fingerprints.add(safe_workflow_fingerprint(candidate))

        self.assertEqual(len(fingerprints), 1)

    def test_approved_path_option_dictionary_values_do_not_influence_safe_fingerprint(self):
        path_options = (
            '--artifact-root',
            '--backup-root',
            '--db-backup-file',
            '--db-path',
            '--destination-root',
            '--manifest',
            '--output',
            '--output-dir',
            '--plan',
            '--records-json',
            '--report-output',
            '--restore-root',
            '--screenshot-dir',
            '--source-root',
            '--target-root',
        )
        dictionary = (
            '/run/secrets/token-alpha',
            '/run/secrets/token-beta',
            '/var/lib/fcc/credentials/one',
            'C:\\secrets\\token-gamma',
        )
        for option in path_options:
            with self.subTest(option=option):
                fingerprints = set()
                for path_value in dictionary:
                    config = {
                        'schema_version': 2,
                        'provider_id': 'fcc-unlicensed-conducted',
                        'cutover_candidate_id': 'cutover-test',
                        'steps': [{
                            'evidence_key': 'ingestion_execution',
                            'output': 'ingestion_execution.json',
                            'command': [
                                'python',
                                'scripts/platform_ingestion_execution_evidence.py',
                                'execute',
                                option,
                                path_value,
                            ],
                        }],
                    }
                    fingerprints.add(safe_workflow_fingerprint(config))
                self.assertEqual(len(fingerprints), 1)

    def test_missing_or_non_hex_context_digest_cannot_create_a_receipt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'ingestion_execution.json'
            output.write_bytes(b'valid-output')
            observation = observe_file(output)
            for field, value in (
                ('central_db_schema_sha256', ''),
                ('extraction_manifest_sha256', 'not-a-digest'),
            ):
                with self.subTest(field=field):
                    binding = _binding()
                    binding = ReceiptBinding(**{**binding.__dict__, field: value})
                    with self.assertRaises(ValueError):
                        build_receipt(
                            binding=binding,
                            observation=observation,
                            collected_at='2026-08-23T00:00:00Z',
                        )


def _binding() -> ReceiptBinding:
    return ReceiptBinding(
        provider_id='fcc-unlicensed-conducted',
        cutover_candidate_id='cutover-test',
        workflow_fingerprint='workflow-fingerprint',
        central_db_schema_sha256='a' * 64,
        extraction_manifest_sha256='b' * 64,
        evidence_key='ingestion_execution',
        canonical_filename='ingestion_execution.json',
        collector_identity=collector_identity('ingestion_execution'),
    )


if __name__ == '__main__':
    unittest.main()
