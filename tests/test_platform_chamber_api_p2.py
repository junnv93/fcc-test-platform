"""멀티챔버 Phase 2 (2026-06-15) — chamber service + Platform API contract.

Seals the chamber read/write **adapter + service** layer (on top of the P1 domain
model + central schema) and the three new ``/platform/chambers`` endpoints:

  - GET  /platform/chambers          (platform:read)   — availability dashboard
  - POST /platform/chambers          (platform:admin)  — register a chamber node
  - POST /platform/chambers/heartbeat(platform:chamber) — node heartbeat push

Coverage:
  - read adapter SELECT-only + view/table columns cross-checked against the
    central schema SSOT (docs/platform/central_db_schema.v1.json) — no hardcoded
    column names, no project_id filter (chambers are global).
  - read through the *verbatim* chamber_availability VIEW against a real SQLite
    fixture + OFFLINE derived against an INJECTED clock (DB never computes it).
  - write adapter append-only heartbeat INSERT + idempotent registry upsert +
    frozen-exe (no module-level psycopg) AST guards.
  - heartbeat OFFLINE reject (domain Heartbeat validation flows through service).
  - new node-scoped ``platform:chamber`` token: distinct from read/admin, NOT a
    project-membership grant; authorize allowed/denied/anonymous for all three ops.
  - OpenAPI 3.1 artifact byte-identity + new operation summary/permission coverage.
  - application/platform purity (services import no infrastructure/FastAPI/SQL).
"""
from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _REPO_ROOT / 'src'
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from fcc_test_contracts.common.tree_artifacts import (
    resolve_dependency_artifact,
    resolve_repo_artifact,
)  # noqa: E402
from fcc_test_contracts.common.sqlite_connection_factory import SqliteConnectionContext  # noqa: E402

from support.central_pg_sqlite_shim import (  # noqa: E402
    QmarkConnection,
    RowcountBlindConnection,
    make_sqlite_central,
)

from fcc_test_contracts.common.access_policy import ApiAccessPolicy, ApiPrincipal  # noqa: E402
from fcc_test_kernel.application.central_contract.api_contracts import (  # noqa: E402
    PLATFORM_API_OPERATIONS,
    PLATFORM_API_PERMISSION_DESCRIPTIONS,
    PLATFORM_API_PERMISSIONS,
    PLATFORM_API_ROUTES,
    PLATFORM_API_SCHEMAS,
)
from fcc_test_platform.application.api_schema import build_platform_openapi_schema  # noqa: E402
from fcc_test_platform.application.central_chamber_read_adapter import (  # noqa: E402
    CHAMBER_AVAILABILITY_COLUMNS,
    CHAMBER_AVAILABILITY_QUERY_SQL,
    CHAMBER_NODES_QUERY_SQL,
    CHAMBER_NODE_COLUMNS,
    PostgresCentralChamberReadAdapter,
)
from fcc_test_platform.application.central_chamber_write_adapter import (  # noqa: E402
    CHAMBER_NODES_TABLE,
    HEARTBEAT_EVENT_COLUMNS,
    INSERT_HEARTBEAT_EVENT_SQL,
    UPSERT_CHAMBER_NODE_SQL,
    PostgresCentralChamberWriteAdapter,
)
from fcc_test_platform.application.central_chamber_read_service import (  # noqa: E402
    CentralChamberReadService,
)
from fcc_test_platform.application.central_chamber_write_service import (  # noqa: E402
    CentralChamberWriteService,
)
from fcc_test_kernel.domain.models.chamber_node import ChamberNodeStatus, UnavailableReason  # noqa: E402
from fcc_test_platform.domain.ports.output.central_chamber_read_port import (  # noqa: E402
    CentralChamberReadError,
    CentralChamberReadPort,
)
from fcc_test_platform.domain.ports.output.central_chamber_write_port import (  # noqa: E402
    CentralChamberWritePort,
    ChamberNotFoundError,
    ChamberWriteError,
)


_SCHEMA_JSON = _REPO_ROOT / 'docs' / 'platform' / 'central_db_schema.v1.json'
_PLATFORM_PKG = resolve_repo_artifact(__file__, 'src/application/platform')
_READ_ADAPTER_MODULE = resolve_repo_artifact(__file__, 'src/application/platform/central_chamber_read_adapter.py')
_WRITE_ADAPTER_MODULE = resolve_repo_artifact(__file__, 'src/application/platform/central_chamber_write_adapter.py')
_READ_SERVICE_MODULE = resolve_repo_artifact(__file__, 'src/application/platform/central_chamber_read_service.py')
_WRITE_SERVICE_MODULE = resolve_repo_artifact(__file__, 'src/application/platform/central_chamber_write_service.py')
_ARTIFACT = resolve_dependency_artifact('docs/api/platform-api.openapi.json')

_FORBIDDEN_IMPORT_PREFIXES = (
    'infrastructure', 'fastapi', 'sqlalchemy', 'psycopg', 'pyvisa', 'PySide6',
    'openpyxl', 'pandas',
)


def _schema() -> dict:
    return json.loads(_SCHEMA_JSON.read_text(encoding='utf-8'))


def _canonical_text(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + '\n'


# ── Real SQLite fixture — runs the *verbatim* chamber_availability VIEW ────────
# psycopg uses %s paramstyle; SQLite uses ?. The chamber view uses no PG-specific
# keywords, so the only translation is paramstyle (mirrors the read-API fixture).


def _make_sqlite_central() -> str:
    """중앙 레지스트리 SQLite 스탠드인 — **DDL 은 스키마 JSON SSOT 에서 파생**한다.

    이 함수는 예전에 컬럼을 손으로 베껴 두었고, ``central_pg_sqlite_shim`` 이 같은 것을
    또 베끼고 있었다. 프로덕션이 ``chamber_nodes`` 에 컬럼을 하나 더한 순간 **두 사본이
    모두** 옛 모양을 계속 테스트했고, 실패는 어댑터 SQL 이 실행될 때에야 드러났다(실측).
    이제 둘 다 같은 파생 헬퍼를 쓴다 — 사본이 없으면 드리프트할 것도 없다.
    """
    return make_sqlite_central()


# ── In-process fakes for service-logic + authz tests (no DB) ──────────────────


class _FakeChamberReadPort:
    def __init__(self, availability=None, nodes=None) -> None:
        self._availability = availability or []
        self._nodes = nodes or []

    def read_chamber_nodes(self) -> list:
        return list(self._nodes)

    def read_chamber_availability(self) -> list:
        return list(self._availability)


class _FakeChamberWritePort:
    def __init__(self) -> None:
        self.registered: list = []
        self.heartbeats: list = []

    def register_chamber(self, record):
        self.registered.append(dict(record))
        return dict(record)

    def append_heartbeat(self, record) -> None:
        self.heartbeats.append(dict(record))


_FIXED_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


class TestChamberReadAdapterSql(unittest.TestCase):
    """Read adapter SQL is SELECT-only + columns mirror the schema SSOT."""

    def setUp(self):
        self.schema = _schema()

    def test_read_sql_is_select_only(self):
        for sql in (CHAMBER_NODES_QUERY_SQL, CHAMBER_AVAILABILITY_QUERY_SQL):
            upper = sql.upper()
            self.assertTrue(upper.lstrip().startswith('SELECT'), sql)
            for verb in ('INSERT', 'UPDATE', 'DELETE', 'DROP', 'TRUNCATE', 'MERGE', 'REFRESH'):
                self.assertNotIn(verb, upper, f'read SQL must be SELECT-only — found {verb!r}')

    def test_no_project_id_filter(self):
        # Chambers are global infrastructure — the read must not be project-scoped.
        for sql in (CHAMBER_NODES_QUERY_SQL, CHAMBER_AVAILABILITY_QUERY_SQL):
            self.assertNotIn('project_id', sql.lower())

    def test_availability_columns_match_view_select_aliases(self):
        select = self.schema['views']['chamber_availability']['select']
        for column in CHAMBER_AVAILABILITY_COLUMNS:
            self.assertIn(column, select, column)

    def test_node_columns_subset_of_registry_table(self):
        table_columns = set(self.schema['tables']['chamber_nodes']['columns'])
        self.assertTrue(set(CHAMBER_NODE_COLUMNS) <= table_columns,
                        set(CHAMBER_NODE_COLUMNS) - table_columns)

    def test_availability_columns_include_m2_last_error_json(self):
        # M2 — the read adapter must project the diagnostics column so the read
        # service can derive last_error / last_error_at (single-read, no N+1).
        self.assertIn('last_error_json', CHAMBER_AVAILABILITY_COLUMNS)


class TestChamberWriteAdapterSql(unittest.TestCase):
    """Write adapter: append-only heartbeat INSERT + idempotent registry upsert."""

    def test_heartbeat_sql_is_append_only_insert(self):
        upper = INSERT_HEARTBEAT_EVENT_SQL.upper()
        self.assertTrue(upper.lstrip().startswith('INSERT INTO'), INSERT_HEARTBEAT_EVENT_SQL)
        for verb in ('UPDATE', 'DELETE', 'ON CONFLICT', 'DROP'):
            self.assertNotIn(verb, upper, f'heartbeat must be a plain INSERT — found {verb!r}')

    def test_registry_upsert_targets_chamber_id_conflict(self):
        upper = UPSERT_CHAMBER_NODE_SQL.upper()
        self.assertTrue(upper.lstrip().startswith('INSERT INTO'), UPSERT_CHAMBER_NODE_SQL)
        self.assertIn('ON CONFLICT ("CHAMBER_ID") DO UPDATE', upper)
        # The conflict-update must NOT touch the immutable identity/creation columns.
        self.assertNotIn('"ID" = EXCLUDED', upper)
        self.assertNotIn('"CREATED_AT" = EXCLUDED', upper)


class TestChamberAdapterPurity(unittest.TestCase):
    """frozen-exe + layer purity: no module-level psycopg / infra import."""

    def _module_imports(self, path: Path) -> set:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        names: set = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module)
        return names

    def test_no_postgres_driver_import_in_adapters(self):
        for path in (_READ_ADAPTER_MODULE, _WRITE_ADAPTER_MODULE):
            offenders = {n for n in self._module_imports(path)
                         if n.split('.')[0] in {'psycopg', 'psycopg2', 'asyncpg'}}
            self.assertEqual(set(), offenders, f'{path.name}: {offenders}')

    def test_services_are_dependency_free(self):
        for path in (_READ_SERVICE_MODULE, _WRITE_SERVICE_MODULE,
                     _READ_ADAPTER_MODULE, _WRITE_ADAPTER_MODULE):
            offenders = {
                n for n in self._module_imports(path)
                if any(n.split('.')[0] == p for p in _FORBIDDEN_IMPORT_PREFIXES)
            }
            self.assertEqual(set(), offenders, f'{path.name} impure import: {offenders}')

    def test_adapters_satisfy_ports(self):
        self.assertIsInstance(
            PostgresCentralChamberReadAdapter(lambda: None), CentralChamberReadPort,
        )
        self.assertIsInstance(
            PostgresCentralChamberWriteAdapter(lambda: None), CentralChamberWritePort,
        )


