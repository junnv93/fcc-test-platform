import ast
import json
import unittest
from pathlib import Path

from fcc_test_platform.report_reconstruction_evidence import (
    build_db_only_report_reconstruction_manifest,
    db_only_report_reconstruction_errors,
    is_db_only_report_reconstruction_valid,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_report_reconstruction_evidence.py')
SCHEMA_PATH = project_root / 'docs' / 'platform' / 'db_only_report_reconstruction_evidence.schema.v1.json'


def _valid_manifest() -> dict:
    return {
        'schema_version': 1,
        'provider_id': 'fcc-unlicensed-conducted',
        'session_id': 'session-uuid',
        'report_run_id': 'report-run-uuid',
        'source_mode': 'db_primary',
        'excel_source_used': False,
        'excel_export_only': True,
        'source_snapshot': {
            'central_db_migration_evidence_id': 'migration-evidence-1',
            'ingestion_batch_id': 'ingestion-batch-1',
            'snapshot_sha256': 'c' * 64,
            'measurement_result_count': 12,
        },
        'generated_outputs': [
            {
                'output_type': 'docx',
                'relative_path': 'projects/p/sessions/s/reports/r/unii.docx',
                'storage_backend': 'filesystem',
                'sha256': 'a' * 64,
            },
            {
                'output_type': 'xlsx',
                'relative_path': 'projects/p/sessions/s/reports/r/export.xlsx',
                'storage_backend': 'filesystem',
                'sha256': 'b' * 64,
            },
        ],
        'artifact_resolution': {
            'required_count': 2,
            'resolved_count': 1,
            'missing': [
                {
                    'artifact_type': 'plot_png',
                    'classification': 'template_scope_exclusion',
                    'reviewed_by': 'qa@example.com',
                    'reason': 'Not used by selected report type',
                }
            ],
        },
        'acceptance_audit': {
            'status': 'approved_with_findings',
            'reviewed_by': 'qa@example.com',
            'reviewed_at': '2026-05-15T07:30:00+09:00',
        },
    }


class TestPlatformReportReconstructionEvidence(unittest.TestCase):
    def test_valid_db_only_report_reconstruction_evidence_passes(self):
        manifest = _valid_manifest()

        self.assertTrue(is_db_only_report_reconstruction_valid(manifest))
        self.assertEqual(db_only_report_reconstruction_errors(manifest), [])

    def test_rejects_excel_source_or_wrong_source_mode(self):
        manifest = _valid_manifest()
        manifest['source_mode'] = 'excel_recovery'
        manifest['excel_source_used'] = True
        manifest['excel_export_only'] = False

        issues = db_only_report_reconstruction_errors(manifest)
        codes = {issue.code for issue in issues}

        self.assertIn('invalid_source_mode', codes)
        self.assertIn('excel_source_used', codes)
        self.assertIn('excel_not_export_only', codes)

    def test_rejects_empty_source_snapshot(self):
        manifest = _valid_manifest()
        manifest['source_snapshot']['measurement_result_count'] = 0
        manifest['source_snapshot']['central_db_migration_evidence_id'] = ''
        manifest['source_snapshot']['snapshot_sha256'] = 'short'

        issues = db_only_report_reconstruction_errors(manifest)
        codes = {issue.code for issue in issues}

        self.assertIn('empty_measurement_results', codes)
        self.assertIn('missing_required_field', codes)
        self.assertIn('invalid_sha256', codes)

    def test_rejects_outputs_without_docx_or_pdf_and_bad_path(self):
        manifest = _valid_manifest()
        manifest['generated_outputs'] = [{
            'output_type': 'xlsx',
            'relative_path': 'C:/reports/export.xlsx',
            'storage_backend': 'filesystem',
            'sha256': 'g' * 64,
        }]

        issues = db_only_report_reconstruction_errors(manifest)
        codes = {issue.code for issue in issues}

        self.assertIn('missing_report_output', codes)
        self.assertIn('invalid_relative_path', codes)
        self.assertIn('invalid_sha256', codes)

    def test_rejects_unclassified_missing_artifacts(self):
        manifest = _valid_manifest()
        manifest['artifact_resolution']['missing'][0]['classification'] = 'unknown'
        manifest['artifact_resolution']['missing'][0]['reviewed_by'] = ''

        issues = db_only_report_reconstruction_errors(manifest)
        codes = {issue.code for issue in issues}

        self.assertIn('unclassified_missing_artifact', codes)
        self.assertIn('missing_required_field', codes)

    def test_rejects_artifact_coverage_gap(self):
        manifest = _valid_manifest()
        manifest['artifact_resolution']['required_count'] = 4
        manifest['artifact_resolution']['resolved_count'] = 1

        issues = db_only_report_reconstruction_errors(manifest)

        self.assertIn('artifact_coverage_gap', {issue.code for issue in issues})

    def test_missing_artifacts_require_approved_with_findings(self):
        manifest = _valid_manifest()
        manifest['acceptance_audit']['status'] = 'pass'

        issues = db_only_report_reconstruction_errors(manifest)

        self.assertIn('missing_artifacts_require_findings', {issue.code for issue in issues})

    def test_rejects_failed_acceptance_audit(self):
        manifest = _valid_manifest()
        manifest['acceptance_audit']['status'] = 'fail'
        manifest['acceptance_audit']['reviewed_by'] = ''

        issues = db_only_report_reconstruction_errors(manifest)
        codes = {issue.code for issue in issues}

        self.assertIn('invalid_acceptance_status', codes)
        self.assertIn('missing_required_field', codes)

    def test_builds_valid_manifest_from_reconstruction_run_metadata(self):
        manifest = build_db_only_report_reconstruction_manifest(
            provider_id='fcc-unlicensed-conducted',
            session_id='session-uuid',
            report_run_id='report-run-uuid',
            central_db_migration_evidence_id='migration-evidence-1',
            ingestion_batch_id='ingestion-batch-1',
            source_snapshot_sha256='d' * 64,
            measurement_result_count=3,
            generated_outputs=[{
                'output_type': 'docx',
                'relative_path': 'projects/p/sessions/s/reports/r/generated.docx',
                'storage_backend': 'filesystem',
                'sha256': 'c' * 64,
            }],
            required_artifact_count=2,
            resolved_artifact_count=2,
            missing_artifacts=[],
            acceptance_status='PASS',
            reviewed_by='qa@example.com',
            reviewed_at='2026-05-15T11:25:00+09:00',
        )

        self.assertEqual(manifest['source_mode'], 'db_primary')
        self.assertFalse(manifest['excel_source_used'])
        self.assertTrue(manifest['excel_export_only'])
        self.assertTrue(is_db_only_report_reconstruction_valid(manifest))

    def test_schema_document_declares_contract(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

        self.assertEqual(schema['owner_repository'], 'fcc-test-platform')
        self.assertIn('source_mode must be db_primary', schema['source_rules'])
        self.assertIn('source_snapshot must include snapshot_sha256', schema['source_rules'])
        self.assertIn('sha256 must be a 64-character lowercase hex digest', schema['generated_output_rules'])
        self.assertIn('artifact_resolution must include required_count and resolved_count', schema['artifact_rules'])
        self.assertIn('any missing artifact requires acceptance_audit.status to be approved_with_findings', schema['artifact_rules'])
        self.assertIn('excel_read', schema['forbidden_operations'])

    def test_module_import_boundary_excludes_report_and_runtime_dependencies(self):
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
            'docx',
            'pathlib',
        )
        self.assertFalse([
            module for module in imports
            if module == 'infrastructure' or module.startswith(forbidden_prefixes)
        ])


if __name__ == '__main__':
    unittest.main()
