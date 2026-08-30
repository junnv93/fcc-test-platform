"""Platform read API composition root (FE-P0d, 2026-05-27).

Assembles the central-PostgreSQL-backed read runtime for the platform surface:

    PlatformApiConfig (env)
        central DSN ──► psycopg connection_factory (lazy)
                            │
                            ▼
                 PostgresCentralReadAdapter ──► CentralReadService ──► PlatformApiAdapter
        auth ──► principal_resolver ─────────────────────────────────┘ + ApiAccessPolicy

Loud-fail when the central DB is not configured — the platform read API cannot
serve project-wide coverage/claims without it, so a missing ``FCC_CENTRAL_DB_URL``
raises at composition time rather than producing a runtime that 503s every call.

**frozen-exe safety.** ``psycopg`` is imported lazily inside
``_build_central_connection_factory`` only — a desktop build that never composes
the platform API pulls in zero PostgreSQL driver code. Tests inject a fake/SQLite
connection factory and never touch psycopg. Enforced by
``tests/test_platform_read_api_fe_p0d.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from fcc_test_contracts.common.access_policy import ApiAccessPolicy
import os
from datetime import datetime, timezone

from fcc_test_contracts.common.auth_config import AUTH_MODE_LOCAL_JWT, HttpAuthConfig
from fcc_test_contracts.common.metrics_registry import (
    ApiMetricsRegistry,
    METRICS_NAMESPACE_PLATFORM,
)
from fcc_test_contracts.common.logging_channel import get_logger
from fcc_test_contracts.common.principal_resolver import (
    build_local_jwt_config,
    create_principal_resolver,
)
from application.headless.provider_ui_descriptor import (
    UNLICENSED_PROVIDER_ID,
    build_unlicensed_ui_descriptor,
)
from fcc_test_platform.provider_registry import ProviderReferenceResolverRegistry
from application.central_contract.api_contracts import PLATFORM_API_OPERATIONS
from fcc_test_platform.application.provider_ui_descriptor_registry import (
    ProviderUiDescriptorRegistry,
)
from fcc_test_platform.application.central_audit_write_adapter import (
    PostgresCentralAuditWriteAdapter,
)
from fcc_test_platform.application.central_chamber_read_adapter import (
    PostgresCentralChamberReadAdapter,
)
from fcc_test_platform.application.central_chamber_read_service import CentralChamberReadService
from fcc_test_platform.application.central_chamber_write_adapter import (
    PostgresCentralChamberWriteAdapter,
)
from fcc_test_platform.application.central_chamber_write_service import CentralChamberWriteService
from fcc_test_platform.application.chamber_measurement_service import ChamberMeasurementService
from fcc_test_platform.application.central_claim_write_adapter import (
    PostgresCentralClaimWriteAdapter,
)
from fcc_test_platform.application.central_claim_write_service import ClaimWriteService
from fcc_test_platform.application.central_membership_write_adapter import (
    PostgresCentralMembershipWriteAdapter,
)
from fcc_test_platform.application.central_membership_write_service import (
    MembershipWriteService,
)
from fcc_test_platform.application.central_rbac_read_adapter import (
    PostgresCentralRbacReadAdapter,
)
from fcc_test_platform.application.central_rbac_read_service import CentralRbacReadService
from fcc_test_platform.application.central_project_read_adapter import (
    PostgresCentralProjectReadAdapter,
)
from fcc_test_platform.application.central_project_service import CentralProjectService
from fcc_test_platform.application.user_provisioning_service import UserProvisioningService
from fcc_test_platform.application.central_project_write_adapter import (
    PostgresCentralProjectWriteAdapter,
)
from fcc_test_platform.application.central_user_write_adapter import (
    PostgresCentralUserWriteAdapter,
)
from fcc_test_platform.application.central_report_read_adapter import (
    PostgresCentralReportReadAdapter,
)
from fcc_test_platform.application.central_report_write_adapter import (
    PostgresCentralReportWriteAdapter,
)
from fcc_test_platform.application.central_report_service import CentralReportService
from fcc_test_platform.application.central_test_equipment_list_read_adapter import (
    PostgresCentralTestEquipmentListReadAdapter,
)
from fcc_test_platform.application.central_test_equipment_list_write_adapter import (
    PostgresCentralTestEquipmentListWriteAdapter,
)
from fcc_test_platform.application.central_test_equipment_list_service import (
    CentralTestEquipmentListService,
)
from fcc_test_platform.application.central_reference_read_adapter import (
    PostgresCentralReferenceReadAdapter,
)
from fcc_test_platform.application.central_reference_write_adapter import (
    PostgresCentralReferenceWriteAdapter,
)
from fcc_test_platform.application.central_reference_service import CentralReferenceService
from fcc_test_platform.application.chamber_result_ingestion_service import (
    ChamberResultIngestionService,
)
from fcc_test_platform.central_backend_sync_adapter import CentralBackendSyncAdapter
from application.central_contract.central_sync_readiness import PostgresCentralSyncReadinessProbe
from fcc_test_platform.application.central_sync_metrics import (
    CENTRAL_SYNC_READINESS_COUNTER,
    CentralSyncMetrics,
)
from fcc_test_platform.central_db_config import CENTRAL_DB_ENV
from fcc_test_platform.postgres_central_id_resolver import PostgresCentralIdResolver
from fcc_test_platform.postgres_ingestion_writer import PostgresIngestionWriter
from fcc_test_platform.application.central_read_adapter import PostgresCentralReadAdapter
from fcc_test_platform.application.central_read_service import CentralReadService
from fcc_test_platform.application.central_result_selection_adapter import (
    PostgresCentralResultSelectionAdapter,
)
from fcc_test_platform.application.central_result_selection_service import (
    CentralResultSelectionService,
)
from fcc_test_platform.application.central_project_reference_adapter import (
    PostgresCentralProjectReferenceAdapter,
)
from fcc_test_platform.application.central_project_reference_service import (
    CentralProjectReferenceService,
    ProviderResolver,
)
from fcc_test_platform.application.runtime_config import (
    PLATFORM_ALLOW_INSECURE_ENV,
    PLATFORM_AUTH_ENV_PREFIX,
    PlatformApiConfig,
)
from domain.ports.output.platform_database_port import DbConnection
from fcc_test_contracts.common.health_probe_policy import DEPENDENCY_CENTRAL_DB


logger = get_logger('platform_api')

#: Exact auth-mode env-var name, derived from the HttpAuthConfig SSOT (no
#: hardcoded 'FCC_PLATFORM_AUTH_MODE' literal) — surfaced in the secure-by-default
#: error message so operators get the precise variable to set.
_PLATFORM_AUTH_MODE_ENV = HttpAuthConfig.env_keys(PLATFORM_AUTH_ENV_PREFIX)['auth_mode']


__all__ = [
    'PlatformApiRuntime',
    'build_central_connection_factory',
    'create_platform_runtime',
    'create_platform_runtime_from_config',
    'create_platform_app_from_config',
]


@dataclass
class PlatformApiRuntime:
    """Assembled platform read runtime — config + API adapter + metrics.

    OBS-2 phase 3 parity (FE-P0d S5, 2026-05-27) — ``metrics_registry`` shared
    with the API adapter so the ``GET /platform/metrics`` endpoint + HTTP
    middleware observe the same in-process counters. ``namespace='fcc_platform'``.

    멀티챔버 P7/B4 (2026-06-18) — the platform surface now serves a real-time
    chamber progress relay (``/platform/chambers/events``) on top of the HTTP
    read surface, so ``create_platform_runtime_from_config`` composes the
    ``metrics_registry`` with ``enable_websocket=True`` (WS lifecycle
    gauges/counters: 4-state / 4-reason) and owns a ``progress_broadcaster``
    fan-out engine. ADR-0015 Option B — no new outbound HTTP/WS dependency.
    """

    config: PlatformApiConfig
    api_adapter: object
    metrics_registry: ApiMetricsRegistry
    # 멀티챔버 P7/B4 — central progress relay fan-out engine. None on a runtime
    # composed without the relay (back-compat); production wires one.
    progress_broadcaster: object = None
    # 신원 축 EMS 정합 (2026-08-21) — the process-local access-token revocation
    # list. Held on the runtime because BOTH the login service (writer) and the
    # principal resolver (reader) must be handed the SAME object.
    revocation_list: object = None

    def create_router(self):
        from fcc_test_platform.api.platform_routes import create_platform_router
        return create_platform_router(
            self.api_adapter,
            principal_resolver=create_principal_resolver(
                self.config.auth, revocation_list=self.revocation_list,
            ),
        )

    def dispose(self) -> None:  # symmetry with HeadlessApiRuntime
        # Tear down the progress broadcaster first (closes WS subscriber queues)
        # so a re-composed runtime starts with no dangling subscriptions.
        broadcaster = self.progress_broadcaster
        if broadcaster is not None:
            try:
                broadcaster.dispose()
            except Exception:  # pragma: no cover — never raise on shutdown
                pass
        # Reset the in-process metric counters so a re-composed runtime starts
        # from a clean slate (test isolation, mirror of HeadlessApiRuntime).
        try:
            self.metrics_registry.reset()
        except Exception:  # pragma: no cover — never raise on shutdown
            pass


_spraying_logger = get_logger('platform_api')


def _build_central_connection_factory(database_url: str) -> Callable[[], DbConnection]:
    """Return a ``() -> DbConnection`` factory backed by psycopg (lazy import).

    Each call opens a fresh connection; the read adapter closes it after the
    single SELECT, so no connection is shared across reads. ``psycopg`` is
    imported here only (frozen-exe safety — see module docstring).
    """
    if not database_url:
        raise ValueError('database_url is required to build a connection factory')
    import psycopg  # lazy — keeps desktop frozen-exe free of the PostgreSQL driver

    def _connect() -> DbConnection:
        return psycopg.connect(database_url)

    return _connect


def build_central_connection_factory(database_url: str) -> Callable[[], DbConnection]:
    """Public SSOT for turning a central DSN into a ``() -> DbConnection`` factory.

    The runtime composition (``create_platform_runtime``) and operator tooling
    (``scripts/rekey_central_ingest_evidence_cli.py collect`` — ADR-0005 E4 live
    coverage) share this single entry point so neither embeds its own psycopg
    connect logic. Delegates to ``_build_central_connection_factory`` (lazy
    psycopg import — frozen-exe safety preserved).
    """
    return _build_central_connection_factory(database_url)


def create_platform_runtime(
    config: PlatformApiConfig,
    *,
    connection_factory: Optional[Callable[[], DbConnection]] = None,
    project_reference_provider_resolver: Optional[ProviderResolver] = None,
) -> PlatformApiRuntime:
    """Assemble the platform read runtime.

    Args:
        config: platform settings (central DSN + auth).
        connection_factory: optional pre-built ``() -> DbConnection``. Production
            leaves this ``None`` so a psycopg factory is built from
            ``config.central.database_url``; tests inject a SQLite/fake factory so
            no psycopg dependency or live PostgreSQL is required.
        project_reference_provider_resolver: provider-owned adapter registry or
            resolver. The generic platform composition never imports a provider
            implementation; callers that expose reference publication must
            inject the provider boundary explicitly.

    Raises:
        ValueError: when the central DB is not configured (no DSN and no injected
            factory) — loud-fail so a misconfigured deployment never serves a
            read API that 503s on every request.
    """
    if connection_factory is None:
        if not config.central.enabled:
            raise ValueError(
                'platform read API requires a central DSN: set FCC_CENTRAL_DB_URL '
                'or inject a connection_factory. Refusing to build a read API that '
                'cannot reach the central read model.'
            )
        config.central.parsed_dsn()  # loud-fail on a bad scheme before binding
        connection_factory = _build_central_connection_factory(config.central.database_url)

    if project_reference_provider_resolver is None:
        # The composition root is the one permitted crossing point for provider
        # implementations. The application service still receives only the
        # dependency-free natural-id registry, never this provider import.
        from domain.services.unlicensed.project_result_reference import (
            ConductedDutyReferenceAdapter,
        )

        project_reference_provider_resolver = ProviderReferenceResolverRegistry({
            UNLICENSED_PROVIDER_ID: ConductedDutyReferenceAdapter(),
        })

    read_adapter = PostgresCentralReadAdapter(connection_factory)
    read_service = CentralReadService(read_adapter)

    # FE-P8 (2026-05-28): audit ledger writer — transactional primitive that
    # joins the caller's open cursor. ONE instance shared across the claim
    # write adapter + the membership write adapter so every platform write
    # audits through the same code path.
    audit_writer = PostgresCentralAuditWriteAdapter()

    # FE-P3-write + FE-P8 audit: the claim ledger writer shares the same
    # central connection factory and is wired with the audit writer so the
    # audit ``claim.acquired`` / ``claim.released`` row joins the same
    # transaction as the primary INSERT.
    claim_write_adapter = PostgresCentralClaimWriteAdapter(
        connection_factory, audit_writer=audit_writer,
    )
    claim_write_service = ClaimWriteService(claim_write_adapter)

    # FE-P8 RBAC read — single SELECT per authz check via the
    # project_member_permissions view. The PlatformApiAdapter authorize path
    # unions membership-granted permissions with token permissions.
    rbac_read_adapter = PostgresCentralRbacReadAdapter(connection_factory)
    rbac_read_service = CentralRbacReadService(rbac_read_adapter)
    user_write_adapter = PostgresCentralUserWriteAdapter(connection_factory)
    user_provisioning_service = UserProvisioningService(user_write_adapter)

    # FE-P8 membership write — UPSERT/DELETE on project_membership atomic with
    # the audit INSERT. The membership write adapter takes the shared audit
    # writer (REQUIRED; the adapter constructor refuses None).
    membership_write_adapter = PostgresCentralMembershipWriteAdapter(
        connection_factory, audit_writer=audit_writer,
    )
    membership_write_service = MembershipWriteService(
        membership_write_adapter, rbac_read_service,
    )

    # Web sample inventory — one current/revision service for CRUD, as-of reads,
    # and export. Excel enters only at the renderer edge; no upload parser is
    # composed into the platform API. Construct the read adapter before the
    # project detail adapter so project detail delegates its sample portion to
    # this single inventory projection.
    from fcc_test_platform.application.central_sample_inventory_read_adapter import (
        PostgresCentralSampleInventoryReadAdapter,
    )
    from fcc_test_platform.application.central_sample_inventory_service import (
        CentralSampleInventoryService,
    )
    from fcc_test_platform.application.central_sample_inventory_write_adapter import (
        PostgresCentralSampleInventoryWriteAdapter,
    )
    from fcc_test_platform.application.sample_inventory_export_service import (
        SampleInventoryExportService,
    )
    from infrastructure.excel.sample_inventory_exporter import (
        SampleInventoryExcelExporter,
    )
    sample_inventory_read_adapter = PostgresCentralSampleInventoryReadAdapter(
        connection_factory,
    )
    sample_inventory_service = CentralSampleInventoryService(
        sample_inventory_read_adapter,
        PostgresCentralSampleInventoryWriteAdapter(connection_factory),
    )
    sample_export_service = SampleInventoryExportService(
        sample_inventory_service,
        sample_inventory_read_adapter,
        SampleInventoryExcelExporter(),
    )

    # Phase 1 (2026-06-22) — 프로젝트 진입층. 신규 생성 시 project + users +
    # project_admin membership/audit 를 같은 connection/cursor 에서 커밋한다.
    project_read_adapter = PostgresCentralProjectReadAdapter(
        connection_factory,
        sample_inventory_read_port=sample_inventory_read_adapter,
    )
    # 신규 생성은 project + users(JIT) + project_admin membership/audit 를 같은
    # connection/cursor 에서 원자적으로 커밋한다 → audit_writer 주입 필수. JIT
    # user_write_adapter 는 위(UserProvisioningService 배선)에서 이미 합성됐다.
    project_write_adapter = PostgresCentralProjectWriteAdapter(
        connection_factory, audit_writer=audit_writer,
    )
    project_service = CentralProjectService(
        project_read_adapter, project_write_adapter, membership_write_service,
        user_write_port=user_write_adapter,
    )

    # Phase G (2026-06-23) — test_reports 성적서. read/write 어댑터 + 프로젝트 read
    # 어댑터(management_number → report_number 파생 + citation 조립)를 같은 central
    # connection factory 로 공유. report_number 는 저장 안 함(서비스가 파생).
    report_read_adapter = PostgresCentralReportReadAdapter(connection_factory)
    report_write_adapter = PostgresCentralReportWriteAdapter(connection_factory)
    report_service = CentralReportService(
        report_read_adapter, report_write_adapter, project_read_adapter,
    )

    # 성적서 §6 장비목록 (2026-08-07) — read/write 어댑터 + 프로젝트 read 어댑터를
    # 같은 central connection factory 로 공유한다. 프로젝트 read 는 귀속 확인(404)
    # 용이고, 두 어댑터는 psycopg 를 직접 import 하지 않는다(주입 factory).
    equipment_list_read_adapter = PostgresCentralTestEquipmentListReadAdapter(
        connection_factory,
    )
    equipment_list_write_adapter = PostgresCentralTestEquipmentListWriteAdapter(
        connection_factory,
    )
    equipment_list_service = CentralTestEquipmentListService(
        equipment_list_read_adapter,
        equipment_list_write_adapter,
        project_read_adapter,
        report_read_adapter,
    )

    # 멀티챔버 P2 — chamber availability read + registry/heartbeat write share the
    # same central connection factory. The read service derives OFFLINE against
    # an injected clock (default wall-clock UTC); the write service appends to the
    # chamber_heartbeat_events ledger / upserts chamber_nodes.
    chamber_read_adapter = PostgresCentralChamberReadAdapter(connection_factory)
    chamber_read_service = CentralChamberReadService(chamber_read_adapter)
    chamber_write_adapter = PostgresCentralChamberWriteAdapter(connection_factory)
    chamber_write_service = CentralChamberWriteService(chamber_write_adapter)

    # Cross-session result selection/reference boundary (M1-M5). These
    # adapters are generic and share the already configured central connection
    # factory; compose them before the chamber service because the chamber
    # service resolves trusted reference snapshots before forwarding work.
    result_selection_port = PostgresCentralResultSelectionAdapter(connection_factory)
    result_selection_service = CentralResultSelectionService(result_selection_port)
    project_result_reference_service = CentralProjectReferenceService(
        PostgresCentralProjectReferenceAdapter(connection_factory),
        selection_port=result_selection_port,
        provider_resolver=project_reference_provider_resolver,
    )

    # 멀티챔버 P5 — 중앙 측정 프록시. 웹은 중앙 1곳만 인증; 이 서비스가 챔버 가용성을
    # P2 read service 로 IDLE 게이트한 뒤 base_url 을 조회해 노드 Session API 로 forward
    # 한다. forward 어댑터는 stdlib urllib + outbound_http traceparent SSOT(httpx 금지).
    # ``HttpChamberProxyAdapter`` 는 infrastructure → lazy import(PlatformApiAdapter 와
    # 동일 패턴, frozen-exe 표면 최소화).
    from infrastructure.adapters.driven.chamber_proxy_adapter import (
        HttpChamberProxyAdapter,
    )
    # timeout/재시도는 config 의 ChamberProxyPolicy SSOT 에서 파생(env override —
    # FCC_PLATFORM_CHAMBER_PROXY_*). 기본 인스턴스화 대신 정책 명시 주입(하드코딩 제거).
    chamber_measurement_service = ChamberMeasurementService(
        chamber_read_service,
        HttpChamberProxyAdapter(policy=config.chamber_proxy_policy),
        sample_inventory_service=sample_inventory_service,
        project_reference_service=project_result_reference_service,
    )

    # AuthZ. A configured auth mode yields a principal resolver + access policy;
    # auth disabled yields neither. Unlike the per-engineer headless surface, the
    # platform surface serves cross-engineer **central** coverage/claims, so it is
    # secure-by-default: refuse to build an unauthenticated read API unless an
    # operator explicitly opts in via FCC_PLATFORM_ALLOW_INSECURE (local dev),
    # which is logged loudly. (A per-operation policy without a resolved principal
    # would otherwise deny every request, so access_policy stays None when open.)
    resolver = create_principal_resolver(config.auth)
    access_policy = ApiAccessPolicy(PLATFORM_API_OPERATIONS) if resolver is not None else None
    if access_policy is None:
        if not config.allow_insecure:
            raise ValueError(
                'platform read API refuses to serve cross-engineer central '
                'coverage/claims with auth disabled. Set '
                f'{_PLATFORM_AUTH_MODE_ENV} (trusted_headers|oidc_jwt) for '
                f'production, or {PLATFORM_ALLOW_INSECURE_ENV}=1 for local dev only.'
            )
        logger.warning(
            'Platform read API running WITHOUT auth (%s set) — cross-engineer '
            'central coverage/claims are exposed unauthenticated. Local/dev only; '
            'production MUST configure an auth mode.',
            PLATFORM_ALLOW_INSECURE_ENV,
        )

    # 멀티챔버 P7/B4 (2026-06-18): namespace=``fcc_platform`` + WS ENABLED. The
    # platform surface now serves a real-time chamber progress relay
    # (/platform/chambers/events) on top of the HTTP read surface, so the metrics
    # registry exposes the WS lifecycle gauges/counters (4-state / 4-reason). One
    # instance shared with the API adapter so the metric middleware +
    # ``/platform/metrics`` endpoint + WS handler observe identical counters.
    # 관측성(2026-06-20) — 챔버 가용성 derived gauge family 를 선언(이름 SSOT =
    # chamber_metrics.CHAMBER_GAUGE_FAMILIES, alert-parity 테스트도 동일 선언 사용).
    from fcc_test_platform.application.chamber_metrics import (
        CHAMBER_GAUGE_FAMILIES,
        ChamberMetricsCollector,
    )
    metrics_registry = ApiMetricsRegistry(
        namespace=METRICS_NAMESPACE_PLATFORM, enable_websocket=True,
        gauge_families=CHAMBER_GAUGE_FAMILIES,
        counter_families=(CENTRAL_SYNC_READINESS_COUNTER,),
    )
    central_sync_metrics = CentralSyncMetrics(metrics_registry)

    # 멀티챔버 P7/B4 — central progress relay fan-out engine (ADR-0015 Option B).
    # heartbeat ingest publishes in_use progress here; the WS endpoint subscribes
    # and fans out to web clients. stdlib asyncio only (no new outbound dep).
    from infrastructure.adapters.driven.chamber_progress_broadcaster import (
        ChamberProgressBroadcaster,
    )
    progress_broadcaster = ChamberProgressBroadcaster()

    # WEB-PROVIDER-UI-0: provider UI descriptor registry. The composition root is
    # the SINGLE place that imports the provider builder — the platform service /
    # route layer stays free of provider internals (schema-driven renderer). The
    # current repo registers the in-process Unlicensed descriptor; a future
    # multi-repo deployment would fetch it from the provider's
    # GET /headless/ui-descriptor (or a registry artifact fallback).
    provider_ui_descriptor_registry = ProviderUiDescriptorRegistry({
        UNLICENSED_PROVIDER_ID: build_unlicensed_ui_descriptor().to_dict(),
    })

    # scrape 시점 챔버 gauge 갱신기 — chamber_read_service(가용성 SSOT)에서 status별
    # 카운트 + 최대 heartbeat age 를 registry 에 set.
    chamber_metrics_collector = ChamberMetricsCollector(
        chamber_read_service, metrics_registry,
    )

    # Result ingestion is central-owned. The chamber-side process sends only
    # an authenticated HTTP envelope; this is the sole composition point that
    # wires the existing parent-first/idempotent writer and resolver.
    # The provider identity is fail-closed on purpose, and must stay that way.
    # `provider_id` is not a label: PostgresCentralIdResolver derives central
    # session ids as uuid5(ns, f'{provider_id}:{local_session_id}') and the
    # ingestion idempotency key is (provider_id, provider_session_id). Binding
    # to a *guessed* provider therefore writes a whole namespace of primary
    # keys that are wrong-but-plausible; setting the real value later changes
    # every previously ingested uuid5, so the same physical session re-ingests
    # as a different central row and the ledger splits. Repairing that is an
    # ADR-0005 re-key, not an edit.
    #
    # A default is the one failure mode nothing catches: the three downstream
    # constructors already reject an *empty* provider_id, so an unset variable
    # would have failed loudly anyway. Substituting a plausible value removes
    # that signal without removing the misconfiguration.
    #
    # The chamber side states the same rule for the same reason (see
    # `central_backend_sync_composition`); the central side owns the durable
    # ledger, so if either end deserves the stricter guard it is this one. The
    # dev/compose contract already supplies the value explicitly
    # (`infra/docker-compose.central.yml`, `infra/central/central.env.example`),
    # so failing closed costs that path nothing.
    if not config.central.provider_id:
        raise ValueError(
            f'provider_id is required for central result ingestion — set '
            f'{CENTRAL_DB_ENV["provider_id"]}. Envelopes and central session '
            f'uuids are provider-scoped; a missing provider_id corrupts '
            f'central identity.'
        )
    result_ingestion_writer = PostgresIngestionWriter(connection_factory)
    result_ingestion_resolver = PostgresCentralIdResolver(
        provider_id=config.central.provider_id,
        connection_factory=connection_factory,
    )
    result_ingestion_backend = CentralBackendSyncAdapter(
        provider_id=config.central.provider_id,
        central_id_resolver=result_ingestion_resolver,
        ingestion_writer=result_ingestion_writer,
    )
    chamber_result_ingestion_service = ChamberResultIngestionService(
        result_ingestion_backend,
        provider_id=config.central.provider_id,
        readiness_probe=PostgresCentralSyncReadinessProbe(
            connection_factory, provider_id=config.central.provider_id,
        ),
        readiness_observer=central_sync_metrics.observe,
    )

    # Wave 3 참조 카탈로그 (2026-08-07) — 저작(후보 생성/게시)과 챔버 배달(bundle)
    # 을 같은 central connection factory 로 공유한다. 이 조립이 없으면 참조 4
    # operation 이 전부 `_require_reference_service` 에서 RuntimeError 로 죽는다 —
    # 라우트도 서비스도 어댑터도 존재하는데 **합성만** 빠져 있어 표면 전체가 도달
    # 불가였다(2026-08-08 실측). `tests/test_platform_reference_composition.py` 가
    # 어댑터가 받는 협력자와 여기서 넘기는 협력자의 상등을 AST 로 파생해 단언하므로,
    # 다음 협력자가 같은 방식으로 조용히 빠질 수 없다.
    #
    # `bundle_provider_id` 는 위에서 이미 fail-closed 검증된 `config.central.provider_id`
    # 를 그대로 쓴다 — 새 env 를 만들지 않는 이유는 편의가 아니라 **같은 사실**이기
    # 때문이다: 노드는 chamber id 하나로 pull 하고 provider 를 지명할 수 없어야 하며,
    # 그 근거는 결과 유입 경로가 provider 를 배포 설정에서 받는 것과 동일하다.
    reference_read_adapter = PostgresCentralReferenceReadAdapter(connection_factory)
    reference_write_adapter = PostgresCentralReferenceWriteAdapter(connection_factory)
    # `offered_provider_ids` 는 **화면이 제공하는 provider 집합**이고, 여기가
    # 그 사실과 중앙 등록부를 동시에 쥐는 유일한 지점이다(시드가 같은 이유로
    # `assert_provider_is_selectable` 를 갖는다). 서비스에 레지스트리 객체가
    # 아니라 **호출 가능한 것**을 주는 이유: 서비스가 필요한 것은 "이 id 를
    # 화면이 건넸는가" 하나뿐이고, 레지스트리를 통째로 주면 그 서비스가
    # 렌더링 관심사에 대한 의견을 갖게 된다.
    reference_service = CentralReferenceService(
        reference_read_adapter,
        reference_write_adapter,
        bundle_provider_id=config.central.provider_id,
        offered_provider_ids=provider_ui_descriptor_registry.provider_ids,
    )

    # plot-dual-custody ① (2026-08-09) — 플롯 원본 보관 판정 수신 + 프로젝트 축 조회.
    # 중앙은 회사 파일서버도 챔버 PC 로컬도 열 수 없으므로 **판정하지 않는다**; 이
    # 서비스는 노드가 내린 판정을 받아 보관하고 프로젝트 축으로 접어 돌려준다. 같은
    # central connection factory 를 공유하며(read/write 어댑터 2종), 이 조립이 빠지면
    # 세 operation 이 `_require_artifact_custody_service` 에서 RuntimeError 로 죽는다 —
    # 참조 카탈로그가 정확히 그 상태로 착지했던 전례가 바로 위 문단에 적혀 있다.
    from fcc_test_platform.application.central_artifact_custody_read_adapter import (
        PostgresCentralArtifactCustodyReadAdapter,
    )
    from fcc_test_platform.application.central_artifact_custody_write_adapter import (
        PostgresCentralArtifactCustodyWriteAdapter,
    )
    from fcc_test_platform.application.central_artifact_custody_service import (
        CentralArtifactCustodyService,
    )
    artifact_custody_service = CentralArtifactCustodyService(
        read_port=PostgresCentralArtifactCustodyReadAdapter(connection_factory),
        write_port=PostgresCentralArtifactCustodyWriteAdapter(connection_factory),
    )

    # Phase 6 (2026-06-23) — time-weighted progress read. The rollup query joins
    # published_plan_expectation to measurement coverage on the F1 provider-scoped
    # key (project, provider, condition_hash) through the same central connection
    # factory; the existing coverage_by_condition_hash view (and its FE-P0a seals)
    # is untouched.
    from fcc_test_platform.application.central_progress_read_adapter import (
        PostgresCentralProgressReadAdapter,
    )
    progress_read_adapter = PostgresCentralProgressReadAdapter(connection_factory)

    # Ops probes (2026-07-20) — the readiness endpoint's dependency set. The
    # platform surface has exactly ONE hard dependency: the central DB it was
    # just composed against (a missing DSN already loud-fails above, so by this
    # point the dependency is declared). The probe shares the same connection
    # factory as every read adapter, so readiness observes the *same* path a
    # real request would take rather than a parallel connection with its own
    # credentials that could succeed while the real one fails.
    from fcc_test_platform.application.central_health_adapter import (
        PostgresCentralHealthAdapter,
    )
    from fcc_test_platform.application.readiness_service import ReadinessService
    # ── 로컬 신원 (2026-08-21) ──────────────────────────────────────────────
    # Built ONLY in local_jwt mode. In every other mode the five auth operations
    # stay unwired and raise loudly if reached — half-answering an auth endpoint
    # is worse than not having it.
    #
    # ⚠️ The revocation list is SHARED between the login service (which writes to
    # it on logout) and the principal resolver (which reads it on every request).
    # Two instances would mean logout revoked a token nothing ever checked.
    local_auth_service = None
    revocation_list = None
    if (config.auth.auth_mode or '').strip().lower() == AUTH_MODE_LOCAL_JWT:
        from fcc_test_contracts.common.local_identity import TOKEN_TYPE_ACCESS  # noqa: F401
        from fcc_test_platform.application.local_auth_service import (
            LocalAuthService,
            LoginSprayingDetector,
            TokenRevocationList,
        )
        from fcc_test_platform.application.local_user_store import PostgresLocalUserStore
        from fcc_test_platform.application.password_hasher import BcryptPasswordHasher

        local_jwt_config = build_local_jwt_config(config.auth)
        password_hasher = BcryptPasswordHasher()
        # ⚠️ Pay the dummy-hash construction here. On the login path it would land
        # on the FIRST unknown-email attempt after a restart, making that one
        # request 1.92× slower than a known-email failure — an enumeration bit
        # that survives every axis the service equalises.
        password_hasher.warm()
        revocation_list = TokenRevocationList()
        # ⚠️ ``audit_writer`` 는 관리자 잠금 해제(2026-08-23) 하나 때문에 필요하고,
        # 그 하나가 없으면 store 가 **거부한다**(무감사 행정 조치 금지). 형제 셋
        # (membership · claim · JIT provisioning)이 같은 어댑터를 같은 이유로 받는다 —
        # 감사 행이 primary write 와 **같은 트랜잭션**에 있어야 «변경은 됐는데 감사가
        # 없다» 가 구조적으로 불가능해진다.
        local_user_store = PostgresLocalUserStore(
            connection_factory, audit_writer=audit_writer,
        )
        # 리프레시 회전 subject 상한 (2026-08-23) — **전용 limiter**.
        # ⚠️ 미들웨어의 것을 넘기면 미인증 공격자가 쓰레기 bearer 를 돌려 피해자의
        # 회전 버킷을 축출시키고 예산을 만석으로 되돌린다(자격증명 축이 같은 자리에서
        # 값을 치르고 배운 것을 그대로 물려받는다).
        from fcc_test_contracts.common.refresh_rotation_throttle import (
            RefreshRotationThrottle,
        )
        from fcc_test_contracts.common.rate_limit import (
            FixedWindowRateLimiter as _RotationLimiter,
        )
        # ⚠️ ``config.rate_limit`` 은 미들웨어가 받는 **바로 그 값**이다(아래 route
        # 조립이 같은 표현을 넘긴다). 여기서 다른 출처를 읽으면 kill-switch 를 끈
        # 배포에서 이 tier 만 계속 과금하는 상태가 조용히 성립한다.
        rotation_throttle = RefreshRotationThrottle(
            policy=config.rate_limit,
            limiter=_RotationLimiter(),
            secret=local_jwt_config.secret,
        )
        local_auth_service = LocalAuthService(
            store=local_user_store,
            rotation_throttle=rotation_throttle,
            hasher=password_hasher,
            jwt_config=local_jwt_config,
            clock=lambda: datetime.now(timezone.utc),
            revocation_list=revocation_list,
            # ⚠️ The spraying detector keys its fingerprints with the SIGNING
            # secret rather than a fresh random value: the digests must be stable
            # across a restart or the window resets every deploy, and the secret
            # is already the one value this process holds that an attacker does
            # not. Nothing derived from it is ever emitted.
            spraying_detector=LoginSprayingDetector(
                secret=local_jwt_config.secret,
                # ⚠️ **관측자 없는 탐지기는 조용한 no-op 이다.** 착지 직전까지 이 인자가
                # 비어 있었고(적대 평가 R2), 그 상태에서는 스프레이가 탐지돼도 로그도
                # 메트릭도 경보도 나오지 않았다 — 클래스가 동작한다는 것과 무언가가
                # 듣고 있다는 것은 다른 문장이다.
                #
                # ⚠️ 실리는 것은 **HMAC 다이제스트와 개수뿐**이다. 원본 IP·user-agent·
                # 이메일은 탐지기가 애초에 보관하지 않으므로 이 경보가 PII 채널이 될 수
                # 없다.
                on_suspected=lambda source, accounts: _spraying_logger.warning(
                    'login spraying suspected: source_digest=%s distinct_accounts=%s',
                    source, accounts,
                ),
            ),
        )
        # ⚠️ Without this nobody can log in at all — the users table starts empty and
        # every write path requires an authenticated principal. Creates ONLY when
        # there is no local user yet, and always with a pending password change.
        _bootstrap_local_admin_from_env(local_user_store, os.environ)

    readiness_service = ReadinessService({
        DEPENDENCY_CENTRAL_DB: PostgresCentralHealthAdapter(connection_factory).ping,
    })

    from fcc_test_platform.api.platform_routes import PlatformApiAdapter
    api_adapter = PlatformApiAdapter(
        read_service, access_policy=access_policy, metrics_registry=metrics_registry,
        claim_write_service=claim_write_service,
        rbac_read_service=rbac_read_service,
        membership_write_service=membership_write_service,
        provider_ui_descriptor_registry=provider_ui_descriptor_registry,
        chamber_read_service=chamber_read_service,
        chamber_write_service=chamber_write_service,
        chamber_measurement_service=chamber_measurement_service,
        chamber_result_ingestion_service=chamber_result_ingestion_service,
        progress_broadcaster=progress_broadcaster,
        chamber_metrics_collector=chamber_metrics_collector,
        project_service=project_service,
        sample_inventory_service=sample_inventory_service,
        sample_export_service=sample_export_service,
        progress_read_port=progress_read_adapter,
        report_service=report_service,
        equipment_list_service=equipment_list_service,
        user_provisioning_service=user_provisioning_service,
        readiness_service=readiness_service,
        reference_service=reference_service,
        result_selection_service=result_selection_service,
        project_result_reference_service=project_result_reference_service,
        artifact_custody_service=artifact_custody_service,
        local_auth_service=local_auth_service,
    )
    return PlatformApiRuntime(
        config=config, api_adapter=api_adapter, metrics_registry=metrics_registry,
        progress_broadcaster=progress_broadcaster,
        revocation_list=revocation_list,
    )


def create_platform_runtime_from_config(
    config: PlatformApiConfig,
    *,
    connection_factory: Optional[Callable[[], DbConnection]] = None,
    project_reference_provider_resolver: Optional[ProviderResolver] = None,
) -> PlatformApiRuntime:
    """Alias kept for symmetry with the headless ``*_from_config`` naming."""
    return create_platform_runtime(
        config,
        connection_factory=connection_factory,
        project_reference_provider_resolver=project_reference_provider_resolver,
    )


def create_platform_app_from_config(
    runtime: PlatformApiRuntime,
    config: PlatformApiConfig,
    *,
    lifespan=None,
):
    """Create the FastAPI app for an assembled platform runtime.

    ``lifespan`` is forwarded to the FastAPI constructor so the ASGI entrypoint
    disposes ``runtime`` on shutdown via ``@asynccontextmanager`` (legacy
    event hooks are forbidden by ``TestNoDeprecatedFastApiInWebSurface``).
    """
    from fcc_test_platform.api.platform_routes import create_platform_app

    return create_platform_app(
        runtime.api_adapter,
        # ⚠️ The SAME revocation list the login service writes to. Passing None
        # here would make logout a no-op that still answers 200.
        principal_resolver=create_principal_resolver(
            config.auth, revocation_list=getattr(runtime, 'revocation_list', None),
        ),
        lifespan=lifespan,
        # Inbound throttle (2026-07-19) — env-sourced policy, enabled unless the
        # operator sets FCC_PLATFORM_RATE_LIMIT_ENABLED=0. Keyed by the same
        # trusted-header carrier the auth config declares.
        rate_limit_policy=config.rate_limit,
        rate_limit_subject_header=config.auth.trusted_subject_header(),
        # Account-axis throttle (2026-08-22). ⚠️ In ``local_jwt`` mode the login
        # path carries neither a trusted subject header nor a bearer token, so the
        # middleware's identity tier is *absent* and every tester behind one NAT
        # shares a single peer bucket — the shape of the 2026-08-11 EMS outage, and
        # below the OWASP standard regardless of NAT. The route boundary keys the
        # missing axis on the attempted account, digested with this secret.
        #
        # ⚠️ Derived from the SAME ``build_local_jwt_config`` the login service
        # uses, not re-read from env: two readers would let the throttle key on a
        # value the service never signs with, and the digests would silently reset.
        # ``None`` in every non-local mode, where there is no password login at all.
        credential_secret=_local_jwt_secret_or_none(config),
        **config.app_options(),
    )


def _local_jwt_secret_or_none(config: PlatformApiConfig):
    """The local JWT signing secret, or ``None`` outside ``local_jwt`` mode.

    Kept as a named function rather than an inline conditional so the mode gate is
    one expression with one owner — an inline ``if`` here and another one in
    ``create_platform_runtime`` is exactly how a surface ends up throttling in a
    mode that has no local login, or not throttling in the one that does.
    """
    if (config.auth.auth_mode or '').strip().lower() != AUTH_MODE_LOCAL_JWT:
        return None
    return build_local_jwt_config(config.auth).secret


#: Bootstrap-administrator env names. Read once at composition, never stored.
ENV_BOOTSTRAP_ADMIN_EMAIL = 'FCC_PLATFORM_BOOTSTRAP_ADMIN_EMAIL'
ENV_BOOTSTRAP_ADMIN_PASSWORD = 'FCC_PLATFORM_BOOTSTRAP_ADMIN_PASSWORD'


def _bootstrap_local_admin_from_env(store, environ) -> None:
    """Create the first local administrator when the deployment asks for one.

    ⚠️ **Failures here are loud.** A refused password (policy violation) or an
    unreachable database aborts composition rather than starting an API that
    nobody can authenticate against. The alternative — swallow and continue —
    produces a system that boots green and is permanently unusable, with the
    cause buried in a log line nobody reads.

    ⚠️ The password is read from ``environ`` and handed straight to the hasher.
    It is never logged, never projected into ``as_options()``, and never stored
    anywhere but as a bcrypt hash.
    """
    from fcc_test_contracts.common.env_loaders import read_text
    from fcc_test_platform.application.local_auth_service import bootstrap_local_admin
    from fcc_test_platform.application.password_hasher import BcryptPasswordHasher

    from fcc_test_contracts.common.identity import normalize_email

    email = read_text(environ, ENV_BOOTSTRAP_ADMIN_EMAIL)
    password = read_text(environ, ENV_BOOTSTRAP_ADMIN_PASSWORD)
    if not email or not password:
        return
    # ⚠️ **"Configured but unusable" must not look like "not configured."**
    # ``normalize_email`` folds anything unstorable to ``''`` (2026-08-22), and
    # ``os.environ`` decodes with ``surrogateescape`` — so a ``.env`` saved in CP949
    # on a Korean Windows box arrives as a lone-surrogate string and normalises
    # away. Before the fold that raised ``UnicodeEncodeError`` here and aborted
    # composition; after it, ``bootstrap_local_admin`` returned ``None`` and the
    # deployment booted green **with zero administrators and not even a log line**
    # (adversarial review round 2). This function's whole contract is that failures
    # here are loud — so the fold gets an explicit re-raise rather than inheriting
    # the silent path.
    if not normalize_email(email):
        raise ValueError(
            f'{ENV_BOOTSTRAP_ADMIN_EMAIL} is set but is not a storable identity '
            '(it contains characters that cannot be encoded or stored — check the '
            'file encoding of the env file). Refusing to boot an API nobody can '
            'authenticate against.'
        )
    bootstrap_local_admin(
        store=store,
        hasher=BcryptPasswordHasher(),
        email=email,
        password=password,
        now=datetime.now(timezone.utc),
    )
