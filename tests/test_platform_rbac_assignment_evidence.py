import json
import unittest
from pathlib import Path

from fcc_test_platform.rbac import build_platform_rbac_seed
from fcc_test_platform.rbac_assignment_evidence import (
    is_rbac_assignment_evidence_valid,
    rbac_assignment_evidence_errors,
)


project_root = Path(__file__).parent.parent
SCHEMA_PATH = project_root / 'docs' / 'platform' / 'rbac_assignment_evidence.schema.v1.json'


def _valid_manifest() -> dict:
    records = build_platform_rbac_seed().to_records()
    return {
        'schema_version': 1,
        'evidence_id': 'rbac-1',
        'collected_at': '2026-05-15T18:30:00+09:00',
        'rbac_source': 'docs/platform/rbac_policy.v1.json',
        'permissions': records['permissions'],
        'roles': records['roles'],
        'users': [
            {'subject': 'admin@example.com', 'enabled': True},
            {'subject': 'viewer@example.com', 'enabled': True},
        ],
        'user_roles': [
            {'subject': 'admin@example.com', 'role_key': 'admin'},
            {'subject': 'viewer@example.com', 'role_key': 'viewer'},
        ],
    }


class TestPlatformRbacAssignmentEvidence(unittest.TestCase):
    def test_valid_manifest_passes(self):
        manifest = _valid_manifest()

        self.assertTrue(is_rbac_assignment_evidence_valid(manifest))
        self.assertEqual(rbac_assignment_evidence_errors(manifest), [])

    def test_rejects_missing_permission_and_role_drift(self):
        manifest = _valid_manifest()
        manifest['permissions'] = manifest['permissions'][:-1]
        manifest['roles'][0]['permission_keys'] = []

        codes = {issue.code for issue in rbac_assignment_evidence_errors(manifest)}

        self.assertIn('missing_permission', codes)
        self.assertIn('role_permission_mismatch', codes)

    def test_rejects_unknown_or_disabled_assignments_and_missing_admin(self):
        manifest = _valid_manifest()
        manifest['users'][0]['enabled'] = False
        manifest['user_roles'] = [
            {'subject': 'admin@example.com', 'role_key': 'admin'},
            {'subject': 'ghost@example.com', 'role_key': 'viewer'},
            {'subject': 'viewer@example.com', 'role_key': 'unknown'},
        ]

        codes = {issue.code for issue in rbac_assignment_evidence_errors(manifest)}

        self.assertIn('disabled_user_assignment', codes)
        self.assertIn('unknown_user_assignment', codes)
        self.assertIn('unknown_role_assignment', codes)
        self.assertIn('missing_admin_assignment', codes)

    def test_schema_document_declares_assignment_rules(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

        self.assertEqual(schema['owner_repository'], 'fcc-test-platform')
        self.assertIn('user_roles', schema['required_top_level'])
        self.assertIn('at least one enabled user must have the admin role', schema['rules'])


if __name__ == '__main__':
    unittest.main()
