"""Validate platform performance smoke evidence metadata."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


__all__ = [
    'PERFORMANCE_SMOKE_ROUTES',
    'PerformanceSmokeIssue',
    'is_performance_smoke_valid',
    'performance_smoke_errors',
]


PERFORMANCE_SMOKE_ROUTES = (
    '/health',
    '/headless/api-contract',
    '/headless/capabilities',
)


@dataclass(frozen=True)
class PerformanceSmokeIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'path': self.path,
            'message': self.message,
        }


def is_performance_smoke_valid(manifest: Mapping) -> bool:
    return not performance_smoke_errors(manifest)


def performance_smoke_errors(manifest: Mapping) -> list[PerformanceSmokeIssue]:
    issues: list[PerformanceSmokeIssue] = []
    if manifest.get('schema_version') != 1:
        issues.append(_issue('invalid_schema_version', 'schema_version', 'schema_version must be 1'))
    for key in ('provider_id', 'benchmark_id', 'collected_at', 'target_base_url'):
        _require_text(manifest, key, key, issues)

    max_p95_ms = _float(manifest.get('max_p95_ms'))
    if max_p95_ms is None or max_p95_ms <= 0:
        issues.append(_issue('invalid_max_p95_ms', 'max_p95_ms', 'max_p95_ms must be positive'))

    iterations = _int(manifest.get('iterations'))
    workers = _int(manifest.get('workers'))
    if iterations is None or iterations < 1:
        issues.append(_issue('invalid_iterations', 'iterations', 'iterations must be >= 1'))
    if workers is None or workers < 1:
        issues.append(_issue('invalid_workers', 'workers', 'workers must be >= 1'))

    if manifest.get('compatible') is not True:
        issues.append(_issue('not_compatible', 'compatible', 'compatible must be true'))
    if manifest.get('within_budget') is not True:
        issues.append(_issue('outside_budget', 'within_budget', 'within_budget must be true'))
    if _int(manifest.get('total_failures')) != 0:
        issues.append(_issue('has_failures', 'total_failures', 'total_failures must be 0'))

    _validate_routes(manifest.get('routes'), max_p95_ms=max_p95_ms, issues=issues)
    return issues


def _validate_routes(
    value,
    *,
    max_p95_ms: float | None,
    issues: list[PerformanceSmokeIssue],
) -> None:
    if not isinstance(value, list):
        issues.append(_issue('missing_routes', 'routes', 'routes must be a list'))
        return
    by_route = {}
    for index, raw in enumerate(value):
        path = f'routes[{index}]'
        record = _mapping(raw)
        if not record:
            issues.append(_issue('invalid_route_record', path, 'route benchmark must be an object'))
            continue
        route = _text(record.get('route'))
        by_route[route] = record
        if route not in PERFORMANCE_SMOKE_ROUTES:
            issues.append(_issue('unknown_route', f'{path}.route', 'route is not in the safe benchmark route set'))
        requests = _int(record.get('requests'))
        successes = _int(record.get('successes'))
        failures = _int(record.get('failures'))
        if requests is None or requests < 1:
            issues.append(_issue('invalid_requests', f'{path}.requests', 'requests must be >= 1'))
        if successes is None or successes < 1:
            issues.append(_issue('invalid_successes', f'{path}.successes', 'successes must be >= 1'))
        if failures != 0:
            issues.append(_issue('route_has_failures', f'{path}.failures', 'route failures must be 0'))
        p95_ms = _float(record.get('p95_ms'))
        if p95_ms is None:
            issues.append(_issue('missing_p95_ms', f'{path}.p95_ms', 'p95_ms is required'))
        elif max_p95_ms is not None and p95_ms > max_p95_ms:
            issues.append(_issue('route_p95_exceeded', f'{path}.p95_ms', 'p95_ms exceeds max_p95_ms'))

    missing = set(PERFORMANCE_SMOKE_ROUTES) - set(by_route)
    for route in sorted(missing):
        issues.append(_issue('missing_route', 'routes', f'{route} benchmark is required'))


def _require_text(mapping: Mapping, key: str, path: str, issues: list[PerformanceSmokeIssue]) -> None:
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


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _issue(code: str, path: str, message: str) -> PerformanceSmokeIssue:
    return PerformanceSmokeIssue(code=code, path=path, message=message)
