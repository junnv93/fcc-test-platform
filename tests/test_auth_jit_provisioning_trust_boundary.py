import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from fcc_test_contracts.common.access_policy import ApiAccessPolicy, ApiPrincipal
from fcc_test_contracts.common.auth_config import HttpAuthConfig
from fcc_test_contracts.common.principal_resolver import create_principal_resolver
from fcc_test_contracts.common.oidc_principal_resolver import (
    BearerTokenPrincipalResolver,
    OidcJwtConfig,
)
from application.central_contract.api_contracts import PLATFORM_API_OPERATIONS
from fcc_test_platform.application.user_provisioning_service import UserProvisioningService
from domain.ports.output.central_user_write_port import UserWriteError
from fcc_test_platform.api.platform_routes import (
    PlatformApiAdapter,
    PlatformAuthorizationError,
    api_error_status,
)


class _UserPort:
    def __init__(self, *, enabled=True, error=None):
        self.enabled = enabled
        self.error = error
        self.records = []

    def ensure_user(self, record):
        if self.error is not None:
            raise self.error
        self.records.append(dict(record))
        return {**record, 'enabled': self.enabled}


class _RbacRead:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.user_enabled_calls = 0

    def user_enabled(self, subject, *, user_issuer=''):
        self.user_enabled_calls += 1
        return self.enabled

    def effective_permissions(self, project_id, subject, *, user_issuer=''):
        return set()


class _Request:
    headers = {'authorization': 'Bearer token'}


class TestUserProvisioningService(unittest.TestCase):
    def test_uses_only_verified_principal_metadata(self):
        port = _UserPort()
        service = UserProvisioningService(
            port, id_factory=lambda: 'user-1', clock=lambda: '2026-06-29T00:00:00+00:00',
        )
        principal = ApiPrincipal.from_permissions(
            'oid-123', [], issuer='https://issuer.example',
            display_name='Verified Name', email='verified@example.com',
        )

        service.ensure_provisioned(principal)

        self.assertEqual(port.records[0]['subject'], 'oid-123')
        self.assertEqual(port.records[0]['issuer'], 'https://issuer.example')
        self.assertEqual(port.records[0]['display_name'], 'Verified Name')
        self.assertEqual(port.records[0]['email'], 'verified@example.com')

    def test_dedup_skips_repeated_write_for_same_request_scope(self):
        port = _UserPort()
        service = UserProvisioningService(port, id_factory=lambda: 'u', clock=lambda: 'now')
        dedup = set()
        principal = ApiPrincipal.from_permissions('sub', [], issuer='iss')

        service.ensure_provisioned(principal, dedup=dedup)
        service.ensure_provisioned(principal, dedup=dedup)

        self.assertEqual(len(port.records), 1)

    def test_disabled_user_fails_closed(self):
        service = UserProvisioningService(_UserPort(enabled=False))
        with self.assertRaises(PermissionError):
            service.ensure_provisioned(ApiPrincipal.from_permissions('sub', [], issuer='iss'))


