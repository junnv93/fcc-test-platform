import ast
import json
import tempfile
import unittest
from pathlib import Path

from fcc_test_platform.idp_deployment_evidence import idp_deployment_errors
from fcc_test_platform.idp_deployment_collect_cli import (
    ClientRegistration,
    collect_idp_deployment_evidence,
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
_GUTS_SOURCE = moved_module_source('fcc_test_platform.idp_deployment_collect_cli')



def _fetcher(url: str, timeout_seconds: float) -> dict:
    if url.endswith('/.well-known/openid-configuration'):
        return {
            'issuer': 'https://login.example.com/tenant',
            'authorization_endpoint': 'https://login.example.com/authorize',
            'token_endpoint': 'https://login.example.com/token',
            'jwks_uri': 'https://login.example.com/keys',
            'response_types_supported': ['code'],
            'code_challenge_methods_supported': ['S256'],
        }
    if url == 'https://login.example.com/keys':
        return {'keys': [{'kid': '1', 'alg': 'RS256'}]}
    raise AssertionError(url)


class TestPlatformIdpDeploymentCollect(unittest.TestCase):
    def test_collects_validator_compatible_manifest(self):
        manifest = collect_idp_deployment_evidence(
            evidence_id='idp-deploy-1',
            provider_key='company-idp',
            issuer='https://login.example.com/tenant/',
            client=ClientRegistration(
                client_id='fcc-platform-shell',
                redirect_uris=['https://platform.example.com/'],
                post_logout_redirect_uris=['https://platform.example.com/'],
                scopes=['openid', 'profile', 'api://fcc-platform/access'],
                public_client=True,
                client_secret_present=False,
                pkce_required=True,
            ),
            login_flow={
                'browser_login_verified': True,
                'redirect_state_verified': True,
                'token_exchange_verified': True,
                'access_token_audience_verified': True,
                'role_claim_verified': True,
                'errors': [],
            },
            session_persistence={
                'access_token_storage': 'sessionStorage',
                'local_storage_used': False,
                'logout_clears_session': True,
            },
            timeout_seconds=1.0,
            json_fetcher=_fetcher,
        )

        self.assertEqual(idp_deployment_errors(manifest), [])
        self.assertEqual(manifest['jwks']['algorithms'], ['RS256'])
        self.assertEqual(manifest['issuer'], 'https://login.example.com/tenant')

    def test_collector_surfaces_missing_pkce_from_discovery(self):
        def fetcher(url: str, timeout_seconds: float) -> dict:
            payload = _fetcher(url, timeout_seconds)
            if url.endswith('/.well-known/openid-configuration'):
                payload['code_challenge_methods_supported'] = ['plain']
            return payload

        manifest = collect_idp_deployment_evidence(
            evidence_id='idp-deploy-1',
            provider_key='company-idp',
            issuer='https://login.example.com/tenant',
            client=ClientRegistration(
                client_id='fcc-platform-shell',
                redirect_uris=['https://platform.example.com/'],
                post_logout_redirect_uris=['https://platform.example.com/'],
                scopes=['openid', 'profile', 'api://fcc-platform/access'],
                public_client=True,
                client_secret_present=False,
                pkce_required=True,
            ),
            login_flow={
                'browser_login_verified': True,
                'redirect_state_verified': True,
                'token_exchange_verified': True,
                'access_token_audience_verified': True,
                'role_claim_verified': True,
                'errors': [],
            },
            session_persistence={
                'access_token_storage': 'sessionStorage',
                'local_storage_used': False,
                'logout_clears_session': True,
            },
            timeout_seconds=1.0,
            json_fetcher=fetcher,
        )

        self.assertIn('pkce_s256_not_supported', {issue.code for issue in idp_deployment_errors(manifest)})

    def test_cli_writes_output_and_honors_require_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'idp.json'

            status = main([
                '--evidence-id', 'idp-deploy-1',
                '--provider-key', 'company-idp',
                '--issuer', 'https://login.example.com/tenant',
                '--client-id', 'fcc-platform-shell',
                '--redirect-uri', 'https://platform.example.com/',
                '--scope', 'openid',
                '--scope', 'profile',
                '--scope', 'api://fcc-platform/access',
                '--public-client',
                '--pkce-required',
                '--browser-login-verified',
                '--redirect-state-verified',
                '--token-exchange-verified',
                '--access-token-audience-verified',
                '--role-claim-verified',
                '--logout-clears-session',
                '--output', str(output),
                '--require-valid',
            ], json_fetcher=_fetcher)

            manifest = json.loads(output.read_text(encoding='utf-8'))

        self.assertEqual(status, 0)
        self.assertEqual(idp_deployment_errors(manifest), [])

    def test_cli_import_boundary_uses_stdlib_only(self):
        tree = ast.parse(_GUTS_SOURCE.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden = {'requests', 'httpx', 'fastapi', 'sqlite3', 'sqlalchemy', 'playwright', 'selenium', 'jwt'}
        self.assertTrue(
            imports,
            f'{_GUTS_SOURCE} 가 아무것도 import 하지 않는다 — 알맹이가 또 옮겨갔다면 '
            '이 검사도 «새 자리»를 가리키게 고쳐라. 이 팔을 지우면 아래 경계 단언이 '
            '«참이지만 아무것도 재지 않는 참»으로 되돌아간다.',
        )

        self.assertFalse(sorted(forbidden.intersection(imports)))


if __name__ == '__main__':
    unittest.main()
