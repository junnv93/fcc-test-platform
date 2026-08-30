"""Output port — central RBAC read (FE-P8, 2026-05-28).

The platform read+write surface authorizes a request by unioning the
principal's token-borne permissions with the permissions granted by their
project membership. Membership resolution reads through the
``project_member_permissions`` view (see
``docs/platform/central_db_schema.v1.json``) so the entire 5-way join collapses
into one indexed SELECT per authz check — never an N+1 walk over the
``project_membership`` table.

The Port also exposes a ``read_project_memberships`` list path (FE-P2 RBAC
roster) so the read surface stays SELECT-only on this side; membership writes
live in ``central_membership_write_port``.

Read-only by construction: the Port exposes only ``read_*`` methods.

Placement (hexagonal): driven-port abstraction lives under
``domain/ports/output`` alongside ``central_read_port.py`` /
``central_claim_write_port.py`` (``TestProtocolPlacement`` enforces all
``Protocol`` definitions live under ``domain/ports``).

dependency-free: stdlib typing only (no infrastructure / pyvisa / openpyxl /
pandas / PySide6 / fastapi / sqlalchemy / psycopg imports).
"""
from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable


__all__ = [
    'CentralRbacReadError',
    'CentralRbacReadPort',
]


class CentralRbacReadError(RuntimeError):
    """Raised when a central RBAC read fails — loud, never a silent empty result.

    A silently-empty membership response would deny a legitimately-authorized
    operator (false 403) — worse than a 5xx because the operator may chase the
    wrong configuration. Always raise loud so the API surfaces a 503 and the
    cause (DB outage / typo) is visible.
    """


@runtime_checkable
class CentralRbacReadPort(Protocol):
    """Read project-scoped RBAC state from the central database."""

    def read_member_permissions(
        self,
        project_id: str,
        user_issuer: str,
        user_subject: str,
    ) -> list[dict]:
        """Return rows for one (project, issuer, subject).

        One row per granted permission. The view filters expired rows in the
        service layer (with an injected clock) so authorization is
        deterministic; the adapter returns ``expires_at`` verbatim. Empty list
        means "no membership for this user in this project" — the authz layer
        then falls back to the token-only path (backward-compatible).

        Row dict keys: ``project_id`` / ``user_issuer`` / ``user_subject`` /
        ``permission_key`` / ``role_key`` / ``assigned_at`` / ``expires_at`` /
        ``user_enabled``.
        """
        ...

    def read_project_memberships(
        self,
        project_id: str,
        *,
        limit: Optional[int] = None,
        after: Optional[Sequence[str]] = None,
    ) -> list[dict]:
        """Return one row per assigned (user, role) pair in the project.

        Distinct from :meth:`read_member_permissions` (which expands roles to
        permissions) — this is the RBAC roster for the FE-P2 admin UI. Row
        dict keys: ``project_id`` / ``user_issuer`` / ``user_subject`` /
        ``role_key`` / ``assigned_at`` / ``expires_at`` / ``team``. ``limit`` /
        ``after`` drive the same opt-in keyset pagination as the coverage /
        claims reads.
        """
        ...

    def resolve_user_id(self, user_issuer: str, user_subject: str) -> Optional[str]:
        """Resolve a central ``users.id`` uuid from ``(issuer, subject)``.

        Returns ``None`` when the subject is not registered (the IdP sync
        populates ``users``; an unknown subject means the user hasn't logged
        in yet, which the membership write service maps to a 404). Used by
        :class:`MembershipWriteService` to translate API inputs (subjects) to
        the project_membership.user_id FK.
        """
        ...

    def read_user_enabled(self, user_issuer: str, user_subject: str) -> Optional[bool]:
        """Return central ``users.enabled`` for ``(issuer, subject)``.

        ``None`` means the principal has no central users row; callers may keep
        token-only compatibility in that case. ``False`` is an explicit central
        disable and must deny both token and membership grants.
        """
        ...
