from __future__ import annotations

import sys
import inspect
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / 'src', ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fcc_test_contracts.common.access_policy import ApiPrincipal  # noqa: E402
from fcc_test_contracts.common.identity import LOCAL_IDENTITY_ISSUER  # noqa: E402
from fcc_test_contracts.common.local_identity import (  # noqa: E402
    LOCAL_JWT_MIN_SECRET_BYTES,
    TOKEN_TYPE_ACCESS,
    LocalJwtConfig,
    verify_token,
)
from fcc_test_platform.application.local_auth_service import LocalAuthService  # noqa: E402
import fcc_test_platform.application.local_user_store as local_user_store_module  # noqa: E402
from fcc_test_platform.application.local_user_store import (  # noqa: E402
    GLOBAL_PERMISSIONS_SQL,
    LOCAL_PERMISSIONS_SQL,
    PostgresLocalUserStore,
)
from fcc_test_platform.application.password_hasher import BcryptPasswordHasher  # noqa: E402


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
SECRET = 'k' * LOCAL_JWT_MIN_SECRET_BYTES
JWT = LocalJwtConfig(
    secret=SECRET,
    issuer='https://fcc.internal/auth',
    audience='fcc-web',
)


class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.statement = None
        self.params = None

    def execute(self, statement, params):
        self.statement = statement
        self.params = params

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _Connection:
    def __init__(self, rows):
        self.cursor_instance = _Cursor(rows)
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def close(self):
        self.closed = True


def test_local_permission_sql_is_a_separate_union_from_global_only_sql():
    assert 'role_permissions' not in GLOBAL_PERMISSIONS_SQL
    assert 'global_role_grants' in GLOBAL_PERMISSIONS_SQL

    assert 'role_permissions' in LOCAL_PERMISSIONS_SQL
    assert 'global_role_grants' in LOCAL_PERMISSIONS_SQL
    assert 'permissions' in LOCAL_PERMISSIONS_SQL
    assert 'u."issuer"' in LOCAL_PERMISSIONS_SQL
    assert 'gr."role_key" = r."role_key"' in LOCAL_PERMISSIONS_SQL
    assert 'project_admin' not in LOCAL_PERMISSIONS_SQL
    assert 'system_admin' not in LOCAL_PERMISSIONS_SQL
    assert 'platform:read' not in LOCAL_PERMISSIONS_SQL


def test_local_permission_sql_has_one_module_level_ssot_assignment():
    source = inspect.getsource(local_user_store_module)
    assert source.count('LOCAL_PERMISSIONS_SQL =') == 1


def test_postgres_store_executes_the_canonical_local_permission_resolver():
    connection = _Connection([
        ('platform:admin',),
        ('platform:read',),
        ('platform:sample-hard-delete',),
    ])
    store = PostgresLocalUserStore(lambda: connection)

    permissions = store.local_permissions('user-1')

    assert permissions == (
        'platform:admin',
        'platform:read',
        'platform:sample-hard-delete',
    )
    assert connection.cursor_instance.statement == LOCAL_PERMISSIONS_SQL
    assert connection.cursor_instance.params == (
        'user-1', 'urn:fcc:identity:local',
        'user-1', 'urn:fcc:identity:local',
    )
    assert connection.closed


class _ResolverStore:
    def __init__(self, *, force_password_change: bool):
        hasher = BcryptPasswordHasher(cost_rounds=4)
        self.user = {
            'id': 'user-1',
            'subject': 'admin@example.test',
            'display_name': 'Admin',
            'email': 'admin@example.test',
            'enabled': True,
            'password_hash': hasher.hash('Old-Password-1!'),
            'force_password_change': force_password_change,
            'password_changed_at': None if force_password_change else NOW,
            'session_version': 1,
            'failed_login_attempts': 0,
            'locked_until': None,
        }
        self.local_permission_calls = 0

    def find_by_email(self, email):
        if str(email).strip().lower() != self.user['email']:
            return None
        return dict(self.user)

    def record_successful_login(self, user_id, *, now):
        assert user_id == self.user['id']

    def record_failed_login(self, user_id, *, now):
        raise AssertionError('the happy-path test must not record a failure')

    def update_password(self, user_id, *, password_hash, now):
        assert user_id == self.user['id']
        self.user['password_hash'] = password_hash
        self.user['force_password_change'] = False
        self.user['password_changed_at'] = now
        self.user['session_version'] += 1
        return self.user['session_version']

    def local_permissions(self, user_id):
        assert user_id == self.user['id']
        self.local_permission_calls += 1
        return ('platform:admin', 'platform:read')

    def global_permissions(self, user_id):
        raise AssertionError('the local-auth token must use local_permissions()')


def _service(store):
    return LocalAuthService(
        store=store,
        hasher=BcryptPasswordHasher(cost_rounds=4),
        jwt_config=JWT,
        clock=lambda: NOW,
        epoch_clock=lambda: int(datetime.now(timezone.utc).timestamp()),
    )


def test_post_change_token_uses_local_resolver_and_forced_change_stays_permissionless():
    store = _ResolverStore(force_password_change=True)
    service = _service(store)
    principal = ApiPrincipal.from_permissions(
        store.user['subject'], [], issuer=LOCAL_IDENTITY_ISSUER,
        force_password_change=True,
    )

    changed = service.change_password(
        principal,
        current_password='Old-Password-1!',
        new_password='New-Password-2!',
    )
    changed_claims = verify_token(
        JWT, changed['access_token'], expected_type=TOKEN_TYPE_ACCESS,
    )

    assert changed['force_password_change'] is False
    assert changed_claims['fpc'] is False
    assert changed_claims['permissions'] == ['platform:admin', 'platform:read']
    assert store.local_permission_calls == 1

    forced_store = _ResolverStore(force_password_change=True)
    forced = _service(forced_store).login(
        email=forced_store.user['email'], password='Old-Password-1!',
    )
    forced_claims = verify_token(
        JWT, forced['access_token'], expected_type=TOKEN_TYPE_ACCESS,
    )

    assert forced['force_password_change'] is True
    assert forced_claims['permissions'] == []
    assert forced_store.local_permission_calls == 0
