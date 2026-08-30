import ast
import json
import unittest
from pathlib import Path

from fcc_test_contracts.common.access_policy import API_PERMISSION_ADMIN, API_PERMISSION_PUBLIC
from fcc_test_contracts.headless.api_contracts import HEADLESS_API_OPERATIONS
from fcc_test_platform.rbac import (
    build_platform_rbac_seed,
    operation_permission_keys,
    validate_platform_rbac_seed,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_rbac.py')
POLICY_PATH = project_root / 'docs' / 'platform' / 'rbac_policy.v1.json'
SCHEMA_PATH = project_root / 'docs' / 'platform' / 'central_db_schema.v1.json'


class TestPlatformRbacPolicy(unittest.TestCase):
    def test_operation_permission_keys_come_from_api_contract(self):
        expected = {
            contract['permission']
            for contract in HEADLESS_API_OPERATIONS.values()
            if contract['permission'] != API_PERMISSION_PUBLIC
        }

        self.assertEqual(set(operation_permission_keys()), expected)

    def test_rbac_seed_covers_all_non_public_operation_permissions(self):
        seed = build_platform_rbac_seed()
        records = seed.to_records()
        operation_permissions = set(operation_permission_keys())
        seed_permissions = {row['permission_key'] for row in records['permissions']}

        self.assertTrue(operation_permissions <= seed_permissions)
        self.assertIn(API_PERMISSION_ADMIN, seed_permissions)
        self.assertEqual(validate_platform_rbac_seed(seed), [])

    def test_standard_roles_are_deterministic_and_do_not_grant_unknown_permissions(self):
        seed = build_platform_rbac_seed()
        roles = {role.role_key: role for role in seed.roles}
        allowed_permissions = set(seed.permissions)

        self.assertEqual(
            list(roles),
            ['viewer', 'operator', 'report_manager', 'admin'],
        )
        # The platform RBAC seed is DERIVED from the headless API operation
        # contract (module docstring + rbac_policy.v1.json permission_source), so
        # every non-public ``*:read`` permission flows to the viewer role. The
        # test-plan draft surface (IMPL-1, 2026-06-03) added ``test_plan:read``,
        # which is naturally granted here. ``test_plan:author`` ends with neither
        # ``:read`` nor ``:control`` so it stays defined-but-unassigned to the
        # standard roles (draft authoring is headless token-scoped — D-2 deferred
        # for platform-membership union).
        self.assertEqual(
            set(roles['viewer'].permission_keys),
            {'headless:read', 'report_automation:read', 'test_plan:read'},
        )
        self.assertIn('test_plan:author', set(seed.permissions))
        self.assertNotIn(
            'test_plan:author',
            set(roles['operator'].permission_keys) | set(roles['report_manager'].permission_keys),
        )
        self.assertIn('headless:control', roles['operator'].permission_keys)
        self.assertIn('report_automation:control', roles['report_manager'].permission_keys)
        self.assertEqual(roles['admin'].permission_keys, (API_PERMISSION_ADMIN,))
        for key, role in roles.items():
            self.assertTrue(set(role.permission_keys) <= allowed_permissions, key)
            if key != 'admin':
                self.assertNotIn(API_PERMISSION_ADMIN, role.permission_keys)

    def test_policy_document_and_central_schema_cover_rbac_tables(self):
        policy = json.loads(POLICY_PATH.read_text(encoding='utf-8'))
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

        self.assertEqual(policy['owner_repository'], 'fcc-test-platform')
        self.assertEqual(
            policy['permission_source'],
            'src/application/headless/api_contracts.py::HEADLESS_API_OPERATIONS',
        )
        self.assertTrue(set(policy['database_tables']) <= set(schema['tables']))
        self.assertEqual(
            set(policy['standard_roles']),
            {'viewer', 'operator', 'report_manager', 'admin'},
        )

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
        )
        self.assertFalse([
            module for module in imports
            if module == 'infrastructure' or module.startswith(forbidden_prefixes)
        ])


if __name__ == '__main__':
    unittest.main()
