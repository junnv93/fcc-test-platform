"""중앙 플랫폼 계약 — 표면 횡단 어휘.

도메인 enum 에서 **파생**된 값 공간, 계약/봉투 버전 상수, 그리고 권한 토큰 설명표.
표면 모듈이 아니라 여기 있는 이유는 하나다 — 둘 이상의 표면이 읽는다.
"""
from __future__ import annotations

from application.central_contract.central_sync_readiness import CentralSyncReadinessCode
from fcc_test_contracts.common.access_policy import (
    API_PERMISSION_AUTHENTICATED,
    API_PERMISSION_PUBLIC,
)
from fcc_test_contracts.common.internal_rbac_contract import INTERNAL_RBAC_ROUTES
from domain.models.artifact_custody import CustodyStatus
from domain.models.chamber_node import UnavailableReason
from domain.models.reference_catalog import (
    CatalogFamily,
    RevisionProvenanceKind,
    RevisionState,
)
from domain.models.sample_inventory import INTAKE_FIELDS, SAMPLE_EDITABLE_FIELDS, SampleStatus
from domain.services.chamber_mode_policy import ChamberModeVerdict
from domain.services.project_metadata_edit import EDITABLE_PROJECT_META_FIELDS
from domain.services.reference_scope_policy import ReferenceScopeKind
from domain.services.test_equipment_list_policy import (
    ITEM_PERSISTED_FIELDS,
    ItemType,
    ListStatus,
    TEST_ITEM_KEYS,
)

# 멀티챔버 M2 — unavailable_reason 의 허용 vocabulary 는 도메인 enum SSOT 에서
# 파생한다(스키마/테스트/프론트가 리터럴로 재선언하지 않는다 — plan §P0). enum 정의
# 순서를 그대로 써 OpenAPI 출력이 결정적이다.
_UNAVAILABLE_REASON_VALUES: list[str] = [reason.value for reason in UnavailableReason]


#: 챔버 모드 대조 어휘 — 도메인 SSOT 파생(계약이 토큰을 두 번째로 선언하지 않는다).
_CHAMBER_MODE_VERDICT_VALUES: list[str] = [verdict.value for verdict in ChamberModeVerdict]


# Wave 3 (2026-08-07) — the reference-catalog vocabularies are DERIVED from the
# domain enums, never re-spelled here. The central DDL declares the same three
# state tokens and the same two scope kinds as CHECK constraints; a parity test
# locks all three sites together. Declaration order is the enum order, so the
# OpenAPI output is deterministic.
_REVISION_STATE_VALUES: list[str] = [state.value for state in RevisionState]


_REVISION_PROVENANCE_KIND_VALUES: list[str] = [
    kind.value for kind in RevisionProvenanceKind
]


_REFERENCE_SCOPE_KIND_VALUES: list[str] = [kind.value for kind in ReferenceScopeKind]


_CATALOG_FAMILY_VALUES: list[str] = [family.value for family in CatalogFamily]


# 성적서 §6 장비목록 (2026-08-07) — 어휘도 항목 필드 집합도 도메인에서 파생한다.
# 중앙 DDL 이 같은 토큰을 CHECK 로 선언하고 parity 테스트가 세 곳을 함께 잠근다.
# 항목 property 를 여기 손으로 나열하면 §6 표의 열 집합 사본이 하나 더 생긴다.
_EQUIPMENT_ITEM_TYPE_VALUES: list[str] = [item_type.value for item_type in ItemType]


_EQUIPMENT_LIST_STATUS_VALUES: list[str] = [status.value for status in ListStatus]


#: 시험항목 어휘 — 성적서 한 편에 대응하는 닫힌 집합(DTS/BLE/BT/UNII). 도메인
#: ``TestItemKey`` 에서 파생하며 선언 순서(E6~E9)를 보존한다. 여기에 배열을 직접
#: 적으면 어휘가 도메인/스키마 두 곳으로 쪼개진다.
_EQUIPMENT_TEST_ITEM_VALUES: list[str] = list(TEST_ITEM_KEYS)


#: 항목 스키마의 nullable 문자열 property — 도메인 영속 필드에서 파생.
#: ``item_type`` 은 enum 이라 따로 선언하므로 제외한다.
_EQUIPMENT_ITEM_TEXT_FIELDS: tuple[str, ...] = tuple(
    field for field in ITEM_PERSISTED_FIELDS if field != 'item_type'
)


