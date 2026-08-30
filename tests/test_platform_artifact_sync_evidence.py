import ast
import json
import unittest
from pathlib import Path

from fcc_test_platform.artifact_sync_evidence import (
    artifact_sync_evidence_errors,
    is_artifact_sync_evidence_valid,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_artifact_sync_evidence.py')
SCHEMA_PATH = project_root / 'docs' / 'platform' / 'artifact_sync_evidence.schema.v1.json'


def _valid_manifest() -> dict:
    return {
        'schema_version': 1,
        'provider_id': 'fcc-unlicensed-conducted',
        'session_id': 'session-uuid',
        'sync_job_id': 'sync-job-1',
        'source_root_id': 'lab-pc-01-artifacts',
        'destination_root_id': 'platform-file-server',
        'synced_at': '2026-05-15T08:00:00+09:00',
        'artifact_count': 1,
        'report_output_count': 1,
        'files': [
            {
                'record_type': 'artifact',
                'relative_path': 'projects/p/sessions/s/results/r/plot_png/plot.png',
                'storage_backend': 'filesystem',
                'sha256': 'a' * 64,
                'byte_size': 123,
                'sync_status': 'synced',
                'source_exists': True,
                'destination_exists': True,
            },
            {
                'record_type': 'report_output',
                'relative_path': 'projects/p/sessions/s/reports/r/unii.pdf',
                'storage_backend': 'filesystem',
                'sha256': 'b' * 64,
                'byte_size': 456,
                'sync_status': 'synced',
                'source_exists': True,
                'destination_exists': True,
            },
        ],
    }


class TestPlatformArtifactSyncEvidence(unittest.TestCase):
    def test_valid_sync_evidence_passes(self):
        manifest = _valid_manifest()

        self.assertTrue(is_artifact_sync_evidence_valid(manifest))
        self.assertEqual(artifact_sync_evidence_errors(manifest), [])

    def test_rejects_unsafe_path_and_bad_hash(self):
        manifest = _valid_manifest()
        manifest['files'][0]['relative_path'] = 'C:/server/share/plot.png'
        manifest['files'][0]['sha256'] = 'g' * 64

        issues = artifact_sync_evidence_errors(manifest)
        codes = {issue.code for issue in issues}

        self.assertIn('invalid_relative_path', codes)
        self.assertIn('invalid_sha256', codes)

    def test_rejects_unsynced_or_missing_files(self):
        manifest = _valid_manifest()
        manifest['files'][0]['sync_status'] = 'pending'
        manifest['files'][0]['source_exists'] = False
        manifest['files'][1]['destination_exists'] = False

        issues = artifact_sync_evidence_errors(manifest)
        codes = {issue.code for issue in issues}

        self.assertIn('file_not_synced', codes)
        self.assertIn('source_missing', codes)
        self.assertIn('destination_missing', codes)

    def test_rejects_duplicate_records_and_count_mismatch(self):
        manifest = _valid_manifest()
        manifest['files'].append(dict(manifest['files'][0]))
        manifest['artifact_count'] = 1

        issues = artifact_sync_evidence_errors(manifest)
        codes = {issue.code for issue in issues}

        self.assertIn('duplicate_relative_path', codes)
        self.assertIn('declared_count_mismatch', codes)

    def test_rejects_invalid_record_type_backend_and_byte_size(self):
        manifest = _valid_manifest()
        manifest['files'][0]['record_type'] = 'plot_blob'
        manifest['files'][0]['storage_backend'] = 'local_disk'
        manifest['files'][0]['byte_size'] = -1

        issues = artifact_sync_evidence_errors(manifest)
        codes = {issue.code for issue in issues}

        self.assertIn('invalid_record_type', codes)
        self.assertIn('invalid_storage_backend', codes)
        self.assertIn('invalid_byte_size', codes)

    def test_schema_document_declares_contract(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

        self.assertEqual(schema['owner_repository'], 'fcc-test-platform')
        self.assertIn('files', schema['required_top_level'])
        self.assertIn('sync_status must be synced for production-ready evidence', schema['status_rules'])
        self.assertIn('sha256 must be a 64-character lowercase hex digest', schema['status_rules'])
        self.assertIn('file_copy', schema['forbidden_operations'])

    def test_module_import_boundary_excludes_file_and_runtime_dependencies(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden_prefixes = (
            'application.reporting',
            'reporting',
            'infrastructure',
            'fastapi',
            'sqlalchemy',
            'pandas',
            'openpyxl',
            'sqlite3',
            'shutil',
            'os',
            'pathlib',
            'hashlib',
            'requests',
            'httpx',
        )
        self.assertFalse([
            module for module in imports
            if module == 'infrastructure' or module.startswith(forbidden_prefixes)
        ])


if __name__ == '__main__':
    unittest.main()
