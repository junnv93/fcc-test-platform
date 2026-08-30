"""Dependency-free mapping from provider envelopes to platform DB records.

FE-P0c (2026-05-26) extends measurement record mapping with FE-P0a central
columns (`condition_hash` / `project_id` / `operator` / `attempt_number` /
`is_latest`) and introduces ``map_measurement_attempt_record`` for the
``measurement_attempts`` central table. Operator provenance (FE-P0b) is
populated into BOTH ``operator`` and ``recorded_by`` columns from the same
source — ``payload.provenance.recorded_by`` at the outbox layer. condition_hash
is propagated verbatim from ``compute_condition_hash`` (never recomputed
centrally) — see central schema ``ingestion_contract`` rule
``condition_hash_is_propagated_never_recomputed``.

``coverage_by_condition_hash`` is a CENTRAL materialized VIEW derived from
``measurement_attempts`` (FE-P0a) — this module never writes a coverage record
directly. Structural guards in ``tests/test_platform_ingestion_fe_p0c.py``
enforce this invariant by AST.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import json
from typing import Iterable, Mapping, Optional


__all__ = [
    'DEFAULT_TEST_SESSION_STATUS',
    'REPORT_RUN_COMPLETED_STATUS',
    'PlatformIngestionBatch',
    'build_platform_ingestion_batch',
    'map_artifact_metadata_record',
    'map_measurement_attempt_record',
    'map_measurement_result_record',
    'map_report_output_record',
    'map_report_run_record',
    'map_test_session_record',
]


#: Status stamped on a session row created by the ingestion pipeline. The
#: session parent row exists only so the measurement FKs resolve; lifecycle
#: transitions (completed/aborted) are owned by the central session APIs, so
#: the ingest-time value is the neutral 'active' and is never overwritten on
#: re-sync (the upsert leaves an existing row untouched).
DEFAULT_TEST_SESSION_STATUS = 'active'

# A report-output batch is emitted only after the provider/report path has
# reached this terminal state. The status is evidence supplied by that path,
# not a database-side guess or a timestamp surrogate.
REPORT_RUN_COMPLETED_STATUS = 'completed'


@dataclass(frozen=True)
class PlatformIngestionBatch:
    # test_sessions is the FK parent of measurement_results / measurement_attempts
    # / artifacts, so it is declared (and ordered) first. Default empty keeps
    # every pre-existing caller byte-identical.
    test_sessions: list[dict] = field(default_factory=list)
    report_runs: list[dict] = field(default_factory=list)
    measurement_results: list[dict] = field(default_factory=list)
    measurement_attempts: list[dict] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    report_outputs: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'test_sessions': [dict(row) for row in self.test_sessions],
            'report_runs': [dict(row) for row in self.report_runs],
            'measurement_results': [dict(row) for row in self.measurement_results],
            'measurement_attempts': [dict(row) for row in self.measurement_attempts],
            'artifacts': [dict(row) for row in self.artifacts],
            'report_outputs': [dict(row) for row in self.report_outputs],
        }


def build_platform_ingestion_batch(
    *,
    provider_id: str,
    session_id: str,
    result_envelopes: Iterable[Mapping],
    artifact_metadata: Iterable[Mapping] = (),
    report_outputs: Iterable[Mapping] = (),
    attempt_envelopes: Iterable[Mapping] = (),
    result_record_ids_by_provider_result_id: Optional[Mapping[str, str]] = None,
    report_run_id: str = '',
    report_run_evidence: Optional[Mapping] = None,
    provider_session_id: str = '',
    chamber_id: str = '',
    session_project_id: str = '',
    session_sample_id: str = '',
    session_status: str = DEFAULT_TEST_SESSION_STATUS,
    session_origin: str = '',
    session_workbook_handle: str = '',
    session_sample_snapshot_json: str = '',
    session_sample_snapshot_schema_version: str = '',
    session_project_result_reference_snapshot_json: str = '',
    session_project_result_reference_snapshot_schema_version: str = '',
) -> PlatformIngestionBatch:
    result_lookup = result_record_ids_by_provider_result_id or {}
    measurement_results = [
        map_measurement_result_record(
            provider_id=provider_id,
            session_id=session_id,
            envelope=envelope,
        )
        for envelope in result_envelopes
    ]
    attempt_records = [
        map_measurement_attempt_record(
            provider_id=provider_id,
            session_id=session_id,
            envelope=envelope,
        )
        for envelope in attempt_envelopes
    ]
    _apply_is_latest_derivation(attempt_records)
    # The session parent row is only emitted when the caller supplies its
    # natural key. Callers that predate the parent-row upsert (and fixtures that
    # seed test_sessions themselves) pass nothing and stay byte-identical.
    session_records = (
        [
            map_test_session_record(
                session_id=session_id,
                provider_id=provider_id,
                provider_session_id=provider_session_id,
                chamber_id=chamber_id,
                project_id=session_project_id,
                sample_id=session_sample_id,
                status=session_status,
                session_origin=session_origin,
                workbook_handle=session_workbook_handle,
                sample_snapshot_json=session_sample_snapshot_json,
                sample_snapshot_schema_version=session_sample_snapshot_schema_version,
                project_result_reference_snapshot_json=(
                    session_project_result_reference_snapshot_json
                ),
                project_result_reference_snapshot_schema_version=(
                    session_project_result_reference_snapshot_schema_version
                ),
            )
        ]
        if _optional_text(provider_session_id)
        else []
    )
    report_output_records = [
        map_report_output_record(
            report_run_id=report_run_id,
            output=output,
        )
        for output in report_outputs
    ]
    report_run_records = (
        [
            map_report_run_record(
                report_run_id=report_run_id,
                provider_id=provider_id,
                session_id=session_id,
                evidence={} if report_run_evidence is None else report_run_evidence,
            )
        ]
        if report_output_records
        else []
    )
    return PlatformIngestionBatch(
        test_sessions=session_records,
        report_runs=report_run_records,
        measurement_results=measurement_results,
        measurement_attempts=attempt_records,
        artifacts=[
            map_artifact_metadata_record(
                provider_id=provider_id,
                session_id=session_id,
                artifact=artifact,
                result_record_ids_by_provider_result_id=result_lookup,
            )
            for artifact in artifact_metadata
        ],
        report_outputs=report_output_records,
    )


def map_test_session_record(
    *,
    session_id: str,
    provider_id: str,
    provider_session_id: str,
    chamber_id: str = '',
    project_id: str = '',
    sample_id: str = '',
    status: str = DEFAULT_TEST_SESSION_STATUS,
    session_origin: str = '',
    workbook_handle: str = '',
    sample_snapshot_json: str = '',
    sample_snapshot_schema_version: str = '',
    project_result_reference_snapshot_json: str = '',
    project_result_reference_snapshot_schema_version: str = '',
) -> dict:
    """Map the central ``test_sessions`` parent row for one ingestion batch.

    ``id`` is caller-supplied on purpose: it is the deterministic uuid5 derived
    from ``provider_id`` + chamber scope + the local session id for new chamber
    traffic (or the legacy provider/local format for pre-multichamber callers),
    which is what makes a re-sync idempotent without the offline measurement
    loop ever calling central.

    ``created_at`` / ``updated_at`` are deliberately OMITTED — the central
    schema gives both a ``now()`` default (migration 011), so the DB owns them
    for every caller. Stamping them here would recreate the caller-stamp drift
    class that migration 009 removed for measurement ids.
    """
    record: dict = {
        'id': _required_value(session_id, 'session_id'),
        'provider_id': _required_value(provider_id, 'provider_id'),
        'provider_session_id': _required_value(provider_session_id, 'provider_session_id'),
        'status': _required_value(status, 'status'),
    }
    chamber = _optional_text(chamber_id)
    if chamber:
        record['chamber_id'] = chamber
    project = _optional_text(project_id)
    if project:
        record['project_id'] = project
    sample = _optional_text(sample_id)
    if sample:
        record['sample_id'] = sample
    # 세션 출처 (PC 단위 모드 배타 ①) — 형제 두 칸과 **같은 규율**로 비어 있지 않을
    # 때만 싣는다. 미선언 세션의 유입 행이 오늘과 byte-identical 해야, 이 축이 켜진
    # 것과 그 세션이 로컬이었던 것이 구분된다.
    origin = _optional_text(session_origin)
    if origin:
        record['session_origin'] = origin
    handle = _optional_text(workbook_handle)
    if handle:
        record['workbook_handle'] = handle
    snapshot_json = _optional_text(sample_snapshot_json)
    snapshot_version = _optional_text(sample_snapshot_schema_version)
    if snapshot_json and snapshot_version:
        record['sample_snapshot_json'] = snapshot_json
        record['sample_snapshot_schema_version'] = snapshot_version
    reference_snapshot_json = (
        project_result_reference_snapshot_json
        if isinstance(project_result_reference_snapshot_json, str)
        else ''
    )
    reference_snapshot_version = _optional_text(
        project_result_reference_snapshot_schema_version
    )
    if reference_snapshot_json and reference_snapshot_version:
        record['project_result_reference_snapshot_json'] = reference_snapshot_json
        record['project_result_reference_snapshot_schema_version'] = (
            reference_snapshot_version
        )
    return record


def map_measurement_result_record(
    *,
    provider_id: str,
    session_id: str,
    envelope: Mapping,
) -> dict:
    provider_result_id = _required_text(envelope, 'result_id')
    record: dict = {
        'provider_id': _required_value(provider_id, 'provider_id'),
        'session_id': _required_value(session_id, 'session_id'),
        'provider_result_id': provider_result_id,
        'test_name': _required_text(envelope, 'test_name'),
        'technology': _required_text(envelope, 'technology'),
        'condition_json': _json_payload(envelope.get('condition') or {}),
        'result_json': _json_payload(envelope.get('result') or {}),
        'verdict': _optional_text(envelope.get('verdict')),
        'measured_at': _optional_text(envelope.get('measured_at')),
    }
    # FE-P0c (2026-05-26) — FE-P0a central columns. Optional so legacy 9-column
    # callers remain byte-identical. condition_hash is propagated verbatim from
    # the local payload (compute_condition_hash) — central never recomputes.
    condition_hash = _optional_text(envelope.get('condition_hash'))
    if condition_hash:
        record['condition_hash'] = condition_hash
    project_id = _optional_text(envelope.get('project_id'))
    if project_id:
        record['project_id'] = project_id
    operator = _optional_text(envelope.get('operator'))
    if operator:
        record['operator'] = operator
    return record


def map_measurement_attempt_record(
    *,
    provider_id: str,
    session_id: str,
    envelope: Mapping,
) -> dict:
    """Map a measurement-attempt envelope to a ``measurement_attempts`` record.

    FE-P0c (2026-05-26) — append-only ledger row. ``operator`` AND
    ``recorded_by`` are populated from the SAME source (the outbox payload's
    ``provenance.recorded_by`` at the higher layer; the envelope key here is
    ``operator``) to satisfy the FE-P0b req5 cross-table mapping contract.
    ``condition_hash`` is propagated verbatim — central never recomputes.
    ``is_latest`` is filled in batch-derivation; this helper sets a default
    of ``True`` for single-attempt envelopes and the batch helper later
    derives one candidate within the exact (project_id, provider_id,
    condition_hash) group. The central database remains the recency authority.
    """
    record: dict = {
        'provider_id': _required_value(provider_id, 'provider_id'),
        'session_id': _required_value(session_id, 'session_id'),
        'test_name': _required_text(envelope, 'test_name'),
        'technology': _required_text(envelope, 'technology'),
        'condition_hash': _required_text(envelope, 'condition_hash'),
        'attempt_number': _required_int(envelope, 'attempt_number'),
        'is_latest': _coerce_bool(envelope.get('is_latest', True)),
        'status': _optional_text(envelope.get('status')) or 'completed',
        'result_json': _json_payload(envelope.get('result') or {}),
    }
    # FE-P0b req5 (2026-05-25) — operator + recorded_by both populated from
    # the same provenance source. Both columns mirror to keep query paths
    # (latest projection + audit) consistent.
    operator = _optional_text(envelope.get('operator'))
    if operator:
        record['operator'] = operator
        record['recorded_by'] = operator
    # Optional FE-P0a columns
    project_id = _optional_text(envelope.get('project_id'))
    if project_id:
        record['project_id'] = project_id
    # NOTE (FE-P0c Phase A, 2026-05-26): ``measurement_result_id`` is a CENTRAL
    # uuid FK to ``measurement_results.id``; it cannot be derived from the
    # local outbox payload because the central uuid does not exist until the
    # SAME-transaction projection INSERT (FE-P0a ingestion_contract Rule 2).
    # The ingestion writer (PostgresIngestionTransaction.upsert_attempt) is
    # responsible for resolving (provider_id, provider_result_id) → uuid via
    # ``RETURNING id`` inside the transaction. We do NOT map result_id into
    # measurement_result_id here — that would write a provider-supplied string
    # into a uuid column and silently violate FK integrity.
    provider_result_id = _optional_text(envelope.get('result_id'))
    if provider_result_id:
        # Side-band only — preserved so the writer's FK resolution step can
        # look up the matching ``measurement_results`` uuid. Removed before
        # ``build_postgres_upsert`` (see PostgresIngestionTransaction).
        record['_fk_provider_result_id'] = provider_result_id
    verdict = _optional_text(envelope.get('verdict'))
    if verdict:
        record['verdict'] = verdict
    margin = _optional_text(envelope.get('margin'))
    if margin:
        record['margin'] = margin
    run_id = _optional_text(envelope.get('run_id'))
    if run_id:
        record['run_id'] = run_id
    idempotency_key = _optional_text(envelope.get('idempotency_key'))
    if idempotency_key:
        record['idempotency_key'] = idempotency_key
    provenance = envelope.get('provenance')
    if isinstance(provenance, Mapping) and provenance:
        record['provenance_json'] = _json_payload(provenance)
    measured_at = _optional_text(envelope.get('measured_at'))
    if measured_at:
        record['measured_at'] = measured_at
    return record


def map_artifact_metadata_record(
    *,
    provider_id: str,
    session_id: str,
    artifact: Mapping,
    result_record_ids_by_provider_result_id: Optional[Mapping[str, str]] = None,
) -> dict:
    provider_result_id = _optional_text(artifact.get('result_id'))
    result_record_id = ''
    if provider_result_id and result_record_ids_by_provider_result_id:
        result_record_id = _optional_text(
            result_record_ids_by_provider_result_id.get(provider_result_id)
        )
    return {
        'provider_id': _required_value(provider_id, 'provider_id'),
        'session_id': _required_value(session_id, 'session_id'),
        'measurement_result_id': result_record_id,
        'artifact_type': _required_text(artifact, 'artifact_type'),
        'relative_path': _required_text(artifact, 'relative_path'),
        'original_filename': _optional_text(artifact.get('original_filename')),
        'sha256': _optional_text(artifact.get('sha256')),
        'byte_size': _optional_int(artifact.get('byte_size')),
        'storage_backend': _optional_text(artifact.get('storage_backend')) or 'filesystem',
    }


def map_report_output_record(
    *,
    report_run_id: str,
    output: Mapping,
) -> dict:
    return {
        'report_run_id': _required_value(report_run_id, 'report_run_id'),
        'file_name': _required_text(output, 'file_name'),
        'relative_path': _required_text(output, 'relative_path'),
        'sha256': _optional_text(output.get('sha256')),
        'byte_size': _optional_int(output.get('byte_size')),
        'storage_backend': _optional_text(output.get('storage_backend')) or 'filesystem',
    }


def map_report_run_record(
    *,
    report_run_id: str,
    provider_id: str,
    session_id: str,
    evidence: Mapping,
) -> dict:
    """Map the single report parent from terminal provider/report evidence.

    The parent is deliberately materialized only when at least one output was
    mapped by ``build_platform_ingestion_batch``. Its identity is supplied by
    the report path, while provider/session/status are required evidence. The
    database owns ``created_at``; no caller timestamp is accepted here.
    """
    if not isinstance(evidence, Mapping):
        raise ValueError('report_run_evidence must be an object')
    status = _required_text(evidence, 'status').lower()
    if status != REPORT_RUN_COMPLETED_STATUS:
        raise ValueError(
            'report_run_evidence.status must be '
            f'{REPORT_RUN_COMPLETED_STATUS!r}'
        )
    record: dict = {
        'id': _required_value(report_run_id, 'report_run_id'),
        'provider_id': _required_value(provider_id, 'provider_id'),
        'session_id': _required_value(session_id, 'session_id'),
        'status': status,
    }
    provider_request_id = _optional_text(evidence.get('provider_report_request_id'))
    if provider_request_id:
        record['provider_report_request_id'] = provider_request_id
    if 'report_types' in evidence and evidence.get('report_types') is not None:
        record['report_types_json'] = _json_document(
            evidence.get('report_types'), 'report_types'
        )
    if 'warnings' in evidence and evidence.get('warnings') is not None:
        record['warnings_json'] = _json_document(evidence.get('warnings'), 'warnings')
    return record


def _apply_is_latest_derivation(records: list[dict]) -> None:
    """Derive one latest candidate per exact provider partition in a batch.

    ``attempt_number`` is session-local and is deliberately not a project-wide
    recency key. The central writer re-derives the authoritative value after the
    INSERT using ``measured_at DESC NULLS LAST, created_at DESC, id DESC``;
    this batch pass only prevents multiple newly supplied candidates from being
    marked latest before that transaction runs. Missing timestamps retain input
    order as a deterministic final in-memory tie-breaker; the database-owned
    ``created_at``/``id`` order remains authoritative after persistence.
    """
    if not records:
        return
    groups: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        key = (
            str(record.get('project_id') or ''),
            str(record.get('provider_id') or ''),
            str(record.get('condition_hash') or ''),
        )
        groups[key].append(index)
    for indices in groups.values():
        if len(indices) <= 1:
            continue
        max_index = max(indices, key=lambda index: _batch_recency_key(records[index], index))
        for index in indices:
            records[index]['is_latest'] = (index == max_index)


def _batch_recency_key(record: Mapping, input_order: int) -> tuple:
    """Return a deterministic, NULLS-LAST ordering key for batch candidates."""
    measured_at = _optional_text(record.get('measured_at'))
    created_at = _optional_text(record.get('created_at'))
    identifier = _optional_text(record.get('id'))
    return (
        measured_at != '',
        measured_at,
        created_at != '',
        created_at,
        identifier,
        input_order,
    )


def _json_payload(value) -> str:
    if not isinstance(value, Mapping):
        raise ValueError('JSON payload must be an object')
    return json.dumps(dict(value), sort_keys=True, separators=(',', ':'))


def _json_document(value, key: str) -> str:
    if not isinstance(value, (Mapping, list, tuple)):
        raise ValueError(f'{key} must be a JSON object or array')
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _required_text(data: Mapping, key: str) -> str:
    return _required_value(data.get(key), key)


def _required_value(value, key: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f'{key} is required')
    return text


def _required_int(data: Mapping, key: str) -> int:
    value = data.get(key)
    if value is None or value == '':
        raise ValueError(f'{key} is required')
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{key} must be an integer') from exc


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ('true', '1', 'yes', 't'):
        return True
    if text in ('false', '0', 'no', 'f', ''):
        return False
    raise ValueError(f'is_latest must be boolean, got {value!r}')


def _optional_text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _optional_int(value) -> Optional[int]:
    if value is None or value == '':
        return None
    return int(value)
