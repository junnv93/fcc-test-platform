"""P5-C — project report-sessions read contract + routing metadata."""
from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fcc_test_contracts.common.sqlite_connection_factory import SqliteConnectionFactory  # noqa: E402
from support.central_pg_sqlite_shim import (  # noqa: E402
    AdoptedQmarkConnection,
    create_central_view,
    create_tables_from_schema,
)

#: 이 파일의 픽스처가 세우는 중앙 테이블. 뷰가 이 표들 위에 서므로 **집합만** 여기서
#: 고르고 각 표의 컬럼은 고르지 않는다 — 컬럼을 고르는 순간 그것이 손-복사다.
_FIXTURE_TABLES = (
    "providers",
    "test_sessions",
    "measurement_attempts",
    "chamber_nodes",
    "chamber_heartbeat_events",
)


def _central_fixture():
    """중앙 스키마 JSON SSOT 에서 **전부** 파생한 SQLite 픽스처.

    ⚠️ 테이블 DDL 과 뷰 SELECT 중 **하나만** 파생하면 반쪽이고, 그 반쪽이 실제로
    2026-08-26 에 ``no such column: provider_id`` 로 깨졌다: 뷰는 SSOT 에서 읽는데
    그 뷰가 서는 ``measurement_attempts`` DDL 은 손으로 적혀 있어, 프로덕션 뷰가
    ``provider_id``/``status`` 를 참조하기 시작하자 픽스처만 옛 모양에 남았다.
    같은 사고가 2026-08-16 에 ``chamber_nodes.accepts_web_sessions`` 로 한 번 났고
    그때는 컬럼을 손으로 더해 봉합했다 — 그래서 다시 났다.
    """
    conn = SqliteConnectionFactory(":memory:").create()
    create_tables_from_schema(conn, _FIXTURE_TABLES)
    create_central_view(conn, "materialized_views", "coverage_by_condition_hash")
    create_central_view(conn, "views", "chamber_availability")
    return conn

from fcc_test_contracts.common.access_policy import ApiAccessPolicy, ApiPrincipal  # noqa: E402
from fcc_test_kernel.application.central_contract.api_contracts import (  # noqa: E402
    PLATFORM_API_OPERATIONS,
    PLATFORM_API_PERMISSIONS,
    PLATFORM_API_ROUTES,
    PLATFORM_API_SCHEMAS,
)
from fcc_test_platform.application.central_read_service import CentralReadService  # noqa: E402
from fcc_test_platform.application.central_read_adapter import (  # noqa: E402
    PostgresCentralReadAdapter,
    REPORT_SESSION_COLUMNS,
    REPORT_SESSIONS_QUERY_SQL,
)
from fcc_test_platform.api.platform_routes import PlatformApiAdapter  # noqa: E402


class _FakeCentralReadPort:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def read_project_coverage(self, project_id, **_kwargs):  # pragma: no cover
        raise NotImplementedError

    def read_active_claims(self, project_id, **_kwargs):  # pragma: no cover
        raise NotImplementedError

    def read_sync_status(self, project_id):  # pragma: no cover
        raise NotImplementedError

    def read_project_report_sessions(self, project_id):
        self.calls.append(project_id)
        return list(self.rows)


class _FakeReadService:
    def __init__(self):
        self.calls = []

    def project_report_sessions(self, project_id):
        self.calls.append(project_id)
        return [{'project_id': project_id, 'submit_session_id': 7}]


class TestContractDeclares(unittest.TestCase):
    def test_operation_route_permission_schema(self):
        self.assertIn('list_project_report_sessions', PLATFORM_API_OPERATIONS)
        self.assertEqual(
            PLATFORM_API_ROUTES['list_project_report_sessions'],
            ('GET', '/platform/projects/{project_id}/report-sessions'),
        )
        self.assertEqual(
            PLATFORM_API_PERMISSIONS['list_project_report_sessions'], 'platform:read',
        )
        self.assertIn('ProjectReportSessionList', PLATFORM_API_SCHEMAS)
        self.assertIn('ProjectReportSessionEnvelope', PLATFORM_API_SCHEMAS)

    def test_reuses_platform_read(self):
        self.assertEqual(
            PLATFORM_API_PERMISSIONS['list_project_report_sessions'],
            PLATFORM_API_PERMISSIONS['get_project_coverage'],
        )


