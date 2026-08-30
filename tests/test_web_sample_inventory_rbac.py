from __future__ import annotations

import json
from pathlib import Path

from fcc_test_platform.application.local_auth_service import (
    BOOTSTRAP_GLOBAL_ROLE_KEY,
    bootstrap_local_admin,
)
from fcc_test_platform.application.local_user_store import GLOBAL_PERMISSIONS_SQL
from fcc_test_platform.application.rbac_role_catalog import (
    GLOBAL_ROLE_KEYS,
    global_permissions_for,
    permissions_for,
)


ROOT = Path(__file__).resolve().parents[1]


def test_sample_editor_is_one_whole_record_permission_for_pm_and_engineer():
    for role in ('project_pm', 'project_engineer', 'project_admin'):
        assert 'platform:sample-write' in permissions_for(role)
    assert 'platform:sample-hard-delete' not in permissions_for('project_admin')
    assert 'system_admin' in GLOBAL_ROLE_KEYS
    assert global_permissions_for('system_admin') == frozenset({'platform:sample-hard-delete'})


def test_global_permission_lookup_cannot_promote_project_membership():
    assert 'global_role_grants' in GLOBAL_PERMISSIONS_SQL
    assert 'role_permissions' not in GLOBAL_PERMISSIONS_SQL
    assert 'project_member_permissions' not in GLOBAL_PERMISSIONS_SQL


def test_bootstrap_adds_dedicated_global_role_without_changing_project_role_contract():
    class Store:
        def __init__(self):
            self.grants = []

        def count_local_users(self):
            return 0

        def create_local_user(self, **kwargs):
            return {'id': 'user-1', 'subject': 'user-1', **kwargs}

        def grant_role(self, user_id, role_key):
            self.grants.append(('project-or-legacy', user_id, role_key))

        def grant_global_role(self, user_id, role_key):
            self.grants.append(('global', user_id, role_key))

    class Hasher:
        def hash(self, password):
            return 'hash'

    store = Store()
    created = bootstrap_local_admin(
        store=store, hasher=Hasher(), email='admin@example.test', password='Strong-Password-1!',
        now='2026-08-24T00:00:00Z', id_factory=lambda: 'user-1',
    )
    assert created['id'] == 'user-1'
    assert ('global', 'user-1', BOOTSTRAP_GLOBAL_ROLE_KEY) in store.grants


def test_dev_realm_exposes_only_the_new_sample_write_and_hard_delete_tokens():
    realm = json.loads((ROOT / 'infra/keycloak/fcc-dev-realm.json').read_text())
    permissions = {
        value
        for group in realm.get('groups', [])
        for value in (group.get('attributes') or {}).get('permissions', [])
    }
    sample_permissions = {
        value for value in permissions if value.startswith('platform:sample-')
    }
    assert sample_permissions == {
        'platform:sample-write', 'platform:sample-hard-delete',
    }
