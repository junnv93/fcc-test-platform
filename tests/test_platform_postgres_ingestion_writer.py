import ast
import unittest
from pathlib import Path

from fcc_test_platform.provider_ingestion_worker import execute_platform_ingestion_plan
from fcc_test_platform.postgres_ingestion_writer import (
    PostgresIngestionWriter,
    PostgresIngestionTransaction,
    build_postgres_upsert,
)

from tests._moved_module_source import moved_module_source
from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
#: ⚠️ **경로도 로거 이름도 모듈에게 묻는다** (2026-09-03).
#: 추출(2026-08-30)이 이 모듈을 `fcc_test_platform/postgres_ingestion_writer.py` 로
#: 옮겼는데 이 파일은 모노레포 시절의 경로와 **로거 이름**을 들고 있었다.
#: 코드는 `logging.getLogger(__name__)` 을 쓰므로 로거 이름은 모듈의 dotted name 이다 —
#: 그것을 여기 리터럴로 적으면 이관 때마다 두 번째 SSOT 가 낡는다.
_WRITER_MODULE = 'fcc_test_platform.postgres_ingestion_writer'
MODULE_PATH = moved_module_source(_WRITER_MODULE)
#: 로거 이름 = 모듈의 dotted name. 파생이지 리터럴이 아니다.
WRITER_LOGGER = _WRITER_MODULE


class FakeCursor:
    def __init__(self):
        self.executed = []
        self.closed = False
        self.rowcount = 1

    def execute(self, statement, parameters):
        self.executed.append((statement, parameters))

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _step_payload() -> dict:
    return {
        'steps': [{
            'order': 1,
            'target_table': 'measurement_results',
            'operation': 'upsert',
            'idempotency_key': ['provider-uuid', 'result-1'],
            'record': {
                'provider_id': 'provider-uuid',
                'session_id': 'session-uuid',
                'provider_result_id': 'result-1',
                'test_name': 'OBW',
                'technology': 'DTS',
                'condition_json': '{}',
                'result_json': '{}',
            },
        }],
    }