# W3 백엔드 — PATCH body 의 property 집합은 도메인 정책 SSOT 튜플에서 파생한다
# (필드 목록을 스키마에 다시 적으면 SSOT 가 둘이 되어 drift 한다). 선언 순서를
# 그대로 써 OpenAPI 출력이 결정적이다.
_UPDATE_PROJECT_PROPERTIES: dict = {
    field: {'type': 'string', 'nullable': True}
    for field in EDITABLE_PROJECT_META_FIELDS
}


_SAMPLE_STATUS_VALUES = [status.value for status in SampleStatus]


_SAMPLE_TEXT_PROPERTIES = {
    field: {'type': 'string', 'nullable': True}
    for field in SAMPLE_EDITABLE_FIELDS
}


_SAMPLE_INTAKE_PROPERTIES = {
    field: {'type': 'string', 'nullable': True}
    for field in INTAKE_FIELDS
}


#: Response header carrying the opaque keyset continuation token. The response
#: BODY stays a plain array (ProjectCoverageList / ActiveClaimList) so the
#: already-shipped FE-P2 dashboard (which reads the array) is unaffected;
#: pagination clients read this header (GitHub-style). RFC-6648 note: a custom
#: header is pragmatic here for an opaque cursor.
PLATFORM_NEXT_CURSOR_HEADER = 'X-Next-Cursor'


PLATFORM_API_CONTRACT_VERSION = '1.0.0'


PLATFORM_API_COMPATIBILITY_MAJOR = 1


# Surface title — single SSOT here (dependency-free contract) so the schema
# builder + the FastAPI app shell default both derive from one literal (no
# duplicated title/version string across api_schema / platform_routes).
PLATFORM_API_TITLE = 'FCC Platform Read API'


# Versioned node→central wire envelope. The central API contract is the only
# place that owns this value; launchers, HTTP adapters, and OpenAPI derive from
# it instead of repeating a route-specific literal.
CHAMBER_RESULT_INGESTION_SCHEMA_VERSION = 'fcc.platform.chamber-result-ingestion.v1'


# The receipt schema and the chamber HTTP adapter share the platform readiness
# enum's vocabulary through this existing contract module.  Keeping the
# derivation here avoids a second literal list and preserves the adapter's
# established import boundary.
CENTRAL_SYNC_READINESS_CODE_VALUES: tuple[str, ...] = CentralSyncReadinessCode.values()


CENTRAL_SYNC_READY_CODE = CentralSyncReadinessCode.READY.value


# plot-dual-custody ① — 보관 판정 보고 봉투 버전. 위와 같은 이유로 여기가 유일한 소유처다.
ARTIFACT_CUSTODY_REPORT_SCHEMA_VERSION = 'fcc.platform.artifact-custody-report.v1'


# 보관 상태 vocabulary 는 **도메인 enum 에서 파생**한다 — 스키마·DB CHECK·프론트가
# 리터럴로 재선언하면 네 곳이 되고, 갈라진 순간 화면과 게이트가 다른 답을 낸다.
# enum 정의 순서를 그대로 써 OpenAPI 출력이 결정적이다.
ARTIFACT_CUSTODY_STATUSES: list[str] = [status.value for status in CustodyStatus]


# findings 에는 ``VERIFIED`` 가 오지 않는다 — 조치 가능한 것만 나르기 때문이다.
# 이것도 **뺄셈으로 파생**한다: 목록을 따로 적으면 도메인에 다섯 번째 토큰이 생겨도
# 여기는 그대로여서 조용히 거절된다.
ARTIFACT_CUSTODY_FINDING_STATUSES: list[str] = [
    status for status in ARTIFACT_CUSTODY_STATUSES if status != CustodyStatus.VERIFIED.value
]


