"""Platform read API driving adapter (FE-P0d, 2026-05-27).

FastAPI is imported only inside ``create_platform_router`` / ``create_platform_app``
so the repository keeps running its unit tests without the web dependency.

``PlatformApiAdapter`` mirrors ``HeadlessApiAdapter`` (authorize → delegate) but
delegates to ``CentralReadService`` (central read model) instead of local-SQLite
services. Error mapping: bad uuid → 400, AuthZ denied → 403, central backend
failure (``CentralReadError``) → 503.
"""
from __future__ import annotations

from functools import wraps
from typing import Optional, TYPE_CHECKING

from fcc_test_contracts.common.access_policy import (
    API_PERMISSION_ADMIN,
    API_PERMISSION_WILDCARD,
    REASON_PASSWORD_CHANGE_REQUIRED,
    ApiAccessPolicy,
    ApiPrincipal,
)
from fcc_test_contracts.common.local_identity import LocalTokenError
from fcc_test_contracts.common.logging_channel import get_logger
from fcc_test_contracts.common.correlation import current_request_id
from fcc_test_platform.application.local_auth_service import (
    InvalidCredentialsError,
    LocalAccountNotFoundError,
    LocalAuthService,
    PasswordChangeRequiredError,
    RefreshRotationLimitedError,
)
from fcc_test_platform.application.local_user_store import LocalUserStoreError
from fcc_test_platform.application.password_hasher import PasswordRejected
from fcc_test_contracts.common.api_error_codes import (
    ERROR_CODE_TITLES,
    UNCLASSIFIED_ERROR_CODE,
    ErrorCode,
    ProblemDetails,
    build_problem_details,
    resolve_error_code,
    status_for_code,
)
from fcc_test_contracts.common.metrics_registry import (
    ApiMetricsRegistry,
    WS_CLOSE_REASON_DENIED,
    WS_CLOSE_REASON_ERROR,
    WS_CLOSE_REASON_NORMAL,
    WS_CLOSE_REASON_TIMEOUT,
    WS_STATE_CLOSING,
    WS_STATE_CONNECTING,
    WS_STATE_OPEN,
    build_route_pattern_index,
    lookup_operation as _lookup_route_operation,
)
from fcc_test_kernel.domain.models.chamber_node import (
    ChamberNodeStatus,
    ChamberProgress,
    ChamberProgressEvent,
)
from fcc_test_platform.domain.ports.output.chamber_progress_broadcast_port import (
    ChamberProgressBroadcastPort,
)
from fcc_test_platform.application.central_artifact_custody_service import (
    ArtifactCustodyReportRejected,
)
from fcc_test_platform.domain.ports.output.central_artifact_custody_port import (
    ArtifactCustodyNotFoundError,
    ArtifactCustodyProviderNotFoundError,
    CentralArtifactCustodyError,
)
from fcc_test_contracts.common.operator_notice import (
    CHANNEL_PLATFORM_API,
    announce,
)
from fcc_test_kernel.application.central_contract.api_contracts import (
    ARTIFACT_CUSTODY_REPORT_SCHEMA_VERSION,
    PLATFORM_API_CONTRACT_VERSION,
    PLATFORM_API_OPERATIONS,
    PLATFORM_API_ROUTES,
    PLATFORM_INTERNAL_RBAC_ROUTES,
    PLATFORM_API_TITLE,
    PLATFORM_NEXT_CURSOR_HEADER,
    ProjectResultReferenceRequestUnprocessableError,
    validate_project_result_reference_request,
)
from fcc_test_platform.application.central_claim_write_service import (
    ClaimConflictError,
    ClaimPairingError,
    ClaimWriteService,
)
from fcc_test_platform.application.central_chamber_read_service import CentralChamberReadService
from fcc_test_platform.application.central_chamber_write_service import CentralChamberWriteService
from fcc_test_platform.application.chamber_measurement_service import (
    ChamberMeasurementService,
    ChamberNotAvailableError,
    ChamberNotFoundError,
    ChamberProxyError,
)
from fcc_test_platform.application.central_project_service import (
    CentralProjectService,
    ProjectModelUnresolvedError,
    ProjectNotFoundError,
)
from fcc_test_platform.application.central_read_service import CentralReadService
from fcc_test_platform.application.central_result_selection_service import CentralResultSelectionService
from fcc_test_platform.application.published_plan_expectation_service import (
    PublishedPlanExpectationError,
    PublishedPlanExpectationNotFound,
    PublishedPlanExpectationService,
)
from fcc_test_platform.application.published_plan_identity_adapter import (
    PublishedPlanIdentityError,
)
from fcc_test_platform.application.central_project_reference_service import (
    CentralProjectReferenceService,
)
from fcc_test_platform.application.chamber_result_ingestion_service import (
    ChamberResultIngestionError,
    ChamberResultIngestionService,
    ChamberResultIngestionUpstreamError,
)
from fcc_test_platform.application.central_rbac_read_service import CentralRbacReadService
from fcc_test_platform.application.user_provisioning_service import UserProvisioningService
from fcc_test_platform.application.central_membership_write_service import (
    MembershipNotFoundError,
    MembershipRoleUnknownError,
    MembershipUserUnknownError,
    MembershipWriteService,
)
from fcc_test_platform.application.central_sample_inventory_service import (
    CentralSampleInventoryService,
    SampleInventoryConflictError,
    SampleInventoryNotFoundError,
)
from fcc_test_platform.application.sample_inventory_export_service import (
    SampleInventoryExportCategoryUnresolvedError,
    SampleInventoryExportService,
    SampleInventoryExportTemplateError,
)
from fcc_test_platform.domain.ports.output.central_sample_inventory_write_port import (
    CentralSampleInventoryWriteError,
)
from fcc_test_platform.domain.ports.output.central_sample_inventory_read_port import (
    CentralSampleInventoryReadError,
)
from fcc_test_contracts.common.login_throttle_policy import spraying_source_key
from fcc_test_contracts.common.rate_limit_policy import RATE_LIMIT_DETAIL
from fcc_test_contracts.web.rate_limit_middleware import (
    RATE_LIMIT_LIMIT_HEADER,
    RATE_LIMIT_REMAINING_HEADER,
    RETRY_AFTER_HEADER,
)
from fcc_test_platform.domain.ports.output.central_chamber_read_port import CentralChamberReadError
from fcc_test_platform.domain.ports.output.central_chamber_write_port import ChamberWriteError
from fcc_test_platform.domain.ports.output.central_project_port import (
    CentralProjectError,
    ProjectIdentifierConflictError,
)
from fcc_test_platform.domain.ports.output.central_reference_port import (
    CentralReferenceError,
    ReferenceCoupledPublishError,
    ReferenceProviderNotFoundError,
    ReferenceProviderNotRegisteredError,
    ReferencePublishConflictError,
    ReferenceRevisionNotFoundError,
    ReferenceStateConflictError,
)
from fcc_test_platform.domain.ports.output.central_report_port import (
    CentralReportError,
    ReportEditionConflictError,
    ReportSessionNotFoundError,
)
from fcc_test_platform.domain.ports.output.central_test_equipment_list_port import (
    CentralTestEquipmentListError,
    EquipmentListConflictError,
    EquipmentListNotFoundError,
)
from fcc_test_platform.domain.ports.output.central_claim_write_port import ClaimWriteError
from fcc_test_platform.domain.ports.output.central_membership_write_port import MembershipWriteError
from fcc_test_kernel.domain.ports.output.central_rbac_read_port import CentralRbacReadError
from fcc_test_platform.domain.ports.output.central_user_write_port import UserWriteError
from fcc_test_platform.domain.ports.output.central_read_port import CentralReadError
from fcc_test_platform.domain.ports.output.central_result_selection_port import (
    SelectionCandidateNotFoundError,
    SelectionCrossScopeError,
    SelectionProviderNotFoundError,
    SelectionRevisionConflictError,
)
from fcc_test_platform.domain.ports.output.central_project_reference_port import (
    ReferenceNotFoundError,
    ReferenceRetiredError,
    ReferenceIncompatibleError,
    ReferenceHashMismatchError,
    ReferenceSourceMismatchError,
    ReferenceScopeMismatchError,
    CentralProjectReferenceError,
)
from fcc_test_contracts.common.health_probe_policy import (
    LIVENESS_PATH_SUFFIX,
    READINESS_PATH_SUFFIX,
    READINESS_UNAVAILABLE_DETAIL,
    ReadinessSnapshot,
    liveness_payload,
)
from fcc_test_platform.domain.ports.output.central_progress_read_port import (
    CentralProgressReadError,
    CentralProgressReadPort,
)
from fcc_test_platform.application.provider_ui_descriptor_registry import (
    ProviderUiDescriptorNotFound,
    ProviderUiDescriptorRegistry,
)

if TYPE_CHECKING:  # avoid a hard import on the read-only surface
    from fcc_test_platform.application.central_reference_service import CentralReferenceService
    from fcc_test_platform.application.central_artifact_custody_service import (
        CentralArtifactCustodyService,
    )
    from fcc_test_platform.application.central_report_service import CentralReportService
    from fcc_test_platform.application.central_test_equipment_list_service import (
        CentralTestEquipmentListService,
    )
    from fcc_test_platform.application.readiness_service import ReadinessService


__all__ = [
    'CHAMBER_BINDING_AXES',
    'CHAMBER_BINDING_AXIS_ENVELOPE',
    'CHAMBER_BINDING_AXIS_PATH',
    'PlatformApiAdapter',
    'PlatformAuthorizationError',
    'api_error_status',
    'create_platform_app',
    'create_platform_router',
]


# Chamber resource-binding axes (2026-08-05). A chamber-scoped write declares its
# chamber on one or more of these axes; ``_enforce_chamber_token_binding`` is the
# only site that compares them, so the axis names exist once instead of being
# re-spelled inside each route's error text.
CHAMBER_BINDING_AXIS_PATH = 'path chamber_id'
CHAMBER_BINDING_AXIS_ENVELOPE = 'envelope chamber_id'
CHAMBER_BINDING_AXES = (
    CHAMBER_BINDING_AXIS_PATH,
    CHAMBER_BINDING_AXIS_ENVELOPE,
)


# OBS-2 phase 3 metrics parity (FE-P0d S5, 2026-05-27): path template → operation
# reverse index via the shared ``application/common/metrics_registry`` SSOT —
# identical helper to Session/Headless. ``/platform/metrics`` is intentionally
# NOT in PLATFORM_API_ROUTES so the lookup returns ``None`` and the middleware
# skips self-referential observation (mirror of the headless surface).
_PLATFORM_PATH_PATTERNS = build_route_pattern_index(PLATFORM_API_ROUTES)


def _lookup_platform_operation(request_path: str):
    """Return canonical operation name for a request path; None if no match."""
    return _lookup_route_operation(_PLATFORM_PATH_PATTERNS, request_path)


class PlatformAuthorizationError(PermissionError):
    """Raised when a principal is not authorized for a platform operation."""


