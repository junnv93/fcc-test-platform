import ast
import json
import tempfile
import unittest
from pathlib import Path

from fcc_test_platform.idp_deployment_evidence import idp_deployment_errors
from scripts.platform_idp_deployment_collect import (
    ClientRegistration,
    collect_idp_deployment_evidence,
    main,
)


project_root = Path(__file__).parent.parent
SCRIPT_PATH = project_root / 'scripts' / 'platform_idp_deployment_collect.py'


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
        tree = ast.parse(SCRIPT_PATH.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden = {'requests', 'httpx', 'fastapi', 'sqlite3', 'sqlalchemy', 'playwright', 'selenium', 'jwt'}
        self.assertFalse(sorted(forbidden.intersection(imports)))


if __name__ == '__main__':
    unittest.main()
