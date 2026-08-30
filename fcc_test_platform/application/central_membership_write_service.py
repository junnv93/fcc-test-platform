"""Platform membership write service (FE-P8, 2026-05-28).

``MembershipWriteService`` is the application boundary between the platform
driving adapter and the central membership write path. It owns the *decision*
logic that turns assign/revoke API calls into a stable platform outcome:

1. **boundary validation** — ``project_id`` must be a well-formed uuid;
   ``user_subject`` and ``role_key`` non-empty. A bad input raises
   ``ValueError`` (→ 400).
2. **role catalog whitelist** — ``role_key`` MUST be in the
   ``rbac_role_catalog.PROJECT_ROLE_KEYS`` SSOT (loaded from the schema JSON).
   An unknown role raises ``MembershipRoleUnknownError`` (→ 400). This is the
   "no hardcoded role grants in code" enforcement — role validity flows from
   the schema, never from a Python literal.
3. **user resolution** — ``user_subject`` → ``users.id`` via the RBAC read
   port. An unknown subject raises ``MembershipUserUnknownError`` (→ 404)
   because the IdP sync must onboard the user first.
4. **actor provenance** — the audit ``actor_subject`` is the authenticated
   principal (never the request body). The adapter binds the audit INSERT
   into the same transaction as the primary write.
5. **revoke not-found** — when the (project, user, role) triple has no
   current assignment, the adapter returns ``None``; the service raises
   ``MembershipNotFoundError`` (→ 404) WITHOUT emitting an audit (don't
   audit a no-op).

``condition_hash`` is not relevant here (membership is project-scoped, not
condition-scoped).

dependency-free of infrastructure / FastAPI / SQL — only the domain ports +
stdlib ``uuid`` / ``datetime``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from fcc_test_contracts.common.identity import LEGACY_IDENTITY_ISSUER
from fcc_test_platform.application.central_rbac_read_service import CentralRbacReadService
from application.central_contract.envelope_helpers import optional_text, require_uuid, text
from fcc_test_platform.application.rbac_role_catalog import is_known_role
from domain.services.team_policy import is_known_team, normalize_team
from domain.ports.output.central_membership_write_port import CentralMembershipWritePort


__all__ = [
    'MembershipNotFoundError',
    'MembershipRoleUnknownError',
    'MembershipUserUnknownError',
    'MembershipWriteService',
    'issuer_lookup_candidates',
]


class MembershipRoleUnknownError(ValueError):
    """Requested role_key is not in the rbac_role_grants SSOT (→ 400).

    Subclass of ValueError so the existing 400-mapping in ``api_error_status``
    catches it without a dedicated branch.
    """


class MembershipUserUnknownError(RuntimeError):
    """The user_subject has no central users row (→ 404)."""


class MembershipNotFoundError(RuntimeError):
    """No (project, user, role) triple currently assigned to revoke (→ 404)."""


def issuer_lookup_candidates(user_issuer: str, actor_issuer: str) -> tuple[str, ...]:
    """Ordered (issuer) candidates used to find the target user's central row.

    Central identity is keyed by ``(issuer, subject)``, but the assign/revoke
    payload carries only a subject in practice — the UI knows *who* to grant,
    not which issuer URL their row was written under. Canonicalizing a blank
    issuer straight to :data:`LEGACY_IDENTITY_ISSUER` made every OIDC user
    unresolvable: JIT provisioning writes their row under the *real* validated
    issuer, so the lookup missed and assignment 404'd.

    Resolution order when no issuer is given:

    1. the **actor's** issuer — membership is granted within one IdP, and the
       actor's issuer is validated, so it is the correct default;
    2. :data:`LEGACY_IDENTITY_ISSUER` — rows written before the issuer column
       existed (migration 008) and trusted-header/local principals.

    An **explicit** issuer is never widened: the caller named one identity, so
    exactly that one is looked up.
    """
    explicit = str(user_issuer or '').strip()
    if explicit:
        return (explicit,)
    candidates: list[str] = []
    actor = str(actor_issuer or '').strip()
    if actor:
        candidates.append(actor)
    if LEGACY_IDENTITY_ISSUER not in candidates:
        candidates.append(LEGACY_IDENTITY_ISSUER)
    return tuple(candidates)


class MembershipWriteService:
    def __init__(
        self,
        write_port: CentralMembershipWritePort,
        rbac_read: CentralRbacReadService,
        *,
        clock: Optional[Callable[[], str]] = None,
        id_factory: Optional[Callable[[], str]] = None,
    ) -> None:
        self._write = write_port
        self._rbac = rbac_read
        self._clock = clock or _utcnow_iso
        self._id_factory = id_factory or _uuid4_str

    def _resolve_user_id(self, subject: str, candidates: tuple[str, ...]) -> tuple[str, str]:
        """First matching central user id + the issuer it matched under.

        Returns ``('', '')`` when the subject exists under none of them — the
        caller raises ``MembershipUserUnknownError`` (404) with the tried list,
        so a miss is never silently treated as a different user.

        The matched issuer is returned (not discarded) so the audit row records
        WHICH identity was actually targeted. That matters for the legacy
        fallback: a subject absent under the actor's issuer but present under
        the legacy one resolves to the legacy row, and those two are only the
        same person if the subject string is globally unique — true for IdP
        UUIDs, not guaranteed for trusted-header subjects. Recording it keeps
        the fallback auditable instead of invisible.
        """
        for issuer in candidates:
            user_id = self._rbac.resolve_user_id(subject, user_issuer=issuer)
            if user_id:
                return user_id, issuer
        return '', ''

    def assign(
        self,
        project_id: str,
        *,
        user_subject: str,
        role_key: str,
        actor_subject: str,
        user_issuer: str = '',
        actor_issuer: str = '',
        expires_at: Optional[str] = None,
        team: Optional[str] = None,
    ) -> dict:
        """Assign or update a project membership role.

        Returns the resulting ``MembershipEnvelope`` (project_id /
        user_subject / role_key / assigned_at / expires_at). Raises:

        - ``ValueError`` — malformed project_id / empty user_subject /
          empty role_key / empty actor_subject.
        - ``MembershipRoleUnknownError`` — role_key not in PROJECT_ROLE_KEYS.
        - ``MembershipUserUnknownError`` — user_subject has no users row.
        """
        pid = require_uuid(project_id, 'project_id')
        subject = _require_text(user_subject, 'user_subject')
        # Optional issuer: a blank issuer resolves against the actor's issuer
        # first and the legacy issuer second, so the subject-based UI can
        # assign/revoke without knowing the issuer URL (see
        # ``issuer_lookup_candidates``). An explicit issuer is used verbatim.
        candidates = issuer_lookup_candidates(user_issuer, actor_issuer)
        role = _require_text(role_key, 'role_key')
        actor = _require_text(actor_subject, 'actor_subject')
        if not is_known_role(role):
            raise MembershipRoleUnknownError(
                f'role_key {role!r} is not in the project role catalog '
                f'(see rbac_role_grants in central_db_schema.v1.json)'
            )
        # Phase D — 시험원 하위 team(RF/SAR) 분류 라벨. Optional; an unknown
        # non-empty token is a 400 (validation), never silently dropped.
        if not is_known_team(team):
            raise ValueError(
                f'team {team!r} is not a known tester sub-team '
                f'(see TEAM_CODES in domain/services/team_policy.py)'
            )
        normalized_team = normalize_team(team)
        if normalized_team is not None and role != 'project_engineer':
            raise ValueError(
                'team may only be assigned with role_key project_engineer'
            )
        user_id, resolved_issuer = self._resolve_user_id(subject, candidates)
        if not user_id:
            raise MembershipUserUnknownError(
                f'user {subject!r} is not registered in central users under any '
                f'of {list(candidates)!r} — login once or onboard via IdP/SCIM '
                'before assignment'
            )
        now = self._clock()
        membership_record = {
            'id': self._id_factory(),
            'project_id': pid,
            'user_id': user_id,
            'role_key': role,
            'team': normalized_team,
            'assigned_at': now,
            'expires_at': _opt_text(expires_at),
            'created_at': now,
        }
        audit_record = self._audit_record(
            event_type='membership.assigned',
            project_id=pid,
            actor_subject=actor,
            target_user_subject=subject,
            role_key=role,
            occurred_at=now,
            detail_json=_detail_assign(role, expires_at, resolved_issuer),
        )
        row = self._write.assign_role_with_audit(membership_record, audit_record)
        return _envelope_from_row(row)

    def revoke(
        self,
        project_id: str,
        *,
        user_subject: str,
        role_key: str,
        actor_subject: str,
        user_issuer: str = '',
        actor_issuer: str = '',
    ) -> dict:
        """Revoke a project membership role.

        Returns the pre-revoke ``MembershipEnvelope``. Raises:

        - ``ValueError`` — malformed input (per ``assign``).
        - ``MembershipRoleUnknownError`` — role_key not in catalog.
        - ``MembershipUserUnknownError`` — user_subject has no users row.
        - ``MembershipNotFoundError`` — no current (user, role) assignment.
        """
        pid = require_uuid(project_id, 'project_id')
        subject = _require_text(user_subject, 'user_subject')
        # Same issuer resolution as ``assign`` — see ``issuer_lookup_candidates``.
        candidates = issuer_lookup_candidates(user_issuer, actor_issuer)
        role = _require_text(role_key, 'role_key')
        actor = _require_text(actor_subject, 'actor_subject')
        if not is_known_role(role):
            raise MembershipRoleUnknownError(
                f'role_key {role!r} is not in the project role catalog'
            )
        user_id, resolved_issuer = self._resolve_user_id(subject, candidates)
        if not user_id:
            raise MembershipUserUnknownError(
                f'user {subject!r} is not registered in central users under any '
                f'of {list(candidates)!r}'
            )
        now = self._clock()
        audit_record = self._audit_record(
            event_type='membership.revoked',
            project_id=pid,
            actor_subject=actor,
            target_user_subject=subject,
            role_key=role,
            occurred_at=now,
            detail_json=_detail_revoke(role, resolved_issuer),
        )
        revoked = self._write.revoke_role_with_audit(pid, user_id, role, audit_record)
        if revoked is None:
            raise MembershipNotFoundError(
                f'no membership (project={pid}, user={subject!r}, role={role!r}) to revoke'
            )
        return _envelope_from_row(revoked)

    def _audit_record(
        self,
        *,
        event_type: str,
        project_id: str,
        actor_subject: str,
        target_user_subject: Optional[str],
        role_key: Optional[str],
        occurred_at: str,
        detail_json: Optional[str],
        target_claim_id: Optional[str] = None,
    ) -> dict[str, Any]:
        return {
            'id': self._id_factory(),
            'event_type': event_type,
            'project_id': project_id,
            'actor_subject': actor_subject,
            'target_user_subject': target_user_subject,
            'target_claim_id': target_claim_id,
            'role_key': role_key,
            'detail_json': detail_json,
            'occurred_at': occurred_at,
            'created_at': occurred_at,
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


def _detail_assign(role_key: str, expires_at: Optional[str], resolved_issuer: str = '') -> str:
    """JSON-encoded audit detail for an assign event (verbatim role + expiry).

    ``resolved_issuer`` records WHICH identity the subject resolved to, so a
    legacy-issuer fallback is auditable rather than invisible.
    """
    import json
    return json.dumps(
        {
            'role_key': role_key,
            'expires_at': _opt_text(expires_at),
            'resolved_issuer': _opt_text(resolved_issuer),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _detail_revoke(role_key: str, resolved_issuer: str = '') -> str:
    import json
    return json.dumps(
        {'role_key': role_key, 'resolved_issuer': _opt_text(resolved_issuer)},
        ensure_ascii=False,
        sort_keys=True,
    )


def _envelope_from_row(row: dict) -> dict:
    return {
        'project_id': text(row.get('project_id')),
        'user_issuer': text(row.get('user_issuer')),
        'user_subject': text(row.get('user_subject')),
        'role_key': text(row.get('role_key')),
        'assigned_at': text(row.get('assigned_at')),
        'expires_at': optional_text(row.get('expires_at')),
        'team': optional_text(row.get('team')),
    }
