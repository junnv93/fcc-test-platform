"""Platform download grant and retention execution planning policies.

This module creates metadata plans only. It does not read files, sign URLs,
delete files, move archives, call networks, or connect to a database.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping

from fcc_test_contracts.common.access_policy import API_PERMISSION_ADMIN
from fcc_test_platform.artifact_storage import RetentionCandidate, normalize_relative_path


__all__ = [
    'DOWNLOAD_PERMISSION_BY_RECORD_TYPE',
    'DownloadGrant',
    'DownloadGrantPolicy',
    'RetentionExecutionPlan',
    'build_download_grant',
    'build_retention_execution_plan',
]


DOWNLOAD_PERMISSION_BY_RECORD_TYPE = {
    'artifact': 'headless:read',
    'report_output': 'report_automation:read',
}

_ALLOWED_DISPOSITIONS = frozenset({'inline', 'attachment'})


@dataclass(frozen=True)
class DownloadGrantPolicy:
    max_ttl_seconds: int = 900
    default_ttl_seconds: int = 300

    def __post_init__(self) -> None:
        if self.max_ttl_seconds <= 0:
            raise ValueError('max_ttl_seconds must be positive')
        if self.default_ttl_seconds <= 0:
            raise ValueError('default_ttl_seconds must be positive')
        if self.default_ttl_seconds > self.max_ttl_seconds:
            raise ValueError('default_ttl_seconds must be <= max_ttl_seconds')


@dataclass(frozen=True)
class DownloadGrant:
    record_type: str
    relative_path: str
    storage_backend: str
    permission: str
    disposition: str
    expires_at: datetime
    metadata: dict

    def to_dict(self) -> dict:
        return {
            'record_type': self.record_type,
            'relative_path': self.relative_path,
            'storage_backend': self.storage_backend,
            'permission': self.permission,
            'disposition': self.disposition,
            'expires_at': self.expires_at.isoformat().replace('+00:00', 'Z'),
            'metadata': dict(self.metadata),
        }


@dataclass(frozen=True)
class RetentionExecutionPlan:
    dry_run: bool
    approved_by: str
    approved_at: datetime
    candidates: tuple[dict, ...]
    reason: str

    def to_dict(self) -> dict:
        return {
            'dry_run': self.dry_run,
            'approved_by': self.approved_by,
            'approved_at': self.approved_at.isoformat().replace('+00:00', 'Z'),
            'candidates': [dict(candidate) for candidate in self.candidates],
            'reason': self.reason,
        }


def build_download_grant(
    *,
    record_type: str,
    record: Mapping,
    principal_permissions: Iterable[str],
    now: datetime,
    policy: DownloadGrantPolicy | None = None,
    ttl_seconds: int | None = None,
    disposition: str = 'attachment',
) -> DownloadGrant:
    """Return a download grant metadata record after policy checks."""

    grant_policy = policy or DownloadGrantPolicy()
    normalized_type = _record_type(record_type)
    permission = DOWNLOAD_PERMISSION_BY_RECORD_TYPE[normalized_type]
    _require_permission(permission, principal_permissions)
    relative_path = normalize_relative_path(str(record.get('relative_path') or ''))
    storage_backend = _required_text(record, 'storage_backend')
    if record.get('exists') is not True:
        raise ValueError('record must exist before a download grant can be issued')
    normalized_disposition = _disposition(disposition)
    ttl = ttl_seconds if ttl_seconds is not None else grant_policy.default_ttl_seconds
    if ttl <= 0:
        raise ValueError('ttl_seconds must be positive')
    if ttl > grant_policy.max_ttl_seconds:
        raise ValueError('ttl_seconds must be <= max_ttl_seconds')

    current = _aware_utc(now)
    return DownloadGrant(
        record_type=normalized_type,
        relative_path=relative_path,
        storage_backend=storage_backend,
        permission=permission,
        disposition=normalized_disposition,
        expires_at=current + timedelta(seconds=ttl),
        metadata={
            'record_id': _optional_text(record.get('id')),
            'sha256': _optional_text(record.get('sha256')),
            'byte_size': _optional_int(record.get('byte_size')),
            'created_at': _optional_text(record.get('created_at')),
        },
    )


def build_retention_execution_plan(
    *,
    candidates: Iterable[RetentionCandidate | Mapping],
    approved_by: str,
    approved_at: datetime,
    reason: str,
    dry_run: bool = True,
) -> RetentionExecutionPlan:
    """Return a retention execution plan. This never deletes or moves files."""

    approver = str(approved_by or '').strip()
    if not approver:
        raise ValueError('approved_by is required')
    text_reason = str(reason or '').strip()
    if not text_reason:
        raise ValueError('reason is required')

    rows = tuple(_candidate_record(candidate) for candidate in candidates)
    if not rows:
        raise ValueError('at least one retention candidate is required')

    return RetentionExecutionPlan(
        dry_run=bool(dry_run),
        approved_by=approver,
        approved_at=_aware_utc(approved_at),
        candidates=rows,
        reason=text_reason,
    )


def _candidate_record(candidate: RetentionCandidate | Mapping) -> dict:
    row = candidate.to_dict() if isinstance(candidate, RetentionCandidate) else dict(candidate)
    record_type = _record_type(row.get('record_type'))
    relative_path = normalize_relative_path(str(row.get('relative_path') or ''))
    storage_backend = _required_text(row, 'storage_backend')
    reason = _required_text(row, 'reason')
    return {
        'record_type': record_type,
        'relative_path': relative_path,
        'storage_backend': storage_backend,
        'reason': reason,
        'age_days': _optional_int(row.get('age_days')),
    }


def _record_type(value) -> str:
    text = str(value or '').strip()
    if text not in DOWNLOAD_PERMISSION_BY_RECORD_TYPE:
        allowed = ', '.join(sorted(DOWNLOAD_PERMISSION_BY_RECORD_TYPE))
        raise ValueError(f'record_type must be one of: {allowed}')
    return text


def _require_permission(permission: str, principal_permissions: Iterable[str]) -> None:
    permissions = {str(item).strip() for item in principal_permissions if str(item).strip()}
    if API_PERMISSION_ADMIN in permissions or permission in permissions:
        return
    raise PermissionError(f'missing required permission: {permission}')


def _disposition(value: str) -> str:
    text = str(value or '').strip().lower()
    if text not in _ALLOWED_DISPOSITIONS:
        raise ValueError('disposition must be inline or attachment')
    return text


def _required_text(mapping: Mapping, key: str) -> str:
    text = _optional_text(mapping.get(key))
    if not text:
        raise ValueError(f'{key} is required')
    return text


def _optional_text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _optional_int(value) -> int | None:
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