class TestPlatformProvisioningBoundary(unittest.TestCase):
    def test_authorize_provisions_before_token_decision_and_reads_enabled_each_time(self):
        port = _UserPort()
        provisioning = UserProvisioningService(port, id_factory=lambda: 'u', clock=lambda: 'now')
        rbac = _RbacRead(enabled=True)
        adapter = PlatformApiAdapter(
            object(),
            access_policy=ApiAccessPolicy(PLATFORM_API_OPERATIONS),
            principal=ApiPrincipal.from_permissions('sub', ['platform:read'], issuer='iss'),
            rbac_read_service=rbac,
            user_provisioning_service=provisioning,
        )

        adapter.authorize('get_project_coverage', project_id='p')
        rbac.enabled = False
        with self.assertRaises(PlatformAuthorizationError):
            adapter.authorize('get_project_coverage', project_id='p')

        self.assertEqual(len(port.records), 1)
        self.assertEqual(rbac.user_enabled_calls, 2)

    def test_anonymous_principal_does_not_provision(self):
        port = _UserPort()
        provisioning = UserProvisioningService(port)
        adapter = PlatformApiAdapter(
            object(),
            access_policy=ApiAccessPolicy(PLATFORM_API_OPERATIONS),
            principal=ApiPrincipal.anonymous(),
            rbac_read_service=_RbacRead(enabled=True),
            user_provisioning_service=provisioning,
        )
        with self.assertRaises(PlatformAuthorizationError):
            adapter.authorize('get_project_coverage', project_id='p')
        self.assertEqual(port.records, [])

    def test_user_write_error_maps_to_503(self):
        self.assertEqual(api_error_status(UserWriteError('down')), 503)
        provisioning = UserProvisioningService(_UserPort(error=UserWriteError('down')))
        adapter = PlatformApiAdapter(
            object(),
            access_policy=ApiAccessPolicy(PLATFORM_API_OPERATIONS),
            principal=ApiPrincipal.from_permissions('sub', ['platform:read'], issuer='iss'),
            rbac_read_service=_RbacRead(enabled=True),
            user_provisioning_service=provisioning,
        )
        with self.assertRaises(UserWriteError) as ctx:
            adapter.authorize('get_project_coverage', project_id='p')
        self.assertEqual(api_error_status(ctx.exception), 503)

    def test_with_principal_copies_provisioning_collaborator_with_request_dedup(self):
        port = _UserPort()
        provisioning = UserProvisioningService(port, id_factory=lambda: 'u', clock=lambda: 'now')
        shared = PlatformApiAdapter(
            object(),
            access_policy=ApiAccessPolicy(PLATFORM_API_OPERATIONS),
            rbac_read_service=_RbacRead(enabled=True),
            user_provisioning_service=provisioning,
        )
        scoped = shared.with_principal(
            ApiPrincipal.from_permissions('sub', ['platform:read'], issuer='iss')
        )

        scoped.authorize('get_project_coverage', project_id='p')
        scoped.authorize('get_project_coverage', project_id='p')

        self.assertEqual(len(port.records), 1)


class TestOidcProvisioningClaims(unittest.TestCase):
    def test_custom_subject_name_email_claims_are_verified_jwt_only(self):
        fake_jwt = _fake_jwt({
            'oid': 'object-id-1',
            'displayName': 'OID Name',
            'mail': 'oid@example.com',
        })
        resolver = BearerTokenPrincipalResolver(
            OidcJwtConfig(
                issuer='https://login.example.com/tenant',
                audience='fcc-platform',
                jwks_uri='https://login.example.com/tenant/keys',
                subject_claim='oid',
                name_claim='displayName',
                email_claim='mail',
            ),
            key_provider=types.SimpleNamespace(get_key=lambda kid: 'public-key'),
        )

        with patch.dict(sys.modules, {'jwt': fake_jwt}):
            principal = resolver.resolve(_Request())

        self.assertEqual(principal.subject, 'object-id-1')
        self.assertEqual(principal.display_name, 'OID Name')
        self.assertEqual(principal.email, 'oid@example.com')

    def test_entra_startup_requires_oid_subject_claim(self):
        with self.assertRaisesRegex(ValueError, 'Microsoft Entra.*oidc_subject_claim'):
            create_principal_resolver(
                HttpAuthConfig(
                    auth_mode='oidc_jwt',
                    oidc_issuer='https://login.microsoftonline.com/tenant/v2.0',
                    oidc_audience='fcc-platform',
                    oidc_jwks_uri='https://login.microsoftonline.com/tenant/keys',
                )
            )


def _fake_jwt(claims):
    module = types.SimpleNamespace()
    module.get_unverified_header = lambda token: {'kid': 'key-1'}

    def decode(token, key, algorithms, audience, issuer, leeway=0):
        return claims

    module.decode = decode
    return module


if __name__ == '__main__':
    unittest.main()
