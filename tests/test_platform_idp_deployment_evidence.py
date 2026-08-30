import ast
import json
import unittest
from pathlib import Path

from fcc_test_platform.idp_deployment_evidence import (
    idp_deployment_errors,
    is_idp_deployment_valid,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_idp_deployment_evidence.py')
SCHEMA_PATH = project_root / 'docs' / 'platform' / 'idp_deployment_evidence.schema.v1.json'


def _valid_manifest() -> dict:
    return {
        'schema_version': 1,
        'evidence_id': 'idp-deploy-1',
        'collected_at': '2026-05-15T18:00:00+09:00',
        'provider_key': 'company-entra-id',
        'issuer': 'https://login.example.com/tenant/v2.0',
        'discovery_document': {
            'issuer': 'https://login.example.com/tenant/v2.0',
            'authorization_endpoint': 'https://login.example.com/oauth2/authorize',
            'token_endpoint': 'https://login.example.com/oauth2/token',
            'jwks_uri': 'https://login.example.com/discovery/keys',
            'response_types_supported': ['code'],
            'code_challenge_methods_supported': ['S256'],
        },
        'jwks': {
            'fetched': True,
            'key_count': 2,
            'algorithms': ['RS256'],
        },
        'clients': [
            {
                'client_id': 'fcc-platform-shell',
                'public_client': True,
                'client_secret_present': False,
                'pkce_required': True,
                'redirect_uris': ['https://platform.example.com/'],
                'post_logout_redirect_uris': ['https://platform.example.com/'],
                'scopes': ['openid', 'profile', 'api://fcc-platform/access'],
            }
        ],
        'login_flow': {
            'browser_login_verified': True,
            'redirect_state_verified': True,
            'token_exchange_verified': True,
            'access_token_audience_verified': True,
            'role_claim_verified': True,
            'errors': [],
        },
        'session_persistence': {
            'access_token_storage': 'sessionStorage',
            'local_storage_used': False,
            'logout_clears_session': True,
        },
    }


class TestPlatformIdpDeploymentEvidence(unittest.TestCase):
    def test_valid_manifest_passes(self):
        manifest = _valid_manifest()

        self.assertTrue(is_idp_deployment_valid(manifest))
        self.assertEqual(idp_deployment_errors(manifest), [])

    def test_rejects_insecure_discovery_and_missing_pkce(self):
        manifest = _valid_manifest()
        manifest['issuer'] = 'http://login.example.com/tenant'
        manifest['discovery_document']['code_challenge_methods_supported'] = ['plain']

        codes = {issue.code for issue in idp_deployment_errors(manifest)}

        self.assertIn('insecure_url', codes)
        self.assertIn('issuer_mismatch', codes)
        self.assertIn('pkce_s256_not_supported', codes)

    def test_rejects_missing_jwks_and_secret_client(self):
        manifest = _valid_manifest()
        manifest['jwks'] = {'fetched': False, 'key_count': 0, 'algorithms': ['HS256']}
        manifest['clients'][0]['client_secret_present'] = True
        manifest['clients'][0]['pkce_required'] = False

        codes = {issue.code for issue in idp_deployment_errors(manifest)}

        self.assertIn('jwks_not_fetched', codes)
        self.assertIn('missing_jwks_keys', codes)
        self.assertIn('missing_asymmetric_jwks_algorithm', codes)
        self.assertIn('client_secret_present', codes)
        self.assertIn('pkce_not_required', codes)

    def test_rejects_unverified_login_and_unsafe_session_storage(self):
        manifest = _valid_manifest()
        manifest['login_flow']['browser_login_verified'] = False
        manifest['login_flow']['errors'] = ['state mismatch']
        manifest['session_persistence']['access_token_storage'] = 'localStorage'
        manifest['session_persistence']['local_storage_used'] = True
        manifest['session_persistence']['logout_clears_session'] = False

        codes = {issue.code for issue in idp_deployment_errors(manifest)}

        self.assertIn('browser_login_verified_not_verified', codes)
        self.assertIn('login_flow_errors_present', codes)
        self.assertIn('unsafe_access_token_storage', codes)
        self.assertIn('local_storage_used', codes)
        self.assertIn('logout_not_clearing_session', codes)

    def test_schema_document_matches_rules(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

        self.assertEqual(schema['owner_repository'], 'fcc-test-platform')
        self.assertIn('clients', schema['required_top_level'])
        self.assertIn('authorization code flow and S256 PKCE must be supported', schema['rules'])

    def test_module_import_boundary_excludes_runtime_dependencies(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden = {'requests', 'httpx', 'fastapi', 'subprocess', 'sqlite3', 'sqlalchemy', 'jwt', 'os'}
        self.assertFalse(sorted(forbidden.intersection(imports)))


if __name__ == '__main__':
    unittest.main()
