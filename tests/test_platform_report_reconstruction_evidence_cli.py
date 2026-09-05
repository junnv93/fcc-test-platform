import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import fcc_test_platform.report_reconstruction_evidence_cli as cli


project_root = Path(__file__).parent.parent


def _valid_manifest() -> dict:
    return {
        'schema_version': 1,
        'provider_id': 'fcc-unlicensed-conducted',
        'session_id': 'session-uuid',
        'report_run_id': 'report-run-uuid',
        'source_mode': 'db_primary',
        'excel_source_used': False,
        'excel_export_only': True,
        'source_snapshot': {
            'central_db_migration_evidence_id': 'migration-evidence-1',
            'ingestion_batch_id': 'ingestion-batch-1',
            'snapshot_sha256': 'c' * 64,
            'measurement_result_count': 12,
        },
        'generated_outputs': [{
            'output_type': 'docx',
            'relative_path': 'projects/p/sessions/s/reports/r/unii.docx',
            'storage_backend': 'filesystem',
            'sha256': 'a' * 64,
        }],
        'artifact_resolution': {
            'required_count': 1,
            'resolved_count': 1,
            'missing': [],
        },
        'acceptance_audit': {
            'status': 'pass',
            'reviewed_by': 'qa@example.com',
            'reviewed_at': '2026-05-15T11:15:00+09:00',
        },
    }


class TestPlatformReportReconstructionEvidenceCli(unittest.TestCase):
    def test_valid_manifest_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'manifest.json'
            path.write_text(json.dumps(_valid_manifest()), encoding='utf-8')

            completed = _run_cli('validate', str(path))

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertEqual(json.loads(completed.stdout), {'valid': True, 'issues': []})

    def test_invalid_manifest_returns_one(self):
        manifest = _valid_manifest()
        manifest['excel_source_used'] = True
        manifest['generated_outputs'][0]['sha256'] = 'short'
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'manifest.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')

            completed = _run_cli('validate', str(path))

        self.assertEqual(completed.returncode, 1)
        codes = {issue['code'] for issue in json.loads(completed.stdout)['issues']}
        self.assertIn('excel_source_used', codes)
        self.assertIn('invalid_sha256', codes)

    def test_read_error_returns_two(self):
        completed = _run_cli('validate', 'missing.json')

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)['issues'][0]['code'], 'read_error')

    def test_template_outputs_db_primary_but_not_valid_proof(self):
        completed = _run_cli('template', '--session-id', 'session-1', '--report-run-id', 'report-1')

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['source_mode'], 'db_primary')
        self.assertFalse(payload['excel_source_used'])
        self.assertEqual(payload['source_snapshot']['measurement_result_count'], 0)
        self.assertEqual(payload['source_snapshot']['snapshot_sha256'], '<sha256>')
        self.assertEqual(payload['generated_outputs'][0]['sha256'], '<sha256>')

    def test_platform_cli_has_no_provider_owned_execute_command(self):
        completed = _run_cli('execute')

        self.assertEqual(completed.returncode, 2)

    def test_cli_has_no_report_runtime_imports(self):
        path = project_root / 'scripts' / 'platform_report_reconstruction_evidence.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        imports = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden = ('openpyxl', 'pandas', 'docx', 'sqlite3', 'sqlalchemy', 'fastapi', 'subprocess', 'reporting')
        self.assertFalse([module for module in imports if module.startswith(forbidden)])


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, 'scripts/platform_report_reconstruction_evidence.py', *args],
        cwd=str(project_root),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


if __name__ == '__main__':
    unittest.main()
