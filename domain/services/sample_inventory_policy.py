"""Pure policy for web sample CRUD, revisions, filters, and snapshots."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Any, Iterable, Mapping, Optional, Sequence

from fcc_test_kernel.domain.models.sample_inventory import (
    INTAKE_FIELDS,
    REVISION_SNAPSHOT_FIELDS,
    SAMPLE_EDITABLE_FIELDS,
    SNAPSHOT_SCHEMA_VERSION,
    Sample,
    SampleIntake,
    SampleRevision,
    SampleRevisionEvent,
    SampleSnapshot,
    SampleStatus,
)


class SampleInventoryPolicyError(ValueError):
    """Base error for invalid inventory input."""


class SampleExpectedVersionConflict(SampleInventoryPolicyError):
    """The caller attempted to overwrite a newer current projection."""


class SampleInvalidTransition(SampleInventoryPolicyError):
    """The requested sample status transition is not valid."""


class SampleUnknownField(SampleInventoryPolicyError):
    """A PATCH contains a field outside the web sample contract."""


class SampleInvalidFilter(SampleInventoryPolicyError):
    """A list/export filter is malformed."""


def utc_now_iso(value: Optional[datetime] = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def normalize_status(value: Any) -> SampleStatus:
    if isinstance(value, SampleStatus):
        return value
    try:
        return SampleStatus(str(value))
    except (TypeError, ValueError) as exc:
        raise SampleInventoryPolicyError(
            f'unsupported sample status: {value!r}'
        ) from exc


def validate_expected_version(expected_version: Any) -> int:
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise SampleInventoryPolicyError('expected_version must be an integer')
    if expected_version < 1:
        raise SampleInventoryPolicyError('expected_version must be positive')
    return expected_version


def assert_expected_version(current_version: int, expected_version: Any) -> None:
    expected = validate_expected_version(expected_version)
    if current_version != expected:
        raise SampleExpectedVersionConflict(
            f'sample version conflict: expected {expected}, current {current_version}'
        )


def validate_patch(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise SampleInventoryPolicyError('sample patch must be an object')
    unknown = sorted(set(payload) - set(SAMPLE_EDITABLE_FIELDS) - {'latest_intake'})
    if unknown:
        raise SampleUnknownField(f'unsupported sample fields: {", ".join(unknown)}')
    result = {field: payload[field] for field in SAMPLE_EDITABLE_FIELDS if field in payload}
    if 'latest_intake' in payload:
        intake = payload['latest_intake']
        if not isinstance(intake, Mapping):
            raise SampleInventoryPolicyError('latest_intake must be an object')
        unknown_intake = sorted(set(intake) - set(INTAKE_FIELDS))
        if unknown_intake:
            raise SampleUnknownField(
                f'unsupported intake fields: {", ".join(unknown_intake)}'
            )
        result['latest_intake'] = {
            field: intake[field] for field in INTAKE_FIELDS if field in intake
        }
    if not result:
        raise SampleInventoryPolicyError('sample patch must change at least one field')
    return result


def sample_projection(sample: Sample | Mapping[str, Any]) -> dict[str, Any]:
    """Return the complete revision payload.

    ``id`` is not a mutable revision field, but it is retained as an identity
    witness so the canonical snapshot builder cannot accidentally manufacture
    a snapshot with a null ``sample_id`` when it receives a domain ``Sample``.
    """
    value = sample.as_dict() if isinstance(sample, Sample) else dict(sample)
    latest = value.get('latest_intake')
    if latest is not None:
        latest = {field: latest.get(field) for field in INTAKE_FIELDS}
    return {
        'id': value.get('id', value.get('sample_id')),
    } | {
        field: value.get(field) for field in SAMPLE_EDITABLE_FIELDS
    } | {
        'status': normalize_status(value.get('status', SampleStatus.ACTIVE)).value,
        'row_version': int(value.get('row_version', 1)),
        'latest_intake': latest,
    }


def apply_patch(
    current: Sample | Mapping[str, Any],
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Apply a validated patch and return complete post-state + exact changes."""
    patch = validate_patch(payload)
    before = sample_projection(current)
    after = deepcopy(before)
    for field, value in patch.items():
        if field == 'latest_intake':
            existing = dict(after.get('latest_intake') or {})
            existing.update(value)
            after[field] = {name: existing.get(name) for name in INTAKE_FIELDS}
        else:
            after[field] = value
    changed: list[str] = []
    for field in REVISION_SNAPSHOT_FIELDS:
        if before.get(field) != after.get(field):
            changed.append(field)
    if not changed:
        raise SampleInventoryPolicyError('sample patch does not change any value')
    return after, tuple(changed)


def transition_status(current: Any, requested: Any) -> SampleStatus:
    before = normalize_status(current)
    after = normalize_status(requested)
    if before is after:
        return after
    if (before, after) not in {
        (SampleStatus.ACTIVE, SampleStatus.DELETED),
        (SampleStatus.DELETED, SampleStatus.ACTIVE),
    }:
        raise SampleInvalidTransition(f'{before.value} -> {after.value} is not allowed')
    return after


