"""멀티챔버 P1 — 챔버 도메인 + 중앙 스키마 drift 가드.

Phase 1 범위: 순수 도메인 모델(``chamber_node``) + 중앙 read/write 포트 2개 +
중앙 SQL/JSON 스키마(chamber_nodes / chamber_heartbeat_events append-only ledger /
chamber_availability VIEW). application service / Platform API / adapter 는 Phase 2.

봉인:
  - JSON 스키마: 테이블/VIEW 존재 + append_only + reported_status allowed_values
    (offline 미포함) + VIEW no-DB-now() (offline 은 service 파생).
  - JSON↔SQL drift: ``render_ddl(load_schema())`` == 생성된 SQL 파일.
  - live SQLite: chamber_availability VIEW latest-heartbeat-wins + 0-heartbeat 챔버 노출.
  - 도메인: enum 3값 / derive offline·reported / Heartbeat OFFLINE reject / frozen 불변 /
    chamber_node.py AST 도메인 순수성.
"""
import ast
import dataclasses
import json
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))
sys.path.insert(0, str(project_root / 'scripts'))

from fcc_test_contracts.common.sqlite_connection_factory import (  # noqa: E402
    SQLITE_IN_MEMORY_DB,
    SqliteConnectionFactory,
)
from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402

from support.central_pg_sqlite_shim import create_tables_from_schema  # noqa: E402
from domain.models.chamber_node import (  # noqa: E402
    DEFAULT_HEARTBEAT_TTL_SECONDS,
    MAX_LAST_ERROR_LENGTH,
    ChamberAvailability,
    ChamberNode,
    ChamberNodeStatus,
    ChamberProgress,
    Heartbeat,
    UnavailableReason,
    derive_chamber_status,
    derive_unavailable_reason,
    redact_error_message,
)

SCHEMA_PATH = project_root / 'docs' / 'platform' / 'central_db_schema.v1.json'
DDL_PATH = resolve_repo_artifact(__file__, 'docs/platform/migrations/001_initial_central_db.sql')
MODEL_PATH = resolve_repo_artifact(__file__, 'src/domain/models/chamber_node.py')


class TestChamberSchemaTables(unittest.TestCase):
    """중앙 스키마에 챔버 레지스트리 + heartbeat ledger + availability VIEW 존재."""

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        self.tables = self.schema['tables']

    def test_chamber_nodes_registry_table_exists(self):
        nodes = self.tables['chamber_nodes']
        columns = nodes['columns']
        for name in ('id', 'chamber_id', 'name', 'base_url', 'enabled', 'heartbeat_ttl_seconds'):
            self.assertIn(name, columns, name)
        # chamber_id 는 자연 키(unique) — heartbeat ledger 의 FK 대상.
        self.assertTrue(columns['chamber_id']['unique'])
        self.assertTrue(columns['chamber_id']['required'])
        self.assertEqual(columns['heartbeat_ttl_seconds']['type'], 'integer')
        names = {idx['name'] for idx in nodes.get('indexes', [])}
        self.assertIn('ux_chamber_nodes_chamber_id', names)
        # registry 는 ledger 가 아니다 — append_only 마커가 붙으면 안 된다.
        self.assertNotIn('append_only', nodes)

    def test_heartbeat_events_is_append_only_ledger(self):
        events = self.tables['chamber_heartbeat_events']
        self.assertTrue(events.get('append_only') is True)
        columns = events['columns']
        for name in ('id', 'chamber_id', 'reported_status', 'occurred_at', 'created_at'):
            self.assertIn(name, columns, name)
            self.assertTrue(columns[name]['required'], name)
        # heartbeat 는 chamber_nodes 자연 키 + (선택) test_sessions 를 참조.
        self.assertEqual(columns['chamber_id']['references'], 'chamber_nodes.chamber_id')
        self.assertEqual(columns['session_id']['references'], 'test_sessions.id')
        self.assertFalse(columns['session_id']['required'])

    def test_reported_status_allowed_values_exclude_offline(self):
        spec = self.tables['chamber_heartbeat_events']['columns']['reported_status']
        # OFFLINE 은 저장되지 않는 파생 상태 — heartbeat 가 보고할 수 없다.
        self.assertEqual(set(spec['allowed_values']), {'idle', 'in_use'})
        self.assertNotIn('offline', spec['allowed_values'])

    def test_heartbeat_events_indexes_cover_latest_lookup(self):
        names = {idx['name'] for idx in self.tables['chamber_heartbeat_events'].get('indexes', [])}
        for required in (
            'idx_chamber_heartbeat_events_chamber_occurred',
            'idx_chamber_heartbeat_events_status_occurred',
        ):
            self.assertIn(required, names)


