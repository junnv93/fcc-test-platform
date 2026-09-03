"""Application service for the web sample inventory."""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import json
from typing import Any, Callable, Mapping, Optional

from fcc_test_kernel.domain.models.sample_inventory import SampleStatus
from domain.ports.output.central_sample_inventory_read_port import (
    CentralSampleInventoryReadPort,
)
from domain.ports.output.central_sample_inventory_write_port import (
    CentralSampleInventoryNotFoundError,
    CentralSampleInventoryWritePort,
)
from domain.services.sample_inventory_policy import (
    SampleExpectedVersionConflict,
    SampleInvalidFilter,
    assert_expected_version,
    canonical_snapshot,
    normalize_status,
    snapshot_json,
    utc_now_iso,
    validate_expected_version,
    validate_inventory_filter,
)


class SampleInventoryNotFoundError(LookupError):
    """Requested sample/project is unknown."""


class SampleInventoryConflictError(ValueError):
    """Optimistic concurrency conflict."""

    PROBLEM_PARAM_FIELDS = ('resource', 'expected_version')


class SampleInventoryHardDeleteForbiddenError(PermissionError):
    """Hard delete is a distinct system-admin operation."""


class CentralSampleInventoryService:
    def __init__(self, read_port: CentralSampleInventoryReadPort,
                 write_port: CentralSampleInventoryWritePort,
                 *, clock: Optional[Callable[[], str]] = None) -> None:
        self._read = read_port
        self._write = write_port
        self._clock = clock or utc_now_iso

    def list_samples(
        self, *, project_id: Optional[str] = None, team: Optional[str] = None,
        status: Optional[str] = None, as_of: Optional[str] = None,
        after: Optional[str] = None, limit: int = 100,
        include_deleted: bool = False,
    ) -> dict:
        validate_inventory_filter(limit=limit, status=status)
        if status in (None, '') and include_deleted:
            # Explicit include_deleted widens the default active-only query. A
            # caller that supplied status remains authoritative.
            effective_status = None
        elif status == 'all':
            effective_status = None
        else:
            effective_status = normalize_status(status or SampleStatus.ACTIVE).value
        cursor_arity = 4 if as_of else 2
        decoded_after = _decode_cursor(after, arity=cursor_arity) if after else None
        effective_include_deleted = bool(include_deleted or status == 'all')
        page = self._read.list_samples(
            project_id=project_id, team=_clean(team), status=effective_status,
            as_of=as_of, after=decoded_after, limit=limit,
            include_deleted=effective_include_deleted,
        )
        items = list(page.get('items') or [])
        if as_of:
            items = [_as_of_item(item, as_of=as_of) for item in items]
        return {
            'items': items,
            'next_cursor': _encode_cursor(page.get('next_cursor')),
            'as_of': as_of,
            'filters': {
                'project_id': project_id,
                'team': _clean(team),
                'status': status or ('all' if include_deleted else 'active'),
                'include_deleted': effective_include_deleted,
            },
        }

    def get_sample(self, project_id: str, sample_id: str, *, as_of: Optional[str] = None) -> dict:
        value = self._read.get_sample(project_id, sample_id, as_of=as_of)
        if value is None:
            raise SampleInventoryNotFoundError(f'unknown sample_id {sample_id}')
        if as_of and value.get('snapshot') is not None:
            snapshot = dict(value['snapshot'])
            snapshot.update({
                'id': sample_id,
                'sample_id': sample_id,
                'project_id': project_id,
                'as_of': as_of,
                'revision_number': value.get('revision_number'),
            })
            return snapshot
        return dict(value)

    def list_history(self, project_id: str, sample_id: str, *, after: Optional[str] = None,
                     limit: int = 100) -> dict:
        validate_inventory_filter(limit=limit)
        if self._read.get_sample(project_id, sample_id) is None:
            raise SampleInventoryNotFoundError(f'unknown sample_id {sample_id}')
        page = self._read.list_history(
            project_id, sample_id,
            after=_decode_cursor(after, arity=3) if after else None,
            limit=limit,
        )
        return {
            'items': list(page.get('items') or []),
            'next_cursor': _encode_cursor(page.get('next_cursor')),
        }

    def create_sample(self, project_id: str, payload: Mapping[str, Any], *,
                      actor_subject: str) -> dict:
        try:
            return self._write.create_sample(
                project_id, payload, actor_subject=_actor(actor_subject),
                occurred_at=self._clock(),
            )
        except CentralSampleInventoryNotFoundError as exc:
            raise SampleInventoryNotFoundError(str(exc)) from exc

    def patch_sample(self, project_id: str, sample_id: str, payload: Mapping[str, Any], *,
                     expected_version: int, actor_subject: str) -> dict:
        expected_version = validate_expected_version(expected_version)
        try:
            return self._write.patch_sample(
                project_id, sample_id, payload, expected_version=expected_version,
                actor_subject=_actor(actor_subject), occurred_at=self._clock(),
            )
        except CentralSampleInventoryNotFoundError as exc:
            raise SampleInventoryNotFoundError(str(exc)) from exc
        except SampleExpectedVersionConflict as exc:
            raise SampleInventoryConflictError(str(exc)) from exc
        except ValueError as exc:
            if 'version conflict' in str(exc).lower():
                raise SampleInventoryConflictError(str(exc)) from exc
            raise

    def change_status(self, project_id: str, sample_id: str, status: str, *,
                      expected_version: int, actor_subject: str) -> dict:
        normalize_status(status)
        expected_version = validate_expected_version(expected_version)
        try:
            return self._write.change_status(
                project_id, sample_id, status, expected_version=expected_version,
                actor_subject=_actor(actor_subject), occurred_at=self._clock(),
            )
        except CentralSampleInventoryNotFoundError as exc:
            raise SampleInventoryNotFoundError(str(exc)) from exc
        except ValueError as exc:
            if 'version conflict' in str(exc).lower():
                raise SampleInventoryConflictError(str(exc)) from exc
            raise

    def soft_delete(self, project_id: str, sample_id: str, *, expected_version: int,
                    actor_subject: str) -> dict:
        return self.change_status(
            project_id, sample_id, SampleStatus.DELETED.value,
            expected_version=expected_version, actor_subject=actor_subject,
        )

    def restore(self, project_id: str, sample_id: str, *, expected_version: int,
                actor_subject: str) -> dict:
        return self.change_status(
            project_id, sample_id, SampleStatus.ACTIVE.value,
            expected_version=expected_version, actor_subject=actor_subject,
        )

    def hard_delete(self, sample_id: str, *, actor_subject: str) -> dict:
        try:
            return self._write.hard_delete(
                sample_id, actor_subject=_actor(actor_subject), occurred_at=self._clock(),
            )
        except CentralSampleInventoryNotFoundError as exc:
            raise SampleInventoryNotFoundError(str(exc)) from exc

    def build_measurement_snapshot(
        self, project_id: str, sample_id: str, *, published_plan_id: Optional[str] = None,
    ) -> dict:
        """Build a complete snapshot before the node is asked to touch hardware."""
        plan_id = _clean(published_plan_id)
        inputs = self._read.get_measurement_snapshot_inputs(
            project_id, sample_id, published_plan_id=plan_id,
        )
        if plan_id:
            plan_project_id = inputs.get('plan_project_id')
            if inputs.get('plan_project_count', 0) != 1 or plan_project_id is None:
                raise ValueError('published_plan_id is unknown')
            if plan_project_id != project_id:
                raise ValueError('published_plan_id does not belong to project_id')
        sample = inputs.get('sample')
        if sample is None:
            raise SampleInventoryNotFoundError(f'unknown sample_id {sample_id}')
        if sample.get('status') != SampleStatus.ACTIVE.value:
            raise ValueError('measurement requires an active sample')
        project = inputs.get('project')
        if project is None:
            raise SampleInventoryNotFoundError(f'unknown project_id {project_id}')
        project_status = project.get('project_status', project.get('status'))
        if project_status not in (None, 'active'):
            raise ValueError('measurement requires an active project')
        revision_number = int(
            inputs.get('sample_revision')
            or sample.get('row_version')
            or 1
        )
        snapshot = canonical_snapshot(
            project=project, sample=sample,
            latest_intake=sample.get('latest_intake'),
            sample_revision=revision_number,
            captured_at=self._clock(),
        )
        # Force serialization once at the ownership boundary so downstream
        # adapters receive a deterministic JSON-compatible object and cannot
        # reinterpret dates/nullable values.
        return json.loads(snapshot_json(snapshot))


