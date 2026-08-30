"""중앙 플랫폼 계약 — 참조 카탈로그 (provider 측 저작/게시 + 챔버 bundle 수신).

``api_contracts`` facade 가 이 모듈의 표를 병합해 ``PLATFORM_API_*`` 로 재노출한다.
모듈 경계는 **표 종류가 아니라 operation 표면**이다 — git 이력 실측에서 계약 변경
커밋의 92%(52 중 48)가 정확히 한 표면만 만졌고, 표 종류로 자르면 엔드포인트 하나
추가가 항상 다섯 표를 동시에 만진다(네 표가 같은 operationId 키로 병렬 배열돼 있다).
"""
from __future__ import annotations

from application.central_contract.api_operation_factory import _operation
from application.central_contract.api_vocabulary import (
    PLATFORM_NEXT_CURSOR_HEADER,
    _CATALOG_FAMILY_VALUES,
    _REFERENCE_SCOPE_KIND_VALUES,
    _REVISION_PROVENANCE_KIND_VALUES,
    _REVISION_STATE_VALUES,
)
from domain.services.reference_entry_edit_policy import MAX_ENTRY_EDITS_PER_REQUEST

#: 이 모듈이 소유하는 route path prefix. 분할은 **선언이 아니라 판정 대상**이다 —
#: ``tests/test_central_contract_decomposition_axis.py`` 가 prefix 집합이 쌍마다
#: 서로소이고 ``PLATFORM_API_ROUTES`` 의 모든 경로를 덮는지, 그리고 각 operation 이
#: 자기 경로의 **최장 일치** prefix 를 소유한 모듈에 물리적으로 선언돼 있는지 파생
#: 판정한다. 새 엔드포인트가 어느 모듈로 가야 하는지 사람이 기억하지 않는다.
SURFACE_PREFIXES: tuple[str, ...] = (
    '/platform/providers/{provider_id}/reference-revisions',
    '/platform/providers/{provider_id}/reference-families',
    '/platform/chambers/{chamber_id}/reference-bundle',
)


# 이 표면의 operation 만 참조하는 에러 응답 조각. 둘 이상의 표면이 참조하게 되면
# ``api_operation_factory`` 로 올라가야 하고, 그 판정도 파생 검사가 한다.
# Wave 3 — reference catalog error responses (SSOT).
_REFERENCE_SCOPE_INVALID_400 = (
    'The scope could not be resolved for that family — a room-scoped family was '
    'asked for without a room, or the scope_kind contradicts the family axis. '
    'Refused rather than defaulted: a silently defaulted bucket applies another '
    "room's measurement path and the wrong number still looks plausible."
)

_REFERENCE_PROVIDER_NOT_FOUND_404 = (
    'Unknown provider_id — no central providers row owns this reference family. '
    'Providers are operator-registered reference data, not ingested. '
    'The code distinguishes the two remedies: REFERENCE_PROVIDER_NOT_REGISTERED '
    'when this deployment offers the provider and an operator must register the '
    'row, NOT_FOUND when the id is unknown to both registries and the caller '
    'should correct it.'
)

# Both provider outcomes AND the revision outcome share one status on the
# operations addressed by (provider, revision). One description per status is all
# OpenAPI offers, so it names all three causes; the machine distinction travels in
# ``code``, which is the member that exists for exactly this.
_CHAMBER_OR_PROVIDER_NOT_FOUND_404 = (
    'Unknown chamber_id — not registered in the central chamber_nodes registry '
    '— or the provider this deployment delivers for has no central providers '
    'row. See ``code``: REFERENCE_PROVIDER_NOT_REGISTERED is the second case '
    'and is fixed by an operator registering the provider, not by touching the '
    'chamber registry.'
)

_REFERENCE_REVISION_OR_PROVIDER_NOT_FOUND_404 = (
    'Unknown revision_id — no central reference_revisions row, or the revision '
    'belongs to another provider (answered identically so the surface is not an '
    'oracle for real revision ids) — or the provider itself is unknown. See '
    '``code``: REFERENCE_PROVIDER_NOT_REGISTERED means an operator must register '
    'the provider; NOT_FOUND means the id is unknown to both registries.'
)

_REFERENCE_PUBLISH_CONFLICT_409 = (
    'Another revision is already PUBLISHED for this (provider, family, profile, '
    'scope), or the revision is not in a publishable state. Publish uniqueness is '
    'enforced by a partial unique index in the central DDL, so the conflict is '
    'refused at the origin rather than detected later on a chamber replica.'
)