class TestChamberAvailabilityView(unittest.TestCase):
    """chamber_availability 는 verbatim VIEW — offline 은 DB 가 계산하지 않는다."""

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

    def test_chamber_availability_is_plain_view_not_materialized(self):
        views = self.schema.get('views', {})
        materialized = self.schema.get('materialized_views', {})
        self.assertIn('chamber_availability', views)
        self.assertNotIn('chamber_availability', materialized)

    def test_view_projects_latest_heartbeat_per_chamber(self):
        select = self.schema['views']['chamber_availability']['select']
        # latest-per-chamber = ROW_NUMBER() OVER (PARTITION BY chamber_id ORDER BY occurred_at DESC).
        self.assertIn('ROW_NUMBER() OVER', select)
        self.assertIn('PARTITION BY h.chamber_id', select)
        self.assertIn('ORDER BY h.occurred_at DESC', select)
        # 0-heartbeat 챔버도 노출 — LEFT JOIN.
        self.assertIn('LEFT JOIN', select)
        self.assertIn('chamber_heartbeat_events', select)
        self.assertIn('chamber_nodes', select)
        # ttl + last_heartbeat 를 verbatim 노출(서비스 파생 입력).
        self.assertIn('heartbeat_ttl_seconds', select)
        self.assertIn('last_heartbeat_at', select)
        self.assertIn('reported_status', select)

    def test_view_does_not_compute_offline_with_db_clock(self):
        # offline 파생은 service 의 주입 clock 책임 — VIEW 에 DB-side now() 금지.
        select = self.schema['views']['chamber_availability']['select'].lower()
        for forbidden in ('now()', 'current_timestamp', "'offline'", 'julianday', 'datetime('):
            self.assertNotIn(forbidden, select, forbidden)


class TestChamberSchemaDdlDrift(unittest.TestCase):
    """JSON 스키마 ↔ 생성된 SQL 파일 drift 0 봉인 (exporter 가 유일한 SQL 생산자)."""

    def test_generated_ddl_matches_committed_sql_file(self):
        import export_platform_central_db_ddl as exporter

        generated = exporter.render_ddl(exporter.load_schema(SCHEMA_PATH))
        committed = DDL_PATH.read_text(encoding='utf-8')
        self.assertEqual(
            committed,
            generated,
            'docs/platform/migrations/001_initial_central_db.sql 이 JSON 스키마와 어긋남 — '
            'python scripts/export_platform_central_db_ddl.py --write 로 재생성하라.',
        )

    def test_ddl_renders_chamber_objects(self):
        ddl = DDL_PATH.read_text(encoding='utf-8')
        self.assertIn('CREATE TABLE IF NOT EXISTS "chamber_nodes"', ddl)
        self.assertIn('CREATE TABLE IF NOT EXISTS "chamber_heartbeat_events"', ddl)
        self.assertIn('CREATE OR REPLACE VIEW "chamber_availability"', ddl)
        # append-only 마커 주석 + reported_status CHECK 렌더.
        self.assertIn(
            'CONSTRAINT "ck_chamber_heartbeat_events_reported_status" '
            "CHECK (\"reported_status\" IN ('idle', 'in_use'))",
            ddl,
        )