class PlatformApiAdapter:
    """Thin driving adapter over the central read service."""

    def __init__(
        self,
        read_service: CentralReadService,
        access_policy: Optional[ApiAccessPolicy] = None,
        principal: Optional[ApiPrincipal] = None,
        metrics_registry: Optional[ApiMetricsRegistry] = None,
        claim_write_service: Optional[ClaimWriteService] = None,
        rbac_read_service: Optional[CentralRbacReadService] = None,
        membership_write_service: Optional[MembershipWriteService] = None,
        provider_ui_descriptor_registry: Optional[ProviderUiDescriptorRegistry] = None,
        chamber_read_service: Optional[CentralChamberReadService] = None,
        chamber_write_service: Optional[CentralChamberWriteService] = None,
        chamber_measurement_service: Optional[ChamberMeasurementService] = None,
        chamber_result_ingestion_service: Optional[ChamberResultIngestionService] = None,
        progress_broadcaster: Optional[ChamberProgressBroadcastPort] = None,
        chamber_metrics_collector=None,
        project_service: Optional[CentralProjectService] = None,
        sample_inventory_service: Optional[CentralSampleInventoryService] = None,
        sample_export_service: Optional[SampleInventoryExportService] = None,
        progress_read_port: Optional[CentralProgressReadPort] = None,
        report_service: Optional['CentralReportService'] = None,
        equipment_list_service: Optional['CentralTestEquipmentListService'] = None,
        reference_service: Optional['CentralReferenceService'] = None,
        result_selection_service: Optional[CentralResultSelectionService] = None,
        published_plan_expectation_service: Optional[PublishedPlanExpectationService] = None,
        project_result_reference_service: Optional[CentralProjectReferenceService] = None,
        artifact_custody_service: Optional['CentralArtifactCustodyService'] = None,
        user_provisioning_service: Optional[UserProvisioningService] = None,
        provisioning_dedup: Optional[set[tuple[str, str]]] = None,
        readiness_service: Optional['ReadinessService'] = None,
        local_auth_service: Optional['LocalAuthService'] = None,
    ) -> None:
        self._read_service = read_service
        # 신원 축 EMS 정합 (2026-08-21) — local password login. ``None`` on every
        # pre-existing test and on any deployment running oidc_jwt, where the
        # five auth operations raise loudly rather than half-answering.
        self._local_auth_service = local_auth_service
        self._access_policy = access_policy
        self._principal = principal
        # OBS-2 phase 3 parity (FE-P0d S5, 2026-05-27): ``None`` → metric
        # recording is a no-op (bare unit test). The production composition root
        # injects one with ``namespace='fcc_platform'`` + ``enable_websocket=True``
        # (HTTP + chamber progress WS surface) shared with middleware/endpoints.
        self._metrics_registry = metrics_registry
        # FE-P3-write: ``None`` on the read-only surface (bare read tests). The
        # composition root injects a ClaimWriteService when the write path is wired.
        self._claim_write_service = claim_write_service
        # FE-P8 (2026-05-28): RBAC read service + membership write service.
        # When ``rbac_read_service`` is None, authorize() falls back to the
        # token-only path (backward-compat for tests that pre-date FE-P8).
        # When the membership write service is None, the assign/revoke
        # handlers raise loudly — composition always wires it in production.
        self._rbac_read_service = rbac_read_service
        self._membership_write_service = membership_write_service
        # WEB-PROVIDER-UI-0: provider UI descriptor registry (read-only proxy
        # source). None on bare read tests; the composition root wires it.
        self._provider_ui_descriptor_registry = provider_ui_descriptor_registry
        # 멀티챔버 P2: chamber availability read + registry/heartbeat write
        # services. None on the pre-P2 read-only surface (bare tests); the
        # composition root injects them when the chamber path is wired. The
        # handlers raise loudly if invoked unwired (mirror of membership).
        self._chamber_read_service = chamber_read_service
        self._chamber_write_service = chamber_write_service
        # 멀티챔버 P5: 중앙 측정 프록시 서비스(IDLE 게이트 → base_url → 노드 forward).
        # None on pre-P5 surface (bare tests); composition root injects it.
        self._chamber_measurement_service = chamber_measurement_service
        self._chamber_result_ingestion_service = chamber_result_ingestion_service
        # 멀티챔버 P7/B4: 중앙 진행 릴레이 fan-out broadcaster(ADR-0015 Option B). None
        # on pre-P7 surface / HTTP-only tests; the composition root injects one and
        # the WS endpoint subscribes to it. push_chamber_heartbeat publishes an
        # in_use progress event so the WS fan-out is real-time (heartbeat polling
        # fallback preserved).
        self._progress_broadcaster = progress_broadcaster
        # 관측성(2026-06-20) — scrape 시점 챔버 gauge 갱신기. None 이면 no-op
        # (bare 테스트/챔버 미배선). composition 이 ChamberMetricsCollector 주입.
        self._chamber_metrics_collector = chamber_metrics_collector
        # Phase 1 (2026-06-22) — 프로젝트 진입층 서비스(list/detail/create). None on
        # pre-Phase-1 surface (bare tests); composition root injects it. create_project
        # handler raises loudly if invoked unwired (mirror of membership/chamber).
        self._project_service = project_service
        # Web sample inventory is a server-owned CRUD/history service. The Excel
        # renderer is a separate collaborator so API handlers never parse uploads
        # or rebuild template data.
        self._sample_inventory_service = sample_inventory_service
        self._sample_export_service = sample_export_service
        # Phase 6 (2026-06-23) — time-weighted progress read port. None on the
        # pre-P6 surface (bare tests); the composition root wires it. The
        # get_project_progress handler raises loudly if invoked unwired.
        self._progress_read_port = progress_read_port
        # Phase G (2026-06-23) — test_reports 성적서 service (list/create/citation).
        # None on the pre-Phase-G surface (bare tests); the composition root wires
        # it. The report handlers raise loudly if invoked unwired (mirror of the
        # project/membership/chamber services).
        self._report_service = report_service
        # 성적서 §6 장비목록 표면. report_service 와 같은 이유로 Optional 이다 —
        # 이 서비스가 없는 배포는 해당 라우트를 노출하지 않고, 미배선 상태로
        # 도달하면 핸들러가 조용히 degrade 하지 않고 loud 실패한다.
        self._equipment_list_service = equipment_list_service
        # Wave 3 — the reference catalog surface. Optional for the same reason
        # the report service is: a deployment without it simply does not expose
        # those routes, and the handlers raise loudly rather than degrade if one
        # is reached unwired.
        self._reference_service = reference_service
        self._result_selection_service = result_selection_service
        # plan-delivery (2026-09-02) — the write that makes central KNOW a
        # published plan. ``None`` when the progress tier is not wired; the
        # route then fails loud rather than pretending it registered.
        self._published_plan_expectation_service = published_plan_expectation_service
        self._project_result_reference_service = project_result_reference_service
        # plot-dual-custody ① — 보관 판정 수신/조회. None 이면 세 handler 가
        # loud 실패한다(조용한 '이상 없음' 금지 — 빈 결과는 통과처럼 읽힌다).
        self._artifact_custody_service = artifact_custody_service
        # Auth JIT provisioning (§1, 2026-06-29): platform central RBAC surface
        # only. The service is stateless; the dedup set is request-scoped because
        # with_principal() creates one adapter per request.
        self._user_provisioning_service = user_provisioning_service
        self._provisioning_dedup = provisioning_dedup if provisioning_dedup is not None else set()
        # Ops probes (2026-07-20) — readiness dependency evaluator. ``None`` means
        # this composition declared NO dependency to check, and readiness then
        # reports ready with an empty dependency map (the "nothing to check"
        # case, explicit in ``ReadinessSnapshot.ready``). Production always wires
        # one, so an unwired readiness cannot silently mask a failing central DB
        # there; a bare unit test simply gets the trivial verdict.
        self._readiness_service = readiness_service

    @property
    def metrics_registry(self) -> Optional[ApiMetricsRegistry]:
        return self._metrics_registry

    def refresh_metrics(self) -> None:
        """scrape 직전 derived 메트릭(챔버 gauge) 갱신 — collector best-effort."""
        collector = self._chamber_metrics_collector
        if collector is not None:
            collector.refresh()

    @property
    def progress_broadcaster(self) -> Optional[ChamberProgressBroadcastPort]:
        return self._progress_broadcaster

    # ── Ops probes (2026-07-20) ─────────────────────────────────────────────
    # Auth-exempt by the same reasoning as ``/platform/metrics``: an orchestrator
    # (docker healthcheck, k8s kubelet, load balancer) cannot carry an OIDC
    # token, so requiring one would make the probe permanently fail. The
    # disclosure cost is bounded by the bodies themselves — a constant status
    # token, and for readiness a logical dependency NAME plus one of two status
    # tokens (``health_probe_policy`` owns that vocabulary; no DSN, host,
    # version or exception text is ever rendered). ``authorize()`` is therefore
    # deliberately NOT called here.

    def liveness(self) -> dict:
        """Liveness: is this process able to answer at all? No dependency I/O.

        Named to mirror the Session surface's ``adapter.liveness()`` so the three
        surfaces expose one vocabulary. Constant-cost by design — see
        ``health_probe_policy`` for why a dependency-aware liveness probe turns a
        dependency blip into a restart storm.
        """
        return liveness_payload()

    def readiness(self) -> ReadinessSnapshot:
        """Readiness: can this process serve traffic right now?

        Returns the snapshot rather than a body/status pair so the *route* owns
        the HTTP rendering (200 vs 503) and this adapter stays transport-free —
        the same split every other method on this adapter uses.
        """
        service = self._readiness_service
        if service is None:
            return ReadinessSnapshot(dependencies={}, checked_at=0.0)
        return service.check()

    def with_principal(self, principal: Optional[ApiPrincipal]) -> 'PlatformApiAdapter':
        """Return a request-scoped view without mutating shared state.

        ⚠️ **협력자 목록을 손으로 나열하지 않는다.** 손으로 적으면 새 협력자가
        생성자에는 들어가고 이 투영에서는 빠지는 일이 생기고, 그때 그 협력자는
        **인증이 켜진 배포에서만** 사라진다(``with_principal`` 은 principal_resolver
        가 주입됐을 때만 도는 경로다). 어댑터를 직접 만드는 테스트는 전부 green 이므로
        아무것도 잡지 못한다 — 세션 어댑터에서 ``allowed_storage_roots`` 가 정확히
        그렇게 떨어져 나가 허용 목록이 영구 400 이었다.

        그래서 값은 ``_request_scoped_state()`` **한 곳**에서 나오고, 그 함수가
        생성자 kwarg 전량을 덮는지는 ``inspect.signature`` 로 봉인된다. 새 kwarg 는
        봉인을 red 로 만들어 스스로 합류를 요구한다.
        """
        return PlatformApiAdapter(**{
            **self._request_scoped_state(),
            'principal': principal,
            # 요청마다 새 집합 — 공유하면 한 요청의 JIT provisioning 기록이 다음
            # 요청의 중복 제거에 새어 들어간다.
            'provisioning_dedup': set(),
        })

    def _request_scoped_state(self) -> dict:
        """생성자 kwarg → 현재 값. 새 kwarg 가 자동으로 투영에 합류한다."""
        return {
            'read_service': self._read_service,
            'access_policy': self._access_policy,
            'principal': self._principal,
            'metrics_registry': self._metrics_registry,
            'claim_write_service': self._claim_write_service,
            'rbac_read_service': self._rbac_read_service,
            'membership_write_service': self._membership_write_service,
            'provider_ui_descriptor_registry': self._provider_ui_descriptor_registry,
            'chamber_read_service': self._chamber_read_service,
            'chamber_write_service': self._chamber_write_service,
            'chamber_measurement_service': self._chamber_measurement_service,
            'chamber_result_ingestion_service': self._chamber_result_ingestion_service,
            'progress_broadcaster': self._progress_broadcaster,
            'chamber_metrics_collector': self._chamber_metrics_collector,
            'project_service': self._project_service,
            'sample_inventory_service': self._sample_inventory_service,
            'sample_export_service': self._sample_export_service,
            'progress_read_port': self._progress_read_port,
            'report_service': self._report_service,
            'equipment_list_service': self._equipment_list_service,
            'reference_service': self._reference_service,
            'result_selection_service': self._result_selection_service,
            'published_plan_expectation_service': self._published_plan_expectation_service,
            'project_result_reference_service': self._project_result_reference_service,
            'artifact_custody_service': self._artifact_custody_service,
            'user_provisioning_service': self._user_provisioning_service,
            'provisioning_dedup': self._provisioning_dedup,
            'readiness_service': self._readiness_service,
            'local_auth_service': self._local_auth_service,
        }

    def authorize(self, operation: str, *, project_id: Optional[str] = None) -> None:
        """Allow when (token grants the required permission) OR (project
        membership grants it) — the FE-P8 union.

        Backward-compatible: with no access policy wired (auth disabled
        composition path), this is a no-op. With no RBAC read service wired
        (pre-FE-P8 tests / no central membership), only the token path runs —
        identical behaviour to FE-P0d. The union is monotonic: enabling
        membership-based grants can ONLY widen the allowed set, never
        narrow it, so existing token holders keep their access.

        ``project_id`` is required for the membership path; passing None
        means the operation is project-scoped at the API level but the
        authorizer falls back to token-only (sync-status, for example, is
        scoped to a project URL but membership wouldn't make sense if the
        operator can read other projects via their token).
        """
        if self._access_policy is None:
            return
        self._ensure_principal_provisioned()
        if not self._central_principal_enabled():
            raise PlatformAuthorizationError('principal user is disabled')
        # 1) Token path — same decision as the FE-P0d surface.
        decision = self._access_policy.authorize(operation, self._principal)
        if decision.allowed:
            return
        # 2) Membership path — only when both the RBAC service AND a principal
        # with a non-anonymous subject AND a project_id are available. Wildcard
        # / admin token holders already short-circuited at step (1).
        # 신원 축 EMS 정합 (2026-08-21) — a pending password change is NOT a
        # missing permission, and collapsing the two would leave a bootstrap
        # administrator at a dead end: "forbidden" tells them to stop, while this
        # tells them to do one specific thing and proceed. The membership path is
        # skipped deliberately — no project role can grant its way past it.
        if decision.reason == REASON_PASSWORD_CHANGE_REQUIRED:
            raise PasswordChangeRequiredError(
                'password change required before this operation'
            )
        granted = self._membership_grants(operation, project_id)
        if granted:
            return
        raise PlatformAuthorizationError(decision.reason or 'forbidden')

    def _membership_grants(self, operation: str, project_id: Optional[str]) -> bool:
        """True when the principal's project membership grants the operation."""
        if self._rbac_read_service is None or self._principal is None or not project_id:
            return False
        subject = (self._principal.subject or '').strip()
        if not subject or subject == 'anonymous':
            return False
        contract = PLATFORM_API_OPERATIONS.get(operation) or {}
        required = str(contract.get('permission') or '').strip()
        if not required:
            return False
        # Token-borne wildcard/admin already short-circuited at the token path;
        # membership can ONLY widen by carrying the specific required permission.
        if required in (API_PERMISSION_WILDCARD, API_PERMISSION_ADMIN):
            return False
        try:
            permissions = self._rbac_read_service.effective_permissions(
                project_id,
                subject,
                user_issuer=getattr(self._principal, 'issuer', ''),
            )
        except CentralRbacReadError:
            # Loud upstream: re-raise so the route boundary maps to 503.
            raise
        return required in permissions

    def resolve_effective_project_permissions(self, project_id: str) -> frozenset[str]:
        """Return this validated principal's central project permissions.

        This is an internal service boundary for the provider headless process,
        not a browser-facing permission catalog. Reusing the existing
        membership-read authorization keeps the platform adapter responsible
        for central user-enabled, issuer/subject, expiry, and project UUID
        rules; the headless process never opens PostgreSQL or expands roles.
        """
        self.authorize('list_project_memberships', project_id=project_id)
        if self._rbac_read_service is None:
            raise RuntimeError(
                'effective project permissions called but rbac_read_service is not wired '
                '(production composition must inject it)'
            )
        principal = self._principal
        if principal is None:
            return frozenset()
        subject = (principal.subject or '').strip()
        if not subject or subject == 'anonymous':
            return frozenset()
        return self._rbac_read_service.effective_permissions(
            project_id,
            subject,
            user_issuer=getattr(principal, 'issuer', ''),
        )

    def _central_principal_enabled(self) -> bool:
        if self._rbac_read_service is None or self._principal is None:
            return True
        subject = (self._principal.subject or '').strip()
        if not subject or subject == 'anonymous':
            return True
        enabled = self._rbac_read_service.user_enabled(
            subject,
            user_issuer=getattr(self._principal, 'issuer', ''),
        )
        return enabled is not False

    def _ensure_principal_provisioned(self) -> None:
        service = self._user_provisioning_service
        principal = self._principal
        if service is None or principal is None:
            return
        subject = (principal.subject or '').strip()
        if not subject or subject == 'anonymous':
            return
        service.ensure_provisioned(principal, dedup=self._provisioning_dedup)

    def get_project_coverage(
        self, project_id: str, *, limit: Optional[int] = None, cursor: Optional[str] = None,
        technology: Optional[str] = None,
    ) -> dict:
        self.authorize('get_project_coverage', project_id=project_id)
        return self._read_service.project_coverage(
            project_id, technology=technology, limit=limit, cursor=cursor,
        )

    def list_project_claims(
        self, project_id: str, *, limit: Optional[int] = None, cursor: Optional[str] = None,
        technology: Optional[str] = None,
    ) -> dict:
        self.authorize('list_project_claims', project_id=project_id)
        return self._read_service.project_claims(
            project_id, technology=technology, limit=limit, cursor=cursor,
        )

    def get_project_sync_status(self, project_id: str) -> dict:
        self.authorize('get_project_sync_status', project_id=project_id)
        return self._read_service.project_sync_status(project_id)

    def get_project_progress(self, project_id: str) -> list:
        """Phase 6 time-weighted progress rollup per (area, bucket) — read-only."""
        self.authorize('get_project_progress', project_id=project_id)
        if self._progress_read_port is None:
            raise RuntimeError('progress_read_port is not wired')
        return [
            rollup.to_dict()
            for rollup in self._progress_read_port.get_project_progress(project_id)
        ]

    def list_project_report_sessions(self, project_id: str) -> list:
        self.authorize('list_project_report_sessions', project_id=project_id)
        return self._read_service.project_report_sessions(project_id)

    # ── Cross-session result selection (M1-M5) ───────────────────────────────
    # Provider and condition are path axes on every operation.  The application
    # service/central adapter owns the exact-scope SQL and append-only CAS; this
    # driving adapter only supplies authorization and the verified actor.
    def list_project_result_selections(
        self, project_id: str, provider_id: str, *,
        limit: Optional[int] = None, cursor: Optional[str] = None,
    ) -> dict:
        self.authorize('list_project_result_selections', project_id=project_id)
        return self._require_result_selection_service(
            'list_project_result_selections'
        ).list_effective_results(
            project_id, provider_id, limit=100 if limit is None else limit,
            cursor=cursor,
        )

    def list_project_result_attempts(
        self, project_id: str, provider_id: str, condition_hash: str, *,
        limit: Optional[int] = None, cursor: Optional[str] = None,
    ) -> dict:
        self.authorize('list_project_result_attempts', project_id=project_id)
        return self._require_result_selection_service(
            'list_project_result_attempts'
        ).list_attempts(
            project_id, provider_id, condition_hash,
            limit=100 if limit is None else limit, cursor=cursor,
        )

    def select_project_result(
        self, project_id: str, provider_id: str, condition_hash: str,
        body: Optional[dict] = None,
    ) -> dict:
        self.authorize('select_project_result', project_id=project_id)
        actor = self._require_actor('select_project_result')
        payload = body or {}
        return self._require_result_selection_service(
            'select_project_result'
        ).select(
            project_id, provider_id, condition_hash,
            attempt_id=payload.get('attempt_id'),
            expected_revision=payload.get('expected_revision'),
            actor_subject=actor,
            reason=payload.get('reason'),
        )

    def clear_project_result_selection(
        self, project_id: str, provider_id: str, condition_hash: str,
        body: Optional[dict] = None,
    ) -> dict:
        self.authorize('clear_project_result_selection', project_id=project_id)
        actor = self._require_actor('clear_project_result_selection')
        payload = body or {}
        return self._require_result_selection_service(
            'clear_project_result_selection'
        ).clear(
            project_id, provider_id, condition_hash,
            expected_revision=payload.get('expected_revision'),
            actor_subject=actor,
            reason=payload.get('reason'),
        )

    def ingest_published_plan_expectation(
        self, project_id: str, provider_id: str, body: Optional[dict] = None,
    ) -> dict:
        """Register a published plan's conditions as this project's denominator.

        ⚠️ **This is also how central learns the plan exists.**
        ``build_measurement_snapshot`` answers ``published_plan_id is unknown``
        from the very table this writes, so a plan that never reaches here cannot
        be measured — the browser can author and publish it and the chamber will
        still be told the id is not one central knows.
        """
        self.authorize('ingest_published_plan_expectation', project_id=project_id)
        # The actor is required for the same reason every other central write
        # requires one: an anonymous principal must fail before a transaction
        # opens. The ingest itself is attributed by the plan, not the caller.
        self._require_actor('ingest_published_plan_expectation')
        payload = body or {}
        return self._require_published_plan_expectation_service(
            'ingest_published_plan_expectation'
        ).ingest(
            project_id, provider_id,
            plan_id=payload.get('plan_id'),
            plan_published_at=payload.get('plan_published_at'),
            conditions=payload.get('conditions') or (),
        )

    def list_project_result_references(
        self, project_id: str, *, producer_provider_id: Optional[str] = None,
        state: Optional[str] = None, limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        self.authorize('list_project_result_references', project_id=project_id)
        return self._require_project_result_reference_service(
            'list_project_result_references'
        ).list_references(
            project_id, producer_provider_id=producer_provider_id, state=state,
            limit=100 if limit is None else limit, cursor=cursor,
        )

    def create_project_result_reference(
        self, project_id: str, body: Optional[dict] = None,
    ) -> dict:
        self.authorize('create_project_result_reference', project_id=project_id)
        actor = self._require_actor('create_project_result_reference')
        payload = validate_project_result_reference_request(body)
        return self._require_project_result_reference_service(
            'create_project_result_reference'
        ).publish(
            project_id=project_id,
            provider_id=payload.get('provider_id'),
            condition_hash=payload.get('condition_hash'),
            reason=payload.get('reason'),
            actor_subject=actor,
        )

    def retire_project_result_reference(
        self, project_id: str, revision_id: str, body: Optional[dict] = None,
    ) -> dict:
        self.authorize('retire_project_result_reference', project_id=project_id)
        actor = self._require_actor('retire_project_result_reference')
        payload = body or {}
        return self._require_project_result_reference_service(
            'retire_project_result_reference'
        ).retire(revision_id, actor_subject=actor, reason=payload.get('reason'))

    # ── Phase 1 (2026-06-22) — 프로젝트 진입층 ────────────────────────────────
    # list 는 호출자 멤버십으로 scope(token path 가 platform:read 게이트, 행 필터는
    # subject). detail 은 project-scoped read(membership union). create 는 GLOBAL
    # 'authenticated' 연산 — 생성자(actor) 필수(자동 admin 멤버십 + audit).

    def list_projects(
        self, *, status: str = 'active', q: Optional[str] = None,
        limit: Optional[int] = None, cursor: Optional[str] = None,
    ) -> dict:
        self.authorize('list_projects')
        if self._project_service is None:
            raise RuntimeError(
                'list_projects called but project_service is not wired '
                '(production composition must inject it; bare tests should inject one)'
            )
        # Read-open by status (project-status-visibility): no membership scoping —
        # any authenticated principal sees the directory filtered by status.
        # W3 백엔드 — returns a page dict ({items, next_cursor}); the route emits
        # the array body + the cursor header (same shape as coverage/claims).
        return self._project_service.list_projects(
            status=status, q=q, limit=limit, cursor=cursor,
        )

    def complete_project(self, project_id: str) -> dict:
        # project-status-visibility — mark a project completed. project_id is
        # passed to authorize so a project-membership admin (not only a global
        # platform:admin token) may perform it.
        self.authorize('complete_project', project_id=project_id)
        if self._project_service is None:
            raise RuntimeError('complete_project called but project_service is not wired')
        return self._project_service.complete_project(project_id)

    def reopen_project(self, project_id: str) -> dict:
        self.authorize('reopen_project', project_id=project_id)
        if self._project_service is None:
            raise RuntimeError('reopen_project called but project_service is not wired')
        return self._project_service.reopen_project(project_id)

    def get_project(self, project_id: str) -> dict:
        self.authorize('get_project', project_id=project_id)
        if self._project_service is None:
            raise RuntimeError('get_project called but project_service is not wired')
        return self._project_service.get_project(project_id)

    def update_project(self, project_id: str, body: Optional[dict] = None) -> dict:
        # W3 백엔드 — 성적서 표지 메타 부분 편집. project_id 를 authorize 에 넘겨
        # project-membership admin 도 수행 가능(complete/reopen 과 동일 경계). 본문
        # 검증(부분갱신 semantics + 범위 밖 필드 거부)은 도메인 정책 SSOT 가 소유하므로
        # 여기서는 본문을 그대로 전달한다(어댑터가 필드를 다시 열거하지 않는다).
        self.authorize('update_project', project_id=project_id)
        if self._project_service is None:
            raise RuntimeError('update_project called but project_service is not wired')
        return self._project_service.update_project_metadata(project_id, body)

    def create_project(self, body: Optional[dict] = None) -> dict:
        self.authorize('create_project')
        if self._project_service is None:
            raise RuntimeError('create_project called but project_service is not wired')
        actor = self._authenticated_actor()
        if not actor:
            # ADR-0017 D3 — create grants the creator project_admin (membership +
            # audit), so it REQUIRES an authenticated actor. The 'authenticated'
            # gate already denies anonymous when auth is on; this also refuses the
            # allow-insecure (auth-disabled) path rather than create an
            # owner-less project.
            raise PlatformAuthorizationError(
                'create_project requires an authenticated actor (creator becomes '
                'project_admin) — anonymous/auth-disabled creation is refused'
            )
        payload = body or {}
        # JIT (결함 B) — forward the creator's VERIFIED profile claims so the
        # project service can provision their central ``users`` row. Empty when
        # the principal carries no email/name (trusted-header / claim-less token).
        principal = self._principal
        actor_email = getattr(principal, 'email', '') if principal else ''
        actor_display_name = getattr(principal, 'display_name', '') if principal else ''
        return self._project_service.create_project(
            model_name=payload.get('model_name'),
            actor_subject=actor,
            actor_issuer=getattr(principal, 'issuer', '') if principal else '',
            actor_email=actor_email,
            actor_display_name=actor_display_name,
            customer=payload.get('customer'),
            manufacturer=payload.get('manufacturer'),
            management_number=payload.get('management_number'),
            fcc_grantee_code=payload.get('fcc_grantee_code'),
            applicant_name=payload.get('applicant_name'),
            applicant_address=payload.get('applicant_address'),
            eut_description=payload.get('eut_description'),
            test_standard=payload.get('test_standard'),
        )

    # ── Phase G (2026-06-23) — test_reports 성적서 surface ────────────────────
    # list/citation read 은 project-scoped(membership union), create 는
    # platform:admin. report_number 는 service 가 management_number+edition 에서 파생.

    def list_reports(self, project_id: str) -> list:
        self.authorize('list_reports', project_id=project_id)
        if self._report_service is None:
            raise RuntimeError('list_reports called but report_service is not wired')
        return self._report_service.list_reports(project_id)

    def create_report(self, project_id: str, body: Optional[dict] = None) -> dict:
        self.authorize('create_report', project_id=project_id)
        if self._report_service is None:
            raise RuntimeError('create_report called but report_service is not wired')
        payload = body or {}
        return self._report_service.create_report(
            project_id,
            edition=payload.get('edition'),
            date_of_issue=payload.get('date_of_issue'),
            date_tested_start=payload.get('date_tested_start'),
            date_tested_end=payload.get('date_tested_end'),
            prepared_by=payload.get('prepared_by'),
            prepared_site=payload.get('prepared_site'),
            rev_history_json=payload.get('rev_history_json'),
        )

    def get_report_citation(
        self, project_id: str, *, edition: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        self.authorize('get_report_citation', project_id=project_id)
        if self._report_service is None:
            raise RuntimeError(
                'get_report_citation called but report_service is not wired'
            )
        kwargs = {'edition': edition}
        if session_id is not None:
            kwargs['session_id'] = session_id
        return self._report_service.get_report_citation(project_id, **kwargs)

    # ── 성적서 §6 장비목록 (2026-08-07) ────────────────────────────────────────
    # EMS 가 표준 장비리스트의 SSOT 이고 이 표면은 프로젝트가 실제로 쓴 목록을
    # 기록·확정한다. 읽기는 platform:read, 쓰기는 platform:claim(engineer 티어
    # mutating 토큰) — authorize 가 토큰 ∪ 프로젝트 멤버십 union 이므로
    # 프로젝트 멤버인 시험원은 토큰 없이도 자기 목록을 끝낼 수 있다.

    def list_test_equipment_lists(self, project_id: str) -> list:
        self.authorize('list_test_equipment_lists', project_id=project_id)
        service = self._require_equipment_list_service('list_test_equipment_lists')
        return service.list_lists(project_id)

    def get_test_equipment_list(self, project_id: str, equipment_list_id: str) -> dict:
        self.authorize('get_test_equipment_list', project_id=project_id)
        service = self._require_equipment_list_service('get_test_equipment_list')
        return service.get_list(project_id, equipment_list_id)

    def create_test_equipment_list(
        self, project_id: str, body: Optional[dict] = None
    ) -> dict:
        self.authorize('create_test_equipment_list', project_id=project_id)
        service = self._require_equipment_list_service('create_test_equipment_list')
        payload = body or {}
        return service.create_list(
            project_id,
            test_item_key=payload.get('test_item_key'),
            test_item_name=payload.get('test_item_name'),
            test_report_id=payload.get('test_report_id'),
            source_profile_key=payload.get('source_profile_key'),
            source_revision_key=payload.get('source_revision_key'),
            source_pulled_at=payload.get('source_pulled_at'),
        )

    def replace_test_equipment_list_items(
        self, project_id: str, equipment_list_id: str, body: Optional[dict] = None
    ) -> dict:
        self.authorize('replace_test_equipment_list_items', project_id=project_id)
        service = self._require_equipment_list_service('replace_test_equipment_list_items')
        payload = body or {}
        return service.replace_items(
            project_id, equipment_list_id, payload.get('items') or []
        )

    def attach_test_equipment_list(
        self, project_id: str, equipment_list_id: str, body: Optional[dict] = None
    ) -> dict:
        self.authorize('attach_test_equipment_list', project_id=project_id)
        service = self._require_equipment_list_service('attach_test_equipment_list')
        payload = body or {}
        return service.attach_to_report(
            project_id, equipment_list_id, test_report_id=payload.get('test_report_id'),
        )

    def confirm_test_equipment_list(
        self, project_id: str, equipment_list_id: str
    ) -> dict:
        self.authorize('confirm_test_equipment_list', project_id=project_id)
        service = self._require_equipment_list_service('confirm_test_equipment_list')
        return service.confirm_list(project_id, equipment_list_id)

    def _require_equipment_list_service(self, operation: str):
        """미배선이면 loud 실패 — 조용히 빈 목록을 돌려주지 않는다."""
        if self._equipment_list_service is None:
            raise RuntimeError(
                f'{operation} called but equipment_list_service is not wired'
            )
        return self._equipment_list_service

    # ── Wave 3 (2026-08-07) — reference catalog ───────────────────────────────
    # The central platform is the authoritative origin of measurement reference
    # data; chamber PCs hold replicas. Reads are project-member reads, create and
    # publish are project-management acts, and node delivery uses the node-scoped
    # chamber token bound to the path — never platform:read, which would also
    # hand a chamber PC coverage, claims and memberships.

    def list_reference_revisions(
        self,
        provider_id: str,
        *,
        family: Optional[str] = None,
        scope_kind: Optional[str] = None,
        scope_id: Optional[str] = None,
        state: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        self.authorize('list_reference_revisions')
        service = self._require_reference_service('list_reference_revisions')
        rows, next_cursor = service.list_revisions(
            provider_id,
            family=family,
            scope_kind=scope_kind,
            scope_id=scope_id,
            state=state,
            limit=limit,
            cursor=cursor,
        )
        # A page dict, so the shared _emit_page helper puts the keyset cursor in
        # the response header and leaves the body a plain array — the same shape
        # coverage, claims and memberships already return.
        return {'items': rows, 'next_cursor': next_cursor}

    def get_reference_revision(self, provider_id: str, revision_id: str) -> dict:
        """One revision with its entries and the column order to render them in.

        Reviewing a candidate before publishing it is the other half of the
        design that stops the importer at CANDIDATE. Without this operation the
        only thing a client can show is a summary, and "publish something you
        have not seen" is not review.
        """
        # 인가가 **먼저**다. `_require_reference_service` 를 앞에 두면, 참조
        # 표면을 배포하지 않은 노드에서 **무권한 익명 호출자**가 403 이 아니라
        # 500 을 받는다 — 그것만으로 "여기는 참조 쓰기가 배포돼 있지 않다"를
        # 알 수 있고, 그 사실은 권한 없는 사람이 알 일이 아니다(2026-08-09
        # 적대적 리뷰 실증). scope resolver 는 **지연 호출**이라 서비스 해소를
        # 그 안으로 옮기면 순서만 바꿔도 동작은 그대로다.
        self._authorize_reference_scope(
            'get_reference_revision',
            resolve_scope=lambda: self._reference_revision_scope(
                self._require_reference_service('get_reference_revision'),
                revision_id,
            ),
        )
        return self._require_reference_service(
            'get_reference_revision'
        ).read_revision(provider_id, revision_id)

    def create_reference_revision(
        self, provider_id: str, body: Optional[dict] = None,
    ) -> dict:
        payload = body or {}
        self._authorize_reference_scope(
            'create_reference_revision',
            resolve_scope=lambda: (payload.get('family'), payload.get('scope_id')),
        )
        service = self._require_reference_service('create_reference_revision')
        # The actor is the verified principal, never the body. This row records
        # who put a value in front of an instrument, so an actor a client could
        # name would be an actor a client could forge.
        return service.create_candidate(
            provider_id,
            request=payload,
            created_by=self._require_actor('create_reference_revision'),
        )

    def fork_reference_revision(
        self, provider_id: str, revision_id: str, body: Optional[dict] = None,
    ) -> dict:
        """Copy a published revision into a candidate the tester can edit.

        Takes no body. Everything a fork needs is already on the parent, and a
        body would only offer somewhere for a client to assert something the
        parent already answers.
        """
        # 인가가 **먼저**다. `_require_reference_service` 를 앞에 두면, 참조
        # 표면을 배포하지 않은 노드에서 **무권한 익명 호출자**가 403 이 아니라
        # 500 을 받는다 — 그것만으로 "여기는 참조 쓰기가 배포돼 있지 않다"를
        # 알 수 있고, 그 사실은 권한 없는 사람이 알 일이 아니다(2026-08-09
        # 적대적 리뷰 실증). scope resolver 는 **지연 호출**이라 서비스 해소를
        # 그 안으로 옮기면 순서만 바꿔도 동작은 그대로다.
        self._authorize_reference_scope(
            'fork_reference_revision',
            resolve_scope=lambda: self._reference_revision_scope(
                self._require_reference_service('fork_reference_revision'),
                revision_id,
            ),
        )
        return self._require_reference_service(
            'fork_reference_revision'
        ).fork_published(
            provider_id,
            revision_id,
            forked_by=self._require_actor('fork_reference_revision'),
        )

    def list_reference_families(self, provider_id: str) -> list:
        """저작 가능한 패밀리와 각각이 요구하는 칸 목록(순수 정책 투영).

        ``provider_id`` 는 경로에 있고 **투영의 내용**을 바꾸지 않는다 — 정책이
        하나만 적재돼 있기 때문이다. 그것을 근거로 주소에서 빼면, provider 마다
        다른 correction 열 어휘가 생기는 날 경로를 바꿔야 하고 그것은 계약 변경이다.

        내용을 안 바꾼다고 **검증까지 안 하는 것은 아니다**(2026-08-25). 경로의
        `{provider_id}` 는 주장이고, 검증이 없으면 미등록 provider 가 `200` 에
        패밀리 목록을 받아 화면이 저작 폼을 그린다 — 시험원은 다 채운 뒤
        저장에서 404 를 만난다. 서비스가 해소하고, 이 핸들러는 여전히 인가를
        **먼저** 한다.
        """
        self.authorize('list_reference_families')
        return self._require_reference_service(
            'list_reference_families'
        ).list_reference_families(provider_id)

    def create_authored_reference_revision(
        self, provider_id: str, body: Optional[dict] = None,
    ) -> dict:
        """Create a revision authored on the web (no workbook snapshot behind it)."""
        payload = body or {}
        self._authorize_reference_scope(
            'create_authored_reference_revision',
            resolve_scope=lambda: (payload.get('family'), payload.get('scope_id')),
        )
        service = self._require_reference_service(
            'create_authored_reference_revision'
        )
        return service.create_authored_candidate(
            provider_id,
            request=payload,
            created_by=self._require_actor('create_authored_reference_revision'),
        )

    def update_reference_revision_rows(
        self, provider_id: str, revision_id: str, body: Optional[dict] = None,
    ) -> dict:
        # 인가가 **먼저**다 — 형제 route 들과 같은 이유(참조 표면 미배포 노드에서
        # 무권한 호출자가 403 대신 500 을 받으면 그 자체가 배포 상태를 흘린다).
        self._authorize_reference_scope(
            'update_reference_revision_rows',
            resolve_scope=lambda: self._reference_revision_scope(
                self._require_reference_service(
                    'update_reference_revision_rows'
                ),
                revision_id,
            ),
        )
        return self._require_reference_service(
            'update_reference_revision_rows'
        ).update_candidate_rows(
            provider_id,
            revision_id,
            request=body or {},
            updated_by=self._require_actor('update_reference_revision_rows'),
        )

    def update_reference_revision_entries(
        self, provider_id: str, revision_id: str, body: Optional[dict] = None,
    ) -> dict:
        # 인가가 **먼저**다. `_require_reference_service` 를 앞에 두면, 참조
        # 표면을 배포하지 않은 노드에서 **무권한 익명 호출자**가 403 이 아니라
        # 500 을 받는다 — 그것만으로 "여기는 참조 쓰기가 배포돼 있지 않다"를
        # 알 수 있고, 그 사실은 권한 없는 사람이 알 일이 아니다(2026-08-09
        # 적대적 리뷰 실증). scope resolver 는 **지연 호출**이라 서비스 해소를
        # 그 안으로 옮기면 순서만 바꿔도 동작은 그대로다.
        self._authorize_reference_scope(
            'update_reference_revision_entries',
            resolve_scope=lambda: self._reference_revision_scope(
                self._require_reference_service(
                    'update_reference_revision_entries'
                ),
                revision_id,
            ),
        )
        return self._require_reference_service(
            'update_reference_revision_entries'
        ).update_candidate_entries(
            provider_id,
            revision_id,
            request=body or {},
            updated_by=self._require_actor('update_reference_revision_entries'),
        )

    def publish_reference_revision(
        self, provider_id: str, revision_id: str, body: Optional[dict] = None,
    ) -> dict:
        # 인가가 **먼저**다. `_require_reference_service` 를 앞에 두면, 참조
        # 표면을 배포하지 않은 노드에서 **무권한 익명 호출자**가 403 이 아니라
        # 500 을 받는다 — 그것만으로 "여기는 참조 쓰기가 배포돼 있지 않다"를
        # 알 수 있고, 그 사실은 권한 없는 사람이 알 일이 아니다(2026-08-09
        # 적대적 리뷰 실증). scope resolver 는 **지연 호출**이라 서비스 해소를
        # 그 안으로 옮기면 순서만 바꿔도 동작은 그대로다.
        self._authorize_reference_scope(
            'publish_reference_revision',
            resolve_scope=lambda: self._reference_revision_scope(
                self._require_reference_service('publish_reference_revision'),
                revision_id,
            ),
        )
        payload = body or {}
        return self._require_reference_service(
            'publish_reference_revision'
        ).publish(
            provider_id,
            revision_id,
            published_by=self._require_actor('publish_reference_revision'),
            publish_reason=payload.get('publish_reason'),
            coupled_revision_id=payload.get('coupled_revision_id'),
        )

    def get_chamber_reference_bundle(
        self,
        chamber_id: str,
        *,
        scope_project_id: Optional[str] = None,
        bundle_etag: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        """Deliver the published reference data one chamber must measure with."""
        self.authorize('get_chamber_reference_bundle')
        # The machine token is bound to the path chamber, so a node cannot fetch
        # another room's cabling. Binding runs BEFORE the service, so an
        # unauthorized request never reaches a central read.
        self._enforce_chamber_token_binding(
            (CHAMBER_BINDING_AXIS_PATH, chamber_id),
            require_binding=True,
        )
        service = self._require_reference_service('get_chamber_reference_bundle')
        # No provider is passed: the service holds the configured one. A node
        # naming its own provider could fetch another product line's cabling.
        return service.build_bundle(
            None,
            chamber_id,
            project_id=scope_project_id,
            known_bundle_etag=bundle_etag,
            limit=limit,
            cursor=cursor,
        )

    def _authorize_reference_scope(self, operation: str, *, resolve_scope) -> None:
        """Token first; membership only for a PROJECT-scoped family.

        Until 2026-08-08 the reference operations called ``authorize(op)`` with
        no ``project_id``, so the membership half of the FE-P8 union could never
        fire for them. A role grant nobody's membership can exercise is a false
        statement about the permission graph, so the grant added by this wave
        would have been inert for membership-derived permissions.

        The asymmetry is deliberate and is the point. Being a member of project X
        is a reason to touch *that project's* frequency table or analyzer
        settings. It is NOT a reason to change **room 1's cable loss**: a room
        outlives every project, and one project spans two rooms — which is why
        ``reference_scope_policy`` exists at all. Room-scoped families therefore
        stay token-only; the person who changes what an entire room measures with
        holds an explicitly granted token, not a project membership.

        Order matters for two reasons. A token holder pays no extra lookup (this
        is the common path), and a caller who is refused never learns whether the
        revision exists — the scope lookup happens only after the token path has
        already failed, and its result is discarded on refusal.
        """
        try:
            self.authorize(operation)
            return
        except PlatformAuthorizationError:
            pass
        # Only now — the token path already failed, so the lookup costs the
        # common path nothing and its result never reaches a refused caller.
        try:
            resolved = resolve_scope()
        except Exception:  # noqa: BLE001 — a failed lookup must not become 500
            resolved = None
        family, scope_id = resolved if resolved else (None, None)
        project_scope = None
        if family and scope_id:
            from fcc_test_kernel.domain.services.reference_scope_policy import (
                ReferenceScopeError,
                ReferenceScopeKind,
                scope_kind_for,
            )
            from fcc_test_kernel.domain.models.reference_catalog import CatalogFamily

            try:
                if scope_kind_for(CatalogFamily(family)) is ReferenceScopeKind.PROJECT:
                    project_scope = scope_id
            except (ReferenceScopeError, ValueError):
                project_scope = None
        # Re-run the full union. With no project scope this raises exactly the
        # error the token path already produced, so an unknown revision and an
        # unauthorized one are indistinguishable to the caller.
        self.authorize(operation, project_id=project_scope)

    @staticmethod
    def _reference_revision_scope(service, revision_id: str):
        """``(family, scope_id)`` of a stored revision, or ``None``.

        Returning ``None`` for an unknown id is what keeps a refusal silent
        about existence: the caller gets the same authorization error either
        way.
        """
        row = service.peek_revision_scope(revision_id)
        return (row['family'], row['scope_id']) if row else None

    def _require_reference_service(self, operation: str):
        if self._reference_service is None:
            raise RuntimeError(
                f'{operation} called but reference_service is not wired'
            )
        return self._reference_service

    def _require_result_selection_service(self, operation: str):
        if self._result_selection_service is None:
            raise RuntimeError(f'{operation} called but result_selection_service is not wired')
        return self._result_selection_service

    def _require_published_plan_expectation_service(self, operation: str):
        if self._published_plan_expectation_service is None:
            raise RuntimeError(
                f'{operation} called but published_plan_expectation_service is not wired'
            )
        return self._published_plan_expectation_service

    def _require_project_result_reference_service(self, operation: str):
        if self._project_result_reference_service is None:
            raise RuntimeError(
                f'{operation} called but project_result_reference_service is not wired'
            )
        return self._project_result_reference_service

    def _require_actor(self, operation: str) -> str:
        """The verified principal, or a refusal.

        These rows record who put a value in front of an instrument, so an
        anonymous write is not something to attribute to a placeholder.
        """
        actor = self._authenticated_actor()
        if not actor:
            raise PlatformAuthorizationError(
                f'{operation} requires an authenticated actor; the revision '
                'records who authored it and that cannot be anonymous'
            )
        return actor

    def list_providers(self) -> list:
        # WEB-PROVIDER-UI-0 — provider-scoped list (token-only authorize, no
        # project membership union). Served from the injected registry as
        # selectable summaries so the frontend builds a backend-driven picker
        # rather than a hardcoded provider list.
        self.authorize('list_providers')
        if self._provider_ui_descriptor_registry is None:
            raise RuntimeError(
                'list_providers called but provider_ui_descriptor_registry is not '
                'wired (production composition must inject it; bare tests should '
                'inject a registry)'
            )
        return self._provider_ui_descriptor_registry.summaries()

    def get_provider_ui_descriptor(self, provider_id: str) -> dict:
        # WEB-PROVIDER-UI-0 — provider-scoped (not project-scoped), so token-only
        # authorize (no project membership union). Served from the injected
        # registry; unknown provider → ProviderUiDescriptorNotFound (404).
        self.authorize('get_provider_ui_descriptor')
        if self._provider_ui_descriptor_registry is None:
            raise RuntimeError(
                'get_provider_ui_descriptor called but provider_ui_descriptor_'
                'registry is not wired (production composition must inject it; '
                'bare tests should inject a registry)'
            )
        return self._provider_ui_descriptor_registry.get(provider_id)

    def acquire_project_claim(self, project_id: str, body: Optional[dict] = None) -> dict:
        self.authorize('acquire_project_claim', project_id=project_id)
        payload = body or {}
        # FE-P8 operator provenance: when authenticated, the lock-holder
        # operator is the authenticated principal subject — never the
        # request body's self-reported value (which a client could spoof).
        # With auth disabled (FCC_PLATFORM_ALLOW_INSECURE), fall back to the
        # body for local-dev compatibility.
        operator = self._authenticated_operator(payload.get('operator'))
        actor = self._authenticated_actor() or operator
        return self._claim_write_service.acquire(
            project_id,
            technology=payload.get('technology'),
            condition_hash=payload.get('condition_hash'),
            operator=operator,
            session_id=payload.get('session_id'),
            reason=payload.get('reason'),
            expires_at=payload.get('expires_at'),
            actor_subject=actor,
        )

    def release_project_claim(
        self, project_id: str, claim_id: str, body: Optional[dict] = None,
    ) -> dict:
        self.authorize('release_project_claim', project_id=project_id)
        payload = body or {}
        operator = self._authenticated_operator(payload.get('operator'))
        actor = self._authenticated_actor() or operator
        return self._claim_write_service.release(
            project_id,
            claim_id,
            operator=operator,
            reason=payload.get('reason'),
            action=payload.get('action') or 'released',
            actor_subject=actor,
        )

    def list_project_memberships(
        self, project_id: str, *,
        limit: Optional[int] = None, cursor: Optional[str] = None,
    ) -> dict:
        self.authorize('list_project_memberships', project_id=project_id)
        if self._rbac_read_service is None:
            raise RuntimeError(
                'list_project_memberships called but rbac_read_service is not wired '
                '(production composition must inject it; bare-read tests should '
                'inject a fake)'
            )
        return self._rbac_read_service.list_memberships(
            project_id, limit=limit, cursor=cursor,
        )

    def assign_project_membership(
        self, project_id: str, body: Optional[dict] = None,
    ) -> dict:
        self.authorize('assign_project_membership', project_id=project_id)
        if self._membership_write_service is None:
            raise RuntimeError(
                'assign_project_membership called but membership_write_service '
                'is not wired'
            )
        payload = body or {}
        actor = self._authenticated_actor()
        if not actor:
            # platform:admin gated, so the principal MUST be authenticated.
            # In a misconfigured composition (allow_insecure + admin op), we
            # still require an actor for the audit row — refuse loudly rather
            # than write an audit with an anonymous actor.
            raise PlatformAuthorizationError(
                'assign_project_membership requires an authenticated actor for audit'
            )
        return self._membership_write_service.assign(
            project_id,
            user_subject=payload.get('user_subject'),
            user_issuer=payload.get('user_issuer'),
            # The actor's VALIDATED issuer resolves a body that names only a
            # subject: membership is granted within one IdP, so the target row
            # lives under the same issuer the actor authenticated against
            # (mirrors create_project's actor_issuer forwarding).
            actor_issuer=self._actor_issuer(),
            role_key=payload.get('role_key'),
            actor_subject=actor,
            expires_at=payload.get('expires_at'),
            team=payload.get('team'),
        )

    def revoke_project_membership(
        self, project_id: str, body: Optional[dict] = None,
    ) -> dict:
        self.authorize('revoke_project_membership', project_id=project_id)
        if self._membership_write_service is None:
            raise RuntimeError(
                'revoke_project_membership called but membership_write_service '
                'is not wired'
            )
        payload = body or {}
        actor = self._authenticated_actor()
        if not actor:
            raise PlatformAuthorizationError(
                'revoke_project_membership requires an authenticated actor for audit'
            )
        return self._membership_write_service.revoke(
            project_id,
            user_subject=payload.get('user_subject'),
            user_issuer=payload.get('user_issuer'),
            actor_issuer=self._actor_issuer(),
            role_key=payload.get('role_key'),
            actor_subject=actor,
        )

    # ── 멀티챔버 P2 — chamber availability / registry / heartbeat ──────────────
    # Chambers are GLOBAL (not project-scoped), so authorize() is called WITHOUT
    # a project_id → token-only path (the membership union needs a project_id and
    # short-circuits to False). The heartbeat token (platform:chamber) is node-
    # scoped and never granted via project membership.

    def list_chambers(self) -> dict:
        self.authorize('list_chambers')
        if self._chamber_read_service is None:
            raise RuntimeError(
                'list_chambers called but chamber_read_service is not wired '
                '(production composition must inject it; bare tests should inject one)'
            )
        return self._chamber_read_service.chamber_availability()

    def register_chamber(self, body: Optional[dict] = None) -> dict:
        self.authorize('register_chamber')
        if self._chamber_write_service is None:
            raise RuntimeError(
                'register_chamber called but chamber_write_service is not wired'
            )
        payload = body or {}
        return self._chamber_write_service.register(
            chamber_id=payload.get('chamber_id'),
            name=payload.get('name'),
            base_url=payload.get('base_url'),
            enabled=payload.get('enabled', True),
            heartbeat_ttl_seconds=payload.get('heartbeat_ttl_seconds'),
        )

    def push_chamber_heartbeat(self, body: Optional[dict] = None) -> dict:
        self.authorize('push_chamber_heartbeat')
        payload = body or {}
        self._enforce_chamber_token_binding(
            (CHAMBER_BINDING_AXIS_ENVELOPE, payload.get('chamber_id')),
        )
        if self._chamber_write_service is None:
            raise RuntimeError(
                'push_chamber_heartbeat called but chamber_write_service is not wired'
            )
        ack = self._chamber_write_service.heartbeat(
            chamber_id=payload.get('chamber_id'),
            reported_status=payload.get('reported_status'),
            session_id=payload.get('session_id'),
            expires_at=payload.get('expires_at'),
            # C1 — carry the node's progress snapshot (in_use only; the domain
            # invariant rejects progress on idle → 400 problem+json).
            progress=payload.get('progress'),
            # M2 — carry the node's latest operational error (redacted at the
            # write boundary; optional on any status).
            last_error=payload.get('last_error'),
        )
        # 멀티챔버 P7/B4 — fan out the in_use progress to WS subscribers (ADR-0015
        # Option B real-time relay). The heartbeat() above already validated the
        # domain invariant (progress only with in_use), so a non-None normalized
        # progress here is guaranteed in_use. Broadcast is best-effort: a fan-out
        # failure NEVER breaks the ingest ack (the C1 ledger is the SSOT; WS is a
        # real-time complement to the GET /platform/chambers polling fallback).
        self._broadcast_progress(ack, payload.get('progress'))
        return ack

    def _enforce_chamber_token_binding(
        self,
        *declared_axes: tuple[str, object],
        require_binding: bool = False,
    ) -> None:
        """멀티챔버(2026-06-20) — per-chamber 토큰 바인딩 enforcement seam.

        chamber machine 토큰이 ``chamber_id`` claim 으로 한 chamber 에 묶여 있으면, 그
        토큰으로 *다른* chamber 를 heartbeat 하려는 시도를 거부(403). M1 staging 에서
        실측된 갭(공유 platform:chamber 토큰이 임의 chamber_id 를 heartbeat → ambiguous
        fleet) 정공. claim 없는 일반/레거시 토큰은 heartbeat에 한해 통과한다. 결과
        ingestion처럼 결과 귀속을 만드는 경계는 ``require_binding=True``로 claim 없는
        토큰도 거부한다. authZ (permission) 통과 이후의 *리소스 바인딩* 검사이므로
        authorize() 와 직교.

        2026-08-05 — a chamber-scoped request may declare its chamber on more
        than one axis (URL path *and* request envelope). Whichever axis
        disagrees, the request is attributing results to a chamber the caller is
        not authorized for, so **every** disagreement is a single authorization
        outcome (403) raised from this one seam. Enforcing an axis anywhere else
        would re-classify the same authorization failure as payload validation
        (400) purely by raise-site, which is exactly the defect this signature
        removes: axes are declared by the caller, never checked ad hoc after it.
        """
        principal = self._principal
        bound = (principal.chamber_id if principal is not None else '') or ''
        declared = tuple(
            (axis, str(value or '').strip()) for axis, value in declared_axes
        )
        if bound:
            for axis, reported in declared:
                if reported != bound:
                    raise PlatformAuthorizationError(
                        f"chamber token is bound to '{bound}' and cannot report "
                        f"chamber '{reported}' as the request {axis}"
                    )
        elif require_binding:
            raise PlatformAuthorizationError(
                'result ingestion requires a chamber-bound token'
            )
        # Cross-axis agreement is independent of the token claim so a legacy
        # unbound token can never mix two chambers inside one request either.
        for axis, reported in declared[1:]:
            reference_axis, reference = declared[0]
            if reported != reference:
                raise PlatformAuthorizationError(
                    f"request {reference_axis} chamber '{reference}' and "
                    f"{axis} chamber '{reported}' must be the same chamber"
                )

    def _broadcast_progress(self, ack: dict, raw_progress: object) -> None:
        broadcaster = self._progress_broadcaster
        if broadcaster is None:
            return
        progress = ChamberProgress.from_raw(raw_progress if isinstance(raw_progress, dict) else None)
        if progress is None:
            return
        # Defense in depth: only in_use heartbeats carry progress (domain invariant);
        # never fan out on a non-in_use ack even if a caller bypassed validation.
        if ack.get('reported_status') != ChamberNodeStatus.IN_USE.value:
            return
        try:
            broadcaster.publish(ChamberProgressEvent(
                chamber_id=ack.get('chamber_id'),
                progress=progress,
                session_id=ack.get('session_id'),
                occurred_at=ack.get('occurred_at'),
            ))
        except Exception:  # pragma: no cover — best-effort fan-out
            pass

    # ── 멀티챔버 P5 — 중앙 측정 프록시 ─────────────────────────────────────────
    # Chambers are GLOBAL (not project-scoped) → authorize WITHOUT a project_id
    # (token-only path). start is platform:claim gated (engineer-tier remote
    # action), progress is platform:read. The service gates on IDLE (in_use/
    # offline → 409), looks up the base_url, and forwards to the node Session API.

    def start_chamber_measurement(
        self, chamber_id: str, body: Optional[dict] = None,
    ) -> dict:
        self.authorize('start_chamber_measurement')
        if self._chamber_measurement_service is None:
            raise RuntimeError(
                'start_chamber_measurement called but chamber_measurement_service '
                'is not wired (production composition must inject it)'
            )
        payload = body or {}
        project_id = str(payload.get('project_id') or '').strip()
        sample_id = str(payload.get('sample_id') or '').strip()
        requires_snapshot = bool(
            getattr(self._chamber_measurement_service, 'requires_sample_snapshot', False)
        )
        if requires_snapshot and (not project_id or not sample_id):
            raise ValueError('project_id and sample_id are required')
        reference_requests = payload.get('project_result_reference_requests')
        if reference_requests is not None and not isinstance(reference_requests, list):
            raise ValueError('project_result_reference_requests must be a list')
        return self._chamber_measurement_service.start_measurement(
            chamber_id,
            project_id=project_id or None,
            sample_id=sample_id or None,
            published_plan_id=payload.get('published_plan_id'),
            project_result_reference_requests=reference_requests,
            reference_consumer_provider_id=(
                str(payload.get('reference_consumer_provider_id') or '').strip() or None
            ),
        )

    def get_chamber_measurement_progress(self, chamber_id: str) -> dict:
        self.authorize('get_chamber_measurement_progress')
        if self._chamber_measurement_service is None:
            raise RuntimeError(
                'get_chamber_measurement_progress called but '
                'chamber_measurement_service is not wired'
            )
        return self._chamber_measurement_service.measurement_progress(chamber_id)

    def push_chamber_result_ingestion(
        self, chamber_id: str, body: Optional[dict] = None,
    ) -> dict:
        """Accept a result outbox envelope from the bound chamber node."""
        self.authorize('push_chamber_result_ingestion')
        payload = body or {}
        # Both declared axes are bound in the single authorization seam, so a
        # token/path/envelope disagreement is one 403 outcome regardless of which
        # axis drifted. Binding runs before the service so an unauthorized
        # envelope never reaches central persistence.
        self._enforce_chamber_token_binding(
            (CHAMBER_BINDING_AXIS_PATH, chamber_id),
            (CHAMBER_BINDING_AXIS_ENVELOPE, payload.get('chamber_id')),
            require_binding=True,
        )
        if self._chamber_result_ingestion_service is None:
            raise RuntimeError(
                'push_chamber_result_ingestion called but the central ingestion '
                'service is not wired'
            )
        return self._chamber_result_ingestion_service.ingest(payload).as_dict()

    # ── plot-custody ② (2026-08-09) — 챔버별 플롯 저장 위치 ───────────────────

    def update_chamber_storage_root(
        self, chamber_id: str, body: Optional[dict] = None,
    ) -> dict:
        """Set (or clear) where this chamber PC writes measurement plots."""
        self.authorize('update_chamber_storage_root')
        payload = body or {}
        service = self._require_chamber_write_service('update_chamber_storage_root')
        # PATCH 규약: **생략 = 불변**, null = 삭제. 필드가 하나뿐이라도 구분을
        # 접어두지 않는다 — 접으면 필드가 둘이 되는 순간 규약이 갈라진다.
        if 'artifact_storage_root' not in payload:
            raise ValueError(
                'artifact_storage_root is required — omitting every field would '
                'be a no-op PATCH, which is a client mistake worth naming'
            )
        return service.set_artifact_storage_root(
            chamber_id=chamber_id,
            artifact_storage_root=payload.get('artifact_storage_root'),
        )

    def update_chamber_web_session_approval(
        self, chamber_id: str, body: Optional[dict] = None,
    ) -> dict:
        """운영자가 이 챔버의 웹 세션 승인을 선언한다 (챔버 모드 축)."""
        self.authorize('update_chamber_web_session_approval')
        payload = body or {}
        service = self._require_chamber_write_service(
            'update_chamber_web_session_approval',
        )
        # 형제 저장-위치 PATCH 와 같은 규약: **생략 = 불변**. 다만 여기서 null 은
        # 삭제가 아니라 **판정 철회**이고, 그것은 false 와 다른 값이다.
        if 'accepts_web_sessions' not in payload:
            raise ValueError(
                'accepts_web_sessions is required — omitting every field would '
                'be a no-op PATCH, which is a client mistake worth naming'
            )
        return service.set_web_session_approval(
            chamber_id=chamber_id,
            accepts_web_sessions=payload.get('accepts_web_sessions'),
        )

    def get_chamber_settings(self, chamber_id: str) -> dict:
        """What this chamber node must configure itself with (node-scoped)."""
        self.authorize('get_chamber_settings')
        # 머신 토큰은 경로 챔버에 묶인다 — 묶지 않으면 한 노드가 다른 방의 설정을
        # 읽는다. reference-bundle 과 같은 규율이다.
        self._enforce_chamber_token_binding(
            (CHAMBER_BINDING_AXIS_PATH, chamber_id),
            require_binding=True,
        )
        service = self._require_chamber_write_service('get_chamber_settings')
        return service.get_settings(chamber_id)

    # ── SPLIT-6 ② (2026-08-10) — 챔버별 계측기 연결 설정 ──────────────────────

    def get_chamber_equipment_config(self, chamber_id: str) -> dict:
        """This chamber's instrument connection settings (operator-scoped read).

        No chamber-token binding here, and that is the point of it being a
        separate operation: this door is for a person looking at the web screen,
        who holds ``platform:read`` and no chamber claim at all. The node's own
        read keeps its binding on ``get_chamber_settings`` above.
        """
        self.authorize('get_chamber_equipment_config')
        service = self._require_chamber_write_service('get_chamber_equipment_config')
        return service.get_equipment_config(chamber_id)

    def update_chamber_equipment_config(
        self, chamber_id: str, body: Optional[dict] = None,
    ) -> dict:
        """Merge an operator's edits into this chamber's instrument settings."""
        self.authorize('update_chamber_equipment_config')
        payload = body or {}
        service = self._require_chamber_write_service(
            'update_chamber_equipment_config'
        )
        # 저장 위치 축과 같은 규약이되 한 단계 아래다 — 거기서는 필드의 생략이
        # 불변이고, 여기서는 **키**의 생략이 불변이다. 봉투 자체가 빠진 것은
        # 그 규약의 밖이라 이름을 붙여 거절한다(무동작 PATCH 는 클라이언트 실수다).
        if 'equipment_config' not in payload:
            raise ValueError(
                'equipment_config is required — omitting it would be a no-op '
                'PATCH, which is a client mistake worth naming. Send only the '
                'keys you edited: an absent key is left unchanged, a null key '
                'is deleted.'
            )
        return service.patch_equipment_config(
            chamber_id=chamber_id,
            equipment_config=payload.get('equipment_config'),
        )

    # ── 로컬 신원 (2026-08-21) ───────────────────────────────────────────
    #
    # ⚠️ ``source_fingerprint`` is a caller-supplied opaque string used ONLY for
    # spraying detection, where it is immediately HMAC-digested. It is never
    # stored, logged, or echoed.

    def local_auth_login(self, body: Optional[dict] = None,
                         *, source_fingerprint: str = '') -> dict:
        self.authorize('local_auth_login')
        payload = body or {}
        return self._require_local_auth_service('local_auth_login').login(
            email=payload.get('email'),
            password=payload.get('password'),
            source_fingerprint=source_fingerprint,
        )

    def local_auth_refresh(self, body: Optional[dict] = None) -> dict:
        self.authorize('local_auth_refresh')
        payload = body or {}
        return self._require_local_auth_service('local_auth_refresh').refresh(
            refresh_token=payload.get('refresh_token'),
        )

    def local_auth_me(self) -> dict:
        self.authorize('local_auth_me')
        return self._require_local_auth_service('local_auth_me').me(self._principal)

    def local_auth_change_password(
        self, body: Optional[dict] = None, *, access_token: str = '',
        source_fingerprint: str = '',
    ) -> dict:
        self.authorize('local_auth_change_password')
        payload = body or {}
        service = self._require_local_auth_service('local_auth_change_password')
        return service.change_password(
            self._principal,
            current_password=payload.get('current_password'),
            new_password=payload.get('new_password'),
            # ⚠️ This surface is a password-guessing surface too, and until
            # 2026-08-22 it reported nothing to the spraying detector — an
            # attacker spreading guesses with stolen tokens left no trace at all.
            source_fingerprint=source_fingerprint,
            # ⚠️ The presented token is retired on success. Without it, "I changed
            # my password" does not end even the session doing the changing.
            access_token=access_token,
        )

    def local_auth_logout(
        self, body: Optional[dict] = None, *, access_token: str = '',
    ) -> dict:
        self.authorize('local_auth_logout')
        payload = body or {}
        return self._require_local_auth_service('local_auth_logout').logout(
            self._principal,
            access_token=access_token,
            # ⚠️ Revoking only the access token revokes nothing: a captured refresh
            # token keeps minting new ones for its full 7-day life.
            refresh_token=payload.get('refresh_token'),
        )

    def unlock_local_account(self, body: Optional[dict] = None) -> dict:
        """관리자가 로컬 계정의 로그인 잠금을 푼다 (2026-08-23).

        ⚠️ 행위자는 **인증된 principal** 에서만 온다 — body 에서 받으면 감사 원장이
        *누가 풀었는가* 에 대해 요청자가 적은 값을 기록한다(FE-P8 operator provenance,
        형제 멤버십 축과 같은 규율).
        """
        self.authorize('unlock_local_account')
        payload = body or {}
        # ⚠️ **빠진 칸은 «그런 계정 없음» 이 아니다** (적대 평가 3R 실측). 요청 칸은
        # ``email`` 이고 응답 칸은 ``subject`` 라, 응답을 보고 ``{"subject": …}`` 로
        # 다시 보낸 관리자는 404 *"no local account for that identifier"* 를 받는다 —
        # 계정은 멀쩡히 존재하는데. 그 404 의 설명이 존재하는 이유가 바로 *"오타와
        # 성공한 해제를 구분하지 못하는 관리자"* 를 막기 위해서인데, 칸 이름을 틀린
        # 것은 주소의 오타가 아니고 같은 답으로 떨어지면 안 된다.
        identifier = payload.get('email')
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(
                "unlock_local_account requires a non-empty 'email' field "
                '(the response envelope calls the same value "subject", but the '
                'request field is "email")'
            )
        service = self._require_local_auth_service('unlock_local_account')
        return service.unlock_account(self._principal, subject=identifier)

    def _require_local_auth_service(self, operation: str):
        if self._local_auth_service is None:
            raise RuntimeError(
                f'{operation} called but local_auth_service is not wired — this '
                'deployment does not run AUTH_MODE=local_jwt'
            )
        return self._local_auth_service

    def _require_chamber_write_service(self, operation: str):
        if self._chamber_write_service is None:
            raise RuntimeError(
                f'{operation} called but chamber_write_service is not wired'
            )
        return self._chamber_write_service

    # ── plot-dual-custody ① (2026-08-09) — 플롯 원본 보관 현황 ────────────────
    # 중앙은 회사 파일서버도 챔버 PC 로컬 디스크도 열 수 없으므로 **판정하지 않는다.**
    # 판정은 증거가 있는 노드에서 나오고 이 표면은 그것을 받아 보관·조회한다. 참조
    # 데이터가 중앙→로컬 PULL 인 것의 거울상이고, 방향이 반대인 이유는 하나다 —
    # 증거가 있는 쪽이 판정한다.

    def push_artifact_custody_report(
        self, chamber_id: str, body: Optional[dict] = None,
    ) -> dict:
        """Accept plot-custody verdicts observed by the bound chamber node."""
        self.authorize('push_artifact_custody_report')
        payload = body or {}
        # result-ingestion 과 **같은** 두 축 바인딩. 경로만 묶고 봉투를 안 묶으면 한
        # 챔버의 토큰으로 다른 챔버의 세션에 보관 판정을 심을 수 있고, 그 판정은
        # 발행 게이트가 참조하는 값이다.
        self._enforce_chamber_token_binding(
            (CHAMBER_BINDING_AXIS_PATH, chamber_id),
            (CHAMBER_BINDING_AXIS_ENVELOPE, payload.get('chamber_id')),
            require_binding=True,
        )
        service = self._require_artifact_custody_service('push_artifact_custody_report')
        receipt = service.store_report(
            chamber_id=chamber_id,
            provider_id=str(payload.get('provider_id') or ''),
            sessions=payload.get('sessions') or [],
        )
        return {
            'schema_version': ARTIFACT_CUSTODY_REPORT_SCHEMA_VERSION,
            'chamber_id': chamber_id,
            'accepted': receipt.get('accepted', []),
            'superseded': receipt.get('superseded', []),
            # ⚠️ 시각은 **서비스가 찍는다**. 여기서 벽시계를 부르지 않는 것이 이 모듈의
            # import 집합이 이미 단언하던 사실이고(이 파일에는 ``datetime`` import 가
            # 없다), 그것을 어긴 한 줄이 정확히 이 자리에서 ``NameError`` 로 죽었다 —
            # 세 형제 서비스에 각각 정의된 ``_utc_now_iso`` 를 이 계층으로 복사해 온
            # 것이라, 이름은 있어 보이는데 여기엔 없었다. 같은 이름의 칸을 찍는 형제
            # 수신 서비스(``chamber_result_ingestion_service``)도 서비스에서 찍는다.
            'received_at': receipt.get('received_at'),
        }

    def get_project_artifact_custody(self, project_id: str) -> dict:
        """Whether this project's plots are where the audit will look for them."""
        self.authorize('get_project_artifact_custody', project_id=project_id)
        service = self._require_artifact_custody_service('get_project_artifact_custody')
        return service.get_project_custody(project_id)

    def get_artifact_custody_snapshot(
        self, project_id: str, snapshot_id: str,
    ) -> dict:
        """Which specific plots are missing or diverged for one reported session."""
        self.authorize('get_artifact_custody_snapshot', project_id=project_id)
        service = self._require_artifact_custody_service('get_artifact_custody_snapshot')
        # 프로젝트 귀속 확인은 read 어댑터의 WHERE 절에 있다 — 서비스 밖에서 한 번 더
        # 조회하면 TOCTOU 창이 생기고 왕복도 는다. 미귀속은 404 이고, 그것이 존재를
        # 노출하지 않는 유일한 답이다.
        return service.get_snapshot(project_id, snapshot_id)

    def _require_artifact_custody_service(self, operation: str):
        """미배선이면 loud 실패 — 조용히 "이상 없음"을 돌려주지 않는다.

        보관 조회에서 빈 결과는 통과처럼 읽힌다. 배선이 빠진 것을 그 모양으로
        강등하면 이 축이 존재하는 이유가 정확히 뒤집힌다.
        """
        if self._artifact_custody_service is None:
            raise RuntimeError(
                f'{operation} called but artifact_custody_service is not wired'
            )
        return self._artifact_custody_service

    # ── Web sample inventory CRUD/history/export ─────────────────────────────

    def list_sample_inventory(
        self, *, project_id: Optional[str] = None, team: Optional[str] = None,
        status: Optional[str] = None, as_of: Optional[str] = None,
        after: Optional[str] = None, limit: int = 100,
        include_deleted: bool = False,
    ) -> dict:
        self.authorize('list_sample_inventory', project_id=project_id)
        return self._require_sample_inventory_service('list_sample_inventory').list_samples(
            project_id=project_id, team=team, status=status, as_of=as_of,
            after=after, limit=limit, include_deleted=include_deleted,
        )

    def create_sample(self, project_id: str, body: Optional[dict] = None) -> dict:
        self.authorize('create_sample', project_id=project_id)
        return self._require_sample_inventory_service('create_sample').create_sample(
            project_id, body or {}, actor_subject=self._require_authenticated_actor(),
        )

    def get_sample(self, project_id: str, sample_id: str, *, as_of: Optional[str] = None) -> dict:
        self.authorize('get_sample', project_id=project_id)
        return self._require_sample_inventory_service('get_sample').get_sample(
            project_id, sample_id, as_of=as_of,
        )

    def patch_sample(self, project_id: str, sample_id: str, body: Optional[dict] = None) -> dict:
        self.authorize('patch_sample', project_id=project_id)
        payload = dict(body or {})
        expected_version = payload.pop('expected_version', None)
        return self._require_sample_inventory_service('patch_sample').patch_sample(
            project_id, sample_id, payload, expected_version=expected_version,
            actor_subject=self._require_authenticated_actor(),
        )

    def change_sample_status(self, project_id: str, sample_id: str, body: Optional[dict] = None) -> dict:
        self.authorize('change_sample_status', project_id=project_id)
        payload = body or {}
        return self._require_sample_inventory_service('change_sample_status').change_status(
            project_id, sample_id, payload.get('status'),
            expected_version=payload.get('expected_version'),
            actor_subject=self._require_authenticated_actor(),
        )

    def delete_sample(self, project_id: str, sample_id: str, body: Optional[dict] = None) -> dict:
        self.authorize('delete_sample', project_id=project_id)
        payload = body or {}
        return self._require_sample_inventory_service('delete_sample').soft_delete(
            project_id, sample_id, expected_version=payload.get('expected_version'),
            actor_subject=self._require_authenticated_actor(),
        )

    def hard_delete_sample(self, sample_id: str) -> dict:
        # No project_id is accepted here. The dedicated token is resolved through
        # the global system-admin path; project membership cannot widen it.
        try:
            self.authorize('hard_delete_sample')
        except PermissionError:
            principal = self._principal
            actor = str(getattr(principal, 'subject', '') or 'anonymous').strip()
            _ROUTE_LOGGER.warning(
                'security_event sample hard-delete denied',
                extra={
                    'actor_subject': actor or 'anonymous',
                    'operation': 'hard_delete_sample',
                    'correlation_id': current_request_id(),
                },
            )
            raise
        return self._require_sample_inventory_service('hard_delete_sample').hard_delete(
            sample_id, actor_subject=self._require_authenticated_actor(),
        )

    def list_sample_history(self, project_id: str, sample_id: str, *,
                            after: Optional[str] = None, limit: int = 100) -> dict:
        self.authorize('list_sample_history', project_id=project_id)
        return self._require_sample_inventory_service('list_sample_history').list_history(
            project_id, sample_id, after=after, limit=limit,
        )

    def list_sample_intakes(self, project_id: str, sample_id: str) -> dict:
        self.authorize('list_sample_intakes', project_id=project_id)
        return self._require_sample_inventory_service('list_sample_intakes').list_intakes(
            project_id, sample_id,
        )

    def list_sample_custody_events(self, project_id: str, sample_id: str) -> dict:
        self.authorize('list_sample_custody_events', project_id=project_id)
        return self._require_sample_inventory_service(
            'list_sample_custody_events').list_custody_events(project_id, sample_id)

    def append_sample_custody_event(self, project_id: str, sample_id: str,
                                    body: Optional[dict] = None) -> dict:
        self.authorize('append_sample_custody_event', project_id=project_id)
        return self._require_sample_inventory_service(
            'append_sample_custody_event').append_custody_event(
            project_id, sample_id, body or {},
            actor_subject=self._require_authenticated_actor(),
        )

    def delete_sample_custody_event(self, project_id: str, sample_id: str,
                                    event_id: str) -> dict:
        self.authorize('delete_sample_custody_event', project_id=project_id)
        return self._require_sample_inventory_service(
            'delete_sample_custody_event').delete_custody_event(
            project_id, sample_id, event_id,
            actor_subject=self._require_authenticated_actor(),
        )

    def export_sample_inventory(
        self, project_id: str, template: str, *, team: Optional[str] = None,
        status: Optional[str] = None, as_of: Optional[str] = None,
        include_deleted: bool = False,
    ):
        self.authorize('export_sample_inventory', project_id=project_id)
        if self._sample_export_service is None:
            raise RuntimeError('sample export service is not wired')
        return self._sample_export_service.export(
            project_id, template, team=team, status=status, as_of=as_of,
            include_deleted=include_deleted,
        )

    def _require_sample_inventory_service(self, operation: str) -> CentralSampleInventoryService:
        if self._sample_inventory_service is None:
            raise RuntimeError(f'{operation} called but sample_inventory_service is not wired')
        return self._sample_inventory_service

    def _authenticated_actor(self) -> Optional[str]:
        """Return the authenticated principal's subject when usable.

        Returns ``None`` when no principal is bound (auth disabled), the
        principal is anonymous, or the subject is empty. Audit-bearing sample
        writes must use ``_require_authenticated_actor`` and never invent a
        fallback subject.
        """
        if self._principal is None:
            return None
        subject = (self._principal.subject or '').strip()
        if not subject or subject == 'anonymous':
            return None
        return subject

    def _require_authenticated_actor(self) -> str:
        actor = self._authenticated_actor()
        if not actor:
            raise PermissionError('authenticated principal is required for sample mutation')
        return actor

    def _actor_issuer(self) -> str:
        """The bound principal's validated issuer ('' when no principal).

        Only ever read from the authenticated principal — never from the
        request body, which the client controls.
        """
        principal = self._principal
        if principal is None:
            return ''
        return str(getattr(principal, 'issuer', '') or '').strip()

    def _authenticated_operator(self, body_operator: object) -> str:
        """Resolve the lock-holder operator with the FE-P8 provenance rule.

        Authenticated principal subject WINS over the request body — a client
        cannot spoof an operator they are not. With auth disabled, the body
        value is honored (local-dev compatibility); a missing body value when
        auth is disabled is left for ClaimWriteService to reject with
        ``ValueError`` (operator is required).
        """
        actor = self._authenticated_actor()
        if actor:
            return actor
        # Auth disabled / anonymous path — preserve the legacy body-supplied
        # operator (FE-P3-write tests built before FE-P8 still construct
        # requests with explicit operator).
        return '' if body_operator is None else str(body_operator)


# ── RFC 9457 error contract (B1) ────────────────────────────────────────────
# Declarative exception→ErrorCode table (ordered most-specific-first, mirroring
# the old isinstance chain). HTTP status derives from the single
# ``ERROR_CODE_STATUS`` SSOT (status byte-identical) and every error now carries
# a machine-readable ``code``. Unmapped exceptions fall through to
# ``INTERNAL_ERROR`` — the platform surface's historical 500 default.
#
# Ordering constraints preserved from the old chain:
#   - membership/provider 404 group is checked before the 409/503/400 families.
#   - ClaimConflictError / ClaimPairingError (RuntimeError subclasses) precede
#     the ValueError→400 branch so they are not swallowed by the 500 default.
#   - MembershipRoleUnknownError IS a ValueError → resolves to VALIDATION_ERROR
#     (400) via the trailing ValueError row (no special branch needed).
#: 이 모듈의 로거. 5xx 를 렌더할 때 원인을 남기는 데 쓴다 — 아래 경계 참조.
_ROUTE_LOGGER = get_logger('platform_api')

_PLATFORM_ERROR_CODE_TABLE: tuple[tuple[type, ErrorCode], ...] = (
    (PlatformAuthorizationError, ErrorCode.FORBIDDEN),
    # 신원 축 EMS 정합 (2026-08-21). ⚠️ BOTH rows MUST stay ABOVE the
    # ``PermissionError`` row below: both classes DERIVE from PermissionError,
    # and this table is most-specific-first. Below it they are unreachable and
    # every login failure silently becomes a 403 FORBIDDEN — which tells a
    # browser 'stop' where it needed 'show the login form'.
    (InvalidCredentialsError, ErrorCode.AUTH_INVALID_CREDENTIALS),
    (PasswordChangeRequiredError, ErrorCode.AUTH_PASSWORD_CHANGE_REQUIRED),
    (LocalTokenError, ErrorCode.AUTH_INVALID_CREDENTIALS),
    (PermissionError, ErrorCode.FORBIDDEN),
    # A password that fails the policy is a client-fixable 400, and
    # ``PasswordRejected`` derives from ValueError, so it only needs to precede
    # the generic ValueError row for its ``params.field`` to survive.
    (PasswordRejected, ErrorCode.VALIDATION_ERROR),
    # 관리자 잠금 해제(2026-08-23) — 없는 계정은 클라이언트가 고칠 수 있는 404 다.
    # ⚠️ ``LocalUserStoreError``(503) **위**에 있어야 한다: 그 둘은 상속 관계가
    # 아니라 순서가 강제되지는 않지만, 원인이 다른 두 실패를 같은 이웃에 두면
    # 다음 세션이 «저장소 장애» 로 읽는다.
    (LocalAccountNotFoundError, ErrorCode.NOT_FOUND),
    # 리프레시 회전 subject 상한 (2026-08-23) → 429. ``RuntimeError`` 서브클래스라
    # ``LocalUserStoreError``(503) **위**에 있어야 한다 — 아래로 내려가도 상속으로는
    # 안 잡히지만, 순서가 의미를 갖는 표에서 이웃이 곧 설명이다.
    (RefreshRotationLimitedError, ErrorCode.RATE_LIMITED),
    (LocalUserStoreError, ErrorCode.UPSTREAM_UNAVAILABLE),
    (MembershipNotFoundError, ErrorCode.MEMBERSHIP_NOT_FOUND),
    (MembershipUserUnknownError, ErrorCode.MEMBERSHIP_NOT_FOUND),
    (ProviderUiDescriptorNotFound, ErrorCode.PROVIDER_NOT_FOUND),
    (ClaimConflictError, ErrorCode.CLAIM_CONFLICT),
    (ClaimPairingError, ErrorCode.CLAIM_CONFLICT),
    (CentralReadError, ErrorCode.UPSTREAM_UNAVAILABLE),
    (SelectionRevisionConflictError, ErrorCode.CONFLICT),
    # plan-delivery (2026-09-02). NotFound(LookupError) → 404 and the malformed
    # envelope(ValueError) → 422 must both precede the generic ValueError → 400
    # row; the identity backend failure is a 503 like every other central read.
    (PublishedPlanExpectationNotFound, ErrorCode.NOT_FOUND),
    (PublishedPlanExpectationError, ErrorCode.VALIDATION_ERROR),
    (PublishedPlanIdentityError, ErrorCode.UPSTREAM_UNAVAILABLE),
    (SelectionProviderNotFoundError, ErrorCode.NOT_FOUND),
    (SelectionCandidateNotFoundError, ErrorCode.NOT_FOUND),
    (SelectionCrossScopeError, ErrorCode.NOT_FOUND),
    (ReferenceNotFoundError, ErrorCode.NOT_FOUND),
    (ReferenceRetiredError, ErrorCode.CONFLICT),
    (ReferenceHashMismatchError, ErrorCode.VALIDATION_ERROR),
    (ReferenceIncompatibleError, ErrorCode.VALIDATION_ERROR),
    (ReferenceScopeMismatchError, ErrorCode.VALIDATION_ERROR),
    (ReferenceSourceMismatchError, ErrorCode.VALIDATION_ERROR),
    (CentralProjectReferenceError, ErrorCode.UPSTREAM_UNAVAILABLE),
    (ClaimWriteError, ErrorCode.UPSTREAM_UNAVAILABLE),
    (CentralRbacReadError, ErrorCode.UPSTREAM_UNAVAILABLE),
    (UserWriteError, ErrorCode.UPSTREAM_UNAVAILABLE),
    (MembershipWriteError, ErrorCode.UPSTREAM_UNAVAILABLE),
    # 멀티챔버 P2 — chamber backend failures map to 503 (same as the other
    # central read/write families) so a chamber outage surfaces loud, never as a
    # blank availability dashboard.
    (CentralChamberReadError, ErrorCode.UPSTREAM_UNAVAILABLE),
    (ChamberWriteError, ErrorCode.UPSTREAM_UNAVAILABLE),
    (ChamberResultIngestionUpstreamError, ErrorCode.UPSTREAM_UNAVAILABLE),
    # plot-dual-custody ① — 없는/타 프로젝트 스냅샷은 404(존재를 노출하지 않는다),
    # 노드가 보낸 계약 위반은 400, 중앙 원장 장애는 503. NotFound 가 상위 클래스
    # (CentralArtifactCustodyError)보다 **먼저** 와야 한다 — most-specific-first 가
    # 아니면 클라이언트가 고칠 수 있는 404 가 중앙 장애 503 으로 둔갑한다.
    (ArtifactCustodyNotFoundError, ErrorCode.NOT_FOUND),
    # 등록되지 않은 provider 로 온 보고도 404 다 — provider 는 운영자가 등록하는
    # 참조 데이터이지 인입되는 것이 아니므로 **클라이언트 잘못**이고, 형제
    # ``ReferenceProviderNotFoundError`` 가 같은 이유로 404 다. 이 줄이 없던 동안
    # 그 경우는 상위 클래스로 떨어져 503(중앙 장애)으로 나갔고, 운영자는 보낸 값이
    # 아니라 컨테이너를 보러 갔다(실측 2026-09-03).
    (ArtifactCustodyProviderNotFoundError, ErrorCode.NOT_FOUND),
    (ArtifactCustodyReportRejected, ErrorCode.VALIDATION_ERROR),
    (CentralArtifactCustodyError, ErrorCode.UPSTREAM_UNAVAILABLE),
    # 멀티챔버 P5 — measurement proxy. Unknown chamber → 404, not-idle (in_use/
    # offline) → 409, node forward failure → 503. ChamberNotFoundError(LookupError)
    # + ChamberNotAvailableError/ChamberProxyError(RuntimeError) precede the
    # ValueError → 400 row so they are not swallowed by the 500 default.
    (ChamberNotFoundError, ErrorCode.NOT_FOUND),
    (ChamberNotAvailableError, ErrorCode.CONFLICT),
    (ChamberProxyError, ErrorCode.UPSTREAM_UNAVAILABLE),
    # Phase 1 — unknown project_id on detail read → 404. CentralProjectError
    # (backend failure) → 503. Both precede the ValueError → 400 row.
    # W3 백엔드 — ProjectIdentifierConflictError IS a CentralProjectError subclass,
    # so it MUST stay above its superclass: most-specific-first is what makes a
    # duplicate 관리번호/모델명 a client-fixable 409 instead of a 503 outage.
    #
    # The DEDICATED ``PROJECT_IDENTIFIER_CONFLICT`` code (not the generic
    # ``CONFLICT``) is what lets a client tell "this identifier is taken, fix the
    # input" apart from every other 409 on this surface without string-matching
    # ``detail``; ``params.field`` then names WHICH identifier. The code is
    # platform-scoped in ``ERROR_CODE_SURFACE_SCOPE`` so it is published in this
    # surface's OpenAPI ``ErrorCode`` enum only — the headless artifact, which has
    # no project directory write path, is unchanged.
    (ProjectNotFoundError, ErrorCode.NOT_FOUND),
    (ProjectIdentifierConflictError, ErrorCode.PROJECT_IDENTIFIER_CONFLICT),
    (CentralProjectError, ErrorCode.UPSTREAM_UNAVAILABLE),
    # W3 백엔드 — sample-inventory attribution axis. ProjectModelUnresolvedError IS
    # a ValueError, so it MUST stay above the generic ValueError row: that ordering
    # is what turns "this project has no model yet" into its own machine code
    # instead of an indistinguishable "your request is malformed" 400. Status is
    # the same 400 either way, so no existing client's status handling shifts.
    # Wave 3 — reference catalog. The three client-fixable outcomes precede the
    # generic rows so none of them is swallowed: an unknown provider or revision
    # is a 404, a lost publish race or a non-candidate is a 409, and only a real
    # backend fault is a 503. ReferenceScopeError IS a ValueError, so it must sit
    # above the ValueError row — that ordering is what turns "this family is
    # room-scoped, you declared project" into a fixable 400 with its own meaning
    # rather than an indistinguishable malformed-request.
    # ⚠️ ORDER IS LOAD-BEARING — ``ReferenceProviderNotRegisteredError`` IS a
    # ``ReferenceProviderNotFoundError``. Below its parent it would never be
    # reached and the distinction would vanish silently: the wire would keep
    # answering the generic 404 while the code claimed otherwise. Both are 404,
    # so no client's status handling shifts; what changes is that a client can
    # now tell "you named an id nobody knows" (fix the id) from "an operator
    # must register this provider" (nothing you can do).
    (ReferenceProviderNotRegisteredError, ErrorCode.REFERENCE_PROVIDER_NOT_REGISTERED),
    (ReferenceProviderNotFoundError, ErrorCode.NOT_FOUND),
    (ReferenceRevisionNotFoundError, ErrorCode.NOT_FOUND),
    (ReferencePublishConflictError, ErrorCode.CONFLICT),
    # 반쪽 게시 거부와 상태 위반(후보를 fork 하려는 시도)도 409 다. 세 conflict 를
    # 하나의 예외 타입으로 접지 않는 이유는 상태코드가 같아서가 아니라 **사실이
    # 다르기** 때문이다 — 접으면 되돌릴 방법이 사람용 문장을 파싱하는 것뿐이다.
    (ReferenceCoupledPublishError, ErrorCode.CONFLICT),
    (ReferenceStateConflictError, ErrorCode.CONFLICT),
    (CentralReferenceError, ErrorCode.UPSTREAM_UNAVAILABLE),
    # 성적서 §6 장비목록 — 없는 목록(그리고 **다른 프로젝트의 목록**)은 404,
    # 자연키 중복·확정본 편집·확정 불가는 409, 진짜 백엔드 장애만 503.
    # EquipmentListConflictError 는 RuntimeError 라 아래 ValueError → 400 행보다
    # 위에 있어야 500 기본값에 삼켜지지 않는다. 신규 ErrorCode 는 만들지 않았다 —
    # NOT_FOUND / CONFLICT / UPSTREAM_UNAVAILABLE 이 세 결과를 모두 덮으므로
    # 이 표면의 OpenAPI ErrorCode enum 이 그대로 유지된다.
    (EquipmentListNotFoundError, ErrorCode.NOT_FOUND),
    (EquipmentListConflictError, ErrorCode.CONFLICT),
    (CentralTestEquipmentListError, ErrorCode.UPSTREAM_UNAVAILABLE),
    (ProjectModelUnresolvedError, ErrorCode.PROJECT_MODEL_UNRESOLVED),
    (SampleInventoryNotFoundError, ErrorCode.NOT_FOUND),
    (SampleInventoryConflictError, ErrorCode.CONFLICT),
    (SampleInventoryExportCategoryUnresolvedError,
     ErrorCode.SAMPLE_EXPORT_CATEGORY_UNRESOLVED),
    (SampleInventoryExportTemplateError, ErrorCode.VALIDATION_ERROR),
    (CentralSampleInventoryReadError, ErrorCode.UPSTREAM_UNAVAILABLE),
    (CentralSampleInventoryWriteError, ErrorCode.UPSTREAM_UNAVAILABLE),
    # Phase G — report session identity/not-found → 404, duplicate edition → 409,
    # report backend failure →
    # 503. ReportEditionConflictError (RuntimeError) precedes the ValueError → 400
    # row so it is not swallowed by the 500 default; the report service reuses
    # ProjectNotFoundError (already mapped to 404 above) for an unknown project.
    (ReportSessionNotFoundError, ErrorCode.NOT_FOUND),
    (ReportEditionConflictError, ErrorCode.CONFLICT),
    (CentralReportError, ErrorCode.UPSTREAM_UNAVAILABLE),
    (ProjectResultReferenceRequestUnprocessableError,
     ErrorCode.REFERENCE_REQUEST_UNPROCESSABLE),
    (ValueError, ErrorCode.VALIDATION_ERROR),
)
#: Anything not in the table. Sourced from the shared sentinel so the three web
#: surfaces cannot drift apart again (they did — see ``UNCLASSIFIED_ERROR_CODE``).
_PLATFORM_DEFAULT_ERROR_CODE = UNCLASSIFIED_ERROR_CODE


# The RFC 9457 ``params`` extension used to be assembled here by a private
# ``_problem_params(exc)`` isinstance chain — the only site in the repository that
# populated it, which is why a headless 400 could not name the offending field
# while a platform 409 could. It is gone: an exception now DECLARES its structured
# context (``PROBLEM_PARAM_FIELDS``) and ``build_problem_details`` reads the
# declaration, so this boundary needs no exception table and the other two
# surfaces get the same behaviour without a second and third copy of one.
#
# ``ProjectIdentifierConflictError`` still publishes ``{field, resource}`` and its
# 409 body is byte-identical; only the place that knows so has moved onto the
# exception, which already held both attributes for exactly this purpose.


def api_error_status(exc: Exception) -> int:
    """HTTP status for adapter-level exceptions without importing FastAPI.

    Thin delegation onto the ``ERROR_CODE_STATUS`` SSOT — the exception taxonomy
    lives in ``_PLATFORM_ERROR_CODE_TABLE`` and the status is derived from the
    resolved :class:`ErrorCode` (B1). Status is byte-identical to the pre-B1
    isinstance chain (sealed by ``test_api_error_contract_*``).
    """
    return status_for_code(
        resolve_error_code(
            exc, _PLATFORM_ERROR_CODE_TABLE, default=_PLATFORM_DEFAULT_ERROR_CODE,
        )
    )


def create_platform_router(
    adapter: PlatformApiAdapter,
    principal_resolver=None,
    *,
    ws_heartbeat_seconds: float = 20.0,
    credential_throttle=None,
):
    """Create FastAPI routes for the platform read surface.

    ``ws_heartbeat_seconds`` controls the keepalive ping interval for the
    ``/platform/chambers/events`` WS fan-out (멀티챔버 P7/B4). Set to ``0`` to
    disable heartbeats (tests).

    ``credential_throttle`` is the account-axis charger
    (:class:`application.common.credential_throttle.CredentialThrottle`). ``None``
    charges nothing, which is the byte-identical pre-2026-08-22 behaviour and what
    a router-only test gets. ⚠️ Production does **not** take that default —
    ``create_platform_app`` supplies one, and
    ``TestTheProductionEntrypointWiresTheAccountAxis`` asserts it through the
    factory the ASGI entrypoint actually calls.
    """
    try:
        from fastapi import APIRouter, HTTPException, Request, Response, WebSocket
        from fastapi.websockets import WebSocketDisconnect
    except ImportError as exc:
        raise RuntimeError(
            'FastAPI is required to create the platform API router. '
            'Install fastapi and uvicorn in the web runtime.'
        ) from exc
    import asyncio
    globals()['Request'] = Request
    globals()['Response'] = Response
    # This module uses ``from __future__ import annotations`` (PEP 563), so route
    # handler annotations are strings FastAPI resolves against module globals.
    # Expose WebSocket so ``websocket: WebSocket`` on the WS handler resolves to
    # the type (else FastAPI treats it as a missing query param). Mirror of the
    # Request/Response exposure above.
    globals()['WebSocket'] = WebSocket

    router = APIRouter()

    def request_adapter(request=None) -> PlatformApiAdapter:
        if principal_resolver is None:
            return adapter
        return adapter.with_principal(principal_resolver.resolve(request))

    def route_error_boundary(handler):
        @wraps(handler)
        def _wrapped(*args, **kwargs):
            try:
                return handler(*args, **kwargs)
            except (
                PlatformAuthorizationError,
                PermissionError,
                ClaimConflictError,
                ClaimPairingError,
                ClaimWriteError,
                CentralReadError,
                SelectionRevisionConflictError,
                SelectionProviderNotFoundError,
                SelectionCandidateNotFoundError,
                SelectionCrossScopeError,
                ReferenceNotFoundError,
                ReferenceRetiredError,
                ReferenceHashMismatchError,
                ReferenceIncompatibleError,
                ReferenceScopeMismatchError,
                ReferenceSourceMismatchError,
                CentralProjectReferenceError,
                # FE-P8 error classes — the 404 / 503 / 400 mapping flows
                # through api_error_status; HTTPException reads it.
                MembershipNotFoundError,
                MembershipUserUnknownError,
                MembershipWriteError,
                CentralRbacReadError,
                UserWriteError,
                ProviderUiDescriptorNotFound,
                # 멀티챔버 P2 — chamber read/write backend failures → 503.
                CentralChamberReadError,
                ChamberWriteError,
                ChamberResultIngestionError,
                ChamberResultIngestionUpstreamError,
                # 멀티챔버 P5 — measurement proxy 404 / 409 / 503.
                ChamberNotFoundError,
                ChamberNotAvailableError,
                ChamberProxyError,
                # Phase 1 — project entry 404 / 503.
                ProjectNotFoundError,
                CentralProjectError,
                SampleInventoryNotFoundError,
                SampleInventoryConflictError,
                SampleInventoryExportCategoryUnresolvedError,
                SampleInventoryExportTemplateError,
                CentralSampleInventoryReadError,
                CentralSampleInventoryWriteError,
                # Phase G — report surface 404 / 409 / 503.
                ReportEditionConflictError,
                ReportSessionNotFoundError,
                CentralReportError,
                # 성적서 §6 장비목록 404 / 409 / 503. 에러표에만 등재하고 여기
                # 빠지면 매핑은 있는데 boundary 가 잡지 않아 500 으로 샌다.
                EquipmentListNotFoundError,
                EquipmentListConflictError,
                CentralTestEquipmentListError,
                # Wave 3 참조 카탈로그 404 / 409 / 503. 이 다섯은 에러표
                # (`_PLATFORM_ERROR_CODE_TABLE`)에 **등재돼 있었는데 여기 빠져
                # 있었다** — 넷 다 RuntimeError 서브클래스이고 catch-all 이 없어
                # 선언된 404/409/503 이 전부 500 으로 샜다. 바로 위 §6 주석이
                # 경고하는 그 형태이며, 참조 표면이 그 두 번째 사례다.
                # 재발은 `TestEveryMappedErrorIsCaughtByTheBoundary` 가 표와
                # 이 튜플의 포함관계를 AST 로 파생해 막는다.
                ReferenceProviderNotFoundError,
                ReferenceRevisionNotFoundError,
                ReferencePublishConflictError,
                ReferenceCoupledPublishError,
                ReferenceStateConflictError,
                CentralReferenceError,
                # 플롯 보관 404 / 503. 참조 축과 **같은 결함**이었다 — 에러표
                # (`_PLATFORM_ERROR_CODE_TABLE`)에 등재됐는데 여기 빠져 있었고,
                # 둘 다 RuntimeError 서브클래스라 선언된 상태코드가 500 으로 샜다.
                # 두 웨이브가 독립적으로 같은 자리를 밟았다는 것이, 이 정합을
                # 주석이 아니라 파생 단언으로 잠근 이유 그 자체다
                # (`TestEveryMappedErrorIsCaughtByTheBoundary` 가 이것을 잡았다).
                ArtifactCustodyNotFoundError,
                ArtifactCustodyProviderNotFoundError,
                # 신원 축 EMS 정합 (2026-08-21). InvalidCredentialsError /
                # PasswordChangeRequiredError / LocalTokenError derive from
                # PermissionError and PasswordRejected from ValueError, so they
                # are already inside this tuple by inheritance — they are named
                # anyway so the AST containment gate can see them, and so that
                # re-parenting one later cannot silently drop it out.
                # LocalUserStoreError is a RuntimeError and would otherwise be
                # the exact leak the reference-surface comment above describes.
                InvalidCredentialsError,
                PasswordChangeRequiredError,
                LocalTokenError,
                PasswordRejected,
                LocalUserStoreError,
                # 관리자 잠금 해제 (2026-08-23). ``LookupError`` 서브클래스라 위
                # 어느 항목에도 상속으로 들어오지 않는다 — 즉 **세 번째로** 같은
                # 자리를 밟을 수 있었고, 실제로 이 웨이브의 라우트 테스트가 착지
                # 전에 `404 != 500` 으로 재현했다. 위 두 주석이 경고하는 그 형태다.
                LocalAccountNotFoundError,
                RefreshRotationLimitedError,
                CentralArtifactCustodyError,
                ProjectResultReferenceRequestUnprocessableError,
                ValueError,
            ) as exc:
                problem = build_problem_details(
                    exc,
                    _PLATFORM_ERROR_CODE_TABLE,
                    default=_PLATFORM_DEFAULT_ERROR_CODE,
                )
                # RFC 9457 (B1): detail IS the ProblemDetails dict; rendered as a
                # top-level ``application/problem+json`` body by
                # ``install_problem_details_handler``. Status byte-identical.
                # ⚠️ **429 는 ``Retry-After`` 없이 나가면 안 된다** (RFC 9110
                # §10.2.3). 없으면 클라이언트가 즉시 재시도하고, 그 재시도가 창을
                # 내내 고정시켜 스로틀이 스스로 만든 정체가 된다. 미들웨어의 429 는
                # 이미 그 헤더를 싣고, 이 경계도 같은 상수를 쓴다(형식이 둘로 갈리면
                # 클라이언트가 한쪽만 이해한다).
                # ⚠️ **5xx 는 로그를 남긴다.** ``LocalUserStoreError`` 의 docstring 이
                # *"원인은 `raise ... from exc` 로 서버 트레이스백에 그대로 남으므로
                # 진단은 잃지 않는다"* 고 적지만, 이 경계가 problem+json 으로 렌더하고
                # 끝내면 체인된 원인은 **아무 데도 가지 않는다** — 적대 평가가 실
                # 배포에서 그것을 확인했다(503 하나에 대해 로그 0줄, 운영자는 DB 장애로
                # 오진한다). 4xx 는 클라이언트가 고칠 일이므로 남기지 않는다.
                if problem.status >= 500:
                    _ROUTE_LOGGER.exception(
                        'platform operation failed with %s (%s)',
                        problem.status, problem.code,
                    )
                    # ⚠️ **로거만으로는 운영자에게 닿지 않는다** (적대 평가 2R 실측:
                    # 503 하나에 `docker logs` **0줄**). 5xx 는 «DB 가 죽었다» 와
                    # «마이그레이션이 빠졌다» 를 가르는 유일한 문장이므로 컨테이너
                    # 런타임이 잡는 스트림에 반드시 나가야 한다. ⚠️ 그 두 줄을 여기
                    # 손으로 적지 않는다 — 이 계층은 raw ``print`` 가 금지돼 있고
                    # (``TestPhase7NoPrintInInfrastructure``), 그 금지는 옳다.
                    # 어댑터는 협력자를 이름으로 부른다.
                    announce(
                        CHANNEL_PLATFORM_API,
                        f'{problem.status} {_code_text(problem.code)}: '
                        f'{_cause_chain(exc)}',
                    )
                retry_after = getattr(exc, 'retry_after_seconds', None)
                headers = None
                if isinstance(retry_after, int) and retry_after > 0:
                    # ⚠️ **두 429 는 같은 모양이어야 한다.** 미들웨어의 429 는
                    # ``params.retry_after`` 와 RateLimit 헤더를 함께 싣는다 — 그것이
                    # 이 저장소가 만든 **기계가 읽는 채널**이다. 여기서 헤더만 싣고
                    # 본문 params 를 빼면 그 채널을 읽는 클라이언트는 새 tier 에 대해
                    # 아무것도 못 읽는다(적대 평가 2R: 형식이 실제로 갈라져 있었다).
                    problem = ProblemDetails(
                        status=problem.status,
                        title=problem.title,
                        code=problem.code,
                        detail=problem.detail,
                        params={
                            **(dict(problem.params) if problem.params else {}),
                            'retry_after': int(retry_after),
                        },
                    )
                    headers = {RETRY_AFTER_HEADER: str(int(retry_after))}
                    limit = getattr(exc, 'rate_limit', None)
                    if isinstance(limit, int) and limit > 0:
                        headers[RATE_LIMIT_LIMIT_HEADER] = str(limit)
                        headers[RATE_LIMIT_REMAINING_HEADER] = '0'
                raise HTTPException(
                    status_code=problem.status,
                    detail=problem.as_dict(),
                    headers=headers,
                ) from exc

        return _wrapped

    def _emit_page(response, page: dict) -> list:
        # Body stays a plain array (backward compatible); the next-page keyset
        # cursor rides in the response header (GitHub-style).
        next_cursor = page.get('next_cursor')
        if next_cursor:
            response.headers[PLATFORM_NEXT_CURSOR_HEADER] = next_cursor
        return page['items']

    def get_project_coverage(
        project_id: str, request: Request, response: Response,
        limit: Optional[int] = None, cursor: str = '', technology: str = '',
    ):
        page = request_adapter(request).get_project_coverage(
            project_id, limit=limit, cursor=cursor or None, technology=technology or None,
        )
        return _emit_page(response, page)

    def resolve_effective_project_permissions(project_id: str, request: Request):
        permissions = request_adapter(request).resolve_effective_project_permissions(
            project_id,
        )
        return {'permissions': sorted(permissions)}

    def list_project_claims(
        project_id: str, request: Request, response: Response,
        limit: Optional[int] = None, cursor: str = '', technology: str = '',
    ):
        page = request_adapter(request).list_project_claims(
            project_id, limit=limit, cursor=cursor or None, technology=technology or None,
        )
        return _emit_page(response, page)

    def get_project_sync_status(project_id: str, request: Request):
        # Single freshness object (not a paginated array) — returned directly.
        return request_adapter(request).get_project_sync_status(project_id)

    def get_project_progress(project_id: str, request: Request):
        # Phase 6 — bounded per-(area, bucket) rollup array (no pagination).
        return request_adapter(request).get_project_progress(project_id)

    def list_project_report_sessions(project_id: str, request: Request):
        # P5-C — bounded reportable session choices with node routing metadata.
        return request_adapter(request).list_project_report_sessions(project_id)

    def list_project_result_selections(
        project_id: str, provider_id: str, request: Request, response: Response,
        limit: Optional[int] = None, cursor: str = '',
    ):
        page = request_adapter(request).list_project_result_selections(
            project_id, provider_id, limit=limit, cursor=cursor or None,
        )
        return _emit_page(response, page)

    def list_project_result_attempts(
        project_id: str, provider_id: str, condition_hash: str,
        request: Request, response: Response, limit: Optional[int] = None,
        cursor: str = '',
    ):
        page = request_adapter(request).list_project_result_attempts(
            project_id, provider_id, condition_hash,
            limit=limit, cursor=cursor or None,
        )
        return _emit_page(response, page)

    def select_project_result(
        project_id: str, provider_id: str, condition_hash: str,
        request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).select_project_result(
            project_id, provider_id, condition_hash, body,
        )

    def ingest_published_plan_expectation(
        project_id: str, provider_id: str,
        request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).ingest_published_plan_expectation(
            project_id, provider_id, body,
        )

    def clear_project_result_selection(
        project_id: str, provider_id: str, condition_hash: str,
        request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).clear_project_result_selection(
            project_id, provider_id, condition_hash, body,
        )

    def list_project_result_references(
        project_id: str, request: Request, response: Response,
        provider_id: str = '', state: str = '', limit: Optional[int] = None,
        cursor: str = '',
    ):
        page = request_adapter(request).list_project_result_references(
            project_id, producer_provider_id=provider_id or None,
            state=state or None, limit=limit, cursor=cursor or None,
        )
        return _emit_page(response, page)

    def create_project_result_reference(
        project_id: str, request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).create_project_result_reference(project_id, body)

    def retire_project_result_reference(
        project_id: str, revision_id: str, request: Request,
        body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).retire_project_result_reference(
            project_id, revision_id, body,
        )

    def list_projects(
        request: Request, response: Response, status: str = 'active',
        q: str = '', limit: Optional[int] = None, cursor: str = '',
    ):
        # Plain array of the project directory filtered by status (read-open — any
        # authenticated principal). Defaults to 'active' (in-progress);
        # ?status=completed or ?status=all widen it. W3 백엔드 — ?q= searches
        # server-side (관리번호 포함) and ?limit=/?cursor= page by keyset; the
        # continuation token rides in the response header, body stays an array.
        page = request_adapter(request).list_projects(
            status=status, q=q or None, limit=limit, cursor=cursor or None,
        )
        return _emit_page(response, page)

    def create_project(request: Request, body: Optional[dict] = None):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).create_project(body)

    def get_project(project_id: str, request: Request):
        # Single project detail object — returned directly.
        return request_adapter(request).get_project(project_id)

    def update_project(
        project_id: str, request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).update_project(project_id, body)

    # ── Phase G — test_reports 성적서 surface ─────────────────────────────────
    def list_reports(project_id: str, request: Request):
        # Plain array of the project's reports (newest first) — returned directly.
        return request_adapter(request).list_reports(project_id)

    def create_report(
        project_id: str, request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).create_report(project_id, body)

    def get_report_citation(
        project_id: str, request: Request, edition: str = '', session_id: str = '',
    ):
        # Single citation object; optional ?edition feeds the derived report_number.
        return request_adapter(request).get_report_citation(
            project_id, edition=edition or None, session_id=session_id or None,
        )

    # 성적서 §6 장비목록 — 프로젝트가 실제로 사용한 장비/시험용 소프트웨어.
    def list_test_equipment_lists(project_id: str, request: Request):
        # Plain array of the project's equipment lists (newest first).
        return request_adapter(request).list_test_equipment_lists(project_id)

    def create_test_equipment_list(
        project_id: str, request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).create_test_equipment_list(project_id, body)

    def get_test_equipment_list(
        project_id: str, equipment_list_id: str, request: Request,
    ):
        return request_adapter(request).get_test_equipment_list(
            project_id, equipment_list_id,
        )

    def replace_test_equipment_list_items(
        project_id: str, equipment_list_id: str, request: Request,
        body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).replace_test_equipment_list_items(
            project_id, equipment_list_id, body,
        )

    def attach_test_equipment_list(
        project_id: str, equipment_list_id: str, request: Request,
        body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).attach_test_equipment_list(
            project_id, equipment_list_id, body,
        )

    def confirm_test_equipment_list(
        project_id: str, equipment_list_id: str, request: Request,
    ):
        return request_adapter(request).confirm_test_equipment_list(
            project_id, equipment_list_id,
        )

    def list_providers(request: Request):
        # Provider summary list (read-only) — returned directly.
        return request_adapter(request).list_providers()

    def get_provider_ui_descriptor(provider_id: str, request: Request):
        # Single descriptor object (read-only proxy) — returned directly.
        return request_adapter(request).get_provider_ui_descriptor(provider_id)

    def acquire_project_claim(
        project_id: str, request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).acquire_project_claim(project_id, body)

    def release_project_claim(
        project_id: str, claim_id: str, request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).release_project_claim(project_id, claim_id, body)

    def list_project_memberships(
        project_id: str, request: Request, response: Response,
        limit: Optional[int] = None, cursor: str = '',
    ):
        page = request_adapter(request).list_project_memberships(
            project_id, limit=limit, cursor=cursor or None,
        )
        return _emit_page(response, page)

    def assign_project_membership(
        project_id: str, request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).assign_project_membership(project_id, body)

    def revoke_project_membership(
        project_id: str, request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).revoke_project_membership(project_id, body)

    def complete_project(project_id: str, request: Request):
        return request_adapter(request).complete_project(project_id)

    def reopen_project(project_id: str, request: Request):
        return request_adapter(request).reopen_project(project_id)

    def list_reference_revisions(
        request: Request, response: Response, provider_id: str,
        family: str = '', scope_kind: str = '', scope_id: str = '',
        state: str = '', limit: Optional[int] = None, cursor: str = '',
    ):
        page = request_adapter(request).list_reference_revisions(
            provider_id,
            family=family or None,
            scope_kind=scope_kind or None,
            scope_id=scope_id or None,
            state=state or None,
            limit=limit,
            cursor=cursor or None,
        )
        return _emit_page(response, page)

    def get_reference_revision(provider_id: str, revision_id: str, request: Request):
        return request_adapter(request).get_reference_revision(
            provider_id, revision_id,
        )

    def create_reference_revision(
        provider_id: str, request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).create_reference_revision(provider_id, body)

    def fork_reference_revision(
        provider_id: str, revision_id: str, request: Request,
        body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).fork_reference_revision(
            provider_id, revision_id, body,
        )

    def list_reference_families(provider_id: str, request: Request):
        return request_adapter(request).list_reference_families(provider_id)

    def create_authored_reference_revision(
        provider_id: str, request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).create_authored_reference_revision(
            provider_id, body,
        )

    def update_reference_revision_rows(
        provider_id: str, revision_id: str, request: Request,
        body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).update_reference_revision_rows(
            provider_id, revision_id, body,
        )

    def update_reference_revision_entries(
        provider_id: str, revision_id: str, request: Request,
        body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).update_reference_revision_entries(
            provider_id, revision_id, body,
        )

    def publish_reference_revision(
        provider_id: str, revision_id: str, request: Request,
        body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).publish_reference_revision(
            provider_id, revision_id, body,
        )

    def get_chamber_reference_bundle(
        chamber_id: str, request: Request,
        scope_project_id: str = '', bundle_etag: str = '',
        limit: Optional[int] = None, cursor: str = '',
    ):
        # A single bundle object (revisions + tag + unchanged flag) — returned
        # directly rather than through _emit_page, because the cut is one answer
        # and splitting it across a header would let a caller act on half of it.
        return request_adapter(request).get_chamber_reference_bundle(
            chamber_id,
            scope_project_id=scope_project_id or None,
            bundle_etag=bundle_etag or None,
            limit=limit,
            cursor=cursor or None,
        )

    def update_chamber_storage_root(
        chamber_id: str, request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).update_chamber_storage_root(chamber_id, body)

    def _deny_if_throttled(operation, request, body=None) -> None:
        """Charge the account tier and raise 429 when the attempt is over budget.

        ⚠️ Charged **before** the adapter is touched, so a denied attempt costs no
        database round-trip and no bcrypt verify — a throttle that runs after the
        expensive work bounds the answer rate but not the cost, and the cost is
        what an attacker is actually spending.

        The denial is rendered from the *same* ``ProblemDetails`` value object and
        the *same* header constants the rate-limit middleware uses, so the two
        enforcement points cannot drift into two 429 formats.
        """
        if credential_throttle is None:
            return
        outcome = credential_throttle.check(operation, body)
        if outcome is None:
            return
        code = ErrorCode.RATE_LIMITED
        problem = ProblemDetails(
            status=status_for_code(code),
            title=ERROR_CODE_TITLES[code],
            code=code,
            detail=RATE_LIMIT_DETAIL,
            params={'retry_after': outcome.retry_after_seconds},
        )
        raise HTTPException(
            status_code=problem.status,
            detail=problem.as_dict(),
            headers={
                RETRY_AFTER_HEADER: str(outcome.retry_after_seconds),
                RATE_LIMIT_LIMIT_HEADER: str(outcome.limit),
                RATE_LIMIT_REMAINING_HEADER: str(outcome.remaining),
            },
        )

    def local_auth_login(request: Request, body: Optional[dict] = None):
        request, body = _normalize_request_body_args(request, body)
        # ⚠️ Enforce first, charge later. The check does not spend the budget —
        # ``record_failure`` below does, and only when the attempt actually failed.
        # A success must leave no trace, or attempts-to-429 becomes a channel that
        # reports when an account last authenticated (see ``CredentialThrottle``).
        _deny_if_throttled('local_auth_login', request, body)
        try:
            return request_adapter(request).local_auth_login(
                body, source_fingerprint=_source_fingerprint(request),
            )
        except Exception:
            # ⚠️ EVERY exception spends a slot, not just InvalidCredentialsError.
            # A caller who can make this path raise some other way would otherwise
            # probe for free, and the budget self-heals in one window — so
            # fail-closed is the cheap direction here.
            if credential_throttle is not None:
                credential_throttle.record_failure('local_auth_login', body)
            raise

    def local_auth_refresh(request: Request, body: Optional[dict] = None):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).local_auth_refresh(body)

    def local_auth_me(request: Request):
        return request_adapter(request).local_auth_me()

    def local_auth_change_password(request: Request, body: Optional[dict] = None):
        request, body = _normalize_request_body_args(request, body)
        # ⚠️ **No account tier here, and that is a decision.** A first draft put
        # this surface on the login bucket. Adversarial review showed the cost lands
        # on the one workflow every new operator must complete: the forced
        # first-password-change screen, where a policy rejection (weak new password,
        # 400) costs the same budget as a guess — because the tier is charged before
        # the handler can tell them apart. Sharing also imported the login door's DoS
        # into an authenticated surface: a stolen token could spend the victim's
        # login budget.
        #
        # Nothing is lost by leaving it out. This surface is already bounded by the
        # account **lockout** — five wrong current-passwords and the account locks
        # for fifteen minutes — which is far tighter than six per minute, and which
        # this same wave taught this surface to honour (see ``change_password``).
        return request_adapter(request).local_auth_change_password(
            body,
            access_token=_bearer_token_of(request),
            source_fingerprint=_source_fingerprint(request),
        )

    def local_auth_logout(request: Request, body: Optional[dict] = None):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).local_auth_logout(
            body, access_token=_bearer_token_of(request),
        )

    def unlock_local_account(request: Request, body: Optional[dict] = None):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).unlock_local_account(body)

    def update_chamber_web_session_approval(
        chamber_id: str, request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).update_chamber_web_session_approval(
            chamber_id, body,
        )

    def get_chamber_settings(chamber_id: str, request: Request):
        return request_adapter(request).get_chamber_settings(chamber_id)

    def get_chamber_equipment_config(chamber_id: str, request: Request):
        return request_adapter(request).get_chamber_equipment_config(chamber_id)

    def update_chamber_equipment_config(
        chamber_id: str, request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).update_chamber_equipment_config(
            chamber_id, body,
        )

    def push_artifact_custody_report(
        chamber_id: str, request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).push_artifact_custody_report(chamber_id, body)

    def get_project_artifact_custody(project_id: str, request: Request):
        # 프로젝트 요약 + 세션 행이 한 답이다 — 페이지로 쪼개면 호출자가 절반을 보고
        # "이상 없음"이라고 판단할 수 있다(차단 세션이 다음 페이지에 있을 때).
        return request_adapter(request).get_project_artifact_custody(project_id)

    def get_artifact_custody_snapshot(
        project_id: str, snapshot_id: str, request: Request,
    ):
        return request_adapter(request).get_artifact_custody_snapshot(
            project_id, snapshot_id,
        )

    def list_chambers(request: Request):
        # Single availability object (items + server_time) — returned directly,
        # not paginated (chamber count is small).
        return request_adapter(request).list_chambers()

    def register_chamber(request: Request, body: Optional[dict] = None):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).register_chamber(body)

    def push_chamber_heartbeat(request: Request, body: Optional[dict] = None):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).push_chamber_heartbeat(body)

    def start_chamber_measurement(
        chamber_id: str, request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).start_chamber_measurement(chamber_id, body)

    def get_chamber_measurement_progress(chamber_id: str, request: Request):
        return request_adapter(request).get_chamber_measurement_progress(chamber_id)

    def push_chamber_result_ingestion(
        chamber_id: str, request: Request, body: Optional[dict] = None,
    ):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).push_chamber_result_ingestion(chamber_id, body)

    # Web sample inventory — JSON CRUD/history plus XLSX download. The browser
    # never uploads a workbook; the server owns the export bytes and filename.
    def list_sample_inventory(
        request: Request, project_id: str = '', team: str = '', status: str = '',
        as_of: str = '', after: str = '', limit: int = 100,
        include_deleted: bool = False,
    ):
        return request_adapter(request).list_sample_inventory(
            project_id=project_id or None, team=team or None, status=status or None,
            as_of=as_of or None, after=after or None, limit=limit,
            include_deleted=include_deleted,
        )

    def create_sample(project_id: str, request: Request, body: Optional[dict] = None):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).create_sample(project_id, body)

    def get_sample(project_id: str, sample_id: str, request: Request, as_of: str = ''):
        return request_adapter(request).get_sample(project_id, sample_id, as_of=as_of or None)

    def patch_sample(project_id: str, sample_id: str, request: Request,
                     body: Optional[dict] = None):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).patch_sample(project_id, sample_id, body)

    def change_sample_status(project_id: str, sample_id: str, request: Request,
                             body: Optional[dict] = None):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).change_sample_status(project_id, sample_id, body)

    def delete_sample(project_id: str, sample_id: str, request: Request,
                      body: Optional[dict] = None):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).delete_sample(project_id, sample_id, body)

    def hard_delete_sample(sample_id: str, request: Request):
        return request_adapter(request).hard_delete_sample(sample_id)

    def list_sample_history(project_id: str, sample_id: str, request: Request,
                            after: str = '', limit: int = 100):
        return request_adapter(request).list_sample_history(
            project_id, sample_id, after=after or None, limit=limit,
        )

    def list_sample_intakes(project_id: str, sample_id: str, request: Request):
        return request_adapter(request).list_sample_intakes(project_id, sample_id)

    def list_sample_custody_events(project_id: str, sample_id: str, request: Request):
        return request_adapter(request).list_sample_custody_events(project_id, sample_id)

    def append_sample_custody_event(project_id: str, sample_id: str, request: Request,
                                    body: Optional[dict] = None):
        request, body = _normalize_request_body_args(request, body)
        return request_adapter(request).append_sample_custody_event(
            project_id, sample_id, body,
        )

    def delete_sample_custody_event(project_id: str, sample_id: str, event_id: str,
                                    request: Request):
        return request_adapter(request).delete_sample_custody_event(
            project_id, sample_id, event_id,
        )

    def export_sample_inventory(
        project_id: str, template: str, request: Request,
        response: Response, team: str = '', status: str = '', as_of: str = '',
        include_deleted: bool = False,
    ):
        result = request_adapter(request).export_sample_inventory(
            project_id, template, team=team or None, status=status or None,
            as_of=as_of or None, include_deleted=include_deleted,
        )
        response.headers['Content-Disposition'] = (
            f'attachment; filename="{_safe_content_disposition_filename(result.filename)}"'
        )
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return Response(
            content=result.content, media_type=result.content_type,
            headers={
                'Content-Disposition': (
                    f'attachment; filename="{_safe_content_disposition_filename(result.filename)}"'
                ),
                'X-Content-Type-Options': 'nosniff',
            },
        )

    route_handlers = {
        'list_projects': list_projects,
        'create_project': create_project,
        'get_project': get_project,
        'update_project': update_project,
        'get_project_coverage': get_project_coverage,
        'list_project_claims': list_project_claims,
        'get_project_sync_status': get_project_sync_status,
        'get_project_progress': get_project_progress,
        'list_project_report_sessions': list_project_report_sessions,
        'list_project_result_selections': list_project_result_selections,
        'list_project_result_attempts': list_project_result_attempts,
        'select_project_result': select_project_result,
        'ingest_published_plan_expectation': ingest_published_plan_expectation,
        'clear_project_result_selection': clear_project_result_selection,
        'list_project_result_references': list_project_result_references,
        'create_project_result_reference': create_project_result_reference,
        'retire_project_result_reference': retire_project_result_reference,
        'list_providers': list_providers,
        'get_provider_ui_descriptor': get_provider_ui_descriptor,
        'acquire_project_claim': acquire_project_claim,
        'release_project_claim': release_project_claim,
        'list_project_memberships': list_project_memberships,
        'assign_project_membership': assign_project_membership,
        'revoke_project_membership': revoke_project_membership,
        'complete_project': complete_project,
        'reopen_project': reopen_project,
        'list_chambers': list_chambers,
        'register_chamber': register_chamber,
        'push_chamber_heartbeat': push_chamber_heartbeat,
        'start_chamber_measurement': start_chamber_measurement,
        'get_chamber_measurement_progress': get_chamber_measurement_progress,
        'push_chamber_result_ingestion': push_chamber_result_ingestion,
        'list_sample_inventory': list_sample_inventory,
        'create_sample': create_sample,
        'get_sample': get_sample,
        'patch_sample': patch_sample,
        'change_sample_status': change_sample_status,
        'delete_sample': delete_sample,
        'hard_delete_sample': hard_delete_sample,
        'list_sample_history': list_sample_history,
        'list_sample_intakes': list_sample_intakes,
        'list_sample_custody_events': list_sample_custody_events,
        'append_sample_custody_event': append_sample_custody_event,
        'delete_sample_custody_event': delete_sample_custody_event,
        'export_sample_inventory': export_sample_inventory,
        'list_reports': list_reports,
        'create_report': create_report,
        'get_report_citation': get_report_citation,
        'list_test_equipment_lists': list_test_equipment_lists,
        'create_test_equipment_list': create_test_equipment_list,
        'get_test_equipment_list': get_test_equipment_list,
        'replace_test_equipment_list_items': replace_test_equipment_list_items,
        'attach_test_equipment_list': attach_test_equipment_list,
        'confirm_test_equipment_list': confirm_test_equipment_list,
        'list_reference_revisions': list_reference_revisions,
        'get_reference_revision': get_reference_revision,
        'create_reference_revision': create_reference_revision,
        'fork_reference_revision': fork_reference_revision,
        'list_reference_families': list_reference_families,
        'create_authored_reference_revision': create_authored_reference_revision,
        'update_reference_revision_rows': update_reference_revision_rows,
        'update_reference_revision_entries': update_reference_revision_entries,
        'publish_reference_revision': publish_reference_revision,
        'get_chamber_reference_bundle': get_chamber_reference_bundle,
        'update_chamber_storage_root': update_chamber_storage_root,
        'unlock_local_account': unlock_local_account,
        'update_chamber_web_session_approval': update_chamber_web_session_approval,
        'local_auth_login': local_auth_login,
        'local_auth_refresh': local_auth_refresh,
        'local_auth_me': local_auth_me,
        'local_auth_change_password': local_auth_change_password,
        'local_auth_logout': local_auth_logout,
        'get_chamber_settings': get_chamber_settings,
        'get_chamber_equipment_config': get_chamber_equipment_config,
        'update_chamber_equipment_config': update_chamber_equipment_config,
        'push_artifact_custody_report': push_artifact_custody_report,
        'get_project_artifact_custody': get_project_artifact_custody,
        'get_artifact_custody_snapshot': get_artifact_custody_snapshot,
    }
    for name, handler in route_handlers.items():
        method, path = PLATFORM_API_ROUTES[name]
        router.add_api_route(path, route_error_boundary(handler), methods=[method])

    # Internal provider authorization boundary. Keep it outside the public
    # route map/OpenAPI artifact; its implementation still uses the same
    # authenticated platform adapter and central RBAC read service.
    method, path = PLATFORM_INTERNAL_RBAC_ROUTES['effective_project_permissions']
    router.add_api_route(
        path,
        route_error_boundary(resolve_effective_project_permissions),
        methods=[method],
        include_in_schema=False,
    )

    # Ops probes (2026-07-20). Paths are assembled from the surface prefix + the
    # ``health_probe_policy`` suffix SSOT, so the throttle's probe-budget rule
    # (which matches on those same suffixes) and the routes can never disagree
    # about what a probe path is.
    #
    # Like ``/platform/metrics``, these are NOT declared in PLATFORM_API_ROUTES:
    # the OpenAPI artifact describes the *business* contract the SPA is generated
    # from, and an infrastructure probe is not part of it (declaring it would
    # regenerate docs/api + the frontend client for an endpoint no client calls).
    _probe_prefix = PLATFORM_API_ROUTES['list_projects'][1].rsplit('/', 1)[0]

    @router.get(f'{_probe_prefix}{LIVENESS_PATH_SUFFIX}')
    def _liveness_endpoint():  # pragma: no cover — exercised via TestClient
        return liveness_payload()

    @router.get(f'{_probe_prefix}{READINESS_PATH_SUFFIX}')
    def _readiness_endpoint():  # pragma: no cover — exercised via TestClient
        snapshot = adapter.readiness()
        if snapshot.ready:
            return snapshot.as_dict()
        # Not-ready is a 503 rendered through the repository's existing RFC 9457
        # contract (ErrorCode → status SSOT), not a bespoke shape. ``detail`` is
        # the constant policy string: which dependency failed is in the process
        # log, never in an unauthenticated body.
        problem = ProblemDetails(
            status=status_for_code(ErrorCode.UPSTREAM_UNAVAILABLE),
            title=ERROR_CODE_TITLES[ErrorCode.UPSTREAM_UNAVAILABLE],
            code=ErrorCode.UPSTREAM_UNAVAILABLE,
            detail=READINESS_UNAVAILABLE_DETAIL,
        )
        raise HTTPException(status_code=problem.status, detail=problem.as_dict())

    # OBS-2 phase 3 parity (FE-P0d S5, 2026-05-27): Prometheus exposition
    # endpoint. Mirror of ``GET /headless/metrics`` — auth-exempt by design
    # (scrapers live in trusted network segments). Returns the shared
    # :class:`ApiMetricsRegistry` rendering with namespace ``fcc_platform``.
    # B4 enables the WS section for ``/platform/chambers/events`` lifecycle
    # metrics. Not declared in PLATFORM_API_ROUTES, so the OpenAPI contract
    # artifact is unaffected.
    @router.get('/platform/metrics')
    def _metrics_endpoint():  # pragma: no cover — exercised via TestClient
        from fastapi.responses import PlainTextResponse
        adapter.refresh_metrics()  # scrape 시점 챔버 derived gauge 갱신(best-effort)
        registry = adapter.metrics_registry
        body = registry.render() if registry is not None else ''
        return PlainTextResponse(
            content=body,
            media_type='text/plain; version=0.0.4',
        )

    # 멀티챔버 P7/B4 — central progress relay WS fan-out (ADR-0015 Option B). Mirror
    # of the node Session API ``/session/events`` handler (auth → accept → trace →
    # heartbeat ping → subscribe → fan-out → cleanup + 4-state/4-reason metrics).
    # Reuses the existing FastAPI/uvicorn WS server surface (no new outbound dep).
    _ws_path = PLATFORM_API_ROUTES['subscribe_chamber_progress'][1]

    @router.websocket(_ws_path)
    async def _chamber_events(websocket: WebSocket) -> None:
        import uuid as _uuid
        from fcc_test_contracts.common.logging_channel import get_logger
        from fcc_test_contracts.common.correlation import (
            bind_connection_id,
            bind_trace_context,
        )
        from fcc_test_contracts.common.inbound_http import extract_ws_trace_context
        from fcc_test_contracts.common.ws_subprotocol_auth import (
            accepted_subprotocol,
            bearer_credential_request,
            parse_bearer_offer,
        )

        logger = get_logger('platform_api')
        connection_id = _uuid.uuid4().hex
        client = getattr(getattr(websocket, 'client', None), 'host', '?')

        _ws_trace = extract_ws_trace_context(websocket)
        _ws_trace_id = _ws_trace.trace_id
        _ws_span_id = _ws_trace.span_id
        _ws_sampled = _ws_trace.sampled
        _ws_tracestate = _ws_trace.tracestate

        # W3-4 (2026-08-01) — browser bearer credential via Sec-WebSocket-Protocol
        # (RFC 6455 §4.1; ws_subprotocol_auth SSOT). Parsed once here for the
        # accept() negotiation below; bearer_credential_request() re-derives the
        # identical offer from the same header to build the resolver's
        # credential view, so the two decisions cannot diverge.
        _ws_bearer_offer = parse_bearer_offer(websocket.headers.get('sec-websocket-protocol', ''))

        _ws_registry = adapter.metrics_registry
        if _ws_registry is not None:
            _ws_registry.inc_ws_connection(WS_STATE_CONNECTING)

        # AuthZ before accept — platform:read gate (subscribe_chamber_progress).
        # Principal resolution runs INSIDE this try (M2b, W3-4): a malformed or
        # expired bearer credential raises PermissionError from the resolver's
        # own decode step, same as an authorize() denial. Resolving outside this
        # boundary let a bad token escape uncaught — the ASGI server then tears
        # the socket down with 1006 (abnormal closure) instead of the policy
        # 1008 the frontend reconnect policy expects. This wave is the first to
        # make that path reachable (browsers previously had no way to offer a
        # bearer credential on a WS upgrade at all).
        try:
            ws_adapter = (
                adapter
                if principal_resolver is None
                else adapter.with_principal(
                    principal_resolver.resolve(bearer_credential_request(websocket))
                )
            )
            ws_adapter.authorize('subscribe_chamber_progress')
        except (PlatformAuthorizationError, PermissionError) as exc:
            logger.info(
                'platform.ws connect=denied connection_id=%s client=%s reason=%s',
                connection_id, client, exc,
            )
            if _ws_registry is not None:
                _ws_registry.dec_ws_connection(WS_STATE_CONNECTING)
                _ws_registry.inc_ws_closed_total(WS_CLOSE_REASON_DENIED)
            await websocket.close(code=1008)
            return

        _conn_ctx = bind_connection_id(connection_id)
        _conn_ctx.__enter__()
        _trace_ctx = bind_trace_context(
            _ws_trace_id, _ws_span_id, sampled=_ws_sampled, tracestate=_ws_tracestate,
        )
        _trace_ctx.__enter__()

        await websocket.accept(
            subprotocol=accepted_subprotocol(
                _ws_bearer_offer,
                principal_resolver_active=principal_resolver is not None,
            ),
        )
        if _ws_registry is not None:
            _ws_registry.dec_ws_connection(WS_STATE_CONNECTING)
            _ws_registry.inc_ws_connection(WS_STATE_OPEN)
        await websocket.send_json({
            'kind': 'connection_accepted',
            'payload': [connection_id],
            'connection_id': connection_id,
            'trace_id': _ws_trace_id,
            'span_id': _ws_span_id,
            'tracestate': _ws_tracestate,
        })
        logger.info(
            'platform.ws connect=ok connection_id=%s client=%s', connection_id, client,
        )

        close_code = 1000
        heartbeat_failed = False
        ws_stream_task = asyncio.current_task()
        heartbeat_task = None
        if ws_heartbeat_seconds > 0:
            async def _heartbeat():
                nonlocal close_code, heartbeat_failed
                try:
                    while True:
                        await asyncio.sleep(ws_heartbeat_seconds)
                        await websocket.send_json({
                            'kind': 'ping',
                            'payload': [],
                            'connection_id': connection_id,
                        })
                except (WebSocketDisconnect, asyncio.CancelledError):
                    return
                except Exception:  # pragma: no cover — defensive
                    heartbeat_failed = True
                    close_code = 1001
                    try:
                        await websocket.close(code=close_code)
                    except Exception:
                        pass
                    if ws_stream_task is not None:
                        ws_stream_task.cancel()
                    return
            heartbeat_task = asyncio.create_task(_heartbeat())

        broadcaster = adapter.progress_broadcaster
        try:
            if broadcaster is not None:
                # ``async with`` so the subscription is ALWAYS unregistered on
                # exit — disconnect (WebSocketDisconnect), uvicorn task
                # cancellation (CancelledError), or stream end. Without it the
                # dead _Subscription leaks in the broadcaster's strong-ref set and
                # every later publish wastefully fills its queue (mirror of the
                # session WS handler's ``async with self._event_bus.subscribe()``).
                async with broadcaster.subscribe() as subscription:
                    async for event in subscription:
                        await websocket.send_json(event.as_wire())
            else:
                # No broadcaster wired (misconfig / degraded): keep the socket open
                # so the client's polling fallback covers data; stream nothing.
                while True:
                    await asyncio.sleep(3600)
        except WebSocketDisconnect:
            close_code = 1000
            return
        except asyncio.CancelledError:
            if heartbeat_failed:
                return
            raise
        except Exception as exc:  # pragma: no cover
            logger.exception(
                'platform.ws stream_error connection_id=%s client=%s: %s',
                connection_id, client, exc,
            )
            close_code = 1011
            raise
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except (asyncio.CancelledError, Exception):
                    pass
            try:
                await websocket.close(code=close_code)
            except Exception:
                pass
            logger.info(
                'platform.ws disconnect connection_id=%s client=%s code=%s',
                connection_id, client, close_code,
            )
            if _ws_registry is not None:
                _ws_registry.dec_ws_connection(WS_STATE_OPEN)
                _ws_registry.inc_ws_connection(WS_STATE_CLOSING)
                _ws_registry.dec_ws_connection(WS_STATE_CLOSING)
                _reason = (
                    WS_CLOSE_REASON_TIMEOUT
                    if heartbeat_failed
                    else
                    WS_CLOSE_REASON_ERROR
                    if close_code == 1011
                    else WS_CLOSE_REASON_NORMAL
                )
                _ws_registry.inc_ws_closed_total(_reason)
            try:
                _trace_ctx.__exit__(None, None, None)
            except Exception:
                pass
            try:
                _conn_ctx.__exit__(None, None, None)
            except Exception:
                pass

    return router


