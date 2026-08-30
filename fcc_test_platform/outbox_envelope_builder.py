"""Convert local ResultOutbox payloads into platform ingestion envelopes (FE-P0c Phase B).

This is the SSOT for the local→central boundary of the measurement attempt
flow. The outbox payload (FE-P0b, dict produced by
``measurement_history_store._enqueue_attempt_outbox``) is dependency-free JSON;
the ingestion envelope is the dict shape consumed by
``platform_ingestion.map_measurement_attempt_record`` /
``map_measurement_result_record``.

Boundary responsibilities:

1. Hoist FE-P0b sub-keys (``provenance.recorded_by``, ``context.project_id``,
   ``context.sheet_name``) into top-level envelope keys consumed by the
   mappers — caller-side hoisting in test fixtures is permanently forbidden.
2. Resolve local int session_id and local project_code into central uuids via
   ``CentralIdResolverPort`` — silent coercion is impossible (resolver raises).
3. Propagate ``condition_hash`` verbatim from payload (FE-P0a
   ingestion_contract Rule ``condition_hash_is_propagated_never_recomputed``).
4. Normalize ``result_json`` from the local result1/result2/result_sum + units
   columns (FE-P0a local↔central field mapping).
5. Mirror ``operator`` from ``provenance.recorded_by`` so the mapper produces
   ``operator`` AND ``recorded_by`` populated from the same source (FE-P0b req5).

Dependency-free: no infrastructure / fastapi / pyvisa / openpyxl / pandas /
PySide6 / sqlalchemy / sqlite3 imports — enforced by
``tests/test_platform_ingestion_fe_p0c.py`` import-boundary AST guard.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional

from fcc_test_platform.central_id_resolver import (
    CentralIdResolutionError,
    CentralIdResolverPort,
)
from fcc_test_platform.session_identity import (
    measurement_target_key,
    provider_result_identity,
)


__all__ = [
    'OutboxEnvelopeBuildError',
    'envelope_from_outbox_attempt_payload',
    'envelopes_from_outbox_events',
    'normalize_result_json_payload',
]


class OutboxEnvelopeBuildError(ValueError):
    """Raised when the outbox payload cannot be converted into a valid envelope.

    Loud-fail so the ingestion worker rejects ill-formed payloads instead of
    silently producing records with empty technology / missing condition_hash.
    """


def envelope_from_outbox_attempt_payload(
    payload: Mapping,
    *,
    provider_id: str,
    central_id_resolver: CentralIdResolverPort,
    chamber_id: str | None = None,
) -> dict:
    """Convert a single outbox attempt payload into an ingestion envelope.

    Raises ``OutboxEnvelopeBuildError`` for missing required fields.
    Raises ``CentralIdResolutionError`` for unresolved local→central ids.
    """
    if not isinstance(payload, Mapping):
        raise OutboxEnvelopeBuildError('payload must be a mapping')
    if not provider_id or not str(provider_id).strip():
        raise OutboxEnvelopeBuildError('provider_id is required')

    context = payload.get('context') or {}
    if not isinstance(context, Mapping):
        raise OutboxEnvelopeBuildError('payload.context must be a mapping')
    provenance = payload.get('provenance') or {}
    if not isinstance(provenance, Mapping):
        raise OutboxEnvelopeBuildError('payload.provenance must be a mapping')

    condition_hash = _required_text(payload, 'condition_hash')
    attempt_number = _required_int(payload, 'attempt_number')

    # ``technology_code`` is the column on MeasurementAttempt (FE-P0c Phase B
    # payload boost). ``sheet_name`` (e.g. 'OBW') is the test_name in central
    # vocabulary — the sheet a measurement was conducted in.
    technology = _optional_text(payload.get('technology_code'))
    if not technology:
        raise OutboxEnvelopeBuildError(
            f'payload.technology_code is required for attempt {payload.get("attempt_id")} '
            f'— central measurement_attempts.technology is NOT NULL'
        )
    test_name = _optional_text(context.get('sheet_name'))
    if not test_name:
        # Fallback: provider may have written a different test_name in the
        # outbox metadata. Surface loud so the writer never silently passes
        # an empty test_name into the schema NOT NULL column.
        test_name = _optional_text(payload.get('test_name'))
    if not test_name:
        raise OutboxEnvelopeBuildError(
            f'envelope.test_name could not be derived (no context.sheet_name + '
            f'no payload.test_name) for attempt {payload.get("attempt_id")}'
        )

    # Local int session → central uuid (loud-fail on missing mapping)
    local_session_id = payload.get('session_id')
    if local_session_id is None:
        raise OutboxEnvelopeBuildError('payload.session_id is required')
    # Measurement target scope — the axis that separates two devices measured on
    # one chamber. Both context keys are already carried by ``_CONTEXT_KEYS``;
    # deriving here (rather than threading a new payload field) keeps the target
    # the envelope's own fact and costs the measurement path no round-trip.
    target_identity = measurement_target_key(
        context.get('model_number'), context.get('sample_code'),
    )
    if chamber_id:
        central_session_uuid = central_id_resolver.resolve_session_uuid(
            int(local_session_id), chamber_id=chamber_id, target_identity=target_identity,
        )
    else:
        # Preserve the pre-multichamber resolver call shape for legacy local
        # outbox callers and their deterministic UUIDs.
        central_session_uuid = central_id_resolver.resolve_session_uuid(
            int(local_session_id), target_identity=target_identity,
        )

    # Local text project_code → central uuid (None if project_id absent)
    local_project_id = context.get('project_id')
    central_project_uuid = central_id_resolver.resolve_project_uuid(local_project_id)

    # operator = recorded_by (FE-P0b req5: same source, both columns populated by mapper)
    operator = _optional_text(provenance.get('recorded_by'))

    envelope: dict = {
        # Provider-controlled result identifier — used by the writer's FK
        # resolution step to look up measurement_results.id INSIDE the SAME
        # transaction (FE-P0a ingestion_contract Rule 2). Never written to
        # measurement_result_id directly (Phase A correctness).
        'result_id': _provider_result_id_from_payload(payload, chamber_id=chamber_id),
        'test_name': test_name,
        'technology': technology,
        'condition_hash': condition_hash,
        'attempt_number': attempt_number,
        'status': _optional_text(payload.get('status')) or 'completed',
        'result': normalize_result_json_payload(payload),
        # Echo provenance object so the mapper can persist it under
        # provenance_json (audit / OTel reconstruction).
        'provenance': dict(provenance),
        # Side-band session uuid — used by builder caller. Echo here for
        # logging/audit; ingestion uses the resolver result via kw arg.
        '_central_session_uuid': central_session_uuid,
    }
    if operator:
        envelope['operator'] = operator
    if central_project_uuid:
        envelope['project_id'] = central_project_uuid

    # Measured model number — the *identity* half of the project axis.
    #
    # ``context.project_id`` above is empty for every locally measured session:
    # ``SessionStore.create_session_from_metadata`` records model/sample and no
    # project, because ``MeasurementTargetIdentity`` deliberately has no project
    # code. The model number is what the session actually knows, and central
    # ``device_models`` can turn it into a project. Hoisting it here (the payload
    # has carried it all along via ``_CONTEXT_KEYS``) is what lets the sync
    # adapter recover the project without a round-trip on the measurement path.
    model_number = _optional_text(context.get('model_number'))
    if model_number:
        envelope['model_number'] = model_number

    # Sample code travels for the same reason and by the same mechanism: the
    # target identity is (model, sample), so hoisting only the model would let
    # two samples of one model keep colliding — half a fix reads like a whole
    # one. ``_CONTEXT_KEYS`` has carried this value all along.
    sample_code = _optional_text(context.get('sample_code'))
    if sample_code:
        envelope['sample_code'] = sample_code

    # Session snapshot provenance is an opaque transport value.  The local
    # enrichment seam has already selected the canonical JSON; this mapper only
    # copies it to the per-attempt envelope so callers that use the envelope
    # directly do not lose the session evidence.  Central still chooses the
    # first complete pair for the session parent row.
    sample_snapshot_json = _optional_text(payload.get('sample_snapshot_json'))
    sample_snapshot_schema_version = _optional_text(
        payload.get('sample_snapshot_schema_version')
    )
    if sample_snapshot_json and sample_snapshot_schema_version:
        envelope['sample_snapshot_json'] = sample_snapshot_json
        envelope['sample_snapshot_schema_version'] = sample_snapshot_schema_version

    reference_snapshot_json = payload.get('project_result_reference_snapshot_json')
    reference_snapshot_schema_version = _optional_text(
        payload.get('project_result_reference_snapshot_schema_version')
    )
    if isinstance(reference_snapshot_json, str) and reference_snapshot_json and (
        reference_snapshot_schema_version
    ):
        envelope['project_result_reference_snapshot_json'] = reference_snapshot_json
        envelope['project_result_reference_snapshot_schema_version'] = (
            reference_snapshot_schema_version
        )

    # Keep the session FK identities separate from the opaque canonical JSON.
    # The central ingestion lane can persist these values without parsing or
    # rebuilding the snapshot bytes.
    session_project_id = _optional_text(payload.get('session_project_id'))
    session_sample_id = _optional_text(payload.get('session_sample_id'))
    if session_project_id and session_sample_id:
        envelope['session_project_id'] = session_project_id
        envelope['session_sample_id'] = session_sample_id

    # Optional/nullable propagation
    pass_fail = _optional_text(payload.get('pass_fail'))
    if pass_fail:
        envelope['verdict'] = pass_fail
    margin = _optional_text(payload.get('margin'))
    if margin:
        envelope['margin'] = margin
    is_latest = payload.get('is_latest')
    if is_latest is not None:
        envelope['is_latest'] = bool(is_latest)
    idempotency_key = _optional_text(payload.get('idempotency_key'))
    if idempotency_key:
        envelope['idempotency_key'] = idempotency_key
    measured_at = _optional_text(payload.get('measured_at'))
    if measured_at:
        envelope['measured_at'] = measured_at

    return envelope


def envelopes_from_outbox_events(
    events: Iterable[Mapping],
    *,
    provider_id: str,
    central_id_resolver: CentralIdResolverPort,
    payload_parser,
    chamber_id: str | None = None,
) -> tuple[str, list[dict]]:
    """Convert a batch of outbox events (list_pending_result_events shape) into
    (central_session_uuid, [attempt_envelope, ...]).

    All events MUST share the same session — caller enforces this when slicing
    the batch from ResultOutbox. The resolver is invoked once per unique
    session.
    """
    envelopes: list[dict] = []
    session_uuid: Optional[str] = None
    for event in events:
        if not isinstance(event, Mapping):
            raise OutboxEnvelopeBuildError('outbox event must be a mapping')
        raw_payload = event.get('payload_json')
        if not raw_payload:
            raise OutboxEnvelopeBuildError(
                f'outbox event {event.get("id")} has empty payload_json'
            )
        payload = payload_parser(raw_payload)
        envelope = envelope_from_outbox_attempt_payload(
            payload,
            provider_id=provider_id,
            central_id_resolver=central_id_resolver,
            chamber_id=chamber_id,
        )
        if session_uuid is None:
            session_uuid = envelope['_central_session_uuid']
        elif envelope['_central_session_uuid'] != session_uuid:
            raise OutboxEnvelopeBuildError(
                f'cross-session batch detected (events span multiple sessions) — '
                f'caller must slice the batch by session before invoking the builder'
            )
        envelopes.append(envelope)
    if session_uuid is None:
        raise OutboxEnvelopeBuildError('event list is empty')
    return session_uuid, envelopes


def normalize_result_json_payload(payload: Mapping) -> dict:
    """Reshape local result1/result2/result_sum + unit columns into one JSON
    envelope, matching the FE-P0a local↔central field mapping note
    ('Local separate columns are normalized into one JSON envelope. Unit metadata preserved verbatim').
    """
    out: dict = {}
    for key in ('result1', 'result2', 'result_sum', 'result1_unit', 'result2_unit', 'result_sum_unit'):
        value = payload.get(key)
        if value is None or value == '':
            continue
        out[key] = value
    # Preserve dccf when present (FE-P0b history fingerprint includes Power)
    dccf = payload.get('dccf')
    if dccf is not None and dccf != '':
        out['dccf'] = dccf
    return out


def _provider_result_id_from_payload(
    payload: Mapping, *, chamber_id: str | None = None,
) -> str:
    # Prefer an explicit ``test_result_id`` (FK to TestResult) — that is the
    # local identifier that maps 1:1 to ``measurement_results.provider_result_id``
    # in central (provider issues a stable text id keyed on the local fact row).
    candidate = _optional_text(payload.get('test_result_id'))
    if candidate:
        return provider_result_identity(candidate, chamber_id)
    # Fallback: the attempt id itself can serve as provider_result_id when the
    # provider chose to use attempt ids as the result identity space.
    fallback = _optional_text(payload.get('attempt_id'))
    if fallback:
        return provider_result_identity(fallback, chamber_id)
    raise OutboxEnvelopeBuildError(
        'cannot derive provider_result_id: payload has no test_result_id and no attempt_id'
    )


def _required_text(payload: Mapping, key: str) -> str:
    text = _optional_text(payload.get(key))
    if not text:
        raise OutboxEnvelopeBuildError(f'payload.{key} is required')
    return text


def _required_int(payload: Mapping, key: str) -> int:
    value = payload.get(key)
    if value is None or value == '':
        raise OutboxEnvelopeBuildError(f'payload.{key} is required')
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise OutboxEnvelopeBuildError(f'payload.{key} must be an integer') from exc


def _optional_text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()