class TestChamberAvailabilityViewLiveSql(unittest.TestCase):
    """live SQLite — VIEW SELECT 본문을 실행해 latest-heartbeat-wins 의미를 봉인."""

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        self.conn = SqliteConnectionFactory(SQLITE_IN_MEMORY_DB).create()
        self.conn.row_factory = sqlite3.Row
        # ⚠️ 손으로 베낀 CREATE TABLE 이 아니라 **스키마 JSON SSOT 에서 파생**한다.
        # 옛 형상은 컬럼을 여기 그대로 적어 두었고, 프로덕션이 뷰가 참조하는 컬럼을
        # 하나 더한 순간(챔버 모드 축, 2026-08-16) 이 픽스처의 CREATE VIEW 가
        # `no such column` 으로 죽었다 — 픽스처가 옛 모양을 계속 테스트하고 있었다는
        # 뜻이다. 형제 `tests/support/central_pg_sqlite_shim` 이 같은 이유로 이미
        # 파생을 쓰고 그 사유를 자기 주석에 적고 있다.
        create_tables_from_schema(
            self.conn, ['chamber_nodes', 'chamber_heartbeat_events'],
        )
        select = self.schema['views']['chamber_availability']['select']
        self.conn.execute(f'CREATE VIEW chamber_availability AS {select}')

    def tearDown(self):
        self.conn.close()

    def _node(self, chamber_id, ttl=90):
        # 열 이름을 명시한다 — 위치 INSERT 는 컬럼이 하나 늘어나는 순간 조용히
        # 어긋나거나(값 밀림) 죽는다.
        self.conn.execute(
            'INSERT INTO chamber_nodes '
            '(id, chamber_id, name, base_url, enabled, heartbeat_ttl_seconds, '
            ' created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
            (chamber_id, chamber_id, chamber_id, f'http://{chamber_id}:8000',
             1, ttl, '2026-06-15T00:00:00Z', '2026-06-15T00:00:00Z'),
        )

    def _heartbeat(self, event_id, chamber_id, status, occurred_at, session_id=None):
        self.conn.execute(
            "INSERT INTO chamber_heartbeat_events "
            "(id, chamber_id, reported_status, session_id, occurred_at, expires_at, detail_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (event_id, chamber_id, status, session_id, occurred_at, None, None, occurred_at),
        )

    def test_latest_heartbeat_per_chamber_wins(self):
        self._node('chA')
        self._heartbeat('e1', 'chA', 'idle', '2026-06-15T10:00:00Z')
        self._heartbeat('e2', 'chA', 'in_use', '2026-06-15T10:05:00Z', session_id='s1')
        self.conn.commit()
        rows = list(self.conn.execute('SELECT * FROM chamber_availability'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['reported_status'], 'in_use')
        self.assertEqual(rows[0]['last_heartbeat_at'], '2026-06-15T10:05:00Z')
        self.assertEqual(rows[0]['session_id'], 's1')

    def test_chamber_with_zero_heartbeats_still_appears(self):
        # LEFT JOIN — 등록만 되고 heartbeat 없는 챔버도 노출(last_heartbeat_at NULL → service OFFLINE).
        self._node('chQuiet')
        self.conn.commit()
        rows = list(self.conn.execute("SELECT * FROM chamber_availability WHERE chamber_id='chQuiet'"))
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['last_heartbeat_at'])
        self.assertIsNone(rows[0]['reported_status'])
        self.assertEqual(rows[0]['heartbeat_ttl_seconds'], 90)

    def test_each_registered_chamber_yields_one_row(self):
        self._node('chA')
        self._node('chB')
        self._heartbeat('e1', 'chA', 'idle', '2026-06-15T10:00:00Z')
        self._heartbeat('e2', 'chA', 'idle', '2026-06-15T10:01:00Z')
        self._heartbeat('e3', 'chB', 'in_use', '2026-06-15T10:00:00Z')
        self.conn.commit()
        rows = list(self.conn.execute('SELECT chamber_id FROM chamber_availability ORDER BY chamber_id'))
        self.assertEqual([r['chamber_id'] for r in rows], ['chA', 'chB'])


class TestChamberNodeDomainModel(unittest.TestCase):
    """순수 도메인 모델 — enum / 파생 SSOT / 불변식."""

    def test_status_enum_has_three_values(self):
        self.assertEqual(
            {s.value for s in ChamberNodeStatus},
            {'idle', 'in_use', 'offline'},
        )
        self.assertTrue(ChamberNodeStatus.IDLE.is_reportable)
        self.assertTrue(ChamberNodeStatus.IN_USE.is_reportable)
        self.assertFalse(ChamberNodeStatus.OFFLINE.is_reportable)

    def test_models_are_frozen(self):
        node = ChamberNode(chamber_id='chA', name='A', base_url='http://a:8000')
        with self.assertRaises(dataclasses.FrozenInstanceError):
            node.name = 'B'  # type: ignore[misc]
        hb = Heartbeat(
            chamber_id='chA',
            reported_status=ChamberNodeStatus.IDLE,
            occurred_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            hb.chamber_id = 'chB'  # type: ignore[misc]

    def test_node_default_ttl_from_domain_constant(self):
        node = ChamberNode(chamber_id='chA', name='A', base_url='http://a:8000')
        self.assertEqual(node.heartbeat_ttl_seconds, DEFAULT_HEARTBEAT_TTL_SECONDS)
        self.assertTrue(node.enabled)

    def test_heartbeat_rejects_offline_reported_status(self):
        # OFFLINE 은 보고 불가 — 파생 전용.
        with self.assertRaises(ValueError):
            Heartbeat(
                chamber_id='chA',
                reported_status=ChamberNodeStatus.OFFLINE,
                occurred_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
            )

    def test_derive_status_offline_when_no_heartbeat(self):
        now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(
            derive_chamber_status(reported_status=None, last_heartbeat_at=None, now=now),
            ChamberNodeStatus.OFFLINE,
        )

    def test_derive_status_offline_when_stale(self):
        now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
        stale = now - timedelta(seconds=DEFAULT_HEARTBEAT_TTL_SECONDS + 1)
        self.assertEqual(
            derive_chamber_status(
                reported_status=ChamberNodeStatus.IN_USE,
                last_heartbeat_at=stale,
                now=now,
            ),
            ChamberNodeStatus.OFFLINE,
        )

    def test_derive_status_reports_fresh_status(self):
        now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
        fresh = now - timedelta(seconds=DEFAULT_HEARTBEAT_TTL_SECONDS - 1)
        self.assertEqual(
            derive_chamber_status(
                reported_status=ChamberNodeStatus.IN_USE,
                last_heartbeat_at=fresh,
                now=now,
            ),
            ChamberNodeStatus.IN_USE,
        )

    def test_availability_effective_status_delegates_to_derive(self):
        now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
        fresh = ChamberAvailability(
            chamber_id='chA', name='A', base_url='http://a:8000', enabled=True,
            reported_status=ChamberNodeStatus.IDLE,
            last_heartbeat_at=now - timedelta(seconds=10),
        )
        self.assertEqual(fresh.effective_status(now), ChamberNodeStatus.IDLE)
        quiet = ChamberAvailability(
            chamber_id='chQ', name='Q', base_url='http://q:8000', enabled=True,
            reported_status=None, last_heartbeat_at=None,
        )
        self.assertEqual(quiet.effective_status(now), ChamberNodeStatus.OFFLINE)


class TestChamberNodeDomainPurity(unittest.TestCase):
    """chamber_node.py 는 순수 — infrastructure/외부 lib import 0 (AST)."""

    FORBIDDEN = ('infrastructure', 'pyvisa', 'openpyxl', 'pandas', 'PySide6')

    def test_no_forbidden_imports(self):
        tree = ast.parse(MODEL_PATH.read_text(encoding='utf-8'))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.split('.')[0] == f or alias.name.startswith(f + '.')
                           for f in self.FORBIDDEN):
                        offenders.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ''
                if any(mod.split('.')[0] == f or mod.startswith(f + '.') for f in self.FORBIDDEN):
                    offenders.append(mod)
        self.assertEqual(offenders, [], f'chamber_node.py 도메인 순수성 위반: {offenders}')