_REFERENCE_STATE_CONFLICT_409 = (
    'The revision is not in a state that permits this operation — only a '
    'PUBLISHED revision may be forked, and only a CANDIDATE may be edited. '
    'Distinct from the publish-slot conflict: both are 409, but one is answered '
    'by reloading and the other by forking, and collapsing them would leave the '
    'caller parsing a sentence written for a person to tell which.'
)

_REFERENCE_EDIT_INVALID_400 = (
    'The proposed edit is not an edit of an existing row: it named a '
    'reference_id this revision does not have, carried a payload whose key set '
    'is not the family runtime row, or tried to move an identity field. '
    'Refused rather than partially applied — a skipped edit reports success '
    "while the tester's value never changes, and a payload of the wrong shape "
    'projects a malformed row into the table the measurement path reads.'
)

_REFERENCE_AUTHORED_INVALID_400 = (
    'Authored revision rejected — the family/scope axis disagrees, a row payload '
    'is not the runtime row shape for that family, two rows share an identity, '
    'or the revision has no rows at all.'
)

_REFERENCE_ROW_EDIT_INVALID_400 = (
    'Row edit rejected — no additions or removals were supplied, a removal names '
    'a row this revision does not have, an added row duplicates a surviving '
    "row's identity, a payload is not the runtime row shape for that family, or "
    'the change would remove every row.'
)

_REFERENCE_EDIT_STALE_409 = (
    'The revision changed since it was loaded (expected_etag no longer '
    'matches), so applying this edit would discard the other write. Reload and '
    're-apply. The check lives in the UPDATE WHERE clause, not in a preceding '
    'read, so there is no window in which two edits can both believe they won.'
)


ROUTES: dict[str, tuple[str, str]] = {
    # Wave 3 (2026-08-07) — reference catalog. Nested under {provider_id}, NOT
    # under {project_id}: a revision's bucket is (provider, family, profile,
    # scope) where scope is a ROOM for the cabling families, so nesting it under
    # a project would imply cable loss belongs to a project — the exact mistake
    # reference_scope_policy exists to prevent (a project spanning two rooms needs
    # two correction sets). Provider IS part of the identity, because 'correction'
    # means different things to unlicensed / mmWave / licensed headless, so it
    # belongs in the path rather than being inferred from server config.
    # Publish is an action sub-resource, so the state transition has its own route
    # rather than a mutable ``state`` field on the revision.
    'list_reference_revisions': (
        'GET', '/platform/providers/{provider_id}/reference-revisions',
    ),
    'get_reference_revision': (
        'GET',
        '/platform/providers/{provider_id}/reference-revisions/{revision_id}',
    ),
    'create_reference_revision': (
        'POST', '/platform/providers/{provider_id}/reference-revisions',
    ),
    'fork_reference_revision': (
        'POST',
        '/platform/providers/{provider_id}/reference-revisions/{revision_id}/fork',
    ),
    # Wave B (2026-08-11). ``/authored`` 는 워크북 임포터와 **다른 문**이다 —
    # 어느 operation 이 돌았는지가 곧 provenance 이므로 같은 경로에 플래그를 더하면
    # 감사 사실이 요청 모양이 정하는 값이 된다.
    # provider 아래다. 리비전이 provider 스코프인 사유가 *'correction' 이 unlicensed /
    # mmWave / licensed 에서 다른 것을 뜻한다* 인데, 그렇다면 그 correction 이 **어떤
    # 칸을 갖는가**도 provider 의 사실이다. 오늘 답이 provider 무관하게 같은 것은
    # 정책이 하나만 적재돼 있기 때문이지 주소가 주장할 일이 아니다.
    'list_reference_families': (
        'GET', '/platform/providers/{provider_id}/reference-families',
    ),
    'create_authored_reference_revision': (
        'POST', '/platform/providers/{provider_id}/reference-revisions/authored',
    ),
    # 행 추가·삭제는 값 편집(``/entries``)의 형제이되 다른 자원이다.
    'update_reference_revision_rows': (
        'POST',
        '/platform/providers/{provider_id}/reference-revisions/{revision_id}/rows',
    ),
    'update_reference_revision_entries': (
        'PUT',
        '/platform/providers/{provider_id}/reference-revisions/{revision_id}/entries',
    ),
    'publish_reference_revision': (
        'POST',
        '/platform/providers/{provider_id}/reference-revisions/{revision_id}/publish',
    ),
    # Node delivery lives under the chamber, so the machine token can be bound to
    # the path chamber_id exactly like heartbeat / result ingestion.
    'get_chamber_reference_bundle': (
        'GET', '/platform/chambers/{chamber_id}/reference-bundle',
    ),
}


