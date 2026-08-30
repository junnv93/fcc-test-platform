import ast
import json
import unittest
from pathlib import Path

from fcc_test_platform.frontend_deployment_evidence import (
    frontend_deployment_errors,
    is_frontend_deployment_valid,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_frontend_deployment_evidence.py')
SCHEMA_PATH = project_root / 'docs' / 'platform' / 'frontend_deployment_evidence.schema.v1.json'


def _valid_manifest() -> dict:
    return {
        'schema_version': 1,
        'evidence_id': 'frontend-deploy-1',
        'collected_at': '2026-05-15T16:00:00+09:00',
        'app_url': 'https://platform.example.com',
        'backend_base_url': 'https://api.platform.example.com',
        'hosting_provider': 'managed-static-hosting',
        'build_version': '2026.05.15.1',
        'build_sha256': 'a' * 64,
        'deployed': True,
        'tls_valid': True,
        'asset_cache_policy': {
            'immutable_assets': True,
            'html_cache_control': 'no-store, max-age=0',
        },
        'environment': {
            'name': 'production',
            'backend_base_url': 'https://api.platform.example.com',
            'variables': [
                {'name': 'API_BASE_URL', 'value': 'https://api.platform.example.com'},
                {'name': 'OIDC_CLIENT_SECRET', 'value': '<redacted>'},
            ],
        },
        'secret_scan': {
            'status': 'pass',
            'findings': [],
        },
    }


class TestPlatformFrontendDeploymentEvidence(unittest.TestCase):
    def test_valid_manifest_passes(self):
        manifest = _valid_manifest()

        self.assertTrue(is_frontend_deployment_valid(manifest))
        self.assertEqual(frontend_deployment_errors(manifest), [])

    def test_rejects_local_or_insecure_urls(self):
        manifest = _valid_manifest()
        manifest['app_url'] = 'http://localhost:3000'
        manifest['backend_base_url'] = 'http://127.0.0.1:8000'

        codes = {issue.code for issue in frontend_deployment_errors(manifest)}

        self.assertIn('non_https_url', codes)

    def test_rejects_unproven_deployment_and_invalid_build_hash(self):
        manifest = _valid_manifest()
        manifest['deployed'] = False
        manifest['tls_valid'] = False
        manifest['build_sha256'] = 'short'

        codes = {issue.code for issue in frontend_deployment_errors(manifest)}

        self.assertIn('frontend_not_deployed', codes)
        self.assertIn('tls_not_valid', codes)
        self.assertIn('invalid_build_sha256', codes)

    def test_rejects_cache_policy_without_no_store_html(self):
        manifest = _valid_manifest()
        manifest['asset_cache_policy']['immutable_assets'] = False
        manifest['asset_cache_policy']['html_cache_control'] = 'max-age=3600'

        codes = {issue.code for issue in frontend_deployment_errors(manifest)}

        self.assertIn('immutable_assets_not_enabled', codes)
        self.assertIn('html_cache_not_no_store', codes)

    def test_rejects_backend_config_mismatch_and_unredacted_secret(self):
        manifest = _valid_manifest()
        manifest['environment']['backend_base_url'] = 'https://wrong.example.com'
        manifest['environment']['variables'][1]['value'] = 'plain-secret-value'

        codes = {issue.code for issue in frontend_deployment_errors(manifest)}

        self.assertIn('backend_config_mismatch', codes)
        self.assertIn('unredacted_secret', codes)

    def test_rejects_failed_secret_scan(self):
        manifest = _valid_manifest()
        manifest['secret_scan'] = {
            'status': 'fail',
            'findings': [{'path': 'dist/app.js', 'kind': 'token'}],
        }

        codes = {issue.code for issue in frontend_deployment_errors(manifest)}

        self.assertIn('secret_scan_not_passed', codes)
        self.assertIn('secret_scan_findings_present', codes)

    def test_schema_document_matches_validator_rules(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

        self.assertEqual(schema['owner_repository'], 'fcc-test-platform')
        self.assertIn('build_sha256', schema['required_top_level'])
        self.assertIn('secret_scan.status must be pass and findings must be empty', schema['rules'])

    def test_module_import_boundary_excludes_runtime_side_effect_dependencies(self):
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