class TestChamberProgressHeartbeatCarried(unittest.TestCase):
    """C1 — heartbeat-carried progress 도메인 값 객체 + Heartbeat 불변식."""

    def test_progress_from_raw_normalizes_types(self):
        progress = ChamberProgress.from_raw(
            {'is_running': 1, 'completed': '3', 'total': '10', 'ratio': '0.3'}
        )
        self.assertEqual(progress.as_dict(), {
            'is_running': True, 'completed': 3, 'total': 10, 'ratio': 0.3,
        })

    def test_progress_from_raw_empty_or_none_is_none(self):
        self.assertIsNone(ChamberProgress.from_raw(None))
        self.assertIsNone(ChamberProgress.from_raw({}))

    def test_progress_from_raw_invalid_field_values_degrade_to_none(self):
        # 유효 JSON object 지만 필드 값 타입이 깨진 경우(coercion 불가)는 전체를
        # None 으로 degrade — freshness probe 가 비정상 ledger 값에 500 으로 터지지
        # 않도록 한다(정규화 SSOT 는 도메인 ChamberProgress 단일 소유).
        for bad in (
            {'is_running': True, 'completed': 'x', 'total': 10, 'ratio': 0.3},
            {'is_running': True, 'completed': 1, 'total': [1, 2], 'ratio': 0.3},
            {'is_running': True, 'completed': 1, 'total': 2, 'ratio': {'a': 1}},
            {'completed': object()},
            {'ratio': 'not-a-float'},
        ):
            self.assertIsNone(
                ChamberProgress.from_raw(bad),
                msg=f'깨진 필드 값은 None 으로 degrade 해야 함: {bad!r}',
            )

    def test_progress_from_raw_falsy_collections_coerce_to_zero(self):
        # 빈 collection 은 falsy 이므로 0/0.0 으로 강제(raise 아님) — 명시 회귀 고정.
        progress = ChamberProgress.from_raw(
            {'is_running': False, 'completed': [], 'total': 0, 'ratio': {}}
        )
        self.assertEqual(progress.as_dict(), {
            'is_running': False, 'completed': 0, 'total': 0, 'ratio': 0.0,
        })

    def test_progress_from_raw_non_dict_is_none(self):
        # dict 아닌 입력(방어적)도 raise 없이 None.
        for bad in ('string', 5, [1, 2], object()):
            self.assertIsNone(ChamberProgress.from_raw(bad))  # type: ignore[arg-type]

    def test_progress_zero_snapshot(self):
        self.assertEqual(
            ChamberProgress.zero().as_dict(),
            {'is_running': False, 'completed': 0, 'total': 0, 'ratio': 0.0},
        )

    def test_progress_is_frozen(self):
        progress = ChamberProgress.zero()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            progress.completed = 5  # type: ignore[misc]

    def test_heartbeat_carries_progress_only_when_in_use(self):
        # in_use heartbeat 는 progress 운반 OK.
        hb = Heartbeat(
            chamber_id='chA',
            reported_status=ChamberNodeStatus.IN_USE,
            occurred_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
            progress=ChamberProgress(is_running=True, completed=2, total=4, ratio=0.5),
        )
        self.assertIsNotNone(hb.progress)

    def test_heartbeat_rejects_progress_on_idle(self):
        # idle 노드는 진행 중 측정이 없음 — progress 운반 금지(C1 불변식).
        with self.assertRaises(ValueError):
            Heartbeat(
                chamber_id='chA',
                reported_status=ChamberNodeStatus.IDLE,
                occurred_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
                progress=ChamberProgress.zero(),
            )

    def test_idle_heartbeat_without_progress_is_valid(self):
        hb = Heartbeat(
            chamber_id='chA',
            reported_status=ChamberNodeStatus.IDLE,
            occurred_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
        self.assertIsNone(hb.progress)

    def test_availability_carries_progress_field(self):
        avail = ChamberAvailability(
            chamber_id='chA', name='A', base_url='http://a:8000', enabled=True,
            reported_status=ChamberNodeStatus.IN_USE,
            last_heartbeat_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
            progress=ChamberProgress(is_running=True, completed=1, total=2, ratio=0.5),
        )
        self.assertEqual(avail.progress.completed, 1)


class TestChamberHeartbeatProgressSchema(unittest.TestCase):
    """C1 — progress_json ledger 컬럼 + availability VIEW verbatim 노출."""

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

    def test_heartbeat_events_has_progress_json_column(self):
        columns = self.schema['tables']['chamber_heartbeat_events']['columns']
        self.assertIn('progress_json', columns)
        self.assertEqual(columns['progress_json']['type'], 'json')
        # in_use 일 때만 운반 — required 가 아니다(idle/zero-heartbeat 는 NULL).
        self.assertFalse(columns['progress_json']['required'])

    def test_view_exposes_progress_json_verbatim(self):
        select = self.schema['views']['chamber_availability']['select']
        self.assertIn('progress_json', select)
        self.assertIn('h.progress_json', select)
        self.assertIn('latest.progress_json', select)

    def test_ddl_renders_progress_json(self):
        ddl = DDL_PATH.read_text(encoding='utf-8')
        self.assertIn('"progress_json" JSONB', ddl)


class TestChamberAvailabilityViewProgressLiveSql(unittest.TestCase):
    """live SQLite — VIEW 가 최신 heartbeat 의 progress_json 을 verbatim 통과."""

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        self.conn = SqliteConnectionFactory(SQLITE_IN_MEMORY_DB).create()
        self.conn.row_factory = sqlite3.Row
        # ⚠️ 손으로 베낀 CREATE TABLE 이 아니라 **스키마 JSON SSOT 에서 파생**한다.
        # 옛 형상은 컬럼을 여기 그대로 적어 두었고, 프로덕션이 뷰가 참조하는 컬럼을
        # 하나 더한 순간(챔버 모드 축, 2026-08-16) 이 픽스처의 CREATE VIEW 가
        # `no such column` 으로 죽었다 — 픽스처가 옛 모양을 계속 테스트하고 있었다는
        # 뜻이다. 형제 `tests/support/central_pg_sqlite_shim` 이 같은 이유로 이미
        # 파생을 쓰고 그 사유를 자기 주석에 적고 있다.
        create_tables_from_schema(
            self.conn, ['chamber_nodes', 'chamber_heartbeat_events'],
        )
        select = self.schema['views']['chamber_availability']['select']
        self.conn.execute(f'CREATE VIEW chamber_availability AS {select}')

    def tearDown(self):
        self.conn.close()

    def test_latest_heartbeat_progress_json_is_exposed(self):
        self.conn.execute(
            "INSERT INTO chamber_nodes (id, chamber_id, name, base_url, enabled, "
            "heartbeat_ttl_seconds, created_at, updated_at) "
            "VALUES ('chA','chA','A','http://a:8000',1,90,'t','t')"
        )
        self.conn.execute(
            "INSERT INTO chamber_heartbeat_events "
            "(id, chamber_id, reported_status, occurred_at, progress_json, created_at) "
            "VALUES ('e1','chA','in_use','2026-06-17T10:05:00Z',"
            "'{\"is_running\": true, \"completed\": 3, \"total\": 10, \"ratio\": 0.3}',"
            "'2026-06-17T10:05:00Z')"
        )
        self.conn.commit()
        row = list(self.conn.execute('SELECT progress_json FROM chamber_availability'))[0]
        self.assertIn('"completed": 3', row['progress_json'])


class TestUnavailableReasonDomain(unittest.TestCase):
    """M2 — unavailable_reason 파생 SSOT + redaction (순수 도메인)."""

    NOW = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)

    def test_enum_has_four_values(self):
        self.assertEqual(
            {r.value for r in UnavailableReason},
            {'heartbeat_timeout', 'disabled', 'never_seen', 'unknown'},
        )

    def test_disabled_takes_precedence_over_freshness(self):
        # enabled=False → disabled, status 와 직교 (heartbeat 신선해도 disabled).
        fresh = self.NOW - timedelta(seconds=5)
        self.assertEqual(
            derive_unavailable_reason(
                reported_status=ChamberNodeStatus.IDLE,
                last_heartbeat_at=fresh, now=self.NOW, enabled=False,
            ),
            UnavailableReason.DISABLED,
        )
        # 같은 입력에서 status 는 enabled 미고려 → idle 유지 (직교 봉인).
        self.assertEqual(
            derive_chamber_status(
                reported_status=ChamberNodeStatus.IDLE,
                last_heartbeat_at=fresh, now=self.NOW,
            ),
            ChamberNodeStatus.IDLE,
        )

    def test_never_seen_when_enabled_and_no_heartbeat(self):
        self.assertEqual(
            derive_unavailable_reason(
                reported_status=None, last_heartbeat_at=None,
                now=self.NOW, enabled=True,
            ),
            UnavailableReason.NEVER_SEEN,
        )

    def test_heartbeat_timeout_when_stale(self):
        stale = self.NOW - timedelta(seconds=DEFAULT_HEARTBEAT_TTL_SECONDS + 1)
        self.assertEqual(
            derive_unavailable_reason(
                reported_status=ChamberNodeStatus.IN_USE,
                last_heartbeat_at=stale, now=self.NOW, enabled=True,
            ),
            UnavailableReason.HEARTBEAT_TIMEOUT,
        )

    def test_unknown_when_fresh_but_unclassifiable_status(self):
        fresh = self.NOW - timedelta(seconds=5)
        self.assertEqual(
            derive_unavailable_reason(
                reported_status=None, last_heartbeat_at=fresh,
                now=self.NOW, enabled=True,
            ),
            UnavailableReason.UNKNOWN,
        )

    def test_none_when_usable(self):
        fresh = self.NOW - timedelta(seconds=5)
        for status in (ChamberNodeStatus.IDLE, ChamberNodeStatus.IN_USE):
            self.assertIsNone(
                derive_unavailable_reason(
                    reported_status=status, last_heartbeat_at=fresh,
                    now=self.NOW, enabled=True,
                ),
                msg=status,
            )

    def test_availability_method_delegates(self):
        fresh = self.NOW - timedelta(seconds=5)
        avail = ChamberAvailability(
            chamber_id='a', name='A', base_url='http://a:8000', enabled=False,
            reported_status=ChamberNodeStatus.IDLE, last_heartbeat_at=fresh,
        )
        self.assertEqual(avail.unavailable_reason(self.NOW), UnavailableReason.DISABLED)

    def test_redact_strips_url_path_token_device(self):
        redacted = redact_error_message(
            "connect to http://10.0.0.5:8000/x failed token=abcdef123456789 "
            "at C:\\secrets\\key.pem via GPIB0::18::INSTR"
        )
        self.assertNotIn('http://', redacted)
        self.assertNotIn('C:\\secrets', redacted)
        self.assertNotIn('GPIB0::18::INSTR', redacted)
        self.assertNotIn('abcdef123456789', redacted)
        self.assertIn('[redacted', redacted)

    def test_redact_collapses_and_caps_length(self):
        long = 'error ' * 200
        redacted = redact_error_message(long)
        self.assertLessEqual(len(redacted), MAX_LAST_ERROR_LENGTH)

    def test_redact_none_and_empty(self):
        self.assertIsNone(redact_error_message(None))
        self.assertIsNone(redact_error_message('   '))