PERMISSIONS: dict[str, str] = {
    # Wave 3 (2026-08-07) — reference catalog. The central platform is the
    # AUTHORITATIVE ORIGIN of measurement reference data; the chamber PC holds a
    # replica. Reading revisions is a project-member read → platform:read.
    #
    # 2026-08-08 — 저작/게시가 platform:admin 에서 platform:reference-write 로
    # 옮겨졌다. 옛 주석은 그것을 "성적서 발행·멤버십 쓰기와 같은 층위의 프로젝트
    # 관리 행위"로 읽었는데, 그 판단은 **누가 그 일을 하는가**를 잘못 짚었다:
    # 재배선 뒤 케이블 손실을 다시 재고 그 값을 올리는 사람은 관리자가 아니라
    # 시험원이다(운영자 판정 2026-08-08). platform:admin 을 유지하면 시험원은
    # 자기가 측정한 값을 스스로 올릴 수 없고, 워크북이 권위를 잃을 수 없다.
    #
    # 그러나 platform:claim 재사용은 기각했다 — 그 토큰은 측정 클레임 원장의
    # 것이고 project_engineer 가 이미 보유하므로, 재사용하면 "시험원 전원이 방
    # 케이블 손실을 게시할 수 있다"가 **아무 결정도 기록되지 않은 채** 참이 된다.
    # 장비목록 웨이브가 platform:claim 을 재사용하며 "새 토큰은 3자 bijection
    # 갱신을 강제하므로 별도 wave 의 일"이라고 적었고, 이 웨이브가 그 별도 wave 다.
    #
    # This token is intentionally independent from project administration: the
    # same PM/tester actor owns the whole sample record.
    # 4-eyes(저작자 ≠ 승인자) 축이 실제로 오면 그 집은 권한이 아니라 이미 존재하는
    # approved_by / approval_reason 컬럼과 ValidationIssueCode.APPROVAL_REQUIRED 다.
    'list_reference_revisions': 'platform:read',
    'get_reference_revision': 'platform:read',
    'create_reference_revision': 'platform:reference-write',
    # fork 와 편집은 저작이므로 게시와 **같은 행위자**다. 토큰을 쪼개면 항상 함께
    # 부여되는 한 쌍이 되어 드리프트 표면만 늘어난다(016 이 이미 같은 이유로
    # 하나로 뒀다). 신규 grantable 토큰 0 → RBAC bijection 무변경.
    'fork_reference_revision': 'platform:reference-write',
    'update_reference_revision_entries': 'platform:reference-write',
    # Wave B (2026-08-11) — 웹 저작(처음부터 만들기)과 행 추가·삭제. 둘 다
    # 같은 행위자(시험원)가 같은 표에 쓰는 일이라 **기존 토큰을 재사용**한다:
    # 행위자와 스코프가 같은 토큰을 쪼개면 drift 표면만 늘고(016 이 저작/게시
    # 분리를 같은 논거로 기각했다) 신규 grantable 토큰 0 이 유지된다.
    # 패밀리별 열 어휘 카탈로그 — 순수 도메인 정책의 투영이라 읽기 토큰이다.
    'list_reference_families': 'platform:read',
    'create_authored_reference_revision': 'platform:reference-write',
    'update_reference_revision_rows': 'platform:reference-write',
    'publish_reference_revision': 'platform:reference-write',
    # Delivery to a chamber node is node-scoped exactly like heartbeat and result
    # ingestion: the machine token is bound to the path chamber_id. Deliberately
    # NOT platform:read — giving a chamber PC platform:read would also hand it
    # coverage / claims / memberships, falsifying the documented least-privilege
    # property that a chamber token can only self-report and fetch its own
    # measurement path. A node-only route costs one operation and keeps that true.
    'get_chamber_reference_bundle': 'platform:chamber',
}


OPERATION_QUERY: dict[str, tuple[str, ...]] = {
    # Wave 3 — revision listing narrows by identity facets and pages with the
    # same keyset SSOT as coverage / claims / memberships.
    'list_reference_revisions': (
        'family', 'scope_kind', 'scope_id', 'state', 'limit', 'cursor',
    ),
    # The delivery bundle pages over ONE cursor axis spanning revisions and their
    # entries, so a coupled family group can never arrive in halves.
    'get_chamber_reference_bundle': (
        'scope_project_id', 'bundle_etag', 'limit', 'cursor',
    ),
}


