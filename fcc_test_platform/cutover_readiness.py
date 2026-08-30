"""Validate platform cutover readiness evidence bundles.

The gate checks captured evidence only. It does not run hardware, apply
migrations, sync files, validate JWTs, call networks, or write databases.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from fcc_test_platform.application.platform_cutover_catalog import (
    catalog_entry,
    catalog_keys,
)
from fcc_test_platform.evidence_primitives import placeholder_value_paths
# Keep the platform-owned validator dependency explicit.  The catalog remains
# the single dispatch/inventory source; this import preserves the lane's
# scheduled validator relationship for extraction-boundary checks.
from fcc_test_contracts.common.provider_service_evidence import (  # noqa: F401
    provider_service_deployment_errors as _platform_service_deployment_errors,
)


__all__ = [
    'CUTOVER_REQUIRED_EVIDENCE',
    'CutoverReadinessIssue',
    'cutover_readiness_errors',
    'is_cutover_ready',
    'validate_collector_manifest',
]


CUTOVER_REQUIRED_EVIDENCE = catalog_keys()


@dataclass(frozen=True)
class CutoverReadinessIssue:
    code: str
    path: str
    message: str
    evidence_key: str = ''

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'path': self.path,
            'message': self.message,
            'evidence_key': self.evidence_key,
        }


def is_cutover_ready(
    bundle: Mapping,
    *,
    central_db_schema: Mapping | None = None,
    extraction_manifest: Mapping | None = None,
) -> bool:
    return not cutover_readiness_errors(
        bundle,
        central_db_schema=central_db_schema,
        extraction_manifest=extraction_manifest,
    )


def cutover_readiness_errors(
    bundle: Mapping,
    *,
    central_db_schema: Mapping | None = None,
    extraction_manifest: Mapping | None = None,
) -> list[CutoverReadinessIssue]:
    issues: list[CutoverReadinessIssue] = []
    if bundle.get('schema_version') != 1:
        issues.append(_issue('invalid_schema_version', 'schema_version', 'schema_version must be 1'))
    for key in ('provider_id', 'cutover_candidate_id', 'evaluated_at'):
        _require_text(bundle, key, key, issues)

    evidence = _mapping(bundle.get('evidence'))
    if not evidence:
        issues.append(_issue('missing_evidence', 'evidence', 'evidence bundle is required'))
        return issues

    for key in CUTOVER_REQUIRED_EVIDENCE:
        item = _mapping(evidence.get(key))
        if not item:
            issues.append(_issue('missing_required_evidence', f'evidence.{key}', f'{key} evidence is required', key))
            continue
        issues.extend(_validate_manifest_item(
            key,
            item,
            central_db_schema=central_db_schema,
            extraction_manifest=extraction_manifest,
        ))

    extra = set(evidence) - set(CUTOVER_REQUIRED_EVIDENCE)
    for key in sorted(extra):
        issues.append(_issue('unknown_evidence_key', f'evidence.{key}', 'unknown cutover evidence key', key))
    return issues


def _validate_manifest_item(
    key: str,
    item: Mapping,
    *,
    central_db_schema: Mapping | None,
    extraction_manifest: Mapping | None,
) -> list[CutoverReadinessIssue]:
    issues = _validate_generic_item(key, item)
    manifest = _mapping(item.get('manifest'))
    if not manifest:
        issues.append(_issue('missing_manifest', f'evidence.{key}.manifest', 'manifest is required', key))
        return issues

    issues.extend(validate_collector_manifest(
        key,
        manifest,
        central_db_schema=central_db_schema,
        extraction_manifest=extraction_manifest,
    ))
    return issues


def validate_collector_manifest(
    key: str,
    manifest: Mapping,
    *,
    central_db_schema: Mapping | None,
    extraction_manifest: Mapping | None,
) -> list[CutoverReadinessIssue]:
    """Validate one raw collector manifest through its catalog binding."""
    entry = catalog_entry(key)
    issues: list[CutoverReadinessIssue] = []
    if not isinstance(manifest, Mapping):
        return [_issue(
            'invalid_manifest',
            f'evidence.{key}.manifest',
            'collector output must be a JSON object',
            key,
        )]

    issues.extend(_placeholder_evidence_issues(key, manifest))
    missing_contexts = [
        context
        for context in entry.required_contexts
        if (context == 'central_db_schema' and central_db_schema is None)
        or (context == 'extraction_manifest' and extraction_manifest is None)
    ]
    if missing_contexts:
        for context in missing_contexts:
            issues.append(_issue(
                f'missing_{context}',
                f'evidence.{key}.manifest.{context}',
                f'{context} is required for {key} validation',
                key,
            ))
        return issues

    if 'central_db_schema' in entry.required_contexts:
        sub_issues = entry.validator(manifest, central_db_schema)
    elif 'extraction_manifest' in entry.required_contexts:
        sub_issues = entry.validator(manifest, extraction_manifest=extraction_manifest)
    else:
        sub_issues = entry.validator(manifest)
    for sub_issue in sub_issues:
        code = str(getattr(sub_issue, 'code', 'invalid_manifest'))
        path = str(getattr(sub_issue, 'path', 'manifest'))
        message = str(getattr(sub_issue, 'message', 'manifest validation failed'))
        issues.append(_issue(code, f'evidence.{key}.manifest.{path}', message, key))
    return issues


def _placeholder_evidence_issues(key: str, manifest: Mapping) -> list[CutoverReadinessIssue]:
    """Reject manifests that still carry angle-bracket template sentinels.

    A real evidence manifest collected from a live environment never contains
    ``<...>`` placeholder tokens (those come from the suggested-command
    templates). Any hit means the bundle is a template/fixture rather than
    production evidence, so the cutover gate must fail it.
    """
    issues: list[CutoverReadinessIssue] = []
    for path, tokens in placeholder_value_paths(manifest):
        issues.append(_issue(
            'placeholder_evidence_value',
            f'evidence.{key}.manifest.{path}',
            f'manifest still contains unresolved placeholder tokens {list(tokens)}; '
            'real cutover evidence must not carry template sentinels',
            key,
        ))
    return issues


def _validate_generic_item(key: str, item: Mapping) -> list[CutoverReadinessIssue]:
    issues: list[CutoverReadinessIssue] = []
    _require_text(item, 'evidence_id', f'evidence.{key}.evidence_id', issues, key)
    _require_text(item, 'collected_at', f'evidence.{key}.collected_at', issues, key)
    if item.get('validated') is not True:
        issues.append(_issue('evidence_not_validated', f'evidence.{key}.validated', 'validated must be true', key))
    raw_issues = item.get('issues')
    if not isinstance(raw_issues, list):
        issues.append(_issue('missing_evidence_issues', f'evidence.{key}.issues', 'issues must be a list', key))
    elif raw_issues:
        issues.append(_issue('evidence_has_issues', f'evidence.{key}.issues', 'evidence issues must be empty', key))
    return issues


def _require_text(
    mapping: Mapping,
    key: str,
    path: str,
    issues: list[CutoverReadinessIssue],
    evidence_key: str = '',
) -> None:
    if not _text(mapping.get(key)):
        issues.append(_issue('missing_required_field', path, f'{key} is required', evidence_key))


def _mapping(value) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _issue(code: str, path: str, message: str, evidence_key: str = '') -> CutoverReadinessIssue:
    return CutoverReadinessIssue(code=code, path=path, message=message, evidence_key=evidence_key)
