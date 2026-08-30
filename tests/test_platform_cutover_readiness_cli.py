import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fcc_test_platform.cutover_readiness import CUTOVER_REQUIRED_EVIDENCE


project_root = Path(__file__).parent.parent


def _generic_ref(key: str) -> dict:
    return {
        'evidence_id': f'{key}-evidence',
        'collected_at': '2026-05-15T10:05:00+09:00',
        'validated': True,
        'issues': [],
    }


def _bundle() -> dict:
    return {
        'schema_version': 1,
        'provider_id': 'fcc-unlicensed-conducted',
        'cutover_candidate_id': 'cutover-2026-05-15',
        'evaluated_at': '2026-05-15T10:05:00+09:00',
        'evidence': {key: _generic_ref(key) for key in CUTOVER_REQUIRED_EVIDENCE},
    }


class TestPlatformCutoverReadinessCli(unittest.TestCase):
    def test_generic_bundle_without_nested_manifests_returns_one(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_path = Path(tmpdir) / 'bundle.json'
            bundle_path.write_text(json.dumps(_bundle()), encoding='utf-8')

            completed = _run_cli(bundle_path)

        self.assertEqual(completed.returncode, 1, completed.stderr + completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload['ready'])
        self.assertEqual({issue['code'] for issue in payload['issues']}, {'missing_manifest'})

    def test_invalid_bundle_returns_one_and_evidence_issue(self):
        bundle = _bundle()
        del bundle['evidence']['hardware_smoke']
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_path = Path(tmpdir) / 'bundle.json'
            bundle_path.write_text(json.dumps(bundle), encoding='utf-8')

            completed = _run_cli(bundle_path)

        self.assertEqual(completed.returncode, 1)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload['ready'])
        matching = [
            issue for issue in payload['issues']
            if issue['code'] == 'missing_required_evidence'
        ]
        self.assertEqual(matching[0]['evidence_key'], 'hardware_smoke')

    def test_read_error_returns_two(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_path = Path(tmpdir) / 'bundle.json'
            bundle_path.write_text('{not-json', encoding='utf-8')

            completed = _run_cli(bundle_path)

        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload['ready'])
        self.assertEqual(payload['issues'][0]['code'], 'read_error')

    def test_central_db_schema_option_is_passed_to_validator(self):
        bundle = _bundle()
        bundle['evidence']['db_migration']['manifest'] = {
            'schema_version': 1,
            'migration_id': '001_initial_central_db',
            'database_engine': 'postgresql',
            'database_name': 'fcc_platform',
            'migration_status': 'applied',
            'applied_at': '2026-05-15T10:00:00+09:00',
            'applied_by': 'platform-ci',
            'schema_contract_sha256': 'a' * 64,
            'ddl_sha256': 'b' * 64,
            'tables': {},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_path = Path(tmpdir) / 'bundle.json'
            schema_path = project_root / 'docs' / 'platform' / 'central_db_schema.v1.json'
            bundle_path.write_text(json.dumps(bundle), encoding='utf-8')

            without_schema = _run_cli(bundle_path)
            with_schema = _run_cli(bundle_path, '--central-db-schema', str(schema_path))

        self.assertIn('missing_central_db_schema', {issue['code'] for issue in json.loads(without_schema.stdout)['issues']})
        self.assertIn('missing_evidence_tables', {issue['code'] for issue in json.loads(with_schema.stdout)['issues']})

    def test_extraction_manifest_option_is_passed_to_validator(self):
        bundle = _bundle()
        bundle['evidence']['extraction_package']['manifest'] = {
            'schema_version': 1,
            'evidence_id': 'extract-1',
            'collected_at': '2026-05-15T10:00:00+09:00',
            'manifest_path': 'docs/api/headless_contract_extraction_manifest.v1.json',
            'compatible': True,
            'issues': [],
            'repositories': {
                'fcc-test-contracts': _extracted_repo('fcc-test-contracts'),
                'fcc-test-platform': _extracted_repo('fcc-test-platform'),
            },
        }
        extraction_manifest = {
            'repositories': {
                'fcc-test-contracts': {
                    'entries': [{
                        'current_path': 'src/application/headless/api_contracts.py',
                        'future_path': 'fcc_test_contracts/headless/api_contracts.py',
                        'kind': 'python_module',
                    }],
                },
                'fcc-test-platform': {
                    'entries': [{
                        'current_path': 'docs/platform/cutover_readiness_evidence.schema.v1.json',
                        'future_path': 'docs/cutover_readiness_evidence.schema.v1.json',
                        'kind': 'platform_evidence_schema',
                    }],
                },
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle_path = Path(tmpdir) / 'bundle.json'
            extraction_path = Path(tmpdir) / 'extraction-manifest.json'
            bundle_path.write_text(json.dumps(bundle), encoding='utf-8')
            extraction_path.write_text(json.dumps(extraction_manifest), encoding='utf-8')

            without_manifest = _run_cli(bundle_path)
            with_manifest = _run_cli(bundle_path, '--extraction-manifest', str(extraction_path))

        self.assertNotIn('missing_manifest_entry', {issue['code'] for issue in json.loads(without_manifest.stdout)['issues']})
        self.assertIn('missing_manifest_entry', {issue['code'] for issue in json.loads(with_manifest.stdout)['issues']})

    def test_template_command_outputs_all_required_evidence_keys(self):
        completed = subprocess.run(
            [
                sys.executable,
                'scripts/platform_cutover_readiness.py',
                'template',
                '--provider-id',
                'fcc-unlicensed-conducted',
                '--cutover-candidate-id',
                'cutover-test',
                '--evaluated-at',
                '2026-05-15T10:10:00+09:00',
            ],
            cwd=str(project_root),
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['provider_id'], 'fcc-unlicensed-conducted')
        self.assertEqual(payload['cutover_candidate_id'], 'cutover-test')
        self.assertEqual(set(payload['evidence']), set(CUTOVER_REQUIRED_EVIDENCE))
        self.assertFalse(payload['evidence']['hardware_smoke']['validated'])
        self.assertEqual(payload['evidence']['hardware_smoke']['manifest'], {})

    def test_cli_does_not_import_runtime_side_effect_dependencies(self):
        text = (project_root / 'scripts' / 'platform_cutover_readiness.py').read_text(encoding='utf-8')

        self.assertIn('platform_cutover_readiness', text)
        self.assertNotIn('FastAPI', text)
        self.assertNotIn('uvicorn', text)
        self.assertNotIn('sqlite3', text)
        self.assertNotIn('subprocess', text)


def _run_cli(bundle_path: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            'scripts/platform_cutover_readiness.py',
            'validate',
            str(bundle_path),
            *extra,
        ],
        cwd=str(project_root),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def _extracted_repo(repo_name: str) -> dict:
    return {
        'target_repository': repo_name,
        'target_ref': 'commit',
        'extracted_at': '2026-05-15T10:00:00+09:00',
        'package_compatible': True,
        'extracted': True,
        'entries': [{
            'current_path': 'other.py',
            'future_path': 'other.py',
            'kind': 'python_module',
            'byte_size': 1,
            'source_sha256': 'a' * 64,
            'destination_sha256': 'a' * 64,
            'copied': True,
        }],
    }


if __name__ == '__main__':
    unittest.main()
