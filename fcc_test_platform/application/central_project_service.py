"""Platform project service (Phase 1, 2026-06-22).

``CentralProjectService`` is the application boundary between the platform
driving adapter (``PlatformApiAdapter``) and the central project read/write
ports. It owns the ADR-0017 decision logic for the "내 프로젝트" entry surface:

1. **boundary validation** — ``model_name`` non-empty (→ ``ValueError`` → 400);
   ``project_id`` well-formed uuid on detail reads.
2. **D1 — project = model 1:1 + same-model reuse** — creating a project creates
   one ``projects`` row + one ``device_models`` row with
   ``project_code == model name``. A request whose model name matches an
   existing ``project_code`` returns the EXISTING project (idempotent — never a
   duplicate, never a conflict).
3. **D3 — creator auto-admin** — on a genuinely new project the creator is
   granted ``project_admin`` membership via the shared ``MembershipWriteService``
   (reuses the FE-P8 audited write path; no new membership code). The created
   project is then read back and returned as the detail envelope.

ADR-0017 D2 — claim/coverage are NOT touched (sample-agnostic). Sample rows are
recorded later on the measurement path (Phase 3), so a freshly created project
has an empty ``samples`` list.

dependency-free of infrastructure / FastAPI / SQL — only the domain ports +
the membership write service + stdlib ``uuid`` / ``datetime``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable, Mapping, Optional

from fcc_test_contracts.common.identity import canonical_issuer
from fcc_test_platform.application.central_membership_write_service import (
    MembershipWriteService,
)
from fcc_test_platform.application.central_project_read_adapter import (
    PROJECT_DIRECTORY_KEYSET_DOMAINS,
)
from fcc_test_kernel.application.central_contract.envelope_helpers import (
    optional_text,
    require_uuid,
    text,
)
from fcc_test_kernel.application.central_contract.pagination import (
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from fcc_test_platform.application.rbac_role_catalog import PROJECT_ADMIN_ROLE_KEY
from fcc_test_platform.domain.ports.output.central_project_port import (
    CentralProjectError,
    CentralProjectReadPort,
    CentralProjectWritePort,
    ProjectIdentifierConflictError,
)
from fcc_test_platform.domain.ports.output.central_user_write_port import CentralUserWritePort
from fcc_test_platform.domain.services import fcc_id_policy
from fcc_test_platform.domain.services.project_directory_query import (
    PROJECT_DIRECTORY_CURSOR_FIELDS,
    normalize_search_term,
    search_like_pattern,
)
from fcc_test_kernel.domain.services.project_metadata_edit import (
    APPLICANT_IDENTITY_FIELD,
    APPLICANT_SUGGESTION_FIELDS,
    CREATE_PROJECT_IDENTITY_FIELD,
    DEVICE_MODEL_META_FIELDS,
    EDITABLE_PROJECT_META_FIELDS,
    PROJECT_TABLE_META_FIELDS,
    parse_project_create_request,
    parse_project_metadata_update,
)


__all__ = [
    'ProjectNotFoundError',
    'ProjectModelUnresolvedError',
    'CentralProjectService',
]


class ProjectModelUnresolvedError(ValueError):
    """The project carries no ``device_models`` model name (→ **400**, W3 백엔드).

    Subclass of ``ValueError`` so every existing boundary that already maps a
    validation failure to 400 keeps working; the platform error table lists it
    ABOVE the bare ``ValueError`` row so it resolves to its own machine code
    (``PROJECT_MODEL_UNRESOLVED``) and a client can tell "this project needs a
    model before you can import samples into it" apart from "your request body is
    malformed" without string-matching ``detail``.

    Raised only where an operation NEEDS the attribution axis (sample-inventory
    import): a project without a model is otherwise perfectly readable.
    """


class ProjectNotFoundError(LookupError):
    """Requested project_id has no central ``projects`` row (→ 404).

    Subclass of ``LookupError`` (mirror of ``ChamberNotFoundError``) so the
    platform error table maps it to ``NOT_FOUND`` without colliding with the
    ``ValueError`` → 400 family.
    """


#: Accepted ``?status`` filter values for ``list_projects`` (project-status-
#: visibility). Mirrors the OpenAPI enum so an out-of-domain value is a loud 400
#: (``ValueError``) rather than a silently empty result.
_VALID_LIST_STATUS = frozenset({'active', 'completed', 'all'})


class CentralProjectService:
    def __init__(
        self,
        read_port: CentralProjectReadPort,
        write_port: CentralProjectWritePort,
        membership_write_service: MembershipWriteService,
        *,
        user_write_port: Optional[CentralUserWritePort] = None,
        clock: Optional[Callable[[], str]] = None,
        id_factory: Optional[Callable[[], str]] = None,
    ) -> None:
        self._read = read_port
        self._write = write_port
        if membership_write_service is None:
            raise ValueError(
                'membership_write_service is required — the creator auto-admin '
                'grant (ADR-0017 D3) has no alternative path'
            )
        self._membership = membership_write_service
        # JIT provisioning (결함 B, issuer+subject): when wired, the authenticated
        # creator is upserted into central ``users`` (keyed by the OIDC (issuer,
        # subject) tuple) before the project_admin grant — so a brand-new principal
        # no longer 404s on "onboard via the IdP sync first". Optional so
        # auth-disabled / pre-JIT compositions keep working.
        self._users = user_write_port
        self._clock = clock or _utcnow_iso
        self._id_factory = id_factory or _uuid4_str

    def list_projects(
        self,
        *,
        status: Optional[str] = 'active',
        q: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        """Return a page of visible projects (read-open — any authenticated
        caller, NOT membership-scoped).

        Defaults to ``active`` (in-progress) projects; pass ``status='completed'``
        or ``status='all'``. An out-of-domain ``status`` raises ``ValueError``
        (→ 400) rather than returning a silently empty list.

        W3 백엔드 — directory scale. ``q`` is a case-insensitive substring search
        over the ``PROJECT_SEARCH_COLUMNS`` SSOT (관리번호 포함); ``limit``/``cursor``
        drive **keyset** pagination (never OFFSET). Returns
        ``{'items': [...], 'next_cursor': str|None}`` — mirror of
        ``CentralReadService._read_page``, so the route emits the body as a plain
        array and puts the continuation token in the response header.

        **Backward compatibility**: with no ``q``/``limit``/``cursor`` this is the
        pre-W3 unbounded read and ``items`` is byte-identical to the old list
        return (계약 S11) — **정렬 지시까지** 옛 문장과 같다(keyset tie-breaker 는
        페이지 경계가 있는 질의에만 붙는다, ``directory_order_columns``). 바뀐 것은
        *반환값의 형태*뿐이고 라우트가 내보내는 HTTP body 는 그대로다.
        """
        normalized = _opt_text(status) or 'active'
        if normalized not in _VALID_LIST_STATUS:
            raise ValueError(
                f'invalid status filter {normalized!r} — expected one of '
                f'{sorted(_VALID_LIST_STATUS)}'
            )
        # Blank ``?q=`` (a cleared search box) is "no filter", not a bad request.
        term = normalize_search_term(q)
        pattern = search_like_pattern(term) if term is not None else None
        if limit is None and not cursor:
            rows = self._read.list_projects(status=normalized, q=pattern)
            return {'items': [_list_envelope(row) for row in rows], 'next_cursor': None}
        # A cursor (with or without an explicit limit) means the client is paging —
        # decode/validate it loudly (a silently-ignored bad cursor would restart at
        # page 1 and make a forward-paging client re-read / skip rows) and apply the
        # default page size when no explicit limit was given.
        size = clamp_limit(limit)
        after = (
            decode_cursor(
                cursor, arity=len(PROJECT_DIRECTORY_CURSOR_FIELDS),
                domains=PROJECT_DIRECTORY_KEYSET_DOMAINS,
            )
            if cursor else None
        )
        # One extra row detects a further page without a second query.
        rows = self._read.list_projects(
            status=normalized, q=pattern, limit=size + 1, after=after,
        )
        has_more = len(rows) > size
        page_rows = rows[:size]
        next_cursor = None
        if has_more and page_rows:
            # Built from the READ ROW, not the response envelope: ``created_at`` is
            # deliberately absent from the envelope (adding it would break the
            # byte-identical guarantee above), so the envelope cannot carry the
            # full sort key.
            next_cursor = encode_cursor(
                [page_rows[-1][field] for field in PROJECT_DIRECTORY_CURSOR_FIELDS]
            )
        return {
            'items': [_list_envelope(row) for row in page_rows],
            'next_cursor': next_cursor,
        }

    def list_applicant_suggestions(
        self, *, q: Optional[str] = None, limit: Optional[int] = None,
    ) -> list[dict]:
        """Return the applicant directory for the create form's auto-fill.

        신청자는 마스터 테이블이 아니라 **프로젝트 행에서 파생**된다(계약 결정):
        같은 신청자로 만든 프로젝트가 여럿이면 가장 최근 것의 주소/제조사가 기본값이
        된다. 별도 마스터를 두면 프로젝트가 든 값과 마스터 값이 갈라지고, 어느 쪽이
        진실인지 묻는 질문이 새로 생긴다 — 그 질문 자체를 만들지 않는다.

        ``q`` 는 프로젝트 검색과 **같은 정규화·이스케이프 규약**을 쓴다
        (``normalize_search_term`` → ``search_like_pattern``): 빈 검색어는 필터
        없음이고, 사용자가 친 ``%``/``_`` 는 와일드카드가 아니라 글자다.

        ``limit`` 은 항상 유계다 — ``clamp_limit`` 이 기본/상한 SSOT 이고, 상한 없는
        읽기를 만들지 않는다. 자동완성은 타이핑마다 호출되므로 상한 없는 읽기는
        신청자가 누적될수록 그대로 비용이 된다.

        Returns: ``APPLICANT_SUGGESTION_FIELDS`` + ``project_count`` 을 담은 행들,
        최근 쓰인 신청자 순. 빈 목록은 "아직 신청자가 없다"는 정상 상태다(백엔드
        장애는 ``CentralProjectError`` 로 loud 하게 드러난다).
        """
        term = normalize_search_term(q)
        pattern = search_like_pattern(term) if term is not None else None
        rows = self._read.list_applicant_suggestions(q=pattern, limit=clamp_limit(limit))
        return [_applicant_envelope(row) for row in rows]

    def get_project(self, project_id: str) -> dict:
        """Return one project's detail. Raises ``ValueError`` (malformed id) or
        ``ProjectNotFoundError`` (unknown project)."""
        pid = require_uuid(project_id, 'project_id')
        detail = self._read.read_project_detail(pid)
        if detail is None:
            raise ProjectNotFoundError(f'unknown project_id {pid!r}')
        return _detail_envelope(detail)

    def resolve_model_name(self, project_id: str) -> str:
        """Return the project's authoritative model name — the **attribution axis**.

        W3 백엔드. Sample-inventory import needs to know which model's rows belong
        to this project, and the only authoritative answer is the central
        ``device_models.model_name`` this project 1:1-overlays (ADR-0017 D1) — NOT
        whatever model name happens to appear first in the uploaded workbook, and
        never a client-supplied value (letting the request body pick the
        attribution axis would promote mis-attribution to an API feature).

        Raises the same errors as :meth:`get_project` (``ValueError`` → 400 on a
        malformed id, :class:`ProjectNotFoundError` → 404, ``CentralProjectError``
        → 503) plus :class:`ProjectModelUnresolvedError` when the project carries
        no model. Fail-closed on purpose: an import that cannot name its
        attribution axis has no safe default, and its write has no undo.
        """
        model_name = (self.get_project(project_id).get('model_name') or '').strip()
        if not model_name:
            raise ProjectModelUnresolvedError(
                f'project {project_id!r} has no device model — cannot attribute a '
                'sample-inventory import to a model'
            )
        return model_name

    def update_project_metadata(
        self, project_id: str, body: Optional[Mapping] = None,
    ) -> dict:
        """Partially update a project's 성적서 표지 메타 and return its detail.

        Partial-update semantics (``project_metadata_edit`` SSOT): a field the
        client did not send stays unchanged, a field sent as ``null`` is cleared.
        ``status`` / ``model_name`` / ``project_code`` are rejected loudly — the
        first belongs to the complete/reopen action sub-resources, the latter two
        are the project identity (re-keying, ADR-0005).

        Raises ``ValueError`` (malformed body/id → 400) or
        ``ProjectNotFoundError`` (unknown project → 404). NOT protected against
        lost updates: two concurrent editors of the SAME field are last-write-wins
        (different fields never collide since only sent keys are written).
        """
        updates = parse_project_metadata_update(body)
        pid = require_uuid(project_id, 'project_id')
        updated = self._write.update_project_metadata(pid, updates, self._clock())
        if updated is None:
            raise ProjectNotFoundError(f'unknown project_id {pid!r}')
        return self.get_project(pid)

    def complete_project(self, project_id: str) -> dict:
        """Mark a project completed (status → ``completed``). **Idempotent** —
        completing an already-completed project succeeds as a no-op. Returns the
        updated project detail. Raises ``ProjectNotFoundError`` (unknown project)."""
        return self._set_project_status(project_id, 'completed')

    def reopen_project(self, project_id: str) -> dict:
        """Reopen a project (status → ``active``). **Idempotent** — reopening an
        already-active project is a no-op success. Returns the updated project
        detail. Raises ``ProjectNotFoundError`` (unknown project)."""
        return self._set_project_status(project_id, 'active')

    def _set_project_status(self, project_id: str, status: str) -> dict:
        # Explicit-action endpoints (complete/reopen) pin the target status, so
        # the value is never client-supplied (no invalid-transition surface). The
        # write is idempotent (status is overwritten unconditionally — no
        # WHERE status= guard), so re-completing / re-opening is a safe no-op. The
        # DB CHECK (ck_projects_status) is the final guard.
        pid = require_uuid(project_id, 'project_id')
        updated = self._write.update_project_status(pid, status, self._clock())
        if updated is None:
            raise ProjectNotFoundError(f'unknown project_id {pid!r}')
        return self.get_project(pid)

    def create_project(
        self, body: Optional[Mapping] = None, *, actor_subject: str,
        actor_issuer: str = '', actor_email: str = '', actor_display_name: str = '',
    ) -> dict:
        """Create (or reuse) a project from the request ``body`` and return its detail.

        ADR-0017 D1 — ``project_code == model name``; a same-code request returns
        the existing project (idempotent). D3 — the creator is granted
        ``project_admin``. The creator is first JIT-provisioned into central
        ``users`` (결함 B) so a brand-new principal does not 404.

        The user-ensure + admin grant run on BOTH the new and the reuse path and
        are each idempotent, so a partial failure self-heals on retry rather than
        leaving an owner-less project (Codex review §2 — retry story in lieu of a
        single cross-service transaction; the residual non-atomicity is tracked).

        **본문은 도메인 파서가 소유한다** (2026-09-04). 이전에는 이 시그니처가 필드
        하나당 키워드 인자 하나를 들고 있었고, HTTP 어댑터가 ``payload.get(...)`` 를
        같은 수만큼 늘어놓아 넘겼다 — 즉 편집 필드 목록의 사본이 서비스와 어댑터에
        각각 있었다. 지금은 ``parse_project_create_request`` 하나가 필수/폐기/미지
        필드를 판정하고, 아래 레코드 조립은 필드→테이블 SSOT 에서 파생한다. 필드가
        늘거나 줄 때 이 파일은 **바뀌지 않는다**.

        Raises:
            ValueError: 본문 검증 실패(필수 누락, 폐기·미지 필드 등) → 400.
        """
        values = parse_project_create_request(body)
        # 필수 판정을 파서가 이미 통과시켰으므로 non-empty 가 보장된다.
        name = str(values[CREATE_PROJECT_IDENTITY_FIELD])
        actor = _require_text(actor_subject, 'actor_subject')
        # actor_issuer is optional: a blank issuer (legacy / trusted-header /
        # claim-less principal) canonicalizes to LEGACY_IDENTITY_ISSUER so central
        # identity stays keyed by (issuer, subject) without forcing every caller to
        # know the issuer URL.
        issuer = canonical_issuer(actor_issuer)
        # project_code is the natural identity (ADR-0017 D1). Verbatim trimmed
        # model name (schema ux_projects_project_code uniqueness is exact-match).
        project_code = name

        existing = self._write.find_project_by_code(project_code)
        if existing is not None:
            # D1 same-model reuse. Re-assert the creator's JIT identity + admin
            # grant (both idempotent) so a legacy orphaned project self-heals.
            project_id = text(existing.get('project_id'))
            self._ensure_actor_user(actor, issuer, actor_email, actor_display_name)
            self._grant_creator_admin(project_id, actor)
            return self.get_project(project_id)

        now = self._clock()
        project_id = self._id_factory()
        # 메타 칸은 **소속 테이블 SSOT** 로 갈라 담는다. 값은 파서가 이미 정규화했다
        # (trim, 빈 문자열 → None). 본문에 없던 칸은 ``values`` 에 없으므로 ``None`` 이
        # 되어 컬럼이 NULL 로 남는다 — 생성에서는 "미기재"와 "지움"이 같은 결과다.
        #
        # ``management_number`` 는 UNIQUE 이고 성적서 번호(S-{management_number}-…)의
        # 재료라 필수 칸이며, ``status`` 는 생성 시 항상 'active' 다(값 도메인
        # active|completed — 영문 토큰, 한글은 i18n). ``fcc_id`` 는 저장하지 않는다:
        # fcc_grantee_code + model_name 에서 파생된다(fcc_id_policy SSOT).
        # ``projects.name`` 은 없다(2026-09-04, 마이그레이션 033). 그 컬럼은 항상
        # ``project_code`` 의 사본이었고 아무도 읽지 않았다 — ADR-0017 D1 이
        # ``project_code == model name`` 을 못박은 뒤로는 같은 문자열의 세 번째
        # 사본이었다.
        project_record = {
            'id': project_id,
            'project_code': project_code,
            'status': 'active',
            **{field: values.get(field) for field in PROJECT_TABLE_META_FIELDS},
            'created_at': now,
            'updated_at': now,
        }
        device_model_record = {
            'id': self._id_factory(),
            'project_id': project_id,
            'model_name': name,
            **{field: values.get(field) for field in DEVICE_MODEL_META_FIELDS},
            'created_at': now,
            'updated_at': now,
        }
        atomic_create = getattr(self._write, 'create_project_with_model_and_admin_grant', None)
        if not callable(atomic_create):
            raise CentralProjectError(
                'atomic project creator grant is required: project, user, '
                'membership, and audit must commit in one transaction'
            )
        try:
            atomic_create(
                project_record,
                device_model_record,
                self._actor_user_record(actor, issuer, actor_email, actor_display_name, now),
                {
                    'id': self._id_factory(),
                    'project_id': project_id,
                    'user_id': None,
                    'role_key': PROJECT_ADMIN_ROLE_KEY,
                    'team': None,
                    'assigned_at': now,
                    'expires_at': None,
                    'created_at': now,
                },
                {
                    'id': self._id_factory(),
                    'event_type': 'membership.assigned',
                    'project_id': project_id,
                    'actor_subject': actor,
                    'target_user_subject': actor,
                    'target_claim_id': None,
                    'role_key': PROJECT_ADMIN_ROLE_KEY,
                    'detail_json': '{"role_key": "project_admin", "expires_at": null}',
                    'occurred_at': now,
                    'created_at': now,
                },
            )
        except CentralProjectError as exc:
            # W3 백엔드 — a UNIQUE conflict on a key OTHER than project_code (i.e.
            # management_number) is a genuine client-fixable 409, NOT a create
            # race. Re-raise it before the same-code self-heal below, otherwise a
            # duplicate 관리번호 would be reported as a backend failure (or, worse,
            # silently return some other project).
            if (
                isinstance(exc, ProjectIdentifierConflictError)
                and exc.field != 'project_code'
            ):
                raise
            # Race: a concurrent creator won. Re-assert the JIT identity + admin
            # grant on the existing project (both idempotent) and return it.
            raced = self._write.find_project_by_code(project_code)
            if raced is None:
                raise
            project_id = text(raced.get('project_id'))
            self._ensure_actor_user(actor, issuer, actor_email, actor_display_name)
            self._grant_creator_admin(project_id, actor)
            return self.get_project(project_id)
        return self.get_project(project_id)

    def _grant_creator_admin(self, project_id: str, actor: str) -> None:
        """Idempotently grant the creator project_admin (ADR-0017 D3).

        Used on the reuse / race self-heal paths (the new-project path grants
        atomically inside ``create_project_with_model_and_admin_grant``).
        """
        self._membership.assign(
            project_id,
            user_subject=actor,
            role_key=PROJECT_ADMIN_ROLE_KEY,
            actor_subject=actor,
        )

    def _ensure_actor_user(
        self, actor: str, issuer: str, email: str, display_name: str,
    ) -> None:
        if self._users is None:
            return
        user = self._users.ensure_user(
            self._actor_user_record(actor, issuer, email, display_name, self._clock())
        )
        if not _is_enabled(user.get('enabled')):
            raise PermissionError('actor user is disabled')

    def _actor_user_record(
        self, actor: str, issuer: str, email: str, display_name: str, now: str,
    ) -> dict:
        return {
            'id': self._id_factory(),
            'issuer': issuer,
            'subject': actor,
            'display_name': str(display_name or '').strip(),
            'email': str(email or '').strip(),
            'enabled': True,
            'created_at': now,
            'updated_at': now,
        }


# ── helpers ────────────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid4_str() -> str:
    return str(uuid.uuid4())


def _require_text(value: object, label: str) -> str:
    cleaned = '' if value is None else str(value).strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _opt_text(value: object) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _is_enabled(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {'0', 'false', 'f', 'no', 'off'}




def _meta_envelope(row: dict) -> dict:
    """표지 메타 칸을 도메인 SSOT 순서대로 투영한다.

    필드를 손으로 나열하지 않는 이유는 이 함수의 호출자가 둘(목록/상세)이기
    때문이다 — 나열하면 사본이 둘 생기고, 편집 필드가 바뀌는 날 한쪽만 고쳐진다.
    (``customer`` 폐기 때 실제로 두 곳이 각각 옛 필드를 들고 있었다.)
    """
    return {field: optional_text(row.get(field)) for field in EDITABLE_PROJECT_META_FIELDS}


def _applicant_envelope(row: dict) -> dict:
    """신청자 제안 한 건. 식별 칸은 조회 조건상 non-null 이 보장되므로 ``text``,
    함께 따라오는 칸은 미기재가 정상이므로 ``optional_text`` 다 — 그 구분이 곧
    "이름 없는 제안은 제안이 아니다"라는 사실의 표현이다."""
    return {
        **{
            field: (
                text(row.get(field)) if field == APPLICANT_IDENTITY_FIELD
                else optional_text(row.get(field))
            )
            for field in APPLICANT_SUGGESTION_FIELDS
        },
        'project_count': int(row.get('project_count') or 0),
    }


def _list_envelope(row: dict) -> dict:
    return {
        'project_id': text(row.get('project_id')),
        'project_code': text(row.get('project_code')),
        'model_name': text(row.get('model_name')),
        'status': optional_text(row.get('status')),
        **_meta_envelope(row),
        # Derived (never stored): grantee_code + product_code(model_name).
        'fcc_id': fcc_id_policy.fcc_id(
            row.get('fcc_grantee_code'), text(row.get('model_name'))
        ),
        'sample_count': int(row.get('sample_count') or 0),
    }


def _detail_envelope(row: dict) -> dict:
    samples = row.get('samples') or []
    return {
        'project_id': text(row.get('project_id')),
        'project_code': text(row.get('project_code')),
        'model_name': text(row.get('model_name')),
        'status': optional_text(row.get('status')),
        **_meta_envelope(row),
        # Derived (never stored): grantee_code + product_code(model_name).
        'fcc_id': fcc_id_policy.fcc_id(
            row.get('fcc_grantee_code'), text(row.get('model_name'))
        ),
        'created_at': text(row.get('created_at')),
        'samples': [_sample_envelope(sample) for sample in samples],
    }


def _sample_envelope(row: dict) -> dict:
    return {
        'sample_id': text(row.get('sample_id')),
        'sample_code': text(row.get('sample_code')),
        'serial_number': optional_text(row.get('serial_number')),
        'model_id': optional_text(row.get('model_id')),
        # PM 칸 인벤토리 메타(Phase C, 전부 nullable text).
        'sample_number': optional_text(row.get('sample_number')),
        'test_category': optional_text(row.get('test_category')),
        'label_number': optional_text(row.get('label_number')),
        'smsn': optional_text(row.get('smsn')),
        'intake_cert': optional_text(row.get('intake_cert')),
        'assigned_team': optional_text(row.get('assigned_team')),
        'sender': optional_text(row.get('sender')),
        'receiver': optional_text(row.get('receiver')),
        'received_date': optional_text(row.get('received_date')),
        'released_date': optional_text(row.get('released_date')),
        # Payload-reduction follow-up — 시험원 입고 칸은 최신 입고 1건 + 이력 개수만
        # 싣는다(전체 append-only 이력을 매 detail 응답에 싣지 않는다). 최신성 판정은
        # read adapter(created_at DESC) SSOT 가 이미 끝냈으므로 여기서 head 만 투영.
        'latest_intake': _latest_intake_envelope(row.get('latest_intake')),
        'intake_count': int(row.get('intake_count') or 0),
    }


def _latest_intake_envelope(row: Optional[dict]) -> Optional[dict]:
    """Project the sample's latest intake (or None when it has no history)."""
    if not row:
        return None
    return _intake_envelope(row)


def _intake_envelope(row: dict) -> dict:
    return {
        'sample_intake_id': text(row.get('sample_intake_id')),
        'intake_date': optional_text(row.get('intake_date')),
        'bl': optional_text(row.get('bl')),
        'ap': optional_text(row.get('ap')),
        'cp': optional_text(row.get('cp')),
        'csc': optional_text(row.get('csc')),
        'rf_cal': optional_text(row.get('rf_cal')),
        'hw_rev': optional_text(row.get('hw_rev')),
        'note': optional_text(row.get('note')),
    }
