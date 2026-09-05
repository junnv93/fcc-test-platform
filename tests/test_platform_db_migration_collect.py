import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch


project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'scripts'))
sys.path.insert(0, str(project_root / 'src'))

from fcc_test_platform.db_migration_evidence import central_db_migration_evidence_errors
from fcc_test_platform.db_migration_collect_cli import (
    _index_orders,
    _index_predicate,
    _index_columns,
    _index_metadata,
    _index_unique,
    build_manifest_from_introspection,
    main,
)


SCHEMA_PATH = project_root / 'docs' / 'platform' / 'central_db_schema.v1.json'
class TestPlatformDbMigrationCollect(unittest.TestCase):
    def test_build_manifest_from_introspection_matches_validator(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        manifest = build_manifest_from_introspection(
            schema=schema,
            schema_contract_bytes=SCHEMA_PATH.read_bytes(),
            database_name='fcc_platform',
            migration_id='001_initial_central_db',
            migration_status='applied',
            applied_at='2026-05-15T16:20:00+09:00',
            applied_by='platform-ci',
            columns=_column_rows(schema),
            indexes=_index_rows(schema),
        )

        self.assertRegex(manifest['schema_contract_sha256'], r'^[0-9a-f]{64}$')
        self.assertRegex(manifest['ddl_sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(central_db_migration_evidence_errors(manifest, schema), [])

    def test_index_definition_parser_preserves_columns_and_unique_flag(self):
        definition = 'CREATE UNIQUE INDEX ux_example ON public.example USING btree ("provider_id", "relative_path")'

        self.assertTrue(_index_unique(definition))
        self.assertEqual(_index_columns(definition), ['provider_id', 'relative_path'])

    def test_partial_index_where_predicate_is_not_parsed_as_columns(self):
        # Regression: live PostgreSQL renders partial-index DDL with a trailing
        # WHERE clause. The naive "last parenthesis group" parser captured the
        # predicate ("idempotency_key IS NOT NULL") instead of the key column.
        definition = (
            'CREATE UNIQUE INDEX ux_measurement_attempts_idempotency_key '
            'ON public.measurement_attempts USING btree (idempotency_key) '
            'WHERE (idempotency_key IS NOT NULL)'
        )

        self.assertTrue(_index_unique(definition))
        self.assertEqual(_index_columns(definition), ['idempotency_key'])

    def test_postgresql_recency_index_order_modifiers_do_not_change_ssot_columns(self):
        # PostgreSQL includes DESC/NULLS LAST in pg_indexes.indexdef, while the
        # schema SSOT stores those modifiers in its separate ``orders`` map.
        definition = (
            'CREATE INDEX idx_measurement_attempts_project_provider_condition_recency '
            'ON public.measurement_attempts USING btree '
            '(project_id, provider_id, condition_hash, measured_at DESC NULLS LAST, '
            'created_at DESC, id DESC) WHERE (status = \'completed\'::text)'
        )

        self.assertEqual(
            _index_columns(definition),
            ['project_id', 'provider_id', 'condition_hash', 'measured_at', 'created_at', 'id'],
        )

    def test_expression_index_keeps_nested_parentheses(self):
        definition = 'CREATE INDEX idx_example ON public.example USING btree (lower(name), provider_id)'

        self.assertEqual(_index_columns(definition), ['lower(name)', 'provider_id'])

    def test_ordered_index_parser_separates_column_identity_from_ordering(self):
        definition = (
            'CREATE INDEX idx_recency ON public.example USING btree '
            '(project_id, measured_at DESC NULLS LAST, created_at DESC, id DESC) '
            "WHERE status = 'completed'"
        )

        self.assertEqual(
            _index_columns(definition),
            ['project_id', 'measured_at', 'created_at', 'id'],
        )
        self.assertEqual(
            _index_orders(definition, _index_columns(definition)),
            {
                'measured_at': 'DESC NULLS LAST',
                'created_at': 'DESC',
                'id': 'DESC',
            },
        )
        self.assertEqual(_index_predicate(definition), "status = 'completed'")
        self.assertEqual(
            _index_metadata(definition),
            {
                'orders': {
                    'measured_at': 'DESC NULLS LAST',
                    'created_at': 'DESC',
                    'id': 'DESC',
                },
                'where': "status = 'completed'",
            },
        )

    def test_hostile_parentheses_and_casts_normalize_without_reordering_predicate_terms(self):
        definition = (
            'CREATE UNIQUE INDEX ux_users_email_lower ON public.users USING btree '
            '(lower(email)) WHERE ((issuer = \'urn:fcc:identity:local\'::text) '
            'AND (email IS NOT NULL) AND (email <> \'\'::text))'
        )

        self.assertEqual(_index_columns(definition), ['lower(email)'])
        self.assertEqual(
            _index_predicate(definition),
            "issuer = 'urn:fcc:identity:local' AND email IS NOT NULL AND email <> ''",
        )

    def test_hostile_order_and_null_shapes_are_preserved_as_separate_metadata(self):
        definition = (
            'CREATE INDEX idx_order_shapes ON public.example USING btree '
            '(first ASC NULLS FIRST, second DESC NULLS LAST, third NULLS FIRST, fourth DESC) '
            "WHERE ((state = 'READY'::text) AND (deleted_at IS NULL))"
        )
        columns = _index_columns(definition)

        self.assertEqual(columns, ['first', 'second', 'third', 'fourth'])
        self.assertEqual(_index_orders(definition, columns), {
            'first': 'ASC NULLS FIRST',
            'second': 'DESC NULLS LAST',
            'third': 'ASC NULLS FIRST',
            'fourth': 'DESC',
        })
        self.assertEqual(_index_predicate(definition), "state = 'READY' AND deleted_at IS NULL")

    def test_hostile_quoted_comma_and_parentheses_do_not_escape_index_key_group(self):
        definition = (
            "CREATE INDEX idx_expression ON public.example USING btree "
            "(lower(name), coalesce(label, 'a,b')) WHERE ((label IS NULL))"
        )

        self.assertEqual(
            _index_columns(definition),
            ['lower(name)', "coalesce(label, 'a,b')"],
        )
        self.assertEqual(_index_predicate(definition), 'label IS NULL')

    def test_cli_dependency_error_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'migration.json'
            stderr = StringIO()
            with patch('fcc_test_platform.db_migration_collect_cli._connect', side_effect=RuntimeError('driver missing')):
                with redirect_stderr(stderr):
                    exit_code = main([
                        '--dsn',
                        'postgresql://example',
                        '--output',
                        str(output),
                        '--applied-by',
                        'platform-ci',
                    ])

        self.assertEqual(exit_code, 2)
        self.assertFalse(json.loads(stderr.getvalue())['collected'])


def _column_rows(schema: dict) -> list[dict]:
    rows = []
    for table_name, table in schema['tables'].items():
        for column_name, column in table['columns'].items():
            rows.append({
                'table_name': table_name,
                'column_name': column_name,
                'data_type': _data_type(column['type']),
                'udt_name': _udt_name(column['type']),
                'is_nullable': 'NO' if column.get('required') else 'YES',
            })
    return rows


def _index_rows(schema: dict) -> list[dict]:
    rows = []
    for table_name, table in schema['tables'].items():
        for index in table.get('indexes', []):
            unique = 'UNIQUE ' if index.get('unique') else ''
            using = index.get('using') or 'btree'
            if index.get('expressions'):
                # PostgreSQL renders an expression key unquoted (e.g.
                # `lower(customer) gin_trgm_ops`); quoting it here would make this
                # fixture disagree with real pg_indexes output.
                column_values = list(index['expressions'])
            else:
                column_values = [f'"{column}"' for column in index['columns']]
            order_by_name = index.get('orders') or {}
            rendered_columns = []
            for column in column_values:
                bare_column = column.strip('"')
                suffix = order_by_name.get(bare_column)
                rendered_columns.append(f'{column}{(" " + suffix) if suffix else ""}')
            definition = (
                f'CREATE {unique}INDEX {index["name"]} ON public.{table_name} '
                f'USING {using} ({", ".join(rendered_columns)})'
            )
            if index.get('where'):
                definition += f' WHERE ({index["where"]})'
            rows.append({
                'table_name': table_name,
                'index_name': index['name'],
                'index_definition': definition,
            })
    return rows


def _data_type(schema_type: str) -> str:
    return {
        'uuid': 'uuid',
        'text': 'text',
        'json': 'jsonb',
        'boolean': 'boolean',
        'timestamp': 'timestamp with time zone',
        'integer': 'integer',
        'numeric': 'numeric',
    }[schema_type]


def _udt_name(schema_type: str) -> str:
    return {
        'uuid': 'uuid',
        'json': 'jsonb',
    }.get(schema_type, _data_type(schema_type))


if __name__ == '__main__':
    unittest.main()
