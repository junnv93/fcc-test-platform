"""Unit seal for JIT user provisioning on project create (결함 B, 2026-06-26).

Fake-port coverage (no PostgreSQL): the create_project JIT wiring, the additive
ApiPrincipal profile fields, and the users-upsert SQL shape (enabled preserved,
empty claims coalesced). The live PostgreSQL behaviour (brand-new subject →
onboard + project_admin, idempotent upsert, enabled-not-re-enabled) is exercised
by the smoke in docs/development/central-db-migrations.md / the evaluation; local
pytest SIGABRTs so these run under `python -m unittest` too.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(_ROOT / 'src'))

from fcc_test_contracts.common.access_policy import ApiPrincipal  # noqa: E402
from fcc_test_platform.application.central_project_service import CentralProjectService  # noqa: E402
from fcc_test_platform.application.central_user_write_adapter import UPSERT_USER_SQL  # noqa: E402
from fcc_test_kernel.domain.services.project_metadata_edit import (  # noqa: E402
    CREATE_PROJECT_REQUIRED_FIELDS,
)


def _create_body(model_name: str, **overrides) -> dict:
    """생성 요청 본문 — 필수 칸이 채워진 최소 형태(필수 집합은 도메인 SSOT)."""
    body = {
        'model_name': model_name,
        'management_number': f'MGMT-{model_name}',
        'applicant_name': 'ACME Corp',
    }
    body.update(overrides)
    return body


assert set(CREATE_PROJECT_REQUIRED_FIELDS) <= set(_create_body('X'))

_FIXED_UUID = '11111111-1111-1111-1111-111111111111'


class _FakeUserWrite:
    def __init__(self):
        self.records = []

    def ensure_user(self, record):
        # Mirror the real adapter RETURNING projection: a successful upsert reports
        # the persisted ``enabled`` state (the service fails closed on disabled).
        self.records.append(dict(record))
        return {
            'id': 'user-id',
            'issuer': record.get('issuer'),
            'subject': record['subject'],
            'enabled': True,
        }


class _FakeMembership:
    def __init__(self):
        self.assigns = []

    def assign(self, project_id, *, user_subject, role_key, actor_subject):
        self.assigns.append((project_id, user_subject, role_key, actor_subject))
        return {}


class _FakeProjectWrite:
    def __init__(self, existing=None):
        self.existing = existing
        self.created = []
        self.atomic_calls = []  # (project, model, user, membership, audit) records

    def find_project_by_code(self, project_code):
        return self.existing

    def create_project_with_model(self, project_record, device_model_record):
        self.created.append((dict(project_record), dict(device_model_record)))
        return {'project_id': project_record['id']}

    def create_project_with_model_and_admin_grant(
        self, project_record, device_model_record, user_record,
        membership_record, audit_record,
    ):
        # The new-project path commits project + creator user + admin membership +
        # audit in ONE transaction (issuer+subject identity), so the user upsert
        # happens HERE, not via the injected user_write_port.
        self.created.append((dict(project_record), dict(device_model_record)))
        self.atomic_calls.append((
            dict(project_record), dict(device_model_record),
            dict(user_record), dict(membership_record), dict(audit_record),
        ))
        return {'project_id': project_record['id']}


class _FakeRead:
    def read_project_detail(self, project_id):
        return {'project_id': project_id, 'project_code': 'SM-X', 'model_name': 'SM-X'}

    def list_projects(self, *, status=None, q=None, limit=None, after=None):
        # CentralProjectReadPort signature (W3 백엔드). This suite never reads the
        # directory — it exists so the fake stays structurally conformant.
        return []

    def list_applicant_suggestions(self, *, q=None, limit):
        # Same reason as above — the suite never reads the applicant directory.
        return []


def _make(user_write=None, existing=None):
    membership = _FakeMembership()
    write = _FakeProjectWrite(existing)
    svc = CentralProjectService(
        _FakeRead(), write, membership,
        user_write_port=user_write,
        id_factory=lambda: _FixedId(),
        clock=lambda: '2026-01-01T00:00:00Z',
    )
    return svc, membership, write


class _FixedId:
    # id_factory returns a fresh valid uuid string each call (project_id +
    # user_record id are distinct in real life; a valid uuid satisfies require_uuid).
    _seq = 0

    def __new__(cls):
        cls._seq += 1
        return f'{cls._seq:08d}-1111-1111-1111-111111111111'


class TestApiPrincipalProfile(unittest.TestCase):
    def test_carries_email_and_display_name(self):
        p = ApiPrincipal.from_permissions('s', ['platform:read'], email='a@x.com', display_name='Al')
        self.assertEqual((p.email, p.display_name), ('a@x.com', 'Al'))

    def test_defaults_are_empty_backward_compatible(self):
        p = ApiPrincipal.from_permissions('s', [])
        self.assertEqual((p.email, p.display_name), ('', ''))


class TestCreateProjectJit(unittest.TestCase):
    def test_new_project_provisions_creator_atomically(self):
        svc, membership, write = _make(user_write=_FakeUserWrite(), existing=None)
        svc.create_project(_create_body('SM-X'), actor_subject='sub-new', actor_issuer='https://idp.example/tenant', actor_email='new@x.com', actor_display_name='New User')
        # New path commits project + creator user + admin membership + audit in ONE
        # transaction (issuer+subject identity) — no separate ensure/grant hops.
        self.assertEqual(len(write.atomic_calls), 1)
        _proj, _model, user_rec, membership_rec, audit_rec = write.atomic_calls[0]
        self.assertEqual(user_rec['subject'], 'sub-new')
        self.assertEqual(user_rec['issuer'], 'https://idp.example/tenant')
        self.assertEqual(user_rec['email'], 'new@x.com')
        self.assertEqual(user_rec['display_name'], 'New User')
        self.assertIs(user_rec['enabled'], True)
        self.assertEqual(membership_rec['role_key'], 'project_admin')
        self.assertEqual(audit_rec['event_type'], 'membership.assigned')
        # New path does NOT take the separate ensure/grant round-trips.
        self.assertEqual(len(membership.assigns), 0)

    def test_blank_issuer_falls_back_to_legacy(self):
        svc, _membership, write = _make(user_write=_FakeUserWrite(), existing=None)
        svc.create_project(_create_body('SM-X'), actor_subject='sub-new')
        user_rec = write.atomic_calls[0][2]
        self.assertEqual(user_rec['issuer'], 'urn:fcc:identity:legacy')

    def test_reuse_path_self_heals_grant(self):
        users = _FakeUserWrite()
        svc, membership, write = _make(user_write=users, existing={'project_id': _FIXED_UUID})
        svc.create_project(_create_body('SM-X'), actor_subject='sub-new')
        # Reuse must NOT create a second project (and never the atomic create)...
        self.assertEqual(write.created, [])
        self.assertEqual(write.atomic_calls, [])
        # ...but MUST re-ensure the user + re-assert the admin grant (idempotent
        # self-heal of a possibly orphaned project).
        self.assertEqual(len(users.records), 1)
        self.assertEqual(users.records[0]['issuer'], 'urn:fcc:identity:legacy')
        self.assertEqual(len(membership.assigns), 1)
        self.assertEqual(membership.assigns[0][2], 'project_admin')

    def test_new_path_provisions_even_without_user_write_port(self):
        # The new-project path provisions the creator inside the atomic write, so
        # it does not depend on the injected user_write_port (that port backs the
        # reuse self-heal path only).
        svc, _membership, write = _make(user_write=None, existing=None)
        svc.create_project(_create_body('SM-X'), actor_subject='sub-new')
        self.assertEqual(len(write.atomic_calls), 1)
        self.assertEqual(write.atomic_calls[0][2]['subject'], 'sub-new')


class TestUpsertSqlShape(unittest.TestCase):
    def test_conflict_on_issuer_subject(self):
        self.assertIn('ON CONFLICT ("issuer", "subject") DO UPDATE', UPSERT_USER_SQL)

    def test_enabled_preserved_not_in_update_set(self):
        # Only the SET clause (between DO UPDATE SET and RETURNING) must omit
        # enabled — it legitimately appears in the RETURNING projection.
        set_clause = UPSERT_USER_SQL.split('DO UPDATE SET', 1)[1].split('RETURNING', 1)[0]
        self.assertNotIn('enabled', set_clause.lower())

    def test_empty_claims_coalesced(self):
        # display_name/email refresh only from a non-empty claim.
        self.assertIn("COALESCE(NULLIF(EXCLUDED.\"display_name\", '')", UPSERT_USER_SQL)
        self.assertIn("COALESCE(NULLIF(EXCLUDED.\"email\", '')", UPSERT_USER_SQL)


if __name__ == '__main__':
    unittest.main()
