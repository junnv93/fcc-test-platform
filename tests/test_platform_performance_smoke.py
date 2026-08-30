import ast
import json
import unittest
from pathlib import Path

from fcc_test_platform.performance_smoke import (
    PERFORMANCE_SMOKE_ROUTES,
    is_performance_smoke_valid,
    performance_smoke_errors,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_performance_smoke.py')
SCHEMA_PATH = project_root / 'docs' / 'platform' / 'performance_smoke_evidence.schema.v1.json'


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


class TestPlatformPerformanceSmoke(unittest.TestCase):
    def test_valid_manifest_passes(self):
        manifest = _valid_manifest()

        self.assertTrue(is_performance_smoke_valid(manifest))
        self.assertEqual(performance_smoke_errors(manifest), [])

    def test_rejects_failed_or_over_budget_run(self):
        manifest = _valid_manifest()
        manifest['compatible'] = False
        manifest['within_budget'] = False
        manifest['total_failures'] = 1
        manifest['routes'][0]['failures'] = 1
        manifest['routes'][0]['p95_ms'] = 999.0

        issues = performance_smoke_errors(manifest)
        codes = {issue.code for issue in issues}

        self.assertIn('not_compatible', codes)
        self.assertIn('outside_budget', codes)
        self.assertIn('has_failures', codes)
        self.assertIn('route_has_failures', codes)
        self.assertIn('route_p95_exceeded', codes)

    def test_rejects_missing_required_safe_route(self):
        manifest = _valid_manifest()
        manifest['routes'] = manifest['routes'][:-1]

        issues = performance_smoke_errors(manifest)

        self.assertIn('missing_route', {issue.code for issue in issues})

    def test_schema_document_matches_route_contract(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

        self.assertEqual(schema['owner_repository'], 'fcc-test-platform')
        self.assertEqual(schema['required_routes'], list(PERFORMANCE_SMOKE_ROUTES))
        self.assertIn('each route p95_ms must be <= max_p95_ms', schema['rules'])

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
            'requests',
            'httpx',
            'subprocess',
        )
        self.assertFalse([
            module for module in imports
            if module == 'infrastructure' or module.startswith(forbidden_prefixes)
        ])


if __name__ == '__main__':
    unittest.main()