class TestChamberLastErrorSchema(unittest.TestCase):
    """M2 — last_error_json ledger 컬럼 + VIEW verbatim 노출 + DDL."""

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

    def test_heartbeat_events_has_last_error_json_column(self):
        columns = self.schema['tables']['chamber_heartbeat_events']['columns']
        self.assertIn('last_error_json', columns)
        self.assertEqual(columns['last_error_json']['type'], 'json')
        self.assertFalse(columns['last_error_json']['required'])

    def test_view_exposes_last_error_json_verbatim(self):
        select = self.schema['views']['chamber_availability']['select']
        self.assertIn('h.last_error_json', select)
        self.assertIn('latest.last_error_json', select)

    def test_view_still_has_no_db_clock(self):
        # M2 added no DB-side now()/offline computation — reason is service-derived.
        select = self.schema['views']['chamber_availability']['select'].lower()
        for forbidden in ('now()', 'current_timestamp', "'offline'", "'disabled'"):
            self.assertNotIn(forbidden, select, forbidden)

    def test_ddl_renders_last_error_json(self):
        ddl = DDL_PATH.read_text(encoding='utf-8')
        self.assertIn('"last_error_json" JSONB', ddl)


# ── Additive upgrade-path safety (Codex P0, 2026-06-20) ───────────────────────
# Pre-existing central DBs created before C1 progress_json / M2 last_error_json
# must still receive those columns. CREATE TABLE IF NOT EXISTS skips an existing
# table (column NOT added), yet the CREATE OR REPLACE chamber_availability VIEW
# always references them — so the single committed migration MUST carry an
# idempotent ALTER ... ADD COLUMN IF NOT EXISTS BEFORE the view is (re)created.

