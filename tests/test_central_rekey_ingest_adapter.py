"""ADR-0005 central re-key ingestion adapter (B-1, 2026-06-01).

`PostgresCentralRekeyIngestAdapter` 가 provider-produced mapping envelope 를 central
`measurement_attempts.condition_hash_v2` 에 verbatim 적용함을 봉인한다. central PG migration/
ingestion **실행 0** — `QmarkConnection`/`QmarkCursor` (claim write test 동형) 로 실제 SQL 을
SQLite 에 `%s`→`?` 번역해 contract 를 검증한다.

시나리오 (Codex 요구 1-7 + 원자성):
- clean ingest fills v2 / rerun idempotent / existing-same-v2 skipped
- existing-different-v2 → conflict abort (write 0, 다른 eligible pair 도 미적용)
- condition_hash 불가침 / never-recompute (어댑터 텍스트 금지 토큰 0) / declared checksum audit 보존
- SQL UPDATE/SELECT only (DDL/INSERT/DELETE 0) / audit 실패 시 primary rollback
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / 'src'
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402
from fcc_test_contracts.common.sqlite_connection_factory import SqliteConnectionContext  # noqa: E402

from fcc_test_platform.application.central_audit_write_adapter import (  # noqa: E402
    PostgresCentralAuditWriteAdapter,
)
from fcc_test_platform.application import central_rekey_ingest_adapter as ingest_mod  # noqa: E402
from fcc_test_platform.application.central_rekey_ingest_adapter import (  # noqa: E402
    REKEY_PROPAGATION_EVENT_TYPE,
    PostgresCentralRekeyIngestAdapter,
    build_propagation_audit_detail,
)
from fcc_test_platform.domain.ports.output.central_audit_write_port import AuditWriteError  # noqa: E402
from fcc_test_platform.domain.ports.output.central_rekey_ingest_port import (  # noqa: E402
    RekeyIngestConflictError,
    RekeyIngestError,
)
from fcc_test_platform.domain.services.rekey_dry_run import RekeyMappingEnvelope  # noqa: E402


# ── SQLite fixture (mirrors test_platform_claim_write_fe_p3) ─────────────────


from support.central_pg_sqlite_shim import (  # noqa: E402
    QmarkConnection,
    QmarkCursor,
)


def _build_fixture(db_path: str) -> None:
    """measurement_attempts (condition_hash NOT NULL + condition_hash_v2 nullable) +
    audit_events (no CHECK — re-key event_type CHECK 확장은 deferred migration)."""
    with SqliteConnectionContext(db_path) as conn:
        conn.execute(
            'CREATE TABLE measurement_attempts ('
            'id TEXT PRIMARY KEY, condition_hash TEXT NOT NULL, condition_hash_v2 TEXT)'
        )
        conn.execute(
            'CREATE TABLE audit_events ('
            'id TEXT PRIMARY KEY, event_type TEXT NOT NULL, project_id TEXT, '
            'actor_subject TEXT NOT NULL, target_user_subject TEXT, '
            'target_claim_id TEXT, role_key TEXT, detail_json TEXT, '
            'occurred_at TEXT NOT NULL, created_at TEXT NOT NULL)'
        )
        conn.commit()


def _seed_attempts(db_path: str, rows: list[tuple]) -> None:
    """rows: (id, condition_hash, condition_hash_v2-or-None)."""
    with SqliteConnectionContext(db_path) as conn:
        conn.executemany('INSERT INTO measurement_attempts VALUES (?, ?, ?)', rows)
        conn.commit()


def _attempts(db_path: str) -> list[tuple]:
    with SqliteConnectionContext(db_path) as conn:
        cur = conn.execute(
            'SELECT id, condition_hash, condition_hash_v2 FROM measurement_attempts ORDER BY id'
        )
        return cur.fetchall()


def _audit_rows(db_path: str) -> list[tuple]:
    with SqliteConnectionContext(db_path) as conn:
        cur = conn.execute(
            'SELECT id, event_type, detail_json FROM audit_events ORDER BY id'
        )
        return cur.fetchall()


def _envelope(pairs, *, provider='unlicensed',
              old_ck='OLD_CK', new_ck='NEW_CK', map_ck='MAP_CK') -> RekeyMappingEnvelope:
    return RekeyMappingEnvelope(
        provider=provider,
        pairs=tuple(pairs),
        old_hash_checksum=old_ck,
        new_hash_checksum=new_ck,
        mapping_checksum=map_ck,
        record_count=len({old for old, _ in pairs}),
    )


class _IngestFixture(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        tmp.close()
        self.db_path = tmp.name
        _build_fixture(self.db_path)
        self.factory = lambda: QmarkConnection(self.db_path)

    def tearDown(self) -> None:
        Path(self.db_path).unlink(missing_ok=True)

    def _adapter(self, audit_writer=None) -> PostgresCentralRekeyIngestAdapter:
        return PostgresCentralRekeyIngestAdapter(self.factory, audit_writer=audit_writer)


class TestRekeyIngestApply(_IngestFixture):
    def test_clean_ingest_fills_v2(self) -> None:
        _seed_attempts(self.db_path, [
            ('a1', 'oldA', None), ('a2', 'oldA', None), ('b1', 'oldB', None),
        ])
        audit = self._adapter().ingest_mapping_envelope(
            _envelope([('oldA', 'newA'), ('oldB', 'newB')]),
        )
        self.assertEqual(audit.updated_count, 3)
        self.assertEqual(audit.skipped_count, 0)
        self.assertEqual(
            _attempts(self.db_path),
            [('a1', 'oldA', 'newA'), ('a2', 'oldA', 'newA'), ('b1', 'oldB', 'newB')],
        )

    def test_rerun_is_idempotent(self) -> None:
        _seed_attempts(self.db_path, [('a1', 'oldA', None), ('b1', 'oldB', None)])
        env = _envelope([('oldA', 'newA'), ('oldB', 'newB')])
        self._adapter().ingest_mapping_envelope(env)
        after_first = _attempts(self.db_path)
        audit2 = self._adapter().ingest_mapping_envelope(env)  # no error
        self.assertEqual(audit2.updated_count, 0)
        self.assertEqual(audit2.skipped_count, 2)
        self.assertEqual(_attempts(self.db_path), after_first)

    def test_existing_same_v2_skipped(self) -> None:
        # one oldA row already at newA → not a conflict, skipped; the other filled.
        _seed_attempts(self.db_path, [('a1', 'oldA', 'newA'), ('a2', 'oldA', None)])
        audit = self._adapter().ingest_mapping_envelope(_envelope([('oldA', 'newA')]))
        self.assertEqual(audit.updated_count, 1)
        self.assertEqual(audit.skipped_count, 1)
        self.assertEqual(
            _attempts(self.db_path), [('a1', 'oldA', 'newA'), ('a2', 'oldA', 'newA')],
        )

    def test_existing_different_v2_conflict_aborts_write_zero(self) -> None:
        # a1 already at a DIFFERENT v2 (conflict); b1 is eligible. The pre-scan must
        # abort BEFORE any UPDATE → b1 stays NULL (write 0).
        _seed_attempts(self.db_path, [('a1', 'oldA', 'WRONG'), ('b1', 'oldB', None)])
        with self.assertRaises(RekeyIngestConflictError) as ctx:
            self._adapter().ingest_mapping_envelope(
                _envelope([('oldA', 'newA'), ('oldB', 'newB')]),
            )
        self.assertEqual(len(ctx.exception.conflicts), 1)
        self.assertEqual(
            _attempts(self.db_path), [('a1', 'oldA', 'WRONG'), ('b1', 'oldB', None)],
        )

    def test_condition_hash_never_overwritten(self) -> None:
        _seed_attempts(self.db_path, [('a1', 'oldA', None)])
        self._adapter().ingest_mapping_envelope(_envelope([('oldA', 'newA')]))
        rows = _attempts(self.db_path)
        self.assertEqual(rows[0][1], 'oldA')  # live condition_hash unchanged
        self.assertEqual(rows[0][2], 'newA')  # only v2 filled

    def test_no_matching_rows_is_noop(self) -> None:
        _seed_attempts(self.db_path, [('a1', 'oldA', None)])
        audit = self._adapter().ingest_mapping_envelope(_envelope([('oldZ', 'newZ')]))
        self.assertEqual(audit.updated_count, 0)
        self.assertEqual(_attempts(self.db_path), [('a1', 'oldA', None)])


class _RaceInjectingCursor(QmarkCursor):
    """Injects a foreign conflicting ``condition_hash_v2`` exactly once, right
    before the first re-key UPDATE — simulating a concurrent writer that fills a
    row with a DIFFERENT v2 *after* the pre-scan passed (TOCTOU). The injection
    runs on the same raw cursor/transaction so the post-write re-scan sees it and
    an abort rolls it back together with our own write (write 0)."""

    def __init__(self, raw, *, target_id: str, foreign_v2: str) -> None:
        super().__init__(raw)
        self._target_id = target_id
        self._foreign_v2 = foreign_v2
        self._injected = False

    def execute(self, statement: str, parameters: tuple = ()) -> None:
        upper = statement.lstrip().upper()
        if (not self._injected and upper.startswith('UPDATE')
                and 'CONDITION_HASH_V2' in statement.upper()):
            self._injected = True
            self._raw.execute(
                'UPDATE measurement_attempts SET condition_hash_v2 = ? WHERE id = ?',
                (self._foreign_v2, self._target_id),
            )
        super().execute(statement, parameters)


class _RaceConnection(QmarkConnection):
    def __init__(self, db_path: str, *, target_id: str, foreign_v2: str) -> None:
        super().__init__(db_path)
        self._target_id = target_id
        self._foreign_v2 = foreign_v2

    def cursor(self) -> _RaceInjectingCursor:
        return _RaceInjectingCursor(
            self._conn.cursor(), target_id=self._target_id, foreign_v2=self._foreign_v2)


class TestRekeyIngestRaceConflict(_IngestFixture):
    """pre-scan 통과 후 발생한 conflict 를 post-write re-verification 이 잡아 write 0
    으로 abort 함을 봉인 (공유자원 원자성 — Codex P0)."""

    def test_post_scan_catches_conflict_after_prescan_write_zero(self) -> None:
        # a1·a2 둘 다 oldA/NULL → pre-scan clean. 첫 UPDATE 직전 a2 에 foreign v2 주입 →
        # post-scan 이 잡아야 한다 (pre-scan 만이면 conflict 0 으로 거짓 success).
        _seed_attempts(self.db_path, [('a1', 'oldA', None), ('a2', 'oldA', None)])
        adapter = PostgresCentralRekeyIngestAdapter(
            lambda: _RaceConnection(self.db_path, target_id='a2', foreign_v2='FOREIGN'))
        with self.assertRaises(RekeyIngestConflictError) as ctx:
            adapter.ingest_mapping_envelope(_envelope([('oldA', 'newA')]))
        self.assertEqual(len(ctx.exception.conflicts), 1)
        # write 0 — a1 의 정당한 update + a2 foreign 주입 모두 rollback (원상복구).
        self.assertEqual(
            _attempts(self.db_path), [('a1', 'oldA', None), ('a2', 'oldA', None)])

    def test_updated_count_is_actual_rowcount_not_eligible_precount(self) -> None:
        # mutant-proof: a1 NULL → UPDATE 직전 동시 writer 가 a1 을 SAME value(newA)로 채움
        # (conflict 아님). eligible pre-count 는 1(스캔 시점 NULL)이지만 실제 UPDATE rowcount 는
        # 0(이미 채워져 IS NULL 미매치). updated_count==0 이어야 한다 — 구현을 eligible
        # pre-count 로 되돌리면 1 을 보고해 이 테스트가 FAIL(rowcount 봉인 mutant-proof).
        _seed_attempts(self.db_path, [('a1', 'oldA', None)])
        adapter = PostgresCentralRekeyIngestAdapter(
            lambda: _RaceConnection(self.db_path, target_id='a1', foreign_v2='newA'))
        audit = adapter.ingest_mapping_envelope(_envelope([('oldA', 'newA')]))
        self.assertEqual(audit.updated_count, 0)  # 실측 rowcount (eligible pre-count 이면 1)
        self.assertEqual(audit.skipped_count, 0)  # 스캔 시점엔 already-set 아니었음
        self.assertEqual(_attempts(self.db_path), [('a1', 'oldA', 'newA')])  # foreign fill 잔존


class TestRekeyIngestAudit(_IngestFixture):
    def test_declared_mapping_checksum_preserved_in_audit(self) -> None:
        _seed_attempts(self.db_path, [('a1', 'oldA', None)])
        env = _envelope([('oldA', 'newA')], old_ck='OCK', new_ck='NCK', map_ck='MAPCK_VERBATIM')
        adapter = self._adapter(audit_writer=PostgresCentralAuditWriteAdapter())
        adapter.ingest_mapping_envelope(env, audit_meta={
            'id': 'evt-1', 'actor_subject': 'platform-owner',
            'project_id': 'proj-1', 'occurred_at': 't0', 'created_at': 't0',
        })
        audit_rows = _audit_rows(self.db_path)
        self.assertEqual(len(audit_rows), 1)
        _id, event_type, detail_json = audit_rows[0]
        self.assertEqual(event_type, REKEY_PROPAGATION_EVENT_TYPE)
        detail = json.loads(detail_json)
        # declared checksums copied verbatim (no recompute).
        self.assertEqual(detail['mapping_checksum'], 'MAPCK_VERBATIM')
        self.assertEqual(detail['old_hash_checksum'], 'OCK')
        self.assertEqual(detail['new_hash_checksum'], 'NCK')
        self.assertEqual(detail['applied_count'], 1)

    def test_audit_detail_builder_is_pure_string_copy(self) -> None:
        env = _envelope([('oldA', 'newA')], map_ck='ABC123')
        detail = build_propagation_audit_detail(env, applied_count=7)
        self.assertEqual(detail['mapping_checksum'], 'ABC123')
        self.assertEqual(detail['applied_count'], 7)
        self.assertEqual(detail['provider'], 'unlicensed')

    def test_no_audit_when_writer_absent(self) -> None:
        _seed_attempts(self.db_path, [('a1', 'oldA', None)])
        self._adapter().ingest_mapping_envelope(
            _envelope([('oldA', 'newA')]),
            audit_meta={'id': 'x', 'actor_subject': 's', 'occurred_at': 't', 'created_at': 't'},
        )
        self.assertEqual(_audit_rows(self.db_path), [])  # no writer → no audit row

    def test_audit_failure_rolls_back_propagation(self) -> None:
        _seed_attempts(self.db_path, [('a1', 'oldA', None)])

        class _FailingAudit:
            def append_event_in_transaction(self, cursor, record):
                raise AuditWriteError('boom')

        with self.assertRaises(RekeyIngestError):
            self._adapter(audit_writer=_FailingAudit()).ingest_mapping_envelope(
                _envelope([('oldA', 'newA')]),
                audit_meta={'id': 'x', 'actor_subject': 's', 'occurred_at': 't', 'created_at': 't'},
            )
        # atomic — failed audit rolls the UPDATE back (v2 stays NULL).
        self.assertEqual(_attempts(self.db_path), [('a1', 'oldA', None)])


_FORBIDDEN_TOKENS = (
    'compute_condition_hash',
    'compute_stable_condition_hash',
    'compute_stable_hash_for_field_set',
    'build_mapping_envelope',
    'verify_mapping_envelope_integrity',
)
_ADAPTER_PATH = resolve_repo_artifact(__file__, 'src/application/platform/central_rekey_ingest_adapter.py')


class TestRekeyIngestNeverRecompute(unittest.TestCase):
    """central never-recompute — adapter neither hashes nor builds/verifies envelopes."""

    def _text(self) -> str:
        return _ADAPTER_PATH.read_text(encoding='utf-8')

    def _imports(self) -> set:
        tree = ast.parse(self._text())
        names: set = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_no_hashlib_import(self) -> None:
        self.assertNotIn('hashlib', self._imports())

    def test_no_forbidden_hash_or_envelope_tokens(self) -> None:
        text = self._text()
        offenders = [tok for tok in _FORBIDDEN_TOKENS if tok in text]
        self.assertEqual(offenders, [])

    def test_measurement_attempts_mutation_is_update_only(self) -> None:
        # The only verb against measurement_attempts is UPDATE — no DDL / INSERT /
        # DELETE (the expand migration is deferred; ingestion is verbatim re-key only).
        # Check the *runtime* SQL constants (f-strings interpolate the table name).
        all_sql = ' '.join((
            ingest_mod.UPDATE_CONDITION_HASH_V2_SQL,
            ingest_mod.CONFLICT_SCAN_SQL,
            ingest_mod.ELIGIBLE_COUNT_SQL,
            ingest_mod.ALREADY_SET_COUNT_SQL,
        ))
        for verb in ('ALTER TABLE', 'CREATE TABLE', 'DROP TABLE',
                     'INSERT INTO', 'DELETE FROM'):
            self.assertNotIn(verb, all_sql, f'{verb} must not appear in ingest SQL')
        # the one mutating statement targets condition_hash_v2 via UPDATE only.
        self.assertTrue(
            ingest_mod.UPDATE_CONDITION_HASH_V2_SQL.startswith('UPDATE "measurement_attempts"')
        )
        self.assertEqual(ingest_mod.UPDATE_CONDITION_HASH_V2_SQL.count('UPDATE'), 1)


class TestRekeyIngestAdapterPurity(unittest.TestCase):
    def test_adapter_imports_no_driver(self) -> None:
        imports = TestRekeyIngestNeverRecompute()._imports()
        for forbidden in ('psycopg', 'psycopg2', 'sqlalchemy'):
            self.assertNotIn(forbidden, imports)

    def test_connection_factory_must_be_callable(self) -> None:
        with self.assertRaises(ValueError):
            PostgresCentralRekeyIngestAdapter(connection_factory=object())  # type: ignore[arg-type]


if __name__ == '__main__':
    unittest.main()
