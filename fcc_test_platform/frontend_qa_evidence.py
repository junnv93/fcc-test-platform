"""Validate platform frontend browser QA evidence metadata."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from fcc_test_platform.artifact_storage import normalize_relative_path
from fcc_test_platform.evidence_primitives import is_sha256_hex


__all__ = [
    'FRONTEND_REQUIRED_VIEWS',
    'FrontendQaIssue',
    'frontend_qa_errors',
    'is_frontend_qa_valid',
]


FRONTEND_REQUIRED_VIEWS = (
    'overview',
    'jobs',
    'session',
    'reports',
)


@dataclass(frozen=True)
class FrontendQaIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'path': self.path,
            'message': self.message,
        }


def is_frontend_qa_valid(manifest: Mapping) -> bool:
    return not frontend_qa_errors(manifest)


def frontend_qa_errors(manifest: Mapping) -> list[FrontendQaIssue]:
    issues: list[FrontendQaIssue] = []
    if manifest.get('schema_version') != 1:
        issues.append(_issue('invalid_schema_version', 'schema_version', 'schema_version must be 1'))
    for key in ('evidence_id', 'collected_at', 'app_url', 'browser'):
        _require_text(manifest, key, key, issues)
    if manifest.get('auth_flow_verified') is not True:
        issues.append(_issue('auth_flow_not_verified', 'auth_flow_verified', 'auth_flow_verified must be true'))
    if manifest.get('central_backend_verified') is not True:
        issues.append(_issue('central_backend_not_verified', 'central_backend_verified', 'central_backend_verified must be true'))
    if manifest.get('provider_contract_routes_verified') is not True:
        issues.append(_issue('provider_routes_not_verified', 'provider_contract_routes_verified', 'provider_contract_routes_verified must be true'))

    _validate_viewports(manifest.get('viewport_results'), issues)
    _validate_screenshots(manifest.get('screenshots'), issues)
    return issues


def _validate_viewports(value, issues: list[FrontendQaIssue]) -> None:
    if not isinstance(value, list):
        issues.append(_issue('missing_viewports', 'viewport_results', 'viewport_results must be a list'))
        return
    if not value:
        issues.append(_issue('empty_viewports', 'viewport_results', 'viewport_results must not be empty'))
        return
    covered_views: set[str] = set()
    for index, raw in enumerate(value):
        path = f'viewport_results[{index}]'
        result = _mapping(raw)
        if not result:
            issues.append(_issue('invalid_viewport_result', path, 'viewport result must be an object'))
            continue
        for key in ('name', 'width', 'height'):
            if key in {'width', 'height'}:
                if _int(result.get(key)) is None or _int(result.get(key)) <= 0:
                    issues.append(_issue(f'invalid_{key}', f'{path}.{key}', f'{key} must be positive'))
            else:
                _require_text(result, key, f'{path}.{key}', issues)
        if result.get('rendered') is not True:
            issues.append(_issue('viewport_not_rendered', f'{path}.rendered', 'rendered must be true'))
        if result.get('responsive_pass') is not True:
            issues.append(_issue('responsive_check_failed', f'{path}.responsive_pass', 'responsive_pass must be true'))
        if result.get('console_errors') not in ([], None):
            issues.append(_issue('console_errors_present', f'{path}.console_errors', 'console_errors must be empty'))
        if result.get('failed_requests') not in ([], None):
            issues.append(_issue('failed_requests_present', f'{path}.failed_requests', 'failed_requests must be empty'))
        views = result.get('views_verified')
        if isinstance(views, list):
            covered_views.update(str(view) for view in views if str(view or '').strip())
    missing_views = set(FRONTEND_REQUIRED_VIEWS) - covered_views
    for view in sorted(missing_views):
        issues.append(_issue('missing_required_view', 'viewport_results.views_verified', f'{view} view must be verified'))


def _validate_screenshots(value, issues: list[FrontendQaIssue]) -> None:
    if not isinstance(value, list):
        issues.append(_issue('missing_screenshots', 'screenshots', 'screenshots must be a list'))
        return
    if not value:
        issues.append(_issue('empty_screenshots', 'screenshots', 'screenshots must not be empty'))
        return
    for index, raw in enumerate(value):
        path = f'screenshots[{index}]'
        screenshot = _mapping(raw)
        if not screenshot:
            issues.append(_issue('invalid_screenshot', path, 'screenshot must be an object'))
            continue
        for key in ('viewport', 'relative_path', 'sha256'):
            _require_text(screenshot, key, f'{path}.{key}', issues)
        _validate_relative_path(screenshot.get('relative_path'), f'{path}.relative_path', issues)
        sha256 = _text(screenshot.get('sha256'))
        if sha256 and not is_sha256_hex(sha256):
            issues.append(_issue('invalid_sha256', f'{path}.sha256', 'sha256 must be 64 lowercase hex characters'))


def _validate_relative_path(value, path: str, issues: list[FrontendQaIssue]) -> None:
    try:
        normalize_relative_path(str(value or ''))
    except ValueError as exc:
        issues.append(_issue('invalid_relative_path', path, str(exc)))


def _require_text(mapping: Mapping, key: str, path: str, issues: list[FrontendQaIssue]) -> None:
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


def _issue(code: str, path: str, message: str) -> FrontendQaIssue:
    return FrontendQaIssue(code=code, path=path, message=message)
