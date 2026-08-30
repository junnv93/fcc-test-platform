import ast
import json
import unittest
from pathlib import Path

from fcc_test_platform.frontend_qa_evidence import (
    FRONTEND_REQUIRED_VIEWS,
    frontend_qa_errors,
    is_frontend_qa_valid,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_frontend_qa_evidence.py')
SCHEMA_PATH = project_root / 'docs' / 'platform' / 'frontend_browser_qa_evidence.schema.v1.json'


def _valid_manifest() -> dict:
    return {
        'schema_version': 1,
        'evidence_id': 'frontend-qa-1',
        'collected_at': '2026-05-15T15:00:00+09:00',
        'app_url': 'https://platform.example.com',
        'browser': 'Chromium 125',
        'auth_flow_verified': True,
        'central_backend_verified': True,
        'provider_contract_routes_verified': True,
        'viewport_results': [
            {
                'name': 'desktop',
                'width': 1440,
                'height': 900,
                'rendered': True,
                'responsive_pass': True,
                'views_verified': list(FRONTEND_REQUIRED_VIEWS),
                'console_errors': [],
                'failed_requests': [],
            }
        ],
        'screenshots': [
            {
                'viewport': 'desktop',
                'relative_path': 'frontend/desktop-overview.png',
                'sha256': 'a' * 64,
            }
        ],
    }


class TestPlatformFrontendQaEvidence(unittest.TestCase):
    def test_valid_manifest_passes(self):
        manifest = _valid_manifest()

        self.assertTrue(is_frontend_qa_valid(manifest))
        self.assertEqual(frontend_qa_errors(manifest), [])

    def test_rejects_unverified_browser_qa(self):
        manifest = _valid_manifest()
        manifest['auth_flow_verified'] = False
        manifest['viewport_results'][0]['console_errors'] = ['TypeError']
        manifest['viewport_results'][0]['views_verified'] = ['overview']

        issues = frontend_qa_errors(manifest)
        codes = {issue.code for issue in issues}

        self.assertIn('auth_flow_not_verified', codes)
        self.assertIn('console_errors_present', codes)
        self.assertIn('missing_required_view', codes)

    def test_rejects_invalid_screenshot_hash(self):
        manifest = _valid_manifest()
        manifest['screenshots'][0]['sha256'] = '<sha256>'

        issues = frontend_qa_errors(manifest)

        self.assertIn('invalid_sha256', {issue.code for issue in issues})

    def test_rejects_unsafe_screenshot_path(self):
        manifest = _valid_manifest()
        manifest['screenshots'][0]['relative_path'] = '../outside.png'

        issues = frontend_qa_errors(manifest)

        self.assertIn('invalid_relative_path', {issue.code for issue in issues})

    def test_schema_document_matches_required_views(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

        self.assertEqual(schema['owner_repository'], 'fcc-test-platform')
        self.assertEqual(schema['required_views'], list(FRONTEND_REQUIRED_VIEWS))
        self.assertIn('screenshot sha256 values must be 64 lowercase hex characters', schema['rules'])
        self.assertIn('screenshot relative_path values must be safe relative paths', schema['rules'])

    def test_module_import_boundary_excludes_browser_runtime_dependencies(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden = {'requests', 'httpx', 'fastapi', 'subprocess', 'sqlite3', 'sqlalchemy', 'playwright', 'selenium', 'os'}
        self.assertFalse(sorted(forbidden.intersection(imports)))


if __name__ == '__main__':
    unittest.main()
