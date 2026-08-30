import ast
import json
import unittest
from pathlib import Path

from fcc_test_platform.ingestion_execution_evidence import (
    build_ingestion_execution_manifest,
    canonical_ingestion_plan_sha256,
    ingestion_execution_errors,
    is_ingestion_execution_valid,
)
from fcc_test_platform.provider_ingestion_worker import IngestionExecutionResult, IngestionStepExecution

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_ingestion_execution_evidence.py')
SCHEMA_PATH = project_root / 'docs' / 'platform' / 'ingestion_execution_evidence.schema.v1.json'


def _valid_manifest() -> dict:
    return {
        'schema_version': 1,
        'evidence_id': 'ingest-exec-1',
        'collected_at': '2026-05-15T19:00:00+09:00',
        'provider_id': 'fcc-unlicensed-conducted',
        'session_id': 'session-uuid',
        'database_name': 'fcc_platform',
        'writer_adapter': 'postgres',
        'plan_sha256': 'a' * 64,
        'executed': True,
        'transaction_committed': True,
        'transaction_rolled_back': False,
        'attempts': 1,
        'attempted_steps': 2,
        'applied_steps': 2,
        'errors': [],
        'retry_errors': [],
        'steps': [
            {
                'order': 1,
                'target_table': 'measurement_results',
                'operation': 'upsert',
                'idempotency_key': ['provider-uuid', 'result-1'],
                'affected_rows': 1,
            },
            {
                'order': 2,
                'target_table': 'artifacts',
                'operation': 'upsert',
                'idempotency_key': ['provider-uuid', 'plot.png'],
                'affected_rows': 1,
            },
        ],
    }


class TestPlatformIngestionExecutionEvidence(unittest.TestCase):
    def test_valid_manifest_passes(self):
        manifest = _valid_manifest()

        self.assertTrue(is_ingestion_execution_valid(manifest))
        self.assertEqual(ingestion_execution_errors(manifest), [])

    def test_rejects_failed_transaction(self):
        manifest = _valid_manifest()
        manifest['executed'] = False
        manifest['transaction_committed'] = False
        manifest['transaction_rolled_back'] = True
        manifest['attempts'] = 0
        manifest['attempted_steps'] = 0
        manifest['applied_steps'] = 0
        manifest['errors'] = ['deadlock']

        codes = {issue.code for issue in ingestion_execution_errors(manifest)}

        self.assertIn('not_executed', codes)
        self.assertIn('transaction_not_committed', codes)
        self.assertIn('transaction_rolled_back', codes)
        self.assertIn('invalid_attempts', codes)
        self.assertIn('invalid_attempted_steps', codes)
        self.assertIn('invalid_applied_steps', codes)
        self.assertIn('execution_errors_present', codes)

    def test_rejects_inconsistent_step_counts(self):
        manifest = _valid_manifest()
        manifest['attempted_steps'] = 1
        manifest['applied_steps'] = 3

        codes = {issue.code for issue in ingestion_execution_errors(manifest)}

        self.assertIn('applied_steps_exceed_attempted', codes)
        self.assertIn('applied_steps_mismatch', codes)

    def test_allows_retry_history_for_successful_final_transaction(self):
        manifest = _valid_manifest()
        manifest['attempts'] = 2
        manifest['retry_errors'] = ['deadlock']

        self.assertTrue(is_ingestion_execution_valid(manifest))
        self.assertEqual(ingestion_execution_errors(manifest), [])

    def test_rejects_malformed_retry_history(self):
        manifest = _valid_manifest()
        manifest['retry_errors'] = 'deadlock'

        codes = {issue.code for issue in ingestion_execution_errors(manifest)}

        self.assertIn('invalid_retry_errors', codes)

    def test_rejects_invalid_plan_hash_and_step(self):
        manifest = _valid_manifest()
        manifest['plan_sha256'] = 'short'
        manifest['steps'][1]['order'] = 1
        manifest['steps'][1]['target_table'] = 'provider_private'
        manifest['steps'][1]['operation'] = 'delete'
        manifest['steps'][1]['idempotency_key'] = []
        manifest['steps'][1]['affected_rows'] = -1

        codes = {issue.code for issue in ingestion_execution_errors(manifest)}

        self.assertIn('invalid_plan_sha256', codes)
        self.assertIn('invalid_step_order', codes)
        self.assertIn('unknown_target_table', codes)
        self.assertIn('invalid_operation', codes)
        self.assertIn('missing_idempotency_key', codes)
        self.assertIn('invalid_affected_rows', codes)

    def test_builds_manifest_from_execution_result_and_canonical_plan_hash(self):
        plan = {
            'steps': [{
                'order': 1,
                'target_table': 'measurement_results',
                'operation': 'upsert',
                'idempotency_key': ['provider-uuid', 'result-1'],
                'record': {'provider_id': 'provider-uuid', 'provider_result_id': 'result-1'},
            }],
        }
        result = IngestionExecutionResult(
            attempted_steps=1,
            applied_steps=1,
            attempts=2,
            committed=True,
            rolled_back=False,
            retry_errors=('deadlock',),
            steps=(IngestionStepExecution(
                order=1,
                target_table='measurement_results',
                operation='upsert',
                idempotency_key=('provider-uuid', 'result-1'),
                affected_rows=1,
            ),),
        )

        manifest = build_ingestion_execution_manifest(
            evidence_id='ingest-live-1',
            provider_id='fcc-unlicensed-conducted',
            session_id='session-uuid',
            database_name='fcc_platform',
            plan=plan,
            result=result,
            collected_at='2026-05-15T10:50:00+09:00',
        )

        self.assertEqual(manifest['plan_sha256'], canonical_ingestion_plan_sha256(plan))
        self.assertTrue(is_ingestion_execution_valid(manifest))
        self.assertEqual(manifest['attempted_steps'], 1)
        self.assertEqual(manifest['applied_steps'], 1)
        self.assertEqual(manifest['retry_errors'], ['deadlock'])
        self.assertEqual(manifest['steps'][0]['affected_rows'], 1)

    def test_schema_document_matches_rules(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

        self.assertEqual(schema['owner_repository'], 'fcc-test-platform')
        self.assertIn('plan_sha256', schema['required_top_level'])
        self.assertIn('attempted_steps', schema['required_top_level'])
        self.assertIn('applied_steps', schema['required_top_level'])
        self.assertIn('retry_errors', schema['required_top_level'])
        self.assertIn('operation must be upsert', schema['rules'])

    def test_module_import_boundary_excludes_runtime_dependencies(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden = {'requests', 'httpx', 'fastapi', 'subprocess', 'sqlite3', 'sqlalchemy', 'psycopg', 'os'}
        self.assertFalse(sorted(forbidden.intersection(imports)))


if __name__ == '__main__':
    unittest.main()