class TestPlatformPostgresIngestionWriter(unittest.TestCase):
    def test_builds_parameterized_postgres_upsert(self):
        statement, parameters = build_postgres_upsert(
            'artifacts',
            {
                'provider_id': 'provider-uuid',
                'session_id': 'session-uuid',
                'relative_path': 'sessions/s/plot.png',
                'artifact_type': 'plot_png',
                'storage_backend': 'filesystem',
            },
            ('provider-uuid', 'sessions/s/plot.png'),
        )

        self.assertIn('INSERT INTO "artifacts"', statement)
        self.assertIn('ON CONFLICT ("provider_id", "relative_path") DO UPDATE SET', statement)
        self.assertNotIn('provider-uuid', statement)
        self.assertIn('%s', statement)
        self.assertIn('provider-uuid', parameters)

    def test_rejects_unknown_table_and_unsafe_identifier(self):
        with self.assertRaisesRegex(ValueError, 'unsupported ingestion table'):
            build_postgres_upsert('provider_private', {'id': '1'}, ('1',))

        with self.assertRaisesRegex(ValueError, 'unsafe SQL identifier'):
            build_postgres_upsert('artifacts;drop', {'id': '1'}, ('1',))

    def test_rejects_idempotency_key_mismatch(self):
        with self.assertRaisesRegex(ValueError, 'idempotency_key'):
            build_postgres_upsert(
                'measurement_results',
                {
                    'provider_id': 'provider-uuid',
                    'provider_result_id': 'result-1',
                    'session_id': 'session-uuid',
                },
                ('provider-uuid', 'wrong-result'),
            )

    def test_reference_snapshot_conflict_is_fill_only_and_byte_guarded(self):
        record = {
            'id': 'session-1',
            'provider_id': 'provider-uuid',
            'chamber_id': 'chamber-1',
            'provider_session_id': 'provider-session-1',
            'project_id': 'project-1',
            'status': 'active',
            'project_result_reference_snapshot_json': '{"project_id":"project-1"}',
            'project_result_reference_snapshot_schema_version': 'fcc.project-result-reference-session.v1',
        }

        statement, _ = build_postgres_upsert(
            'test_sessions', record,
            ('provider-uuid', 'chamber-1', 'provider-session-1'),
        )

        self.assertIn(
            'COALESCE("test_sessions"."project_result_reference_snapshot_json", '
            'EXCLUDED."project_result_reference_snapshot_json")',
            statement,
        )
        self.assertIn(
            '"test_sessions"."project_result_reference_snapshot_json" IS NULL',
            statement,
        )
        self.assertIn(
            '"test_sessions"."project_result_reference_snapshot_schema_version" IS NULL',
            statement,
        )
        self.assertIn(
            '"test_sessions"."project_result_reference_snapshot_json" = '
            'EXCLUDED."project_result_reference_snapshot_json"',
            statement,
        )
        self.assertIn(
            'ON CONFLICT ("provider_id", "chamber_id", "provider_session_id") '
            'DO UPDATE SET',
            statement,
        )
        self.assertNotIn('ON CONFLICT DO UPDATE SET', statement)

    def test_existing_session_parent_snapshot_fill_commits_through_shared_upsert(self):
        connection = FakeConnection()
        transaction = PostgresIngestionTransaction(connection)
        record = {
            'id': 'seeded-session-id',
            'provider_id': 'provider-uuid',
            'chamber_id': 'seeded-chamber',
            'provider_session_id': 'seeded-provider-session',
            'project_id': 'project-1',
            'status': 'completed',
            'project_result_reference_snapshot_json': '{"project_id":"project-1"}',
            'project_result_reference_snapshot_schema_version': 'fcc.project-result-reference-session.v1',
        }

        affected = transaction.upsert(
            'test_sessions', record,
            ('provider-uuid', 'seeded-chamber', 'seeded-provider-session'),
        )
        transaction.commit()

        self.assertEqual(affected, 1)
        self.assertTrue(connection.committed)
        statement, _parameters = connection.cursor_instance.executed[0]
        self.assertIn(
            'ON CONFLICT ("provider_id", "chamber_id", "provider_session_id") '
            'DO UPDATE SET',
            statement,
        )
        self.assertIn(
            '"project_result_reference_snapshot_json" = COALESCE(',
            statement,
        )

    def test_conflicting_complete_snapshot_replay_is_observable(self):
        connection = FakeConnection()
        connection.cursor_instance.rowcount = 0
        transaction = PostgresIngestionTransaction(connection)
        record = {
            'id': 'session-1',
            'provider_id': 'provider-uuid',
            'chamber_id': 'chamber-1',
            'provider_session_id': 'provider-session-1',
            'project_id': 'project-1',
            'status': 'active',
            'project_result_reference_snapshot_json': '{"project_id":"other"}',
            'project_result_reference_snapshot_schema_version': 'fcc.project-result-reference-session.v1',
        }

        with self.assertLogs(
            WRITER_LOGGER,
            level='WARNING',
        ) as logs:
            affected = transaction.upsert(
                'test_sessions', record,
                ('provider-uuid', 'chamber-1', 'provider-session-1'),
            )

        self.assertEqual(affected, 0)
        self.assertIn('first complete snapshot retained', '\n'.join(logs.output))
        transaction.commit()
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)

    def test_writer_integrates_with_ingestion_worker(self):
        connection = FakeConnection()
        writer = PostgresIngestionWriter(lambda: connection)

        result = execute_platform_ingestion_plan(_step_payload(), writer)

        self.assertTrue(result.committed)
        self.assertTrue(connection.committed)
        self.assertTrue(connection.cursor_instance.closed)
        self.assertEqual(len(connection.cursor_instance.executed), 1)

    def test_module_import_boundary_excludes_driver_and_provider_runtime_dependencies(self):
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
            'psycopg2',
            'asyncpg',
            'os',
        )
        self.assertFalse([
            module for module in imports
            if module == 'infrastructure' or module.startswith(forbidden_prefixes)
        ])


if __name__ == '__main__':
    unittest.main()
