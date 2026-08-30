import ast
import json
import unittest
from pathlib import Path

from fcc_test_platform.backup_restore_drill import (
    backup_restore_drill_errors,
    is_backup_restore_drill_valid,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_restore_drill.py')
SCHEMA_PATH = project_root / 'docs' / 'platform' / 'backup_restore_drill.schema.v1.json'


def _valid_manifest() -> dict:
    return {
        'schema_version': 1,
        'restore_point_id': 'rp-2026-05-14T10:00:00Z',
        'db_backup': {
            'backup_point_id': 'rp-2026-05-14T10:00:00Z',
            'database_name': 'fcc_platform',
            'created_at': '2026-05-14T10:00:00Z',
            'sha256': 'a' * 64,
        },
        'artifact_backup': {
            'backup_point_id': 'rp-2026-05-14T10:00:00Z',
            'relative_path': 'backups/rp-2026-05-14/artifacts.tar.zst',
            'created_at': '2026-05-14T10:00:00Z',
            'sha256': 'b' * 64,
        },
        'artifacts': [
            {
                'relative_path': 'projects/p/sessions/s/results/r/plot_png/plot.png',
                'sha256': 'c' * 64,
                'storage_backend': 'filesystem',
                'restored': True,
                'exists_after_restore': True,
            }
        ],
        'report_outputs': [
            {
                'relative_path': 'projects/p/sessions/s/reports/r/unii.pdf',
                'sha256': 'd' * 64,
                'storage_backend': 'filesystem',
                'restored': True,
                'exists_after_restore': True,
            }
        ],
    }


class TestPlatformBackupRestoreDrill(unittest.TestCase):
    def test_valid_restore_manifest_passes(self):
        self.assertTrue(is_backup_restore_drill_valid(_valid_manifest()))
        self.assertEqual(backup_restore_drill_errors(_valid_manifest()), [])

    def test_rejects_mismatched_backup_points(self):
        manifest = _valid_manifest()
        manifest['artifact_backup']['backup_point_id'] = 'different'

        issues = backup_restore_drill_errors(manifest)

        self.assertIn('backup_point_mismatch', {issue.code for issue in issues})
        self.assertIn('artifact_backup.backup_point_id', {issue.path for issue in issues})

    def test_rejects_unsafe_paths_and_missing_hashes(self):
        manifest = _valid_manifest()
        manifest['artifact_backup']['relative_path'] = '../backup.tar'
        manifest['artifacts'][0]['relative_path'] = 'C:/server/share/plot.png'
        manifest['artifacts'][0]['sha256'] = 'g' * 64

        issues = backup_restore_drill_errors(manifest)
        issue_codes = {issue.code for issue in issues}

        self.assertIn('invalid_relative_path', issue_codes)
        self.assertIn('invalid_sha256', issue_codes)

    def test_rejects_records_that_are_not_restored(self):
        manifest = _valid_manifest()
        manifest['report_outputs'][0]['restored'] = False
        manifest['report_outputs'][0]['exists_after_restore'] = False

        issues = backup_restore_drill_errors(manifest)

        self.assertIn('not_restored', {issue.code for issue in issues})
        self.assertIn('missing_after_restore', {issue.code for issue in issues})

    def test_schema_document_declares_same_point_in_time_rule(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

        self.assertEqual(schema['owner_repository'], 'fcc-test-platform')
        self.assertIn('restore_point_id', schema['required_top_level'])
        self.assertIn('backup_point_id must match restore_point_id', schema['same_point_in_time_rule'])
        self.assertIn('relative_path', schema['restored_record_required_fields'])
        self.assertIn('sha256 values must be 64-character lowercase hex digests', schema['hash_rules'])

    def test_module_import_boundary_excludes_runtime_dependencies(self):
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
        )
        self.assertFalse([
            module for module in imports
            if module == 'infrastructure' or module.startswith(forbidden_prefixes)
        ])


if __name__ == '__main__':
    unittest.main()
