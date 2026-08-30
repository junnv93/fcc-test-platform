import ast
import json
import unittest
from pathlib import Path

from fcc_test_platform.identity_policy import (
    identity_policy_errors,
    is_identity_policy_valid,
)
from fcc_test_platform.rbac import build_platform_rbac_seed

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_identity_policy.py')
POLICY_PATH = project_root / 'docs' / 'platform' / 'identity_policy.v1.json'


def _valid_policy() -> dict:
    return {
        'schema_version': 1,
        'provider_key': 'company-entra-id',
        'oidc': {
            'issuer': 'https://login.example.com/tenant/v2.0',
            'jwks_uri': 'https://login.example.com/tenant/discovery/v2.0/keys',
            'audiences': ['fcc-test-platform'],
            'algorithm': 'RS256',
        },
        'session': {
            'idle_timeout_seconds': 3600,
            'refresh_token_rotation': True,
            'refresh_rotation_seconds': 300,
        },
        'claim_mapping': {
            'subject_claim': 'sub',
            'display_name_claim': 'name',
            'email_claim': 'email',
            'role_claim': 'groups',
            'admin_group_allowlist': ['FCC Platform Admins'],
            'role_map': {
                'FCC Viewers': 'viewer',
                'FCC Operators': 'operator',
                'FCC Report Managers': 'report_manager',
                'FCC Platform Admins': 'admin',
            },
        },
    }


class TestPlatformIdentityPolicy(unittest.TestCase):
    def test_valid_identity_policy_passes(self):
        policy = _valid_policy()

        self.assertTrue(is_identity_policy_valid(policy))
        self.assertEqual(identity_policy_errors(policy), [])

    def test_rejects_insecure_or_localhost_oidc_urls(self):
        policy = _valid_policy()
        policy['oidc']['issuer'] = 'http://login.example.com/tenant'
        policy['oidc']['jwks_uri'] = 'https://localhost/keys'

        issues = identity_policy_errors(policy)
        codes = {issue.code for issue in issues}

        self.assertIn('insecure_url', codes)
        self.assertIn('localhost_url', codes)

    def test_rejects_missing_audience_and_weak_algorithm(self):
        policy = _valid_policy()
        policy['oidc']['audiences'] = []
        policy['oidc']['algorithm'] = 'HS256'

        issues = identity_policy_errors(policy)
        codes = {issue.code for issue in issues}

        self.assertIn('missing_audiences', codes)
        self.assertIn('weak_algorithm', codes)

    def test_rejects_unsafe_session_policy(self):
        policy = _valid_policy()
        policy['session']['idle_timeout_seconds'] = 99
        policy['session']['refresh_token_rotation'] = False
        policy['session']['refresh_rotation_seconds'] = 30

        issues = identity_policy_errors(policy)
        codes = {issue.code for issue in issues}

        self.assertIn('invalid_idle_timeout', codes)
        self.assertIn('refresh_rotation_disabled', codes)
        self.assertIn('invalid_refresh_rotation_seconds', codes)

    def test_role_mapping_uses_standard_rbac_roles(self):
        policy = _valid_policy()
        policy['claim_mapping']['role_map']['Unknown Group'] = 'superuser'

        issues = identity_policy_errors(policy)

        self.assertIn('unknown_role_mapping', {issue.code for issue in issues})

    def test_admin_mapping_requires_allowlist(self):
        policy = _valid_policy()
        policy['claim_mapping']['admin_group_allowlist'] = []

        issues = identity_policy_errors(policy)

        self.assertIn('admin_mapping_not_allowlisted', {issue.code for issue in issues})

    def test_policy_document_matches_rbac_roles(self):
        policy_doc = json.loads(POLICY_PATH.read_text(encoding='utf-8'))
        standard_roles = {role.role_key for role in build_platform_rbac_seed().roles}

        self.assertEqual(policy_doc['owner_repository'], 'fcc-test-platform')
        self.assertEqual(standard_roles, {'viewer', 'operator', 'report_manager', 'admin'})
        self.assertIn('role_map values must use the standard roles from rbac_policy.v1.json', policy_doc['claim_mapping_rules'])
        self.assertIn('jwt_validation', policy_doc['forbidden_operations'])

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
            'jwt',
            'requests',
            'httpx',
        )
        self.assertFalse([
            module for module in imports
            if module == 'infrastructure' or module.startswith(forbidden_prefixes)
        ])


if __name__ == '__main__':
    unittest.main()