RESPONSE_HEADERS: dict[str, dict] = {
    'list_reference_revisions': {
        PLATFORM_NEXT_CURSOR_HEADER: {
            'description': (
                'Opaque keyset cursor for the next page. Absent on the last page '
                'or an unbounded (no-limit) read. Pass it back as ?cursor= to continue.'
            ),
            'schema': {'type': 'string'},
        },
    },
    'get_chamber_reference_bundle': {
        PLATFORM_NEXT_CURSOR_HEADER: {
            'description': (
                'Opaque keyset cursor for the next bundle page. Absent on the last '
                'page. Every page of one bundle carries the SAME bundle_etag; if it '
                'changes mid-walk the node must discard the partial bundle and '
                'restart, so a coupled family group is never applied in halves.'
            ),
            'schema': {'type': 'string'},
        },
    },
}


SCHEMAS: dict[str, dict] = {
    # Wave 3 (2026-08-07) — reference catalog.
    #
    # ``payload`` is declared as an EXPLICIT open mapping
    # (``additionalProperties: True``), never as a bare ``{'type': 'object'}``.
    # A bare object renders as ``Record<string, never>`` in the generated TS —
    # a type nothing satisfies — which is the defect class the free-form-object
    # SSOT exists to prevent. The payload IS a runtime lookup row whose field set
    # belongs to the provider's PROJECTION_FIELD_CONTRACT; the platform stores and
    # returns it without interpreting it.
    'ReferenceEntryRecord': {
        'type': 'object',
        'required': [
            'reference_id', 'identity_key', 'entry_order', 'payload', 'content_sha256',
        ],
        'properties': {
            'reference_id': {'type': 'string', 'minLength': 1},
            'identity_key': {'type': 'string', 'minLength': 1},
            'entry_order': {'type': 'integer', 'minimum': 0},
            'payload': {'type': 'object', 'additionalProperties': True},
            'test_condition_ids': {'type': 'array', 'items': {'type': 'string'}},
            'effective_from': {'type': 'string', 'nullable': True},
            'effective_to': {'type': 'string', 'nullable': True},
            'source_sheet_name': {'type': 'string', 'nullable': True},
            'source_row_number': {'type': 'integer', 'nullable': True},
            'content_sha256': {'type': 'string', 'minLength': 1},
        },
        'additionalProperties': False,
    },
    'ReferenceRevisionSummary': {
        'type': 'object',
        'required': [
            'revision_id', 'provider_id', 'family', 'profile_id', 'scope_kind',
            'scope_id', 'revision_number', 'state', 'version', 'etag',
            'content_sha256', 'source_snapshot_id', 'source_manifest_sha256',
            'provenance_kind',
            'created_by', 'created_at', 'updated_by', 'updated_at', 'entry_count',
        ],
        'properties': {
            'revision_id': {'type': 'string', 'format': 'uuid'},
            'provider_id': {'type': 'string'},
            'family': {'type': 'string', 'enum': _CATALOG_FAMILY_VALUES},
            'profile_id': {'type': 'string'},
            'scope_kind': {'type': 'string', 'enum': _REFERENCE_SCOPE_KIND_VALUES},
            'scope_id': {'type': 'string'},
            # minimum 1, not 0: the chamber replica's schema CHECKs
            # revision_number > 0 and version > 0, so a central row the replica
            # could not store would be a delivery that fails on every arrival.
            # The bound belongs on the origin's contract, where it is refusable.
            'revision_number': {'type': 'integer', 'minimum': 1},
            'state': {'type': 'string', 'enum': _REVISION_STATE_VALUES},
            'version': {'type': 'integer', 'minimum': 1},
            'etag': {'type': 'string'},
            'content_sha256': {'type': 'string'},
            # Required, not nullable: the replica's schema declares both NOT NULL,
            # and every revision has a provenance whether it was imported or forked.
            'source_snapshot_id': {'type': 'string'},
            'source_manifest_sha256': {'type': 'string'},
            'official_manifest_sha256': {'type': 'string', 'nullable': True},
            'forked_from_revision_id': {'type': 'string', 'nullable': True},
            # Required and non-nullable: every revision has a provenance, and a
            # nullable third state would make readers handle an "unknown" that
            # never occurs. It appears HERE (a response) and in no request —
            # which operation ran IS the value, so a client-supplied one would
            # be a forgeable claim about audit evidence.
            'provenance_kind': {
                'type': 'string', 'enum': _REVISION_PROVENANCE_KIND_VALUES,
            },
            'entry_count': {'type': 'integer', 'minimum': 0},
            'created_by': {'type': 'string'},
            'created_at': {'type': 'string'},
            'updated_by': {'type': 'string'},
            'updated_at': {'type': 'string'},
            # The full lifecycle triple set travels because the replica's schema
            # has all of these columns; a bundle that omitted any of them could
            # not reconstruct the revision faithfully, and a replica that had to
            # invent a value would no longer be a replica.
            'approved_by': {'type': 'string', 'nullable': True},
            'approved_at': {'type': 'string', 'nullable': True},
            'approval_reason': {'type': 'string', 'nullable': True},
            'published_by': {'type': 'string', 'nullable': True},
            'published_at': {'type': 'string', 'nullable': True},
            'publish_reason': {'type': 'string', 'nullable': True},
            'retired_by': {'type': 'string', 'nullable': True},
            'retired_at': {'type': 'string', 'nullable': True},
            'retirement_reason': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
    'ReferenceRevisionList': {
        'type': 'array',
        'items': {'$ref': '#/schemas/ReferenceRevisionSummary'},
    },
    'ReferenceRevisionEnvelope': {
        'type': 'object',
        'required': ['revision', 'entries'],
        'properties': {
            'revision': {'$ref': '#/schemas/ReferenceRevisionSummary'},
            'entries': {
                'type': 'array',
                'items': {'$ref': '#/schemas/ReferenceEntryRecord'},
            },
        },
        'additionalProperties': False,
    },
    'CreateReferenceRevisionRequest': {
        'type': 'object',
        'required': [
            'family', 'profile_id', 'scope_kind', 'scope_id',
            'source_snapshot_id', 'source_manifest_sha256', 'entries',
        ],
        'properties': {
            'family': {'type': 'string', 'enum': _CATALOG_FAMILY_VALUES},
            'profile_id': {'type': 'string', 'minLength': 1},
            'scope_kind': {'type': 'string', 'enum': _REFERENCE_SCOPE_KIND_VALUES},
            'scope_id': {'type': 'string', 'minLength': 1},
            'source_snapshot_id': {'type': 'string', 'minLength': 1},
            'source_manifest_sha256': {'type': 'string', 'minLength': 1},
            # Provenance of a fork (fork_published), so "where did this project's
            # starting values come from" is answerable without a version tree.
            'forked_from_revision_id': {'type': 'string', 'nullable': True},
            'entries': {
                'type': 'array',
                'items': {'$ref': '#/schemas/ReferenceEntryRecord'},
            },
        },
        'additionalProperties': False,
    },
    # 웹 저작(처음부터 만들기). 워크북 임포터의 요청과 **닮았으되 다른 것**이다:
    # `source_snapshot_id`/`source_manifest_sha256` 이 **없고**(워크북 스냅샷의
    # 사실이라 웹이 지어낼 수 없다), 엔트리가 `payload` 만 나른다 — `reference_id`
    # `identity_key` `content_sha256` 은 전부 payload 의 파생값이라 서버가 민팅한다.
    # 클라이언트가 정체성을 보내면 저장된 `identity_key` 가 그 행을 설명하지 않는
    # 상태를 만들 수 있고, 그 어긋남은 투영이 측정 경로의 테이블을 채울 때 드러난다.
    'AuthoredReferenceEntry': {
        'type': 'object',
        'required': ['payload'],
        'properties': {
            'payload': {'type': 'object', 'additionalProperties': True},
        },
        'additionalProperties': False,
    },
    'CreateAuthoredReferenceRevisionRequest': {
        'type': 'object',
        'required': ['family', 'profile_id', 'scope_kind', 'scope_id', 'entries'],
        'properties': {
            'family': {'type': 'string', 'enum': _CATALOG_FAMILY_VALUES},
            'profile_id': {'type': 'string', 'minLength': 1},
            'scope_kind': {'type': 'string', 'enum': _REFERENCE_SCOPE_KIND_VALUES},
            'scope_id': {'type': 'string', 'minLength': 1},
            'entries': {
                'type': 'array',
                'minItems': 1,
                'maxItems': MAX_ENTRY_EDITS_PER_REQUEST,
                'items': {'$ref': '#/schemas/AuthoredReferenceEntry'},
            },
        },
        'additionalProperties': False,
    },
    # 행 추가·삭제. 값 편집과 **다른 요청**인 이유는 값 편집 정책이 식별 필드 이동을
    # 거부하는 사유 그 자체다("추가+삭제이지 편집이 아니다"). 삭제는 `reference_id`
    # 로 지목하고(UNIQUE 인덱스를 가진 유일한 키) 추가는 payload 만 나른다.
    'UpdateReferenceRevisionRowsRequest': {
        'type': 'object',
        'required': ['expected_etag'],
        'properties': {
            'expected_etag': {'type': 'string', 'minLength': 1},
            'additions': {
                'type': 'array',
                'maxItems': MAX_ENTRY_EDITS_PER_REQUEST,
                'items': {'$ref': '#/schemas/AuthoredReferenceEntry'},
            },
            'removals': {
                'type': 'array',
                'maxItems': MAX_ENTRY_EDITS_PER_REQUEST,
                'items': {'type': 'string', 'minLength': 1},
            },
        },
        'additionalProperties': False,
    },
    # 한 행의 값 변경 하나. 주소는 `reference_id` 이고 `identity_key` 가 아니다 —
    # UNIQUE 인덱스를 가진 것은 `(revision_id, reference_id)` 뿐이라, 쓰기 대상을
    # `identity_key` 로 지목하는 것은 두 답이 가능한 키를 고르는 것이다.
    # 처음부터 만들기 화면이 필요로 하는 유일한 것 — **어떤 칸을 채워야 하는가**.
    # 프론트가 6 패밀리 × N 컬럼을 다시 적으면 같은 순서가 두 언어로 쪼개지고, 그
    # 드리프트는 시험원이 만든 행이 투영에서 거부될 때에야 드러난다. 상세 응답이
    # `payload_columns`/`identity_columns` 를 주는 것과 **같은 규칙**이고, 다른 점은
    # 아직 리비전이 하나도 없는 패밀리에도 답할 수 있다는 것뿐이다.
    'ReferenceFamilyDescriptor': {
        'type': 'object',
        'required': [
            'family', 'scope_kind', 'payload_columns', 'identity_columns',
            'coupled_with', 'default_profile_id',
        ],
        'properties': {
            'family': {'type': 'string', 'enum': _CATALOG_FAMILY_VALUES},
            'scope_kind': {'type': 'string', 'enum': _REFERENCE_SCOPE_KIND_VALUES},
            # 프로필 축의 기본값도 서버가 준다 — 화면이 'default' 를 적으면 그
            # 상수가 두 언어에 존재하게 되고, 프로필이 여럿이 되는 날 갈라진다.
            'default_profile_id': {'type': 'string', 'minLength': 1},
            'payload_columns': {'type': 'array', 'items': {'type': 'string'}},
            'identity_columns': {'type': 'array', 'items': {'type': 'string'}},
            'coupled_with': {
                'type': 'string', 'enum': _CATALOG_FAMILY_VALUES, 'nullable': True,
            },
        },
        'additionalProperties': False,
    },
    'ReferenceFamilyList': {
        'type': 'array',
        'items': {'$ref': '#/schemas/ReferenceFamilyDescriptor'},
    },
    'ReferenceEntryEdit': {
        'type': 'object',
        'required': ['reference_id', 'payload'],
        'properties': {
            'reference_id': {'type': 'string', 'minLength': 1},
            'payload': {'type': 'object', 'additionalProperties': True},
        },
        'additionalProperties': False,
    },
    # 파생값(content_sha256 / etag / version / provenance_kind)은 **하나도 없다**.
    # 서버가 다시 세고, 클라이언트가 보내면 해시 규칙이 Python/TS 두 언어로
    # 갈라진다 — 그 드리프트는 게시된 뒤에야 드러난다. 여기 있는 `expected_etag`
    # 는 예외가 아니다: 클라이언트가 조립하는 값이 아니라 **서버가 준 값을 그대로
    # 되돌려 보내는** 동시성 토큰이다.
    'UpdateReferenceRevisionEntriesRequest': {
        'type': 'object',
        'required': ['expected_etag', 'edits'],
        'properties': {
            'expected_etag': {'type': 'string', 'minLength': 1},
            # `maxItems` 는 도메인 상수에서 파생한다 — 두 곳에 숫자를 적으면
            # 클라이언트가 통과시킨 요청을 서버가 거부하는 날이 온다. 이것은
            # **쓰기**를 제한하고 파싱 비용을 제한하지 않는다: 본문은 이 계약이
            # 발화하기 전에 이미 디코드됐고, 본문 크기 자체를 막는 것은 모든
            # 라우트가 공유하는 문제라 미들웨어/프록시의 일이다(장부 등재).
            'edits': {
                'type': 'array',
                'minItems': 1,
                'maxItems': MAX_ENTRY_EDITS_PER_REQUEST,
                'items': {'$ref': '#/schemas/ReferenceEntryEdit'},
            },
        },
        'additionalProperties': False,
    },
    # Publishing takes only a reason: the revision is already immutable content,
    # and the actor comes from the verified principal, never from the body.
    # 검토 화면이 실제로 그릴 수 있는 상세. 목록은 요약만 주므로 "보고 나서 게시"가
    # 목록만으로는 성립하지 않는다. payload_columns 는 패밀리별 런타임 행의 필드
    # 순서(provider 도메인 PROJECTION_FIELD_CONTRACT)를 그대로 싣는다 — 클라이언트가
    # payload 키에서 파생하면 null 필드가 생략된 엔트리마다 열 집합이 달라지고,
    # TS 에 6 패밀리 × N 컬럼을 다시 적으면 같은 순서가 두 언어로 쪼개진다.
    'ReferenceRevisionDetail': {
        'type': 'object',
        'required': [
            'revision', 'entries', 'payload_columns', 'identity_columns',
            'coupled_with',
        ],
        'properties': {
            'revision': {'$ref': '#/schemas/ReferenceRevisionSummary'},
            'entries': {
                'type': 'array',
                'items': {'$ref': '#/schemas/ReferenceEntryRecord'},
            },
            'payload_columns': {'type': 'array', 'items': {'type': 'string'}},
            # 편집 화면이 읽기 전용으로 렌더해야 하는 열 — 이 행을 *이 행이게*
            # 하는 필드들(provider 도메인 IDENTITY_FIELD_CONTRACT). payload_columns
            # 와 같은 이유로 서버가 준다: 프론트가 재선언하면 같은 규칙이 두 언어로
            # 갈라지고, 그 드리프트는 시험원이 식별 열을 고칠 수 있게 된 뒤에야
            # 드러난다.
            'identity_columns': {'type': 'array', 'items': {'type': 'string'}},
            # 결합 그룹의 나머지 반쪽 패밀리(없으면 null). 열 순서와 같은 이유로
            # 서버가 준다 — 결합 어휘는 도메인 SSOT 에 있고, 클라이언트가 짝을
            # 적거나 거부 메시지에서 파싱하면 그 어휘가 두 곳이 된다.
            'coupled_with': {
                'type': 'string', 'enum': _CATALOG_FAMILY_VALUES, 'nullable': True,
            },
        },
        'additionalProperties': False,
    },
    'PublishReferenceRevisionRequest': {
        'type': 'object',
        'properties': {
            'publish_reason': {'type': 'string', 'nullable': True},
            # 결합 그룹(correction ↔ switch_port_mapping)의 나머지 반쪽. 결합
            # 패밀리를 이것 없이 게시하면 typed 409 이고, 메시지가 형제 패밀리
            # 이름을 댄다. 비결합 패밀리에 주는 것도 거부한다 — 조용히 무시하면
            # 클라이언트가 결합을 요청했다고 믿는 채로 반쪽이 게시된다.
            'coupled_revision_id': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
    # One page of a delivery bundle. Revisions and their entries share ONE cursor
    # axis and every page repeats the same bundle_etag; a node that sees the tag
    # change mid-walk discards the partial bundle rather than applying half of a
    # coupled family group.
    'ChamberReferenceBundle': {
        'type': 'object',
        'required': ['chamber_id', 'bundle_etag', 'generated_at', 'unchanged', 'revisions'],
        'properties': {
            'chamber_id': {'type': 'string'},
            'bundle_etag': {'type': 'string'},
            'generated_at': {'type': 'string'},
            # The next page's keyset cursor, IN THE BODY rather than in a header.
            # A node has to read the tag and the cursor together to walk safely —
            # continue only while the tag holds — and splitting the two across
            # body and header is what lets a caller act on half of an answer.
            # Null on the last page and on an unbounded (no-limit) read.
            'next_cursor': {'type': 'string', 'nullable': True},
            # True when the caller's ?bundle_etag= already matches: revisions is
            # empty and nothing needs to be applied. Distinguishes "you are
            # current" from "this room has no published reference data", which an
            # empty array alone cannot express.
            'unchanged': {'type': 'boolean'},
            'revisions': {
                'type': 'array',
                'items': {'$ref': '#/schemas/ReferenceRevisionEnvelope'},
            },
        },
        'additionalProperties': False,
    },
}


OPERATIONS: dict[str, dict] = {
    # Wave 3 (2026-08-07) — reference catalog. Every operation names a DECLARED
    # response schema; leaving one undeclared would fall through to the inline
    # ``{'type': 'object'}`` fallback and grow the platform surface's
    # bare-fallback ratchet instead of paying it down.
    'list_reference_revisions': _operation(
        request=None,
        response='ReferenceRevisionList',
        permission=PERMISSIONS['list_reference_revisions'],
        error_responses={
            '400': _REFERENCE_SCOPE_INVALID_400,
            # 2026-08-25 — 이 목록은 미등록 provider 에 `200 []` 를 답했고 그것이
            # "아직 시드 안 됨"과 구분되지 않았다. 이제 갈라지므로 계약도 그렇게 적는다.
            '404': _REFERENCE_PROVIDER_NOT_FOUND_404,
        },
    ),
    'get_reference_revision': _operation(
        request=None,
        response='ReferenceRevisionDetail',
        permission=PERMISSIONS['get_reference_revision'],
        error_responses={'404': _REFERENCE_REVISION_OR_PROVIDER_NOT_FOUND_404},
    ),
    'create_reference_revision': _operation(
        request='CreateReferenceRevisionRequest',
        response='ReferenceRevisionEnvelope',
        permission=PERMISSIONS['create_reference_revision'],
        error_responses={
            '400': _REFERENCE_SCOPE_INVALID_400,
            '404': _REFERENCE_PROVIDER_NOT_FOUND_404,
        },
    ),
    # fork 와 편집은 둘 다 **상세**를 돌려준다(요약 봉투가 아니라). 시험원의 다음
    # 동작이 곧 "이 판을 화면에서 연다"이고, 상세에는 열 순서·식별 열·결합 형제가
    # 실려 있어 두 번째 왕복이 필요 없다.
    'fork_reference_revision': _operation(
        request=None,
        response='ReferenceRevisionDetail',
        permission=PERMISSIONS['fork_reference_revision'],
        error_responses={
            '404': _REFERENCE_REVISION_OR_PROVIDER_NOT_FOUND_404,
            '409': _REFERENCE_STATE_CONFLICT_409,
        },
    ),
    'list_reference_families': _operation(
        request=None,
        response='ReferenceFamilyList',
        permission=PERMISSIONS['list_reference_families'],
        error_responses={'404': _REFERENCE_PROVIDER_NOT_FOUND_404},
    ),
    'create_authored_reference_revision': _operation(
        request='CreateAuthoredReferenceRevisionRequest',
        response='ReferenceRevisionEnvelope',
        permission=PERMISSIONS['create_authored_reference_revision'],
        error_responses={
            '400': _REFERENCE_AUTHORED_INVALID_400,
            '404': _REFERENCE_PROVIDER_NOT_FOUND_404,
        },
    ),
    'update_reference_revision_rows': _operation(
        request='UpdateReferenceRevisionRowsRequest',
        response='ReferenceRevisionDetail',
        permission=PERMISSIONS['update_reference_revision_rows'],
        error_responses={
            '400': _REFERENCE_ROW_EDIT_INVALID_400,
            '404': _REFERENCE_REVISION_OR_PROVIDER_NOT_FOUND_404,
            '409': _REFERENCE_EDIT_STALE_409,
        },
    ),
    'update_reference_revision_entries': _operation(
        request='UpdateReferenceRevisionEntriesRequest',
        response='ReferenceRevisionDetail',
        permission=PERMISSIONS['update_reference_revision_entries'],
        error_responses={
            '400': _REFERENCE_EDIT_INVALID_400,
            '404': _REFERENCE_REVISION_OR_PROVIDER_NOT_FOUND_404,
            '409': _REFERENCE_EDIT_STALE_409,
        },
    ),
    'publish_reference_revision': _operation(
        request='PublishReferenceRevisionRequest',
        response='ReferenceRevisionEnvelope',
        permission=PERMISSIONS['publish_reference_revision'],
        error_responses={
            '404': _REFERENCE_REVISION_OR_PROVIDER_NOT_FOUND_404,
            '409': _REFERENCE_PUBLISH_CONFLICT_409,
        },
    ),
    'get_chamber_reference_bundle': _operation(
        request=None,
        response='ChamberReferenceBundle',
        permission=PERMISSIONS['get_chamber_reference_bundle'],
        # Two causes share this status, and the description must name both
        # (2026-08-25). The delivery provider comes from deployment config, so
        # an unregistered one 404s here even though no {provider_id} appears in
        # the path — and a node operator told only "unknown chamber_id" would
        # chase a chamber-registration problem that does not exist.
        error_responses={'404': _CHAMBER_OR_PROVIDER_NOT_FOUND_404},
    ),
}