def _code_text(code: object) -> str:
    """The wire value of an error code, not its Python repr.

    ⚠️ An operator greps this line against the contract, and the contract
    publishes ``UPSTREAM_UNAVAILABLE`` — not ``ErrorCode.UPSTREAM_UNAVAILABLE``.
    """
    return str(getattr(code, 'value', code))


def _safe_content_disposition_filename(value: object) -> str:
    """Return an ASCII filename safe for a quoted Content-Disposition value."""
    text = str(value or 'sample-inventory.xlsx')
    safe = ''.join(
        char if (char.isascii() and (char.isalnum() or char in '._-')) else '_'
        for char in text
    ).strip('._')
    return safe or 'sample-inventory.xlsx'


#: How far to walk ``__cause__`` when reporting a 5xx. One level, because the
#: point is to name the *original* failure, not to print a stack.
_CAUSE_DEPTH = 3


def _cause_chain(exc: BaseException) -> str:
    """``TypeName: message`` for the exception and its chained causes.

    ⚠️ **The wrapper alone cannot tell apart the two cases this line exists to
    tell apart** (adversarial review 3R). Every write failure surfaces as the
    same ``LocalUserStoreError('central users write failed')``, so a missing
    migration and a dead database printed byte-identical notices — and the
    distinguishing ``CheckViolation`` lived in ``__cause__``, which reaches only
    the file sink that is not mounted. Measured on a deliberately pre-027
    database: the operator saw exactly what a dead DB would have shown.
    """
    parts = []
    seen: set = set()
    current: object = exc
    while isinstance(current, BaseException) and len(parts) < _CAUSE_DEPTH:
        if id(current) in seen:
            break
        seen.add(id(current))
        try:
            rendered = f'{type(current).__name__}: {current}'
        except Exception:  # noqa: BLE001 — a hostile __str__ must not hide the 5xx
            rendered = type(current).__name__
        parts.append(rendered)
        current = current.__cause__
    return ' <- '.join(parts)


