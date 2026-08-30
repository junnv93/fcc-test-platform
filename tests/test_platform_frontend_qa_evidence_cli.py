import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fcc_test_platform.frontend_qa_evidence import FRONTEND_REQUIRED_VIEWS


project_root = Path(__file__).parent.parent
CLI_PATH = project_root / 'scripts' / 'platform_frontend_qa_evidence.py'


def _valid_manifest() -> dict:
    return {
        'schema_version': 1,
        'evidence_id': 'frontend-qa-1',
        'collected_at': '2026-05-15T15:00:00+09:00',
        'app_url': 'https://platform.example.com',
        'browser': 'Chromium 125',
        'auth_flow_verified': True,
        'central_backend_verified': True,
        'provider_contract_routes_verified': True,
        'viewport_results': [
            {
                'name': 'desktop',
                'width': 1440,
                'height': 900,
                'rendered': True,
                'responsive_pass': True,
                'views_verified': list(FRONTEND_REQUIRED_VIEWS),
                'console_errors': [],
                'failed_requests': [],
            }
        ],
        'screenshots': [
            {
                'viewport': 'desktop',
                'relative_path': 'frontend/desktop-overview.png',
                'sha256': 'a' * 64,
            }
        ],
    }


class TestPlatformFrontendQaEvidenceCli(unittest.TestCase):
    def test_valid_manifest_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'frontend.json'
            path.write_text(json.dumps(_valid_manifest()), encoding='utf-8')

            completed = _run_cli('validate', str(path))

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertEqual(json.loads(completed.stdout), {'valid': True, 'issues': []})

    def test_invalid_manifest_returns_one(self):
        manifest = _valid_manifest()
        manifest['central_backend_verified'] = False
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'frontend.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')

            completed = _run_cli('validate', str(path))

        self.assertEqual(completed.returncode, 1)
        self.assertIn('central_backend_not_verified', {issue['code'] for issue in json.loads(completed.stdout)['issues']})

    def test_read_error_returns_two(self):
        completed = _run_cli('validate', 'missing.json')

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)['issues'][0]['code'], 'read_error')

    def test_template_does_not_claim_browser_qa(self):
        completed = _run_cli('template', '--evidence-id', 'frontend-template', '--app-url', 'https://platform.example.com')

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['evidence_id'], 'frontend-template')
        self.assertFalse(payload['auth_flow_verified'])
        self.assertFalse(payload['central_backend_verified'])
        self.assertIn('replace with an empty list after QA', payload['viewport_results'][0]['console_errors'])

    def test_cli_import_boundary_excludes_browser_runtime_dependencies(self):
        tree = ast.parse(CLI_PATH.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden = {'requests', 'httpx', 'fastapi', 'subprocess', 'sqlite3', 'sqlalchemy', 'playwright', 'selenium', 'os'}
        self.assertFalse(sorted(forbidden.intersection(imports)))


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, 'scripts/platform_frontend_qa_evidence.py', *args],
        cwd=str(project_root),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


if __name__ == '__main__':
    unittest.main()
