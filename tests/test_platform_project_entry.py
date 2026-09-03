"""Platform project entry contract (Phase 1, 2026-06-22).

Seals the ADR-0017 Self-Enforcing Guards for the "내 프로젝트" entry surface,
at the ``CentralProjectService`` boundary with in-memory fakes (no live
PostgreSQL — mirrors the other platform contract tests' fake-injection style):

- D1 — project = model 1:1 + same-model reuse is idempotent (no duplicate, no
  second auto-admin grant).
- D3 — the creator is auto-granted the schema-derived ``project_admin`` role.
- create→list→detail round-trips; unknown project → 404 (ProjectNotFoundError);
  empty model_name → 400 (ValueError).
- the read/write ports structurally satisfy their @runtime_checkable Protocols.

Owned by ``/verify-platform-project-entry``.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fcc_test_contracts.common.sqlite_connection_factory import SqliteConnectionFactory  # noqa: E402

from fcc_test_platform.application.central_project_service import (  # noqa: E402
    CentralProjectService,
    ProjectNotFoundError,
)
from fcc_test_platform.application.rbac_role_catalog import (  # noqa: E402
    PROJECT_ADMIN_ROLE_KEY,
    is_known_role,
)
from fcc_test_platform.domain.ports.output.central_project_port import (  # noqa: E402
    CentralProjectReadPort,
    CentralProjectWritePort,
)
from fcc_test_platform.application.central_user_write_adapter import UPSERT_USER_SQL  # noqa: E402


class _InMemoryCentral:
    """Shared in-memory projects/device_models/samples store backing both fakes."""

    def __init__(self) -> None:
        self.projects: dict[str, dict] = {}        # project_id -> detail dict
        self.code_index: dict[str, str] = {}       # project_code -> project_id
        self.memberships: list[dict] = []
        self.audit_events: list[dict] = []
        self.create_calls = 0


class _FakeProjectReadPort:
    def __init__(self, central: _InMemoryCentral) -> None:
        self._c = central

    def list_projects(self, *, status=None, q=None, limit=None, after=None):
        # status filtering is exercised at the adapter layer; the fake returns the
        # full store (the service just maps rows → envelopes).
        if q is not None or limit is not None or after is not None:
            # Loud, not a silent no-op (FakeDialog idiom): this fake models ONLY
            # the unbounded directory read. Quietly ignoring the search/keyset
            # arguments would let a broken pagination path pass here while the
            # real seals live in test_platform_project_directory_invariants.py.
            raise AssertionError(
                'unbounded-read fake received search/keyset arguments '
                f'(q={q!r}, limit={limit!r}, after={after!r})'
            )
        rows = []
        for detail in self._c.projects.values():
            rows.append({
                'project_id': detail['project_id'],
                'project_code': detail['project_code'],
                'model_name': detail['model_name'],
                'customer': detail.get('customer'),
                'manufacturer': detail.get('manufacturer'),
                'management_number': detail.get('management_number'),
                'status': detail.get('status'),
                'fcc_grantee_code': detail.get('fcc_grantee_code'),
                'applicant_name': detail.get('applicant_name'),
                'applicant_address': detail.get('applicant_address'),
                'eut_description': detail.get('eut_description'),
                'test_standard': detail.get('test_standard'),
                'sample_count': len(detail.get('samples') or []),
            })
        return rows

    def read_project_detail(self, project_id):
        detail = self._c.projects.get(project_id)
        return dict(detail) if detail is not None else None


class _FakeProjectWritePort:
    def __init__(self, central: _InMemoryCentral) -> None:
        self._c = central

    def find_project_by_code(self, project_code):
        pid = self._c.code_index.get(project_code)
        if pid is None:
            return None
        return {'project_id': pid, 'project_code': project_code}

    def create_project_with_model(self, project_record, device_model_record):
        self._c.create_calls += 1
        pid = project_record['id']
        self._c.projects[pid] = {
            'project_id': pid,
            'project_code': project_record['project_code'],
            'model_name': device_model_record['model_name'],
            'customer': project_record.get('customer'),
            'manufacturer': device_model_record.get('manufacturer'),
            'management_number': project_record.get('management_number'),
            'status': project_record.get('status'),
            'fcc_grantee_code': project_record.get('fcc_grantee_code'),
            'applicant_name': project_record.get('applicant_name'),
            'applicant_address': project_record.get('applicant_address'),
            'eut_description': project_record.get('eut_description'),
            'test_standard': project_record.get('test_standard'),
            'created_at': project_record.get('created_at'),
            'samples': [],
        }
        self._c.code_index[project_record['project_code']] = pid
        return {'project_id': pid, 'project_code': project_record['project_code']}

    def update_project_status(self, project_id, status, updated_at):
        detail = self._c.projects.get(project_id)
        if detail is None:
            return None
        detail['status'] = status
        return {'project_id': project_id, 'status': status}

    def update_project_metadata(self, project_id, updates, updated_at):
        # W3 백엔드 — 성적서 메타 부분 편집(보낸 키만 반영). 이 store 는 projects 와
        # device_models 를 한 detail dict 로 평탄화하므로 필드 소속 분리는 여기서
        # 검증하지 않는다 (그 봉인은 test_platform_project_directory_invariants.py).
        detail = self._c.projects.get(project_id)
        if detail is None:
            return None
        detail.update(updates)
        return {'project_id': project_id}

    def create_project_with_model_and_admin_grant(
        self,
        project_record,
        device_model_record,
        user_record,
        membership_record,
        audit_record,
    ):
        result = self.create_project_with_model(project_record, device_model_record)
        materialized = dict(membership_record)
        materialized['user_id'] = user_record['id']
        materialized['user_issuer'] = user_record['issuer']
        materialized['user_subject'] = user_record['subject']
        self._c.memberships.append(materialized)
        self._c.audit_events.append(dict(audit_record))
        return result


class _FakeMembershipService:
    """Records assign() calls (duck-typed for CentralProjectService)."""

    def __init__(self) -> None:
        self.assigns: list[dict] = []

    def assign(
        self,
        project_id,
        *,
        user_subject,
        role_key,
        actor_subject,
        user_issuer='',
        expires_at=None,
    ):
        self.assigns.append({
            'project_id': project_id,
            'user_issuer': user_issuer,
            'user_subject': user_subject,
            'role_key': role_key,
            'actor_subject': actor_subject,
        })
        return {
            'project_id': project_id, 'user_issuer': user_issuer, 'user_subject': user_subject,
            'role_key': role_key, 'assigned_at': 'now', 'expires_at': None,
        }


def _make_service(seq=None):
    central = _InMemoryCentral()
    membership = _FakeMembershipService()
    # deterministic id factory so created project ids are predictable uuids.
    ids = iter(seq or [
        '11111111-1111-4111-8111-111111111111',  # project 1
        '22222222-2222-4222-8222-222222222222',  # device_model 1
        '33333333-3333-4333-8333-333333333333',  # actor user 1
        '44444444-4444-4444-8444-444444444444',  # membership 1
        '55555555-5555-4555-8555-555555555555',  # audit 1
    ])
    service = CentralProjectService(
        _FakeProjectReadPort(central), _FakeProjectWritePort(central), membership,
        clock=lambda: '2026-06-22T00:00:00+00:00',
        id_factory=lambda: next(ids),
    )
    return service, central, membership


class TestProjectEntryProtocolConformance(unittest.TestCase):
    def test_fakes_satisfy_runtime_checkable_ports(self):
        central = _InMemoryCentral()
        self.assertIsInstance(_FakeProjectReadPort(central), CentralProjectReadPort)
        self.assertIsInstance(_FakeProjectWritePort(central), CentralProjectWritePort)


class TestAdminRoleKeyIsSchemaDerived(unittest.TestCase):
    def test_admin_role_key_is_a_known_role(self):
        # ADR-0017 D3 — the auto-admin role is derived from rbac_role_grants, not
        # a code literal, so it is always a valid project role.
        self.assertTrue(is_known_role(PROJECT_ADMIN_ROLE_KEY))


class TestCreateProject(unittest.TestCase):
    def test_create_returns_detail_with_model_and_empty_samples(self):
        service, _central, _m = _make_service()
        detail = service.create_project(actor_issuer='urn:fcc:identity:legacy', model_name='SM-S921U', actor_subject='alice')
        self.assertEqual(detail['model_name'], 'SM-S921U')
        # D1 — project_code == model name.
        self.assertEqual(detail['project_code'], 'SM-S921U')
        # D2 — samples are recorded at measurement time, so a new project is empty.
        self.assertEqual(detail['samples'], [])

    def test_create_grants_creator_project_admin(self):
        service, central, _membership = _make_service()
        service.create_project(actor_issuer='urn:fcc:identity:legacy', model_name='SM-S921U', actor_subject='alice')
        # D3 — exactly one auto-admin grant to the creator, with the schema role.
        self.assertEqual(len(central.memberships), 1)
        grant = central.memberships[0]
        self.assertEqual(grant['role_key'], PROJECT_ADMIN_ROLE_KEY)
        self.assertEqual(grant['user_subject'], 'alice')
        self.assertEqual(central.audit_events[0]['actor_subject'], 'alice')

    def test_create_defaults_status_active_and_optional_management_number(self):
        # Phase A — status defaults to the 'active' token on create; the
        # PM-assigned management_number is optional (None when not given).
        service, _central, _m = _make_service()
        detail = service.create_project(actor_issuer='urn:fcc:identity:legacy', model_name='SM-S921U', actor_subject='alice')
        self.assertEqual(detail['status'], 'active')
        self.assertIsNone(detail['management_number'])

    def test_create_persists_management_number_when_supplied(self):
        # The PM-assigned management number round-trips through create → detail
        # (UNIQUE/nullable; Report Number 생성근간 S-{management_number}-…).
        service, _central, _m = _make_service()
        detail = service.create_project(
            actor_issuer='urn:fcc:identity:legacy',
            model_name='SM-S921U', actor_subject='alice',
            management_number='4792232056',
        )
        self.assertEqual(detail['management_number'], '4792232056')
        # and it surfaces on the list envelope too.
        listed = service.list_projects()['items']
        self.assertEqual(listed[0]['management_number'], '4792232056')
        self.assertEqual(listed[0]['status'], 'active')

    def test_report_meta_round_trips_and_fcc_id_is_derived(self):
        # Phase B — the 5 성적서 표지 메타 fields round-trip create → detail/list,
        # and fcc_id is DERIVED (grantee + product_code(model_name)), not stored.
        service, _central, _m = _make_service()
        detail = service.create_project(
            actor_issuer='urn:fcc:identity:legacy',
            model_name='SM-X940', actor_subject='alice',
            fcc_grantee_code='A3L', applicant_name='Samsung',
            applicant_address='Suwon, KR', eut_description='Tablet',
            test_standard='FCC Part 15',
        )
        self.assertEqual(detail['fcc_grantee_code'], 'A3L')
        self.assertEqual(detail['applicant_name'], 'Samsung')
        self.assertEqual(detail['applicant_address'], 'Suwon, KR')
        self.assertEqual(detail['eut_description'], 'Tablet')
        self.assertEqual(detail['test_standard'], 'FCC Part 15')
        # Derived: 'A3L' + product_code('SM-X940')='SMX940'.
        self.assertEqual(detail['fcc_id'], 'A3LSMX940')
        listed = service.list_projects()['items']
        self.assertEqual(listed[0]['fcc_id'], 'A3LSMX940')
        self.assertEqual(listed[0]['test_standard'], 'FCC Part 15')

    def test_fcc_id_is_none_without_grantee_code(self):
        # No grantee code ⇒ FCC ID cannot be formed (None), even with a model.
        service, _central, _m = _make_service()
        detail = service.create_project(actor_issuer='urn:fcc:identity:legacy', model_name='SM-X940', actor_subject='alice')
        self.assertIsNone(detail['fcc_grantee_code'])
        self.assertIsNone(detail['fcc_id'])

    def test_empty_model_name_is_rejected(self):
        service, _central, _m = _make_service()
        with self.assertRaises(ValueError):
            service.create_project(actor_issuer='urn:fcc:identity:legacy', model_name='   ', actor_subject='alice')

    def test_missing_actor_is_rejected(self):
        service, _central, _m = _make_service()
        with self.assertRaises(ValueError):
            service.create_project(actor_issuer='urn:fcc:identity:legacy', model_name='SM-S921U', actor_subject='')

    def test_missing_actor_issuer_falls_back_to_legacy(self):
        # A blank actor_issuer (non-OIDC / trusted-header / claim-less principal)
        # canonicalizes to LEGACY_IDENTITY_ISSUER so the central user is still keyed
        # by (issuer, subject) — onboarding never requires the caller to know the
        # issuer URL. (OIDC principals carry their real validated issuer.)
        service, central, _m = _make_service()
        service.create_project(model_name='SM-S921U', actor_subject='alice')
        self.assertEqual(central.memberships[0]['user_issuer'], 'urn:fcc:identity:legacy')

    def test_user_upsert_preserves_disabled_actor_state(self):
        update_clause = (
            UPSERT_USER_SQL
            .split('DO UPDATE SET', 1)[1]
            .split('RETURNING', 1)[0]
        )
        self.assertNotIn('"enabled"', update_clause)


class TestSameModelReuseIdempotent(unittest.TestCase):
    def test_same_model_returns_same_project_no_duplicate(self):
        service, central, _membership = _make_service()
        first = service.create_project(actor_issuer='urn:fcc:identity:legacy', model_name='SM-S921U', actor_subject='alice')
        second = service.create_project(actor_issuer='urn:fcc:identity:legacy', model_name='SM-S921U', actor_subject='bob')
        # D1 — same model name reuses the existing project (idempotent).
        self.assertEqual(first['project_id'], second['project_id'])
        # exactly one underlying create + one auto-admin grant (no duplicate work).
        self.assertEqual(central.create_calls, 1)
        self.assertEqual(len(central.memberships), 1)


class TestListAndDetail(unittest.TestCase):
    def test_create_then_list_then_detail_roundtrip(self):
        service, _central, _m = _make_service()
        created = service.create_project(model_name='SM-S921U', actor_subject='alice')
        listed = service.list_projects()['items']
        self.assertEqual([p['project_id'] for p in listed], [created['project_id']])
        self.assertEqual(listed[0]['model_name'], 'SM-S921U')
        self.assertEqual(listed[0]['sample_count'], 0)
        detail = service.get_project(created['project_id'])
        self.assertEqual(detail['project_id'], created['project_id'])

    def test_unknown_project_raises_not_found(self):
        service, _central, _m = _make_service()
        with self.assertRaises(ProjectNotFoundError):
            service.get_project('99999999-9999-4999-8999-999999999999')

    def test_malformed_project_id_raises_value_error(self):
        service, _central, _m = _make_service()
        with self.assertRaises(ValueError):
            service.get_project('not-a-uuid')


class TestProjectStatusLifecycle(unittest.TestCase):
    """project-status-visibility — complete/reopen status transitions."""

    def test_complete_then_reopen_round_trips_status(self):
        service, _central, _m = _make_service()
        created = service.create_project(model_name='SM-S921U', actor_subject='alice')
        pid = created['project_id']
        self.assertEqual(created['status'], 'active')

        completed = service.complete_project(pid)
        self.assertEqual(completed['project_id'], pid)
        self.assertEqual(completed['status'], 'completed')
        # The transition is persisted (a fresh read reflects it).
        self.assertEqual(service.get_project(pid)['status'], 'completed')

        reopened = service.reopen_project(pid)
        self.assertEqual(reopened['status'], 'active')
        self.assertEqual(service.get_project(pid)['status'], 'active')

    def test_complete_unknown_project_raises_not_found(self):
        service, _central, _m = _make_service()
        with self.assertRaises(ProjectNotFoundError):
            service.complete_project('99999999-9999-4999-8999-999999999999')

    def test_reopen_malformed_id_raises_value_error(self):
        service, _central, _m = _make_service()
        with self.assertRaises(ValueError):
            service.reopen_project('not-a-uuid')

    def test_complete_is_idempotent(self):
        # Completing an already-completed project is a no-op success (the write
        # overwrites status unconditionally — no spurious 409).
        service, _central, _m = _make_service()
        pid = service.create_project(model_name='SM-S921U', actor_subject='alice')[
            'project_id'
        ]
        service.complete_project(pid)
        again = service.complete_project(pid)
        self.assertEqual(again['status'], 'completed')

    def test_invalid_status_filter_raises_value_error(self):
        # ?status outside {active, completed, all} is a loud 400 (ValueError), not
        # a silently empty list (matches the OpenAPI enum).
        service, _central, _m = _make_service()
        with self.assertRaises(ValueError):
            service.list_projects(status='archived')

    def test_status_all_is_accepted(self):
        service, _central, _m = _make_service()
        service.create_project(model_name='SM-S921U', actor_subject='alice')
        # 'all' is a valid sentinel (the read adapter returns every project).
        self.assertEqual(len(service.list_projects(status='all')['items']), 1)


class TestProjectStatusSqlAgainstDdl(unittest.TestCase):
    """project-status-visibility — the real status-filter SELECT + status UPDATE
    run against a DDL-shaped SQLite table (the fake read port ignores status, so
    this is the only place the actual SQL behavior is sealed)."""

    def _conn(self):
        import sqlite3

        conn = SqliteConnectionFactory(':memory:').create()
        conn.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY, project_code TEXT, name TEXT, customer TEXT,
                management_number TEXT, status TEXT, fcc_grantee_code TEXT,
                applicant_name TEXT, applicant_address TEXT, eut_description TEXT,
                test_standard TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE device_models (
                id TEXT PRIMARY KEY, project_id TEXT, model_name TEXT,
                manufacturer TEXT
            );
            CREATE TABLE samples (id TEXT PRIMARY KEY, project_id TEXT NOT NULL);
            INSERT INTO projects (id, project_code, status, created_at) VALUES
                ('p-a','A','active','2026-06-01T00:00:00Z'),
                ('p-b','B','active','2026-06-02T00:00:00Z'),
                ('p-c','C','completed','2026-06-03T00:00:00Z');
            """
        )
        return conn

    def test_status_filter_select_returns_only_matching_status(self):
        from fcc_test_platform.application.central_project_read_adapter import (
            PROJECT_LIST_COLUMNS,
            PROJECT_LIST_SQL_BY_STATUS,
        )

        conn = self._conn()
        try:
            sql = PROJECT_LIST_SQL_BY_STATUS.replace('%s', '?')
            active = [
                dict(zip(PROJECT_LIST_COLUMNS, r)) for r in conn.execute(sql, ('active',))
            ]
            self.assertEqual({r['project_id'] for r in active}, {'p-a', 'p-b'})
            completed = [
                dict(zip(PROJECT_LIST_COLUMNS, r))
                for r in conn.execute(sql, ('completed',))
            ]
            self.assertEqual({r['project_id'] for r in completed}, {'p-c'})
        finally:
            conn.close()

    def test_update_status_returning_distinguishes_updated_from_unknown(self):
        from fcc_test_platform.application.central_project_write_adapter import (
            UPDATE_PROJECT_STATUS_SQL,
        )

        conn = self._conn()
        try:
            sql = UPDATE_PROJECT_STATUS_SQL.replace('%s', '?')
            updated = conn.execute(sql, ('completed', 'now', 'p-a')).fetchall()
            self.assertEqual(len(updated), 1)  # one row → existed
            self.assertEqual(
                conn.execute('SELECT status FROM projects WHERE id=?', ('p-a',)).fetchone()[0],
                'completed',
            )
            unknown = conn.execute(sql, ('completed', 'now', 'p-zzz')).fetchall()
            self.assertEqual(unknown, [])  # no row → unknown → service 404
        finally:
            conn.close()


