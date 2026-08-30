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
from platform_db_migration_runner import (
    advisory_lock_id,
    apply_migration_and_collect,
    main,
)
from test_platform_db_migration_collect import _column_rows, _index_rows


SCHEMA_PATH = project_root / 'docs' / 'platform' / 'central_db_schema.v1.json'


class TestPlatformDbMigrationRunner(unittest.TestCase):
    def test_apply_uses_advisory_lock_transaction_and_collects_valid_manifest(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        connection = FakeConnection(schema)

        with patch('platform_db_migration_runner._connect', return_value=connection):
            manifest = apply_migration_and_collect(
                dsn='postgresql://platform',
                schema=schema,
                schema_contract_bytes=SCHEMA_PATH.read_bytes(),
                migration_sql='CREATE TABLE IF NOT EXISTS example(id uuid);',
                db_schema_name='public',
                migration_id='001_initial_central_db',
                database_name='',
                applied_at='2026-05-15T14:00:00+09:00',
                applied_by='platform-ci',
                lock_key='test-lock',
            )

        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertTrue(connection.closed)
        self.assertEqual(connection.statements[0][0], 'SELECT pg_advisory_xact_lock(%s)')
        self.assertEqual(connection.statements[0][1], (advisory_lock_id('test-lock'),))
        self.assertEqual(connection.statements[1][0], 'CREATE TABLE IF NOT EXISTS example(id uuid);')
        self.assertEqual(manifest['database_name'], 'fcc_platform')
        self.assertEqual(central_db_migration_evidence_errors(manifest, schema), [])

    def test_apply_rolls_back_on_failure(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        connection = FakeConnection(schema, fail_on='BROKEN SQL')

        with patch('platform_db_migration_runner._connect', return_value=connection):
            with self.assertRaises(RuntimeError):
                apply_migration_and_collect(
                    dsn='postgresql://platform',
                    schema=schema,
                    schema_contract_bytes=SCHEMA_PATH.read_bytes(),
                    migration_sql='BROKEN SQL',
                    db_schema_name='public',
                    migration_id='001_initial_central_db',
                    database_name='fcc_platform',
                    applied_at='2026-05-15T14:00:00+09:00',
                    applied_by='platform-ci',
                    lock_key='test-lock',
                )

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertTrue(connection.closed)

    def test_cli_dependency_error_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'nested' / 'cutover' / 'db_migration.json'
            stderr = StringIO()
            with patch('platform_db_migration_runner._connect', side_effect=RuntimeError('driver missing')):
                with redirect_stderr(stderr):
                    exit_code = main([
                        '--dsn', 'postgresql://platform',
                        '--output', str(output),
                        '--applied-by', 'platform-ci',
                    ])
            self.assertTrue(output.parent.is_dir())

        self.assertEqual(exit_code, 2)
        payload = json.loads(stderr.getvalue())
        self.assertFalse(payload['applied'])
        self.assertFalse(payload['collected'])


class FakeConnection:
    def __init__(self, schema: dict, fail_on: str = ''):
        self.schema = schema
        self.fail_on = fail_on
        self.statements = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakeCursor:
    def __init__(self, connection: FakeConnection):
        self.connection = connection
        self.description = []
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement, params=None):
        self.connection.statements.append((statement, params))
        if self.connection.fail_on and self.connection.fail_on in statement:
            raise RuntimeError('migration failed')
        if 'current_database()' in statement:
            self.description = [('current_database',)]
            self._rows = [('fcc_platform',)]
        elif 'information_schema.columns' in statement:
            self.description = [('table_name',), ('column_name',), ('data_type',), ('udt_name',), ('is_nullable',)]
            self._rows = [
                (
                    row['table_name'],
                    row['column_name'],
                    row['data_type'],
                    row['udt_name'],
                    row['is_nullable'],
                )
                for row in _column_rows(self.connection.schema)
            ]
        elif 'pg_indexes' in statement:
            self.description = [('table_name',), ('index_name',), ('index_definition',)]
            self._rows = [
                (row['table_name'], row['index_name'], row['index_definition'])
                for row in _index_rows(self.connection.schema)
            ]

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return self._rows


if __name__ == '__main__':
    unittest.main()
