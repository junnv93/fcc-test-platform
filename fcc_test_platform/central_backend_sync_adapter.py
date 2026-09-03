"""CentralBackendSyncPort implementation — outbox → ingestion pipeline (FE-P0c Phase D).

Closes the production runtime gap: this adapter is the SSOT that connects
``BackendSyncService.sync_pending`` (which pulls FE-P0b outbox events) to the
``PostgresIngestionWriter`` (FE-P0c Phase C transaction). Before Phase D the
two layers existed but were never wired — outbox events accumulated locally
and no central record was ever written.

Pipeline (per batch):

    list_pending_result_events()  ← caller (BackendSyncService)
            │ events: list[dict]
            ▼
    [adapter.sync_result_events]
            │
            ├─ group events by local session_id (so the batch goes into a
            │   single SAME-session transaction — FE-P0a contract requires
            │   it for the is_latest toggle)
            │
            ▼
    envelopes_from_outbox_events(events, resolver)
            │ envelopes: list[dict]
            ▼
    build_platform_ingestion_batch(provider_id, central_session_uuid,
                                    attempt_envelopes=envelopes)
            │ batch: PlatformIngestionBatch
            ▼
    build_platform_ingestion_plan(batch)
            │ plan: PlatformIngestionPlan
            ▼
    writer.begin_transaction() → execute steps:
        - measurement_results.upsert (Rule 2 projection)
        - measurement_attempts.upsert_attempt (Rule 1 toggle + INSERT)
        - commit() → post-commit refresh (Rule 4)
            │
            ▼
    ResultSyncBatchResult(synced_event_ids=[...], failed_events={...})

Dependency-free at application layer — concrete Postgres connection_factory
is injected by the composition root.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Callable, Iterable, Mapping, Optional

from fcc_test_platform.central_id_resolver import (
    CentralIdResolutionError,
    CentralIdResolverPort,
)
from fcc_test_platform.outbox_envelope_builder import (
    OutboxEnvelopeBuildError,
    envelopes_from_outbox_events,
)
from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch
from fcc_test_platform.provider_ingestion_plan import build_platform_ingestion_plan
from fcc_test_platform.provider_ingestion_worker import (
    IngestionExecutionResult,
    execute_platform_ingestion_plan,
)
from fcc_test_platform.session_identity import (
    LEGACY_CHAMBER_ID,
    measurement_target_key,
    normalize_chamber_id,
    provider_session_natural_key,
)
from fcc_test_kernel.domain.models.session_provenance import (
    SESSION_ORIGIN_PAYLOAD_KEY,
    WORKBOOK_HANDLE_PAYLOAD_KEY,
    normalize_workbook_handle,
    parse_session_origin,
    session_origin_value,
)
from fcc_test_kernel.domain.ports.output.central_backend_sync_port import ResultSyncBatchResult
from fcc_test_platform.domain.ports.output.platform_ingestion_port import PlatformIngestionWriter


__all__ = [
    'CentralBackendSyncAdapter',
]


#: 노드가 보낸 outbox payload 안의 아티팩트 목록 키. 노드 쪽 SSOT 는
#: ``application.artifacts.outbox_artifact_enrichment.ARTIFACTS_PAYLOAD_KEY`` 이고,
#: 이 모듈은 추출 레인이라 그것을 import 하지 않는다 — 두 상수의 동치는 테스트가 잠근다.
_ARTIFACTS_PAYLOAD_KEY = 'artifacts'


def _accepted_origin(raw: str) -> Optional[str]:
    """알려진 origin 토큰만 통과 (모르면 ``None``).

    중앙 CHECK 가 어휘를 잠그므로, 모르는 토큰을 그대로 넘기면 그 세션 버킷의 **측정
    결과 유입 전체**가 거부된다. 이 축은 관측이고 측정 유입을 막지 않는다.
    """
    return session_origin_value(parse_session_origin(raw)) or None


#: 노드가 보낸 outbox payload 안의 세션 출처 키 → ``build_platform_ingestion_batch``
#: 인자 이름. 키·검증기 모두 **shared-kernel 도메인 SSOT** 에서 온다(노드 쪽 enrichment
#: 모듈은 다른 패키지라 import 하지 않지만, 어휘를 소유한 도메인은 이 레인의 선언된
#: 의존이다) — 리터럴을 들면 그 자리가 바로 드리프트 표면이다.
#:
#: ⚠️ **받는 쪽도 검증한다.** 보내는 쪽(enrichment)이 이미 거르지만 이 어댑터는 노드가
#: 보낸 봉투를 그대로 받는 자리이고, 챔버 HTTP 유입도 같은 경로를 지난다. 검증 없이
#: 넘기면 알 수 없는 토큰 하나가 중앙 CHECK 에 걸려 **그 세션 버킷의 측정 결과 유입
#: 전체**가 실패한다 — 관측 축이 측정 축을 인질로 잡는 바로 그 형태다. 그래서 검증기는
#: 키마다 붙어 있고, 통과하지 못한 값은 **그 칸만 비운다**(이름을 대며 경고).
_SESSION_PROVENANCE_KEYS: tuple[tuple[str, str, Callable[[str], Optional[str]]], ...] = (
    (SESSION_ORIGIN_PAYLOAD_KEY, 'session_origin', _accepted_origin),
    (WORKBOOK_HANDLE_PAYLOAD_KEY, 'session_workbook_handle', normalize_workbook_handle),
)


#: stdlib logger — this module sits in the extraction lane, which must stay free
#: of cross-layer imports. The JSON sink attaches at the root, so structured
#: ``extra={}`` fields reach it unchanged.
logger = logging.getLogger(__name__)


class CentralBackendSyncAdapter:
    """Outbox events → central DB pipeline implementing ``CentralBackendSyncPort``.

    Groups events by session (so each PostgresIngestionTransaction acts on a
    single session per Rule 1) and emits a ``ResultSyncBatchResult`` that
    BackendSyncService consumes verbatim.

    Phase F (2026-05-26) — uses the production ``execute_platform_ingestion_plan``
    worker (which dispatches measurement_attempts/is_latest=true rows to
    ``upsert_attempt`` + ``project_results_from_latest_attempt`` and triggers
    the post-commit coverage refresh on a separate autocommit connection),
    closing the runtime gap flagged by the external review.
    """

    def __init__(
        self,
        *,
        provider_id: str,
        central_id_resolver: CentralIdResolverPort,
        ingestion_writer: PlatformIngestionWriter,
        payload_parser: Callable[[str], Mapping] = json.loads,
    ) -> None:
        if not provider_id or not str(provider_id).strip():
            raise ValueError('provider_id is required')
        self._provider_id = str(provider_id).strip()
        self._resolver = central_id_resolver
        self._writer = ingestion_writer
        self._payload_parser = payload_parser

    def sync_result_events(
        self,
        events: list[dict],
        *,
        provider_uuid: str | None = None,
    ) -> ResultSyncBatchResult:
        if not events:
            return ResultSyncBatchResult()

        # The composition keeps the configured provider code for the resolver's
        # deterministic session namespace. Once platform readiness resolves the
        # central row, every FK-bearing ingestion record uses providers.id.
        central_provider_id = str(provider_uuid or self._provider_id).strip()
        if not central_provider_id:
            raise ValueError('provider_uuid is required for central ingestion')
        synced_ids: list[int] = []
        failed: dict[int, str] = {}

        # Per-chamber/session bucketing — single chamber/session per transaction
        # enforces both the is_latest scope and the central identity boundary.
        # A local integer session id is not globally unique across chambers.
        # FE-P0a ingestion_contract Rule 1 (is_latest toggle scope).
        buckets: dict[tuple[str, int], list[Mapping]] = defaultdict(list)
        order_by_event_id: dict[int, int] = {}
        for index, event in enumerate(events):
            try:
                payload = self._payload_parser(event['payload_json'])
                local_session_id = int(payload['session_id'])
                chamber_id = normalize_chamber_id(event.get('chamber_id'))
            except (KeyError, TypeError, ValueError) as exc:
                event_id = int(event.get('id') or -1)
                failed[event_id] = f'invalid outbox payload: {exc}'
                continue
            buckets[(chamber_id, local_session_id)].append(event)
            event_id = int(event['id'])
            order_by_event_id[event_id] = index

        for (chamber_id, local_session_id), session_events in buckets.items():
            session_synced, session_failed = self._sync_one_session_bucket(
                session_events,
                local_session_id=local_session_id,
                chamber_id=chamber_id,
                provider_id=central_provider_id,
            )
            synced_ids.extend(session_synced)
            failed.update(session_failed)

        synced_ids.sort(key=lambda eid: order_by_event_id.get(eid, eid))
        return ResultSyncBatchResult(synced_event_ids=synced_ids, failed_events=failed)

    def _sync_one_session_bucket(
        self,
        session_events: list[Mapping],
        *,
        local_session_id: int,
        chamber_id: str,
        provider_id: str,
    ) -> tuple[list[int], dict[int, str]]:
        event_ids = [int(event['id']) for event in session_events]
        try:
            central_session_uuid, attempt_envelopes = envelopes_from_outbox_events(
                session_events,
                provider_id=provider_id,
                central_id_resolver=self._resolver,
                payload_parser=self._payload_parser,
                chamber_id=(None if chamber_id == LEGACY_CHAMBER_ID else chamber_id),
            )
        except (OutboxEnvelopeBuildError, CentralIdResolutionError) as exc:
            return [], {event_id: f'envelope build failed: {exc}' for event_id in event_ids}

        # measurement_results envelopes are derived from the same attempt
        # envelopes so the SAME-transaction Rule 2 projection has source rows
        # to UPDATE. The result envelope's required fields come from the
        # attempt envelope verbatim — provider_result_id, test_name, technology.
        result_envelopes = [
            self._result_envelope_from_attempt(envelope) for envelope in attempt_envelopes
        ]

        batch = build_platform_ingestion_batch(
            provider_id=provider_id,
            session_id=central_session_uuid,
            result_envelopes=result_envelopes,
            attempt_envelopes=attempt_envelopes,
            # Session parent row — the measurement FKs (session_id) resolve only
            # if this row exists, and nothing else in production creates it.
            # ``provider_session_id`` is the local session id verbatim: it is the
            # provider/chamber-scoped natural key the central UNIQUE index is built on,
            # and it is the same value that feeds the deterministic session uuid,
            # so re-sync collapses onto the identical row.
            provider_session_id=provider_session_natural_key(
                local_session_id, self._session_target_identity(attempt_envelopes),
            ),
            chamber_id=chamber_id,
            session_project_id=self._session_project_id(attempt_envelopes),
            session_sample_id=self._session_sample_id(attempt_envelopes),
            artifact_metadata=self._session_artifacts(session_events),
            # 세션 출처 (PC 단위 모드 배타 ①) — 아티팩트와 **같은 채널**이고 같은
            # 이유다: 중앙에는 로컬 DB 가 없으므로 조회하지 않고 봉투가 실어 온다.
            **self._session_provenance(session_events),
            # The snapshot is selected by the platform before hardware start and
            # transported opaquely through the same outbox payload.  It is not
            # rebuilt from current inventory here.
            **self._session_sample_snapshot(session_events),
            **self._session_reference_snapshot(session_events),
        )
        plan = build_platform_ingestion_plan(batch)

        try:
            execution = execute_platform_ingestion_plan(plan, self._writer)
        except Exception as exc:
            return [], {event_id: f'ingestion worker raised: {exc}' for event_id in event_ids}

        if not execution.committed:
            error_text = '; '.join(execution.errors) or 'ingestion not committed'
            return [], {event_id: error_text for event_id in event_ids}
        return list(event_ids), {}

    def _session_artifacts(self, session_events: list[Mapping]) -> list[Mapping]:
        """봉투가 실어 온 아티팩트 메타데이터 (없으면 빈 목록).

        **중앙에는 로컬 DB 가 없다** — 아티팩트는 조회하는 것이 아니라 노드가 보낸
        payload 에 실려 온다(``outbox_artifact_enrichment``). 그래서 여기서는 읽기만
        한다. 옛 노드(보강 없는 배포)는 키가 없어 빈 목록이 되고 동기화는 오늘과
        똑같이 동작한다.

        **사본은 권위가 아니다.** 이 행들은 "이 세션의 증거가 이 주소·이 지문으로
        존재한다"를 말하지 "중앙이 그것을 갖고 있다"를 말하지 않는다. 원본 실재는
        별개 축(보관 점검)이 원본 루트를 직접 보고 판정하며 이 행들은 거기 참여하지
        않는다 — 참여시키면 파일서버에 원본이 없는 프로젝트가 "이상 없음"으로 보인다.

        **한 계획 안의 중복 주소를 접는다.** ``build_platform_ingestion_plan`` 은 한
        계획 안의 중복 멱등키를 오류로 본다(같은 트랜잭션에서 같은 행을 두 번 쓰는
        것은 의도가 불분명하다). 배치 **사이**의 재전송은 중앙 자연키
        ``(provider_id, relative_path)`` 가 같은 행으로 collapse 시킨다.
        """
        seen: set[str] = set()
        artifacts: list[Mapping] = []
        for event in session_events:
            try:
                payload = self._payload_parser(event['payload_json'])
            except (KeyError, TypeError, ValueError):
                continue
            rows = payload.get(_ARTIFACTS_PAYLOAD_KEY) if isinstance(payload, Mapping) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                relative_path = str(row.get('relative_path') or '').strip()
                if not relative_path or relative_path in seen:
                    continue
                seen.add(relative_path)
                artifacts.append(row)
        return artifacts

    def _session_provenance(self, session_events: list[Mapping]) -> dict:
        """봉투가 실어 온 세션 출처 (없으면 빈 dict → 오늘과 byte-identical).

        ``_session_artifacts`` 와 같은 형상이고 같은 근거다 — **중앙에는 로컬 DB 가
        없다**. 옛 노드(보강 없는 배포)는 키가 없어 빈 dict 가 되고 동기화는 오늘과
        똑같이 동작한다.

        한 버킷은 한 로컬 세션이므로 **첫 비어 있지 않은 값**이 그 세션의 답이다
        (형제 ``_session_model_number`` 와 같은 규약).
        """
        found: dict = {}
        for event in session_events:
            try:
                payload = self._payload_parser(event['payload_json'])
            except (KeyError, TypeError, ValueError):
                continue
            if not isinstance(payload, Mapping):
                continue
            for payload_key, batch_key, accept in _SESSION_PROVENANCE_KEYS:
                if batch_key in found:
                    continue
                raw = str(payload.get(payload_key) or '').strip()
                if not raw:
                    continue
                value = accept(raw)
                if value:
                    found[batch_key] = value
                else:
                    logger.warning(
                        'outbox payload carries an unrecognised %s — that column is '
                        'left empty rather than failing the whole ingest for this '
                        'session. value=%r',
                        payload_key, raw,
                    )
            if len(found) == len(_SESSION_PROVENANCE_KEYS):
                break
        return found

    def _session_sample_snapshot(self, session_events: list[Mapping]) -> dict:
        """Return the first complete opaque sample-snapshot pair in the bucket."""
        for event in session_events:
            try:
                payload = self._payload_parser(event['payload_json'])
            except (KeyError, TypeError, ValueError):
                continue
            if not isinstance(payload, Mapping):
                continue
            snapshot = payload.get('sample_snapshot_json')
            schema_version = payload.get('sample_snapshot_schema_version')
            if not isinstance(snapshot, str) or not snapshot.strip():
                continue
            if not isinstance(schema_version, str) or not schema_version.strip():
                continue
            return {
                'session_sample_snapshot_json': snapshot.strip(),
                'session_sample_snapshot_schema_version': schema_version.strip(),
            }
        return {}

    def _session_reference_snapshot(self, session_events: list[Mapping]) -> dict:
        """Return the first complete opaque reference-snapshot pair.

        The central sync lane does not query the reference catalog and does not
        parse provider data. It copies the canonical string selected before the
        session started; a partial or conflicting later event is observable at
        the ingestion writer rather than replacing the first complete value.
        """
        for event in session_events:
            try:
                payload = self._payload_parser(event['payload_json'])
            except (KeyError, TypeError, ValueError):
                continue
            if not isinstance(payload, Mapping):
                continue
            snapshot = payload.get('project_result_reference_snapshot_json')
            schema_version = payload.get(
                'project_result_reference_snapshot_schema_version'
            )
            if not isinstance(snapshot, str) or not snapshot:
                continue
            if not isinstance(schema_version, str) or not schema_version.strip():
                continue
            return {
                'session_project_result_reference_snapshot_json': snapshot,
                'session_project_result_reference_snapshot_schema_version': (
                    schema_version.strip()
                ),
            }
        return {}

    def _session_project_id(self, attempt_envelopes: list[Mapping]) -> str:
        """Central project uuid for this session's parent row ('' when unknown).

        Two sources, in this order — and the order is the point:

        1. **The envelope.** If the outbox already carries a project, that is the
           project the caller named and it is used verbatim. We never widen an
           explicit identity (same discipline as ``issuer_lookup_candidates``).
        2. **Central resolution from the model number.** Local measurement
           sessions do not record a project at all — ``SessionStore.
           create_session_from_metadata`` maps ``model_number``/``sample_code``
           and nothing else, because ``MeasurementTargetIdentity`` deliberately
           has no project axis. So without this step ``test_sessions.project_id``
           is *permanently* NULL, and every project-keyed central read (the §6
           equipment list among them) silently returns nothing. That is not a
           hypothetical: it is why the central report path could not draw §6.

        A miss is **not** an error. Measurement facts are true whether or not the
        project has been registered centrally, so an unresolved model leaves the
        column NULL — exactly today's behaviour — and emits a structured WARNING
        naming the model. ``CONFLICT_FILL_ONLY_COLUMNS`` then lets a later sync
        fill it in once the project exists, without overwriting anything central.
        """
        for envelope in attempt_envelopes:
            sideband = str(envelope.get('session_project_id') or '').strip()
            if sideband:
                return sideband
            candidate = str(envelope.get('project_id') or '').strip()
            if candidate:
                return candidate

        model_number = self._session_model_number(attempt_envelopes)
        if not model_number:
            return ''
        try:
            # Attribute access AND the result reads are all inside the guard on
            # purpose. A resolver that does not implement the Port, or one that
            # returns a bare string instead of the value object, must surface as
            # a *named* warning — not as an ``AttributeError`` escaping through
            # ``build_platform_ingestion_batch``'s argument list and failing the
            # whole batch (which reaches the chamber ingest route as a 503).
            # A silent ``getattr`` default would be the opposite mistake: it is
            # the same failure mode this method exists to fix.
            resolution = self._resolver.resolve_project_by_model_number(model_number)
            if resolution.is_resolved:
                return str(resolution.project_uuid)
            reason = resolution.reason
        except Exception as exc:
            logger.warning(
                'central project resolution failed for model_number=%s (%s: %s) — '
                'session stays project-less. Measurement facts still sync.',
                model_number,
                type(exc).__name__,
                exc,
                extra={'model_number': model_number},
            )
            return ''
        logger.warning(
            'central project unresolved — %s. The session row keeps project_id '
            'NULL, so project-keyed central reads (report equipment list) will '
            'find nothing for it.',
            reason,
            extra={'model_number': model_number},
        )
        return ''

    @staticmethod
    def _session_sample_id(attempt_envelopes: list[Mapping]) -> str:
        """First declared sample FK for the session bucket, if present."""
        for envelope in attempt_envelopes:
            candidate = str(envelope.get('session_sample_id') or '').strip()
            if candidate:
                return candidate
        return ''

    @staticmethod
    def _session_model_number(attempt_envelopes: list[Mapping]) -> str:
        """First non-empty model number in the bucket ('' when absent).

        Every envelope in a bucket belongs to one local session, so they carry
        the same measurement target.
        """
        for envelope in attempt_envelopes:
            candidate = str(envelope.get('model_number') or '').strip()
            if candidate:
                return candidate
        return ''

    @staticmethod
    def _session_target_identity(attempt_envelopes: list[Mapping]) -> str:
        """Target identity for the bucket ('' when the target is not identified).

        Model and sample are read from the **same** envelope rather than as two
        independent first-non-empty scans: composing them from different rows
        would invent a target that was never measured.
        """
        for envelope in attempt_envelopes:
            key = measurement_target_key(
                envelope.get('model_number'), envelope.get('sample_code'),
            )
            if key:
                return key
        return ''

    @staticmethod
    def _result_envelope_from_attempt(attempt_envelope: Mapping) -> dict:
        """Build the measurement_results envelope from the attempt envelope.

        Rule 2 projection writes the same provider_result_id row in the
        results table; this envelope is what build_platform_ingestion_batch
        consumes to map measurement_results.
        """
        return {
            'result_id': attempt_envelope['result_id'],
            'test_name': attempt_envelope['test_name'],
            'technology': attempt_envelope['technology'],
            # condition_json on the results row is a normalized payload —
            # default to empty when the attempt envelope did not carry a
            # condition document (central reconstruction reads attempts).
            'condition': {},
            'result': attempt_envelope.get('result') or {},
            'verdict': attempt_envelope.get('verdict', ''),
            'measured_at': attempt_envelope.get('measured_at', ''),
            'condition_hash': attempt_envelope['condition_hash'],
            'project_id': attempt_envelope.get('project_id', ''),
            'operator': attempt_envelope.get('operator', ''),
        }
