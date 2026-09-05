import ast
import json
import tempfile
import unittest
from pathlib import Path

from fcc_test_platform.frontend_deployment_evidence import frontend_deployment_errors
from fcc_test_platform.frontend_deployment_collect_cli import (
    ProbeResult,
    collect_frontend_deployment_evidence,
    main,
)
from tests._moved_module_source import moved_module_source


project_root = Path(__file__).parent.parent
# ⚠️ 여기 있던 `SCRIPT_PATH`(= `scripts/…` 껍데기 경로)은 2026-09-05 에 «죽은 상수»가
#    됐다. 그것을 쓰던 유일한 자리가 경계 단언이었고, 그 단언이 읽어야 할 것은
#    껍데기가 아니라 알맹이였다(아래 `_GUTS_SOURCE`). 껍데기를 하위 프로세스로
#    부르는 자리는 상수가 아니라 리터럴 경로를 쓴다 — 되살릴 이유가 없다.
# ⚠️ **경계는 «알맹이»에게 물어야 한다** — 껍데기는 22줄이라 어떤 금지 import 도
# 없고, 그것을 읽는 단언은 «참이지만 아무것도 재지 않는 참»이 된다. 경로가 아니라
# 모듈에게 묻는다 (`tests/_moved_module_source.py`).
_GUTS_SOURCE = moved_module_source('fcc_test_platform.frontend_deployment_collect_cli')



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
        tree = ast.parse(_GUTS_SOURCE.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden = {'requests', 'httpx', 'fastapi', 'sqlite3', 'sqlalchemy', 'playwright', 'selenium'}
        self.assertTrue(
            imports,
            f'{_GUTS_SOURCE} 가 아무것도 import 하지 않는다 — 알맹이가 또 옮겨갔다면 '
            '이 검사도 «새 자리»를 가리키게 고쳐라. 이 팔을 지우면 아래 경계 단언이 '
            '«참이지만 아무것도 재지 않는 참»으로 되돌아간다.',
        )

        self.assertFalse(sorted(forbidden.intersection(imports)))


if __name__ == '__main__':
    unittest.main()
