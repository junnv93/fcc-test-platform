import ast
import json
import unittest
from pathlib import Path

from fcc_test_platform.hardware_smoke_evidence import (
    HARDWARE_SMOKE_REQUIRED_EVENT_TYPES,
    hardware_smoke_errors,
    is_hardware_smoke_valid,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_hardware_smoke_evidence.py')
SCHEMA_PATH = project_root / 'docs' / 'platform' / 'headless_hardware_smoke_evidence.schema.v1.json'


def _valid_evidence() -> dict:
    return {
        'schema_version': 1,
        'db_path': 'headless.fcc.db',
        'job': {
            'id': 7,
            'status': 'completed',
            'excel_path': 'smoke.xlsx',
            'session_id': 42,
        },
        'events': [
            {'event_type': event_type}
            for event_type in sorted(HARDWARE_SMOKE_REQUIRED_EVENT_TYPES)
        ],
        'artifacts': [
            {
                'artifact_type': 'plot_png',
                'relative_path': 'session_42/result_1/plot.png',
            }
        ],
        'report_requests': [
            {
                'id': 3,
                'status': 'completed',
                'generated_paths_json': json.dumps(['reports/dts.docx']),
            }
        ],
    }


class TestPlatformHardwareSmokeEvidence(unittest.TestCase):
    def test_valid_evidence_passes(self):
        evidence = _valid_evidence()

        self.assertTrue(is_hardware_smoke_valid(evidence))
        self.assertEqual(hardware_smoke_errors(evidence), [])

    def test_rejects_incomplete_hardware_smoke(self):
        evidence = _valid_evidence()
        evidence['job']['status'] = 'failed'
        evidence['events'] = [{'event_type': 'job_execution_started'}]
        evidence['artifacts'] = []
        evidence['report_requests'] = []

        issues = hardware_smoke_errors(evidence)
        codes = {issue.code for issue in issues}

        self.assertIn('job_not_completed', codes)
        self.assertIn('missing_event_types', codes)
        self.assertIn('missing_artifacts', codes)
        self.assertIn('missing_completed_report', codes)

    def test_rejects_unsafe_artifact_and_report_paths(self):
        evidence = _valid_evidence()
        evidence['artifacts'][0]['relative_path'] = '../plot.png'
        evidence['report_requests'][0]['generated_paths_json'] = json.dumps(['C:/reports/out.docx'])

        issues = hardware_smoke_errors(evidence)
        codes = {issue.code for issue in issues}

        self.assertIn('unsafe_artifact_path', codes)
        self.assertIn('unsafe_generated_path', codes)

    def test_schema_document_matches_required_event_types(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

        self.assertEqual(set(schema['required_event_types']), HARDWARE_SMOKE_REQUIRED_EVENT_TYPES)
        self.assertEqual(schema['required_job_status'], 'completed')
        self.assertIn('absolute paths, drive-qualified paths, and parent traversal are invalid', schema['path_rules'])

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