class TestChamberReadServiceDerivation(unittest.TestCase):
    """OFFLINE derivation uses the INJECTED clock — deterministic, no DB now()."""

    def _service(self, availability):
        return CentralChamberReadService(
            _FakeChamberReadPort(availability=availability), clock=lambda: _FIXED_NOW,
        )

    def test_fresh_heartbeat_reports_status(self):
        fresh = (_FIXED_NOW - timedelta(seconds=10)).isoformat()
        svc = self._service([{
            'chamber_id': 'chA', 'name': 'A', 'base_url': 'http://a:8000', 'enabled': 1,
            'heartbeat_ttl_seconds': 90, 'reported_status': 'in_use',
            'last_heartbeat_at': fresh, 'heartbeat_expires_at': None, 'session_id': 's1',
        }])
        page = svc.chamber_availability()
        item = page['items'][0]
        self.assertEqual(item['status'], 'in_use')
        self.assertEqual(item['reported_status'], 'in_use')
        self.assertTrue(item['enabled'])
        self.assertEqual(page['server_time'], _FIXED_NOW.isoformat())

    def test_stale_heartbeat_derives_offline(self):
        stale = (_FIXED_NOW - timedelta(seconds=91)).isoformat()
        svc = self._service([{
            'chamber_id': 'chA', 'name': 'A', 'base_url': 'http://a:8000', 'enabled': 1,
            'heartbeat_ttl_seconds': 90, 'reported_status': 'idle',
            'last_heartbeat_at': stale, 'heartbeat_expires_at': None, 'session_id': None,
        }])
        item = svc.chamber_availability()['items'][0]
        # Stored reported_status stays verbatim; derived status is OFFLINE.
        self.assertEqual(item['reported_status'], 'idle')
        self.assertEqual(item['status'], 'offline')

    def test_zero_heartbeat_chamber_is_offline(self):
        svc = self._service([{
            'chamber_id': 'chQ', 'name': 'Q', 'base_url': 'http://q:8000', 'enabled': 1,
            'heartbeat_ttl_seconds': 90, 'reported_status': None,
            'last_heartbeat_at': None, 'heartbeat_expires_at': None, 'session_id': None,
        }])
        item = svc.chamber_availability()['items'][0]
        self.assertIsNone(item['reported_status'])
        self.assertEqual(item['status'], 'offline')

    def test_usable_chamber_has_null_unavailable_reason_and_no_error(self):
        fresh = (_FIXED_NOW - timedelta(seconds=10)).isoformat()
        svc = self._service([{
            'chamber_id': 'chA', 'name': 'A', 'base_url': 'http://a:8000', 'enabled': 1,
            'heartbeat_ttl_seconds': 90, 'reported_status': 'idle',
            'last_heartbeat_at': fresh, 'heartbeat_expires_at': None, 'session_id': None,
        }])
        item = svc.chamber_availability()['items'][0]
        self.assertIsNone(item['unavailable_reason'])
        self.assertIsNone(item['last_error'])
        self.assertIsNone(item['last_error_at'])

    def test_disabled_overlay_is_orthogonal_to_status(self):
        # M2 — disabled but still heartbeating: status stays idle, reason=disabled.
        fresh = (_FIXED_NOW - timedelta(seconds=10)).isoformat()
        svc = self._service([{
            'chamber_id': 'chA', 'name': 'A', 'base_url': 'http://a:8000', 'enabled': 0,
            'heartbeat_ttl_seconds': 90, 'reported_status': 'idle',
            'last_heartbeat_at': fresh, 'heartbeat_expires_at': None, 'session_id': None,
        }])
        item = svc.chamber_availability()['items'][0]
        self.assertEqual(item['status'], 'idle')
        self.assertEqual(item['unavailable_reason'], 'disabled')

    def test_stale_chamber_reason_is_heartbeat_timeout(self):
        stale = (_FIXED_NOW - timedelta(seconds=91)).isoformat()
        svc = self._service([{
            'chamber_id': 'chA', 'name': 'A', 'base_url': 'http://a:8000', 'enabled': 1,
            'heartbeat_ttl_seconds': 90, 'reported_status': 'idle',
            'last_heartbeat_at': stale, 'heartbeat_expires_at': None, 'session_id': None,
        }])
        item = svc.chamber_availability()['items'][0]
        self.assertEqual(item['unavailable_reason'], 'heartbeat_timeout')

    def test_never_seen_chamber_reason(self):
        svc = self._service([{
            'chamber_id': 'chQ', 'name': 'Q', 'base_url': 'http://q:8000', 'enabled': 1,
            'heartbeat_ttl_seconds': 90, 'reported_status': None,
            'last_heartbeat_at': None, 'heartbeat_expires_at': None, 'session_id': None,
        }])
        item = svc.chamber_availability()['items'][0]
        self.assertEqual(item['unavailable_reason'], 'never_seen')

    def test_last_error_parsed_and_redacted_on_read(self):
        # Defense-in-depth: even an unredacted ledger value is redacted on read.
        fresh = (_FIXED_NOW - timedelta(seconds=10)).isoformat()
        svc = self._service([{
            'chamber_id': 'chA', 'name': 'A', 'base_url': 'http://a:8000', 'enabled': 1,
            'heartbeat_ttl_seconds': 90, 'reported_status': 'in_use',
            'last_heartbeat_at': fresh, 'heartbeat_expires_at': None, 'session_id': 's1',
            'last_error_json': json.dumps(
                {'message': 'boom at http://10.0.0.5/secret', 'occurred_at': fresh}
            ),
        }])
        item = svc.chamber_availability()['items'][0]
        self.assertIn('boom', item['last_error'])
        self.assertNotIn('http://', item['last_error'])
        self.assertEqual(item['last_error_at'], fresh)