class TestSampleInventoryFields(unittest.TestCase):
    """Phase C — PM 칸 인벤토리 메타가 sample envelope 로 노출된다."""

    def test_sample_pm_fields_surface_in_detail_envelope(self):
        service, central, _m = _make_service()
        created = service.create_project(actor_issuer='urn:fcc:identity:legacy', model_name='SM-S921U', actor_subject='alice')
        # Seed a sample carrying the new PM inventory columns directly into the
        # store (registration write lands in Phase E; C exposes read only).
        central.projects[created['project_id']]['samples'] = [{
            'sample_id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            'sample_code': 'SM-S921U-1',
            'serial_number': 'SN-001',
            'model_id': 'mmmmmmmm-mmmm-4mmm-8mmm-mmmmmmmmmmmm',
            'sample_number': '#2',
            'test_category': 'Main Conduction',
            'label_number': 'L-42',
            'smsn': 'SMSN-7',
            'intake_cert': 'CERT-9',
            'assigned_team': 'RF',
            'sender': 'PM Kim',
            'receiver': 'Tester Lee',
            'received_date': '2026-06-20',
            'released_date': '2026-06-25',
        }]
        detail = service.get_project(created['project_id'])
        sample = detail['samples'][0]
        self.assertEqual(sample['sample_number'], '#2')
        self.assertEqual(sample['test_category'], 'Main Conduction')
        self.assertEqual(sample['label_number'], 'L-42')
        self.assertEqual(sample['smsn'], 'SMSN-7')
        self.assertEqual(sample['intake_cert'], 'CERT-9')
        self.assertEqual(sample['assigned_team'], 'RF')
        self.assertEqual(sample['sender'], 'PM Kim')
        self.assertEqual(sample['receiver'], 'Tester Lee')
        self.assertEqual(sample['received_date'], '2026-06-20')
        self.assertEqual(sample['released_date'], '2026-06-25')

    def test_missing_pm_fields_are_none(self):
        service, central, _m = _make_service()
        created = service.create_project(actor_issuer='urn:fcc:identity:legacy', model_name='SM-S921U', actor_subject='alice')
        central.projects[created['project_id']]['samples'] = [{
            'sample_id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            'sample_code': 'SM-S921U-1',
        }]
        sample = service.get_project(created['project_id'])['samples'][0]
        for key in (
            'sample_number', 'test_category', 'label_number', 'smsn',
            'intake_cert', 'assigned_team', 'sender', 'receiver',
            'received_date', 'released_date',
        ):
            self.assertIsNone(sample[key], key)


