"""Central application boundary for chamber result ingestion.

The chamber sends only a versioned, data-only envelope. This service validates
that envelope and delegates to the existing central ingestion port. The
parent-first mapping, SQL transaction, and idempotent upsert semantics remain
owned by the central composition; no SQL or connection details cross this
boundary.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from fcc_test_kernel.application.central_contract.api_contracts import CHAMBER_RESULT_INGESTION_SCHEMA_VERSION
from fcc_test_kernel.application.central_contract.central_sync_readiness import (
    CentralSyncReadiness,
    CentralSyncReadinessCode,
)
from domain.ports.output.central_backend_sync_port import (
    CentralBackendSyncPort,
    ResultSyncBatchResult,
)


__all__ = [
    'ChamberResultIngestionError',
    'ChamberResultIngestionUpstreamError',
    'ChamberResultIngestionReceipt',
    'ChamberResultIngestionService',
]


class ChamberResultIngestionError(ValueError):
    """Raised for a malformed or contract-invalid chamber envelope."""


class ChamberResultIngestionUpstreamError(RuntimeError):
    """Raised when central persistence cannot accept a valid envelope."""


@dataclass(frozen=True)
class ChamberResultIngestionReceipt:
    schema_version: str
    batch_id: str
    chamber_id: str
    accepted_event_ids: tuple[int, ...]
    failed_event_ids: tuple[int, ...]
    received_at: str
    sync_status: str = 'enabled'
    readiness_code: str = CentralSyncReadinessCode.READY.value
    readiness_reason: str = ''
    retryable: bool = False

    def as_dict(self) -> dict:
        return {
            'schema_version': self.schema_version,
            'batch_id': self.batch_id,
            'chamber_id': self.chamber_id,
            'accepted_event_ids': list(self.accepted_event_ids),
            'failed_event_ids': list(self.failed_event_ids),
            'received_at': self.received_at,
            'sync_status': self.sync_status,
            'readiness_code': self.readiness_code,
            'readiness_reason': self.readiness_reason,
            'retryable': self.retryable,
        }


class ChamberResultIngestionService:
    """Validate a node envelope and execute the existing central sync port."""

    def __init__(
        self,
        backend: CentralBackendSyncPort,
        *,
        provider_id: str,
        clock: Callable[[], datetime] | None = None,
        readiness_probe: Callable[[], CentralSyncReadiness] | None = None,
        readiness_observer: Callable[[CentralSyncReadiness], None] | None = None,
    ) -> None:
        if not provider_id or not str(provider_id).strip():
            raise ValueError('provider_id is required for chamber result ingestion')
        self._backend = backend
        self._provider_id = str(provider_id).strip()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._readiness_probe = readiness_probe
        self._readiness_observer = readiness_observer

    def ingest(self, payload: Mapping[str, object]) -> ChamberResultIngestionReceipt:
        if not isinstance(payload, Mapping):
            raise ChamberResultIngestionError('request body must be an object')
        unexpected = set(payload).difference({
            'schema_version', 'batch_id', 'chamber_id', 'provider_id', 'events',
        })
        if unexpected:
            raise ChamberResultIngestionError('request contains unsupported fields')
        version = self._required_text(payload, 'schema_version')
        if version != CHAMBER_RESULT_INGESTION_SCHEMA_VERSION:
            raise ChamberResultIngestionError('unsupported result-ingestion schema version')
        batch_id = self._required_text(payload, 'batch_id')
        chamber_id = self._required_text(payload, 'chamber_id')
        provider_id = self._required_text(payload, 'provider_id')
        if provider_id != self._provider_id:
            raise ChamberResultIngestionError('provider_id does not match central configuration')
        raw_events = payload.get('events')
        if not isinstance(raw_events, list) or not raw_events:
            raise ChamberResultIngestionError('events must be a non-empty array')

        events: list[dict] = []
        event_ids: list[int] = []
        seen: set[int] = set()
        for raw_event in raw_events:
            if not isinstance(raw_event, Mapping):
                raise ChamberResultIngestionError('each event must be an object')
            if set(raw_event).difference({'event_id', 'payload'}):
                raise ChamberResultIngestionError('event contains unsupported fields')
            raw_id = raw_event.get('event_id')
            if isinstance(raw_id, bool) or not isinstance(raw_id, int):
                raise ChamberResultIngestionError('event_id must be a positive integer')
            event_id = raw_id
            if event_id <= 0 or event_id in seen:
                raise ChamberResultIngestionError('event ids must be positive and unique')
            event_payload = raw_event.get('payload')
            if not isinstance(event_payload, Mapping):
                raise ChamberResultIngestionError('event payload must be an object')
            try:
                serialized = json.dumps(dict(event_payload), separators=(',', ':'))
            except (TypeError, ValueError) as exc:
                raise ChamberResultIngestionError('event payload is not JSON serializable') from exc
            # Keep chamber scope as transport metadata on the central backend
            # port. It is not part of the provider-owned payload and is never
            # forwarded as SQL/plan detail, but it is required to qualify the
            # central session/result identity for multi-chamber providers.
            events.append({
                'id': event_id,
                'payload_json': serialized,
                'chamber_id': chamber_id,
            })
            event_ids.append(event_id)
            seen.add(event_id)

        readiness = self._readiness()
        if not readiness.enabled:
            return ChamberResultIngestionReceipt(
                schema_version=CHAMBER_RESULT_INGESTION_SCHEMA_VERSION,
                batch_id=batch_id,
                chamber_id=chamber_id,
                accepted_event_ids=(),
                failed_event_ids=tuple(event_ids),
                received_at=self._clock().astimezone(timezone.utc).isoformat(),
                sync_status='disabled',
                readiness_code=readiness.code.value,
                readiness_reason=readiness.reason,
                retryable=readiness.retryable,
            )

        try:
            if readiness.provider is None:
                # Legacy/fake callers without a central preflight have no
                # resolved UUID. Production composition always supplies the
                # typed provider identity before this branch.
                result: ResultSyncBatchResult = self._backend.sync_result_events(events)
            else:
                result = self._backend.sync_result_events(
                    events, provider_uuid=readiness.provider.provider_uuid,
                )
        except Exception as exc:  # noqa: BLE001 — route maps central failure to 503
            raise ChamberResultIngestionUpstreamError(
                'central result ingestion failed'
            ) from exc

        accepted = tuple(sorted({int(event_id) for event_id in result.synced_event_ids}))
        failed = tuple(sorted({int(event_id) for event_id in result.failed_events}))
        unknown = set(event_ids).difference(accepted).difference(failed)
        if unknown:
            # A backend must account for every event. Treat an incomplete
            # acknowledgement as a failed delivery so the caller retries the
            # unacknowledged rows instead of incorrectly ACKing them locally.
            failed = tuple(sorted(set(failed).union(unknown)))
        return ChamberResultIngestionReceipt(
            schema_version=CHAMBER_RESULT_INGESTION_SCHEMA_VERSION,
            batch_id=batch_id,
            chamber_id=chamber_id,
            accepted_event_ids=accepted,
            failed_event_ids=failed,
            received_at=self._clock().astimezone(timezone.utc).isoformat(),
        )

    def _readiness(self) -> CentralSyncReadiness:
        if self._readiness_probe is None:
            result = CentralSyncReadiness(CentralSyncReadinessCode.READY)
            self._observe_readiness(result)
            return result
        try:
            result = self._readiness_probe()
            if not isinstance(result, CentralSyncReadiness):
                raise TypeError('readiness probe returned an invalid result')
            if result.enabled and (
                result.provider is None
                or result.provider.provider_code != self._provider_id
            ):
                # A production-ready result is not complete without the
                # central providers.id UUID.  Treat a malformed/custom probe
                # result as a retryable readiness failure instead of allowing
                # the natural-key provider code to reach a UUID FK writer.
                raise TypeError('ready readiness must include the configured provider identity')
            self._observe_readiness(result)
            return result
        except Exception:
            result = CentralSyncReadiness(
                CentralSyncReadinessCode.CENTRAL_DB_UNAVAILABLE,
                'central database readiness check failed', True,
            )
            self._observe_readiness(result)
            return result

    def _observe_readiness(self, result: CentralSyncReadiness) -> None:
        observer = self._readiness_observer
        if observer is None:
            return
        try:
            observer(result)
        except Exception:
            # Readiness telemetry is additive; an observer must never change
            # ingestion availability or durable acknowledgement semantics.
            return

    @staticmethod
    def _required_text(payload: Mapping[str, object], name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ChamberResultIngestionError(f'{name} is required')
        return value.strip()
