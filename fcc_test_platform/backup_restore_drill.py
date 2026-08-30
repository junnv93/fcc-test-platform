"""Validate platform DB plus artifact backup/restore drill evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from fcc_test_platform.artifact_storage import normalize_relative_path
from fcc_test_platform.evidence_primitives import is_sha256_hex


__all__ = [
    'RestoreDrillIssue',
    'backup_restore_drill_errors',
    'is_backup_restore_drill_valid',
]


@dataclass(frozen=True)
class RestoreDrillIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'path': self.path,
            'message': self.message,
        }


def is_backup_restore_drill_valid(manifest: Mapping) -> bool:
    return not backup_restore_drill_errors(manifest)


def backup_restore_drill_errors(manifest: Mapping) -> list[RestoreDrillIssue]:
    issues: list[RestoreDrillIssue] = []
    if manifest.get('schema_version') != 1:
        issues.append(_issue('invalid_schema_version', 'schema_version', 'schema_version must be 1'))

    restore_point_id = _text(manifest.get('restore_point_id'))
    if not restore_point_id:
        issues.append(_issue('missing_restore_point', 'restore_point_id', 'restore_point_id is required'))

    db_backup = _mapping(manifest.get('db_backup'))
    if not db_backup:
        issues.append(_issue('missing_db_backup', 'db_backup', 'db_backup is required'))
    else:
        _validate_backup_record(
            db_backup,
            path='db_backup',
            restore_point_id=restore_point_id,
            issues=issues,
            require_relative_path=False,
        )
        if not _text(db_backup.get('database_name')):
            issues.append(_issue('missing_database_name', 'db_backup.database_name', 'database_name is required'))

    artifact_backup = _mapping(manifest.get('artifact_backup'))
    if not artifact_backup:
        issues.append(_issue('missing_artifact_backup', 'artifact_backup', 'artifact_backup is required'))
    else:
        _validate_backup_record(
            artifact_backup,
            path='artifact_backup',
            restore_point_id=restore_point_id,
            issues=issues,
            require_relative_path=True,
        )

    _validate_restored_records(
        manifest.get('artifacts'),
        record_type='artifact',
        issues=issues,
    )
    _validate_restored_records(
        manifest.get('report_outputs'),
        record_type='report_output',
        issues=issues,
    )

    return issues


def _validate_backup_record(
    record: Mapping,
    *,
    path: str,
    restore_point_id: str,
    issues: list[RestoreDrillIssue],
    require_relative_path: bool,
) -> None:
    backup_point_id = _text(record.get('backup_point_id'))
    if not backup_point_id:
        issues.append(_issue('missing_backup_point', f'{path}.backup_point_id', 'backup_point_id is required'))
    elif restore_point_id and backup_point_id != restore_point_id:
        issues.append(
            _issue(
                'backup_point_mismatch',
                f'{path}.backup_point_id',
                'backup_point_id must match restore_point_id',
            )
        )
    if not _text(record.get('created_at')):
        issues.append(_issue('missing_created_at', f'{path}.created_at', 'created_at is required'))
    sha256 = _text(record.get('sha256'))
    if not sha256:
        issues.append(_issue('missing_sha256', f'{path}.sha256', 'sha256 is required'))
    elif not is_sha256_hex(sha256):
        issues.append(_issue('invalid_sha256', f'{path}.sha256', 'sha256 must be a 64-character lowercase hex digest'))
    if require_relative_path:
        _validate_relative_path(record.get('relative_path'), f'{path}.relative_path', issues)


def _validate_restored_records(
    value,
    *,
    record_type: str,
    issues: list[RestoreDrillIssue],
) -> None:
    path = f'{record_type}s'
    if not isinstance(value, list):
        issues.append(_issue('missing_records', path, f'{path} must be a list'))
        return
    if not value:
        issues.append(_issue('empty_records', path, f'{path} must not be empty'))
        return
    for index, raw in enumerate(value):
        item_path = f'{path}[{index}]'
        record = _mapping(raw)
        if not record:
            issues.append(_issue('invalid_record', item_path, 'record must be an object'))
            continue
        _validate_relative_path(record.get('relative_path'), f'{item_path}.relative_path', issues)
        sha256 = _text(record.get('sha256'))
        if not sha256:
            issues.append(_issue('missing_sha256', f'{item_path}.sha256', 'sha256 is required'))
        elif not is_sha256_hex(sha256):
            issues.append(_issue('invalid_sha256', f'{item_path}.sha256', 'sha256 must be a 64-character lowercase hex digest'))
        if not _text(record.get('storage_backend')):
            issues.append(_issue('missing_storage_backend', f'{item_path}.storage_backend', 'storage_backend is required'))
        if record.get('restored') is not True:
            issues.append(_issue('not_restored', f'{item_path}.restored', 'restored must be true'))
        if record.get('exists_after_restore') is not True:
            issues.append(
                _issue(
                    'missing_after_restore',
                    f'{item_path}.exists_after_restore',
                    'exists_after_restore must be true',
                )
            )


def _validate_relative_path(value, path: str, issues: list[RestoreDrillIssue]) -> None:
    try:
        normalize_relative_path(str(value or ''))
    except ValueError as exc:
        issues.append(_issue('invalid_relative_path', path, str(exc)))


def _mapping(value) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _issue(code: str, path: str, message: str) -> RestoreDrillIssue:
    return RestoreDrillIssue(code=code, path=path, message=message)
