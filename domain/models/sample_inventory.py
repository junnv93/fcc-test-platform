"""Pure value objects for the web-owned sample inventory.

The web inventory deliberately models the current projection, immutable intake
rows, and immutable revision snapshots separately.  No database, spreadsheet,
HTTP, or UI dependency belongs in this module: the same objects are used by
the API service, export policy, and measurement-session snapshot boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional


SNAPSHOT_SCHEMA_VERSION = 'fcc.sample.inventory.snapshot.v1'


class SampleStatus(str, Enum):
    ACTIVE = 'active'
    DELETED = 'deleted'


class SampleRevisionEvent(str, Enum):
    CREATED = 'created'
    UPDATED = 'updated'
    STATUS_CHANGED = 'status_changed'
    RESTORED = 'restored'
    BASELINE = 'baseline'


SAMPLE_EDITABLE_FIELDS: tuple[str, ...] = (
    'sample_number',
    'sample_code',
    'test_category',
    'label_number',
    'smsn',
    'serial_number',
    'intake_cert',
    'assigned_team',
    'sender',
    'receiver',
    'received_date',
    'released_date',
    'note',
)

INTAKE_FIELDS: tuple[str, ...] = (
    'intake_date',
    'bl',
    'ap',
    'cp',
    'csc',
    'rf_cal',
    'hw_rev',
    'note',
    'tech_group',
)

REVISION_SNAPSHOT_FIELDS: tuple[str, ...] = SAMPLE_EDITABLE_FIELDS + (
    'status',
    'row_version',
    'latest_intake',
)


@dataclass(frozen=True)
class SampleIntake:
    """One immutable intake observation."""

    id: Optional[str] = None
    sample_id: Optional[str] = None
    intake_date: Optional[str] = None
    bl: Optional[str] = None
    ap: Optional[str] = None
    cp: Optional[str] = None
    csc: Optional[str] = None
    rf_cal: Optional[str] = None
    hw_rev: Optional[str] = None
    note: Optional[str] = None
    tech_group: Optional[str] = None
    created_at: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'sample_id': self.sample_id,
            'intake_date': self.intake_date,
            'bl': self.bl,
            'ap': self.ap,
            'cp': self.cp,
            'csc': self.csc,
            'rf_cal': self.rf_cal,
            'hw_rev': self.hw_rev,
            'note': self.note,
            'tech_group': self.tech_group,
            'created_at': self.created_at,
        }


@dataclass(frozen=True)
class Sample:
    """Current sample projection returned by the central read service."""

    id: str
    project_id: str
    sample_number: Optional[str] = None
    sample_code: Optional[str] = None
    test_category: Optional[str] = None
    label_number: Optional[str] = None
    smsn: Optional[str] = None
    serial_number: Optional[str] = None
    intake_cert: Optional[str] = None
    assigned_team: Optional[str] = None
    sender: Optional[str] = None
    receiver: Optional[str] = None
    received_date: Optional[str] = None
    released_date: Optional[str] = None
    note: Optional[str] = None
    status: SampleStatus = SampleStatus.ACTIVE
    row_version: int = 1
    latest_intake: Optional[SampleIntake] = None
    intake_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def as_dict(self, *, include_intake: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            'id': self.id,
            'project_id': self.project_id,
            'sample_number': self.sample_number,
            'sample_code': self.sample_code,
            'test_category': self.test_category,
            'label_number': self.label_number,
            'smsn': self.smsn,
            'serial_number': self.serial_number,
            'intake_cert': self.intake_cert,
            'assigned_team': self.assigned_team,
            'sender': self.sender,
            'receiver': self.receiver,
            'received_date': self.received_date,
            'released_date': self.released_date,
            'note': self.note,
            'status': self.status.value,
            'row_version': self.row_version,
            'intake_count': self.intake_count,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }
        if include_intake:
            result['latest_intake'] = (
                self.latest_intake.as_dict() if self.latest_intake else None
            )
        return result


@dataclass(frozen=True)
class SampleRevision:
    """Append-only full post-mutation sample snapshot."""

    id: str
    sample_id: str
    project_id: str
    revision_number: int
    event_type: SampleRevisionEvent
    snapshot: Mapping[str, Any]
    changed_fields: tuple[str, ...] = ()
    actor_subject: str = ''
    occurred_at: str = ''
    created_at: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'sample_id': self.sample_id,
            'project_id': self.project_id,
            'revision_number': self.revision_number,
            'event_type': self.event_type.value,
            'snapshot': dict(self.snapshot),
            'changed_fields': list(self.changed_fields),
            'actor_subject': self.actor_subject,
            'occurred_at': self.occurred_at,
            'created_at': self.created_at,
        }


@dataclass(frozen=True)
class SampleSnapshot:
    """Canonical immutable snapshot carried into a measurement session."""

    schema_version: str
    captured_at: str
    project: Mapping[str, Any]
    sample: Mapping[str, Any]
    latest_intake: Optional[Mapping[str, Any]]
    sample_revision: int
    row_version: int

    def as_dict(self) -> dict[str, Any]:
        return {
            'schema_version': self.schema_version,
            'captured_at': self.captured_at,
            'project': dict(self.project),
            'sample': dict(self.sample),
            'latest_intake': (
                dict(self.latest_intake) if self.latest_intake is not None else None
            ),
            'sample_revision': self.sample_revision,
            'row_version': self.row_version,
        }


@dataclass(frozen=True)
class SampleInventoryFilter:
    """One filter specification shared by list and export."""

    project_id: Optional[str] = None
    team: Optional[str] = None
    status: Optional[SampleStatus] = None
    as_of: Optional[str] = None
    after: Optional[str] = None
    limit: int = 100
    include_deleted: bool = False


def intake_from_mapping(value: Optional[Mapping[str, Any]]) -> Optional[SampleIntake]:
    if value is None:
        return None
    return SampleIntake(
        id=value.get('id'),
        sample_id=value.get('sample_id'),
        intake_date=value.get('intake_date'),
        bl=value.get('bl'),
        ap=value.get('ap'),
        cp=value.get('cp'),
        csc=value.get('csc'),
        rf_cal=value.get('rf_cal'),
        hw_rev=value.get('hw_rev'),
        note=value.get('note'),
        tech_group=value.get('tech_group'),
        created_at=value.get('created_at'),
    )


def sample_from_mapping(value: Mapping[str, Any]) -> Sample:
    status = value.get('status', SampleStatus.ACTIVE)
    if not isinstance(status, SampleStatus):
        status = SampleStatus(str(status))
    return Sample(
        id=str(value['id']),
        project_id=str(value['project_id']),
        sample_number=value.get('sample_number'),
        sample_code=value.get('sample_code'),
        test_category=value.get('test_category'),
        label_number=value.get('label_number'),
        smsn=value.get('smsn'),
        serial_number=value.get('serial_number'),
        intake_cert=value.get('intake_cert'),
        assigned_team=value.get('assigned_team'),
        sender=value.get('sender'),
        receiver=value.get('receiver'),
        received_date=value.get('received_date'),
        released_date=value.get('released_date'),
        note=value.get('note'),
        status=status,
        row_version=int(value.get('row_version', 1)),
        latest_intake=intake_from_mapping(value.get('latest_intake')),
        intake_count=int(value.get('intake_count', 0) or 0),
        created_at=value.get('created_at'),
        updated_at=value.get('updated_at'),
    )


__all__ = [
    'INTAKE_FIELDS',
    'REVISION_SNAPSHOT_FIELDS',
    'SAMPLE_EDITABLE_FIELDS',
    'SNAPSHOT_SCHEMA_VERSION',
    'Sample',
    'SampleIntake',
    'SampleInventoryFilter',
    'SampleRevision',
    'SampleRevisionEvent',
    'SampleSnapshot',
    'SampleStatus',
    'intake_from_mapping',
    'sample_from_mapping',
]