def event_for_change(
    *, created: bool = False, status_before: Any = None,
    status_after: Any = None,
) -> SampleRevisionEvent:
    if created:
        return SampleRevisionEvent.CREATED
    if status_before is not None and status_after is not None:
        before = normalize_status(status_before)
        after = normalize_status(status_after)
        if before is not after:
            return (
                SampleRevisionEvent.RESTORED
                if after is SampleStatus.ACTIVE
                else SampleRevisionEvent.STATUS_CHANGED
            )
    return SampleRevisionEvent.UPDATED


def next_revision_number(revisions: Iterable[SampleRevision | Mapping[str, Any]]) -> int:
    numbers = []
    for revision in revisions:
        value = revision.revision_number if isinstance(revision, SampleRevision) else revision.get('revision_number', 0)
        numbers.append(int(value))
    return max(numbers, default=0) + 1


def _identity_text(value: Any) -> Any:
    """Return an identity value as text, leaving a missing value falsy.

    Database drivers return identity columns as native objects (psycopg gives
    `uuid.UUID`). A snapshot is a JSON document, so identities cross this
    boundary as strings. `None`/`''` are returned unchanged so the caller's
    "requires project_id" check still fires instead of seeing `'None'`.
    """
    if value is None or value == '':
        return value
    return value if isinstance(value, str) else str(value)


def canonical_snapshot(
    *,
    project: Mapping[str, Any],
    sample: Sample | Mapping[str, Any],
    latest_intake: Optional[SampleIntake | Mapping[str, Any]] = None,
    sample_revision: int,
    captured_at: str,
) -> SampleSnapshot:
    """Build the only server-owned session snapshot shape."""
    sample_value = sample_projection(sample)
    sample_id = sample_value.get('id')
    if not sample_id:
        raise SampleInventoryPolicyError('canonical snapshot requires sample_id')
    intake_value = latest_intake
    if intake_value is None:
        intake_value = sample_value.get('latest_intake')
    elif isinstance(intake_value, SampleIntake):
        intake_value = intake_value.as_dict()
    if intake_value is not None:
        intake_value = {
            field: intake_value.get(field) for field in INTAKE_FIELDS
        }
    project_value = {
        # ⚠️ `str(...)`, exactly as `sample_id` below. The snapshot is DEFINED to
        # be JSON (`snapshot_json` dumps it, and the read path immediately does
        # `json.loads(snapshot_json(...))`), and psycopg hands back `projects."id"`
        # as a `uuid.UUID` object. Without the coercion every writer of a snapshot
        # dies in `json.dumps` with `Object of type UUID is not JSON serializable`
        # — measured 2026-09-01 on two independent paths (sample creation → 503,
        # chamber measurement start → 500). The rule already existed here; it had
        # simply been applied to one of the two identities, so the sample axis was
        # green while the project axis could not work at all.
        'project_id': _identity_text(project.get('project_id', project.get('id'))),
        'project_code': project.get('project_code'),
        'model_name': project.get('model_name'),
        'management_number': project.get('management_number'),
    }
    if not project_value['project_id']:
        raise SampleInventoryPolicyError('canonical snapshot requires project_id')
    return SampleSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        captured_at=captured_at,
        project=project_value,
        sample={
            'sample_id': str(sample_id),
            **{field: sample_value.get(field) for field in SAMPLE_EDITABLE_FIELDS},
            'status': sample_value['status'],
        },
        latest_intake=intake_value,
        sample_revision=int(sample_revision),
        row_version=int(sample_value.get('row_version', 1)),
    )


def snapshot_measurement_identity(
    snapshot: Optional[SampleSnapshot | Mapping[str, Any]],
) -> tuple[str, str]:
    """스냅샷이 말하는 측정 대상 정체성 ``(Model Number, Sample No)``.

    스냅샷의 **형태를 아는 자리는 이 모듈 하나**여야 한다 — ``canonical_snapshot`` 이
    그것을 만들었으므로 그것을 읽는 규칙도 여기 산다. 측정 러너가 ``snapshot['project']
    ['model_name']`` 을 직접 파면, 스냅샷 스키마가 바뀌는 날 그 자리가 조용히 빈
    문자열을 돌려준다(그리고 빈 정체성은 서로 다른 두 시료의 결과 DB 를 한 파일로
    합친다 — ``MeasurementTargetIdentity`` 가 존재하는 바로 그 결함).

    - ``Model Number`` ← ``project.model_name`` (프로젝트의 device model).
    - ``Sample No`` ← ``sample.sample_number``, 비어 있으면 ``sample.sample_code``.
      중앙 스키마는 번호 없는 시료를 허용하는데, 번호가 비면 정체성의 절반이 사라져
      같은 모델의 두 시료가 한 DB 로 붕괴한다. ``sample_code`` 는 시험원이 화면에서
      보는 라벨이라 그 자리를 메우는 유일하게 정직한 값이다.

    모르는 값은 ``''`` 다 — ``None`` 이 아니다. 호출자는 이것을 Save Data 칸에 넣고,
    그 칸의 "모름"은 빈 문자열이기 때문이다. 순수 — stdlib only, I/O 0.
    """
    if snapshot is None:
        return ('', '')
    value = snapshot.as_dict() if isinstance(snapshot, SampleSnapshot) else snapshot
    project = value.get('project') or {}
    sample = value.get('sample') or {}
    model_number = _snapshot_text(project.get('model_name'))
    sample_no = (
        _snapshot_text(sample.get('sample_number'))
        or _snapshot_text(sample.get('sample_code'))
    )
    return (model_number, sample_no)


