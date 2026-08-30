"""FE-P0c Phase C invariants — FE-P0a ingestion_contract Rule 1/2/4 enforcement.

Rule 1 — attempts INSERT + prior is_latest=false toggle SAME transaction,
        SERIALIZABLE OR FOR UPDATE.
Rule 2 — measurement_results.(verdict/result_json/operator/measured_at/condition_hash)
        updated in SAME transaction as the attempt INSERT.
Rule 4 — REFRESH MATERIALIZED VIEW CONCURRENTLY coverage_by_condition_hash issued
        AFTER commit; refresh failure does NOT roll back the fact.

Validated against an in-memory ``FakeConnection`` recording every executed
statement, so the assertions describe the SQL contract — not just the absence
of errors.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402


class FakeCursor:
    def __init__(self, rowcount=1, fail_on=None):
        self.executed = []
        self.closed = False
        self.rowcount = rowcount
        self._fail_on = fail_on

    def execute(self, statement, parameters):
        self.executed.append((statement, parameters))
        if self._fail_on and self._fail_on in statement:
            raise RuntimeError(f'simulated failure on: {statement}')

    def close(self):
        self.closed = True


class FakeConnection:
    """Default fake — non-autocommit connection, single shared cursor instance."""

    def __init__(self):
        self.cursors = []
        self.commits = 0
        self.rolled_back = False
        self.autocommit = False
        self.closed = False

    def cursor(self):
        cursor = FakeCursor()
        self.cursors.append(cursor)
        return cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _attempt_record(**overrides):
    base = {
        'provider_id': 'provider-uuid',
        'session_id': 'central-session-uuid',
        'test_name': 'OBW',
        'technology': 'BT',
        'condition_hash': 'CH-deadbeef',
        'attempt_number': 1,
        'is_latest': True,
        'status': 'completed',
        'result_json': '{"result1":"12.5"}',
        'project_id': 'central-project-uuid',
        'operator': 'op-1',
        'recorded_by': 'op-1',
        'verdict': 'Pass',
        'measured_at': '2026-05-26T00:00:00Z',
    }
    base.update(overrides)
    return base


class TestAttemptTransactionStatements(unittest.TestCase):
    """Rule 1 — INSERT new + DB-authoritative provider/timestamp recency."""

    def test_recompute_uses_provider_timestamp_recency_for_legacy_key_shape(self):
        # The three-field idempotency key remains a compatibility shape, not a
        # permission to use the old providerless MAX(attempt_number) branch.
        # Recency is always database-owned and exact-partitioned.
        from fcc_test_platform.postgres_ingestion_writer import (
            build_postgres_attempt_transaction_statements,
        )

        statements = build_postgres_attempt_transaction_statements(
            _attempt_record(),
            fk_resolution_hint={'provider_result_id': 'r1'},
        )

        recompute_sql, recompute_params = statements[1]
        self.assertIn('UPDATE "measurement_attempts"', recompute_sql)
        self.assertNotIn('MAX("attempt_number")', recompute_sql)
        self.assertNotIn('"attempt_number"', recompute_sql)
        self.assertIn('candidate."provider_id" = %s', recompute_sql)
        self.assertIn('candidate."measured_at" DESC NULLS LAST', recompute_sql)
        self.assertIn('candidate."created_at" DESC', recompute_sql)
        self.assertIn('candidate."id" DESC', recompute_sql)
        self.assertIn('"project_id" IS NOT DISTINCT FROM', recompute_sql)
        self.assertIn('"condition_hash" = ', recompute_sql)
        # partition predicate params appear twice (subquery + outer scope)
        self.assertEqual(
            recompute_params,
            (
                'central-project-uuid', 'provider-uuid', 'CH-deadbeef',
                'central-project-uuid', 'provider-uuid', 'CH-deadbeef',
            ),
        )

    def test_null_project_id_uses_is_not_distinct_from_null(self):
        from fcc_test_platform.postgres_ingestion_writer import (
            build_postgres_attempt_transaction_statements,
        )

        record = _attempt_record()
        record.pop('project_id')
        statements = build_postgres_attempt_transaction_statements(
            record,
            fk_resolution_hint={'provider_result_id': 'r1'},
        )

        _, recompute_params = statements[1]
        self.assertIsNone(recompute_params[0])
        self.assertIsNone(recompute_params[3])

    def test_insert_fk_resolution_uses_provider_result_subquery(self):
        from fcc_test_platform.postgres_ingestion_writer import (
            build_postgres_attempt_transaction_statements,
        )

        statements = build_postgres_attempt_transaction_statements(
            _attempt_record(),
            fk_resolution_hint={'provider_result_id': 'r1'},
        )

        insert_sql, insert_params = statements[0]
        self.assertIn('INSERT INTO "measurement_attempts"', insert_sql)
        self.assertIn('"measurement_result_id"', insert_sql)
        self.assertIn(
            'SELECT "id" FROM "measurement_results" WHERE "provider_id" = %s AND "provider_result_id" = %s',
            insert_sql,
        )
        # Last two parameters are the (provider_id, provider_result_id) for the subquery
        self.assertEqual(insert_params[-2], 'provider-uuid')
        self.assertEqual(insert_params[-1], 'r1')

    def test_insert_uses_on_conflict_composite_key_do_nothing(self):
        # The INSERT is idempotent (DO NOTHING); is_latest correctness is the
        # recompute statement's responsibility, so a replay converges via the
        # MAX-based recompute, not a per-row re-assert.
        from fcc_test_platform.postgres_ingestion_writer import (
            build_postgres_attempt_transaction_statements,
        )

        statements = build_postgres_attempt_transaction_statements(
            _attempt_record(),
            fk_resolution_hint={},
        )

        insert_sql, _ = statements[0]
        self.assertIn(
            'ON CONFLICT ("session_id", "condition_hash", "attempt_number") DO NOTHING',
            insert_sql,
        )

    def test_attempt_without_provider_result_id_skips_fk_subquery(self):
        from fcc_test_platform.postgres_ingestion_writer import (
            build_postgres_attempt_transaction_statements,
        )

        statements = build_postgres_attempt_transaction_statements(
            _attempt_record(),
            fk_resolution_hint={},
        )

        insert_sql, _ = statements[0]
        self.assertNotIn('"measurement_result_id"', insert_sql)


class TestSerializableIsolationAndCommit(unittest.TestCase):
    """Rule 1 — SERIALIZABLE set BEFORE any other statement on the transaction.

    Phase F (2026-05-26) external review fix — SET TRANSACTION ISOLATION LEVEL
    SERIALIZABLE is rejected by PostgreSQL once any statement has executed on
    the transaction. The plan order writes ``measurement_results`` BEFORE
    ``measurement_attempts``, so the worker must call
    ``set_serializable_isolation()`` IMMEDIATELY after ``begin_transaction()``
    when the plan contains an is_latest=true attempt. The transaction itself
    enforces this contract by raising if upsert_attempt is invoked without a
    prior set_serializable_isolation.
    """

    def test_explicit_set_serializable_emits_statement_first(self):
        from fcc_test_platform.postgres_ingestion_writer import PostgresIngestionTransaction

        connection = FakeConnection()
        tx = PostgresIngestionTransaction(connection)
        tx.set_serializable_isolation()
        tx.upsert_attempt(
            _attempt_record(),
            ('central-session-uuid', 'CH-deadbeef', 1),
            fk_resolution_hint={'provider_result_id': 'r1'},
        )

        cursor = connection.cursors[0]
        # First statement: SET TRANSACTION ISOLATION LEVEL SERIALIZABLE
        self.assertEqual(
            cursor.executed[0][0],
            'SET TRANSACTION ISOLATION LEVEL SERIALIZABLE',
        )

    def test_serializable_is_idempotent_within_transaction(self):
        from fcc_test_platform.postgres_ingestion_writer import PostgresIngestionTransaction

        connection = FakeConnection()
        tx = PostgresIngestionTransaction(connection)
        tx.set_serializable_isolation()
        tx.set_serializable_isolation()  # idempotent
        tx.upsert_attempt(_attempt_record(),
                          ('central-session-uuid', 'CH-deadbeef', 1),
                          fk_resolution_hint={'provider_result_id': 'r1'})

        cursor = connection.cursors[0]
        serializable_count = sum(
            1 for stmt, _ in cursor.executed
            if 'SET TRANSACTION ISOLATION LEVEL SERIALIZABLE' in stmt
        )
        self.assertEqual(serializable_count, 1)

    def test_upsert_attempt_without_prior_serializable_raises(self):
        """Phase F contract — caller MUST set SERIALIZABLE before upsert_attempt
        with is_latest=true. Forgetting raises a loud ValueError (would be a
        Postgres error in production after the first results upsert ran).
        """
        from fcc_test_platform.postgres_ingestion_writer import PostgresIngestionTransaction

        connection = FakeConnection()
        tx = PostgresIngestionTransaction(connection)

        with self.assertRaisesRegex(ValueError, 'set_serializable_isolation'):
            tx.upsert_attempt(
                _attempt_record(),
                ('central-session-uuid', 'CH-deadbeef', 1),
                fk_resolution_hint={'provider_result_id': 'r1'},
            )

    def test_non_latest_attempt_uses_generic_upsert(self):
        from fcc_test_platform.postgres_ingestion_writer import PostgresIngestionTransaction

        connection = FakeConnection()
        tx = PostgresIngestionTransaction(connection)
        tx.upsert_attempt(
            _attempt_record(is_latest=False),
            ('central-session-uuid', 'CH-deadbeef', 1),
            fk_resolution_hint={'provider_result_id': 'r1'},
        )

        cursor = connection.cursors[0]
        # No SERIALIZABLE for non-latest (composite UNIQUE alone suffices)
        for stmt, _ in cursor.executed:
            self.assertNotIn('SERIALIZABLE', stmt)
        # Single INSERT on the composite key
        insert_statements = [stmt for stmt, _ in cursor.executed if 'INSERT INTO "measurement_attempts"' in stmt]
        self.assertEqual(len(insert_statements), 1)


class TestResultsProjectionRule2(unittest.TestCase):
    """Rule 2 — measurement_results updated in SAME transaction as attempt INSERT."""

    def test_projection_update_sets_five_canonical_columns(self):
        from fcc_test_platform.postgres_ingestion_writer import (
            build_postgres_results_projection_update,
        )

        statement, parameters = build_postgres_results_projection_update(
            provider_id='provider-uuid',
            provider_result_id='r1',
            verdict='Pass',
            result_json='{"result1":"12.5"}',
            operator='op-1',
            measured_at='2026-05-26T00:00:00Z',
            condition_hash='CH-deadbeef',
        )

        self.assertIn('UPDATE "measurement_results"', statement)
        # FE-P0a contract Rule 2 — these exact 5 columns must be projected
        for column in ('verdict', 'result_json', 'operator', 'measured_at', 'condition_hash'):
            self.assertIn(f'"{column}"', statement)
        self.assertIn('WHERE "provider_id" = %s AND "provider_result_id" = %s', statement)
        self.assertEqual(parameters[-2:], ('provider-uuid', 'r1'))

    def test_projection_runs_inside_same_cursor_as_attempt(self):
        """SAME transaction = same cursor = same connection commit boundary."""
        from fcc_test_platform.postgres_ingestion_writer import PostgresIngestionTransaction

        connection = FakeConnection()
        tx = PostgresIngestionTransaction(connection)
        tx.set_serializable_isolation()
        tx.upsert_attempt(
            _attempt_record(),
            ('central-session-uuid', 'CH-deadbeef', 1),
            fk_resolution_hint={'provider_result_id': 'r1'},
        )
        tx.project_results_from_latest_attempt(
            provider_id='provider-uuid',
            provider_result_id='r1',
            verdict='Pass',
            result_json='{"result1":"12.5"}',
            operator='op-1',
            measured_at='2026-05-26T00:00:00Z',
            condition_hash='CH-deadbeef',
        )

        # Both attempt INSERT and results projection UPDATE land on cursor[0]
        cursor = connection.cursors[0]
        statement_kinds = [stmt.split()[0] for stmt, _ in cursor.executed]
        self.assertIn('UPDATE', statement_kinds)
        self.assertIn('INSERT', statement_kinds)

        # 0 commits so far — Rule 2 demands SAME transaction
        self.assertEqual(connection.commits, 0)


class TestCoverageRefreshRule4(unittest.TestCase):
    """Rule 4 — REFRESH MATERIALIZED VIEW CONCURRENTLY on a SEPARATE autocommit
    connection.

    Phase F (2026-05-26) external review fix — PostgreSQL disallows
    REFRESH CONCURRENTLY inside a transaction block. The previous post-commit
    hook inside ``PostgresIngestionTransaction.commit`` issued the REFRESH on
    the same connection where psycopg's default cursor.execute starts an
    implicit transaction — silently rejected. The fix: a dedicated
    ``PostgresIngestionWriter.refresh_coverage_materialized_view()`` method
    that opens a new connection, sets ``connection.autocommit = True``, runs
    REFRESH, and closes.
    """

    def test_commit_does_NOT_issue_refresh_on_same_connection(self):
        """Phase F invariant — refresh has been EXTRACTED from commit()."""
        from fcc_test_platform.postgres_ingestion_writer import PostgresIngestionTransaction

        connection = FakeConnection()
        tx = PostgresIngestionTransaction(connection)
        tx.set_serializable_isolation()
        tx.upsert_attempt(
            _attempt_record(),
            ('central-session-uuid', 'CH-deadbeef', 1),
            fk_resolution_hint={'provider_result_id': 'r1'},
        )
        tx.commit()

        # No REFRESH statement issued on the main connection — that would have
        # been inside the (now-committed) transaction block from psycopg's POV.
        for cursor in connection.cursors:
            for stmt, _ in cursor.executed:
                self.assertNotIn('REFRESH MATERIALIZED VIEW', stmt)
        # attempt_was_written flag exposed to the caller (worker) so it can
        # decide whether to invoke writer.refresh_coverage_materialized_view().
        self.assertTrue(tx.attempt_was_written)

    def test_writer_refresh_opens_new_autocommit_connection(self):
        """Phase F invariant — refresh runs on a NEW connection with
        ``connection.autocommit = True`` so the REFRESH CONCURRENTLY statement
        is at the top-level (no implicit transaction block).
        """
        from fcc_test_platform.postgres_ingestion_writer import (
            COVERAGE_REFRESH_STATEMENT,
            PostgresIngestionWriter,
        )

        connections: list[FakeConnection] = []

        def _factory():
            connections.append(FakeConnection())
            return connections[-1]

        writer = PostgresIngestionWriter(_factory)
        writer.refresh_coverage_materialized_view()

        self.assertEqual(len(connections), 1)
        refresh_connection = connections[0]
        # autocommit toggled BEFORE execute (Phase F constraint)
        self.assertTrue(refresh_connection.autocommit)
        # Exactly one cursor, executing the REFRESH statement
        self.assertEqual(len(refresh_connection.cursors), 1)
        executed = [stmt for stmt, _ in refresh_connection.cursors[0].executed]
        self.assertEqual(executed, [COVERAGE_REFRESH_STATEMENT])
        # Connection closed after refresh
        self.assertTrue(refresh_connection.closed)

    def test_refresh_failure_is_loud_at_writer_caller_swallows(self):
        """Phase F — writer.refresh_coverage_materialized_view raises on failure;
        the worker (caller) swallows so the durable fact (already committed
        elsewhere) is not lost. This guards the boundary contract.
        """
        from fcc_test_platform.postgres_ingestion_writer import PostgresIngestionWriter

        class RefreshFailingConnection(FakeConnection):
            def cursor(self):
                cursor = FakeCursor(fail_on='REFRESH MATERIALIZED VIEW')
                self.cursors.append(cursor)
                return cursor

        writer = PostgresIngestionWriter(lambda: RefreshFailingConnection())
        with self.assertRaisesRegex(RuntimeError, 'REFRESH MATERIALIZED VIEW'):
            writer.refresh_coverage_materialized_view()


class TestPhaseCInvariantImports(unittest.TestCase):
    def test_writer_module_imports_remain_dependency_free(self):
        """Phase C extension must not introduce psycopg/asyncpg/etc."""
        import ast
        path = resolve_repo_artifact(__file__, 'src/application/headless/platform_postgres_ingestion_writer.py')
        tree = ast.parse(path.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        for forbidden in ('psycopg', 'psycopg2', 'asyncpg', 'sqlalchemy', 'sqlite3', 'os'):
            for imported in imports:
                self.assertFalse(
                    imported.startswith(forbidden),
                    f'forbidden import: {imported}',
                )


if __name__ == '__main__':
    unittest.main()