def create_platform_app(
    adapter: PlatformApiAdapter,
    *,
    title: str = PLATFORM_API_TITLE,
    version: str = PLATFORM_API_CONTRACT_VERSION,
    principal_resolver=None,
    lifespan=None,
    ws_heartbeat_seconds: float = 20.0,
    rate_limit_policy=None,
    rate_limit_subject_header=None,
    credential_secret=None,
):
    """Create a FastAPI app shell using the modern ``lifespan`` constructor.

    Deprecated ``@app.on_event`` / ``add_event_handler`` APIs are forbidden by
    ``TestNoDeprecatedFastApiInWebSurface`` — pass a ``@asynccontextmanager`` to
    ``lifespan=`` for startup/shutdown work.

    ``credential_secret`` keys the account-axis digests (2026-08-22). The
    composition root passes the local JWT signing secret in ``local_jwt`` mode; in
    every other mode there is no local password login to throttle, so ``None`` is
    correct rather than degraded.
    """
    try:
        from fastapi import FastAPI, HTTPException
        # Resolved here, at the composition edge, and handed to the two
        # shared installers below. Those modules are owned by the
        # dependency-free contracts lane and import no web framework, so the
        # framework has to enter through the factory that already requires it
        # — inside this same guarded try, so a runtime without FastAPI still
        # gets the RuntimeError below rather than an ImportError later.
        from fastapi.responses import JSONResponse
    except ImportError as exc:
        raise RuntimeError(
            'FastAPI is required to create the platform API app. '
            'Install fastapi and uvicorn in the web runtime.'
        ) from exc

    app = FastAPI(title=title, version=version, lifespan=lifespan)

    # RFC 9457 (B1): render problem-shaped HTTPException detail as a top-level
    # ``application/problem+json`` body (route boundary raises ProblemDetails).
    from fcc_test_contracts.web.problem_response import (
        install_problem_details_handler,
    )
    install_problem_details_handler(
        app, http_exception=HTTPException, json_response=JSONResponse,
    )

    # Inbound rate limiting (2026-07-19). Registered FIRST = innermost, so a
    # 429 still flows back out through the correlation + metrics middlewares
    # (throttling is observable, and the denial carries X-Request-Id). Budget
    # and keying live in ``domain/services/rate_limit_policy``; ``None`` policy
    # — the default — installs nothing, keeping the legacy stack byte-identical.
    from fcc_test_contracts.web.rate_limit_middleware import (
        install_rate_limit_middleware,
        rate_limiting_is_active,
    )
    # ⚠️ **TWO limiters, and the separation is the security property.**
    #
    # A first draft shared one limiter between the middleware's peer/identity tiers
    # and the credential account tier, reasoning "one memory bound, one eviction
    # story". Adversarial review showed that sharing *is* the vulnerability, and
    # demonstrated it end to end: ``FixedWindowRateLimiter`` caps at
    # ``DEFAULT_MAX_TRACKED_KEYS`` and evicts least-recently-seen, the middleware
    # mints one identity key per presented ``Authorization`` header, and a key is
    # inserted *before* the request is denied. So an unauthenticated attacker
    # rotating junk bearer tokens churns past the cap in well under a second and
    # **evicts a victim's account bucket, resetting their spent budget to full.**
    #
    # ``rate_limit.py`` says eviction "can only ever forgive a caller … so the
    # failure mode is safe". That is true of a *fairness* limiter and false of a
    # *security* budget: forgiveness is precisely the attack. A fairness limiter
    # and a credential budget must not share an eviction pool.
    #
    # With its own pool the only keys that can evict an account bucket are other
    # account buckets, and minting those costs a real login attempt bounded by the
    # peer tier. The residual — an attacker willing to spend thousands of attempts
    # over minutes — is named in the ledger rather than claimed away.
    from fcc_test_contracts.common.credential_throttle import CredentialThrottle
    from fcc_test_contracts.common.rate_limit import FixedWindowRateLimiter
    # peer 축 관측 (2026-08-23) — ``FORWARDED_ALLOW_IPS`` 는 서버가 실 주소를 복원할
    # *준비*가 됐는지만 말한다. 서로 다른 주소가 실제로 도착하는지는 관측해야 알고,
    # 그 둘의 불일치가 곧 «릴레이가 전원을 한 버킷으로 접었다» 는 신호다.
    from fcc_test_contracts.common.peer_axis_observer import (
        install_peer_axis_observation,
    )
    install_rate_limit_middleware(
        app,
        policy=rate_limit_policy,
        json_response=JSONResponse,
        subject_header=rate_limit_subject_header,
        peer_axis_recorder=install_peer_axis_observation(
            adapter.metrics_registry,
            active=rate_limiting_is_active(rate_limit_policy),
        ),
    )
    credential_throttle = CredentialThrottle(
        policy=rate_limit_policy,
        limiter=FixedWindowRateLimiter(),
        secret=credential_secret,
    )

    # OBS-2 phase 3 parity (FE-P0d S5, 2026-05-27): metrics middleware factory
    # SSOT — same ``create_metrics_middleware`` as Session/Headless. registry
    # None → no-op fast path. ``/platform/metrics`` is not in PLATFORM_API_ROUTES
    # → lookup None → self-referential observation skip (drown-out 방지).
    from fcc_test_contracts.common.metrics_middleware import create_metrics_middleware
    app.middleware('http')(
        create_metrics_middleware(
            registry=adapter.metrics_registry,
            lookup_operation=_lookup_platform_operation,
        )
    )

    # W3C TraceContext + X-Request-Id correlation middleware (parity with the
    # Session/Headless surfaces; shared SSOT in application.common). Keeps
    # distributed tracing continuous across the central read path.
    from fcc_test_contracts.common.correlation import bind_request_id, bind_trace_context
    from fcc_test_contracts.common.inbound_http import (
        apply_correlation_response_headers,
        extract_incoming_correlation,
    )

    @app.middleware('http')
    async def _request_id_middleware(request, call_next):
        correlation = extract_incoming_correlation(request.headers)
        request.state.request_id = correlation.request_id
        request.state.trace_id = correlation.trace_id
        request.state.span_id = correlation.span_id
        request.state.tracestate = correlation.tracestate
        with bind_request_id(correlation.request_id):
            with bind_trace_context(
                correlation.trace_id,
                correlation.span_id,
                sampled=correlation.sampled,
                tracestate=correlation.tracestate,
            ):
                response = await call_next(request)
        apply_correlation_response_headers(response.headers, correlation)
        return response

    app.include_router(create_platform_router(
        adapter,
        principal_resolver=principal_resolver,
        ws_heartbeat_seconds=ws_heartbeat_seconds,
        credential_throttle=credential_throttle,
    ))
    return app


