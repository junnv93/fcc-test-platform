"""Platform RBAC seed policy derived from the headless API contract."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from fcc_test_contracts.common.access_policy import API_PERMISSION_ADMIN, API_PERMISSION_PUBLIC
from fcc_test_contracts.headless.api_contracts import HEADLESS_API_OPERATIONS


__all__ = [
    'PlatformRbacSeed',
    'RoleSeed',
    'build_platform_rbac_seed',
    'operation_permission_keys',
    'validate_platform_rbac_seed',
]


@dataclass(frozen=True)
class RoleSeed:
    role_key: str
    description: str
    permission_keys: tuple[str, ...]

    def to_record(self) -> dict:
        return {
            'role_key': self.role_key,
            'description': self.description,
            'permission_keys': list(self.permission_keys),
        }


@dataclass(frozen=True)
class PlatformRbacSeed:
    permissions: tuple[str, ...]
    roles: tuple[RoleSeed, ...] = field(default_factory=tuple)

    def to_records(self) -> dict:
        return {
            'permissions': [
                {'permission_key': permission, 'description': _permission_description(permission)}
                for permission in self.permissions
            ],
            'roles': [role.to_record() for role in self.roles],
        }


def operation_permission_keys(operations: Mapping[str, Mapping] | None = None) -> tuple[str, ...]:
    """Return non-public permission keys declared by the API operation contract."""

    source = operations or HEADLESS_API_OPERATIONS
    permissions = {
        _permission(contract)
        for contract in source.values()
        if _permission(contract) and _permission(contract) != API_PERMISSION_PUBLIC
    }
    return tuple(sorted(permissions))


def build_platform_rbac_seed(
    operations: Mapping[str, Mapping] | None = None,
) -> PlatformRbacSeed:
    operation_permissions = operation_permission_keys(operations)
    read_permissions = tuple(permission for permission in operation_permissions if permission.endswith(':read'))
    control_permissions = tuple(permission for permission in operation_permissions if permission.endswith(':control'))
    headless_permissions = tuple(permission for permission in operation_permissions if permission.startswith('headless:'))
    report_permissions = tuple(permission for permission in operation_permissions if permission.startswith('report_automation:'))
    roles = (
        RoleSeed(
            role_key='viewer',
            description='Read-only access to provider status, results, artifacts, reports, and outputs.',
            permission_keys=read_permissions,
        ),
        RoleSeed(
            role_key='operator',
            description='Can monitor provider state and submit or stop measurement jobs.',
            permission_keys=headless_permissions + tuple(
                permission for permission in report_permissions if permission.endswith(':read')
            ),
        ),
        RoleSeed(
            role_key='report_manager',
            description='Can read provider state and control report automation.',
            permission_keys=read_permissions + control_permissions,
        ),
        RoleSeed(
            role_key='admin',
            description='Platform administrator; provider adapter treats admin as a super-permission.',
            permission_keys=(API_PERMISSION_ADMIN,),
        ),
    )
    seed = PlatformRbacSeed(
        permissions=tuple(sorted((*operation_permissions, API_PERMISSION_ADMIN))),
        roles=roles,
    )
    errors = validate_platform_rbac_seed(seed, operation_permissions=operation_permissions)
    if errors:
        raise ValueError('; '.join(errors))
    return seed


def validate_platform_rbac_seed(
    seed: PlatformRbacSeed,
    *,
    operation_permissions: tuple[str, ...] | None = None,
) -> list[str]:
    operation_permissions = operation_permissions or operation_permission_keys()
    allowed_permissions = set(operation_permissions) | {API_PERMISSION_ADMIN}
    errors: list[str] = []
    seed_permissions = set(seed.permissions)
    missing = set(operation_permissions) - seed_permissions
    if missing:
        errors.append(f'missing operation permissions: {sorted(missing)}')
    unknown = seed_permissions - allowed_permissions
    if unknown:
        errors.append(f'unknown seed permissions: {sorted(unknown)}')
    role_keys = [role.role_key for role in seed.roles]
    if len(role_keys) != len(set(role_keys)):
        errors.append('duplicate role keys')
    for role in seed.roles:
        role_unknown = set(role.permission_keys) - allowed_permissions
        if role_unknown:
            errors.append(f'{role.role_key} grants unknown permissions: {sorted(role_unknown)}')
        if role.role_key != 'admin' and API_PERMISSION_ADMIN in role.permission_keys:
            errors.append(f'{role.role_key} must not grant admin')
    return errors


def _permission(contract: Mapping) -> str:
    value = contract.get('permission')
    return str(value or '').strip()


def _permission_description(permission: str) -> str:
    if permission == API_PERMISSION_ADMIN:
        return 'Platform administrator super-permission.'
    if permission.endswith(':read'):
        return f'Read operations for {permission.split(":", 1)[0]}.'
    if permission.endswith(':control'):
        return f'Control operations for {permission.split(":", 1)[0]}.'
    return 'Provider API operation permission.'
