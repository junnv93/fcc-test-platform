"""FE-P3-write (2026-05-27) — Central Platform Claim WRITE API contract.

Seals the new write surface that turns the FE-P3 claim-lock UX from a read-only
*visualization* into an *enforcement* mechanism: acquire/release on the central
``claim_events`` append-only ledger.

Coverage:
  - append-only SQL: the only write verb the adapter emits is ``INSERT INTO
    claim_events``; the open-claim lookups are SELECT-only; columns cross-checked
    against the central schema SSOT (docs/platform/central_db_schema.v1.json).
  - atomic check-and-append against a real SQLite fixture (the verbatim
    ``active_claims`` view SELECT), exercising acquire-conflict + release-pairing.
  - application logic: acquire conflict (different operator → 409), idempotent
    re-acquire (same operator), release pairing (no open claim → 409), uuid/field
    boundary validation, the concurrent-acquire race surfaced as a conflict.
  - OpenAPI 3.1 contract: POST routes, requestBody, 409 response, platform:claim
    permission token (distinct from platform:read), artifact byte-identity.
  - AuthZ: platform:claim enforcement (allowed / read-only-denied / anonymous).
  - composition wires the ClaimWriteService into the adapter.
  - FastAPI route wire: POST acquire/release status codes (200 / 409 / 400 / 403).

The read surface's TestAdapterReadOnlyAndPurity globs ``application/platform/*.py``
so the new write modules are auto-covered for no-local-SQLite / no-recompute /
no-module-level-psycopg purity; this file adds write-specific guards.
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _REPO_ROOT / 'src'
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from fcc_test_contracts.common.tree_artifacts import (
    resolve_dependency_artifact,
    resolve_repo_artifact,
)  # noqa: E402
from fcc_test_contracts.common.sqlite_connection_factory import SqliteConnectionContext  # noqa: E402

from fcc_test_contracts.common.access_policy import ApiAccessPolicy, ApiPrincipal  # noqa: E402
from fcc_test_kernel.application.central_contract.api_contracts import (  # noqa: E402
    PLATFORM_API_OPERATIONS,
    PLATFORM_API_PATH_PARAMS,
    PLATFORM_API_PERMISSIONS,
    PLATFORM_API_ROUTES,
    PLATFORM_API_SCHEMAS,
)
from fcc_test_platform.application.api_schema import build_platform_openapi_schema  # noqa: E402
from fcc_test_platform.application.central_claim_write_adapter import (  # noqa: E402
    CLAIM_EVENT_COLUMNS,
    INSERT_CLAIM_EVENT_SQL,
    OPEN_CLAIM_BY_CONDITION_SQL,
    OPEN_CLAIM_BY_ID_SQL,
    PostgresCentralClaimWriteAdapter,
)
from fcc_test_platform.application.central_claim_write_service import (  # noqa: E402
    ClaimConflictError,
    ClaimPairingError,
    ClaimWriteService,
)
from fcc_test_platform.application.central_read_adapter import (  # noqa: E402
    ACTIVE_CLAIMS_VIEW,
    COVERAGE_VIEW,
)
from domain.ports.output.central_claim_write_port import (  # noqa: E402
    CentralClaimWritePort,
    ClaimWriteError,
)


_P1 = '11111111-1111-1111-1111-111111111111'
_P2 = '22222222-2222-2222-2222-222222222222'

_SCHEMA_JSON = _REPO_ROOT / 'docs' / 'platform' / 'central_db_schema.v1.json'
_PLATFORM_PKG = resolve_repo_artifact(__file__, 'src/application/platform')
_WRITE_ADAPTER_MODULE = resolve_repo_artifact(__file__, 'src/application/platform/central_claim_write_adapter.py')
_ARTIFACT = resolve_dependency_artifact('docs/api/platform-api.openapi.json')


def _schema() -> dict:
    return json.loads(_SCHEMA_JSON.read_text(encoding='utf-8'))


def _canonical_text(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + '\n'


# ── Real SQLite fixture (mirrors the read test) ─────────────────────────────


def _pg_view_to_sqlite(select_sql: str) -> str:
    return select_sql.replace('= true', '= 1').replace('IS NOT DISTINCT FROM', 'IS')


from support.central_pg_sqlite_shim import QmarkConnection  # noqa: E402


def _build_claim_sqlite_fixture(db_path: str) -> None:
    """claim_events table + active_claims view + audit_events table.

    audit_events is REQUIRED by the FE-P8 audit atomicity contract — the
    claim write adapter, wired with the central audit writer in the
    composition root, INSERTs a ``claim.acquired`` / ``claim.released`` row
    in the same transaction as the primary claim INSERT. Tests that exercise
    the composition (TestClaimWriteComposition) need this table or every
    claim write rolls back on the audit INSERT.
    """
    schema = _schema()
    claim_select = _pg_view_to_sqlite(schema['views'][ACTIVE_CLAIMS_VIEW]['select'])
    with SqliteConnectionContext(db_path) as conn:
        conn.execute(
            'CREATE TABLE claim_events ('
            'id TEXT PRIMARY KEY, claim_id TEXT, project_id TEXT, technology TEXT, '
            'condition_hash TEXT, operator TEXT, action TEXT, reason TEXT, '
            'occurred_at TEXT, expires_at TEXT, session_id TEXT, created_at TEXT)'
        )
        conn.execute(f'CREATE VIEW "{ACTIVE_CLAIMS_VIEW}" AS {claim_select}')
        # FE-P8 — audit_events mirrors the central schema columns so the
        # audit INSERT in the claim write transaction succeeds.
        conn.execute(
            'CREATE TABLE audit_events ('
            'id TEXT PRIMARY KEY, event_type TEXT NOT NULL, project_id TEXT, '
            'actor_subject TEXT NOT NULL, target_user_subject TEXT, '
            'target_claim_id TEXT, role_key TEXT, detail_json TEXT, '
            'occurred_at TEXT NOT NULL, created_at TEXT NOT NULL)'
        )
        conn.execute(
            'CREATE TABLE users ('
            'id TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL, '
            'display_name TEXT, email TEXT, enabled INTEGER NOT NULL DEFAULT 1, '
            'created_at TEXT, updated_at TEXT, UNIQUE (issuer, subject))'
        )
        conn.commit()


def _ledger_rows(db_path: str) -> list[tuple]:
    with SqliteConnectionContext(db_path) as conn:
        cur = conn.execute(
            'SELECT claim_id, action, operator, condition_hash FROM claim_events '
            'ORDER BY occurred_at, action'
        )
        return cur.fetchall()


class _ClaimFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name
        _build_claim_sqlite_fixture(self.db_path)
        self.factory = lambda: QmarkConnection(self.db_path)
        self.write_adapter = PostgresCentralClaimWriteAdapter(self.factory)
        self.service = ClaimWriteService(self.write_adapter)

    def tearDown(self) -> None:
        Path(self.db_path).unlink(missing_ok=True)


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# ── A dependency-free fake write port for service-logic isolation ───────────


class _FakeClaimWritePort:
    """Records inserts; scriptable held/open claims for conflict + pairing."""

    def __init__(self) -> None:
        self.inserted: list[dict] = []
        # FE-P8: audit records arrive on a separate channel so the existing
        # ``len(self.inserted) == 1`` assertions (one ledger row per acquire)
        # stay intact while the new fields are still inspectable.
        self.audit_records: list[dict] = []
        self._held_by_condition: dict[str, dict] = {}
        self._open_by_id: dict[str, dict] = {}

    def hold_condition(self, condition_hash: str, claim: dict) -> None:
        self._held_by_condition[condition_hash] = claim

    def open_claim(self, claim_id: str, claim: dict) -> None:
        self._open_by_id[claim_id] = claim

    def acquire_claim_if_unclaimed(self, record, audit_record=None):
        held = self._held_by_condition.get(record['condition_hash'])
        if held is not None:
            return dict(held)
        self.inserted.append(dict(record))
        if audit_record is not None:
            self.audit_records.append(dict(audit_record))
        return None

    def release_open_claim(self, project_id, claim_id, *, event_id, operator,
                           reason, occurred_at, created_at, action='released',
                           audit_record=None):
        open_claim = self._open_by_id.get(claim_id)
        if open_claim is None:
            return None
        record = {
            'id': event_id, 'claim_id': claim_id, 'project_id': project_id,
            'technology': open_claim.get('technology'),
            'condition_hash': open_claim.get('condition_hash'),
            'operator': operator or open_claim.get('operator'),
            'action': action, 'reason': reason, 'occurred_at': occurred_at,
            'expires_at': None, 'session_id': open_claim.get('session_id'),
            'created_at': created_at,
        }
        self.inserted.append(record)
        if audit_record is not None:
            self.audit_records.append(dict(audit_record))
        return record


# ════════════════════════════════════════════════════════════════════════════
# 1. Append-only SQL + schema column cross-check
# ════════════════════════════════════════════════════════════════════════════
class TestClaimWriteSqlAppendOnly(unittest.TestCase):
    def test_insert_sql_is_append_only(self):
        upper = INSERT_CLAIM_EVENT_SQL.upper()
        self.assertTrue(upper.lstrip().startswith('INSERT INTO'), INSERT_CLAIM_EVENT_SQL)
        # Append-only: a fresh ledger row each event — never mutate history.
        for verb in ('UPDATE', 'DELETE', 'ON CONFLICT', 'UPSERT', 'REFRESH', 'DROP', 'TRUNCATE', 'MERGE'):
            self.assertNotIn(verb, upper, f'claim ledger write must be plain INSERT — found {verb!r}')

    def test_open_claim_lookups_are_select_only(self):
        for sql in (OPEN_CLAIM_BY_CONDITION_SQL, OPEN_CLAIM_BY_ID_SQL):
            upper = sql.upper()
            self.assertTrue(upper.lstrip().startswith('SELECT'), sql)
            for verb in ('INSERT', 'UPDATE', 'DELETE', 'REFRESH', 'DROP', 'TRUNCATE', 'MERGE'):
                self.assertNotIn(verb, upper, f'open-claim lookup must be SELECT-only — found {verb!r}')

    def test_columns_match_schema_ssot(self):
        schema_columns = set(_schema()['tables']['claim_events']['columns'])
        self.assertEqual(
            set(CLAIM_EVENT_COLUMNS), schema_columns,
            'claim ledger write columns must equal the central claim_events schema columns',
        )

    def test_open_claim_lookup_targets_active_claims_view_not_a_table(self):
        # The open-claim guard must read the dedup VIEW (the SSOT for "still held"),
        # not re-implement the dedup against the raw ledger.
        self.assertIn(f'"{ACTIVE_CLAIMS_VIEW}"', OPEN_CLAIM_BY_CONDITION_SQL)
        self.assertIn(f'"{ACTIVE_CLAIMS_VIEW}"', OPEN_CLAIM_BY_ID_SQL)
        self.assertNotIn(COVERAGE_VIEW, OPEN_CLAIM_BY_ID_SQL)


# ════════════════════════════════════════════════════════════════════════════
# 2. Write-specific purity (frozen-exe) + Port conformance
# ════════════════════════════════════════════════════════════════════════════
class TestClaimWritePurity(unittest.TestCase):
    def test_write_adapter_no_postgres_driver_import(self):
        offenders = {
            n for n in _module_imports(_WRITE_ADAPTER_MODULE)
            if n.split('.')[0] in {'psycopg', 'psycopg2', 'asyncpg'}
        }
        self.assertEqual(offenders, set(), offenders)

    def test_adapter_satisfies_write_port(self):
        adapter = PostgresCentralClaimWriteAdapter(lambda: None)
        self.assertIsInstance(adapter, CentralClaimWritePort)

    def test_factory_must_be_callable(self):
        with self.assertRaises(ValueError):
            PostgresCentralClaimWriteAdapter(None)  # type: ignore[arg-type]


# ════════════════════════════════════════════════════════════════════════════
# 3. Service decision logic (fake port — no DB)
# ════════════════════════════════════════════════════════════════════════════
class TestClaimWriteServiceLogic(unittest.TestCase):
    def setUp(self) -> None:
        self.port = _FakeClaimWritePort()
        # Deterministic clock + id factory for stable assertions.
        self._ids = iter([f'id-{i}' for i in range(100)])
        self.service = ClaimWriteService(
            self.port, clock=lambda: '2026-05-27T00:00:00+00:00',
            id_factory=lambda: next(self._ids),
        )

    def test_acquire_free_condition_appends_acquired(self):
        env = self.service.acquire(
            _P1, technology='BLE', condition_hash='h1', operator='op1',
        )
        self.assertEqual(env['action'], 'acquired')
        self.assertEqual(env['operator'], 'op1')
        self.assertEqual(env['condition_hash'], 'h1')
        self.assertEqual(len(self.port.inserted), 1)
        self.assertEqual(self.port.inserted[0]['action'], 'acquired')

    def test_acquire_contended_different_operator_conflict(self):
        self.port.hold_condition('h1', {
            'project_id': _P1, 'claim_id': 'cX', 'technology': 'BLE',
            'condition_hash': 'h1', 'operator': 'someone-else',
            'occurred_at': 't', 'expires_at': None, 'session_id': None,
        })
        with self.assertRaises(ClaimConflictError):
            self.service.acquire(_P1, technology='BLE', condition_hash='h1', operator='op1')
        # Conflict must NOT append a duplicate acquire.
        self.assertEqual(self.port.inserted, [])

    def test_acquire_contended_same_operator_idempotent(self):
        self.port.hold_condition('h1', {
            'project_id': _P1, 'claim_id': 'cX', 'technology': 'BLE',
            'condition_hash': 'h1', 'operator': 'op1',
            'occurred_at': 't', 'expires_at': None, 'session_id': None,
        })
        env = self.service.acquire(_P1, technology='BLE', condition_hash='h1', operator=' OP1 ')
        # Normalized operator match → idempotent: returns existing claim, no insert.
        self.assertEqual(env['claim_id'], 'cX')
        self.assertEqual(self.port.inserted, [])

    def test_release_open_claim_appends_released(self):
        claim_id = '33333333-3333-3333-3333-333333333333'
        self.port.open_claim(claim_id, {
            'technology': 'BLE', 'condition_hash': 'h1', 'operator': 'op1',
            'session_id': None,
        })
        env = self.service.release(_P1, claim_id)
        self.assertEqual(env['action'], 'released')
        self.assertEqual(env['claim_id'], claim_id)
        self.assertEqual(len(self.port.inserted), 1)

    def test_release_no_open_claim_pairing_error(self):
        with self.assertRaises(ClaimPairingError):
            self.service.release(_P1, '33333333-3333-3333-3333-333333333333')
        self.assertEqual(self.port.inserted, [])

    def test_expire_action_supported(self):
        claim_id = '33333333-3333-3333-3333-333333333333'
        self.port.open_claim(claim_id, {
            'technology': 'BLE', 'condition_hash': 'h1', 'operator': 'op1', 'session_id': None,
        })
        env = self.service.release(_P1, claim_id, action='expired')
        self.assertEqual(env['action'], 'expired')

    def test_bad_project_uuid_value_error(self):
        with self.assertRaises(ValueError):
            self.service.acquire('not-a-uuid', technology='BLE', condition_hash='h1', operator='op1')

    def test_bad_claim_uuid_value_error(self):
        with self.assertRaises(ValueError):
            self.service.release(_P1, 'not-a-uuid')

    def test_missing_required_fields_value_error(self):
        for kwargs in (
            {'technology': '', 'condition_hash': 'h1', 'operator': 'op'},
            {'technology': 'BLE', 'condition_hash': '', 'operator': 'op'},
            {'technology': 'BLE', 'condition_hash': 'h1', 'operator': ''},
        ):
            with self.assertRaises(ValueError):
                self.service.acquire(_P1, **kwargs)

    def test_invalid_release_action_value_error(self):
        with self.assertRaises(ValueError):
            self.service.release(_P1, '33333333-3333-3333-3333-333333333333', action='deleted')


# ════════════════════════════════════════════════════════════════════════════
# 4. Atomic check-and-append against a real SQLite ledger
# ════════════════════════════════════════════════════════════════════════════
class TestClaimWriteEndToEnd(_ClaimFixture):
    def test_acquire_then_conflicting_acquire_by_other_operator(self):
        first = self.service.acquire(_P1, technology='BLE', condition_hash='h1', operator='alice')
        self.assertEqual(first['action'], 'acquired')
        # A different engineer cannot acquire the same held condition.
        with self.assertRaises(ClaimConflictError):
            self.service.acquire(_P1, technology='BLE', condition_hash='h1', operator='bob')
        # Only one acquired row in the ledger.
        rows = _ledger_rows(self.db_path)
        self.assertEqual([r for r in rows if r[1] == 'acquired'].__len__(), 1)

    def test_acquire_release_reacquire_cycle(self):
        first = self.service.acquire(_P1, technology='BLE', condition_hash='h1', operator='alice')
        self.service.release(_P1, first['claim_id'], operator='alice')
        # After release the condition is free → bob can acquire.
        second = self.service.acquire(_P1, technology='BLE', condition_hash='h1', operator='bob')
        self.assertEqual(second['action'], 'acquired')
        self.assertNotEqual(second['claim_id'], first['claim_id'])
        actions = [r[1] for r in _ledger_rows(self.db_path)]
        self.assertEqual(sorted(actions), ['acquired', 'acquired', 'released'])

    def test_double_release_is_pairing_error(self):
        first = self.service.acquire(_P1, technology='BLE', condition_hash='h1', operator='alice')
        self.service.release(_P1, first['claim_id'])
        with self.assertRaises(ClaimPairingError):
            self.service.release(_P1, first['claim_id'])

    def test_same_operator_reacquire_no_duplicate_row(self):
        self.service.acquire(_P1, technology='BLE', condition_hash='h1', operator='alice')
        self.service.acquire(_P1, technology='BLE', condition_hash='h1', operator='alice')
        rows = [r for r in _ledger_rows(self.db_path) if r[1] == 'acquired']
        self.assertEqual(len(rows), 1)  # idempotent re-acquire — no duplicate ledger row

    def test_loud_fail_on_connection_error(self):
        def _boom():
            raise RuntimeError('db down')

        adapter = PostgresCentralClaimWriteAdapter(_boom)
        service = ClaimWriteService(adapter)
        with self.assertRaises(ClaimWriteError):
            service.acquire(_P1, technology='BLE', condition_hash='h1', operator='alice')


# ════════════════════════════════════════════════════════════════════════════
# 5. OpenAPI contract (POST routes + requestBody + 409 + permission)
# ════════════════════════════════════════════════════════════════════════════
class TestClaimWriteOpenApi(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = build_platform_openapi_schema(None)

    def test_write_routes_present_and_post(self):
        self.assertEqual(PLATFORM_API_ROUTES['acquire_project_claim'][0], 'POST')
        self.assertEqual(PLATFORM_API_ROUTES['release_project_claim'][0], 'POST')
        # acquire shares /claims with the list GET (one path item, two methods).
        claims_path = self.schema['paths']['/platform/projects/{project_id}/claims']
        self.assertIn('get', claims_path)
        self.assertIn('post', claims_path)

    def test_request_body_declared_for_writes(self):
        claims_post = self.schema['paths']['/platform/projects/{project_id}/claims']['post']
        ref = claims_post['requestBody']['content']['application/json']['schema']['$ref']
        self.assertEqual(ref, '#/components/schemas/AcquireClaimRequest')
        release_post = self.schema['paths'][
            '/platform/projects/{project_id}/claims/{claim_id}/release'
        ]['post']
        rel_ref = release_post['requestBody']['content']['application/json']['schema']['$ref']
        self.assertEqual(rel_ref, '#/components/schemas/ReleaseClaimRequest')

    def test_conflict_409_response_declared(self):
        claims_post = self.schema['paths']['/platform/projects/{project_id}/claims']['post']
        self.assertIn('409', claims_post['responses'])

    def test_claim_permission_is_distinct_write_token(self):
        self.assertEqual(PLATFORM_API_PERMISSIONS['acquire_project_claim'], 'platform:claim')
        self.assertEqual(PLATFORM_API_PERMISSIONS['release_project_claim'], 'platform:claim')
        # platform:claim ≠ platform:read (a viewer cannot mutate).
        self.assertNotEqual(
            PLATFORM_API_PERMISSIONS['acquire_project_claim'],
            PLATFORM_API_PERMISSIONS['get_project_coverage'],
        )

    def test_response_envelope_schema_present(self):
        for name in ('AcquireClaimRequest', 'ReleaseClaimRequest', 'ClaimEventEnvelope'):
            self.assertIn(name, PLATFORM_API_SCHEMAS)
        self.assertIn('ClaimEventEnvelope', self.schema['components']['schemas'])

    def test_claim_id_path_param_is_uuid(self):
        self.assertEqual(PLATFORM_API_PATH_PARAMS['claim_id']['format'], 'uuid')

    def test_artifact_byte_identical(self):
        self.assertEqual(
            _ARTIFACT.read_text(encoding='utf-8'), _canonical_text(self.schema),
            'platform-api.openapi.json drifted — run '
            'python scripts/export_session_api_schemas.py',
        )


# ════════════════════════════════════════════════════════════════════════════
# 6. AuthZ — platform:claim enforcement
# ════════════════════════════════════════════════════════════════════════════
class TestClaimWriteAuthZ(_ClaimFixture):
    def _adapter(self, principal):
        from fcc_test_platform.api.platform_routes import (
            PlatformApiAdapter,
            PlatformAuthorizationError,
        )
        self._PlatformAuthorizationError = PlatformAuthorizationError
        from fcc_test_platform.application.central_read_service import CentralReadService
        from fcc_test_platform.application.central_read_adapter import PostgresCentralReadAdapter
        read_service = CentralReadService(PostgresCentralReadAdapter(self.factory))
        return PlatformApiAdapter(
            read_service,
            access_policy=ApiAccessPolicy(PLATFORM_API_OPERATIONS),
            principal=principal,
            claim_write_service=self.service,
        )

    def test_claim_permission_allows_acquire(self):
        adapter = self._adapter(ApiPrincipal.from_permissions('eng-1', ['platform:claim']))
        env = adapter.acquire_project_claim(_P1, {
            'technology': 'BLE', 'condition_hash': 'h1', 'operator': 'eng-1',
        })
        self.assertEqual(env['action'], 'acquired')

    def test_read_only_permission_denied_for_acquire(self):
        adapter = self._adapter(ApiPrincipal.from_permissions('viewer', ['platform:read']))
        with self.assertRaises(self._PlatformAuthorizationError):
            adapter.acquire_project_claim(_P1, {
                'technology': 'BLE', 'condition_hash': 'h1', 'operator': 'viewer',
            })

    def test_anonymous_denied_for_release(self):
        adapter = self._adapter(ApiPrincipal.anonymous())
        with self.assertRaises(self._PlatformAuthorizationError):
            adapter.release_project_claim(_P1, '33333333-3333-3333-3333-333333333333', {})


# ════════════════════════════════════════════════════════════════════════════
# 7. Composition wires the claim write service
# ════════════════════════════════════════════════════════════════════════════
class TestClaimWriteComposition(_ClaimFixture):
    def _config(self):
        from fcc_test_contracts.common.auth_config import HttpAuthConfig
        from fcc_test_platform.central_db_config import CentralDbConfig
        from fcc_test_platform.application.runtime_config import PlatformApiConfig
        return PlatformApiConfig(
            central=CentralDbConfig(database_url='', provider_id='prov'),
            auth=HttpAuthConfig(auth_mode='trusted_headers'),
        )

    def test_runtime_adapter_has_claim_write_service(self):
        from fcc_test_platform.api_composition import create_platform_runtime
        runtime = create_platform_runtime(self._config(), connection_factory=self.factory)
        self.assertIsNotNone(runtime.api_adapter._claim_write_service)

    def test_composed_adapter_acquires_through_central_ledger(self):
        from fcc_test_platform.api_composition import create_platform_runtime
        runtime = create_platform_runtime(self._config(), connection_factory=self.factory)
        # Authorize is open at the adapter level only when no policy; here a policy
        # exists, so supply a principal view with the claim permission.
        view = runtime.api_adapter.with_principal(
            ApiPrincipal.from_permissions('eng', ['platform:claim'])
        )
        env = view.acquire_project_claim(_P1, {
            'technology': 'BLE', 'condition_hash': 'h1', 'operator': 'eng',
        })
        self.assertEqual(env['action'], 'acquired')


# ════════════════════════════════════════════════════════════════════════════
# 8. FastAPI route wire — POST acquire/release status codes
# ════════════════════════════════════════════════════════════════════════════
class TestClaimWriteRouteWire(_ClaimFixture):
    def setUp(self):
        super().setUp()
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest('fastapi not installed in this shard')
        from fcc_test_platform.api.platform_routes import (
            PlatformApiAdapter,
            create_platform_router,
        )
        from fcc_test_platform.application.central_read_service import CentralReadService
        from fcc_test_platform.application.central_read_adapter import PostgresCentralReadAdapter
        read_service = CentralReadService(PostgresCentralReadAdapter(self.factory))
        adapter = PlatformApiAdapter(read_service, claim_write_service=self.service)
        app = FastAPI()
        app.include_router(create_platform_router(adapter))
        self.client = TestClient(app)
        self._claims_url = f'/platform/projects/{_P1}/claims'

    def test_acquire_returns_200_and_envelope(self):
        resp = self.client.post(self._claims_url, json={
            'technology': 'BLE', 'condition_hash': 'h1', 'operator': 'alice',
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['action'], 'acquired')
        self.assertTrue(body['claim_id'])

    def test_conflicting_acquire_returns_409(self):
        self.client.post(self._claims_url, json={
            'technology': 'BLE', 'condition_hash': 'h1', 'operator': 'alice',
        })
        resp = self.client.post(self._claims_url, json={
            'technology': 'BLE', 'condition_hash': 'h1', 'operator': 'bob',
        })
        self.assertEqual(resp.status_code, 409)

    def test_release_round_trip(self):
        acq = self.client.post(self._claims_url, json={
            'technology': 'BLE', 'condition_hash': 'h1', 'operator': 'alice',
        }).json()
        claim_id = acq['claim_id']
        resp = self.client.post(
            f'/platform/projects/{_P1}/claims/{claim_id}/release', json={'operator': 'alice'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['action'], 'released')

    def test_release_unknown_claim_returns_409(self):
        resp = self.client.post(
            f'/platform/projects/{_P1}/claims/33333333-3333-3333-3333-333333333333/release',
            json={},
        )
        self.assertEqual(resp.status_code, 409)

    def test_acquire_missing_fields_returns_400(self):
        resp = self.client.post(self._claims_url, json={'technology': 'BLE'})
        self.assertEqual(resp.status_code, 400)

    def test_acquire_bad_uuid_returns_400(self):
        resp = self.client.post('/platform/projects/not-a-uuid/claims', json={
            'technology': 'BLE', 'condition_hash': 'h1', 'operator': 'alice',
        })
        self.assertEqual(resp.status_code, 400)


# ════════════════════════════════════════════════════════════════════════════
# 9. Cross-language SSOT — the frontend claim token / helpers match the backend
# ════════════════════════════════════════════════════════════════════════════
class TestClaimWriteCrossLanguageSsot(unittest.TestCase):
    _PROJECTS_TSX = _REPO_ROOT / 'apps' / 'web' / 'src' / 'routes' / 'projects.tsx'
    _CLIENT_TS = _REPO_ROOT / 'apps' / 'web' / 'src' / 'api' / 'platform-client.ts'

    _PERMISSIONS_TS = _REPO_ROOT / 'apps' / 'web' / 'src' / 'api' / 'permissions.ts'

    def test_frontend_claim_permission_matches_backend(self):
        if not self._PROJECTS_TSX.is_file():
            self.skipTest('apps/web not present in this shard')
        token = PLATFORM_API_PERMISSIONS['acquire_project_claim']
        self.assertEqual(token, 'platform:claim')
        # The frontend mirrors the backend write permission token (cross-language
        # constant — cannot import Python from TS). Since B2 (RBAC parity SSOT,
        # 2026-06-13) the token literal lives ONLY in permissions.ts and routes
        # import the exported constant (the inline `const PERMISSION_… = '…'` in
        # routes was removed — see tests/test_rbac_parity.py for the full
        # set-equality seal). So assert (a) the SSOT declaration carries the
        # token and (b) projects.tsx consumes the constant, not the raw literal.
        permissions_ts = self._PERMISSIONS_TS.read_text(encoding='utf-8')
        self.assertIn(f"PERMISSION_PLATFORM_CLAIM = '{token}'", permissions_ts)
        text = self._PROJECTS_TSX.read_text(encoding='utf-8')
        self.assertIn('PERMISSION_PLATFORM_CLAIM', text)

    def test_frontend_client_has_write_helpers_on_post_paths(self):
        if not self._CLIENT_TS.is_file():
            self.skipTest('apps/web not present in this shard')
        text = self._CLIENT_TS.read_text(encoding='utf-8')
        self.assertIn('export async function acquireClaim', text)
        self.assertIn('export async function releaseClaim', text)
        # POST paths match PLATFORM_API_ROUTES.
        _, acquire_path = PLATFORM_API_ROUTES['acquire_project_claim']
        _, release_path = PLATFORM_API_ROUTES['release_project_claim']
        self.assertIn(acquire_path, text)
        self.assertIn(release_path, text)


# ════════════════════════════════════════════════════════════════════════════
# 10. Shared envelope helpers SSOT (DRY — read + write services delegate here)
# ════════════════════════════════════════════════════════════════════════════
class TestEnvelopeHelpersSsot(unittest.TestCase):
    def test_require_uuid_canonicalizes_and_rejects(self):
        from fcc_test_kernel.application.central_contract.envelope_helpers import require_uuid
        canon = require_uuid('11111111-1111-1111-1111-111111111111', 'project_id')
        self.assertEqual(canon, '11111111-1111-1111-1111-111111111111')
        with self.assertRaises(ValueError):
            require_uuid('', 'project_id')
        with self.assertRaises(ValueError):
            require_uuid('not-a-uuid', 'claim_id')

    def test_text_optional_int_helpers(self):
        from fcc_test_kernel.application.central_contract.envelope_helpers import (
            int_or_zero, optional_int, optional_text, text,
        )
        self.assertEqual(text(None), '')
        self.assertEqual(text(5), '5')
        self.assertIsNone(optional_text(None))
        self.assertEqual(optional_text('x'), 'x')
        self.assertEqual(int_or_zero(None), 0)
        self.assertEqual(int_or_zero(''), 0)
        self.assertEqual(int_or_zero('3'), 3)
        self.assertIsNone(optional_int(None))
        self.assertEqual(optional_int('2'), 2)

    def test_both_services_delegate_to_shared_helpers_no_local_dup(self):
        # DRY guard: neither service redefines the shared helpers locally.
        read_src = resolve_repo_artifact(
            __file__, 'src/application/platform/central_read_service.py',
        ).read_text(encoding='utf-8')
        write_src = resolve_repo_artifact(
            __file__, 'src/application/platform/central_claim_write_service.py',
        ).read_text(encoding='utf-8')
        for src in (read_src, write_src):
            self.assertIn('from fcc_test_kernel.application.central_contract.envelope_helpers import', src)
        # The uuid-validation logic lives once (envelope_helpers), not copied.
        self.assertNotIn('str(uuid.UUID(', read_src)
        self.assertNotIn('def _require_uuid', write_src)


# ════════════════════════════════════════════════════════════════════════════
# 11. SERIALIZABLE retry — race loser re-evaluates (→ 409) instead of 503
# ════════════════════════════════════════════════════════════════════════════
class _SerErr(Exception):
    sqlstate = '40001'  # PostgreSQL serialization_failure


class _NoopCursor:
    def execute(self, statement, parameters=()):
        return None

    def fetchall(self):
        return []

    def close(self):
        return None


class _CommitConn:
    """Connection whose commit raises a serialization error `fail_times` times."""

    def __init__(self, fail_times: int, counter: list) -> None:
        self._fail_times = fail_times
        self._counter = counter

    def cursor(self):
        return _NoopCursor()

    def commit(self):
        self._counter.append('commit')
        if len(self._counter) <= self._fail_times:
            raise _SerErr('serialization_failure')

    def rollback(self):
        return None

    def close(self):
        return None


class TestClaimWriteSerializationRetry(unittest.TestCase):
    def test_is_serialization_error_detection(self):
        from fcc_test_platform.application.central_claim_write_adapter import _is_serialization_error

        class ByPgcode(Exception):
            pgcode = '40001'

        class SerializationFailure(Exception):
            pass

        self.assertTrue(_is_serialization_error(_SerErr()))
        self.assertTrue(_is_serialization_error(ByPgcode()))
        self.assertTrue(_is_serialization_error(SerializationFailure()))
        self.assertFalse(_is_serialization_error(ValueError('bad input')))

    def test_serialization_failure_retries_then_succeeds(self):
        # First commit aborts (40001) → adapter opens a fresh transaction and the
        # second commit succeeds. The body runs once per attempt (re-evaluation).
        counter: list = []
        body_runs: list = []
        conns = [_CommitConn(fail_times=1, counter=counter), _CommitConn(0, counter)]
        adapter = PostgresCentralClaimWriteAdapter(lambda: conns.pop(0))

        def body(_cursor):
            body_runs.append(1)
            return {'attempt': len(body_runs)}

        result = adapter._in_transaction(body)
        self.assertEqual(len(body_runs), 2)  # retried once (re-read the winner)
        self.assertEqual(result, {'attempt': 2})

    def test_serialization_failure_exhausts_retries_raises_claim_write_error(self):
        counter: list = []
        adapter = PostgresCentralClaimWriteAdapter(
            lambda: _CommitConn(fail_times=99, counter=counter)
        )
        with self.assertRaises(ClaimWriteError):
            adapter._in_transaction(lambda _cursor: {'x': 1})


if __name__ == '__main__':
    unittest.main()
