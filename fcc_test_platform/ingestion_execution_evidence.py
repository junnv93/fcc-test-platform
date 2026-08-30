"""Validate captured platform ingestion execution evidence metadata."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Mapping

from fcc_test_platform.evidence_primitives import is_sha256_hex
from fcc_test_platform.provider_ingestion_plan import INGESTION_TABLE_ORDER, PlatformIngestionPlan
from fcc_test_platform.provider_ingestion_worker import (
    COVERAGE_REFRESH_TOKENS,
    IngestionExecutionResult,
)


__all__ = [
    'IngestionExecutionIssue',
    'build_ingestion_execution_manifest',
    'canonical_ingestion_plan_sha256',
    'ingestion_execution_errors',
    'is_ingestion_execution_valid',
]


@dataclass(frozen=True)
class IngestionExecutionIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'path': self.path,
            'message': self.message,
        }


def is_ingestion_execution_valid(manifest: Mapping) -> bool:
    return not ingestion_execution_errors(manifest)


def canonical_ingestion_plan_sha256(plan: PlatformIngestionPlan | Mapping) -> str:
    payload = plan.to_dict() if isinstance(plan, PlatformIngestionPlan) else dict(plan)
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def build_ingestion_execution_manifest(
    *,
    evidence_id: str,
    provider_id: str,
    session_id: str,
    database_name: str,
    plan: PlatformIngestionPlan | Mapping,
    result: IngestionExecutionResult,
    collected_at: str = '',
) -> dict:
    return {
        'schema_version': 1,
        'evidence_id': _required_value(evidence_id, 'evidence_id'),
        'collected_at': collected_at or _utc_now(),
        'provider_id': _required_value(provider_id, 'provider_id'),
        'session_id': _required_value(session_id, 'session_id'),
        'database_name': _required_value(database_name, 'database_name'),
        'writer_adapter': 'postgres',
        'plan_sha256': canonical_ingestion_plan_sha256(plan),
        'executed': result.committed,
        'transaction_committed': result.committed,
        'transaction_rolled_back': result.rolled_back,
        'attempts': result.attempts,
        'attempted_steps': result.attempted_steps,
        'applied_steps': result.applied_steps,
        'errors': list(result.errors),
        'retry_errors': list(result.retry_errors),
        'steps': [step.to_dict() for step in result.steps],
        # Auditable coverage materialized view refresh outcome (was a silent
        # swallow). 'failed' is recorded but is NOT a hard validation error —
        # the measurement fact is durable regardless of refresh.
        'coverage_refresh': result.coverage_refresh,
        'coverage_refresh_error': result.coverage_refresh_error,
    }


def ingestion_execution_errors(manifest: Mapping) -> list[IngestionExecutionIssue]:
    issues: list[IngestionExecutionIssue] = []
    if manifest.get('schema_version') != 1:
        issues.append(_issue('invalid_schema_version', 'schema_version', 'schema_version must be 1'))
    for key in (
        'evidence_id',
        'collected_at',
        'provider_id',
        'session_id',
        'database_name',
        'writer_adapter',
        'plan_sha256',
    ):
        _require_text(manifest, key, key, issues)
    if _text(manifest.get('writer_adapter')) != 'postgres':
        issues.append(_issue('unsupported_writer_adapter', 'writer_adapter', 'writer_adapter must be postgres'))
    plan_sha256 = _text(manifest.get('plan_sha256'))
    if plan_sha256 and not is_sha256_hex(plan_sha256):
        issues.append(_issue('invalid_plan_sha256', 'plan_sha256', 'plan_sha256 must be 64 lowercase hex characters'))
    if manifest.get('executed') is not True:
        issues.append(_issue('not_executed', 'executed', 'executed must be true'))
    if manifest.get('transaction_committed') is not True:
        issues.append(_issue('transaction_not_committed', 'transaction_committed', 'transaction_committed must be true'))
    if manifest.get('transaction_rolled_back') is not False:
        issues.append(_issue('transaction_rolled_back', 'transaction_rolled_back', 'transaction_rolled_back must be false'))
    if _int(manifest.get('attempts')) is None or _int(manifest.get('attempts')) <= 0:
        issues.append(_issue('invalid_attempts', 'attempts', 'attempts must be positive'))
    _validate_step_counts(manifest, issues)
    if manifest.get('errors') not in ([], None):
        issues.append(_issue('execution_errors_present', 'errors', 'errors must be empty'))
    if 'retry_errors' in manifest and not isinstance(manifest.get('retry_errors'), list):
        issues.append(_issue('invalid_retry_errors', 'retry_errors', 'retry_errors must be a list when present'))
    if 'coverage_refresh' in manifest and _text(manifest.get('coverage_refresh')) not in COVERAGE_REFRESH_TOKENS:
        # 'failed' is an allowed (recorded, non-fatal) outcome — only an
        # unrecognised token is a validation error.
        issues.append(_issue('invalid_coverage_refresh', 'coverage_refresh', 'coverage_refresh must be one of succeeded/failed/not_required when present'))
    _validate_steps(manifest.get('steps'), issues)
    return issues


def _validate_step_counts(manifest: Mapping, issues: list[IngestionExecutionIssue]) -> None:
    attempted = _int(manifest.get('attempted_steps'))
    applied = _int(manifest.get('applied_steps'))
    steps = manifest.get('steps')
    step_count = len(steps) if isinstance(steps, list) else None
    if attempted is None or attempted <= 0:
        issues.append(_issue('invalid_attempted_steps', 'attempted_steps', 'attempted_steps must be positive'))
    if applied is None or applied <= 0:
        issues.append(_issue('invalid_applied_steps', 'applied_steps', 'applied_steps must be positive'))
    if attempted is not None and applied is not None and applied > attempted:
        issues.append(_issue('applied_steps_exceed_attempted', 'applied_steps', 'applied_steps must not exceed attempted_steps'))
    if step_count is not None and applied is not None and applied != step_count:
        issues.append(_issue('applied_steps_mismatch', 'applied_steps', 'applied_steps must match executed steps length'))


def _validate_steps(value, issues: list[IngestionExecutionIssue]) -> None:
    if not isinstance(value, list) or not value:
        issues.append(_issue('missing_steps', 'steps', 'at least one executed step is required'))
        return
    previous_order = 0
    allowed_tables = set(INGESTION_TABLE_ORDER)
    for index, raw in enumerate(value):
        path = f'steps[{index}]'
        step = _mapping(raw)
        if not step:
            issues.append(_issue('invalid_step', path, 'step must be an object'))
            continue
        order = _int(step.get('order'))
        if order is None or order <= previous_order:
            issues.append(_issue('invalid_step_order', f'{path}.order', 'step order must be strictly increasing'))
        else:
            previous_order = order
        table = _text(step.get('target_table'))
        if table not in allowed_tables:
            issues.append(_issue('unknown_target_table', f'{path}.target_table', 'target_table must be part of ingestion table order'))
        if _text(step.get('operation')) != 'upsert':
            issues.append(_issue('invalid_operation', f'{path}.operation', 'operation must be upsert'))
        key = step.get('idempotency_key')
        if not isinstance(key, list) or not [_text(value) for value in key if _text(value)]:
            issues.append(_issue('missing_idempotency_key', f'{path}.idempotency_key', 'idempotency_key must be a non-empty list'))
        if _int(step.get('affected_rows')) is None or _int(step.get('affected_rows')) < 0:
            issues.append(_issue('invalid_affected_rows', f'{path}.affected_rows', 'affected_rows must be >= 0'))


def _require_text(mapping: Mapping, key: str, path: str, issues: list[IngestionExecutionIssue]) -> None:
    if not _text(mapping.get(key)):
        issues.append(_issue('missing_required_field', path, f'{key} is required'))


def _mapping(value) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _required_value(value, key: str) -> str:
    text = _text(value)
    if not text:
        raise ValueError(f'{key} is required')
    return text


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _issue(code: str, path: str, message: str) -> IngestionExecutionIssue:
    return IngestionExecutionIssue(code=code, path=path, message=message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