class TestChamberWriteServiceLogic(unittest.TestCase):
    def setUp(self):
        self.port = _FakeChamberWritePort()
        self.service = CentralChamberWriteService(
            self.port, clock=lambda: _FIXED_NOW.isoformat(), id_factory=lambda: 'fixed-id',
        )

    def test_register_defaults_ttl_to_domain_constant(self):
        from fcc_test_kernel.domain.models.chamber_node import DEFAULT_HEARTBEAT_TTL_SECONDS
        env = self.service.register(chamber_id='chA', name='A', base_url='http://a:8000')
        self.assertEqual(env['heartbeat_ttl_seconds'], DEFAULT_HEARTBEAT_TTL_SECONDS)
        self.assertTrue(env['enabled'])
        self.assertEqual(self.port.registered[0]['chamber_id'], 'chA')

    def test_register_missing_field_value_error(self):
        for kwargs in (
            {'chamber_id': '', 'name': 'A', 'base_url': 'u'},
            {'chamber_id': 'c', 'name': '', 'base_url': 'u'},
            {'chamber_id': 'c', 'name': 'A', 'base_url': ''},
        ):
            with self.assertRaises(ValueError):
                self.service.register(**kwargs)

    def test_heartbeat_appends_idle(self):
        ack = self.service.heartbeat(chamber_id='chA', reported_status='idle')
        self.assertEqual(ack['reported_status'], 'idle')
        self.assertEqual(self.port.heartbeats[0]['reported_status'], 'idle')

    def test_heartbeat_offline_rejected(self):
        # OFFLINE is a derived state — a node can never report it.
        with self.assertRaises(ValueError):
            self.service.heartbeat(chamber_id='chA', reported_status='offline')
        self.assertEqual(self.port.heartbeats, [])

    def test_heartbeat_unknown_status_rejected(self):
        with self.assertRaises(ValueError):
            self.service.heartbeat(chamber_id='chA', reported_status='busy')

    def test_register_rejects_non_positive_ttl(self):
        with self.assertRaises(ValueError):
            self.service.register(
                chamber_id='c', name='A', base_url='u', heartbeat_ttl_seconds=0,
            )

    def test_heartbeat_persists_redacted_last_error_json(self):
        # M2 — last_error is redacted at the write boundary BEFORE persistence.
        self.service.heartbeat(
            chamber_id='chA', reported_status='in_use',
            last_error='analyzer at GPIB0::18::INSTR token=abcdef1234567890 down',
            progress={'is_running': True, 'completed': 1, 'total': 2, 'ratio': 0.5},
        )
        stored = self.port.heartbeats[0]['last_error_json']
        self.assertIsNotNone(stored)
        self.assertNotIn('GPIB0::18::INSTR', stored)
        self.assertNotIn('abcdef1234567890', stored)
        payload = json.loads(stored)
        self.assertIn('analyzer', payload['message'])
        self.assertEqual(payload['occurred_at'], _FIXED_NOW.isoformat())

    def test_heartbeat_without_last_error_stores_null(self):
        self.service.heartbeat(chamber_id='chA', reported_status='idle')
        self.assertIsNone(self.port.heartbeats[0]['last_error_json'])

    def test_last_error_allowed_on_idle_heartbeat(self):
        # An error is orthogonal to progress — a node may report it while idle.
        self.service.heartbeat(
            chamber_id='chA', reported_status='idle', last_error='recovered after fault',
        )
        self.assertIsNotNone(self.port.heartbeats[0]['last_error_json'])


class TestChamberHeartbeatClockUnification(unittest.TestCase):
    """The domain Heartbeat validation timestamp == the stored record's
    ``occurred_at`` == the INJECTED clock (no wall-clock divergence).

    Codex P2 follow-up: ``heartbeat`` previously fed the domain ``Heartbeat``
    a wall-clock ``datetime.now()`` while persisting the injected clock's value —
    two clocks for one event. This seals that both flow from ``self._clock()``.
    """

    def test_validation_timestamp_matches_injected_clock_and_record(self):
        import fcc_test_platform.application.central_chamber_write_service as mod
        captured: dict = {}
        real_heartbeat = mod.Heartbeat

        def _spy(**kwargs):
            captured.update(kwargs)
            return real_heartbeat(**kwargs)

        port = _FakeChamberWritePort()
        mod.Heartbeat = _spy
        try:
            service = CentralChamberWriteService(
                port, clock=lambda: _FIXED_NOW.isoformat(), id_factory=lambda: 'fixed-id',
            )
            service.heartbeat(chamber_id='chA', reported_status='idle')
        finally:
            mod.Heartbeat = real_heartbeat
        # Domain validation saw the injected clock (parsed back to a datetime) —
        # NOT the wall clock.
        self.assertEqual(captured['occurred_at'], _FIXED_NOW)
        # The persisted record's occurred_at is the same injected clock value.
        self.assertEqual(port.heartbeats[0]['occurred_at'], _FIXED_NOW.isoformat())

    def test_no_wall_clock_helper_remains(self):
        # The wall-clock ``_utcnow_dt`` branch must be gone — only the injected
        # clock (and its default factory ``_utcnow_iso``) may produce timestamps.
        tree = ast.parse(_WRITE_SERVICE_MODULE.read_text(encoding='utf-8'))
        funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertNotIn('_utcnow_dt', funcs)


class _SqliteFixture(unittest.TestCase):
    def setUp(self):
        self.db_path = _make_sqlite_central()
        self.factory = lambda: QmarkConnection(self.db_path)


class TestChamberEndToEndSqlite(_SqliteFixture):
    """register → heartbeat → availability through the verbatim VIEW."""

    def test_register_then_heartbeat_then_availability(self):
        write = CentralChamberWriteService(
            PostgresCentralChamberWriteAdapter(self.factory),
            clock=lambda: _FIXED_NOW.isoformat(),
        )
        write.register(chamber_id='chA', name='Chamber A', base_url='http://a:8000')
        # heartbeat occurred_at must be fresh relative to the read clock.
        write.heartbeat(chamber_id='chA', reported_status='in_use', session_id='sess-1')

        read = CentralChamberReadService(
            PostgresCentralChamberReadAdapter(self.factory), clock=lambda: _FIXED_NOW,
        )
        page = read.chamber_availability()
        self.assertEqual(len(page['items']), 1)
        item = page['items'][0]
        self.assertEqual(item['chamber_id'], 'chA')
        self.assertEqual(item['name'], 'Chamber A')
        self.assertEqual(item['status'], 'in_use')
        self.assertEqual(item['session_id'], 'sess-1')

    def test_reregister_is_idempotent_upsert(self):
        write = CentralChamberWriteService(PostgresCentralChamberWriteAdapter(self.factory))
        write.register(chamber_id='chA', name='Old', base_url='http://old:8000')
        write.register(chamber_id='chA', name='New', base_url='http://new:8000')
        read = CentralChamberReadService(
            PostgresCentralChamberReadAdapter(self.factory), clock=lambda: _FIXED_NOW,
        )
        nodes = read.chamber_nodes()['items']
        self.assertEqual(len(nodes), 1)  # upsert, not duplicate
        self.assertEqual(nodes[0]['name'], 'New')
        self.assertEqual(nodes[0]['base_url'], 'http://new:8000')

    def test_zero_heartbeat_chamber_appears_offline(self):
        write = CentralChamberWriteService(PostgresCentralChamberWriteAdapter(self.factory))
        write.register(chamber_id='chQuiet', name='Q', base_url='http://q:8000')
        read = CentralChamberReadService(
            PostgresCentralChamberReadAdapter(self.factory), clock=lambda: _FIXED_NOW,
        )
        item = read.chamber_availability()['items'][0]
        self.assertEqual(item['status'], 'offline')
        self.assertIsNone(item['reported_status'])

    def test_heartbeat_last_error_surfaces_in_availability(self):
        # M2 end-to-end — node-reported error flows through the verbatim VIEW into
        # the read envelope (redacted), single availability read (no N+1).
        write = CentralChamberWriteService(
            PostgresCentralChamberWriteAdapter(self.factory),
            clock=lambda: _FIXED_NOW.isoformat(),
        )
        write.register(chamber_id='chA', name='Chamber A', base_url='http://a:8000')
        write.heartbeat(
            chamber_id='chA', reported_status='in_use', session_id='sess-1',
            last_error='analyzer connect failed at C:\\rf\\key.pem',
        )
        read = CentralChamberReadService(
            PostgresCentralChamberReadAdapter(self.factory), clock=lambda: _FIXED_NOW,
        )
        item = read.chamber_availability()['items'][0]
        self.assertIn('analyzer connect failed', item['last_error'])
        self.assertNotIn('C:\\rf', item['last_error'])
        self.assertEqual(item['last_error_at'], _FIXED_NOW.isoformat())
        # in_use + fresh + enabled ⇒ usable ⇒ no unavailable_reason.
        self.assertIsNone(item['unavailable_reason'])

    def test_disabled_chamber_reason_through_view(self):
        write = CentralChamberWriteService(
            PostgresCentralChamberWriteAdapter(self.factory),
            clock=lambda: _FIXED_NOW.isoformat(),
        )
        write.register(
            chamber_id='chD', name='D', base_url='http://d:8000', enabled=False,
        )
        write.heartbeat(chamber_id='chD', reported_status='idle')
        read = CentralChamberReadService(
            PostgresCentralChamberReadAdapter(self.factory), clock=lambda: _FIXED_NOW,
        )
        item = read.chamber_availability()['items'][0]
        self.assertEqual(item['status'], 'idle')  # orthogonal — still heartbeating
        self.assertEqual(item['unavailable_reason'], 'disabled')

    def test_read_loud_fail_on_connection_error(self):
        def boom():
            raise RuntimeError('central down')
        read = CentralChamberReadService(PostgresCentralChamberReadAdapter(boom))
        with self.assertRaises(CentralChamberReadError):
            read.chamber_availability()

    def test_write_loud_fail_on_connection_error(self):
        def boom():
            raise RuntimeError('central down')
        write = CentralChamberWriteService(PostgresCentralChamberWriteAdapter(boom))
        with self.assertRaises(ChamberWriteError):
            write.heartbeat(chamber_id='chA', reported_status='idle')


