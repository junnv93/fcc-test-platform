import ast
from datetime import datetime, timezone
import json
import unittest
from pathlib import Path

from fcc_test_contracts.common.access_policy import API_PERMISSION_ADMIN
from fcc_test_platform.artifact_storage import RetentionCandidate
from fcc_test_platform.download_policy import (
    DOWNLOAD_PERMISSION_BY_RECORD_TYPE,
    DownloadGrantPolicy,
    build_download_grant,
    build_retention_execution_plan,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_download_policy.py')
POLICY_PATH = project_root / 'docs' / 'platform' / 'download_retention_policy.v1.json'


def _artifact_record() -> dict:
    return {
        'id': 'artifact-1',
        'relative_path': 'projects/p/sessions/s/results/r/plot_png/plot.png',
        'storage_backend': 'filesystem',
        'exists': True,
        'sha256': 'a' * 64,
        'byte_size': 12345,
        'created_at': '2026-05-15T00:00:00Z',
    }


class TestPlatformDownloadRetentionPolicy(unittest.TestCase):
    def test_builds_artifact_download_grant_with_bounded_ttl(self):
        grant = build_download_grant(
            record_type='artifact',
            record=_artifact_record(),
            principal_permissions=['headless:read'],
            now=datetime(2026, 5, 15, 0, 0, tzinfo=timezone.utc),
            policy=DownloadGrantPolicy(max_ttl_seconds=600, default_ttl_seconds=120),
            disposition='inline',
        )

        self.assertEqual(grant.permission, 'headless:read')
        self.assertEqual(grant.disposition, 'inline')
        self.assertEqual(grant.expires_at.isoformat(), '2026-05-15T00:02:00+00:00')
        payload = grant.to_dict()
        self.assertEqual(payload['relative_path'], _artifact_record()['relative_path'])
        self.assertNotIn('signed_url', payload)

    def test_builds_report_output_grant_with_report_permission(self):
        record = _artifact_record()
        record['relative_path'] = 'projects/p/sessions/s/reports/r/unii.pdf'

        grant = build_download_grant(
            record_type='report_output',
            record=record,
            principal_permissions=['report_automation:read'],
            now=datetime(2026, 5, 15, 0, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(grant.permission, 'report_automation:read')

    def test_admin_permission_can_issue_any_download_grant(self):
        grant = build_download_grant(
            record_type='artifact',
            record=_artifact_record(),
            principal_permissions=[API_PERMISSION_ADMIN],
            now=datetime(2026, 5, 15, 0, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(grant.permission, 'headless:read')

    def test_rejects_missing_permission_and_missing_files(self):
        with self.assertRaises(PermissionError):
            build_download_grant(
                record_type='artifact',
                record=_artifact_record(),
                principal_permissions=['report_automation:read'],
                now=datetime(2026, 5, 15, 0, 0, tzinfo=timezone.utc),
            )

        record = _artifact_record()
        record['exists'] = False
        with self.assertRaisesRegex(ValueError, 'must exist'):
            build_download_grant(
                record_type='artifact',
                record=record,
                principal_permissions=['headless:read'],
                now=datetime(2026, 5, 15, 0, 0, tzinfo=timezone.utc),
            )

    def test_rejects_unsafe_path_and_invalid_ttl(self):
        record = _artifact_record()
        record['relative_path'] = 'C:/server/share/plot.png'
        with self.assertRaisesRegex(ValueError, 'drive prefix'):
            build_download_grant(
                record_type='artifact',
                record=record,
                principal_permissions=['headless:read'],
                now=datetime(2026, 5, 15, 0, 0, tzinfo=timezone.utc),
            )

        with self.assertRaisesRegex(ValueError, '<= max_ttl_seconds'):
            build_download_grant(
                record_type='artifact',
                record=_artifact_record(),
                principal_permissions=['headless:read'],
                now=datetime(2026, 5, 15, 0, 0, tzinfo=timezone.utc),
                policy=DownloadGrantPolicy(max_ttl_seconds=10, default_ttl_seconds=10),
                ttl_seconds=11,
            )

    def test_builds_retention_execution_plan_without_deleting_files(self):
        candidate = RetentionCandidate(
            record_type='artifact',
            relative_path='projects/p/sessions/s/results/r/plot_png/old.png',
            storage_backend='filesystem',
            reason='artifact age 90d >= retention 30d',
            age_days=90,
            record={'id': 'artifact-1'},
        )

        plan = build_retention_execution_plan(
            candidates=[candidate],
            approved_by='qa.manager@example.com',
            approved_at=datetime(2026, 5, 15, 1, 0, tzinfo=timezone.utc),
            reason='monthly retention review',
            dry_run=True,
        )

        payload = plan.to_dict()
        self.assertTrue(payload['dry_run'])
        self.assertEqual(payload['approved_by'], 'qa.manager@example.com')
        self.assertEqual(payload['candidates'][0]['relative_path'], candidate.relative_path)
        self.assertNotIn('delete', payload)
        self.assertNotIn('archive_path', payload['candidates'][0])

    def test_retention_execution_requires_approval_metadata(self):
        with self.assertRaisesRegex(ValueError, 'approved_by'):
            build_retention_execution_plan(
                candidates=[{'record_type': 'artifact', 'relative_path': 'a/b.png', 'storage_backend': 'filesystem', 'reason': 'old'}],
                approved_by='',
                approved_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
                reason='monthly review',
            )

        with self.assertRaisesRegex(ValueError, 'at least one'):
            build_retention_execution_plan(
                candidates=[],
                approved_by='qa.manager@example.com',
                approved_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
                reason='monthly review',
            )

    def test_policy_document_matches_permission_ssot(self):
        policy = json.loads(POLICY_PATH.read_text(encoding='utf-8'))

        self.assertEqual(policy['owner_repository'], 'fcc-test-platform')
        self.assertEqual(policy['required_permissions']['artifact'], DOWNLOAD_PERMISSION_BY_RECORD_TYPE['artifact'])
        self.assertEqual(
            policy['required_permissions']['report_output'],
            DOWNLOAD_PERMISSION_BY_RECORD_TYPE['report_output'],
        )
        self.assertEqual(policy['required_permissions']['super_permission'], API_PERMISSION_ADMIN)
        self.assertIn('url_signing', policy['forbidden_operations'])

    def test_module_import_boundary_excludes_io_and_provider_runtime_dependencies(self):
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
            'subprocess',
            'shutil',
            'os',
            'pathlib',
        )
        self.assertFalse([
            module for module in imports
            if module == 'infrastructure' or module.startswith(forbidden_prefixes)
        ])


if __name__ == '__main__':
    unittest.main()
