import ast
from datetime import datetime, timezone
import json
import unittest
from pathlib import Path

from fcc_test_platform.artifact_storage import (
    RetentionPolicy,
    build_artifact_storage_key,
    build_report_output_storage_key,
    normalize_relative_path,
    plan_retention_candidates,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
POLICY_PATH = project_root / 'docs' / 'platform' / 'artifact_storage_policy.v1.json'
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_artifact_storage.py')


class TestPlatformArtifactStoragePolicy(unittest.TestCase):
    def test_normalize_relative_path_rejects_unsafe_addresses(self):
        self.assertEqual(
            normalize_relative_path('project/session/plot.png'),
            'project/session/plot.png',
        )
        self.assertEqual(
            normalize_relative_path('project\\session\\.\\plot.png'),
            'project/session/plot.png',
        )
        for value in [
            '',
            '/server/share/plot.png',
            '//server/share/plot.png',
            'C:/server/share/plot.png',
            '../plot.png',
            'project/../plot.png',
        ]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_relative_path(value)

    def test_builds_deterministic_storage_keys_without_roots(self):
        artifact = build_artifact_storage_key(
            project_id='project 123',
            session_id='session/456',
            result_id='result:17',
            artifact_type='plot_png',
            file_name='output power.png',
        )
        report = build_report_output_storage_key(
            project_id='project 123',
            session_id='session/456',
            report_run_id='report:9',
            file_name='unii.docx',
        )

        self.assertEqual(
            artifact.relative_path,
            'projects/project-123/sessions/session-456/results/result-17/plot_png/output-power.png',
        )
        self.assertEqual(
            report.relative_path,
            'projects/project-123/sessions/session-456/reports/report-9/unii.docx',
        )
        self.assertEqual(artifact.storage_backend, 'filesystem')
        self.assertNotIn('C:', artifact.relative_path)
        self.assertNotIn('//', artifact.relative_path)

    def test_retention_planner_returns_metadata_candidates_without_bytes(self):
        now = datetime(2026, 5, 14, tzinfo=timezone.utc)
        candidates = plan_retention_candidates(
            policy=RetentionPolicy(retain_artifacts_days=30, retain_report_outputs_days=90),
            now=now,
            artifacts=[
                {
                    'relative_path': 'projects/p/sessions/s/results/r/plot_png/old.png',
                    'storage_backend': 'filesystem',
                    'created_at': '2026-03-01T00:00:00Z',
                    'file_bytes': b'not returned as top-level data',
                },
                {
                    'relative_path': 'projects/p/sessions/s/results/r/plot_png/new.png',
                    'storage_backend': 'filesystem',
                    'created_at': '2026-05-01T00:00:00Z',
                },
            ],
            report_outputs=[
                {
                    'relative_path': 'projects/p/sessions/s/reports/r/unii.pdf',
                    'storage_backend': 'filesystem',
                    'created_at': '2026-01-01T00:00:00Z',
                }
            ],
        )

        self.assertEqual([candidate.record_type for candidate in candidates], ['artifact', 'report_output'])
        self.assertEqual(candidates[0].relative_path, 'projects/p/sessions/s/results/r/plot_png/old.png')
        for candidate in candidates:
            payload = candidate.to_dict()
            self.assertIn('reason', payload)
            self.assertNotIn('delete', payload)
            self.assertNotIn('file_bytes', {key for key in payload if key != 'record'})

    def test_policy_document_matches_module_patterns(self):
        policy = json.loads(POLICY_PATH.read_text(encoding='utf-8'))

        self.assertEqual(policy['owner_repository'], 'fcc-test-platform')
        self.assertEqual(
            policy['storage_key_patterns']['artifact'],
            'projects/{project_id}/sessions/{session_id}/results/{result_id}/{artifact_type}/{file_name}',
        )
        self.assertIn('absolute_path', policy['forbidden_metadata'])
        self.assertIn('file_bytes', policy['forbidden_metadata'])

    def test_module_import_boundary_excludes_provider_and_file_io_runtime(self):
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
