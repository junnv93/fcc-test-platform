"""Platform artifact storage address and retention policy helpers.

The central DB stores metadata only. This module defines deterministic relative
storage addresses and retention planning records without touching file bytes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Iterable, Mapping, Optional


__all__ = [
    'ArtifactStorageKey',
    'RetentionCandidate',
    'RetentionPolicy',
    'build_artifact_storage_key',
    'build_report_output_storage_key',
    'normalize_relative_path',
    'plan_retention_candidates',
]


_SEGMENT_RE = re.compile(r'[^A-Za-z0-9._=-]+')


@dataclass(frozen=True)
class ArtifactStorageKey:
    """Stable metadata address under a configured artifact root."""

    relative_path: str
    storage_backend: str = 'filesystem'
    metadata: dict = field(default_factory=dict)

    def to_record(self) -> dict:
        return {
            'relative_path': self.relative_path,
            'storage_backend': self.storage_backend,
            'metadata': dict(self.metadata),
        }


@dataclass(frozen=True)
class RetentionPolicy:
    retain_artifacts_days: int
    retain_report_outputs_days: int

    def __post_init__(self) -> None:
        if self.retain_artifacts_days <= 0:
            raise ValueError('retain_artifacts_days must be positive')
        if self.retain_report_outputs_days <= 0:
            raise ValueError('retain_report_outputs_days must be positive')


@dataclass(frozen=True)
class RetentionCandidate:
    record_type: str
    relative_path: str
    storage_backend: str
    reason: str
    age_days: int
    record: dict

    def to_dict(self) -> dict:
        return {
            'record_type': self.record_type,
            'relative_path': self.relative_path,
            'storage_backend': self.storage_backend,
            'reason': self.reason,
            'age_days': self.age_days,
            'record': dict(self.record),
        }


def normalize_relative_path(value: str) -> str:
    """Return a normalized POSIX relative path or raise `ValueError`."""

    text = str(value or '').strip().replace('\\', '/')
    if not text:
        raise ValueError('relative_path is required')
    if text.startswith('//') or text.startswith('/'):
        raise ValueError('relative_path must not be absolute')
    if re.match(r'^[A-Za-z]:', text):
        raise ValueError('relative_path must not include a drive prefix')

    parts = [part for part in text.split('/') if part not in {'', '.'}]
    if not parts:
        raise ValueError('relative_path is required')
    if any(part == '..' for part in parts):
        raise ValueError('relative_path must not contain traversal')
    return '/'.join(parts)


def build_artifact_storage_key(
    *,
    project_id: str,
    session_id: str,
    result_id: str,
    artifact_type: str,
    file_name: str,
    storage_backend: str = 'filesystem',
) -> ArtifactStorageKey:
    segments = [
        'projects',
        _segment(project_id, 'project_id'),
        'sessions',
        _segment(session_id, 'session_id'),
        'results',
        _segment(result_id, 'result_id'),
        _segment(artifact_type, 'artifact_type'),
        _file_name(file_name),
    ]
    relative_path = normalize_relative_path('/'.join(segments))
    return ArtifactStorageKey(
        relative_path=relative_path,
        storage_backend=_storage_backend(storage_backend),
        metadata={
            'project_id': str(project_id),
            'session_id': str(session_id),
            'result_id': str(result_id),
            'artifact_type': str(artifact_type),
            'file_name': str(file_name),
        },
    )


def build_report_output_storage_key(
    *,
    project_id: str,
    session_id: str,
    report_run_id: str,
    file_name: str,
    storage_backend: str = 'filesystem',
) -> ArtifactStorageKey:
    segments = [
        'projects',
        _segment(project_id, 'project_id'),
        'sessions',
        _segment(session_id, 'session_id'),
        'reports',
        _segment(report_run_id, 'report_run_id'),
        _file_name(file_name),
    ]
    relative_path = normalize_relative_path('/'.join(segments))
    return ArtifactStorageKey(
        relative_path=relative_path,
        storage_backend=_storage_backend(storage_backend),
        metadata={
            'project_id': str(project_id),
            'session_id': str(session_id),
            'report_run_id': str(report_run_id),
            'file_name': str(file_name),
        },
    )


def plan_retention_candidates(
    *,
    artifacts: Iterable[Mapping] = (),
    report_outputs: Iterable[Mapping] = (),
    policy: RetentionPolicy,
    now: datetime,
) -> list[RetentionCandidate]:
    """Return expired metadata records. This function never deletes files."""

    current = _aware_utc(now)
    candidates: list[RetentionCandidate] = []
    candidates.extend(
        _expired_records(
            records=artifacts,
            record_type='artifact',
            max_age_days=policy.retain_artifacts_days,
            now=current,
        )
    )
    candidates.extend(
        _expired_records(
            records=report_outputs,
            record_type='report_output',
            max_age_days=policy.retain_report_outputs_days,
            now=current,
        )
    )
    return candidates


def _expired_records(
    *,
    records: Iterable[Mapping],
    record_type: str,
    max_age_days: int,
    now: datetime,
) -> list[RetentionCandidate]:
    expired: list[RetentionCandidate] = []
    for record in records:
        row = dict(record)
        relative_path = normalize_relative_path(str(row.get('relative_path') or ''))
        created_at = _parse_datetime(row.get('created_at'))
        if created_at is None:
            continue
        age_days = (now - created_at).days
        if age_days < max_age_days:
            continue
        expired.append(
            RetentionCandidate(
                record_type=record_type,
                relative_path=relative_path,
                storage_backend=_storage_backend(row.get('storage_backend') or 'filesystem'),
                reason=f'{record_type} age {age_days}d >= retention {max_age_days}d',
                age_days=age_days,
                record=row,
            )
        )
    return expired


def _segment(value: str, field_name: str) -> str:
    text = str(value or '').strip()
    if not text:
        raise ValueError(f'{field_name} is required')
    text = _SEGMENT_RE.sub('-', text).strip('.-')
    if not text:
        raise ValueError(f'{field_name} has no safe characters')
    return text


def _file_name(value: str) -> str:
    name = str(value or '').strip().replace('\\', '/').split('/')[-1]
    safe = _segment(name, 'file_name')
    if '.' not in safe:
        raise ValueError('file_name must include an extension')
    return safe


def _storage_backend(value: str) -> str:
    backend = _segment(str(value or 'filesystem'), 'storage_backend')
    return backend.lower()


def _parse_datetime(value) -> Optional[datetime]:
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return _aware_utc(value)
    text = str(value).strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        return _aware_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
