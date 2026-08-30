import ast
import json
import unittest
from pathlib import Path

from fcc_test_platform.db_migration_evidence import (
    central_db_migration_evidence_errors,
    is_central_db_migration_evidence_valid,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_db_migration_evidence.py')
SCHEMA_PATH = project_root / 'docs' / 'platform' / 'central_db_schema.v1.json'
EVIDENCE_SCHEMA_PATH = project_root / 'docs' / 'platform' / 'central_db_migration_evidence.schema.v1.json'


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))


def _valid_manifest(schema: dict) -> dict:
    tables = {}
    for table_name, table in schema['tables'].items():
        tables[table_name] = {
            'columns': [
                {
                    'name': column_name,
                    'type': _evidence_type(column['type']),
                    'nullable': not bool(column.get('required')),
                }
                for column_name, column in table['columns'].items()
            ],
            'indexes': [
                {
                    'name': index['name'],
                    # 표현식 인덱스(trigram GIN)는 columns 대신 expressions 를
                    # 갖는다 — 스키마 SSOT 가 PostgreSQL 의 캐노니컬 표기를 그대로
                    # 담고 있어 introspection 결과와 byte-identical 하다.
                    'columns': list(index.get('columns') or index.get('expressions') or ()),
                    'unique': bool(index.get('unique')),
                    'orders': dict(index.get('orders') or {}),
                    'where': index.get('where'),
                }
                for index in table.get('indexes', [])
            ],
        }
    return {
        'schema_version': 1,
        'migration_id': '001_initial_central_db',
        'database_engine': 'postgresql',
        'database_name': 'fcc_platform',
        'migration_status': 'applied',
        'applied_at': '2026-05-15T07:00:00+09:00',
        'applied_by': 'platform-ci',
        'schema_contract_sha256': 'a' * 64,
        'ddl_sha256': 'b' * 64,
        'tables': tables,
    }


class TestPlatformDbMigrationEvidence(unittest.TestCase):
    def test_valid_migration_evidence_passes(self):
        schema = _schema()
        manifest = _valid_manifest(schema)

        self.assertTrue(is_central_db_migration_evidence_valid(manifest, schema))
        self.assertEqual(central_db_migration_evidence_errors(manifest, schema), [])

    def test_rejects_wrong_engine_or_unapplied_migration(self):
        schema = _schema()
        manifest = _valid_manifest(schema)
        manifest['database_engine'] = 'sqlite'
        manifest['migration_status'] = 'pending'

        issues = central_db_migration_evidence_errors(manifest, schema)
        codes = {issue.code for issue in issues}

        self.assertIn('invalid_database_engine', codes)
        self.assertIn('migration_not_applied', codes)

    def test_rejects_missing_table_column_and_index(self):
        schema = _schema()
        manifest = _valid_manifest(schema)
        del manifest['tables']['providers']
        manifest['tables']['measurement_results']['columns'] = [
            column
            for column in manifest['tables']['measurement_results']['columns']
            if column['name'] != 'result_json'
        ]
        manifest['tables']['artifacts']['indexes'] = []

        issues = central_db_migration_evidence_errors(manifest, schema)
        codes = {issue.code for issue in issues}

        self.assertIn('missing_table', codes)
        self.assertIn('missing_column', codes)
        self.assertIn('missing_index', codes)

    def test_rejects_nullable_required_column_and_index_mismatch(self):
        schema = _schema()
        manifest = _valid_manifest(schema)
        for column in manifest['tables']['providers']['columns']:
            if column['name'] == 'provider_id':
                column['nullable'] = True
        for index in manifest['tables']['providers']['indexes']:
            if index['name'] == 'ux_providers_provider_id':
                index['unique'] = False

        issues = central_db_migration_evidence_errors(manifest, schema)
        codes = {issue.code for issue in issues}

        self.assertIn('required_column_nullable', codes)
        self.assertIn('index_unique_mismatch', codes)

    def test_rejects_extra_table_and_bad_hash(self):
        schema = _schema()
        manifest = _valid_manifest(schema)
        manifest['schema_contract_sha256'] = 'g' * 64
        manifest['tables']['provider_private_obw'] = {'columns': [], 'indexes': []}

        issues = central_db_migration_evidence_errors(manifest, schema)
        codes = {issue.code for issue in issues}

        self.assertIn('invalid_sha256', codes)
        self.assertIn('unknown_table', codes)

    def test_rejects_wrong_index_order_and_partial_predicate(self):
        schema = _schema()
        manifest = _valid_manifest(schema)
        target = next(
            index for index in manifest['tables']['measurement_attempts']['indexes']
            if index['name'] == 'idx_measurement_attempts_project_provider_condition_recency'
        )
        target['orders'] = {'measured_at': 'ASC', 'created_at': 'DESC', 'id': 'DESC'}
        target['where'] = "status = 'pending'"
        issues = central_db_migration_evidence_errors(manifest, schema)
        codes = {issue.code for issue in issues}
        self.assertIn('index_orders_mismatch', codes)
        self.assertIn('index_predicate_mismatch', codes)

    def test_evidence_schema_document_declares_contract(self):
        evidence_schema = json.loads(EVIDENCE_SCHEMA_PATH.read_text(encoding='utf-8'))

        self.assertEqual(evidence_schema['owner_repository'], 'fcc-test-platform')
        self.assertEqual(evidence_schema['expected_values']['migration_id'], '001_initial_central_db')
        self.assertIn('tables', evidence_schema['required_top_level'])
        self.assertIn('database_connection', evidence_schema['forbidden_operations'])

    def test_module_import_boundary_excludes_db_and_provider_runtime_dependencies(self):
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
            'psycopg',
            'subprocess',
            'pathlib',
        )
        self.assertFalse([
            module for module in imports
            if module == 'infrastructure' or module.startswith(forbidden_prefixes)
        ])


def _evidence_type(schema_type: str) -> str:
    return {
        'uuid': 'uuid',
        'text': 'text',
        'json': 'jsonb',
        'boolean': 'boolean',
        'timestamp': 'timestamp with time zone',
        'integer': 'integer',
        'numeric': 'numeric',
    }[schema_type]


if __name__ == '__main__':
    unittest.main()
