"""Validate captured platform IdP deployment evidence metadata."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse


__all__ = [
    'IdpDeploymentIssue',
    'idp_deployment_errors',
    'is_idp_deployment_valid',
]


_ASYMMETRIC_ALGORITHMS = {'RS256', 'RS384', 'RS512', 'ES256', 'ES384', 'ES512'}


@dataclass(frozen=True)
class IdpDeploymentIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'path': self.path,
            'message': self.message,
        }


def is_idp_deployment_valid(manifest: Mapping) -> bool:
    return not idp_deployment_errors(manifest)


def idp_deployment_errors(manifest: Mapping) -> list[IdpDeploymentIssue]:
    issues: list[IdpDeploymentIssue] = []
    if manifest.get('schema_version') != 1:
        issues.append(_issue('invalid_schema_version', 'schema_version', 'schema_version must be 1'))
    for key in ('evidence_id', 'collected_at', 'provider_key', 'issuer'):
        _require_text(manifest, key, key, issues)
    _require_https_url(manifest.get('issuer'), 'issuer', issues)
    _validate_discovery(_mapping(manifest.get('discovery_document')), _text(manifest.get('issuer')), issues)
    _validate_jwks(_mapping(manifest.get('jwks')), issues)
    _validate_clients(manifest.get('clients'), issues)
    _validate_login_flow(_mapping(manifest.get('login_flow')), issues)
    _validate_session_persistence(_mapping(manifest.get('session_persistence')), issues)
    return issues


def _validate_discovery(discovery: Mapping, issuer: str, issues: list[IdpDeploymentIssue]) -> None:
    if not discovery:
        issues.append(_issue('missing_discovery_document', 'discovery_document', 'discovery_document is required'))
        return
    if _text(discovery.get('issuer')) != issuer:
        issues.append(_issue('issuer_mismatch', 'discovery_document.issuer', 'discovery issuer must match manifest issuer'))
    for key in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
        _require_https_url(discovery.get(key), f'discovery_document.{key}', issues)
    response_types = {_text(value) for value in discovery.get('response_types_supported') or [] if _text(value)}
    if 'code' not in response_types:
        issues.append(_issue('code_flow_not_supported', 'discovery_document.response_types_supported', 'authorization code flow must be supported'))
    pkce_methods = {_text(value).upper() for value in discovery.get('code_challenge_methods_supported') or [] if _text(value)}
    if 'S256' not in pkce_methods:
        issues.append(_issue('pkce_s256_not_supported', 'discovery_document.code_challenge_methods_supported', 'S256 PKCE must be supported'))


def _validate_jwks(jwks: Mapping, issues: list[IdpDeploymentIssue]) -> None:
    if not jwks:
        issues.append(_issue('missing_jwks', 'jwks', 'jwks evidence is required'))
        return
    if jwks.get('fetched') is not True:
        issues.append(_issue('jwks_not_fetched', 'jwks.fetched', 'jwks.fetched must be true'))
    key_count = _int(jwks.get('key_count'))
    if key_count is None or key_count <= 0:
        issues.append(_issue('missing_jwks_keys', 'jwks.key_count', 'key_count must be positive'))
    algorithms = {_text(value).upper() for value in jwks.get('algorithms') or [] if _text(value)}
    if not algorithms.intersection(_ASYMMETRIC_ALGORITHMS):
        issues.append(_issue('missing_asymmetric_jwks_algorithm', 'jwks.algorithms', 'JWKS must expose at least one asymmetric signing algorithm'))


def _validate_clients(value, issues: list[IdpDeploymentIssue]) -> None:
    if not isinstance(value, list) or not value:
        issues.append(_issue('missing_clients', 'clients', 'at least one frontend client registration is required'))
        return
    for index, raw in enumerate(value):
        path = f'clients[{index}]'
        client = _mapping(raw)
        if not client:
            issues.append(_issue('invalid_client', path, 'client must be an object'))
            continue
        _require_text(client, 'client_id', f'{path}.client_id', issues)
        if client.get('public_client') is not True:
            issues.append(_issue('client_not_public', f'{path}.public_client', 'frontend client must be public'))
        if client.get('client_secret_present') is not False:
            issues.append(_issue('client_secret_present', f'{path}.client_secret_present', 'frontend client must not have a client secret'))
        if client.get('pkce_required') is not True:
            issues.append(_issue('pkce_not_required', f'{path}.pkce_required', 'PKCE must be required'))
        _validate_url_list(client.get('redirect_uris'), f'{path}.redirect_uris', issues)
        _validate_url_list(client.get('post_logout_redirect_uris'), f'{path}.post_logout_redirect_uris', issues)
        scopes = {_text(value) for value in client.get('scopes') or [] if _text(value)}
        for required in ('openid', 'profile'):
            if required not in scopes:
                issues.append(_issue('missing_required_scope', f'{path}.scopes', f'{required} scope is required'))
        if not any(scope.startswith('api://') or scope.endswith('/access') for scope in scopes):
            issues.append(_issue('missing_platform_api_scope', f'{path}.scopes', 'platform API access scope is required'))


def _validate_login_flow(flow: Mapping, issues: list[IdpDeploymentIssue]) -> None:
    if not flow:
        issues.append(_issue('missing_login_flow', 'login_flow', 'login_flow evidence is required'))
        return
    required_flags = (
        'browser_login_verified',
        'redirect_state_verified',
        'token_exchange_verified',
        'access_token_audience_verified',
        'role_claim_verified',
    )
    for key in required_flags:
        if flow.get(key) is not True:
            issues.append(_issue(f'{key}_not_verified', f'login_flow.{key}', f'{key} must be true'))
    if flow.get('errors') not in ([], None):
        issues.append(_issue('login_flow_errors_present', 'login_flow.errors', 'login_flow.errors must be empty'))


def _validate_session_persistence(session: Mapping, issues: list[IdpDeploymentIssue]) -> None:
    if not session:
        issues.append(_issue('missing_session_persistence', 'session_persistence', 'session_persistence evidence is required'))
        return
    storage = _text(session.get('access_token_storage'))
    if storage not in {'memory', 'sessionStorage'}:
        issues.append(_issue('unsafe_access_token_storage', 'session_persistence.access_token_storage', 'access_token_storage must be memory or sessionStorage'))
    if session.get('local_storage_used') is not False:
        issues.append(_issue('local_storage_used', 'session_persistence.local_storage_used', 'localStorage must not store access tokens'))
    if session.get('logout_clears_session') is not True:
        issues.append(_issue('logout_not_clearing_session', 'session_persistence.logout_clears_session', 'logout_clears_session must be true'))


def _validate_url_list(value, path: str, issues: list[IdpDeploymentIssue]) -> None:
    if not isinstance(value, list) or not value:
        issues.append(_issue('missing_url_list', path, 'at least one URL is required'))
        return
    for index, url in enumerate(value):
        _require_https_url(url, f'{path}[{index}]', issues)


def _require_https_url(value, path: str, issues: list[IdpDeploymentIssue]) -> None:
    text = _text(value)
    if not text:
        issues.append(_issue('missing_required_field', path, 'HTTPS URL is required'))
        return
    parsed = urlparse(text)
    if parsed.scheme != 'https' or not parsed.netloc:
        issues.append(_issue('insecure_url', path, 'URL must use HTTPS'))
    host = (parsed.hostname or '').lower()
    if host in {'localhost', '127.0.0.1', '::1'} or host.endswith('.localhost'):
        issues.append(_issue('localhost_url', path, 'URL must not use localhost'))


def _require_text(mapping: Mapping, key: str, path: str, issues: list[IdpDeploymentIssue]) -> None:
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


def _issue(code: str, path: str, message: str) -> IdpDeploymentIssue:
    return IdpDeploymentIssue(code=code, path=path, message=message)
