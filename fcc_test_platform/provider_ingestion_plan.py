"""Build deterministic central DB ingestion plans from mapped provider records.

The plan is metadata for a future platform worker. This module does not write
to a database, publish queue messages, call networks, or touch files.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping


# FE-P0c Phase A (2026-05-26) — side-band record key prefix used to carry
# foreign-key resolution hints from the envelope to the plan step. Pop'd by
# ``build_platform_ingestion_plan`` so the final ``step.record`` contains only
# central-schema columns (avoids SQL identifier errors in build_postgres_upsert).
SIDE_BAND_KEY_PREFIX = '_fk_'

from fcc_test_platform.artifact_storage import normalize_relative_path
from fcc_test_platform.provider_ingestion import PlatformIngestionBatch


__all__ = [
    'INGESTION_TABLE_ORDER',
    'IDEMPOTENCY_KEYS_BY_TABLE',
    'PROVIDER_SCOPED_IDEMPOTENCY_KEYS_BY_TABLE',
    'CHAMBER_SCOPED_IDEMPOTENCY_KEYS_BY_TABLE',
    'provider_scoped_idempotency_key',
    'idempotency_fields_for_record',
    'IngestionPlanStep',
    'PlatformIngestionPlan',
    'build_platform_ingestion_plan',
]


INGESTION_TABLE_ORDER = (
    # test_sessions FIRST — it is the FK parent of measurement_results /
    # measurement_attempts / artifacts (session_id). Before it was ingested,
    # only dev_seed / live-proof scripts created the row, so a live sync into a
    # fresh central DB failed on the session FK and no measurement landed.
    # Ordering it ahead keeps parent and children in ONE transaction.
    'test_sessions',
    # A report output references report_runs, so the report parent follows the
    # session parent and precedes every report_outputs child in the same tx.
    'report_runs',
    'measurement_results',
    'measurement_attempts',
    'artifacts',
    'report_outputs',
)

IDEMPOTENCY_KEYS_BY_TABLE = {
    # Legacy compatibility key for callers that predate chamber transport.
    # New records use CHAMBER_SCOPED_IDEMPOTENCY_KEYS_BY_TABLE below; the
    # central migration supplies the reserved sentinel when this field is
    # absent from the SQL record.
    'test_sessions': ('provider_id', 'provider_session_id'),
    # report_runs has no separate natural-key index; its caller-supplied UUID
    # primary key is the stable replay identity.
    'report_runs': ('id',),
    'measurement_results': ('provider_id', 'provider_result_id'),
    # Legacy FE-P0c contract. Keep the public plan key stable for existing
    # callers; the worker upgrades attempt writes to the provider-scoped key
    # below before they reach the central SQL adapter.
    'measurement_attempts': ('session_id', 'condition_hash', 'attempt_number'),
    'artifacts': ('provider_id', 'relative_path'),
    'report_outputs': ('report_run_id', 'relative_path'),
}

# Additive provider-scoped identity used by the repaired central transaction.
# It is deliberately separate from IDEMPOTENCY_KEYS_BY_TABLE so old outbox
# manifests and local callers remain readable and replay-compatible.
PROVIDER_SCOPED_IDEMPOTENCY_KEYS_BY_TABLE = {
    'measurement_attempts': (
        'provider_id', 'session_id', 'condition_hash', 'attempt_number',
    ),
}

# Multichamber repair (2026-08-05). Records emitted from the chamber HTTP
# boundary use this wider target, matching the central unique index. Legacy
# callers retain the two-field plan representation and rely on the migration
# sentinel at the SQL boundary.
CHAMBER_SCOPED_IDEMPOTENCY_KEYS_BY_TABLE = {
    'test_sessions': ('provider_id', 'chamber_id', 'provider_session_id'),
}

_REQUIRED_FIELDS_BY_TABLE = {
    # created_at/updated_at are absent by design — the central schema defaults
    # them to now() (migration 011) so no caller stamps them.
    'test_sessions': (
        'id',
        'provider_id',
        'provider_session_id',
        'status',
    ),
    'report_runs': (
        'id',
        'provider_id',
        'session_id',
        'status',
    ),
    'measurement_results': (
        'provider_id',
        'session_id',
        'provider_result_id',
        'test_name',
        'technology',
        'condition_json',
        'result_json',
    ),
    'measurement_attempts': (
        'provider_id',
        'session_id',
        'test_name',
        'technology',
        'condition_hash',
        'attempt_number',
        'is_latest',
        'status',
        'result_json',
    ),
    'artifacts': (
        'provider_id',
        'session_id',
        'artifact_type',
        'relative_path',
        'storage_backend',
    ),
    'report_outputs': (
        'report_run_id',
        'file_name',
        'relative_path',
        'storage_backend',
    ),
}


@dataclass(frozen=True)
class IngestionPlanStep:
    order: int
    target_table: str
    operation: str
    idempotency_key: tuple[str, ...]
    record: dict
    # FE-P0c Phase A (2026-05-26) — foreign-key resolution hints. Carries
    # (provider_id, provider_result_id) so the writer can SELECT id FROM
    # measurement_results to populate measurement_attempts.measurement_result_id
    # inside the SAME ingestion transaction (FE-P0a ingestion_contract Rule 2).
    # Default empty so legacy (results/artifacts/report_outputs) steps are byte-identical.
    fk_resolution_hint: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        payload = {
            'order': self.order,
            'target_table': self.target_table,
            'operation': self.operation,
            'idempotency_key': list(self.idempotency_key),
            'record': dict(self.record),
        }
        if self.fk_resolution_hint:
            payload['fk_resolution_hint'] = dict(self.fk_resolution_hint)
        return payload


@dataclass(frozen=True)
class PlatformIngestionPlan:
    steps: tuple[IngestionPlanStep, ...]

    def to_dict(self) -> dict:
        return {
            'steps': [step.to_dict() for step in self.steps],
            'table_order': list(INGESTION_TABLE_ORDER),
        }


def build_platform_ingestion_plan(batch: PlatformIngestionBatch | Mapping) -> PlatformIngestionPlan:
    payload = batch.to_dict() if isinstance(batch, PlatformIngestionBatch) else dict(batch)
    report_runs = _materialize_records(payload, 'report_runs')
    report_outputs = _materialize_records(payload, 'report_outputs')
    _validate_report_parent_invariant(report_runs, report_outputs)
    steps: list[IngestionPlanStep] = []
    seen_keys: set[tuple[str, tuple[str, ...]]] = set()

    for table in INGESTION_TABLE_ORDER:
        raw_records = payload.get(table) or []
        if not isinstance(raw_records, Iterable) or isinstance(raw_records, (str, bytes, Mapping)):
            raise ValueError(f'{table} must be a list of records')
        for raw in raw_records:
            record, fk_hint = _record_and_hints(raw, table)
            key = _idempotency_key(table, record)
            duplicate_marker = (table, key)
            if duplicate_marker in seen_keys:
                raise ValueError(f'duplicate idempotency key for {table}: {key}')
            seen_keys.add(duplicate_marker)
            steps.append(
                IngestionPlanStep(
                    order=len(steps) + 1,
                    target_table=table,
                    operation='upsert',
                    idempotency_key=key,
                    record=record,
                    fk_resolution_hint=fk_hint,
                )
            )

    unknown = set(payload) - set(INGESTION_TABLE_ORDER)
    if unknown:
        raise ValueError(f'unknown ingestion tables: {sorted(unknown)}')
    return PlatformIngestionPlan(steps=tuple(steps))


def _materialize_records(payload: Mapping, table: str) -> list:
    """Consume one table collection once so cross-table validation is safe."""
    raw_records = payload.get(table) or []
    if not isinstance(raw_records, Iterable) or isinstance(raw_records, (str, bytes, Mapping)):
        raise ValueError(f'{table} must be a list of records')
    records = list(raw_records)
    if isinstance(payload, dict):
        payload[table] = records
    return records


def _validate_report_parent_invariant(
    report_runs: list,
    report_outputs: list,
) -> None:
    """Keep report output plans closed over their single FK parent."""
    if not report_outputs:
        if report_runs:
            raise ValueError(
                'report_runs must be empty when report_outputs is empty'
            )
        return
    if len(report_runs) != 1:
        raise ValueError(
            'non-empty report_outputs requires exactly one report_runs record'
        )
    parent = report_runs[0]
    if not isinstance(parent, Mapping):
        raise ValueError('report_runs records must be objects')
    parent_id = _text(parent.get('id'))
    if not parent_id:
        raise ValueError('report_runs.id is required')
    for child in report_outputs:
        if not isinstance(child, Mapping):
            raise ValueError('report_outputs records must be objects')
        child_id = _text(child.get('report_run_id'))
        if not child_id:
            raise ValueError('report_outputs.report_run_id is required')
        if child_id != parent_id:
            raise ValueError(
                'report_outputs.report_run_id must match the single report_runs.id'
            )


def _record_and_hints(value, table: str) -> tuple[dict, dict]:
    """Split a raw record into (schema_record, fk_resolution_hint).

    Side-band keys starting with ``SIDE_BAND_KEY_PREFIX`` are popped into the
    hint so the schema record contains only central-schema columns — required
    by ``build_postgres_upsert``'s identifier validation.
    """
    if not isinstance(value, Mapping):
        raise ValueError(f'{table} records must be objects')
    record: dict = {}
    fk_hint: dict = {}
    for key, item in value.items():
        if isinstance(key, str) and key.startswith(SIDE_BAND_KEY_PREFIX):
            fk_hint[key[len(SIDE_BAND_KEY_PREFIX):]] = item
        else:
            record[key] = item
    for required in _REQUIRED_FIELDS_BY_TABLE[table]:
        if not _text(record.get(required)):
            raise ValueError(f'{table}.{required} is required')
    if 'relative_path' in record:
        record['relative_path'] = normalize_relative_path(str(record['relative_path']))
    return record, fk_hint


def _idempotency_key(table: str, record: Mapping) -> tuple[str, ...]:
    fields = idempotency_fields_for_record(table, record)
    return tuple(_text(record.get(field)) for field in fields)


def idempotency_fields_for_record(table: str, record: Mapping) -> tuple[str, ...]:
    """Return the natural key matching the record's schema generation.

    A missing chamber field is a legacy input and remains plan-compatible. A
    non-empty chamber field must use the chamber-qualified central index.
    """
    if table == 'test_sessions' and _text(record.get('chamber_id')):
        return CHAMBER_SCOPED_IDEMPOTENCY_KEYS_BY_TABLE[table]
    return IDEMPOTENCY_KEYS_BY_TABLE[table]


def provider_scoped_idempotency_key(
    table: str,
    record: Mapping,
    legacy_key: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Return the additive provider-qualified storage key for a record.

    ``legacy_key`` is accepted for callers that persisted a three-field plan
    manifest. Its values are checked against the legacy identity, then the
    provider value is prepended for the central write path.
    """
    fields = PROVIDER_SCOPED_IDEMPOTENCY_KEYS_BY_TABLE.get(table)
    if fields is None:
        return tuple(legacy_key or _idempotency_key(table, record))
    if legacy_key is not None:
        expected_legacy = tuple(
            _text(record.get(field)) for field in IDEMPOTENCY_KEYS_BY_TABLE[table]
        )
        if tuple(_text(value) for value in legacy_key) != expected_legacy:
            raise ValueError('legacy idempotency key does not match record')
    return tuple(_text(record.get(field)) for field in fields)


def _text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()