import re  # noqa: E402

# Columns added after their table's initial shape, **derived** from the schema
# ``added_in`` markers.
#
# ⚠️ 예전에는 이 목록이 손으로 적혀 있었고 바로 위 docstring 은 이미 *"no hardcoded
# list"* 라고 주장하고 있었다 — 주장과 코드가 어긋난 채였다. 챔버 모드 축(2026-08-16)이
# **다른 테이블에** additive 컬럼을 하나 더하자 그 사본이 곧바로 red 가 됐고, 그것이
# 이 파생의 근거다: 목록을 손으로 유지하면 다음 컬럼이 또 여기서 넘어진다.
def _additive_columns(schema, table: str) -> tuple[str, ...]:
    return tuple(
        name for name, spec in schema['tables'][table]['columns'].items()
        if spec.get('added_in')
    )


def _all_additive_columns(schema) -> tuple[tuple[str, str], ...]:
    """(table, column) for every ``added_in`` column in the schema."""
    return tuple(
        (table_name, column)
        for table_name, table in schema['tables'].items()
        for column, spec in table['columns'].items()
        if spec.get('added_in')
    )

# PostgreSQL-native idempotent ADD COLUMN parse — (table, column, type).
_ALTER_ADD_RE = re.compile(
    r'ALTER TABLE "(?P<table>[^"]+)" ADD COLUMN IF NOT EXISTS '
    r'"(?P<column>[^"]+)" (?P<type>[A-Z]+);'
)


