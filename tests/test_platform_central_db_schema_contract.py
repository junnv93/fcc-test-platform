import json
import sqlite3
import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))
sys.path.insert(0, str(project_root / 'scripts'))

from fcc_test_contracts.common.sqlite_connection_factory import (  # noqa: E402
    SQLITE_IN_MEMORY_DB,
    SqliteConnectionFactory,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402

# Reuse the production `--rollback` convention parser SSOT.
from platform_db_migrate import parse_rollback_statements  # noqa: E402

MIGRATIONS_DIR = resolve_repo_artifact(__file__, 'docs/platform/migrations')

SCHEMA_PATH = project_root / 'docs' / 'platform' / 'central_db_schema.v1.json'
ADR_PATH = project_root / 'docs' / 'architecture' / 'frontend' / 'adr' / '0005-central-db-read-model.md'


class TestPlatformCentralDbSchemaContract(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        self.tables = self.schema['tables']

    def test_schema_names_required_platform_tables(self):
        required = {
            'providers',
            'projects',
            'device_models',
            'samples',
            'users',
            'roles',
            'permissions',
            'user_roles',
            'role_permissions',
            'test_sessions',
            'jobs',
            'measurement_results',
            'measurement_attempts',
            'claim_events',
            'project_membership',
            'artifacts',
            'report_runs',
            'report_outputs',
            'diagnostics',
        }

        self.assertEqual(self.schema['owner_repository'], 'fcc-test-platform')
        self.assertTrue(required.issubset(self.tables))

    def test_measurement_results_keep_provider_specific_data_as_json(self):
        columns = self.tables['measurement_results']['columns']

        for name in [
            'provider_id',
            'session_id',
            'provider_result_id',
            'test_name',
            'technology',
            'condition_json',
            'result_json',
            'verdict',
            'measured_at',
        ]:
            self.assertIn(name, columns)
        self.assertEqual(columns['condition_json']['type'], 'json')
        self.assertEqual(columns['result_json']['type'], 'json')
        self.assertTrue(columns['condition_json']['required'])
        self.assertTrue(columns['result_json']['required'])

    def test_artifacts_and_report_outputs_store_metadata_not_bytes(self):
        forbidden_binary_types = {'blob', 'binary', 'bytes', 'bytea'}
        for table_name in ['artifacts', 'report_outputs']:
            columns = self.tables[table_name]['columns']

            self.assertIn('relative_path', columns)
            self.assertIn('storage_backend', columns)
            self.assertIn('byte_size', columns)
            for column_name, spec in columns.items():
                self.assertNotIn(column_name, {'path_or_uri', 'absolute_path'})
                self.assertNotIn(str(spec['type']).lower(), forbidden_binary_types)

    def test_schema_has_browse_and_lookup_indexes(self):
        expected_indexes = {
            'providers': {'ux_providers_provider_id'},
            'test_sessions': {
                'ux_test_sessions_provider_chamber_session',
                'idx_test_sessions_project_sample',
            },
            'measurement_results': {
                'idx_measurement_results_session',
                'idx_measurement_results_technology_test',
            },
            'artifacts': {
                'ux_artifacts_provider_relative_path',
                'idx_artifacts_session_type',
            },
            'report_runs': {'idx_report_runs_session_status'},
            'diagnostics': {'idx_diagnostics_session_severity'},
        }

        for table_name, index_names in expected_indexes.items():
            actual = {
                index['name']
                for index in self.tables[table_name].get('indexes', [])
            }
            self.assertTrue(index_names <= actual, table_name)

    def test_platform_columns_do_not_encode_provider_algorithms(self):
        forbidden = set(self.schema['forbidden_platform_columns'])
        for table_name, table in self.tables.items():
            column_names = set(table['columns'])

            self.assertFalse(
                forbidden.intersection(column_names),
                f'{table_name} contains provider-private columns',
            )

    def test_schema_mentions_no_provider_private_modules(self):
        text = SCHEMA_PATH.read_text(encoding='utf-8')
        forbidden_fragments = [
            'src/measurements',
            'src/reporting/application',
            'src/infrastructure/adapters/driving/api/headless_routes.py',
            'PySide6',
            'SCPI sequencing',
        ]

        for fragment in forbidden_fragments:
            self.assertNotIn(fragment, text)


class TestFeP0aMeasurementAttempts(unittest.TestCase):
    """FE-P0a — append-only attempts ledger + condition_hash + operator + is_latest contract."""

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        self.tables = self.schema['tables']

    def test_measurement_attempts_table_is_append_only_with_required_columns(self):
        attempts = self.tables['measurement_attempts']

        self.assertTrue(attempts.get('append_only') is True)
        columns = attempts['columns']
        for name in (
            'id',
            'session_id',
            'condition_hash',
            'attempt_number',
            'is_latest',
            'operator',
            'status',
            'result_json',
            'project_id',
            'measured_at',
        ):
            self.assertIn(name, columns, name)

        self.assertEqual(columns['condition_hash']['type'], 'text')
        self.assertTrue(columns['condition_hash']['required'])
        self.assertEqual(columns['attempt_number']['type'], 'integer')
        self.assertTrue(columns['attempt_number']['required'])
        self.assertEqual(columns['is_latest']['type'], 'boolean')
        self.assertTrue(columns['is_latest']['required'])
        self.assertEqual(columns['project_id']['references'], 'projects.id')

    def test_measurement_attempts_indexes_cover_idempotency_and_latest_lookup(self):
        names = {
            index['name']
            for index in self.tables['measurement_attempts'].get('indexes', [])
        }

        for required in (
            'ux_measurement_attempts_idempotency_key',
            'ux_measurement_attempts_session_condition_attempt',
            'idx_measurement_attempts_project_condition_hash',
            'idx_measurement_attempts_is_latest',
            'idx_measurement_attempts_operator',
        ):
            self.assertIn(required, names)

    def test_measurement_results_gains_project_fk_condition_hash_and_operator(self):
        columns = self.tables['measurement_results']['columns']

        self.assertEqual(columns['project_id']['references'], 'projects.id')
        self.assertEqual(columns['condition_hash']['type'], 'text')
        self.assertEqual(columns['operator']['type'], 'text')

        names = {
            index['name']
            for index in self.tables['measurement_results'].get('indexes', [])
        }
        self.assertIn('idx_measurement_results_project_condition_hash', names)
        for index in self.tables['measurement_results'].get('indexes', []):
            if index['name'] == 'idx_measurement_results_project_condition_hash':
                self.assertEqual(index['columns'], ['project_id', 'condition_hash'])


class TestFeP0aClaimEventsAndActiveView(unittest.TestCase):
    """FE-P0a — append-only claim_events ledger + active_claims view contract."""

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

    def test_claim_events_table_is_append_only_with_event_columns(self):
        claim_events = self.schema['tables']['claim_events']

        self.assertTrue(claim_events.get('append_only') is True)
        columns = claim_events['columns']
        for name in (
            'id',
            'claim_id',
            'project_id',
            'technology',
            'condition_hash',
            'operator',
            'action',
            'occurred_at',
        ):
            self.assertIn(name, columns, name)
        self.assertEqual(columns['project_id']['references'], 'projects.id')
        self.assertTrue(columns['action']['required'])
        self.assertTrue(columns['claim_id']['required'])
        self.assertTrue(columns['operator']['required'])

    def test_active_claims_is_a_plain_view_not_materialized(self):
        views = self.schema.get('views', {})
        materialized_views = self.schema.get('materialized_views', {})

        self.assertIn('active_claims', views)
        self.assertNotIn('active_claims', materialized_views)
        self.assertIn('SELECT', views['active_claims']['select'].upper())
        self.assertIn('claim_events', views['active_claims']['select'])


class TestFeP0aCoverageMaterializedView(unittest.TestCase):
    """FE-P0a — coverage_by_condition_hash MATERIALIZED VIEW with explicit refresh policy."""

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

    def test_coverage_view_is_materialized_with_unique_index(self):
        materialized_views = self.schema.get('materialized_views', {})

        self.assertIn('coverage_by_condition_hash', materialized_views)
        view = materialized_views['coverage_by_condition_hash']
        self.assertIn('SELECT', view['select'].upper())
        self.assertIn('measurement_attempts', view['select'])
        self.assertIn('is_latest', view['select'])

        unique_indexes = [
            index for index in view.get('indexes', []) if index.get('unique')
        ]
        self.assertTrue(unique_indexes, 'materialized view must have a unique index for CONCURRENTLY refresh')

        unique_columns = unique_indexes[0]['columns']
        for column in ('project_id', 'technology', 'condition_hash'):
            self.assertIn(column, unique_columns)

    def test_coverage_view_refresh_policy_is_explicit(self):
        policies = self.schema.get('refresh_policies', {})
        policy = policies.get('coverage_by_condition_hash')

        self.assertIsNotNone(policy, 'coverage refresh policy must be declared (ADR-0005:158-162 미결 해소)')
        self.assertEqual(policy['trigger'], 'on_ingest')
        self.assertEqual(policy['fallback_interval'], 'PT1H')
        self.assertTrue(policy['concurrent_refresh'])
        self.assertIn('rationale', policy)

    def test_coverage_view_can_decide_duplicate_per_project_condition_hash(self):
        view = self.schema['materialized_views']['coverage_by_condition_hash']
        select = view['select']

        self.assertIn('project_id', select)
        self.assertIn('condition_hash', select)
        self.assertIn('latest_session_id', select)
        self.assertIn('latest_operator', select)
        self.assertIn('latest_measured_at', select)

    def test_coverage_view_declares_performance_budget(self):
        # FE-P0a /goal acceptance: the 16k+ rows × multi-session coverage
        # aggregation budget must be PINNED as a contract acceptance, not left
        # as prose. The other four acceptance items (materialized / refresh /
        # index / local↔central mapping) each have a dedicated invariant; this
        # is the fifth. A live PostgreSQL ratio benchmark cannot run in CI, so
        # the budget is declared here as the SSOT that the deployed environment
        # measures against and that the materialization/index strategy must not
        # silently regress past.
        view = self.schema['materialized_views']['coverage_by_condition_hash']
        budget = view.get('performance_budget')

        self.assertIsNotNone(
            budget,
            'coverage view must pin a performance budget as a contract acceptance '
            '(FE-P0a /goal — budget을 acceptance로 고정)',
        )
        self.assertEqual(budget['class'], 'background')

        # background budget multiplier range = 3-5× (/verify-core-column-projection convention).
        ratio_range = budget['ratio_multiplier_range_x']
        self.assertEqual(len(ratio_range), 2)
        self.assertEqual(ratio_range, [3, 5])
        self.assertLess(ratio_range[0], ratio_range[1])

        # The budget must name its basis (16k+ rows) and reference pattern so it
        # cannot drift into an unjustified magic number.
        self.assertIn('16k', budget['basis'])
        self.assertIn('verify-core-column-projection', budget['reference_pattern'])

        # Materialization is the budget-enforcing mechanism — the contract must
        # say so, otherwise a future refactor to a plain VIEW would silently
        # break the budget while still passing the "is materialized" test.
        self.assertIn('Materialization', budget['mechanism'])
        self.assertIn('CONCURRENTLY', budget['mechanism'])

        # A live ratio benchmark is deferred to the deployed PostgreSQL; the
        # contract must state why CI cannot run it (honest about the gap).
        self.assertEqual(budget['runtime_benchmark'], 'deferred-to-deployed')
        self.assertIn('PostgreSQL', budget['runtime_benchmark_rationale'])
        self.assertIn('0005-central-db-read-model.md', budget['rationale_ref'])


class TestFeP8ProjectMembershipLive(unittest.TestCase):
    """FE-P8 — project_membership is now the live RBAC table (reserved_for removed)."""

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

    def test_project_membership_reserved_marker_removed(self):
        membership = self.schema['tables']['project_membership']

        # FE-P8 makes project_membership a live, managed table — the stub marker
        # MUST be gone so consumers can rely on the assign/revoke contract.
        self.assertNotIn('reserved_for', membership)
        columns = membership['columns']
        for name in ('id', 'project_id', 'user_id', 'role_key', 'assigned_at', 'expires_at'):
            self.assertIn(name, columns)
        self.assertEqual(columns['project_id']['references'], 'projects.id')
        self.assertEqual(columns['user_id']['references'], 'users.id')
        # Unique on the natural composite key — the UPSERT target.
        names = {index['name'] for index in membership.get('indexes', [])}
        self.assertIn('ux_project_membership_project_user_role', names)


class TestFeP8RbacRoleGrants(unittest.TestCase):
    """FE-P8 — rbac_role_grants schema SSOT (role → platform permission mapping).

    The schema is the single source of truth; nothing in src/ may declare role
    grants. The DDL exporter renders this section as idempotent seed INSERTs and
    the project_member_permissions view joins through the resulting grant graph.
    """

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        self.rbac = self.schema['rbac_role_grants']

    def test_grants_define_the_project_role_catalog(self):
        grants = self.rbac['grants']

        # The role catalog MUST be exactly these four project-scoped roles. A new
        # role changes authorization semantics across the platform; adding one is
        # an EXPLICIT schema change acknowledged here, not a quiet drift. Phase D
        # (2026-06-23) added ``project_pm`` (PM 칸 write branch) + the two
        # sample-write permissions splitting PM ↔ 시험원 authority.
        self.assertEqual(
            set(grants),
            {'project_viewer', 'project_engineer', 'project_admin', 'project_pm'},
        )
        # The measurement chain stays monotonically widening — viewer ⊂ engineer
        # ⊂ admin — with the 시험원 intake-write folded into engineer.
        # 2026-08-08: ``platform:reference-write`` joins engineer (and therefore
        # admin). Reference authoring sat on ``platform:admin``, which put the
        # act in the project-management tier — but the person who re-measures a
        # cable loss after a re-cabling is the 시험원, and leaving it on admin
        # meant the one person who knows the new number could not record it.
        # 2026-08-10: ``platform:equipment-write`` joins engineer (and therefore
        # admin) on the SAME argument one step further along — the tester who
        # re-cables the room is also the one who reads the analyzer's new address
        # off the instrument. It is deliberately NOT a reuse of
        # ``platform:reference-write``: that token's membership half only opens
        # for PROJECT-scoped families, and a chamber is not a project (a room
        # outlives every project and one project spans two rooms).
        #
        # ⚠️ Worth knowing when reading this list: the equipment-config route is
        # chamber-scoped and carries no project_id, so this particular grant can
        # only ever be realized through an identity-provider group attribute, not
        # through project membership. It is still listed here because the
        # bijection below requires every grantable token to appear — the same
        # shape ``platform:reference-write`` already has for room-scoped families.
        self.assertEqual(set(grants['project_viewer']), {'platform:read'})
        self.assertEqual(
            set(grants['project_engineer']),
            {'platform:read', 'platform:claim', 'platform:sample-write',
             'platform:reference-write', 'platform:chamber-config-write'},
        )
        self.assertEqual(
            set(grants['project_admin']),
            {'platform:read', 'platform:claim', 'platform:admin',
             'platform:sample-write',
             'platform:reference-write', 'platform:chamber-config-write',
             'test_plan:read'},
        )
        # project_pm is the PM (logistics) branch — read + PM-칸 write, NOT a
        # measurement claimer (orthogonal to the engineer chain).
        self.assertEqual(
            set(grants['project_pm']),
            {'platform:read', 'platform:sample-write'},
        )
        # admin is the superset of every other role (no grant escapes admin).
        for role in ('project_viewer', 'project_engineer', 'project_pm'):
            self.assertTrue(set(grants[role]) <= set(grants['project_admin']))

    def test_every_grant_references_a_described_permission(self):
        descriptions = self.rbac['permission_descriptions']
        grants = self.rbac['grants']

        granted: set[str] = set()
        for role_permissions in grants.values():
            granted.update(role_permissions)
        for role_permissions in self.rbac['global_role_grants']['grants'].values():
            granted.update(role_permissions)
        # Each granted permission must be documented — otherwise the OpenAPI
        # security-scheme docs and the schema can drift.
        self.assertEqual(granted, set(descriptions))

    # 멀티챔버 P2 (2026-06-15) — node-scoped platform permissions that are
    # intentionally NOT project-membership grants. A chamber PC authenticates with
    # a machine token carrying platform:chamber to push heartbeats; it is not a
    # project member, so the project role catalog (viewer/engineer/admin) must NOT
    # grant it. This mirrors the headless 'public' sentinel exclusion in
    # test_rbac_parity — the project-role catalog covers project-scoped permissions,
    # while node-scoped permissions live only on the API gate. SSOT for the
    # exclusion rationale so a future node permission is added here deliberately.
    NODE_SCOPED_PLATFORM_PERMISSIONS = frozenset({'platform:chamber'})

    # ADR-0017 D3 (2026-06-22) — authorization CLASSES that are NOT grantable
    # tokens: "any resolved login" ('authenticated') gates project creation, a
    # GLOBAL operation that a brand-new project cannot gate by membership. Like the
    # headless 'public' sentinel (test_rbac_parity._NON_GRANTABLE_SENTINELS) and
    # the node-scoped chamber token, it is never listed in rbac_role_grants — so it
    # is excluded from the role-catalog cross-check. SSOT for the exclusion.
    NON_GRANTABLE_AUTHZ_CLASSES = frozenset({'authenticated', 'public'})

    def test_role_grants_match_platform_api_permissions(self):
        # The role catalog must cover EXACTLY the project-scoped permissions the
        # platform API declares (cross-source SSOT). Drift between rbac_role_grants
        # and PLATFORM_API_PERMISSIONS would leave some routes unreachable via
        # membership or grant unused permissions. Node-scoped permissions (e.g.
        # platform:chamber) and non-grantable authorization classes (e.g.
        # 'authenticated' — ADR-0017 D3) are excluded — they are machine/global
        # gates, not project role grants.
        from application.central_contract.api_contracts import PLATFORM_API_PERMISSIONS

        api_permissions = (
            set(PLATFORM_API_PERMISSIONS.values())
            - self.NODE_SCOPED_PLATFORM_PERMISSIONS
            - self.NON_GRANTABLE_AUTHZ_CLASSES
        )
        granted: set[str] = set()
        for role_permissions in self.rbac['grants'].values():
            granted.update(role_permissions)
        # ``test_plan:read`` is a headless operation permission deliberately
        # resolved by the same global Local Auth role catalog. Platform routes
        # must remain fully covered, while this one cross-surface read grant is
        # allowed as the explicit bridge to the headless contract.
        global_granted = set().union(*self.rbac['global_role_grants']['grants'].values())
        self.assertTrue(api_permissions <= granted | global_granted)
        self.assertEqual(granted - api_permissions, {'test_plan:read'})
        self.assertEqual(global_granted, {'platform:sample-hard-delete'})

    def test_node_scoped_permissions_are_not_granted_by_any_role(self):
        # Guard the exclusion: a node-scoped permission must never leak into a
        # project role grant (which would let a project member impersonate a
        # chamber node). Also proves the excluded token is actually declared by an
        # API operation (so the exclusion is not masking a typo'd dead token).
        from application.central_contract.api_contracts import PLATFORM_API_PERMISSIONS

        granted: set[str] = set()
        for role_permissions in self.rbac['grants'].values():
            granted.update(role_permissions)
        self.assertEqual(self.NODE_SCOPED_PLATFORM_PERMISSIONS & granted, set())
        self.assertTrue(
            self.NODE_SCOPED_PLATFORM_PERMISSIONS <= set(PLATFORM_API_PERMISSIONS.values()),
            'node-scoped permission(s) must be used by a platform API operation',
        )


class TestFeP8AuditEvents(unittest.TestCase):
    """FE-P8 — audit_events append-only ledger contract."""

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        self.table = self.schema['tables']['audit_events']

    def test_audit_events_is_append_only_with_required_columns(self):
        self.assertTrue(self.table.get('append_only') is True)
        columns = self.table['columns']
        for name in (
            'id',
            'event_type',
            'actor_subject',
            'occurred_at',
            'created_at',
        ):
            self.assertIn(name, columns)
            self.assertTrue(columns[name]['required'], name)
        # project_id / target_user_subject / target_claim_id / role_key /
        # detail_json are optional — different event types populate different
        # subsets, but the actor + when are always known.
        for name in ('project_id', 'target_user_subject', 'target_claim_id', 'role_key', 'detail_json'):
            self.assertIn(name, columns)
            self.assertFalse(columns[name]['required'], name)
        self.assertEqual(columns['project_id']['references'], 'projects.id')

    def test_event_type_allowed_values_cover_claim_and_membership(self):
        allowed = set(self.table['columns']['event_type']['allowed_values'])

        # The event types span the in-scope platform writes. Adding one
        # requires a deliberate schema change — this guards against silent
        # taxonomy bloat, and it did its job: 'account.unlocked' arrived on
        # 2026-08-23 and had to be argued for here rather than appearing.
        #
        # ⚠️ 'account.unlocked' (identity axis, ADR-0021 D-17) is deliberate and
        # is NOT project-scoped — a local account is not a project resource,
        # which is why project_id stays nullable on this table. It is the
        # administrator lifting a login lockout, and it is written in the same
        # transaction as the users UPDATE, so an unlock is durable iff its audit
        # is. ⚠️ A value the deployed CHECK does not know is not a weaker audit
        # trail — it rolls the unlock back — so the incremental migration
        # (027) must carry the identical set. `test_local_account_unlock.py`
        # asserts that set equality; this test owns the *deliberateness*.
        self.assertEqual(
            allowed,
            {
                'claim.acquired', 'claim.released', 'claim.expired',
                'membership.assigned', 'membership.revoked',
                'account.unlocked', 'sample.hard_deleted',
            },
        )

    def test_audit_events_indexes_support_browse(self):
        names = {index['name'] for index in self.table.get('indexes', [])}

        for required in (
            'idx_audit_events_project_occurred',
            'idx_audit_events_actor_occurred',
            'idx_audit_events_event_type_occurred',
        ):
            self.assertIn(required, names)


class TestFeP8ProjectMemberPermissionsView(unittest.TestCase):
    """FE-P8 — project_member_permissions view shape + live SQL semantic check.

    The view is a single-pass JOIN that resolves (project_id, user_subject) →
    platform permissions through the role grant graph. Authorization MUST be a
    single SELECT (no per-row N queries).
    """

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

    def test_project_member_permissions_view_is_declared(self):
        views = self.schema.get('views', {})

        self.assertIn('project_member_permissions', views)
        select = views['project_member_permissions']['select']
        # Joins through project_membership → users → roles → role_permissions → permissions.
        for fragment in (
            'project_membership',
            'users',
            'roles',
            'role_permissions',
            'permissions',
            'user_issuer',
            'user_subject',
            'user_enabled',
            'permission_key',
            'expires_at',
        ):
            self.assertIn(fragment, select, fragment)

    def test_view_resolves_role_grants_to_permissions(self):
        # Live SQL semantic check: a member with role 'project_engineer' must
        # see exactly the permissions {'platform:read', 'platform:claim'} via
        # the view. This catches a refactor that breaks the join graph silently.
        conn = SqliteConnectionFactory(':memory:').create()
        try:
            cur = conn.cursor()
            cur.executescript(
                """
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    issuer TEXT NOT NULL DEFAULT 'urn:fcc:identity:legacy',
                    subject TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    UNIQUE (issuer, subject)
                );
                CREATE TABLE roles (id TEXT PRIMARY KEY, role_key TEXT UNIQUE NOT NULL);
                CREATE TABLE permissions (id TEXT PRIMARY KEY, permission_key TEXT UNIQUE NOT NULL);
                CREATE TABLE role_permissions (role_id TEXT NOT NULL, permission_id TEXT NOT NULL);
                CREATE TABLE project_membership (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role_key TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    expires_at TEXT
                );
                """
            )
            # Seed: project_engineer → {platform:read, platform:claim}.
            cur.execute("INSERT INTO users VALUES ('u1','https://issuer.example','alice@corp',1)")
            cur.execute("INSERT INTO roles VALUES ('r1','project_engineer')")
            cur.execute("INSERT INTO permissions VALUES ('p1','platform:read'), ('p2','platform:claim')")
            cur.execute("INSERT INTO role_permissions VALUES ('r1','p1'), ('r1','p2')")
            cur.execute("INSERT INTO project_membership VALUES ('m1','proj-1','u1','project_engineer','2026-05-28T00:00:00Z',NULL)")
            select = self.schema['views']['project_member_permissions']['select']
            cur.execute(f'CREATE VIEW project_member_permissions AS {select}')
            rows = list(cur.execute(
                "SELECT permission_key FROM project_member_permissions "
                "WHERE project_id = ? AND user_issuer = ? AND user_subject = ? "
                "ORDER BY permission_key",
                ('proj-1', 'https://issuer.example', 'alice@corp'),
            ))
            self.assertEqual([r[0] for r in rows], ['platform:claim', 'platform:read'])
        finally:
            conn.close()


class TestFeP0aLocalCentralFieldMapping(unittest.TestCase):
    """FE-P0a — local SQLite ↔ central PostgreSQL field mapping table (Codex 2차 drift 위험 해소)."""

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        self.mapping = self.schema['local_central_field_mappings']

    def test_field_mapping_lists_required_attempt_correspondences(self):
        attempt_mapping = self.mapping['tables']['measurement_attempts']

        self.assertIn('models.py:410-470', attempt_mapping['local_module'])
        self.assertEqual(attempt_mapping['central_table'], 'measurement_attempts')

        mappings_text = json.dumps(attempt_mapping['mappings'], ensure_ascii=False)
        for required_term in (
            'id (Integer autoincrement)',
            'id (uuid)',
            'condition_id',
            'condition_hash',
            'project_id',
            'operator',
            'run_id',
            'idempotency_key',
        ):
            self.assertIn(required_term, mappings_text, required_term)

    def test_field_mapping_forbids_central_recompute_of_condition_hash(self):
        text = json.dumps(self.mapping, ensure_ascii=False)

        self.assertIn('compute_condition_hash', text)
        self.assertIn('Central never recomputes', text)

    def test_field_mapping_covers_measurement_results_projection(self):
        results_mapping = self.mapping['tables']['measurement_results']

        self.assertEqual(results_mapping['central_table'], 'measurement_results')
        mappings_text = json.dumps(results_mapping['mappings'], ensure_ascii=False)
        self.assertIn('project_id', mappings_text)
        self.assertIn('operator', mappings_text)


class TestFeP0aViewSqlSemantics(unittest.TestCase):
    """FE-P0a (Commit 3) — Live SQL semantic invariant. The view SELECT in the
    schema SSOT is executed against an SQLite fixture (PG/SQLite 양쪽 호환 dialect —
    ROW_NUMBER OVER + IS NOT DISTINCT FROM + correlated EXISTS) so that semantic
    regressions (e.g., outer alias collision, attempt_count always 1) are caught
    by contract, not by accidental observation in production.
    """

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        self.conn = SqliteConnectionFactory(':memory:').create()
        self.conn.row_factory = sqlite3.Row
        self._create_minimal_tables()
        self._create_views_from_schema()

    def tearDown(self):
        self.conn.close()

    def _create_minimal_tables(self):
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE claim_events (
                id TEXT PRIMARY KEY,
                claim_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                technology TEXT NOT NULL,
                condition_hash TEXT NOT NULL,
                operator TEXT NOT NULL,
                action TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                expires_at TEXT,
                session_id TEXT,
                reason TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE measurement_attempts (
                id TEXT PRIMARY KEY,
                provider_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                project_id TEXT,
                measurement_result_id TEXT,
                test_name TEXT NOT NULL,
                technology TEXT NOT NULL,
                condition_hash TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                is_latest INTEGER NOT NULL,
                operator TEXT,
                status TEXT NOT NULL,
                verdict TEXT,
                margin TEXT,
                result_json TEXT NOT NULL,
                run_id TEXT,
                idempotency_key TEXT,
                recorded_by TEXT,
                provenance_json TEXT,
                measured_at TEXT,
                created_at TEXT NOT NULL
            );
            """
        )

    def _create_views_from_schema(self):
        active_claims_select = self.schema['views']['active_claims']['select']
        coverage_select = self.schema['materialized_views']['coverage_by_condition_hash']['select']
        cur = self.conn.cursor()
        cur.execute(f'CREATE VIEW active_claims AS {active_claims_select}')
        cur.execute(f'CREATE VIEW coverage_by_condition_hash AS {coverage_select}')

    def _insert_claim_event(self, **fields):
        defaults = {
            'reason': None,
            'session_id': None,
            'expires_at': None,
            'created_at': fields.get('occurred_at'),
        }
        merged = {**defaults, **fields}
        columns = ', '.join(merged.keys())
        placeholders = ', '.join('?' for _ in merged)
        self.conn.execute(
            f'INSERT INTO claim_events ({columns}) VALUES ({placeholders})',
            tuple(merged.values()),
        )

    def _insert_attempt(self, **fields):
        defaults = {
            'project_id': 'proj-1',
            'measurement_result_id': None,
            'operator': 'op-1',
            'status': 'completed',
            'verdict': 'PASS',
            'margin': None,
            'result_json': '{}',
            'run_id': None,
            'idempotency_key': None,
            'recorded_by': None,
            'provenance_json': None,
            'measured_at': None,
            'created_at': '2026-05-25T00:00:00Z',
        }
        merged = {**defaults, **fields}
        merged['is_latest'] = 1 if merged.get('is_latest') else 0
        columns = ', '.join(merged.keys())
        placeholders = ', '.join('?' for _ in merged)
        self.conn.execute(
            f'INSERT INTO measurement_attempts ({columns}) VALUES ({placeholders})',
            tuple(merged.values()),
        )

    def test_active_claims_excludes_released_and_expired(self):
        # claim A: acquired then released -> not active
        self._insert_claim_event(id='e1', claim_id='A', project_id='p1', technology='BT',
                                 condition_hash='h1', operator='op1', action='acquired',
                                 occurred_at='2026-05-25T10:00:00Z')
        self._insert_claim_event(id='e2', claim_id='A', project_id='p1', technology='BT',
                                 condition_hash='h1', operator='op1', action='released',
                                 occurred_at='2026-05-25T10:30:00Z')
        # claim B: acquired then expired -> not active
        self._insert_claim_event(id='e3', claim_id='B', project_id='p1', technology='BT',
                                 condition_hash='h2', operator='op1', action='acquired',
                                 occurred_at='2026-05-25T11:00:00Z')
        self._insert_claim_event(id='e4', claim_id='B', project_id='p1', technology='BT',
                                 condition_hash='h2', operator='op1', action='expired',
                                 occurred_at='2026-05-25T11:30:00Z')
        # claim C: acquired only -> active
        self._insert_claim_event(id='e5', claim_id='C', project_id='p1', technology='BT',
                                 condition_hash='h3', operator='op2', action='acquired',
                                 occurred_at='2026-05-25T12:00:00Z')
        self.conn.commit()

        rows = list(self.conn.execute('SELECT claim_id, condition_hash FROM active_claims ORDER BY claim_id'))
        self.assertEqual([(row['claim_id'], row['condition_hash']) for row in rows], [('C', 'h3')])

    def test_active_claims_takes_latest_acquire_per_partition(self):
        # 같은 (project, condition_hash) 에 두 claim 이 acquire — 최신 acquire 가 winner.
        self._insert_claim_event(id='e1', claim_id='A', project_id='p1', technology='BT',
                                 condition_hash='h1', operator='op1', action='acquired',
                                 occurred_at='2026-05-25T10:00:00Z')
        self._insert_claim_event(id='e2', claim_id='B', project_id='p1', technology='BT',
                                 condition_hash='h1', operator='op2', action='acquired',
                                 occurred_at='2026-05-25T11:00:00Z')
        self.conn.commit()

        rows = list(self.conn.execute('SELECT claim_id, operator FROM active_claims'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['claim_id'], 'B')
        self.assertEqual(rows[0]['operator'], 'op2')

    def test_active_claims_outer_alias_unambiguous(self):
        # 결함 1 (commit ae743c1 의 잠재 결함) 회귀 가드: outer/inner 모두 claim_events 이면
        # qualified column 이 outer 를 가리켜야 한다. acquire(A, t=10) + release(A, t=5) 에서
        # release 가 acquire 보다 이전이므로 A 는 여전히 active 여야 한다 (later.occurred_at >
        # acquired.occurred_at 조건 — 만약 outer alias 가 inner self-reference 로 잘못 해석되면
        # NOT EXISTS 가 모든 row 에 대해 자기 자신과 비교 false 가 되어 A 가 active 로 잘못 잡힐
        # 수도, 빠질 수도 있다. 명시 alias 라야 의미가 안정).
        self._insert_claim_event(id='e1', claim_id='A', project_id='p1', technology='BT',
                                 condition_hash='h1', operator='op1', action='acquired',
                                 occurred_at='2026-05-25T10:00:00Z')
        self._insert_claim_event(id='e2', claim_id='A', project_id='p1', technology='BT',
                                 condition_hash='h1', operator='op1', action='released',
                                 occurred_at='2026-05-25T09:00:00Z')  # 이전 시각
        self.conn.commit()

        rows = list(self.conn.execute('SELECT claim_id FROM active_claims'))
        # 이전 release 는 acquire 를 무효화하지 않는다 — A 는 active.
        self.assertEqual([row['claim_id'] for row in rows], ['A'])

    def test_coverage_view_attempt_count_reflects_all_attempts(self):
        # 결함 2 회귀 가드: attempt_count 가 is_latest=true 안에서 1 이 아니라 그룹 전체 카운트.
        common = {'provider_id': 'pv', 'session_id': 's1', 'project_id': 'p1',
                  'test_name': 'OBW', 'technology': 'BT', 'condition_hash': 'hX', 'operator': 'op1'}
        self._insert_attempt(id='a1', attempt_number=1, is_latest=False, **common)
        self._insert_attempt(id='a2', attempt_number=2, is_latest=False, **common)
        self._insert_attempt(id='a3', attempt_number=3, is_latest=True, **common)
        self.conn.commit()

        rows = list(self.conn.execute(
            'SELECT latest_attempt_number, attempt_count FROM coverage_by_condition_hash WHERE condition_hash=?',
            ('hX',),
        ))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['latest_attempt_number'], 3)
        self.assertEqual(rows[0]['attempt_count'], 3)

    def test_coverage_view_yields_one_row_per_partition(self):
        # 두 개 project + 두 개 condition_hash + 각 그룹당 latest 1개 → 4 행.
        common = {'provider_id': 'pv', 'test_name': 'OBW', 'technology': 'BT', 'operator': 'op1'}
        idx = 0
        for project in ('p1', 'p2'):
            for cond in ('h1', 'h2'):
                idx += 1
                self._insert_attempt(id=f'a{idx}-old', session_id=f's{idx}',
                                     project_id=project, condition_hash=cond,
                                     attempt_number=1, is_latest=False, **common)
                idx += 1
                self._insert_attempt(id=f'a{idx}-latest', session_id=f's{idx}',
                                     project_id=project, condition_hash=cond,
                                     attempt_number=2, is_latest=True, **common)
        self.conn.commit()

        rows = list(self.conn.execute('SELECT project_id, condition_hash, attempt_count FROM coverage_by_condition_hash ORDER BY project_id, condition_hash'))
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertEqual(row['attempt_count'], 2)

    def test_active_claims_select_qualifies_self_reference_with_outer_alias(self):
        # AST-level guard: outer table must be aliased so the NOT EXISTS subquery can
        # disambiguate. Looks for the literal pattern "claim_events acquired" — the
        # canonical outer alias chosen by FE-P0a.
        select_sql = self.schema['views']['active_claims']['select']
        self.assertIn('claim_events acquired', select_sql)
        self.assertIn('later.claim_id = acquired.claim_id', select_sql)
        self.assertIn('later.occurred_at > acquired.occurred_at', select_sql)

    def test_coverage_view_select_counts_over_all_attempts_not_latest_projection(self):
        # Semantic guard (결함 2 회귀): attempt_count must count ALL attempts of the
        # partition, NOT the is_latest=true projection (which is always 1). The
        # implementation is a single GROUP BY derived table over the full
        # measurement_attempts (FE-P3 duplicate-quality single-pass refactor,
        # 2026-05-28) joined 1:1 to the is_latest=true row — replacing the prior
        # per-aggregate correlated subqueries. The guard checks the *intent* (a
        # COUNT over a measurement_attempts GROUP BY with no is_latest filter), not
        # a specific subquery literal.
        select_sql = self.schema['materialized_views']['coverage_by_condition_hash']['select']
        self.assertIn('attempt_count', select_sql)
        # Aggregate source: a GROUP BY over the partition key (not a windowed count
        # on the latest projection, which would always be 1).
        self.assertIn('COUNT(*) AS attempt_count', select_sql)
        self.assertIn('GROUP BY project_id, technology, condition_hash', select_sql)
        self.assertIn('IS NOT DISTINCT FROM', select_sql)  # null-safe project_id join
        self.assertNotIn('COUNT(*) OVER', select_sql)
        # The aggregate derived table must NOT be filtered to is_latest (that would
        # collapse every count to 1 — the original 결함 2).
        agg_clause = select_sql.split('JOIN (', 1)[1].split(') agg', 1)[0]
        self.assertNotIn('is_latest', agg_clause)


class TestFeP0aActionEnumSsot(unittest.TestCase):
    """FE-P0a (Commit 3) — claim_events.action allowed_values 가 schema SSOT.
    SQL view 본문의 'acquired'/'released'/'expired' 리터럴은 schema.allowed_values 의 부분집합이어야 한다.
    """

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        self.action_spec = self.schema['tables']['claim_events']['columns']['action']

    def test_action_allowed_values_are_declared(self):
        self.assertIn('allowed_values', self.action_spec)
        self.assertEqual(set(self.action_spec['allowed_values']), {'acquired', 'released', 'expired'})

    def test_active_claims_view_action_literals_are_subset_of_allowed_values(self):
        allowed = set(self.action_spec['allowed_values'])
        select_sql = self.schema['views']['active_claims']['select']

        for literal in ('acquired', 'released', 'expired'):
            self.assertIn(literal, allowed, f'{literal!r} used in view but not declared in schema allowed_values')
            self.assertIn(f"'{literal}'", select_sql)

    def test_check_constraint_renders_in_ddl(self):
        ddl_path = resolve_repo_artifact(__file__, 'docs/platform/migrations/001_initial_central_db.sql')
        ddl = ddl_path.read_text(encoding='utf-8')

        self.assertIn('CONSTRAINT "ck_claim_events_action"', ddl)
        self.assertIn("CHECK (\"action\" IN ('acquired', 'released', 'expired'))", ddl)

    def test_sqlite_rejects_unknown_action_value(self):
        # Live boundary check: SQLite enforces the CHECK as declared in DDL.
        conn = SqliteConnectionFactory(':memory:').create()
        try:
            conn.execute(
                """
                CREATE TABLE claim_events (
                    id TEXT PRIMARY KEY,
                    action TEXT NOT NULL CONSTRAINT ck_claim_events_action CHECK (action IN ('acquired', 'released', 'expired'))
                )
                """
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO claim_events(id, action) VALUES ('e1', 'whatever')")
            # Allowed value passes.
            conn.execute("INSERT INTO claim_events(id, action) VALUES ('e2', 'acquired')")
            conn.commit()
        finally:
            conn.close()


class TestFeP0aIdempotencyPartialIndex(unittest.TestCase):
    """FE-P0a (Commit 3) — idempotency_key UNIQUE index must be partial
    (WHERE idempotency_key IS NOT NULL) so multiple attempts can be inserted
    without an idempotency key without colliding on the unique constraint,
    while collisions among non-null keys are still rejected.
    """

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        self.indexes = self.schema['tables']['measurement_attempts']['indexes']

    def test_idempotency_index_is_partial(self):
        idx = next(i for i in self.indexes if i['name'] == 'ux_measurement_attempts_idempotency_key')

        self.assertTrue(idx.get('unique'))
        self.assertEqual(idx.get('where'), 'idempotency_key IS NOT NULL')

    def test_ddl_emits_partial_where_clause(self):
        ddl_path = resolve_repo_artifact(__file__, 'docs/platform/migrations/001_initial_central_db.sql')
        ddl = ddl_path.read_text(encoding='utf-8')

        self.assertIn(
            'CREATE UNIQUE INDEX IF NOT EXISTS "ux_measurement_attempts_idempotency_key"',
            ddl,
        )
        self.assertIn('WHERE idempotency_key IS NOT NULL', ddl)

    def test_sqlite_partial_index_allows_multiple_nulls_but_rejects_dup_nonnull(self):
        conn = SqliteConnectionFactory(':memory:').create()
        try:
            conn.execute(
                """
                CREATE TABLE measurement_attempts (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT
                )
                """
            )
            conn.execute(
                'CREATE UNIQUE INDEX ux_idemp ON measurement_attempts(idempotency_key) WHERE idempotency_key IS NOT NULL'
            )
            conn.execute("INSERT INTO measurement_attempts(id, idempotency_key) VALUES ('a1', NULL)")
            conn.execute("INSERT INTO measurement_attempts(id, idempotency_key) VALUES ('a2', NULL)")
            conn.execute("INSERT INTO measurement_attempts(id, idempotency_key) VALUES ('a3', 'key-1')")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO measurement_attempts(id, idempotency_key) VALUES ('a4', 'key-1')")
            conn.commit()
        finally:
            conn.close()


class TestFeP0aIngestionContract(unittest.TestCase):
    """FE-P0a (Commit 3) — ingestion_contract atomicity/ordering rules SSOT.
    The schema owns the rules; FE-P0c writer implements them. This contract
    test guards the rule set against accidental deletion or weakening.
    """

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        self.contract = self.schema['ingestion_contract']
        self.rule_ids = {rule['id'] for rule in self.contract['rules']}

    def test_contract_declares_required_rules(self):
        required = {
            'attempts_insert_and_is_latest_toggle_atomic',
            'measurement_results_is_projection_of_latest_attempt',
            'claim_events_append_only_and_acquire_release_pairing',
            'coverage_refresh_within_same_unit_of_work',
            'condition_hash_is_propagated_never_recomputed',
        }
        self.assertEqual(required, self.rule_ids)

    def test_atomic_toggle_rule_references_unique_index(self):
        rule = next(r for r in self.contract['rules'] if r['id'] == 'attempts_insert_and_is_latest_toggle_atomic')
        enforced_by = ' '.join(rule.get('enforced_by') or [])

        self.assertIn('ux_measurement_attempts_session_condition_attempt', enforced_by)
        self.assertIn('SERIALIZABLE', rule['description'])
        self.assertIn('FOR UPDATE', rule['description'])

    def test_refresh_rule_references_refresh_policy(self):
        rule = next(r for r in self.contract['rules'] if r['id'] == 'coverage_refresh_within_same_unit_of_work')
        enforced_by = ' '.join(rule.get('enforced_by') or [])

        self.assertIn('refresh_policies.coverage_by_condition_hash', enforced_by)
        self.assertIn('REFRESH MATERIALIZED VIEW CONCURRENTLY', rule['description'])

    def test_condition_hash_rule_forbids_central_recompute(self):
        rule = next(r for r in self.contract['rules'] if r['id'] == 'condition_hash_is_propagated_never_recomputed')

        self.assertIn('compute_condition_hash', rule['description'])
        self.assertIn('MUST NOT recompute', rule['description'])


class TestFeP0aAdrAlignmentDrift(unittest.TestCase):
    """FE-P0a — ADR-0005 ↔ contract drift 0 봉인. ADR 본문이 실제 contract 의 결정을 반영해야 한다."""

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        self.adr_text = ADR_PATH.read_text(encoding='utf-8')

    def test_schema_records_adr_alignment_metadata(self):
        alignment = self.schema['adr_alignment']

        self.assertEqual(alignment['adr_id'], '0005')
        self.assertIn('coverage_by_condition_hash', alignment['alignment'])
        self.assertIn('ADR-0005:158-162', alignment['alignment'])

    def test_adr_acknowledges_fe_p0a_extensions(self):
        for required_term in (
            'measurement_attempts',
            'claim_events',
            'coverage_by_condition_hash',
            'project_membership',
            'condition_hash',
            'refresh policy',
            'FE-P0a',
        ):
            self.assertIn(required_term, self.adr_text, required_term)

    def test_adr_resolves_open_question_about_view_refresh_policy(self):
        # ADR-0005:158-162 originally listed "materialized view refresh 정책 결정 필요" as an open issue.
        # After FE-P0a the ADR must state the decided policy (on_ingest + PT1H fallback).
        self.assertIn('on_ingest', self.adr_text)
        self.assertIn('PT1H', self.adr_text)

    def test_adr_documents_pinned_performance_budget(self):
        # The performance budget pinned in the schema (performance_budget field)
        # must also be acknowledged in the ADR so ADR↔contract drift stays 0.
        self.assertIn('performance_budget', self.adr_text)
        self.assertIn('background', self.adr_text)
        self.assertIn('deferred-to-deployed', self.adr_text)


class TestMigration008IdentityRollback(unittest.TestCase):
    """008 DOWN migration — forward apply then rollback, on a live SQLite fixture.

    The real `--rollback` statements are extracted from 008 and executed (with a
    documented SQLite dialect shim that drops the PostgreSQL-only ``CONCURRENTLY``
    keyword — SQLite has no concurrent index build, but the uniqueness SEMANTICS
    are identical). The forward transformation is hand-written in SQLite-compatible
    DDL (the real forward file uses PG-only ALTER COLUMN SET NOT NULL / btrim /
    CREATE OR REPLACE VIEW), mirroring the existing fixtures in this module
    (TestFeP8ProjectMemberPermissionsView, TestFeP0aActionEnumSsot).
    """

    LEGACY_ISSUER = 'urn:fcc:identity:legacy'

    def setUp(self):
        rollback = parse_rollback_statements(
            (resolve_repo_artifact(__file__, 'docs/platform/migrations/008_identity_issuer_subject_rbac.sql')).read_text(encoding='utf-8')
        )
        # SQLite dialect shim: no CONCURRENTLY (semantics-preserving).
        self.rollback_statements = [stmt.replace(' CONCURRENTLY', '') for stmt in rollback]
        self.assertTrue(self.rollback_statements, 'expected 008 to declare DOWN statements')

        self.conn = SqliteConnectionFactory(SQLITE_IN_MEMORY_DB).create()
        self.conn.row_factory = sqlite3.Row
        self._build_pre_008_users()

    def tearDown(self):
        self.conn.close()

    # -- fixture builders ---------------------------------------------------- #
    def _build_pre_008_users(self):
        """Pre-008 shape: subject-only UNIQUE, no issuer column."""
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE UNIQUE INDEX ux_users_subject ON users (subject);
            """
        )
        cur.execute("INSERT INTO users (id, subject) VALUES ('u1', 'alice@corp')")
        cur.execute("INSERT INTO users (id, subject) VALUES ('u2', 'bob@corp')")
        self.conn.commit()

    def _apply_forward(self):
        """Forward 008 (SQLite-compatible): add legacy-default issuer, switch the
        UNIQUE axis from (subject) to (issuer, subject)."""
        cur = self.conn.cursor()
        # ADD COLUMN ... DEFAULT backfills existing rows with the legacy issuer.
        cur.execute(
            f"ALTER TABLE users ADD COLUMN issuer TEXT NOT NULL DEFAULT '{self.LEGACY_ISSUER}'"
        )
        cur.execute('CREATE UNIQUE INDEX ux_users_issuer_subject ON users (issuer, subject)')
        cur.execute('DROP INDEX ux_users_subject')
        self.conn.commit()

    def _apply_rollback(self):
        cur = self.conn.cursor()
        for stmt in self.rollback_statements:
            cur.execute(stmt)
        self.conn.commit()

    def _index_names(self):
        return {
            row['name']
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }

    # -- forward direction sanity ------------------------------------------- #
    def test_forward_allows_cross_issuer_same_subject(self):
        self._apply_forward()
        # Same subject under a DIFFERENT issuer is legal once keyed by (issuer, subject).
        self.conn.execute(
            "INSERT INTO users (id, subject, issuer) VALUES ('u3', 'alice@corp', 'https://other.idp')"
        )
        # But the same (issuer, subject) pair is still rejected.
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                f"INSERT INTO users (id, subject, issuer) VALUES ('u4', 'alice@corp', '{self.LEGACY_ISSUER}')"
            )

    # -- rollback direction (the deliverable) -------------------------------- #
    def test_rollback_restores_subject_only_uniqueness(self):
        self._apply_forward()
        self._apply_rollback()
        # A duplicate subject (even under a different issuer) is now rejected again.
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO users (id, subject, issuer) VALUES ('u3', 'alice@corp', 'https://other.idp')"
            )

    def test_rollback_drops_issuer_subject_index_and_recreates_subject_index(self):
        self._apply_forward()
        self.assertIn('ux_users_issuer_subject', self._index_names())
        self._apply_rollback()
        names = self._index_names()
        self.assertNotIn('ux_users_issuer_subject', names)
        self.assertIn('ux_users_subject', names)

    def test_rollback_preserves_legacy_issuer_fallback_data(self):
        self._apply_forward()
        self._apply_rollback()
        # Lossless: the issuer column + the legacy classification survive rollback.
        rows = dict(self.conn.execute('SELECT id, issuer FROM users ORDER BY id'))
        self.assertEqual(rows, {'u1': self.LEGACY_ISSUER, 'u2': self.LEGACY_ISSUER})

    def test_rollback_fails_closed_on_cross_issuer_subject_collision(self):
        # Post-008 data MAY hold the same subject under two issuers; restoring the
        # subject-only UNIQUE must then fail LOUDLY (not silently drop a row).
        self._apply_forward()
        self.conn.execute(
            "INSERT INTO users (id, subject, issuer) VALUES ('u3', 'alice@corp', 'https://other.idp')"
        )
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self._apply_rollback()


if __name__ == '__main__':
    unittest.main()