PLATFORM_API_PERMISSION_DESCRIPTIONS: dict[str, str] = {
    # 신원 축 EMS 정합 (2026-08-21) — the platform surface gained its first
    # 'public' operations (local login / refresh). Like AUTHENTICATED this is an
    # authorization CLASS, not a grantable token: it is excluded from
    # rbac_role_grants and permissions.ts by ``_NON_GRANTABLE_SENTINELS``.
    API_PERMISSION_PUBLIC: (
        'No principal required. Used ONLY by the local-login operations, which by '
        'definition run before an identity exists (POST /platform/auth/login) or '
        'carry their own credential in the body (POST /platform/auth/refresh). '
        'NOT a grantable permission: never listed in rbac_role_grants.'
    ),
    # ADR-0017 D3 — authorization CLASS, NOT a grantable token. "Any resolved,
    # non-anonymous login passes; anonymous is denied." Gates project creation, a
    # GLOBAL (non-project-scoped) operation a brand-new project cannot gate by
    # membership. Like 'public' it is excluded from rbac_role_grants /
    # permissions.ts (never minted as a frontend permission).
    API_PERMISSION_AUTHENTICATED: (
        'Any authenticated (resolved, non-anonymous) principal — gates project '
        'creation (POST /platform/projects), a global operation. NOT a grantable '
        'permission: never listed in rbac_role_grants. The creator is auto-granted '
        'project_admin membership on the new project.'
    ),
    'platform:read': (
        'Read project-wide coverage + active claims + memberships from the central '
        'read model (coverage_by_condition_hash / active_claims / project_member_permissions).'
    ),
    'platform:claim': (
        'Acquire / release measurement claims on the central claim_events ledger '
        '(FE-P3-write — enforces cross-engineer duplicate prevention). Also gates '
        'starting a remote chamber measurement (멀티챔버 P5, POST '
        '/platform/chambers/{chamber_id}/measurements) — the engineer-tier remote '
        'action, distinct from the read-only availability/progress views.'
    ),
    'platform:admin': (
        'Assign / revoke project_membership roles (FE-P8). Every membership '
        'change is audited via audit_events (membership.assigned / membership.revoked). '
        'Also gates chamber registration (POST /platform/chambers).'
    ),
    'platform:chamber': (
        'Push a chamber node heartbeat to the central chamber_heartbeat_events '
        'ledger (멀티챔버 P2). Node-scoped machine token — a chamber PC self-reports '
        'idle/in_use; it cannot read coverage/claims or register/manage chambers. '
        'It may also submit its own result outbox through the result-ingestion '
        'boundary; it cannot submit for another chamber. NOT a project-membership '
        'grant (never listed in rbac_role_grants).'
    ),
    'platform:sample-write': (
        'Create and edit every web sample field, including the latest intake '
        'observation. Granted equally to project_pm, project_engineer, '
        'project_admin, and system_admin; every mutation writes an immutable revision.'
    ),
    'platform:sample-hard-delete': (
        'Physically delete one operational sample, its intakes, and revisions. '
        'Global system_admin only; the operation writes a PII-free sample.hard_deleted tombstone.'
    ),
    'platform:reference-write': (
        'Author a reference-catalog candidate revision and publish it '
        '(POST …/reference-revisions and …/publish). Publishing changes what '
        'every chamber in that scope measures with from its NEXT session, so it '
        'is a deliberate, audited act — but it is the TESTER who re-measures a '
        'cable loss after a re-cabling and therefore the tester who must be able '
        'to record it. Granted to project_engineer (=시험원) + project_admin. '
        'Deliberately NOT platform:claim: that token belongs to the measurement '
        'claim ledger and every engineer already holds it, so reusing it would '
        'grant reference publication to everyone with no decision recorded.'
    ),
    'platform:chamber-config-write': (
        "Set a chamber's configuration — the instrument connection settings "
        '(analyzer / BT tester / switchbox GPIB and LAN addresses, PATCH '
        '/platform/chambers/{chamber_id}/equipment-config) AND where that '
        "chamber's plots are stored (PATCH "
        '/platform/chambers/{chamber_id}/storage-root). ONE token because the '
        'actor and the scope are the same for both — a pair that is always '
        'granted together is just one token with an extra drift surface, which '
        'is the reasoning 016 used to refuse splitting authoring from '
        'publishing. Granted to '
        'project_engineer (=시험원) + project_admin: the person who knows the '
        "analyzer's new address after a re-cabling is the tester standing in the "
        'room, not an administrator. Deliberately NOT platform:reference-write: '
        "that token's membership half only opens for PROJECT-scoped families, "
        'and a chamber is not a project — a room outlives every project and one '
        'project spans two rooms. Deliberately NOT platform:admin: that was the '
        'tier the storage-root axis used until 2026-08-11, and holding to it '
        'would mean the tester cannot record the address they just read off '
        'the instrument — operator decision (§6) put every test-related right '
        'with the tester, and the storage root moved onto this token with it. '
        'The chamber-scoped route carries no project_id, so this grant is '
        'realized through the identity-provider group attribute rather than '
        'through project membership — the same shape platform:reference-write '
        'already has for room-scoped families.'
    ),
}


# Internal authenticated service boundary used by the provider headless
# process. It is intentionally not part of ``PLATFORM_API_ROUTES``: the
# browser/OpenAPI contract must not grow a permission-dump surface, while the
# existing platform composition still owns central RBAC, expiry, and enabled
# user checks. The gateway routes this path to platform-api on app-network.
PLATFORM_INTERNAL_RBAC_ROUTES = INTERNAL_RBAC_ROUTES