class TestChamberAdditiveUpgradeDdl(unittest.TestCase):
    """The generated migration carries idempotent additive ALTERs, ordered before
    the views, derived from the schema ``added_in`` markers (no hardcoded list)."""

    def setUp(self):
        self.ddl = DDL_PATH.read_text(encoding='utf-8')
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

    def test_schema_marks_post_initial_columns_additive(self):
        additive = _all_additive_columns(self.schema)
        self.assertTrue(additive, 'schema declares no additive column at all')
        for table_name, name in additive:
            columns = self.schema['tables'][table_name]['columns']
            self.assertTrue(columns[name].get('added_in'), name)
            # additive columns MUST stay nullable — a NOT NULL ADD COLUMN breaks
            # on existing rows.
            self.assertFalse(columns[name]['required'], name)

    def test_ddl_emits_idempotent_add_column_for_each_additive_column(self):
        for table_name, name in _all_additive_columns(self.schema):
            self.assertIn(
                f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS "{name}" ',
                self.ddl,
                f'{table_name}.{name}',
            )

    def test_additive_alters_precede_chamber_availability_view(self):
        # The whole point: the column must exist before the view that selects it is
        # (re)created when this migration is re-run on an older DB.
        alter_idx = self.ddl.index('ADD COLUMN IF NOT EXISTS "last_error_json"')
        view_idx = self.ddl.index('CREATE OR REPLACE VIEW "chamber_availability"')
        self.assertLess(alter_idx, view_idx)

    def test_no_additive_marker_yields_no_section(self):
        # A schema with zero added_in columns renders no additive section (the
        # change is byte-identical for tables that never evolved).
        import export_platform_central_db_ddl as exporter

        bare = {'tables': {
            't': {'columns': {'id': {'type': 'uuid', 'required': True}}},
        }}
        self.assertEqual(exporter._render_additive_upgrades(bare['tables']), [])

    def test_additive_required_column_is_rejected(self):
        # A NOT NULL additive column is a migration hazard (breaks on existing
        # rows) — the exporter refuses to render it.
        import export_platform_central_db_ddl as exporter

        bad = {'t': {'columns': {
            'c': {'type': 'json', 'required': True, 'added_in': 'x'},
        }}}
        with self.assertRaises(ValueError):
            exporter._render_additive_upgrades(bad)


