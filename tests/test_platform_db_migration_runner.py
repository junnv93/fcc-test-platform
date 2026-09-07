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
from fcc_test_platform.db_migration_runner_cli import (
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

        with patch('fcc_test_platform.db_migration_runner_cli._connect', return_value=connection):
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

        with patch('fcc_test_platform.db_migration_runner_cli._connect', return_value=connection):
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
            with patch('fcc_test_platform.db_migration_runner_cli._connect', side_effect=RuntimeError('driver missing')):
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


# ── 생성물은 원장의 드리프트 축에 속하지 않는다 (2026-09-07) ────────────────────
class TestTheGeneratedBootstrapLeavesTheDriftAxis(unittest.TestCase):
    """⚠️ 이것은 «완화»가 아니라 **검사를 구별 가능한 자리로 옮긴 것**이다.

    실측 2026-09-07, 중앙 PC — `central-migrate` 가 exit 3(drift)으로 죽어
    `platform-api`·`platform-api-node`·`web` 셋이 안 떴다. detail 은 이랬다:

        001_initial_central_db: file checksum bbcd0f2861c9… != ledger a1ed7a06b17a…
        (already-applied migration was edited — refusing to re-apply)

    ⚠️ **아무도 안 고쳤다.** exporter 가 스키마 SSOT 에서 재생성한 것이다. 오류 메시지가
    자기 범주 오류를 드러낸다.

    ■ 왜 그 비교에 «참 양성이 없었나»

      · `001` 은 생성물이라 스키마가 바뀔 때마다 체크섬이 바뀐다.
      · 기존 DB 에서 `001` 은 **다시 실행되지 않는다** — 체크섬만 비교된다.
      · 그 체크섬은 「재생성됐다」와 「사람이 고쳤다」를 **원리적으로 못 가른다.**

    남는 효과는 스키마가 정당하게 바뀔 때마다 배포가 멈추는 것뿐이었고, 그 멈춤의
    처방(`reconcile`)은 스스로를 *"the single sanctioned exception to the append-only
    ledger"* 라 부른다. **유일한 불변식에 승인된 예외가 있다는 것이 냄새였다.**
    """

    def _bootstrap(self) -> Path:
        from fcc_test_platform.db_migrate_cli import discover_migrations, is_generated_artifact
        root = Path(__file__).resolve().parents[1] / 'migrations'
        generated = [(v, p) for v, p in discover_migrations(root) if is_generated_artifact(p)]
        self.assertEqual(
            len(generated), 1,
            f'생성물 마이그레이션이 정확히 하나여야 한다 — {[v for v, _ in generated]}. '
            '둘 이상이면 아래 검사들이 어느 것을 말하는지 모호해지고, 0이면 공허하다.',
        )
        return generated[0]

    def test_exactly_one_migration_declares_itself_generated(self):
        """파생이 비거나 넓으면 아래가 공허하거나 과발화한다."""
        version, _ = self._bootstrap()
        self.assertTrue(version.startswith('001'), f'부트스트랩이 001 이 아니다: {version}')

    def test_a_regenerated_bootstrap_does_not_stop_the_deployment(self):
        """이 커밋이 사는 결함. 재생성된 부트스트랩은 드리프트가 아니다."""
        from fcc_test_platform.db_migrate_cli import plan_migrations
        version, path = self._bootstrap()

        to_apply, drift = plan_migrations([(version, path)], {version: 'a1ed7a06b17a' + '0' * 52})

        self.assertEqual(
            drift, [],
            '재생성된 부트스트랩이 여전히 배포를 멈춘다 — 그 비교에는 구별력이 없다.',
        )
        self.assertEqual(to_apply, [], '이미 적용된 것을 다시 적용하려 든다')

    def test_an_edited_incremental_still_stops_the_deployment(self):
        """⚠️ 이빨. 완화가 **증분까지** 번지면 append-only 가 사라진다."""
        from fcc_test_platform.db_migrate_cli import discover_migrations, is_generated_artifact, plan_migrations
        root = Path(__file__).resolve().parents[1] / 'migrations'
        incrementals = [(v, p) for v, p in discover_migrations(root) if not is_generated_artifact(p)]
        self.assertTrue(incrementals, '증분 마이그레이션이 하나도 없다 — 이 검사가 공허하다')
        version, path = incrementals[0]

        _to_apply, drift = plan_migrations([(version, path)], {version: 'deadbeef' + '0' * 56})

        self.assertTrue(
            drift,
            f'{version} 를 고쳤는데 드리프트가 안 났다 — append-only 축이 꺼졌다.',
        )

    def test_the_check_that_replaced_it_still_exists(self):
        """⚠️ **이 완화의 전제를 산다.**

        「사람이 `001` 을 고쳤나」는 버려진 것이 아니라 **바이트 동등을 요구하는 자리로
        옮겨 갔다**(스키마 JSON → exporter → 커밋된 파일). 그 게이트가 사라지면 이
        완화가 조용한 구멍이 된다. 그래서 여기서 그 실재를 단언한다 — 전제를 지우는
        사람이 **이 줄에서** 막히도록.
        """
        import fcc_test_platform.export_central_db_ddl_cli as exporter
        _version, path = self._bootstrap()

        rendered = exporter.render_ddl(exporter.load_schema(
            Path(__file__).resolve().parents[1] / 'docs' / 'platform'
            / 'central_db_schema.v1.json'))
        self.assertEqual(
            path.read_text(encoding='utf-8'), rendered,
            '커밋된 부트스트랩이 exporter 출력과 다르다 — 이것이 이제 「누가 001 을 '
            '고쳤나」를 답하는 유일한 자리다. `python3 scripts/'
            'export_platform_central_db_ddl.py --write` 로 재생성하라.',
        )


if __name__ == '__main__':
    unittest.main()