class TestCentralReadService(unittest.TestCase):
    def test_groups_coverage_rows_and_exposes_node_local_submit_id(self):
        project_id = str(uuid.uuid4())
        port = _FakeCentralReadPort([
            {
                'project_id': project_id,
                'session_id': 'central-session-uuid',
                'provider_session_id': '42',
                'technology': 'BLE',
                'condition_hash': 'h2',
                'latest_measured_at': '2026-07-03T02:00:00Z',
                'latest_verdict': 'PASS',
                'node_id': 'provider-a',
                'node_name': 'Provider A',
                'node_base_url': 'http://node-a:8000',
            },
            {
                'project_id': project_id,
                'session_id': 'central-session-uuid',
                'provider_session_id': '42',
                'technology': 'BT',
                'condition_hash': 'h1',
                'latest_measured_at': '2026-07-03T01:00:00Z',
                'latest_verdict': 'PASS',
                'node_id': 'provider-a',
                'node_name': 'Provider A',
                'node_base_url': 'http://node-a:8000',
            },
            {
                'project_id': project_id,
                'session_id': 'central-unsubmittable',
                'provider_session_id': 'not-local-int',
                'technology': 'UNII',
                'condition_hash': 'h3',
                'latest_measured_at': '2026-07-03T03:00:00Z',
                'latest_verdict': 'PASS',
                'node_id': 'provider-b',
                'node_name': 'Provider B',
                'node_base_url': 'http://node-b:8000',
            },
        ])
        result = CentralReadService(port).project_report_sessions(project_id)
        self.assertEqual(port.calls, [project_id])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['submit_session_id'], 42)
        self.assertEqual(result[0]['node_base_url'], 'http://node-a:8000')
        self.assertEqual(result[0]['technologies'], ['BLE', 'BT'])
        self.assertEqual(result[0]['completed_conditions'], 2)
        self.assertNotIn('session_id', result[0])

    def test_sql_does_not_depend_on_latest_chamber_heartbeat(self):
        db = _central_fixture()
        try:
            db.execute(
                'INSERT INTO providers (id, provider_id, product_line, base_url) '
                'VALUES (?, ?, ?, ?)',
                ('provider-uuid', 'provider-a', 'Provider A', 'http://node-a:8000'),
            )
            db.execute(
                'INSERT INTO test_sessions '
                '(id, provider_id, provider_session_id, project_id) VALUES (?, ?, ?, ?)',
                ('completed-session', 'provider-uuid', '42', 'project-1'),
            )
            db.execute(
                'INSERT INTO measurement_attempts '
                '(project_id, provider_id, session_id, technology, condition_hash, '
                ' operator, measured_at, verdict, attempt_number, is_latest, status) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                ('project-1', 'provider-uuid', 'completed-session', 'BLE', 'h1',
                 'eng-1', '2026-07-03T01:00:00Z', 'PASS', 1, 1, 'completed'),
            )
            db.execute(
                'INSERT INTO chamber_nodes '
                '(chamber_id, name, base_url, enabled, heartbeat_ttl_seconds) '
                'VALUES (?, ?, ?, ?, ?)',
                ('chamber-a', 'Chamber A', 'http://node-a:8000', 1, 30),
            )
            # Latest heartbeat has moved away from the completed session. The
            # report-session SQL must still return the completed session because
            # routing comes from durable test_sessions.provider_id → providers.
            db.execute(
                'INSERT INTO chamber_heartbeat_events '
                '(chamber_id, reported_status, occurred_at, session_id) '
                'VALUES (?, ?, ?, ?)',
                ('chamber-a', 'in_use', '2026-07-03T02:00:00Z', 'next-running-session'),
            )
            db.commit()
            rows = db.execute(
                REPORT_SESSIONS_QUERY_SQL.replace('%s', '?'), ('project-1',),
            ).fetchall()
        finally:
            db.close()
        payload = [dict(zip(REPORT_SESSION_COLUMNS, row)) for row in rows]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]['provider_session_id'], '42')
        self.assertEqual(payload[0]['node_base_url'], 'http://node-a:8000')
        self.assertEqual(payload[0]['node_id'], 'provider-a')