class TestChamberAuthZ(unittest.TestCase):
    """The three chamber operations gate on read / admin / chamber tokens."""

    def _adapter(self, principal):
        from fcc_test_platform.api.platform_routes import (
            PlatformApiAdapter,
            PlatformAuthorizationError,
        )
        from fcc_test_platform.application.central_read_adapter import PostgresCentralReadAdapter
        from fcc_test_platform.application.central_read_service import CentralReadService
        self._PlatformAuthorizationError = PlatformAuthorizationError
        read_service = CentralReadService(PostgresCentralReadAdapter(lambda: None))
        chamber_read = CentralChamberReadService(
            _FakeChamberReadPort(availability=[]), clock=lambda: _FIXED_NOW,
        )
        chamber_write = CentralChamberWriteService(
            _FakeChamberWritePort(), clock=lambda: _FIXED_NOW.isoformat(),
            id_factory=lambda: 'fixed-id',
        )
        return PlatformApiAdapter(
            read_service,
            access_policy=ApiAccessPolicy(PLATFORM_API_OPERATIONS),
            principal=principal,
            chamber_read_service=chamber_read,
            chamber_write_service=chamber_write,
        )

    def test_read_token_lists_chambers(self):
        adapter = self._adapter(ApiPrincipal.from_permissions('viewer', ['platform:read']))
        page = adapter.list_chambers()
        self.assertEqual(page['items'], [])

    def test_chamber_token_pushes_heartbeat(self):
        adapter = self._adapter(ApiPrincipal.from_permissions('node-A', ['platform:chamber']))
        ack = adapter.push_chamber_heartbeat({'chamber_id': 'chA', 'reported_status': 'idle'})
        self.assertEqual(ack['reported_status'], 'idle')

    def test_admin_token_registers_chamber(self):
        adapter = self._adapter(ApiPrincipal.from_permissions('admin', ['platform:admin']))
        env = adapter.register_chamber({
            'chamber_id': 'chA', 'name': 'A', 'base_url': 'http://a:8000',
        })
        self.assertEqual(env['chamber_id'], 'chA')

    def test_read_token_cannot_register_or_heartbeat(self):
        adapter = self._adapter(ApiPrincipal.from_permissions('viewer', ['platform:read']))
        with self.assertRaises(self._PlatformAuthorizationError):
            adapter.register_chamber({'chamber_id': 'c', 'name': 'A', 'base_url': 'u'})
        with self.assertRaises(self._PlatformAuthorizationError):
            adapter.push_chamber_heartbeat({'chamber_id': 'c', 'reported_status': 'idle'})

    def test_chamber_token_cannot_read_or_register(self):
        # The node token is narrow — heartbeat only, never read/admin.
        adapter = self._adapter(ApiPrincipal.from_permissions('node-A', ['platform:chamber']))
        with self.assertRaises(self._PlatformAuthorizationError):
            adapter.list_chambers()
        with self.assertRaises(self._PlatformAuthorizationError):
            adapter.register_chamber({'chamber_id': 'c', 'name': 'A', 'base_url': 'u'})

    def test_anonymous_denied_for_all(self):
        adapter = self._adapter(ApiPrincipal.anonymous())
        with self.assertRaises(self._PlatformAuthorizationError):
            adapter.list_chambers()
        with self.assertRaises(self._PlatformAuthorizationError):
            adapter.register_chamber({'chamber_id': 'c', 'name': 'A', 'base_url': 'u'})
        with self.assertRaises(self._PlatformAuthorizationError):
            adapter.push_chamber_heartbeat({'chamber_id': 'c', 'reported_status': 'idle'})

    # ── per-chamber 토큰 바인딩 enforcement (2026-06-20) ──────────────────────

    def test_bound_chamber_token_heartbeats_own_chamber(self):
        adapter = self._adapter(ApiPrincipal.from_permissions(
            'node-A', ['platform:chamber'], chamber_id='chA',
        ))
        ack = adapter.push_chamber_heartbeat({'chamber_id': 'chA', 'reported_status': 'idle'})
        self.assertEqual(ack['reported_status'], 'idle')

    def test_bound_chamber_token_cannot_heartbeat_other_chamber(self):
        # M1 staging 갭 정공: chA 에 묶인 토큰이 chB 를 heartbeat 하면 거부(403).
        adapter = self._adapter(ApiPrincipal.from_permissions(
            'node-A', ['platform:chamber'], chamber_id='chA',
        ))
        with self.assertRaises(self._PlatformAuthorizationError):
            adapter.push_chamber_heartbeat({'chamber_id': 'chB', 'reported_status': 'idle'})

    def test_unbound_chamber_token_backward_compat(self):
        # claim 없는 일반/레거시 토큰은 통과(프로비저닝 전 무회귀).
        adapter = self._adapter(ApiPrincipal.from_permissions('node', ['platform:chamber']))
        ack = adapter.push_chamber_heartbeat({'chamber_id': 'anything', 'reported_status': 'idle'})
        self.assertEqual(ack['reported_status'], 'idle')

    def test_principal_carries_chamber_id(self):
        p = ApiPrincipal.from_permissions('n', ['platform:chamber'], chamber_id=' chA ')
        self.assertEqual(p.chamber_id, 'chA')  # trimmed
        self.assertEqual(ApiPrincipal.from_permissions('n', []).chamber_id, '')


class TestChamberContractAndArtifact(unittest.TestCase):
    """Contract SSOT wiring + OpenAPI artifact byte-identity."""

    def test_routes_present_with_methods(self):
        self.assertEqual(PLATFORM_API_ROUTES['list_chambers'], ('GET', '/platform/chambers'))
        self.assertEqual(PLATFORM_API_ROUTES['register_chamber'], ('POST', '/platform/chambers'))
        self.assertEqual(
            PLATFORM_API_ROUTES['push_chamber_heartbeat'],
            ('POST', '/platform/chambers/heartbeat'),
        )

    def test_permissions_assigned(self):
        self.assertEqual(PLATFORM_API_PERMISSIONS['list_chambers'], 'platform:read')
        self.assertEqual(PLATFORM_API_PERMISSIONS['register_chamber'], 'platform:admin')
        self.assertEqual(PLATFORM_API_PERMISSIONS['push_chamber_heartbeat'], 'platform:chamber')

    def test_new_chamber_token_is_distinct_and_described(self):
        self.assertIn('platform:chamber', set(PLATFORM_API_PERMISSIONS.values()))
        self.assertIn('platform:chamber', PLATFORM_API_PERMISSION_DESCRIPTIONS)

    def test_request_response_schemas_present(self):
        for name in (
            'ChamberAvailabilityList', 'ChamberAvailabilityEnvelope', 'ChamberNodeEnvelope',
            'RegisterChamberRequest', 'ChamberHeartbeatRequest', 'ChamberHeartbeatAck',
        ):
            self.assertIn(name, PLATFORM_API_SCHEMAS, name)

    def test_operations_declared(self):
        for name in ('list_chambers', 'register_chamber', 'push_chamber_heartbeat'):
            self.assertIn(name, PLATFORM_API_OPERATIONS, name)

    def test_artifact_byte_identical(self):
        self.assertTrue(_ARTIFACT.exists(), f'missing {_ARTIFACT} — run export script')
        on_disk = _ARTIFACT.read_text(encoding='utf-8')
        built = _canonical_text(build_platform_openapi_schema(None))
        self.assertEqual(on_disk, built,
                         'platform-api.openapi.json drifted — run '
                         'python scripts/export_session_api_schemas.py')

    def test_heartbeat_request_status_enum_excludes_offline(self):
        enum = PLATFORM_API_SCHEMAS['ChamberHeartbeatRequest']['properties']['reported_status']['enum']
        self.assertEqual(set(enum), {'idle', 'in_use'})
        self.assertNotIn('offline', enum)

    def test_availability_status_enum_includes_offline(self):
        enum = PLATFORM_API_SCHEMAS['ChamberAvailabilityEnvelope']['properties']['status']['enum']
        self.assertEqual(set(enum), {s.value for s in ChamberNodeStatus})

    def test_availability_unavailable_reason_enum_matches_domain_ssot(self):
        # M2 (Codex P0) — the contract's unavailable_reason vocabulary MUST equal the
        # domain UnavailableReason SSOT; no scattered literal re-declaration is allowed
        # to drift from it (the FE labels + OpenAPI enum both flow from this).
        enum = (
            PLATFORM_API_SCHEMAS['ChamberAvailabilityEnvelope']
            ['properties']['unavailable_reason']['enum']
        )
        self.assertEqual(set(enum), {r.value for r in UnavailableReason})
        # nullable (null ⇒ usable) — the overlay is absent for an idle/in_use chamber.
        self.assertTrue(
            PLATFORM_API_SCHEMAS['ChamberAvailabilityEnvelope']
            ['properties']['unavailable_reason']['nullable']
        )


