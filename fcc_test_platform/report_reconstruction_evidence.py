"""Validate DB-only report reconstruction evidence for web cutover.

The validator checks captured metadata only. It does not open Excel, databases,
file-server artifacts, DOCX, PDF, or generated report files.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from fcc_test_platform.artifact_storage import normalize_relative_path
from fcc_test_platform.evidence_primitives import is_sha256_hex


__all__ = [
    'ReportReconstructionIssue',
    'build_db_only_report_reconstruction_manifest',
    'db_only_report_reconstruction_errors',
    'is_db_only_report_reconstruction_valid',
]


_ALLOWED_AUDIT_STATUSES = {'pass', 'approved_with_findings'}
_ALLOWED_MISSING_CLASSIFICATIONS = {
    'template_scope_exclusion',
    'not_required_for_report_type',
    'operator_approved_missing',
}
_REQUIRED_TOP_LEVEL = (
    'schema_version',
    'provider_id',
    'session_id',
    'report_run_id',
    'source_mode',
    'excel_source_used',
    'excel_export_only',
    'source_snapshot',
    'generated_outputs',
    'artifact_resolution',
    'acceptance_audit',
)


@dataclass(frozen=True)
class ReportReconstructionIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'path': self.path,
            'message': self.message,
        }


def is_db_only_report_reconstruction_valid(manifest: Mapping) -> bool:
    return not db_only_report_reconstruction_errors(manifest)


def build_db_only_report_reconstruction_manifest(
    *,
    provider_id: str,
    session_id: str,
    report_run_id: str,
    central_db_migration_evidence_id: str,
    ingestion_batch_id: str,
    source_snapshot_sha256: str,
    measurement_result_count: int,
    generated_outputs: list[Mapping],
    required_artifact_count: int,
    resolved_artifact_count: int,
    missing_artifacts: list[Mapping] | None = None,
    acceptance_status: str,
    reviewed_by: str,
    reviewed_at: str,
) -> dict:
    return {
        'schema_version': 1,
        'provider_id': _required_value(provider_id, 'provider_id'),
        'session_id': _required_value(session_id, 'session_id'),
        'report_run_id': _required_value(report_run_id, 'report_run_id'),
        'source_mode': 'db_primary',
        'excel_source_used': False,
        'excel_export_only': True,
        'source_snapshot': {
            'central_db_migration_evidence_id': _required_value(
                central_db_migration_evidence_id,
                'central_db_migration_evidence_id',
            ),
            'ingestion_batch_id': _required_value(ingestion_batch_id, 'ingestion_batch_id'),
            'snapshot_sha256': _required_value(source_snapshot_sha256, 'source_snapshot_sha256'),
            'measurement_result_count': int(measurement_result_count),
        },
        'generated_outputs': [_generated_output(output) for output in generated_outputs],
        'artifact_resolution': {
            'required_count': int(required_artifact_count),
            'resolved_count': int(resolved_artifact_count),
            'missing': [dict(item) for item in (missing_artifacts or [])],
        },
        'acceptance_audit': {
            'status': _required_value(acceptance_status, 'acceptance_status').lower(),
            'reviewed_by': _required_value(reviewed_by, 'reviewed_by'),
            'reviewed_at': _required_value(reviewed_at, 'reviewed_at'),
        },
    }


def db_only_report_reconstruction_errors(manifest: Mapping) -> list[ReportReconstructionIssue]:
    issues: list[ReportReconstructionIssue] = []
    if manifest.get('schema_version') != 1:
        issues.append(_issue('invalid_schema_version', 'schema_version', 'schema_version must be 1'))
    for key in _REQUIRED_TOP_LEVEL:
        if key not in manifest:
            issues.append(_issue('missing_top_level', key, f'{key} is required'))

    if _text(manifest.get('source_mode')) != 'db_primary':
        issues.append(_issue('invalid_source_mode', 'source_mode', 'source_mode must be db_primary'))
    if manifest.get('excel_source_used') is not False:
        issues.append(_issue('excel_source_used', 'excel_source_used', 'excel_source_used must be false'))
    if manifest.get('excel_export_only') is not True:
        issues.append(_issue('excel_not_export_only', 'excel_export_only', 'excel_export_only must be true'))
    _require_text(manifest, 'provider_id', 'provider_id', issues)
    _require_text(manifest, 'session_id', 'session_id', issues)
    _require_text(manifest, 'report_run_id', 'report_run_id', issues)

    _validate_source_snapshot(_mapping(manifest.get('source_snapshot')), issues)
    _validate_outputs(manifest.get('generated_outputs'), issues)
    _validate_artifact_resolution(_mapping(manifest.get('artifact_resolution')), issues)
    _validate_acceptance_audit(_mapping(manifest.get('acceptance_audit')), issues)
    _validate_missing_artifact_acceptance(
        _mapping(manifest.get('artifact_resolution')),
        _mapping(manifest.get('acceptance_audit')),
        issues,
    )
    return issues


def _validate_source_snapshot(snapshot: Mapping, issues: list[ReportReconstructionIssue]) -> None:
    if not snapshot:
        issues.append(_issue('missing_source_snapshot', 'source_snapshot', 'source_snapshot is required'))
        return
    _require_text(snapshot, 'central_db_migration_evidence_id', 'source_snapshot.central_db_migration_evidence_id', issues)
    _require_text(snapshot, 'ingestion_batch_id', 'source_snapshot.ingestion_batch_id', issues)
    _require_hash(snapshot, 'snapshot_sha256', 'source_snapshot.snapshot_sha256', issues)
    measurement_count = _int(snapshot.get('measurement_result_count'))
    if measurement_count is None:
        issues.append(
            _issue(
                'missing_measurement_count',
                'source_snapshot.measurement_result_count',
                'measurement_result_count is required',
            )
        )
    elif measurement_count <= 0:
        issues.append(
            _issue(
                'empty_measurement_results',
                'source_snapshot.measurement_result_count',
                'measurement_result_count must be positive',
            )
        )


def _validate_outputs(value, issues: list[ReportReconstructionIssue]) -> None:
    if not isinstance(value, list):
        issues.append(_issue('missing_generated_outputs', 'generated_outputs', 'generated_outputs must be a list'))
        return
    if not value:
        issues.append(_issue('empty_generated_outputs', 'generated_outputs', 'at least one generated output is required'))
        return

    has_docx_or_pdf = False
    for index, raw in enumerate(value):
        path = f'generated_outputs[{index}]'
        output = _mapping(raw)
        if not output:
            issues.append(_issue('invalid_output', path, 'output must be an object'))
            continue
        output_type = _text(output.get('output_type')).lower()
        if output_type in {'docx', 'pdf'}:
            has_docx_or_pdf = True
        if output_type not in {'docx', 'pdf', 'xlsx'}:
            issues.append(_issue('invalid_output_type', f'{path}.output_type', 'output_type must be docx, pdf, or xlsx'))
        _validate_relative_path(output.get('relative_path'), f'{path}.relative_path', issues)
        _require_text(output, 'storage_backend', f'{path}.storage_backend', issues)
        _require_hash(output, 'sha256', f'{path}.sha256', issues)
    if not has_docx_or_pdf:
        issues.append(_issue('missing_report_output', 'generated_outputs', 'at least one DOCX or PDF output is required'))


def _validate_artifact_resolution(resolution: Mapping, issues: list[ReportReconstructionIssue]) -> None:
    if not resolution:
        issues.append(_issue('missing_artifact_resolution', 'artifact_resolution', 'artifact_resolution is required'))
        return
    required_count = _int(resolution.get('required_count'))
    resolved_count = _int(resolution.get('resolved_count'))
    if required_count is None:
        issues.append(_issue('missing_required_artifact_count', 'artifact_resolution.required_count', 'required_count is required'))
    if resolved_count is None:
        issues.append(_issue('missing_resolved_artifact_count', 'artifact_resolution.resolved_count', 'resolved_count is required'))

    missing = resolution.get('missing') or []
    if not isinstance(missing, list):
        issues.append(_issue('invalid_missing_artifacts', 'artifact_resolution.missing', 'missing must be a list'))
        return
    for index, raw in enumerate(missing):
        path = f'artifact_resolution.missing[{index}]'
        item = _mapping(raw)
        classification = _text(item.get('classification'))
        if classification not in _ALLOWED_MISSING_CLASSIFICATIONS:
            issues.append(
                _issue(
                    'unclassified_missing_artifact',
                    f'{path}.classification',
                    'missing artifact must have an approved classification',
                )
            )
        _require_text(item, 'reviewed_by', f'{path}.reviewed_by', issues)
        _require_text(item, 'reason', f'{path}.reason', issues)

    if required_count is not None and resolved_count is not None:
        classified_missing = len(missing)
        if resolved_count + classified_missing < required_count:
            issues.append(
                _issue(
                    'artifact_coverage_gap',
                    'artifact_resolution',
                    'resolved plus classified missing artifacts must cover required_count',
                )
            )


def _validate_acceptance_audit(audit: Mapping, issues: list[ReportReconstructionIssue]) -> None:
    if not audit:
        issues.append(_issue('missing_acceptance_audit', 'acceptance_audit', 'acceptance_audit is required'))
        return
    status = _text(audit.get('status')).lower()
    if status not in _ALLOWED_AUDIT_STATUSES:
        issues.append(
            _issue(
                'invalid_acceptance_status',
                'acceptance_audit.status',
                'status must be pass or approved_with_findings',
            )
        )
    _require_text(audit, 'reviewed_by', 'acceptance_audit.reviewed_by', issues)
    _require_text(audit, 'reviewed_at', 'acceptance_audit.reviewed_at', issues)


def _validate_missing_artifact_acceptance(
    resolution: Mapping,
    audit: Mapping,
    issues: list[ReportReconstructionIssue],
) -> None:
    missing = resolution.get('missing') if resolution else []
    if not isinstance(missing, list) or not missing:
        return
    if _text(audit.get('status')).lower() != 'approved_with_findings':
        issues.append(
            _issue(
                'missing_artifacts_require_findings',
                'acceptance_audit.status',
                'missing artifacts require acceptance status approved_with_findings',
            )
        )


def _validate_relative_path(value, path: str, issues: list[ReportReconstructionIssue]) -> None:
    try:
        normalize_relative_path(str(value or ''))
    except ValueError as exc:
        issues.append(_issue('invalid_relative_path', path, str(exc)))


def _require_text(mapping: Mapping, key: str, path: str, issues: list[ReportReconstructionIssue]) -> None:
    if not _text(mapping.get(key)):
        issues.append(_issue('missing_required_field', path, f'{key} is required'))


def _require_hash(mapping: Mapping, key: str, path: str, issues: list[ReportReconstructionIssue]) -> None:
    value = _text(mapping.get(key))
    if not value:
        issues.append(_issue('missing_required_field', path, f'{key} is required'))
    elif not is_sha256_hex(value):
        issues.append(_issue('invalid_sha256', path, f'{key} must be a SHA-256 hex digest'))


def _generated_output(output: Mapping) -> dict:
    item = dict(output)
    return {
        'output_type': _required_value(item.get('output_type'), 'output_type').lower(),
        'relative_path': _required_value(item.get('relative_path'), 'relative_path'),
        'storage_backend': _required_value(item.get('storage_backend'), 'storage_backend'),
        'sha256': _required_value(item.get('sha256'), 'sha256'),
    }


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


def _issue(code: str, path: str, message: str) -> ReportReconstructionIssue:
    return ReportReconstructionIssue(code=code, path=path, message=message)
