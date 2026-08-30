"""Validate artifact/report file-server sync evidence.

The validator checks metadata captured by a future sync worker. It does not
copy files, calculate hashes, call networks, or write database records.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from fcc_test_platform.artifact_storage import normalize_relative_path
from fcc_test_platform.evidence_primitives import is_sha256_hex


__all__ = [
    'ArtifactSyncIssue',
    'artifact_sync_evidence_errors',
    'is_artifact_sync_evidence_valid',
]


_VALID_RECORD_TYPES = {'artifact', 'report_output'}
_VALID_BACKENDS = {'filesystem', 'object_storage'}


@dataclass(frozen=True)
class ArtifactSyncIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'path': self.path,
            'message': self.message,
        }


def is_artifact_sync_evidence_valid(manifest: Mapping) -> bool:
    return not artifact_sync_evidence_errors(manifest)


def artifact_sync_evidence_errors(manifest: Mapping) -> list[ArtifactSyncIssue]:
    issues: list[ArtifactSyncIssue] = []
    if manifest.get('schema_version') != 1:
        issues.append(_issue('invalid_schema_version', 'schema_version', 'schema_version must be 1'))
    for key in ('provider_id', 'session_id', 'sync_job_id', 'source_root_id', 'destination_root_id', 'synced_at'):
        _require_text(manifest, key, key, issues)

    files = manifest.get('files')
    if not isinstance(files, list):
        issues.append(_issue('missing_files', 'files', 'files must be a list'))
        return issues
    if not files:
        issues.append(_issue('empty_files', 'files', 'files must not be empty'))
        return issues

    counts = {'artifact': 0, 'report_output': 0}
    seen_paths: set[tuple[str, str]] = set()
    for index, raw in enumerate(files):
        path = f'files[{index}]'
        record = _mapping(raw)
        if not record:
            issues.append(_issue('invalid_file_record', path, 'file record must be an object'))
            continue
        record_type = _text(record.get('record_type'))
        if record_type not in _VALID_RECORD_TYPES:
            issues.append(_issue('invalid_record_type', f'{path}.record_type', 'record_type must be artifact or report_output'))
        else:
            counts[record_type] += 1
        relative_path = _validate_relative_path(record.get('relative_path'), f'{path}.relative_path', issues)
        if record_type and relative_path:
            marker = (record_type, relative_path)
            if marker in seen_paths:
                issues.append(_issue('duplicate_relative_path', f'{path}.relative_path', 'duplicate sync record'))
            seen_paths.add(marker)
        backend = _text(record.get('storage_backend'))
        if backend not in _VALID_BACKENDS:
            issues.append(_issue('invalid_storage_backend', f'{path}.storage_backend', 'unsupported storage_backend'))
        _require_hash(record, 'sha256', f'{path}.sha256', issues)
        byte_size = _int(record.get('byte_size'))
        if byte_size is None:
            issues.append(_issue('missing_byte_size', f'{path}.byte_size', 'byte_size is required'))
        elif byte_size < 0:
            issues.append(_issue('invalid_byte_size', f'{path}.byte_size', 'byte_size must be non-negative'))
        if _text(record.get('sync_status')) != 'synced':
            issues.append(_issue('file_not_synced', f'{path}.sync_status', 'sync_status must be synced'))
        if record.get('source_exists') is not True:
            issues.append(_issue('source_missing', f'{path}.source_exists', 'source_exists must be true'))
        if record.get('destination_exists') is not True:
            issues.append(_issue('destination_missing', f'{path}.destination_exists', 'destination_exists must be true'))

    _validate_declared_count(manifest, 'artifact_count', counts['artifact'], issues)
    _validate_declared_count(manifest, 'report_output_count', counts['report_output'], issues)
    return issues


def _validate_declared_count(
    manifest: Mapping,
    key: str,
    actual: int,
    issues: list[ArtifactSyncIssue],
) -> None:
    declared = _int(manifest.get(key))
    if declared is None:
        issues.append(_issue('missing_declared_count', key, f'{key} is required'))
    elif declared != actual:
        issues.append(_issue('declared_count_mismatch', key, f'{key} must equal actual synced records'))


def _validate_relative_path(value, path: str, issues: list[ArtifactSyncIssue]) -> str:
    try:
        return normalize_relative_path(str(value or ''))
    except ValueError as exc:
        issues.append(_issue('invalid_relative_path', path, str(exc)))
        return ''


def _require_text(mapping: Mapping, key: str, path: str, issues: list[ArtifactSyncIssue]) -> None:
    if not _text(mapping.get(key)):
        issues.append(_issue('missing_required_field', path, f'{key} is required'))


def _require_hash(mapping: Mapping, key: str, path: str, issues: list[ArtifactSyncIssue]) -> None:
    value = _text(mapping.get(key))
    if not value:
        issues.append(_issue('missing_required_field', path, f'{key} is required'))
    elif not is_sha256_hex(value):
        issues.append(_issue('invalid_sha256', path, f'{key} must be a SHA-256 hex digest'))


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


def _issue(code: str, path: str, message: str) -> ArtifactSyncIssue:
    return ArtifactSyncIssue(code=code, path=path, message=message)
