import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fcc_test_platform.performance_smoke import PERFORMANCE_SMOKE_ROUTES


project_root = Path(__file__).parent.parent
CLI_PATH = project_root / 'scripts' / 'platform_performance_smoke.py'


def _valid_manifest() -> dict:
    return {
        'schema_version': 1,
        'provider_id': 'fcc-unlicensed-conducted',
        'benchmark_id': 'bench-1',
        'collected_at': '2026-05-15T12:00:00+09:00',
        'target_base_url': 'https://platform.example.com',
        'iterations': 10,
        'workers': 4,
        'max_p95_ms': 500.0,
        'compatible': True,
        'within_budget': True,
        'total_requests': 30,
        'total_failures': 0,
        'routes': [
            {
                'route': route,
                'requests': 10,
                'successes': 10,
                'failures': 0,
                'min_ms': 1.0,
                'mean_ms': 2.0,
                'p95_ms': 3.0,
                'max_ms': 4.0,
            }
            for route in PERFORMANCE_SMOKE_ROUTES
        ],
    }


class TestPlatformPerformanceSmokeCli(unittest.TestCase):
    def test_valid_manifest_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'performance.json'
            path.write_text(json.dumps(_valid_manifest()), encoding='utf-8')

            completed = _run_cli('validate', str(path))

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertEqual(json.loads(completed.stdout), {'valid': True, 'issues': []})

    def test_invalid_manifest_returns_one_with_issues(self):
        manifest = _valid_manifest()
        manifest['routes'][0]['p95_ms'] = 999.0
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'performance.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')

            completed = _run_cli('validate', str(path))

        self.assertEqual(completed.returncode, 1)
        self.assertIn('route_p95_exceeded', {issue['code'] for issue in json.loads(completed.stdout)['issues']})

    def test_read_error_returns_two(self):
        completed = _run_cli('validate', 'missing.json')

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)['issues'][0]['code'], 'read_error')

    def test_template_outputs_required_routes_without_claiming_pass(self):
        completed = _run_cli(
            'template',
            '--provider-id',
            'fcc-unlicensed-conducted',
            '--benchmark-id',
            'bench-template',
            '--target-base-url',
            'https://platform.example.com',
        )

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['benchmark_id'], 'bench-template')
        self.assertEqual({row['route'] for row in payload['routes']}, set(PERFORMANCE_SMOKE_ROUTES))
        self.assertFalse(payload['compatible'])
        self.assertFalse(payload['within_budget'])
        self.assertEqual(payload['iterations'], 0)

    def test_cli_import_boundary_excludes_network_or_runtime_dependencies(self):
        tree = ast.parse(CLI_PATH.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden = {'requests', 'httpx', 'fastapi', 'subprocess', 'sqlite3', 'sqlalchemy', 'os'}
        self.assertFalse(sorted(forbidden.intersection(imports)))


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, 'scripts/platform_performance_smoke.py', *args],
        cwd=str(project_root),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


if __name__ == '__main__':
    unittest.main()
