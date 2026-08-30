"""Validate captured platform frontend deployment evidence metadata."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

from fcc_test_platform.evidence_primitives import is_sha256_hex


__all__ = [
    'FrontendDeploymentIssue',
    'frontend_deployment_errors',
    'is_frontend_deployment_valid',
]


_SECRET_NAME_TOKENS = (
    'secret',
    'token',
    'password',
    'passwd',
    'private_key',
    'client_secret',
    'api_key',
)


@dataclass(frozen=True)
class FrontendDeploymentIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'path': self.path,
            'message': self.message,
        }


def is_frontend_deployment_valid(manifest: Mapping) -> bool:
    return not frontend_deployment_errors(manifest)


def frontend_deployment_errors(manifest: Mapping) -> list[FrontendDeploymentIssue]:
    issues: list[FrontendDeploymentIssue] = []
    if manifest.get('schema_version') != 1:
        issues.append(_issue('invalid_schema_version', 'schema_version', 'schema_version must be 1'))
    for key in (
        'evidence_id',
        'collected_at',
        'app_url',
        'backend_base_url',
        'hosting_provider',
        'build_version',
        'build_sha256',
    ):
        _require_text(manifest, key, key, issues)
    _validate_https_url(manifest.get('app_url'), 'app_url', issues)
    _validate_https_url(manifest.get('backend_base_url'), 'backend_base_url', issues)
    if manifest.get('deployed') is not True:
        issues.append(_issue('frontend_not_deployed', 'deployed', 'deployed must be true'))
    if manifest.get('tls_valid') is not True:
        issues.append(_issue('tls_not_valid', 'tls_valid', 'tls_valid must be true'))
    sha256 = _text(manifest.get('build_sha256'))
    if sha256 and not is_sha256_hex(sha256):
        issues.append(_issue('invalid_build_sha256', 'build_sha256', 'build_sha256 must be 64 lowercase hex characters'))
    _validate_cache_policy(manifest.get('asset_cache_policy'), issues)
    _validate_environment(manifest.get('environment'), manifest.get('backend_base_url'), issues)
    _validate_secret_scan(manifest.get('secret_scan'), issues)
    return issues


def _validate_https_url(value, path: str, issues: list[FrontendDeploymentIssue]) -> None:
    raw = _text(value)
    if not raw:
        return
    parsed = urlparse(raw)
    if parsed.scheme != 'https' or not parsed.netloc:
        issues.append(_issue('non_https_url', path, f'{path} must be an https URL'))
        return
    host = (parsed.hostname or '').lower()
    if host in {'localhost', '127.0.0.1', '::1'} or host.endswith('.localhost'):
        issues.append(_issue('local_url', path, f'{path} must not point at localhost'))


def _validate_cache_policy(value, issues: list[FrontendDeploymentIssue]) -> None:
    policy = _mapping(value)
    if not policy:
        issues.append(_issue('missing_cache_policy', 'asset_cache_policy', 'asset_cache_policy is required'))
        return
    if policy.get('immutable_assets') is not True:
        issues.append(_issue('immutable_assets_not_enabled', 'asset_cache_policy.immutable_assets', 'immutable_assets must be true'))
    html_cache_control = _text(policy.get('html_cache_control')).lower()
    if 'no-store' not in html_cache_control:
        issues.append(_issue('html_cache_not_no_store', 'asset_cache_policy.html_cache_control', 'html_cache_control must include no-store'))


def _validate_environment(value, backend_base_url, issues: list[FrontendDeploymentIssue]) -> None:
    environment = _mapping(value)
    if not environment:
        issues.append(_issue('missing_environment', 'environment', 'environment is required'))
        return
    _require_text(environment, 'name', 'environment.name', issues)
    configured_backend = _text(environment.get('backend_base_url'))
    if configured_backend != _text(backend_base_url):
        issues.append(_issue('backend_config_mismatch', 'environment.backend_base_url', 'environment backend_base_url must match manifest backend_base_url'))
    variables = environment.get('variables')
    if not isinstance(variables, list):
        issues.append(_issue('missing_environment_variables', 'environment.variables', 'environment.variables must be a list'))
        return
    for index, raw in enumerate(variables):
        path = f'environment.variables[{index}]'
        variable = _mapping(raw)
        if not variable:
            issues.append(_issue('invalid_environment_variable', path, 'environment variable must be an object'))
            continue
        name = _text(variable.get('name'))
        value_text = _text(variable.get('value'))
        if not name:
            issues.append(_issue('missing_required_field', f'{path}.name', 'name is required'))
        if _is_secret_name(name) and not _is_redacted_value(value_text):
            issues.append(_issue('unredacted_secret', f'{path}.value', 'secret-like environment values must be redacted'))


def _validate_secret_scan(value, issues: list[FrontendDeploymentIssue]) -> None:
    scan = _mapping(value)
    if not scan:
        issues.append(_issue('missing_secret_scan', 'secret_scan', 'secret_scan is required'))
        return
    if _text(scan.get('status')) != 'pass':
        issues.append(_issue('secret_scan_not_passed', 'secret_scan.status', 'secret_scan.status must be pass'))
    findings = scan.get('findings')
    if findings != []:
        issues.append(_issue('secret_scan_findings_present', 'secret_scan.findings', 'secret_scan.findings must be an empty list'))


def _is_secret_name(value: str) -> bool:
    normalized = value.lower().replace('-', '_')
    return any(token in normalized for token in _SECRET_NAME_TOKENS)


def _is_redacted_value(value: str) -> bool:
    return value in {'<redacted>', 'REDACTED', '********', ''}


def _require_text(mapping: Mapping, key: str, path: str, issues: list[FrontendDeploymentIssue]) -> None:
    if not _text(mapping.get(key)):
        issues.append(_issue('missing_required_field', path, f'{key} is required'))


def _mapping(value) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _issue(code: str, path: str, message: str) -> FrontendDeploymentIssue:
    return FrontendDeploymentIssue(code=code, path=path, message=message)