class TestChamberAdditiveUpgradeOnOldDb(unittest.TestCase):
    """Apply the migration's additive ALTERs to an OLD chamber DB shape (no
    progress_json / last_error_json) and prove the upgrade is safe + idempotent +
    that the availability VIEW can then select the new columns."""

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        self.ddl = DDL_PATH.read_text(encoding='utf-8')
        self.conn = SqliteConnectionFactory(SQLITE_IN_MEMORY_DB).create()
        self.conn.row_factory = sqlite3.Row
        # OLD shape: chamber_heartbeat_events predating C1/M2 — no progress_json,
        # no last_error_json. A legacy heartbeat row already exists.
        self.conn.executescript(
            """
            CREATE TABLE chamber_nodes (
                id TEXT PRIMARY KEY, chamber_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                base_url TEXT NOT NULL, enabled INTEGER NOT NULL,
                heartbeat_ttl_seconds INTEGER NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE chamber_heartbeat_events (
                id TEXT PRIMARY KEY, chamber_id TEXT NOT NULL, reported_status TEXT NOT NULL,
                session_id TEXT, occurred_at TEXT NOT NULL, expires_at TEXT,
                detail_json TEXT, created_at TEXT NOT NULL
            );
            INSERT INTO chamber_nodes VALUES
                ('chOld','chOld','Old','http://old:8000',1,90,'t','t');
            INSERT INTO chamber_heartbeat_events
                (id, chamber_id, reported_status, occurred_at, created_at)
                VALUES ('e0','chOld','idle','2026-06-15T10:00:00Z','2026-06-15T10:00:00Z');
            """
        )

    def tearDown(self):
        self.conn.close()

    def _existing_columns(self, table):
        return {r['name'] for r in self.conn.execute(f'PRAGMA table_info({table})')}

    def _table_exists(self, table) -> bool:
        rows = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
        ).fetchall()
        return bool(rows)

    def _apply_additive_alters(self):
        """Apply the DDL's ``ADD COLUMN IF NOT EXISTS`` statements with SQLite IF-NOT-
        EXISTS semantics (SQLite lacks the clause; the PostgreSQL target has it
        natively). Returns how many columns were actually added — the second call
        returns 0, proving idempotency."""
        added = 0
        for match in _ALTER_ADD_RE.finditer(self.ddl):
            table, column, db_type = match.group('table', 'column', 'type')
            # ⚠️ This fixture builds a CHAMBER-only legacy DB, but the DDL's additive
            # section covers every table in the schema (migration 026 adds columns to
            # `users`). A table this DB never had is not this axis's business — on the
            # real target the CREATE TABLE above it already made the table, and here
            # skipping it keeps the chamber axis measuring the chamber axis.
            if not self._table_exists(table):
                continue
            if column in self._existing_columns(table):
                continue  # IF NOT EXISTS — skip already-present column
            self.conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {db_type}')
            added += 1
        return added

    def test_old_db_lacks_additive_columns_before_upgrade(self):
        cols = self._existing_columns('chamber_heartbeat_events')
        self.assertNotIn('progress_json', cols)
        self.assertNotIn('last_error_json', cols)

    def test_apply_adds_columns_then_view_selects_them(self):
        self._apply_additive_alters()
        # Same scoping as test_apply_is_idempotent: only tables this legacy fixture
        # actually has. A table it never had has nothing to upgrade.
        checked = 0
        for table_name, name in _all_additive_columns(self.schema):
            if not self._table_exists(table_name):
                continue
            self.assertIn(name, self._existing_columns(table_name), f'{table_name}.{name}')
            checked += 1
        self.assertTrue(checked, 'this fixture has no additive column to check')
        # The availability VIEW (which references last_error_json/progress_json and,
        # since 2026-08-16, chamber_nodes.accepts_web_sessions) can now be created and
        # selected — the legacy row surfaces with NULL columns. ⚠️ That is exactly why
        # a view-referenced column MUST carry ``added_in``: without it the ALTER is not
        # rendered and this CREATE VIEW dies with `no such column` on an old DB.
        select = self.schema['views']['chamber_availability']['select']
        self.conn.execute(f'CREATE VIEW chamber_availability AS {select}')
        self.conn.commit()
        rows = list(self.conn.execute(
            "SELECT last_error_json, progress_json FROM chamber_availability "
            "WHERE chamber_id='chOld'"
        ))
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['last_error_json'])
        self.assertIsNone(rows[0]['progress_json'])

    def test_apply_is_idempotent(self):
        first = self._apply_additive_alters()
        # ⚠️ Expected count is derived AGAINST THIS FIXTURE'S TABLES, not against the
        # whole schema. This DB is a CHAMBER-era legacy snapshot; the schema's additive
        # section also covers tables it never had (migration 026 adds columns to
        # `users`). Counting those would make this chamber axis fail every time an
        # unrelated table gains an additive column — and "fix it by widening the
        # fixture" would quietly turn a focused axis into a whole-schema one.
        expected = [
            (table, column) for table, column in _all_additive_columns(self.schema)
            if self._table_exists(table)
        ]
        self.assertTrue(expected, 'this fixture has no additive column to apply')
        self.assertEqual(
            first, len(expected),
            'every additive column on a table this DB HAS must be applied to it',
        )
        # Re-running the same migration adds nothing (IF NOT EXISTS) — additive-safe.
        second = self._apply_additive_alters()
        self.assertEqual(second, 0)

    def test_legacy_row_preserved_after_upgrade(self):
        self._apply_additive_alters()
        row = list(self.conn.execute(
            "SELECT reported_status FROM chamber_heartbeat_events WHERE id='e0'"
        ))[0]
        # ADD COLUMN does not touch existing data.
        self.assertEqual(row['reported_status'], 'idle')


if __name__ == '__main__':
    unittest.main()