class TestReportSessionsSqlSchemaSsot(unittest.TestCase):
    """Seal REPORT_SESSIONS_QUERY_SQL against a SQLite fixture whose central
    ``coverage_by_condition_hash`` + ``chamber_availability`` views are built
    VERBATIM from the schema SSOT SELECTs — so a central column rename or a
    regression back to the non-durable ``chamber_availability`` join is caught
    here, not silently in production (Codex P5-C review requirement)."""

    def _fixture(self):
        # ⚠️ 예전에는 이 자리에 손으로 쓴 CREATE TABLE 다섯 개가 있었고, 그 위에만
        # 뷰를 SSOT 에서 파생했다. 그 **반쪽 파생**이 정확히 두 번 깨졌다:
        # 2026-08-16 chamber_nodes.accepts_web_sessions, 2026-08-26
        # measurement_attempts.provider_id/status. 이제 표도 뷰도 같은 SSOT 에서 온다.
        return _central_fixture()

    def test_completed_session_survives_idle_and_moved_heartbeat(self):
        project_id = str(uuid.uuid4())
        completed = "11111111-1111-1111-1111-111111111111"
        conn = self._fixture()
        try:
            # ⚠️ 명시 컬럼 INSERT 다. 위치 기반 ``VALUES (?, ?, ?, ?)`` 는 SSOT 가 컬럼을
            # 하나 더하는 순간 조용히 어긋나므로(실측: providers 는 선언 10 컬럼) 손으로
            # 베낀 DDL 과 같은 결함 계열이다.
            conn.execute(
                "INSERT INTO providers (id, provider_id, product_line, base_url) "
                "VALUES (?, ?, ?, ?)",
                ("prov-uuid", "provider-a", "Provider A", "http://node-a:8000"),
            )
            conn.execute(
                "INSERT INTO test_sessions "
                "(id, provider_id, provider_session_id, project_id) VALUES (?, ?, ?, ?)",
                (completed, "prov-uuid", "42", project_id),
            )
            conn.execute(
                "INSERT INTO measurement_attempts "
                "(project_id, provider_id, technology, condition_hash, session_id, "
                " operator, measured_at, verdict, attempt_number, is_latest, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (project_id, "prov-uuid", "BLE", "h1", completed, "eng-1",
                 "2026-07-03T01:00:00Z", "PASS", 1, 1, "completed"),
            )
            conn.execute(
                'INSERT INTO chamber_nodes (chamber_id, name, base_url, enabled, '
                'heartbeat_ttl_seconds) VALUES (?, ?, ?, ?, ?)',
                ("chamber-a", "Chamber A", "http://node-a:8000", 1, 30),
            )
            # Chamber ran the completed session, THEN sent a later idle heartbeat
            # (session_id NULL). chamber_availability now reports the chamber as
            # idle → its latest session_id no longer matches `completed`.
            conn.execute(
                "INSERT INTO chamber_heartbeat_events "
                "(chamber_id, reported_status, occurred_at, expires_at, session_id, "
                " progress_json, last_error_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("chamber-a", "in_use", "2026-07-03T01:00:00Z", None, completed, None, None),
            )
            conn.execute(
                "INSERT INTO chamber_heartbeat_events "
                "(chamber_id, reported_status, occurred_at, expires_at, session_id, "
                " progress_json, last_error_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("chamber-a", "idle", "2026-07-03T02:00:00Z", None, None, None, None),
            )
            conn.commit()

            # Precondition: the live projection has DROPPED the completed session
            # (this is exactly the non-durability the durable join must survive).
            avail = conn.execute(
                'SELECT session_id FROM "chamber_availability" WHERE chamber_id = ?',
                ("chamber-a",),
            ).fetchone()
            self.assertIsNone(avail[0])

            # ``AdoptedQmarkConnection.close()`` is a no-op so the shared in-memory
            # connection survives both the adapter- and service-level reads.
            factory = lambda: AdoptedQmarkConnection(conn)
            rows = PostgresCentralReadAdapter(factory).read_project_report_sessions(project_id)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["session_id"], completed)
            self.assertEqual(rows[0]["provider_session_id"], "42")
            self.assertEqual(rows[0]["node_base_url"], "http://node-a:8000")
            self.assertEqual(rows[0]["node_id"], "provider-a")

            # End-to-end: the public envelope still surfaces the reportable
            # session and never leaks the central session UUID / condition_hash.
            envelope = CentralReadService(
                PostgresCentralReadAdapter(factory)
            ).project_report_sessions(project_id)
            self.assertEqual(len(envelope), 1)
            self.assertEqual(envelope[0]["submit_session_id"], 42)
            self.assertNotIn("session_id", envelope[0])
            self.assertNotIn("condition_hash", envelope[0])
        finally:
            conn.close()


class TestHandler(unittest.TestCase):
    def _adapter(self, principal, read_service):
        return PlatformApiAdapter(
            read_service,
            access_policy=ApiAccessPolicy(PLATFORM_API_OPERATIONS),
            principal=principal,
        )

    def test_handler_authorizes_and_delegates(self):
        service = _FakeReadService()
        adapter = self._adapter(
            ApiPrincipal.from_permissions('eng-1', ['platform:read']), service,
        )
        project_id = str(uuid.uuid4())
        self.assertEqual(
            adapter.list_project_report_sessions(project_id),
            [{'project_id': project_id, 'submit_session_id': 7}],
        )
        self.assertEqual(service.calls, [project_id])

    def test_authorization_gate(self):
        from fcc_test_platform.api.platform_routes import (
            PlatformAuthorizationError,
        )
        service = _FakeReadService()
        adapter = self._adapter(
            ApiPrincipal.from_permissions('eng-2', ['headless:read']), service,
        )
        with self.assertRaises(PlatformAuthorizationError):
            adapter.list_project_report_sessions(str(uuid.uuid4()))
        self.assertEqual(service.calls, [])


if __name__ == '__main__':
    unittest.main()
