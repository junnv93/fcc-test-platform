import ast
import json
import tempfile
import unittest
from pathlib import Path

from fcc_test_platform.frontend_deployment_evidence import frontend_deployment_errors
from scripts.platform_frontend_deployment_collect import (
    ProbeResult,
    collect_frontend_deployment_evidence,
    main,
)


project_root = Path(__file__).parent.parent
SCRIPT_PATH = project_root / 'scripts' / 'platform_frontend_deployment_collect.py'


def _probe(url: str, timeout_seconds: float) -> ProbeResult:
    headers = {'cache-control': 'no-store'} if url.endswith('/') else {'cache-control': 'public, max-age=31536000, immutable'}
    return ProbeResult(url=url, ok=True, status_code=200, tls_valid=True, headers=headers)


class TestPlatformFrontendDeploymentCollect(unittest.TestCase):
    def test_collects_validator_compatible_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            build_root = root / 'dist'
            build_root.mkdir()
            (build_root / 'index.html').write_text('<script src="/app.123.js"></script>', encoding='utf-8')
            (build_root / 'app.123.js').write_text('console.log("ok")', encoding='utf-8')

            manifest = collect_frontend_deployment_evidence(
                evidence_id='frontend-deploy-1',
                app_url='https://platform.example.com/',
                backend_base_url='https://api.platform.example.com/health',
                hosting_provider='managed-static-hosting',
                build_version='2026.05.15.1',
                build_root=build_root,
                environment_name='production',
                environment_variables={
                    'API_BASE_URL': 'https://api.platform.example.com',
                    'OIDC_CLIENT_SECRET': 'plain-secret',
                },
                immutable_asset_urls=['https://platform.example.com/app.123.js'],
                secret_scan_json=None,
                timeout_seconds=1.0,
                url_probe=_probe,
            )

        self.assertEqual(frontend_deployment_errors(manifest), [])
        self.assertEqual(manifest['environment']['variables'][1]['value'], '<redacted>')
        self.assertEqual(manifest['secret_scan'], {'status': 'pass', 'findings': []})
        self.assertTrue(manifest['build_sha256'])

    def test_secret_like_build_literal_fails_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            build_root = root / 'dist'
            build_root.mkdir()
            (build_root / 'app.js').write_text('const apiToken = "1234567890abcdef";', encoding='utf-8')

            manifest = collect_frontend_deployment_evidence(
                evidence_id='frontend-deploy-1',
                app_url='https://platform.example.com/',
                backend_base_url='https://api.platform.example.com/health',
                hosting_provider='managed-static-hosting',
                build_version='2026.05.15.1',
                build_root=build_root,
                environment_name='production',
                environment_variables={'API_BASE_URL': 'https://api.platform.example.com'},
                immutable_asset_urls=['https://platform.example.com/app.js'],
                secret_scan_json=None,
                timeout_seconds=1.0,
                url_probe=_probe,
            )

        self.assertIn('secret_scan_not_passed', {issue.code for issue in frontend_deployment_errors(manifest)})

    def test_cli_writes_output_and_honors_require_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            build_root = root / 'dist'
            build_root.mkdir()
            (build_root / 'index.html').write_text('ok', encoding='utf-8')
            output = root / 'frontend-deploy.json'

            status = main([
                '--evidence-id', 'frontend-deploy-1',
                '--app-url', 'https://platform.example.com/',
                '--backend-base-url', 'https://api.platform.example.com/health',
                '--hosting-provider', 'managed-static-hosting',
                '--build-version', '2026.05.15.1',
                '--build-root', str(build_root),
                '--environment-name', 'production',
                '--environment-variable', 'API_BASE_URL=https://api.platform.example.com',
                '--immutable-asset-url', 'https://platform.example.com/app.123.js',
                '--output', str(output),
                '--require-valid',
            ], url_probe=_probe)

            manifest = json.loads(output.read_text(encoding='utf-8'))

        self.assertEqual(status, 0)
        self.assertEqual(frontend_deployment_errors(manifest), [])

    def test_cli_import_boundary_uses_stdlib_only(self):
        tree = ast.parse(SCRIPT_PATH.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden = {'requests', 'httpx', 'fastapi', 'sqlite3', 'sqlalchemy', 'playwright', 'selenium'}
        self.assertFalse(sorted(forbidden.intersection(imports)))


if __name__ == '__main__':
    unittest.main()