def _bearer_token_of(request) -> str:
    """``Authorization: Bearer <token>`` off a request, or ``''``."""
    headers = getattr(request, 'headers', {}) if request is not None else {}
    getter = getattr(headers, 'get', None)
    value = str(getter('authorization', '') if callable(getter) else '').strip()
    if not value.lower().startswith('bearer '):
        return ''
    return value[7:].strip()


def _source_fingerprint(request) -> str:
    """Opaque per-origin string for spraying detection ONLY.

    ⚠️ It is HMAC-digested immediately by ``LoginSprayingDetector`` and is never
    stored, logged, or returned. The client IP is truncated to its /24, so a single
    tester's exact address is not what accumulates — what accumulates is "some
    source kept failing against many different accounts", which is the only
    question the detector asks.

    ⚠️ **``User-Agent`` used to be part of this string and no longer is**
    (2026-08-22 axis correction). It is attacker-chosen, so including it let one
    attacker split themselves across as many buckets as they liked: measured
    20 000 user-agents produced 20 000 buckets holding one account each and
    **zero** alerts. The composition rule now has one owner —
    :func:`domain.services.login_throttle_policy.spraying_source_key` — which
    documents why an observation key may contain nothing the observed party
    controls.
    """
    if request is None:
        return ''
    client = getattr(request, 'client', None)
    return spraying_source_key(getattr(client, 'host', ''))


def _normalize_request_body_args(request, body):
    """Tolerate a direct call passing the JSON body as the first positional arg.

    FastAPI binds ``body: Optional[dict]`` from the request JSON and ``request``
    from the dependency; a unit test calling the route function directly may pass
    a dict where ``request`` is expected. Mirror of the headless adapter helper.
    """
    if body is None and isinstance(request, dict):
        return None, request
    return request, body
