import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fcc_test_platform.rbac import build_platform_rbac_seed


project_root = Path(__file__).parent.parent


def _valid_manifest() -> dict:
    records = build_platform_rbac_seed().to_records()
    return {
        'schema_version': 1,
        'evidence_id': 'rbac-1',
        'collected_at': '2026-05-15T18:30:00+09:00',
        'rbac_source': 'docs/platform/rbac_policy.v1.json',
        'permissions': records['permissions'],
        'roles': records['roles'],
        'users': [{'subject': 'admin@example.com', 'enabled': True}],
        'user_roles': [{'subject': 'admin@example.com', 'role_key': 'admin'}],
    }


class TestPlatformRbacAssignmentEvidenceCli(unittest.TestCase):
    def test_valid_manifest_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'rbac.json'
            path.write_text(json.dumps(_valid_manifest()), encoding='utf-8')

            completed = _run_cli('validate', str(path))

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertEqual(json.loads(completed.stdout), {'valid': True, 'issues': []})

    def test_invalid_manifest_returns_one(self):
        manifest = _valid_manifest()
        manifest['user_roles'] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'rbac.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')

            completed = _run_cli('validate', str(path))

        self.assertEqual(completed.returncode, 1)
        self.assertIn('missing_user_roles', {issue['code'] for issue in json.loads(completed.stdout)['issues']})

    def test_template_is_non_claiming_until_user_enabled(self):
        completed = _run_cli('template', '--evidence-id', 'rbac-template')

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['evidence_id'], 'rbac-template')
        self.assertFalse(payload['users'][0]['enabled'])
        self.assertEqual(payload['user_roles'][0]['role_key'], 'admin')


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, 'scripts/platform_rbac_assignment_evidence.py', *args],
        cwd=str(project_root),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


if __name__ == '__main__':
    unittest.main()
