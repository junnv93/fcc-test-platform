"""Validate applied platform RBAC assignment evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from fcc_test_platform.rbac import build_platform_rbac_seed


__all__ = [
    'RbacAssignmentIssue',
    'is_rbac_assignment_evidence_valid',
    'rbac_assignment_evidence_errors',
]


@dataclass(frozen=True)
class RbacAssignmentIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'path': self.path,
            'message': self.message,
        }


def is_rbac_assignment_evidence_valid(manifest: Mapping) -> bool:
    return not rbac_assignment_evidence_errors(manifest)


def rbac_assignment_evidence_errors(manifest: Mapping) -> list[RbacAssignmentIssue]:
    issues: list[RbacAssignmentIssue] = []
    if manifest.get('schema_version') != 1:
        issues.append(_issue('invalid_schema_version', 'schema_version', 'schema_version must be 1'))
    for key in ('evidence_id', 'collected_at', 'rbac_source'):
        _require_text(manifest, key, key, issues)
    if _text(manifest.get('rbac_source')) != 'docs/platform/rbac_policy.v1.json':
        issues.append(_issue('invalid_rbac_source', 'rbac_source', 'rbac_source must be docs/platform/rbac_policy.v1.json'))
    seed = build_platform_rbac_seed()
    _validate_permissions(manifest.get('permissions'), {permission for permission in seed.permissions}, issues)
    _validate_roles(manifest.get('roles'), {role.role_key: set(role.permission_keys) for role in seed.roles}, issues)
    _validate_users_and_assignments(manifest.get('users'), manifest.get('user_roles'), issues)
    return issues


def _validate_permissions(value, expected: set[str], issues: list[RbacAssignmentIssue]) -> None:
    if not isinstance(value, list):
        issues.append(_issue('missing_permissions', 'permissions', 'permissions must be a list'))
        return
    actual = {_text(item.get('permission_key')) for item in value if isinstance(item, Mapping)}
    missing = expected - actual
    extra = actual - expected
    for permission in sorted(missing):
        issues.append(_issue('missing_permission', 'permissions', f'{permission} permission is required'))
    for permission in sorted(extra):
        issues.append(_issue('unknown_permission', 'permissions', f'{permission} is not in RBAC seed'))


def _validate_roles(value, expected: dict[str, set[str]], issues: list[RbacAssignmentIssue]) -> None:
    if not isinstance(value, list):
        issues.append(_issue('missing_roles', 'roles', 'roles must be a list'))
        return
    actual = {_text(item.get('role_key')): item for item in value if isinstance(item, Mapping)}
    for role_key, expected_permissions in expected.items():
        role = _mapping(actual.get(role_key))
        if not role:
            issues.append(_issue('missing_role', 'roles', f'{role_key} role is required'))
            continue
        permissions = {str(permission).strip() for permission in role.get('permission_keys') or [] if str(permission).strip()}
        if permissions != expected_permissions:
            issues.append(_issue('role_permission_mismatch', f'roles.{role_key}.permission_keys', 'role permissions must match RBAC seed'))
    for role_key in sorted(set(actual) - set(expected)):
        issues.append(_issue('unknown_role', f'roles.{role_key}', 'role is not in RBAC seed'))


def _validate_users_and_assignments(users_value, assignments_value, issues: list[RbacAssignmentIssue]) -> None:
    if not isinstance(users_value, list) or not users_value:
        issues.append(_issue('missing_users', 'users', 'at least one enabled user is required'))
        return
    users = {
        _text(item.get('subject')): item
        for item in users_value
        if isinstance(item, Mapping) and _text(item.get('subject'))
    }
    enabled_users = {subject for subject, item in users.items() if item.get('enabled') is True}
    if not enabled_users:
        issues.append(_issue('missing_enabled_user', 'users', 'at least one enabled user is required'))
    if not isinstance(assignments_value, list) or not assignments_value:
        issues.append(_issue('missing_user_roles', 'user_roles', 'at least one user role assignment is required'))
        return
    assigned_subjects: set[str] = set()
    admin_subjects: set[str] = set()
    seed_roles = {role.role_key for role in build_platform_rbac_seed().roles}
    for index, raw in enumerate(assignments_value):
        path = f'user_roles[{index}]'
        assignment = _mapping(raw)
        subject = _text(assignment.get('subject'))
        role_key = _text(assignment.get('role_key'))
        if subject not in users:
            issues.append(_issue('unknown_user_assignment', f'{path}.subject', 'assignment subject must exist in users'))
        elif subject not in enabled_users:
            issues.append(_issue('disabled_user_assignment', f'{path}.subject', 'assigned user must be enabled'))
        else:
            assigned_subjects.add(subject)
        if role_key not in seed_roles:
            issues.append(_issue('unknown_role_assignment', f'{path}.role_key', 'assignment role must exist in RBAC seed'))
        if role_key == 'admin' and subject in enabled_users:
            admin_subjects.add(subject)
    if enabled_users - assigned_subjects:
        issues.append(_issue('unassigned_enabled_user', 'user_roles', 'every enabled user must have at least one role'))
    if not admin_subjects:
        issues.append(_issue('missing_admin_assignment', 'user_roles', 'at least one enabled user must have admin role'))


def _require_text(mapping: Mapping, key: str, path: str, issues: list[RbacAssignmentIssue]) -> None:
    if not _text(mapping.get(key)):
        issues.append(_issue('missing_required_field', path, f'{key} is required'))


def _mapping(value) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _issue(code: str, path: str, message: str) -> RbacAssignmentIssue:
    return RbacAssignmentIssue(code=code, path=path, message=message)