def _snapshot_text(value: Any) -> str:
    """스냅샷 칸 → 다듬은 문자열. ``None``/공백은 ``''``."""
    return str(value or '').strip()


def snapshot_json(snapshot: SampleSnapshot | Mapping[str, Any]) -> str:
    value = snapshot.as_dict() if isinstance(snapshot, SampleSnapshot) else snapshot
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def choose_as_of_revision(
    revisions: Sequence[SampleRevision | Mapping[str, Any]],
    *,
    as_of: Optional[str],
) -> list[SampleRevision | Mapping[str, Any]]:
    """Choose one inclusive latest revision per sample without OFFSET semantics."""
    cutoff = _parse_timestamp(as_of) if as_of else None
    chosen: dict[str, SampleRevision | Mapping[str, Any]] = {}
    for revision in revisions:
        sample_id = revision.sample_id if isinstance(revision, SampleRevision) else str(revision['sample_id'])
        occurred = revision.occurred_at if isinstance(revision, SampleRevision) else revision['occurred_at']
        if cutoff is not None and _parse_timestamp(str(occurred)) > cutoff:
            continue
        current = chosen.get(sample_id)
        if current is None or _revision_order(revision) > _revision_order(current):
            chosen[sample_id] = revision
    return sorted(chosen.values(), key=_revision_order)


def filter_revision_snapshots(
    revisions: Sequence[SampleRevision | Mapping[str, Any]],
    *,
    project_id: Optional[str] = None,
    team: Optional[str] = None,
    status: Optional[Any] = None,
    as_of: Optional[str] = None,
) -> list[SampleRevision | Mapping[str, Any]]:
    selected = choose_as_of_revision(revisions, as_of=as_of)
    wanted_status = (
        None if status in (None, '', 'all') else normalize_status(status)
    )
    team_key = team.strip().casefold() if isinstance(team, str) and team.strip() else None
    result = []
    for revision in selected:
        project = revision.project_id if isinstance(revision, SampleRevision) else str(revision.get('project_id', ''))
        snapshot = revision.snapshot if isinstance(revision, SampleRevision) else revision.get('snapshot', {})
        if project_id and project != project_id:
            continue
        if wanted_status is not None and snapshot.get('status') != wanted_status.value:
            continue
        if team_key is not None and str(snapshot.get('assigned_team') or '').strip().casefold() != team_key:
            continue
        result.append(revision)
    return result


def validate_inventory_filter(*, limit: Any, status: Any = None) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise SampleInvalidFilter('limit must be an integer between 1 and 500')
    if status not in (None, '', 'all'):
        normalize_status(status)
    return limit


def _parse_timestamp(value: Optional[str]) -> datetime:
    if not value:
        raise SampleInvalidFilter('as_of must be an ISO-8601 UTC timestamp')
    text = str(value).strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SampleInvalidFilter('as_of must be an ISO-8601 UTC timestamp') from exc
    if parsed.tzinfo is None:
        raise SampleInvalidFilter('as_of must include a timezone')
    return parsed.astimezone(timezone.utc)


def _revision_order(revision: SampleRevision | Mapping[str, Any]) -> tuple[datetime, int, str]:
    occurred = revision.occurred_at if isinstance(revision, SampleRevision) else revision['occurred_at']
    number = revision.revision_number if isinstance(revision, SampleRevision) else int(revision.get('revision_number', 0))
    identifier = revision.id if isinstance(revision, SampleRevision) else str(revision.get('id', ''))
    return _parse_timestamp(str(occurred)), int(number), identifier


__all__ = [
    'SampleExpectedVersionConflict',
    'SampleInvalidFilter',
    'SampleInvalidTransition',
    'SampleInventoryPolicyError',
    'SampleUnknownField',
    'apply_patch',
    'assert_expected_version',
    'canonical_snapshot',
    'choose_as_of_revision',
    'event_for_change',
    'filter_revision_snapshots',
    'next_revision_number',
    'normalize_status',
    'sample_projection',
    'snapshot_json',
    'snapshot_measurement_identity',
    'transition_status',
    'utc_now_iso',
    'validate_expected_version',
    'validate_inventory_filter',
    'validate_patch',
]
