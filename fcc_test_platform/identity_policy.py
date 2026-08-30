"""Validate platform identity-provider policy metadata.

This module validates configuration evidence only. It does not validate JWTs,
call identity-provider endpoints, write sessions, or persist users.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

from fcc_test_platform.rbac import build_platform_rbac_seed


__all__ = [
    'IdentityPolicyIssue',
    'identity_policy_errors',
    'is_identity_policy_valid',
]


_MIN_SESSION_TIMEOUT_SECONDS = 300
_MAX_SESSION_TIMEOUT_SECONDS = 12 * 60 * 60
_MIN_REFRESH_ROTATION_SECONDS = 60


@dataclass(frozen=True)
class IdentityPolicyIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'path': self.path,
            'message': self.message,
        }


def is_identity_policy_valid(policy: Mapping) -> bool:
    return not identity_policy_errors(policy)


def identity_policy_errors(policy: Mapping) -> list[IdentityPolicyIssue]:
    issues: list[IdentityPolicyIssue] = []
    if policy.get('schema_version') != 1:
        issues.append(_issue('invalid_schema_version', 'schema_version', 'schema_version must be 1'))
    _require_text(policy, 'provider_key', 'provider_key', issues)
    _validate_oidc(_mapping(policy.get('oidc')), issues)
    _validate_session(_mapping(policy.get('session')), issues)
    _validate_claim_mapping(_mapping(policy.get('claim_mapping')), issues)
    return issues


def _validate_oidc(oidc: Mapping, issues: list[IdentityPolicyIssue]) -> None:
    if not oidc:
        issues.append(_issue('missing_oidc', 'oidc', 'oidc configuration is required'))
        return
    _require_https_url(oidc, 'issuer', 'oidc.issuer', issues)
    _require_https_url(oidc, 'jwks_uri', 'oidc.jwks_uri', issues)
    audiences = oidc.get('audiences')
    if not isinstance(audiences, list) or not [_text(value) for value in audiences if _text(value)]:
        issues.append(_issue('missing_audiences', 'oidc.audiences', 'at least one audience is required'))
    _require_text(oidc, 'algorithm', 'oidc.algorithm', issues)
    if _text(oidc.get('algorithm')).upper() not in {'RS256', 'RS384', 'RS512', 'ES256', 'ES384', 'ES512'}:
        issues.append(_issue('weak_algorithm', 'oidc.algorithm', 'algorithm must be an asymmetric JWT algorithm'))


def _validate_session(session: Mapping, issues: list[IdentityPolicyIssue]) -> None:
    if not session:
        issues.append(_issue('missing_session', 'session', 'session policy is required'))
        return
    timeout = _int(session.get('idle_timeout_seconds'))
    if timeout is None:
        issues.append(_issue('missing_idle_timeout', 'session.idle_timeout_seconds', 'idle_timeout_seconds is required'))
    elif not _MIN_SESSION_TIMEOUT_SECONDS <= timeout <= _MAX_SESSION_TIMEOUT_SECONDS:
        issues.append(
            _issue(
                'invalid_idle_timeout',
                'session.idle_timeout_seconds',
                'idle_timeout_seconds must be between 300 and 43200',
            )
        )
    if session.get('refresh_token_rotation') is not True:
        issues.append(
            _issue(
                'refresh_rotation_disabled',
                'session.refresh_token_rotation',
                'refresh_token_rotation must be true',
            )
        )
    rotation = _int(session.get('refresh_rotation_seconds'))
    if rotation is None:
        issues.append(
            _issue(
                'missing_refresh_rotation_seconds',
                'session.refresh_rotation_seconds',
                'refresh_rotation_seconds is required',
            )
        )
    elif rotation < _MIN_REFRESH_ROTATION_SECONDS:
        issues.append(
            _issue(
                'invalid_refresh_rotation_seconds',
                'session.refresh_rotation_seconds',
                'refresh_rotation_seconds must be >= 60',
            )
        )


def _validate_claim_mapping(mapping: Mapping, issues: list[IdentityPolicyIssue]) -> None:
    if not mapping:
        issues.append(_issue('missing_claim_mapping', 'claim_mapping', 'claim_mapping is required'))
        return
    _require_text(mapping, 'subject_claim', 'claim_mapping.subject_claim', issues)
    _require_text(mapping, 'display_name_claim', 'claim_mapping.display_name_claim', issues)
    _require_text(mapping, 'email_claim', 'claim_mapping.email_claim', issues)
    _require_text(mapping, 'role_claim', 'claim_mapping.role_claim', issues)

    standard_roles = {role.role_key for role in build_platform_rbac_seed().roles}
    role_map = _mapping(mapping.get('role_map'))
    if not role_map:
        issues.append(_issue('missing_role_map', 'claim_mapping.role_map', 'role_map is required'))
        return
    admin_allowlist = {
        _text(value)
        for value in mapping.get('admin_group_allowlist') or []
        if _text(value)
    }
    for external_group, role_key in role_map.items():
        group = _text(external_group)
        role = _text(role_key)
        if not group:
            issues.append(_issue('empty_external_group', 'claim_mapping.role_map', 'external group must not be empty'))
        if role not in standard_roles:
            issues.append(
                _issue(
                    'unknown_role_mapping',
                    f'claim_mapping.role_map.{group}',
                    'role_map values must use standard platform roles',
                )
            )
        if role == 'admin' and group not in admin_allowlist:
            issues.append(
                _issue(
                    'admin_mapping_not_allowlisted',
                    f'claim_mapping.role_map.{group}',
                    'admin role group mappings must be explicitly allowlisted',
                )
            )


def _require_https_url(
    mapping: Mapping,
    key: str,
    path: str,
    issues: list[IdentityPolicyIssue],
) -> None:
    value = _text(mapping.get(key))
    if not value:
        issues.append(_issue('missing_required_field', path, f'{key} is required'))
        return
    parsed = urlparse(value)
    if parsed.scheme != 'https' or not parsed.netloc:
        issues.append(_issue('insecure_url', path, f'{key} must be an HTTPS URL'))
    host = parsed.hostname or ''
    if host in {'localhost', '127.0.0.1'}:
        issues.append(_issue('localhost_url', path, f'{key} must not use localhost'))


def _require_text(
    mapping: Mapping,
    key: str,
    path: str,
    issues: list[IdentityPolicyIssue],
) -> None:
    if not _text(mapping.get(key)):
        issues.append(_issue('missing_required_field', path, f'{key} is required'))


def _mapping(value) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _issue(code: str, path: str, message: str) -> IdentityPolicyIssue:
    return IdentityPolicyIssue(code=code, path=path, message=message)
