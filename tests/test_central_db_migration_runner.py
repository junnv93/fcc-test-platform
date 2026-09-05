"""Unit seal for the incremental central-DB migration runner (결함 A, 2026-06-26).

Pure-helper coverage (no PostgreSQL): discovery ordering, checksum determinism,
and the apply/skip/drift planner. The live PostgreSQL behaviour (apply / idempotent
re-run / 001-only incremental / baseline) is exercised by the smoke procedure in
docs/development/central-db-migrations.md and recorded in the evaluation — the
local pytest run currently SIGABRTs, so these run cleanly under `python -m unittest`
too.
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / 'scripts'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402

from platform_db_migrate import (  # noqa: E402
    _INVALID_INDEX_SQL,
    MigrationApplyError,
    MigrationDriftError,
    MigrationRollbackError,
    checksum_sql,
    discover_migrations,
    migrate,
    migration_requires_non_transactional,
    parse_rollback_statements,
    plan_reconcile,
    plan_rollback,
    plan_migrations,
    reconcile,
    rollback,
    rollback_annotation_lines_are_inert_comments,
    split_sql_statements,
)

MIGRATIONS_DIR = resolve_repo_artifact(__file__, 'docs/platform/migrations')


def _write(d: Path, name: str, body: str = 'SELECT 1;') -> Path:
    p = d / name
    p.write_text(body, encoding='utf-8')
    return p


class FakeCursor:
    def __init__(self, connection: 'FakeConnection') -> None:
        self.connection = connection
        self._last_sql = ''

    def __enter__(self) -> 'FakeCursor':
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def execute(self, sql: str, params=None) -> None:
        self._last_sql = sql
        self.connection.executed.append((sql, params))
        # Non-breaking side-channel: capture autocommit state at each execute so
        # tests can assert the non-transactional path runs under autocommit=True.
        self.connection.autocommit_log.append((sql, self.connection.autocommit))
        if 'DELETE FROM "schema_migrations"' in sql and params:
            self.connection.applied.pop(params[0], None)
        # reconcile UPDATE: params = (checksum, applied_at, applied_by, version)
        if 'UPDATE "schema_migrations" SET checksum' in sql and params:
            self.connection.applied[params[3]] = params[0]

    def fetchall(self):
        if self._last_sql == 'SELECT version, checksum FROM "schema_migrations"':
            return list(self.connection.applied.items())
        if self._last_sql == _INVALID_INDEX_SQL:
            # Pop the next programmed INVALID-index snapshot (FIFO): the runner
            # reads the INVALID-index set ONCE per non-transactional apply (an
            # absolute post-condition), so a test supplies one row-list per
            # CONCURRENTLY migration — e.g. [['ux']] to model a leftover.
            snap = self.connection.invalid_index_snapshots
            rows = snap.pop(0) if snap else []
            return [(name,) for name in rows]
        return []


class FakeConnection:
    def __init__(
        self, applied: dict[str, str], *, invalid_index_snapshots=None
    ) -> None:
        self.applied = dict(applied)
        self.executed: list[tuple[str, object]] = []
        self.autocommit_log: list[tuple[str, bool]] = []
        # FIFO queue of INVALID-index name lists; each `_invalid_index_names`
        # call pops one. Default empty → every snapshot reads no invalid indexes.
        self.invalid_index_snapshots: list[list[str]] = list(
            invalid_index_snapshots or []
        )
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.autocommit = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class TestChecksum(unittest.TestCase):
    def test_deterministic(self) -> None:
        self.assertEqual(checksum_sql('CREATE TABLE x;'), checksum_sql('CREATE TABLE x;'))

    def test_newline_normalised(self) -> None:
        # CRLF vs LF must not change the checksum (cross-platform stable).
        self.assertEqual(checksum_sql('a\r\nb'), checksum_sql('a\nb'))

    def test_distinct_content_distinct_checksum(self) -> None:
        self.assertNotEqual(checksum_sql('a'), checksum_sql('b'))


class TestDiscover(unittest.TestCase):
    def test_orders_by_numeric_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            # Intentionally created out of order; 10 must sort after 2.
            for name in ('010_j.sql', '002_b.sql', '001_a.sql'):
                _write(dp, name)
            self.assertEqual(
                [v for v, _ in discover_migrations(dp)],
                ['001_a', '002_b', '010_j'],
            )

    def test_ignores_non_migration_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            _write(dp, '001_a.sql')
            _write(dp, 'README.md', '# not sql')
            _write(dp, 'notes.sql')  # no NNN_ prefix
            self.assertEqual([v for v, _ in discover_migrations(dp)], ['001_a'])

    def test_duplicate_prefix_raises(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            _write(dp, '001_a.sql')
            _write(dp, '001_b.sql')
            with self.assertRaises(ValueError):
                discover_migrations(dp)


class TestPlan(unittest.TestCase):
    def _migs(self, d: Path):
        _write(d, '001_a.sql', 'CREATE TABLE a;')
        _write(d, '002_b.sql', 'CREATE TABLE b;')
        return discover_migrations(d)

    def test_empty_ledger_queues_all_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            migs = self._migs(Path(d))
            to_apply, drift = plan_migrations(migs, {})
            self.assertEqual([v for v, _, _ in to_apply], ['001_a', '002_b'])
            self.assertEqual(drift, [])

    def test_applied_matching_checksum_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            migs = self._migs(dp)
            c0 = checksum_sql((dp / '001_a.sql').read_text())
            to_apply, drift = plan_migrations(migs, {'001_a': c0})
            self.assertEqual([v for v, _, _ in to_apply], ['002_b'])
            self.assertEqual(drift, [])

    def test_changed_applied_checksum_is_drift_not_reapply(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            migs = self._migs(Path(d))
            to_apply, drift = plan_migrations(migs, {'001_a': 'stale-checksum'})
            # Drift is reported; the edited migration is NOT queued for re-apply.
            self.assertTrue(drift)
            self.assertNotIn('001_a', [v for v, _, _ in to_apply])

    def test_fresh_checksum_is_carried_for_recording(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            migs = self._migs(dp)
            to_apply, _ = plan_migrations(migs, {})
            expected = checksum_sql((dp / '001_a.sql').read_text())
            first = next(item for item in to_apply if item[0] == '001_a')
            self.assertEqual(first[2], expected)


class TestRealRepoMigrations(unittest.TestCase):
    """The actual docs/platform/migrations set is discoverable + drift-free vs itself."""

    def test_repo_migrations_discover_in_order(self) -> None:
        migs = discover_migrations()
        versions = [v for v, _ in migs]
        self.assertEqual(versions, sorted(versions))
        self.assertTrue(versions[0].startswith('001_'))

    def test_repo_migrations_self_consistent_no_drift(self) -> None:
        migs = discover_migrations()
        applied = {v: checksum_sql(p.read_text(encoding='utf-8')) for v, p in migs}
        to_apply, drift = plan_migrations(migs, applied)
        self.assertEqual(to_apply, [])
        self.assertEqual(drift, [])


class TestLockIdParity(unittest.TestCase):
    """The self-contained runner's advisory-lock id MUST match the evidence runner's.

    `platform_db_migrate` inlines `advisory_lock_id` (so it is a single packageable
    file) instead of importing it; this seals the two definitions against drift so
    both tools serialise on the same PostgreSQL advisory lock.
    """

    def test_lock_id_matches_evidence_runner(self) -> None:
        from platform_db_migrate import advisory_lock_id as migrate_lock_id
        from fcc_test_platform.db_migration_runner_cli import advisory_lock_id as evidence_lock_id

        for key in ('fcc-platform:central-db-migrate', 'fcc-platform:001_initial_central_db', 'x'):
            self.assertEqual(migrate_lock_id(key), evidence_lock_id(key))


class TestRollbackConventionParser(unittest.TestCase):
    """The `--rollback` parser primitive itself (pure, no DB)."""

    def test_extracts_statements_in_order_stripping_prefix(self) -> None:
        text = (
            'CREATE TABLE x (id INT);\n'
            '--rollback DROP TABLE x;\n'
            '-- a normal comment, not a rollback\n'
            '--rollback DROP TABLE y;\n'
        )
        self.assertEqual(
            parse_rollback_statements(text),
            ['DROP TABLE x;', 'DROP TABLE y;'],
        )

    def test_keyword_is_case_insensitive_but_contiguous(self) -> None:
        # The keyword is case-insensitive...
        self.assertEqual(parse_rollback_statements('--RollBack DROP INDEX foo;'), ['DROP INDEX foo;'])
        # ...but must be the contiguous `--rollback` token: a prose line that
        # merely starts with "-- Rollback ..." is NOT an annotation.
        self.assertEqual(parse_rollback_statements('-- Rollback scope = the index;'), [])

    def test_no_annotations_yields_empty(self) -> None:
        self.assertEqual(parse_rollback_statements('SELECT 1;\n-- plain comment\n'), [])


class TestMigration008ReversibleContract(unittest.TestCase):
    """008 identity hardening declares a real, forward-safe DOWN migration."""

    def setUp(self) -> None:
        self.path = MIGRATIONS_DIR / '008_identity_issuer_subject_rbac.sql'
        self.text = self.path.read_text(encoding='utf-8')
        self.rollback = parse_rollback_statements(self.text)

    def test_008_declares_rollback_for_the_uniqueness_inversion(self) -> None:
        joined = '\n'.join(self.rollback)
        # Drops the (issuer, subject) UNIQUE and restores subject-only UNIQUE.
        self.assertIn('DROP INDEX CONCURRENTLY IF EXISTS "ux_users_issuer_subject"', joined)
        self.assertIn('CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "ux_users_subject"', joined)
        self.assertIn('ON "users" ("subject")', joined)
        # Order: drop the composite UNIQUE BEFORE recreating the subject-only one.
        drop_idx = next(i for i, s in enumerate(self.rollback) if 'DROP INDEX' in s)
        create_idx = next(i for i, s in enumerate(self.rollback) if 'CREATE UNIQUE INDEX' in s)
        self.assertLess(drop_idx, create_idx)

    def test_008_forward_drops_composite_index_before_concurrent_create(self) -> None:
        statements = split_sql_statements(self.text)
        drop_idx = next(
            i for i, s in enumerate(statements)
            if 'DROP INDEX CONCURRENTLY IF EXISTS "ux_users_issuer_subject"' in s
        )
        create_idx = next(
            i for i, s in enumerate(statements)
            if 'CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "ux_users_issuer_subject"' in s
        )
        self.assertLess(drop_idx, create_idx)

    def test_008_rollback_is_lossless_no_column_or_data_destruction(self) -> None:
        joined = '\n'.join(self.rollback)
        # Lossless: the issuer column + legacy classification are retained.
        self.assertNotIn('DROP COLUMN', joined)
        self.assertNotIn('DELETE FROM', joined)
        self.assertNotIn('TRUNCATE', joined)

    def test_008_forward_apply_is_byte_safe(self) -> None:
        # The whole-file forward runner must execute ZERO rollback statements:
        # every `--rollback` line is an inert SQL comment.
        self.assertTrue(self.rollback, 'expected the DOWN migration to be declared')
        self.assertTrue(rollback_annotation_lines_are_inert_comments(self.text))

    def test_008_concurrent_rollback_not_wrapped_in_transaction(self) -> None:
        # CONCURRENTLY index ops cannot run inside a transaction; the rollback
        # must not introduce a BEGIN/COMMIT wrapper.
        joined = '\n'.join(self.rollback).upper()
        self.assertNotIn('BEGIN', joined)
        self.assertNotIn('COMMIT', joined)

    def test_008_discovered_once_despite_rollback_annotations(self) -> None:
        # The added annotation lines must not trip discovery (e.g. accidentally
        # registering a second 008 entry).
        versions = [v for v, _ in discover_migrations(MIGRATIONS_DIR)]
        self.assertEqual(versions.count('008_identity_issuer_subject_rbac'), 1)


class TestRollbackPlanning(unittest.TestCase):
    def test_plan_rollback_requires_latest_applied_target(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            _write(dp, '001_a.sql', 'CREATE TABLE a;\n--rollback DROP TABLE a;')
            _write(dp, '002_b.sql', 'CREATE TABLE b;\n--rollback DROP TABLE b;')
            migrations = discover_migrations(dp)
            applied = {
                '001_a': checksum_sql((dp / '001_a.sql').read_text(encoding='utf-8')),
                '002_b': checksum_sql((dp / '002_b.sql').read_text(encoding='utf-8')),
            }
            with self.assertRaises(MigrationRollbackError):
                plan_rollback(migrations, applied, target='001_a')

    def test_plan_rollback_rejects_checksum_drift(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            _write(dp, '001_a.sql', 'CREATE TABLE a;\n--rollback DROP TABLE a;')
            migrations = discover_migrations(dp)
            with self.assertRaises(MigrationDriftError):
                plan_rollback(migrations, {'001_a': 'stale-checksum'}, target='001_a')

    def test_plan_rollback_returns_target_path_and_statements(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            path = _write(dp, '001_a.sql', 'CREATE TABLE a;\n--rollback DROP TABLE a;')
            migrations = discover_migrations(dp)
            planned_path, statements = plan_rollback(
                migrations,
                {'001_a': checksum_sql(path.read_text(encoding='utf-8'))},
                target='001_a',
            )
            self.assertEqual(planned_path, path)
            self.assertEqual(statements, ['DROP TABLE a;'])


class TestRollbackCommand(unittest.TestCase):
    def test_rollback_executes_annotations_without_outer_transaction_and_deletes_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            path = _write(
                dp,
                '001_a.sql',
                'CREATE TABLE a;\n'
                '--rollback DROP INDEX CONCURRENTLY IF EXISTS ux_a;\n'
                '--rollback DROP TABLE a;\n',
            )
            fake = FakeConnection({'001_a': checksum_sql(path.read_text(encoding='utf-8'))})

            with patch('platform_db_migrate._connect', return_value=fake):
                result = rollback(
                    dsn='postgresql://example',
                    migrations_dir=dp,
                    target='001_a',
                    applied_by='unit-test',
                )

        executed_sql = [sql for sql, _params in fake.executed]
        self.assertEqual(result['rolled_back'], ['001_a'])
        self.assertIn('DROP INDEX CONCURRENTLY IF EXISTS ux_a;', executed_sql)
        self.assertIn('DROP TABLE a;', executed_sql)
        self.assertIn('DELETE FROM "schema_migrations" WHERE version = %s', executed_sql)
        self.assertTrue(fake.closed)
        self.assertFalse(fake.autocommit)
        self.assertEqual(fake.rollbacks, 0)


class TestNonTransactionalDetection(unittest.TestCase):
    """`migration_requires_non_transactional` — pure detection of the autocommit path."""

    def test_concurrently_in_body_is_detected(self) -> None:
        sql = 'CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux ON "t" ("c");'
        self.assertTrue(migration_requires_non_transactional(sql))

    def test_case_insensitive_detection(self) -> None:
        self.assertTrue(migration_requires_non_transactional('drop index concurrently if exists ux;'))

    def test_plain_ddl_is_transactional(self) -> None:
        sql = 'ALTER TABLE "t" ADD COLUMN "c" TEXT;\nUPDATE "t" SET "c" = \'x\';'
        self.assertFalse(migration_requires_non_transactional(sql))

    def test_explicit_marker_opt_in(self) -> None:
        # Non-CONCURRENTLY DDL that still needs autocommit (e.g. ALTER TYPE ADD VALUE).
        sql = "-- migrate:no-transaction\nALTER TYPE mood ADD VALUE 'ok';"
        self.assertTrue(migration_requires_non_transactional(sql))

    def test_marker_tolerates_no_space(self) -> None:
        self.assertTrue(migration_requires_non_transactional('--migrate:no-transaction\nSELECT 1;'))

    def test_comment_only_concurrently_is_not_a_false_positive(self) -> None:
        # Prose comments AND `--rollback ... CONCURRENTLY` DOWN annotations mention
        # CONCURRENTLY but are inert; the executable body has none → transactional.
        sql = (
            'CREATE TABLE "t" ("id" INT);\n'
            '-- the failing CREATE UNIQUE INDEX CONCURRENTLY leaves an INVALID index\n'
            '--rollback DROP INDEX CONCURRENTLY IF EXISTS "ux";\n'
        )
        self.assertFalse(migration_requires_non_transactional(sql))

    def test_real_008_requires_non_transactional(self) -> None:
        text = (MIGRATIONS_DIR / '008_identity_issuer_subject_rbac.sql').read_text(encoding='utf-8')
        self.assertTrue(migration_requires_non_transactional(text))

    def test_real_007_is_transactional(self) -> None:
        text = (MIGRATIONS_DIR / '007_project_status_domain.sql').read_text(encoding='utf-8')
        self.assertFalse(migration_requires_non_transactional(text))

    def test_real_001_is_transactional(self) -> None:
        text = (MIGRATIONS_DIR / '001_initial_central_db.sql').read_text(encoding='utf-8')
        self.assertFalse(migration_requires_non_transactional(text))


class TestSplitSqlStatements(unittest.TestCase):
    """`split_sql_statements` — pure per-statement splitting for the autocommit path."""

    def test_splits_on_semicolon_and_keeps_terminator(self) -> None:
        self.assertEqual(
            split_sql_statements('CREATE INDEX a ON t (c);\nDROP INDEX b;'),
            ['CREATE INDEX a ON t (c);', 'DROP INDEX b;'],
        )

    def test_drops_blank_fragments(self) -> None:
        self.assertEqual(split_sql_statements(';;\nSELECT 1;;\n'), ['SELECT 1;'])

    def test_strips_comments_and_annotations(self) -> None:
        sql = (
            '-- header comment\n'
            'CREATE INDEX a ON t (c);  -- trailing comment\n'
            '--rollback DROP INDEX a;\n'
            '-- migrate:no-transaction\n'
        )
        self.assertEqual(split_sql_statements(sql), ['CREATE INDEX a ON t (c);'])

    def test_real_008_splits_into_executable_statements(self) -> None:
        text = (MIGRATIONS_DIR / '008_identity_issuer_subject_rbac.sql').read_text(encoding='utf-8')
        statements = split_sql_statements(text)
        # No `--rollback` DOWN annotation leaks into the forward statement list.
        self.assertTrue(all('--rollback' not in s.lower() for s in statements))
        joined = '\n'.join(statements)
        self.assertIn('CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "ux_users_issuer_subject"', joined)
        self.assertIn('DROP INDEX CONCURRENTLY IF EXISTS "ux_users_subject"', joined)


class TestMigrateTransactionRouting(unittest.TestCase):
    """migrate() routes each migration to the correct (transaction vs autocommit) path."""

    def _run(self, tmp: Path, *, invalid_index_snapshots=None):
        fake = FakeConnection({}, invalid_index_snapshots=invalid_index_snapshots)
        with patch('platform_db_migrate._connect', return_value=fake):
            result = migrate(
                dsn='postgresql://example',
                migrations_dir=tmp,
                applied_by='unit-test',
            )
        return fake, result

    def test_concurrently_migration_runs_per_statement_under_autocommit(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            _write(dp, '001_a.sql', 'CREATE TABLE a (id INT);')
            _write(
                dp,
                '002_c.sql',
                'CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux ON a (id);\n'
                'DROP INDEX CONCURRENTLY IF EXISTS old_ux;\n',
            )
            fake, result = self._run(dp)

        self.assertEqual(result['applied'], ['001_a', '002_c'])
        executed_sql = [sql for sql, _p in fake.executed]
        # The CONCURRENTLY migration is executed statement-by-statement (not as one
        # whole-file blob), and each self-committing DDL ran under autocommit=True.
        create_exec = next(e for e in fake.autocommit_log if 'CREATE UNIQUE INDEX CONCURRENTLY' in e[0])
        drop_exec = next(e for e in fake.autocommit_log if 'DROP INDEX CONCURRENTLY' in e[0])
        self.assertTrue(create_exec[1], 'CONCURRENTLY create must run under autocommit=True')
        self.assertTrue(drop_exec[1], 'CONCURRENTLY drop must run under autocommit=True')
        # Individual statements — not the concatenated file text.
        self.assertIn('CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux ON a (id);', executed_sql)
        self.assertIn('DROP INDEX CONCURRENTLY IF EXISTS old_ux;', executed_sql)
        # autocommit restored to its original value; no aborted-tx rollback.
        self.assertFalse(fake.autocommit)
        self.assertEqual(fake.rollbacks, 0)

    def test_plain_migration_runs_in_transaction_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            _write(dp, '001_a.sql', 'CREATE TABLE a (id INT);')
            plain_body = 'ALTER TABLE a ADD COLUMN b TEXT;\nUPDATE a SET b = \'x\';'
            _write(dp, '002_p.sql', plain_body)
            fake, result = self._run(dp)

        self.assertEqual(result['applied'], ['001_a', '002_p'])
        executed_sql = [sql for sql, _p in fake.executed]
        # Transactional path executes the WHOLE file body in a single cursor call
        # (byte-identical to the pre-patch behaviour), under autocommit=False.
        self.assertIn(plain_body, executed_sql)
        plain_exec = next(e for e in fake.autocommit_log if e[0] == plain_body)
        self.assertFalse(plain_exec[1], 'plain migration must run under autocommit=False')
        # Every autocommit execute stayed False across the whole run.
        self.assertTrue(all(not flag for _sql, flag in fake.autocommit_log))
        self.assertGreater(fake.commits, 0)
        self.assertEqual(fake.rollbacks, 0)

    def test_ledger_recorded_for_concurrently_migration(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            _write(dp, '001_a.sql', 'CREATE TABLE a (id INT);')
            _write(dp, '002_c.sql', 'CREATE INDEX CONCURRENTLY IF NOT EXISTS ux ON a (id);')
            fake, _result = self._run(dp)

        record_calls = [
            params for sql, params in fake.executed
            if sql.startswith('INSERT INTO "schema_migrations"') and params
        ]
        recorded_versions = {params[0] for params in record_calls}
        self.assertIn('002_c', recorded_versions)

    def test_invalid_index_after_apply_refuses_to_record(self) -> None:
        # An INVALID index present AFTER the apply (a failed CREATE INDEX
        # CONCURRENTLY, or its leftover skipped by IF NOT EXISTS on retry) fails
        # the absolute post-condition → MigrationApplyError, migration NOT recorded.
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            _write(dp, '001_a.sql', 'CREATE TABLE a (id INT);')
            _write(dp, '002_c.sql', 'CREATE INDEX CONCURRENTLY IF NOT EXISTS ux ON a (id);')
            # ONE snapshot per CONCURRENTLY apply: post-apply INVALID set = {'ux'}.
            fake = FakeConnection({}, invalid_index_snapshots=[['ux']])
            with patch('platform_db_migrate._connect', return_value=fake):
                with self.assertRaises(MigrationApplyError):
                    migrate(
                        dsn='postgresql://example',
                        migrations_dir=dp,
                        applied_by='unit-test',
                    )

        # The first migration is recorded, but the concurrent migration is not
        # recorded after the invalid-index postcondition fails.
        record_calls = [
            params for sql, params in fake.executed
            if sql.startswith('INSERT INTO "schema_migrations"') and params
        ]
        recorded_versions = {params[0] for params in record_calls}
        self.assertIn('001_a', recorded_versions)
        self.assertNotIn('002_c', recorded_versions)
        self.assertGreater(fake.rollbacks, 0)

    def test_retry_leftover_invalid_index_is_still_caught(self) -> None:
        # THE original NO-GO case: on the retry after a failed CONCURRENTLY build,
        # the leftover INVALID index is already present, `CREATE ... IF NOT EXISTS`
        # is a no-op, and it is STILL present after apply. The runner reads ONLY the
        # post-apply INVALID set (it takes no "before" snapshot), so the fake models
        # exactly that observable state: post-apply INVALID = {'ux'}. An absolute
        # post-check catches it; a before/after diff (which the runner does NOT do)
        # would see "no NEW invalid" here and wrongly record — the hole this closes.
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            _write(dp, '001_a.sql', 'CREATE TABLE a (id INT);')
            _write(dp, '002_c.sql', 'CREATE INDEX CONCURRENTLY IF NOT EXISTS ux ON a (id);')
            fake = FakeConnection({}, invalid_index_snapshots=[['ux']])
            with patch('platform_db_migrate._connect', return_value=fake):
                with self.assertRaises(MigrationApplyError):
                    migrate(
                        dsn='postgresql://example',
                        migrations_dir=dp,
                        applied_by='unit-test',
                    )
        recorded_versions = {
            params[0]
            for sql, params in fake.executed
            if sql.startswith('INSERT INTO "schema_migrations"') and params
        }
        self.assertNotIn('002_c', recorded_versions)

    def test_preexisting_invalid_index_also_blocks(self) -> None:
        # Absolute (not diff): even an UNRELATED pre-existing INVALID index blocks —
        # a DB carrying an invalid index is broken and must be repaired before more
        # schema is layered on. This is the deliberate inverse of a scoped diff,
        # which would let the migration's OWN prior-failed leftover slip through.
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            _write(dp, '001_a.sql', 'CREATE TABLE a (id INT);')
            _write(dp, '002_c.sql', 'CREATE INDEX CONCURRENTLY IF NOT EXISTS ux ON a (id);')
            fake = FakeConnection({}, invalid_index_snapshots=[['unrelated']])
            with patch('platform_db_migrate._connect', return_value=fake):
                with self.assertRaises(MigrationApplyError):
                    migrate(
                        dsn='postgresql://example',
                        migrations_dir=dp,
                        applied_by='unit-test',
                    )
        recorded_versions = {
            params[0]
            for sql, params in fake.executed
            if sql.startswith('INSERT INTO "schema_migrations"') and params
        }
        self.assertNotIn('002_c', recorded_versions)

    def test_clean_concurrently_apply_records(self) -> None:
        # No INVALID index after apply (default empty snapshot) → post-condition
        # passes → the CONCURRENTLY migration records normally. Confirms the guard
        # does not false-block a healthy apply.
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            _write(dp, '001_a.sql', 'CREATE TABLE a (id INT);')
            _write(dp, '002_c.sql', 'CREATE INDEX CONCURRENTLY IF NOT EXISTS ux ON a (id);')
            fake = FakeConnection({})
            with patch('platform_db_migrate._connect', return_value=fake):
                result = migrate(
                    dsn='postgresql://example',
                    migrations_dir=dp,
                    applied_by='unit-test',
                )
        self.assertEqual(result['applied'], ['001_a', '002_c'])
        recorded_versions = {
            params[0]
            for sql, params in fake.executed
            if sql.startswith('INSERT INTO "schema_migrations"') and params
        }
        self.assertIn('002_c', recorded_versions)
        self.assertEqual(fake.rollbacks, 0)


class TestReconcilePlanning(unittest.TestCase):
    """`plan_reconcile` — pure partition of drift into bootstrap re-stamp vs blocking.

    Seals the 001 exporter-rerender drift policy (central-db-001-rerender-drift):
    the exporter regenerates the bootstrap migration, so its checksum legitimately
    drifts; that is reconcilable. A NON-bootstrap applied migration drifting is a
    real append-only violation and must block reconcile.
    """

    def _migs(self, d: Path):
        _write(d, '001_a.sql', 'CREATE TABLE a;')
        _write(d, '002_b.sql', 'CREATE TABLE b;')
        return discover_migrations(d)

    def test_no_drift_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            migs = self._migs(dp)
            applied = {v: checksum_sql(p.read_text(encoding='utf-8')) for v, p in migs}
            bootstrap_update, blocking = plan_reconcile(migs, applied)
            self.assertIsNone(bootstrap_update)
            self.assertEqual(blocking, [])

    def test_bootstrap_drift_is_reconcilable(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            migs = self._migs(dp)
            fresh_001 = checksum_sql((dp / '001_a.sql').read_text(encoding='utf-8'))
            applied = {
                '001_a': 'stale-bootstrap-checksum',
                '002_b': checksum_sql((dp / '002_b.sql').read_text(encoding='utf-8')),
            }
            bootstrap_update, blocking = plan_reconcile(migs, applied)
            self.assertEqual(bootstrap_update, ('001_a', fresh_001))
            self.assertEqual(blocking, [])

    def test_non_bootstrap_drift_blocks_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            migs = self._migs(dp)
            applied = {
                '001_a': checksum_sql((dp / '001_a.sql').read_text(encoding='utf-8')),
                '002_b': 'stale-incremental-checksum',
            }
            bootstrap_update, blocking = plan_reconcile(migs, applied)
            self.assertIsNone(bootstrap_update)
            self.assertEqual(len(blocking), 1)
            self.assertIn('002_b', blocking[0])
            self.assertIn('append-only', blocking[0])

    def test_non_bootstrap_drift_blocks_even_with_bootstrap_drift(self) -> None:
        # A drifted incremental migration is NEVER silently reconciled, even when
        # the bootstrap also drifted — the blocking list still surfaces it.
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            migs = self._migs(dp)
            applied = {'001_a': 'stale-bootstrap', '002_b': 'stale-incremental'}
            bootstrap_update, blocking = plan_reconcile(migs, applied)
            self.assertEqual(bootstrap_update[0], '001_a')
            self.assertEqual(len(blocking), 1)
            self.assertIn('002_b', blocking[0])

    def test_unapplied_bootstrap_is_noop(self) -> None:
        # Nothing to reconcile if the bootstrap row isn't in the ledger yet.
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            migs = self._migs(dp)
            bootstrap_update, blocking = plan_reconcile(migs, {})
            self.assertIsNone(bootstrap_update)
            self.assertEqual(blocking, [])

    def test_empty_migrations_is_noop(self) -> None:
        self.assertEqual(plan_reconcile([], {}), (None, []))


class TestReconcileCommand(unittest.TestCase):
    """`reconcile()` DB-facing — re-stamps ONLY the bootstrap row; never runs SQL."""

    def _run(self, tmp: Path, applied: dict[str, str]):
        fake = FakeConnection(applied)
        with patch('platform_db_migrate._connect', return_value=fake):
            result = reconcile(
                dsn='postgresql://example',
                migrations_dir=tmp,
                applied_by='unit-test',
            )
        return fake, result

    def test_bootstrap_drift_restamps_ledger_and_never_runs_migration_sql(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            _write(dp, '001_a.sql', 'CREATE TABLE a;')
            _write(dp, '002_b.sql', 'CREATE TABLE b;')
            migs = discover_migrations(dp)
            fresh_001 = checksum_sql((dp / '001_a.sql').read_text(encoding='utf-8'))
            applied = {
                '001_a': 'stale-bootstrap',
                '002_b': checksum_sql((dp / '002_b.sql').read_text(encoding='utf-8')),
            }
            fake, result = self._run(dp, applied)

        self.assertEqual(result['reconciled'], ['001_a'])
        self.assertEqual(result['mode'], 'reconcile')
        executed_sql = [sql for sql, _p in fake.executed]
        # The bootstrap row's checksum was re-stamped to the current file...
        self.assertTrue(
            any(sql.startswith('UPDATE "schema_migrations" SET checksum') for sql in executed_sql)
        )
        self.assertEqual(fake.applied['001_a'], fresh_001)
        # ...and NO migration DDL ran (reconcile never executes migration bodies).
        self.assertNotIn('CREATE TABLE a;', executed_sql)
        self.assertNotIn('CREATE TABLE b;', executed_sql)
        self.assertTrue(fake.closed)
        self.assertEqual(fake.rollbacks, 0)

    def test_reconcile_is_idempotent_noop_when_already_matching(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            _write(dp, '001_a.sql', 'CREATE TABLE a;')
            migs = discover_migrations(dp)
            applied = {'001_a': checksum_sql((dp / '001_a.sql').read_text(encoding='utf-8'))}
            fake, result = self._run(dp, applied)

        self.assertEqual(result['reconciled'], [])
        executed_sql = [sql for sql, _p in fake.executed]
        self.assertFalse(
            any(sql.startswith('UPDATE "schema_migrations" SET checksum') for sql in executed_sql)
        )

    def test_reconcile_refuses_non_bootstrap_drift(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            _write(dp, '001_a.sql', 'CREATE TABLE a;')
            _write(dp, '002_b.sql', 'CREATE TABLE b;')
            migs = discover_migrations(dp)
            applied = {
                '001_a': checksum_sql((dp / '001_a.sql').read_text(encoding='utf-8')),
                '002_b': 'stale-incremental',
            }
            with self.assertRaises(MigrationDriftError):
                self._run(dp, applied)


class TestMigrateDriftPolicyUnchanged(unittest.TestCase):
    """`migrate` still loud-fails on bootstrap drift — reconcile is a SEPARATE path.

    The drift guard in `migrate` is byte-identical after the reconcile subcommand
    was added: reconcile never leaks into `migrate`, so genuine drift on an
    already-applied migration (bootstrap or not) still stops the automatic runner.
    Only the explicit `reconcile` command re-stamps the exporter-owned bootstrap.
    """

    def test_migrate_raises_on_bootstrap_drift(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            _write(dp, '001_a.sql', 'CREATE TABLE a;')
            _write(dp, '002_b.sql', 'CREATE TABLE b;')
            fake = FakeConnection({'001_a': 'stale-bootstrap'})
            with patch('platform_db_migrate._connect', return_value=fake):
                with self.assertRaises(MigrationDriftError):
                    migrate(
                        dsn='postgresql://example',
                        migrations_dir=dp,
                        applied_by='unit-test',
                    )
            # No ledger re-stamp happened via migrate.
            executed_sql = [sql for sql, _p in fake.executed]
            self.assertFalse(
                any(sql.startswith('UPDATE "schema_migrations" SET checksum') for sql in executed_sql)
            )


if __name__ == '__main__':
    unittest.main()