class TestCompactIntakeReadBack(unittest.TestCase):
    """Phase F follow-up (2026-06-23) — the project-detail sample payload ships a
    compact ``latest_intake`` + ``intake_count`` and NOT the full append-only
    ``intakes`` history. Seals against a regression that restores the unbounded
    full-history payload as the required UI path."""

    def _seed_sample(self, central, project_id, sample):
        central.projects[project_id]['samples'] = [sample]

    def test_latest_intake_and_count_surface_compactly(self):
        service, central, _m = _make_service()
        created = service.create_project(actor_issuer='urn:fcc:identity:legacy', model_name='SM-S921U', actor_subject='alice')
        # Adapter output shape: a sample carries a single pre-selected latest
        # intake + the total history count (NOT an intakes array).
        self._seed_sample(central, created['project_id'], {
            'sample_id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            'sample_code': 'SM-S921U-1',
            'latest_intake': {
                'sample_intake_id': 'i-new', 'intake_date': '2025-11-19',
                'bl': 'BL_NEW', 'ap': 'AP_NEW', 'cp': 'CP_NEW', 'csc': 'CSC_NEW',
                'rf_cal': '2025.09.25', 'hw_rev': 'REV2', 'note': 'SRS',
            },
            'intake_count': 3,
        })
        sample = service.get_project(created['project_id'])['samples'][0]
        # Compact fields present, full-history array gone.
        self.assertNotIn('intakes', sample)
        self.assertEqual(sample['intake_count'], 3)
        self.assertIsNotNone(sample['latest_intake'])
        self.assertEqual(sample['latest_intake']['sample_intake_id'], 'i-new')
        self.assertEqual(sample['latest_intake']['cp'], 'CP_NEW')
        self.assertEqual(sample['latest_intake']['hw_rev'], 'REV2')

    def test_no_intake_yields_null_latest_and_zero_count(self):
        service, central, _m = _make_service()
        created = service.create_project(actor_issuer='urn:fcc:identity:legacy', model_name='SM-S921U', actor_subject='alice')
        self._seed_sample(central, created['project_id'], {
            'sample_id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            'sample_code': 'SM-S921U-1',
        })
        sample = service.get_project(created['project_id'])['samples'][0]
        self.assertNotIn('intakes', sample)
        self.assertIsNone(sample['latest_intake'])
        self.assertEqual(sample['intake_count'], 0)

    def test_wire_schema_has_compact_fields_not_full_history(self):
        # The OpenAPI SampleEnvelope is the wire contract SSOT — it must carry the
        # compact latest_intake + intake_count and NOT the full-history array.
        from fcc_test_kernel.application.central_contract.api_contracts import PLATFORM_API_SCHEMAS

        props = PLATFORM_API_SCHEMAS['SampleEnvelope']['properties']
        self.assertNotIn('intakes', props)
        self.assertIn('latest_intake', props)
        self.assertIn('intake_count', props)
        self.assertEqual(props['intake_count']['type'], 'integer')
        # latest_intake is a required object-or-null union, not nullable+allOf.
        self.assertNotIn('nullable', props['latest_intake'])
        self.assertNotIn('allOf', props['latest_intake'])
        self.assertEqual(
            props['latest_intake']['anyOf'][0]['$ref'],
            '#/schemas/SampleIntakeEnvelope',
        )
        self.assertEqual(props['latest_intake']['anyOf'][1], {'type': 'null'})
        self.assertIn('latest_intake', PLATFORM_API_SCHEMAS['SampleEnvelope']['required'])
        self.assertIn('intake_count', PLATFORM_API_SCHEMAS['SampleEnvelope']['required'])


