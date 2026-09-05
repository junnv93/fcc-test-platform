import json
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from fcc_test_platform.rbac import build_platform_rbac_seed
from fcc_test_platform.rbac_assignment_evidence import rbac_assignment_evidence_errors
from fcc_test_platform.rbac_assignment_collect_cli import (
    _quote_ident,
    build_manifest_from_rows,
    main,
)


class TestPlatformRbacAssignmentCollect(unittest.TestCase):
    def test_build_manifest_from_rows_matches_validator(self):
        seed = build_platform_rbac_seed()
        rows = seed.to_records()
        manifest = build_manifest_from_rows(
            evidence_id='rbac-db-1',
            permissions=rows['permissions'],
            roles=[{'role_key': role['role_key'], 'description': role['description']} for role in rows['roles']],
            role_permissions=[
                {'role_key': role['role_key'], 'permission_key': permission}
                for role in rows['roles']
                for permission in role['permission_keys']
            ],
            users=[
                {
                    'subject': 'admin@example.com',
                    'display_name': 'Admin User',
                    'email': 'admin@example.com',
                    'enabled': True,
                }
            ],
            user_roles=[{'subject': 'admin@example.com', 'role_key': 'admin'}],
        )

        self.assertEqual(rbac_assignment_evidence_errors(manifest), [])

    def test_quote_identifier_escapes_double_quotes(self):
        self.assertEqual(_quote_ident('public'), '"public"')
        self.assertEqual(_quote_ident('tenant"schema'), '"tenant""schema"')

    def test_cli_runtime_error_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stderr = StringIO()
            with patch('fcc_test_platform.rbac_assignment_collect_cli._connect', side_effect=RuntimeError('db unavailable')):
                with redirect_stderr(stderr):
                    exit_code = main([
                        '--dsn',
                        'postgresql://example',
                        '--output',
                        str(Path(tmpdir) / 'rbac.json'),
                        '--evidence-id',
                        'rbac-1',
                    ])

        self.assertEqual(exit_code, 2)
        self.assertFalse(json.loads(stderr.getvalue())['collected'])


if __name__ == '__main__':
    unittest.main()