class TestChamberHttpWire(unittest.TestCase):
    """HTTP route/body/error boundary via FastAPI TestClient (Codex P2 follow-up).

    ``TestChamberAuthZ`` seals the *adapter-method* authz; this class seals the
    *HTTP wire* on top of it — route binding, JSON body → service flow, and the
    RFC 9457 ``application/problem+json`` error mapping (400 validation / 403
    authz). It runs the real ``create_platform_app`` (problem handler installed),
    not a bare router, so the problem rendering is exercised end-to-end.
    """

    def _client(self, principal):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest('fastapi not installed in this shard')
        from fcc_test_platform.api.platform_routes import (
            PlatformApiAdapter,
            create_platform_app,
        )
        from fcc_test_platform.application.central_read_adapter import PostgresCentralReadAdapter
        from fcc_test_platform.application.central_read_service import CentralReadService

        read_service = CentralReadService(PostgresCentralReadAdapter(lambda: None))
        self.write_port = _FakeChamberWritePort()
        self.read_port = _FakeChamberReadPort(availability=[{
            'chamber_id': 'chA', 'name': 'A', 'base_url': 'http://a:8000', 'enabled': 1,
            'heartbeat_ttl_seconds': 90, 'reported_status': 'idle',
            'last_heartbeat_at': (_FIXED_NOW - timedelta(seconds=5)).isoformat(),
            'heartbeat_expires_at': None, 'session_id': None,
        }])
        adapter = PlatformApiAdapter(
            read_service,
            access_policy=ApiAccessPolicy(PLATFORM_API_OPERATIONS),
            principal=principal,
            chamber_read_service=CentralChamberReadService(
                self.read_port, clock=lambda: _FIXED_NOW,
            ),
            chamber_write_service=CentralChamberWriteService(
                self.write_port, clock=lambda: _FIXED_NOW.isoformat(),
                id_factory=lambda: 'fixed-id',
            ),
        )
        return TestClient(create_platform_app(adapter))

    @staticmethod
    def _media(resp) -> str:
        return resp.headers.get('content-type', '').split(';')[0]

    def _read(self):
        return self._client(ApiPrincipal.from_permissions('viewer', ['platform:read']))

    def _admin(self):
        return self._client(ApiPrincipal.from_permissions('admin', ['platform:admin']))

    def _chamber(self):
        return self._client(ApiPrincipal.from_permissions('node-A', ['platform:chamber']))

    # ── GET /platform/chambers (platform:read) ────────────────────────────────

    def test_get_chambers_read_token_returns_availability_envelope(self):
        resp = self._read().get('/platform/chambers')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # Envelope is the availability object (items + server_time), not paginated.
        self.assertIn('items', body)
        self.assertIn('server_time', body)
        self.assertEqual(body['items'][0]['chamber_id'], 'chA')
        self.assertEqual(body['server_time'], _FIXED_NOW.isoformat())

    def test_get_chambers_anonymous_denied_problem(self):
        from fcc_test_contracts.common.api_error_codes import PROBLEM_JSON_MEDIA_TYPE
        resp = self._client(ApiPrincipal.anonymous()).get('/platform/chambers')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self._media(resp), PROBLEM_JSON_MEDIA_TYPE)

    def test_get_chambers_chamber_token_denied(self):
        # The node token grants heartbeat only — never the read dashboard.
        self.assertEqual(self._chamber().get('/platform/chambers').status_code, 403)

    # ── POST /platform/chambers (platform:admin) ──────────────────────────────

    def test_post_chamber_admin_registers_and_body_reaches_service(self):
        resp = self._admin().post('/platform/chambers', json={
            'chamber_id': 'chA', 'name': 'Chamber A', 'base_url': 'http://a:8000',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['chamber_id'], 'chA')
        # The JSON body actually flowed through register() to the write port.
        self.assertEqual(self.write_port.registered[0]['chamber_id'], 'chA')
        self.assertEqual(self.write_port.registered[0]['name'], 'Chamber A')
        self.assertEqual(self.write_port.registered[0]['base_url'], 'http://a:8000')

    def test_post_chamber_validation_error_maps_to_400_problem(self):
        from fcc_test_contracts.common.api_error_codes import PROBLEM_JSON_MEDIA_TYPE
        resp = self._admin().post('/platform/chambers', json={
            'chamber_id': 'chA', 'name': '', 'base_url': 'http://a:8000',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._media(resp), PROBLEM_JSON_MEDIA_TYPE)
        # Rejected at the boundary — nothing reached the write port.
        self.assertEqual(self.write_port.registered, [])

    def test_post_chamber_read_token_denied(self):
        resp = self._read().post('/platform/chambers', json={
            'chamber_id': 'c', 'name': 'A', 'base_url': 'u',
        })
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.write_port.registered, [])

    # ── POST /platform/chambers/heartbeat (platform:chamber) ──────────────────

    def test_post_heartbeat_chamber_token_appends(self):
        resp = self._chamber().post('/platform/chambers/heartbeat', json={
            'chamber_id': 'chA', 'reported_status': 'idle',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['reported_status'], 'idle')
        self.assertEqual(self.write_port.heartbeats[0]['reported_status'], 'idle')

    def test_post_heartbeat_offline_maps_to_400_problem(self):
        from fcc_test_contracts.common.api_error_codes import PROBLEM_JSON_MEDIA_TYPE
        # OFFLINE is a derived state — a node can never self-report it.
        resp = self._chamber().post('/platform/chambers/heartbeat', json={
            'chamber_id': 'chA', 'reported_status': 'offline',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._media(resp), PROBLEM_JSON_MEDIA_TYPE)
        self.assertEqual(self.write_port.heartbeats, [])

    def test_post_heartbeat_unknown_status_maps_to_400_problem(self):
        from fcc_test_contracts.common.api_error_codes import PROBLEM_JSON_MEDIA_TYPE
        resp = self._chamber().post('/platform/chambers/heartbeat', json={
            'chamber_id': 'chA', 'reported_status': 'busy',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._media(resp), PROBLEM_JSON_MEDIA_TYPE)
        self.assertEqual(self.write_port.heartbeats, [])

    def test_post_heartbeat_read_token_denied(self):
        resp = self._read().post('/platform/chambers/heartbeat', json={
            'chamber_id': 'c', 'reported_status': 'idle',
        })
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.write_port.heartbeats, [])


class TestChamberHeartbeatCarriedProgressC1(unittest.TestCase):
    """C1 — heartbeat-carried progress: write 영속 + read 노출 게이트 + 계약."""

    def _read_service(self, availability):
        return CentralChamberReadService(
            _FakeChamberReadPort(availability=availability), clock=lambda: _FIXED_NOW,
        )

    def _avail_row(self, **over):
        row = {
            'chamber_id': 'chA', 'name': 'A', 'base_url': 'http://a:8000', 'enabled': 1,
            'heartbeat_ttl_seconds': 90, 'reported_status': 'in_use',
            'last_heartbeat_at': (_FIXED_NOW - timedelta(seconds=5)).isoformat(),
            'heartbeat_expires_at': None, 'session_id': 's1',
            'progress_json': '{"is_running": true, "completed": 3, "total": 10, "ratio": 0.3}',
        }
        row.update(over)
        return row

    # ── write service ────────────────────────────────────────────────────────
    def test_write_persists_progress_json_on_in_use(self):
        port = _FakeChamberWritePort()
        svc = CentralChamberWriteService(
            port, clock=lambda: _FIXED_NOW.isoformat(), id_factory=lambda: 'fixed-id',
        )
        svc.heartbeat(
            chamber_id='chA', reported_status='in_use',
            progress={'is_running': True, 'completed': 3, 'total': 10, 'ratio': 0.3},
        )
        stored = port.heartbeats[0]['progress_json']
        self.assertIsNotNone(stored)
        self.assertIn('"completed": 3', stored)

    def test_write_rejects_progress_on_idle(self):
        # idle 노드는 진행 중 측정이 없음 — progress 동반 시 도메인 불변식 400.
        port = _FakeChamberWritePort()
        svc = CentralChamberWriteService(
            port, clock=lambda: _FIXED_NOW.isoformat(), id_factory=lambda: 'fixed-id',
        )
        with self.assertRaises(ValueError):
            svc.heartbeat(
                chamber_id='chA', reported_status='idle',
                progress={'is_running': False, 'completed': 0, 'total': 1, 'ratio': 0.0},
            )
        self.assertEqual(port.heartbeats, [])

    def test_write_idle_without_progress_stores_null(self):
        port = _FakeChamberWritePort()
        svc = CentralChamberWriteService(
            port, clock=lambda: _FIXED_NOW.isoformat(), id_factory=lambda: 'fixed-id',
        )
        svc.heartbeat(chamber_id='chA', reported_status='idle')
        self.assertIsNone(port.heartbeats[0]['progress_json'])

    # ── read service exposure gating ───────────────────────────────────────────
    def test_read_exposes_progress_when_in_use(self):
        item = self._read_service([self._avail_row()]).chamber_availability()['items'][0]
        self.assertEqual(item['status'], 'in_use')
        self.assertEqual(item['progress'], {
            'is_running': True, 'completed': 3, 'total': 10, 'ratio': 0.3,
        })

    def test_read_suppresses_stale_progress_when_offline(self):
        # 측정 중 OFFLINE 으로 빠진 챔버의 마지막 progress 는 노출하지 않는다(stale).
        stale = (_FIXED_NOW - timedelta(seconds=91)).isoformat()
        item = self._read_service(
            [self._avail_row(last_heartbeat_at=stale)]
        ).chamber_availability()['items'][0]
        self.assertEqual(item['status'], 'offline')
        self.assertIsNone(item['progress'])

    def test_read_progress_none_when_idle(self):
        item = self._read_service([self._avail_row(
            reported_status='idle', progress_json=None,
        )]).chamber_availability()['items'][0]
        self.assertEqual(item['status'], 'idle')
        self.assertIsNone(item['progress'])

    def test_read_tolerates_malformed_progress_json(self):
        item = self._read_service([self._avail_row(
            progress_json='{not json',
        )]).chamber_availability()['items'][0]
        self.assertIsNone(item['progress'])

    def test_read_tolerates_valid_json_with_invalid_field_values(self):
        # 유효 JSON object 지만 필드 값 타입이 깨진 ledger 값(completed='x' 등)도
        # 500 으로 터지지 않고 progress=None 으로 degrade — in_use 라도 노출 None.
        for bad_json in (
            '{"is_running": true, "completed": "x", "total": 10, "ratio": 0.3}',
            '{"is_running": true, "completed": 1, "total": [1, 2], "ratio": 0.3}',
            '{"is_running": true, "completed": 1, "total": 2, "ratio": {"a": 1}}',
        ):
            item = self._read_service([self._avail_row(
                progress_json=bad_json,
            )]).chamber_availability()['items'][0]
            self.assertEqual(item['status'], 'in_use', msg=bad_json)
            self.assertIsNone(item['progress'], msg=bad_json)

    # ── contract SSOT ──────────────────────────────────────────────────────────
    def test_availability_envelope_progress_refs_session_progress(self):
        prop = PLATFORM_API_SCHEMAS['ChamberAvailabilityEnvelope']['properties']['progress']
        self.assertTrue(prop.get('nullable'))
        self.assertEqual(
            prop['allOf'][0]['$ref'], '#/schemas/ChamberSessionProgress',
        )

    def test_heartbeat_request_carries_optional_progress(self):
        schema = PLATFORM_API_SCHEMAS['ChamberHeartbeatRequest']
        self.assertIn('progress', schema['properties'])
        # in_use 일 때만 운반 — required 아님.
        self.assertNotIn('progress', schema['required'])

    def test_availability_column_tuple_includes_progress_json(self):
        from fcc_test_platform.application.central_chamber_read_adapter import (
            CHAMBER_AVAILABILITY_COLUMNS,
        )
        self.assertIn('progress_json', CHAMBER_AVAILABILITY_COLUMNS)


class TestChamberProgressEndToEndC1(unittest.TestCase):
    """C1 — register → in_use heartbeat(progress) → availability 가 progress 노출.

    단일 availability read 가 모든 챔버 progress 를 운반함을 봉인(N+1 제거 본질)."""

    def test_in_use_heartbeat_progress_surfaces_in_availability(self):
        db_path = _make_sqlite_central()
        factory = lambda: QmarkConnection(db_path)  # noqa: E731
        write = CentralChamberWriteService(
            PostgresCentralChamberWriteAdapter(factory),
            clock=lambda: _FIXED_NOW.isoformat(),
        )
        read = CentralChamberReadService(
            PostgresCentralChamberReadAdapter(factory), clock=lambda: _FIXED_NOW,
        )
        write.register(chamber_id='chA', name='A', base_url='http://a:8000')
        write.heartbeat(
            chamber_id='chA', reported_status='in_use', session_id='sess-1',
            progress={'is_running': True, 'completed': 7, 'total': 20, 'ratio': 0.35},
        )
        item = read.chamber_availability()['items'][0]
        self.assertEqual(item['status'], 'in_use')
        self.assertEqual(item['progress']['completed'], 7)
        self.assertEqual(item['progress']['total'], 20)


class TestChamberMetricsCollector(unittest.TestCase):
    """관측성(2026-06-20) — chamber gauge collector 가 availability(파생 status)에서
    status별 카운트 + 최대 heartbeat age 를 registry 에 set(staleness 재구현 없음)."""

    def _registry(self):
        from fcc_test_contracts.common.metrics_registry import ApiMetricsRegistry
        from fcc_test_platform.application.chamber_metrics import CHAMBER_GAUGE_FAMILIES
        return ApiMetricsRegistry(
            namespace='fcc_platform', enable_websocket=True,
            gauge_families=CHAMBER_GAUGE_FAMILIES,
        )

    class _FakeReadService:
        def __init__(self, snapshot):
            self._snapshot = snapshot

        def chamber_availability(self):
            return self._snapshot

    def test_counts_by_status_and_max_age(self):
        from fcc_test_platform.application.chamber_metrics import ChamberMetricsCollector
        snapshot = {
            'server_time': '2026-06-20T06:00:30+00:00',
            'items': [
                {'chamber_id': 'a', 'status': 'idle',
                 'last_heartbeat_at': '2026-06-20T06:00:25+00:00'},   # age 5
                {'chamber_id': 'b', 'status': 'in_use',
                 'last_heartbeat_at': '2026-06-20T06:00:00+00:00'},   # age 30
                {'chamber_id': 'c', 'status': 'offline',
                 'last_heartbeat_at': None},                          # no age
            ],
        }
        reg = self._registry()
        ChamberMetricsCollector(self._FakeReadService(snapshot), reg).refresh()
        lines = reg.render().splitlines()
        self.assertIn('fcc_platform_chamber_count{availability="idle"} 1', lines)
        self.assertIn('fcc_platform_chamber_count{availability="in_use"} 1', lines)
        self.assertIn('fcc_platform_chamber_count{availability="offline"} 1', lines)
        self.assertIn('fcc_platform_chamber_heartbeat_age_max_seconds 30', lines)

    def test_empty_fleet_all_zero(self):
        from fcc_test_platform.application.chamber_metrics import ChamberMetricsCollector
        reg = self._registry()
        ChamberMetricsCollector(
            self._FakeReadService({'server_time': '2026-06-20T06:00:00+00:00', 'items': []}),
            reg,
        ).refresh()
        lines = reg.render().splitlines()
        self.assertIn('fcc_platform_chamber_count{availability="offline"} 0', lines)
        self.assertIn('fcc_platform_chamber_heartbeat_age_max_seconds 0', lines)

    def test_refresh_absorbs_read_failure(self):
        from fcc_test_platform.application.chamber_metrics import ChamberMetricsCollector

        class _Boom:
            def chamber_availability(self):
                raise RuntimeError('db down')

        reg = self._registry()
        ChamberMetricsCollector(_Boom(), reg).refresh()  # best-effort, no raise
        self.assertIn('fcc_platform_chamber_count{availability="idle"} 0', reg.render().splitlines())


class TestHeartbeatFailureModeSeparation(_SqliteFixture):
    """미등록 챔버 heartbeat = 404, 백엔드 장애 = 503 (부채 청산 M2, 2026-07-30).

    이전에는 미등록 ``chamber_id`` 가 FK 위반 → ``ChamberWriteError`` → **503
    UPSTREAM_UNAVAILABLE** 로 나갔다. 클라이언트가 고칠 수 있는 사실("먼저 등록하라")을
    서버 장애로 보고하면 운영자는 멀쩡한 중앙 DB 를 의심하며 시간을 버린다.

    **두 경로를 모두** 검사한다 — 하나만 보면 회귀를 못 잡는다: 404 만 보면 진짜 장애가
    404 로 새는 것을 못 보고, 503 만 보면 미등록이 다시 503 이 되는 것을 못 본다.

    진짜 SQLite DB 위에서 돈다 — FakeConnection 은 조건부 INSERT 의 ``rowcount``
    판정을 흉내 낼 뿐 검증하지 못한다.
    """

    def _service(self, factory=None):
        return CentralChamberWriteService(
            PostgresCentralChamberWriteAdapter(factory or self.factory),
            clock=lambda: _FIXED_NOW.isoformat(),
        )

    def _ledger_rows(self) -> int:
        with SqliteConnectionContext(self.db_path) as conn:
            return conn.execute('SELECT COUNT(*) FROM chamber_heartbeat_events').fetchone()[0]

    # ── 404 축: 미등록 챔버 ────────────────────────────────────────────────────

    def test_unregistered_chamber_heartbeat_raises_not_found(self):
        with self.assertRaises(ChamberNotFoundError):
            self._service().heartbeat(chamber_id='chGhost', reported_status='idle')

    def test_unregistered_chamber_is_not_reported_as_backend_failure(self):
        """404 가 503 계열로 새지 않는다 — 이것이 본 웨이브가 고친 바로 그 혼동이다."""
        try:
            self._service().heartbeat(chamber_id='chGhost', reported_status='idle')
        except ChamberNotFoundError:
            pass
        else:  # pragma: no cover - 방어
            self.fail('expected ChamberNotFoundError')
        self.assertFalse(
            issubclass(ChamberNotFoundError, ChamberWriteError),
            '두 실패 모드가 상속으로 얽히면 라우트 매핑 순서에 따라 다시 섞인다.',
        )

    def test_unregistered_chamber_writes_no_ledger_row(self):
        with self.assertRaises(ChamberNotFoundError):
            self._service().heartbeat(chamber_id='chGhost', reported_status='idle')
        self.assertEqual(0, self._ledger_rows())

    def test_registered_chamber_heartbeat_still_appends(self):
        """조건부 INSERT 가 정상 경로를 막지 않는다 (회귀 0)."""
        service = self._service()
        service.register(chamber_id='chA', name='A', base_url='http://a:8000')
        service.heartbeat(chamber_id='chA', reported_status='in_use', session_id='s1')
        service.heartbeat(chamber_id='chA', reported_status='idle')
        self.assertEqual(2, self._ledger_rows())

    def test_registering_after_a_rejected_heartbeat_unblocks_it(self):
        """404 는 최종 상태가 아니라 지시다 — 등록하면 곧바로 통과해야 한다."""
        service = self._service()
        with self.assertRaises(ChamberNotFoundError):
            service.heartbeat(chamber_id='chLate', reported_status='idle')
        service.register(chamber_id='chLate', name='L', base_url='http://l:8000')
        service.heartbeat(chamber_id='chLate', reported_status='idle')
        self.assertEqual(1, self._ledger_rows())

    # ── 503 축: 진짜 백엔드 장애 ───────────────────────────────────────────────

    def test_connection_failure_is_still_a_write_error(self):
        def _boom():
            raise RuntimeError('central unreachable')

        with self.assertRaises(ChamberWriteError):
            self._service(_boom).heartbeat(chamber_id='chA', reported_status='idle')

    def test_query_failure_is_still_a_write_error(self):
        class _BrokenCursor:
            rowcount = -1

            def execute(self, statement, parameters=()):
                raise RuntimeError('connection reset mid-statement')

            def close(self):
                pass

        class _BrokenConnection:
            def cursor(self):
                return _BrokenCursor()

            def commit(self):  # pragma: no cover - 도달 전에 실패
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        with self.assertRaises(ChamberWriteError):
            self._service(_BrokenConnection).heartbeat(
                chamber_id='chA', reported_status='idle',
            )

    def test_driver_without_rowcount_support_does_not_invent_a_404(self):
        """rowcount 를 못 내는 드라이버는 '모름'이지 '없음'이 아니다.

        -1/None 을 404 로 해석하면 정상 등록된 챔버의 heartbeat 가 전부 404 가 된다 —
        조건부 INSERT 를 도입하면서 가장 조용히 망가질 수 있는 지점.
        """
        # ⚠️ 이 double 이 모델링하는 결핍은 **rowcount 하나**다. 나머지(번역·fetchall·
        # 커밋)는 실제 드라이버가 전부 갖고 있고, 등록 upsert 는 챔버 모드 축
        # (2026-08-16)부터 RETURNING 으로 저장된 행을 되읽는다. 결핍 외의 것을 함께
        # 빼두면 이 double 은 *존재하지 않는 드라이버*를 모델링하면서 무관한 경로를
        # 실패시킨다 — 그래서 결핍만 뒤집는 shim 의 서브클래스를 쓴다.
        service = self._service(lambda: RowcountBlindConnection(self.db_path))
        service.register(chamber_id='chA', name='A', base_url='http://a:8000')
        service.heartbeat(chamber_id='chA', reported_status='idle')  # 404 나면 안 됨
        self.assertEqual(1, self._ledger_rows())

    # ── SQL 형상 ──────────────────────────────────────────────────────────────

    def test_conditional_insert_gates_on_the_registry_and_stays_append_only(self):
        upper = INSERT_HEARTBEAT_EVENT_SQL.upper()
        self.assertIn('WHERE EXISTS', upper, '존재 게이트가 사라졌다')
        self.assertIn(f'"{CHAMBER_NODES_TABLE}"'.upper(), upper)
        for verb in ('UPDATE', 'DELETE', 'ON CONFLICT', 'DROP'):
            self.assertNotIn(verb, upper, f'append-only 위반 — {verb!r} 발견')
        # 존재 판정은 드라이버 메시지가 아니라 파라미터 바인딩으로 표현된다.
        self.assertEqual(
            len(HEARTBEAT_EVENT_COLUMNS) + 1, INSERT_HEARTBEAT_EVENT_SQL.count('%s'),
            '컬럼 값 + 부모 키 1개가 모두 바인드 파라미터여야 한다(보간 0).',
        )


class TestChamberNotFoundErrorSingleDefinition(unittest.TestCase):
    """``ChamberNotFoundError`` 정의는 src/ 전체에서 정확히 1개다 (M2).

    두 번째 클래스가 생기면 ``isinstance`` 기반 404 매핑이 조용히 갈라져 — 한 경로는
    404, 다른 경로는 500 default 로 떨어진다. AST 로 클래스 *정의* 만 센다(import /
    re-export / 문자열 언급은 세지 않는다).
    """

    #: ⚠️ **훑는 트리를 `pyproject.toml` 에서 파생한다** (2026-09-03).
    #:
    #: 여기 있던 것은 `SRC = <repo>/src` 였고 **그 디렉터리는 이 레인에 없다**
    #: (추출 2026-08-30). `rglob` 이 빈 목록을 돌려주니 이 검사는 **아무것도 훑지
    #: 않았다.** 기대값이 비어 있지 않아 red 였을 뿐, 만약 `[]` 를 기대했다면
    #: **한 파일도 안 보고 초록**이었을 것이다 — 「실패 0건」과 「실행 0건」이
    #: 같은 모양인 그 계급이다(`.claude/rules/check-axis-blindness.md`).
    #:
    #: 그래서 목록을 손으로 고치지 않고, **이 상자가 싣는다고 선언한 것**을
    #: 그대로 읽는다. 커널 이관으로 최상위 이름이 또 바뀌어도 따라온다.
    REPO_ROOT = Path(__file__).resolve().parents[1]
    ERROR_NAME = 'ChamberNotFoundError'
    #: ⚠️ 2026-09-03(커널 3단계) — 최상위 `domain/` 이 사라지며 이 파일이
    #: `fcc_test_platform/` 아래로 갔다. **경로를 적지 않고 모듈에게 묻는다** —
    #: 다음 이관에서 또 낡는 것을 막는다.
    OWNER_MODULE = 'fcc_test_platform.domain.ports.output.central_chamber_write_port'

    @classmethod
    def _shipped_roots(cls) -> list[Path]:
        """`[tool.setuptools.packages.find] include` 가 지목하는 최상위 트리."""
        import tomllib

        config = tomllib.loads(
            (cls.REPO_ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        patterns = (
            config['tool']['setuptools']['packages']['find']['include'])
        roots = []
        for pattern in patterns:
            top = pattern.split('*')[0].split('.')[0].rstrip('.')
            candidate = cls.REPO_ROOT / top
            if candidate.is_dir() and candidate not in roots:
                roots.append(candidate)
        return roots

    def _definition_sites(self):
        sites, scanned = [], 0
        for root in self._shipped_roots():
            for path in sorted(root.rglob('*.py')):
                if '__pycache__' in path.parts:
                    continue
                scanned += 1
                tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef) and node.name == self.ERROR_NAME:
                        sites.append(path.relative_to(self.REPO_ROOT).as_posix())
        return sorted(sites), scanned

    def test_the_scan_actually_visits_files(self):
        """⚠️ 비-공허성 — 이 팔이 없어서 위 검사가 아무것도 안 훑고 있었다."""
        roots = self._shipped_roots()
        self.assertTrue(roots, 'pyproject 의 include 에서 트리를 하나도 못 찾았다')
        _, scanned = self._definition_sites()
        self.assertGreater(scanned, 0, '훑은 파일이 0개다 — 이 검사는 아무것도 판정하지 않는다')

    def test_exactly_one_definition_site(self):
        from tests._moved_module_source import moved_module_source

        sites, _ = self._definition_sites()
        owner = moved_module_source(self.OWNER_MODULE).relative_to(
            self.REPO_ROOT).as_posix()
        self.assertEqual(
            [owner], sites,
            f'{self.ERROR_NAME} 정의가 1개가 아니다: {sites}',
        )

    def test_legacy_import_path_still_resolves_to_the_same_class(self):
        from fcc_test_platform.application.chamber_measurement_service import (
            ChamberNotFoundError as ReExported,
        )
        self.assertIs(
            ChamberNotFoundError, ReExported,
            '하위호환 re-export 가 별개 클래스가 되면 기존 except 절이 조용히 빗나간다.',
        )


class TestHeartbeatNotFoundHttpWire(unittest.TestCase):
    """HTTP 경계에서 404/503 이 실제로 갈린다 (M2).

    ``TestChamberHttpWire`` 와 같은 방식으로 실 ``create_platform_app`` 을 세우되
    write port 만 예외를 던지는 것으로 바꿔 끼운다 — 라우트 매핑 테이블 + RFC 9457
    problem 렌더까지 통과한 뒤의 상태 코드/머신 코드를 본다. (상속하지 않는 이유:
    부모 테스트가 통째로 재실행되어 실행 시간만 늘고 신호는 늘지 않는다.)
    """

    @staticmethod
    def _media(resp) -> str:
        return resp.headers.get('content-type', '').split(';')[0]

    def _client_with_write_error(self, exc):
        from fastapi.testclient import TestClient
        from fcc_test_platform.api.platform_routes import (
            PlatformApiAdapter,
            create_platform_app,
        )
        from fcc_test_platform.application.central_read_adapter import PostgresCentralReadAdapter
        from fcc_test_platform.application.central_read_service import CentralReadService

        class _RaisingWritePort:
            def register_chamber(self, record):  # pragma: no cover - 미사용
                return dict(record)

            def append_heartbeat(self, record):
                raise exc

        adapter = PlatformApiAdapter(
            CentralReadService(PostgresCentralReadAdapter(lambda: None)),
            access_policy=ApiAccessPolicy(PLATFORM_API_OPERATIONS),
            principal=ApiPrincipal.from_permissions('node-A', ['platform:chamber']),
            chamber_write_service=CentralChamberWriteService(
                _RaisingWritePort(), clock=lambda: _FIXED_NOW.isoformat(),
                id_factory=lambda: 'fixed-id',
            ),
        )
        return TestClient(create_platform_app(adapter))

    def _post(self, exc):
        try:
            client = self._client_with_write_error(exc)
        except ImportError:  # pragma: no cover - fastapi 미설치 shard
            self.skipTest('fastapi not installed in this shard')
        return client.post('/platform/chambers/heartbeat', json={
            'chamber_id': 'chA', 'reported_status': 'idle',
        })

    def test_unknown_chamber_maps_to_404_not_found_problem(self):
        from fcc_test_contracts.common.api_error_codes import (
            PROBLEM_JSON_MEDIA_TYPE,
            ErrorCode,
        )
        resp = self._post(ChamberNotFoundError('unknown chamber_id'))
        self.assertEqual(404, resp.status_code)
        self.assertEqual(PROBLEM_JSON_MEDIA_TYPE, self._media(resp))
        self.assertEqual(ErrorCode.NOT_FOUND.value, resp.json()['code'])

    def test_backend_failure_still_maps_to_503_problem(self):
        from fcc_test_contracts.common.api_error_codes import (
            PROBLEM_JSON_MEDIA_TYPE,
            ErrorCode,
        )
        resp = self._post(ChamberWriteError('central unreachable'))
        self.assertEqual(503, resp.status_code)
        self.assertEqual(PROBLEM_JSON_MEDIA_TYPE, self._media(resp))
        self.assertEqual(ErrorCode.UPSTREAM_UNAVAILABLE.value, resp.json()['code'])

    def test_openapi_declares_the_404_for_heartbeat(self):
        schema = build_platform_openapi_schema()
        responses = schema['paths']['/platform/chambers/heartbeat']['post']['responses']
        self.assertIn(
            '404', responses,
            'heartbeat 가 404 를 낼 수 있는데 계약에 없으면 생성 클라이언트가 '
            '그 분기를 영원히 모른다.',
        )
        self.assertIn('chamber', responses['404']['description'].lower())


# ════════════════════════════════════════════════════════════════════════════
# 라이브 PostgreSQL 실증 (부채 청산 M2 companion, 2026-07-30) — env 게이트
#
# 위 SQLite fixture 는 조건부 heartbeat INSERT 가 *동작한다*는 것을 보여주지만, 그
# 문장의 파라미터 타입이 실제 PostgreSQL 에서 INSERT 대상 컬럼으로부터 추론되는지는
# 보여주지 못한다(SQLite 는 타입에 관대하다). 그것이 안 되면 조건부 INSERT 는 실서버
# 에서 통째로 실패하고, heartbeat 경로 전체가 죽는다 — SQLite 만으로 배포할 수 없는
# 종류의 가정이다. ``FCC_KEYSET_PROOF_DB_URL`` 미설정 시 skip(사유 출력).
# ════════════════════════════════════════════════════════════════════════════
_LIVE_PROOF_ENV = 'FCC_KEYSET_PROOF_DB_URL'
_LIVE_PROOF_DSN = os.environ.get(_LIVE_PROOF_ENV, '').strip()


@unittest.skipUnless(
    _LIVE_PROOF_DSN,
    f'{_LIVE_PROOF_ENV} not set — live PostgreSQL heartbeat gate proof skipped',
)
class TestConditionalHeartbeatInsertLivePostgres(unittest.TestCase):
    """존재 게이트가 SQLite 전용 산물이 아님을 실 PG 로 실증."""

    @classmethod
    def setUpClass(cls):
        scripts_dir = str(Path(__file__).parent.parent / 'scripts')
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from platform_keyset_cursor_live_proof import run_live_proof

        cls.evidence = run_live_proof(_LIVE_PROOF_DSN, proof_seed='pytest-chamber')

    def test_registered_chamber_writes_exactly_one_row(self):
        insert = self.evidence['conditional_heartbeat_insert']
        self.assertEqual(1, insert['registered_chamber_rows_written'])

    def test_unregistered_chamber_writes_no_row_and_raises_not_found(self):
        insert = self.evidence['conditional_heartbeat_insert']
        self.assertEqual(0, insert['unregistered_chamber_rows_written'])
        self.assertTrue(insert['unregistered_raises_not_found'])


if __name__ == '__main__':
    unittest.main()