def _actor(value: Optional[str]) -> str:
    cleaned = str(value or '').strip()
    if not cleaned or cleaned.casefold() == 'anonymous':
        # Audit attribution is a security boundary. A missing principal must
        # fail before a write port can open a transaction.
        raise PermissionError('authenticated actor subject is required')
    return cleaned


def _clean(value: Optional[str]) -> Optional[str]:
    cleaned = str(value or '').strip()
    return cleaned or None


def _encode_cursor(value: Any) -> Optional[str]:
    if value in (None, ''):
        return None
    if isinstance(value, str):
        value = [value]
    value = [_cursor_value(item) for item in value]
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _cursor_value(value: Any) -> Any:
    if hasattr(value, 'isoformat') and callable(value.isoformat):
        return value.isoformat()
    return value


def _decode_cursor(value: Optional[str], *, arity: Optional[int] = None):
    if value is None:
        return None
    try:
        padded = value + '=' * (-len(value) % 4)
        result = json.loads(base64.urlsafe_b64decode(padded).decode('utf-8'))
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
        raise SampleInvalidFilter('invalid sample inventory cursor') from exc
    if not isinstance(result, list):
        raise SampleInvalidFilter('invalid sample inventory cursor')
    if arity is not None and len(result) != arity:
        raise SampleInvalidFilter(
            f'invalid sample inventory cursor: expected {arity} values'
        )
    if any(item in (None, '') or isinstance(item, (dict, list, bool)) for item in result):
        raise SampleInvalidFilter('invalid sample inventory cursor')
    return tuple(result)


def _as_of_item(value: Mapping[str, Any], *, as_of: str) -> dict:
    """Flatten the revision envelope into the same item shape as current reads."""
    snapshot = value.get('snapshot') or {}
    sample = dict(snapshot.get('sample') or {})
    project = dict(snapshot.get('project') or {})
    sample_id = sample.get('sample_id') or value.get('sample_id')
    result = {
        'sample_id': sample_id,
        'project_id': project.get('project_id') or value.get('project_id'),
        **{key: sample.get(key) for key in (
            'sample_number', 'sample_code', 'test_category', 'label_number',
            'smsn', 'serial_number', 'intake_cert', 'assigned_team', 'sender',
            'receiver', 'received_date', 'released_date', 'note', 'status',
        )},
        'row_version': snapshot.get('row_version', 1),
        'latest_intake': snapshot.get('latest_intake'),
        'intake_count': 0,
        'created_at': None,
        'updated_at': None,
        'deleted_at': None,
        'deleted_by': None,
        'as_of': as_of,
        'revision_number': value.get('revision_number'),
    }
    return result


__all__ = [
    'CentralSampleInventoryService',
    'SampleInventoryConflictError',
    'SampleInventoryHardDeleteForbiddenError',
    'SampleInventoryNotFoundError',
]