class TestSamplesSqlMatchesDdl(unittest.TestCase):
    """Phase C — PROJECT_SAMPLES_SQL SELECT alias 순서 ↔ PROJECT_SAMPLES_COLUMNS
    tuple 정합을, 실제 DDL 형상의 SQLite shim 에서 end-to-end 로 봉인.

    The read adapter's ``dict(zip(columns, row))`` mapping is only correct if the
    SELECT alias order matches the tuple order; running the real SQL against a
    table shaped like the exported DDL catches any drift loudly.
    """

    def test_real_sql_against_ddl_shaped_table_maps_pm_fields(self):
        import sqlite3

        from fcc_test_platform.application.central_project_read_adapter import (
            PROJECT_SAMPLES_COLUMNS,
            PROJECT_SAMPLES_SQL,
        )

        conn = SqliteConnectionFactory(':memory:').create()
        try:
            conn.execute(
                """
                CREATE TABLE samples (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    model_id TEXT,
                    sample_code TEXT NOT NULL,
                    serial_number TEXT,
                    sample_number TEXT,
                    test_category TEXT,
                    label_number TEXT,
                    smsn TEXT,
                    intake_cert TEXT,
                    assigned_team TEXT,
                    sender TEXT,
                    receiver TEXT,
                    received_date TEXT,
                    released_date TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO samples (id, project_id, model_id, sample_code, "
                "serial_number, sample_number, test_category, label_number, smsn, "
                "intake_cert, assigned_team, sender, receiver, received_date, "
                "released_date, metadata_json, created_at, updated_at) VALUES "
                "('s1','p1','m1','SC-1','SN-1','#2','Radiation','L-1','SMSN-1',"
                "'CERT-1','SAR','PM','Tester','2026-06-20','2026-06-25',NULL,"
                "'2026-06-20T00:00:00Z','2026-06-20T00:00:00Z')"
            )
            # psycopg uses %s; SQLite uses ?. Swap the single placeholder.
            sql = PROJECT_SAMPLES_SQL.replace('%s', '?')
            cur = conn.execute(sql, ('p1',))
            row = cur.fetchone()
            mapped = dict(zip(PROJECT_SAMPLES_COLUMNS, row))
            self.assertEqual(mapped['sample_id'], 's1')
            self.assertEqual(mapped['sample_code'], 'SC-1')
            self.assertEqual(mapped['serial_number'], 'SN-1')
            self.assertEqual(mapped['model_id'], 'm1')
            self.assertEqual(mapped['sample_number'], '#2')
            self.assertEqual(mapped['test_category'], 'Radiation')
            self.assertEqual(mapped['label_number'], 'L-1')
            self.assertEqual(mapped['smsn'], 'SMSN-1')
            self.assertEqual(mapped['intake_cert'], 'CERT-1')
            self.assertEqual(mapped['assigned_team'], 'SAR')
            self.assertEqual(mapped['sender'], 'PM')
            self.assertEqual(mapped['receiver'], 'Tester')
            self.assertEqual(mapped['received_date'], '2026-06-20')
            self.assertEqual(mapped['released_date'], '2026-06-25')
        finally:
            conn.close()

    def test_intakes_sql_returns_latest_only_with_count(self):
        # Phase F follow-up (compact read-back) — PROJECT_INTAKES_SQL returns at
        # most ONE row per sample (the latest by created_at DESC) carrying the
        # total history count via COUNT(*) OVER. The full history is never
        # materialized across the DB→adapter boundary (payload reduction).
        import sqlite3

        from fcc_test_platform.application.central_project_read_adapter import (
            PROJECT_INTAKES_COLUMNS,
            PROJECT_INTAKES_SQL,
        )

        conn = SqliteConnectionFactory(':memory:').create()
        try:
            conn.executescript(
                """
                CREATE TABLE samples (id TEXT PRIMARY KEY, project_id TEXT NOT NULL);
                CREATE TABLE sample_intakes (
                    id TEXT PRIMARY KEY, sample_id TEXT NOT NULL, intake_date TEXT,
                    bl TEXT, ap TEXT, cp TEXT, csc TEXT, rf_cal TEXT, hw_rev TEXT,
                    note TEXT, created_at TEXT NOT NULL
                );
                INSERT INTO samples (id, project_id) VALUES ('s1','p1'),('s2','p1');
                INSERT INTO sample_intakes
                    (id, sample_id, intake_date, cp, created_at) VALUES
                    ('i-old','s1','2025-10-01','CP_A','2025-10-01T00:00:00Z'),
                    ('i-mid','s1','2025-11-01','CP_M','2025-11-01T00:00:00Z'),
                    ('i-new','s1','2025-11-19','CP_B','2025-11-19T00:00:00Z'),
                    ('i-one','s2','2025-09-01','CP_Z','2025-09-01T00:00:00Z');
                """
            )
            sql = PROJECT_INTAKES_SQL.replace('%s', '?')
            rows = [
                dict(zip(PROJECT_INTAKES_COLUMNS, r))
                for r in conn.execute(sql, ('p1',)).fetchall()
            ]
            # Exactly one row per sample (latest only), ordered by sample_id.
            self.assertEqual([r['sample_id'] for r in rows], ['s1', 's2'])
            self.assertEqual([r['sample_intake_id'] for r in rows], ['i-new', 'i-one'])
            self.assertEqual(rows[0]['cp'], 'CP_B')  # latest of s1's 3 intakes
            self.assertEqual(rows[0]['intake_count'], 3)  # full history size
            self.assertEqual(rows[1]['intake_count'], 1)
        finally:
            conn.close()

    def test_intake_count_zero_for_sample_without_intakes(self):
        # A sample with no intake rows is simply absent from the window query
        # result; the adapter defaults it to latest_intake=None / intake_count=0.
        import sqlite3

        from fcc_test_platform.application.central_project_read_adapter import (
            PROJECT_INTAKES_COLUMNS,
            PROJECT_INTAKES_SQL,
        )

        conn = SqliteConnectionFactory(':memory:').create()
        try:
            conn.executescript(
                """
                CREATE TABLE samples (id TEXT PRIMARY KEY, project_id TEXT NOT NULL);
                CREATE TABLE sample_intakes (
                    id TEXT PRIMARY KEY, sample_id TEXT NOT NULL, intake_date TEXT,
                    bl TEXT, ap TEXT, cp TEXT, csc TEXT, rf_cal TEXT, hw_rev TEXT,
                    note TEXT, created_at TEXT NOT NULL
                );
                INSERT INTO samples (id, project_id) VALUES ('s1','p1');
                """
            )
            sql = PROJECT_INTAKES_SQL.replace('%s', '?')
            rows = list(conn.execute(sql, ('p1',)).fetchall())
            self.assertEqual(rows, [])
            self.assertEqual(len(PROJECT_INTAKES_COLUMNS), 11)
        finally:
            conn.close()

    def test_intake_envelope_maps_firmware_fields(self):
        from fcc_test_platform.application.central_project_service import _intake_envelope

        env = _intake_envelope({
            'sample_intake_id': 'i1', 'intake_date': '2025-11-19',
            'bl': 'B', 'ap': 'A', 'cp': 'C', 'csc': 'S',
            'rf_cal': '2025.09.25', 'hw_rev': 'REV1.0', 'note': 'SRS',
        })
        self.assertEqual(env['sample_intake_id'], 'i1')
        self.assertEqual(env['cp'], 'C')
        self.assertEqual(env['rf_cal'], '2025.09.25')
        self.assertIsNone(_intake_envelope({'sample_intake_id': 'i2'})['cp'])

    def test_detail_sample_carries_compact_latest_intake_not_full_history(self):
        # Payload-reduction regression guard — the project-detail sample envelope
        # must ship only the latest intake + a count, NEVER the full append-only
        # history array. A future change that restores ``intakes`` as the required
        # UI path fails here.
        service, central, _m = _make_service()
        created = service.create_project(actor_issuer='urn:fcc:identity:legacy', model_name='SM-S921U', actor_subject='alice')
        central.projects[created['project_id']]['samples'] = [{
            'sample_id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            'sample_code': 'SM-S921U-1',
            'latest_intake': {
                'sample_intake_id': 'i-new', 'intake_date': '2025-11-19',
                'cp': 'CP_B', 'hw_rev': 'REV2',
            },
            'intake_count': 3,
        }]
        sample = service.get_project(created['project_id'])['samples'][0]
        self.assertNotIn('intakes', sample)  # full history is never on the wire
        self.assertEqual(sample['intake_count'], 3)
        self.assertIsNotNone(sample['latest_intake'])
        self.assertEqual(sample['latest_intake']['cp'], 'CP_B')
        self.assertEqual(sample['latest_intake']['hw_rev'], 'REV2')

    def test_detail_sample_with_no_history_has_null_latest_and_zero_count(self):
        service, central, _m = _make_service()
        created = service.create_project(actor_issuer='urn:fcc:identity:legacy', model_name='SM-S921U', actor_subject='alice')
        central.projects[created['project_id']]['samples'] = [{
            'sample_id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            'sample_code': 'SM-S921U-1',
            'latest_intake': None,
            'intake_count': 0,
        }]
        sample = service.get_project(created['project_id'])['samples'][0]
        self.assertIsNone(sample['latest_intake'])
        self.assertEqual(sample['intake_count'], 0)
        self.assertNotIn('intakes', sample)

    def test_adapter_projects_latest_intake_and_count_from_one_ordered_read(self):
        # The read adapter is the single owner of the latest-first rule: it reads
        # the intake history once (created_at DESC) and projects only the head +
        # a count per sample. Driven by a fake connection so the grouping logic is
        # sealed without a live PostgreSQL.
        from fcc_test_platform.application.central_project_read_adapter import (
            PostgresCentralProjectReadAdapter,
            PROJECT_DETAIL_SQL,
            PROJECT_INTAKES_SQL,
            PROJECT_SAMPLES_SQL,
        )

        detail_row = (
            'p1', 'SM-S921U', 'SM-S921U', None, None, None, 'active',
            None, None, None, None, None, '2026-06-23T00:00:00Z',
        )
        # sample_id, sample_code, serial_number, model_id, sample_number, ...
        sample_rows = [
            ('s1', 'SC-1', None, None, '#1', None, None, None, None, None,
             None, None, None, None),
            ('s2', 'SC-2', None, None, '#2', None, None, None, None, None,
             None, None, None, None),
        ]
        # Window-query output: one latest row for s1 carrying total count, s2 absent.
        intake_rows = [
            ('s1', 'i-new', '2025-11-19', None, None, 'CP_B', None, None, 'REV2', None, 2),
        ]

        class _FakeCursor:
            def __init__(self) -> None:
                self._rows: list = []

            def execute(self, statement, params):
                if statement == PROJECT_DETAIL_SQL:
                    self._rows = [detail_row]
                elif statement == PROJECT_SAMPLES_SQL:
                    self._rows = list(sample_rows)
                elif statement == PROJECT_INTAKES_SQL:
                    self._rows = list(intake_rows)
                else:  # pragma: no cover — defensive
                    raise AssertionError(f'unexpected SQL: {statement!r}')

            def fetchall(self):
                return self._rows

            def close(self):
                pass

        class _FakeConnection:
            def cursor(self):
                return _FakeCursor()

            def close(self):
                pass

        adapter = PostgresCentralProjectReadAdapter(lambda: _FakeConnection())
        detail = adapter.read_project_detail('p1')
        samples = {s['sample_id']: s for s in detail['samples']}
        # No full history attached on any sample.
        for s in detail['samples']:
            self.assertNotIn('intakes', s)
        # s1 — head of the descending group is the latest; count is the total.
        self.assertEqual(samples['s1']['latest_intake']['sample_intake_id'], 'i-new')
        self.assertEqual(samples['s1']['latest_intake']['cp'], 'CP_B')
        self.assertEqual(samples['s1']['intake_count'], 2)
        # s2 — no history.
        self.assertIsNone(samples['s2']['latest_intake'])
        self.assertEqual(samples['s2']['intake_count'], 0)

    def test_sample_envelope_schema_is_compact_not_full_history(self):
        # Contract guard — the wire schema exposes latest_intake + intake_count and
        # NOT a full intakes array, so the OpenAPI/codegen surface cannot drift back.
        from fcc_test_kernel.application.central_contract.api_contracts import PLATFORM_API_SCHEMAS

        envelope = PLATFORM_API_SCHEMAS['SampleEnvelope']
        props = envelope['properties']
        self.assertIn('latest_intake', props)
        self.assertIn('intake_count', props)
        self.assertNotIn('intakes', props)
        self.assertEqual(props['intake_count']['type'], 'integer')
        # latest_intake is a required, object-or-null field expressed with the
        # OpenAPI 3.1 ``anyOf: [$ref, {type: null}]`` UNION idiom. The old
        # ``nullable + allOf`` idiom is forbidden because openapi-typescript
        # materializes it as the unusable ``null & SampleIntakeEnvelope``
        # INTERSECTION instead of the intended ``SampleIntakeEnvelope | null``.
        self.assertNotIn('allOf', props['latest_intake'])
        self.assertNotIn('nullable', props['latest_intake'])
        any_of = props['latest_intake']['anyOf']
        self.assertEqual(
            any_of[0]['$ref'],
            '#/schemas/SampleIntakeEnvelope',
        )
        self.assertEqual(any_of[1], {'type': 'null'})
        # latest_intake is the wire core field — it MUST be required so clients
        # always receive an explicit object-or-null value.
        self.assertIn('latest_intake', envelope['required'])
        self.assertIn('intake_count', envelope['required'])

    def test_sample_envelope_latest_intake_materializes_as_nullable_union(self):
        # OpenAPI 3.1 / codegen materialization guard — building the components
        # schema must yield an object-or-null UNION (anyOf with a {"type":"null"}
        # member), never the ``allOf + type:["null"]`` shape that openapi-typescript
        # renders as ``null & SampleIntakeEnvelope``.
        from fcc_test_contracts.common.openapi_schema_builder import build_components_schemas
        from fcc_test_kernel.application.central_contract.api_contracts import PLATFORM_API_SCHEMAS

        components = build_components_schemas(PLATFORM_API_SCHEMAS)
        latest = components['SampleEnvelope']['properties']['latest_intake']
        self.assertNotIn('allOf', latest)
        self.assertEqual(latest['anyOf'][0]['$ref'], '#/components/schemas/SampleIntakeEnvelope')
        null_members = [m for m in latest['anyOf'] if m == {'type': 'null'}]
        self.assertEqual(len(null_members), 1)
        # type:["null"] alongside a $ref allOf is the failing intersection shape.
        self.assertNotEqual(latest.get('type'), ['null'])

    def test_sample_intakes_table_is_declared_in_schema(self):
        import json
        from pathlib import Path

        schema_path = (
            Path(__file__).parent.parent
            / 'docs' / 'platform' / 'central_db_schema.v1.json'
        )
        schema = json.loads(schema_path.read_text(encoding='utf-8'))
        intakes = schema['tables']['sample_intakes']
        columns = intakes['columns']
        for name in (
            'id', 'sample_id', 'intake_date', 'bl', 'ap', 'cp', 'csc',
            'rf_cal', 'hw_rev', 'note', 'created_at', 'updated_at',
        ):
            self.assertIn(name, columns, name)
        self.assertEqual(columns['sample_id']['references'], 'samples.id')
        names = {index['name'] for index in intakes.get('indexes', [])}
        self.assertIn('idx_sample_intakes_sample', names)


if __name__ == '__main__':
    unittest.main()
