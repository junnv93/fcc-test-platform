"""Central PostgreSQL RBAC read adapter (FE-P8, 2026-05-28).

``PostgresCentralRbacReadAdapter`` implements ``CentralRbacReadPort`` against
the central ``project_member_permissions`` view (effective permissions per
member) + the ``project_membership`` table joined to ``users`` (RBAC roster
read) + the ``users`` table (subject → uuid id resolution for membership write).

Design (mirrors ``PostgresCentralReadAdapter`` + ``PostgresCentralClaimWriteAdapter``):

- **injected ``connection_factory``** (``() -> DbConnection``). psycopg is
  imported lazily by the composition root only; this module never imports a
  PostgreSQL driver (frozen-exe safe).
- **read-only**: only ``SELECT``. No INSERT/UPDATE/DELETE.
- **``%s`` paramstyle** (psycopg). project_id / user_subject are bound
  parameters — injection-safe.
- **loud-fail**: a connection/query failure raises ``CentralRbacReadError``.
- column SSOT: ``MEMBER_PERMISSION_COLUMNS`` / ``MEMBERSHIP_COLUMNS`` mirror
  the central view + table; the contract test cross-checks them.
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from fcc_test_kernel.application.central_contract.pagination import CursorValueDomain
from fcc_test_kernel.domain.ports.output.central_rbac_read_port import CentralRbacReadError
from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'MEMBERSHIP_COLUMNS',
    'MEMBERSHIP_KEYSET',
    'MEMBERSHIP_KEYSET_DOMAINS',
    'MEMBERSHIP_QUERY_SQL',
    'MEMBERSHIP_QUERY_SQL_ALL',
    'MEMBERSHIP_QUERY_SQL_PAGED',
    'MEMBER_PERMISSIONS_VIEW',
    'MEMBER_PERMISSION_COLUMNS',
    'MEMBER_PERMISSIONS_QUERY_SQL',
    'PROJECT_MEMBERSHIP_TABLE',
    'USERS_TABLE',
    'USER_ID_BY_SUBJECT_SQL',
    'USER_ENABLED_BY_SUBJECT_SQL',
    'PostgresCentralRbacReadAdapter',
]


MEMBER_PERMISSIONS_VIEW = 'project_member_permissions'
PROJECT_MEMBERSHIP_TABLE = 'project_membership'
USERS_TABLE = 'users'


#: Columns returned by ``read_member_permissions``. Order defines the row→dict
#: mapping. Mirrors the project_member_permissions view SELECT aliases.
MEMBER_PERMISSION_COLUMNS: tuple[str, ...] = (
    'project_id',
    'user_issuer',
    'user_subject',
    'permission_key',
    'role_key',
    'assigned_at',
    'expires_at',
    'user_enabled',
)


#: Columns returned by ``read_project_memberships``. The RBAC roster — one row
#: per (user_subject, role_key) assignment.
MEMBERSHIP_COLUMNS: tuple[str, ...] = (
    'project_id',
    'user_issuer',
    'user_subject',
    'role_key',
    'assigned_at',
    'expires_at',
    # Phase D (2026-06-23) — 시험원 하위 team(RF/SAR) 분류 라벨(권한 직교, nullable).
    'team',
)

#: Keyset cursor order for the membership roster — deterministic, indexed.
MEMBERSHIP_KEYSET: tuple[str, ...] = ('user_issuer', 'user_subject', 'role_key')

#: Value domain per keyset column (부채 청산 M3, 2026-07-30). All three resolve to
#: ``text`` in the central schema (``users.issuer`` / ``users.subject`` /
#: ``project_membership.role_key``), so no cursor value can be out of domain —
#: but the fact is declared rather than assumed, so a future keyset over a uuid /
#: timestamp column cannot inherit "any string is fine" by silence.
MEMBERSHIP_KEYSET_DOMAINS: tuple[CursorValueDomain, ...] = (
    CursorValueDomain.TEXT,  # users.issuer   text
    CursorValueDomain.TEXT,  # users.subject  text
    CursorValueDomain.TEXT,  # project_membership.role_key  text
)


#: Effective-permissions read — one SELECT per authz check. ORDER BY only for
#: deterministic test output (the authz layer doesn't care about row order).
MEMBER_PERMISSIONS_QUERY_SQL = (
    f'SELECT "project_id", "user_issuer", "user_subject", "permission_key", '
    f'"role_key", "assigned_at", "expires_at", "user_enabled" '
    f'FROM "{MEMBER_PERMISSIONS_VIEW}" '
    f'WHERE "project_id" = %s AND "user_issuer" = %s AND "user_subject" = %s '
    f'ORDER BY "permission_key"'
)


def _select_membership(
    *, keyset: bool = False, limited: bool = True,
) -> str:
    """Build the membership roster SELECT (project_membership ⋈ users).

    Read-only by construction. Same keyset pagination shape as the coverage /
    claims reads — opt-in via ``limit`` query param; ``after`` (cursor) is a
    row-value comparison on (user_subject, role_key). ``limited=False`` omits
    LIMIT for the backward-compatible unbounded read.
    """
    select_cols = (
        '"pm"."project_id", "u"."issuer" AS "user_issuer", '
        '"u"."subject" AS "user_subject", "pm"."role_key", '
        '"pm"."assigned_at", "pm"."expires_at", "pm"."team"'
    )
    where = '"pm"."project_id" = %s'
    if keyset:
        where += ' AND ("u"."issuer", "u"."subject", "pm"."role_key") > (%s, %s, %s)'
    limit_clause = ' LIMIT %s' if limited else ''
    return (
        f'SELECT {select_cols} FROM "{PROJECT_MEMBERSHIP_TABLE}" pm '
        f'JOIN "{USERS_TABLE}" u ON u."id" = pm."user_id" '
        f'WHERE {where} ORDER BY "user_issuer", "user_subject", "role_key"{limit_clause}'
    )


MEMBERSHIP_QUERY_SQL_ALL = _select_membership(limited=False)
MEMBERSHIP_QUERY_SQL = _select_membership()
MEMBERSHIP_QUERY_SQL_PAGED = _select_membership(keyset=True)


#: Resolve users.id from a subject. Returns one row when the user exists,
#: empty when not registered. The membership write service maps the latter
#: to a 404 (the IdP must sync the user first).
USER_ID_BY_SUBJECT_SQL = (
    f'SELECT "id" FROM "{USERS_TABLE}" WHERE "issuer" = %s AND "subject" = %s'
)
USER_ENABLED_BY_SUBJECT_SQL = (
    f'SELECT "enabled" FROM "{USERS_TABLE}" WHERE "issuer" = %s AND "subject" = %s'
)


class PostgresCentralRbacReadAdapter:
    """``CentralRbacReadPort`` over a central PostgreSQL connection factory."""

    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def read_member_permissions(
        self, project_id: str, user_issuer: str, user_subject: str,
    ) -> list[dict]:
        return self._query(
            MEMBER_PERMISSIONS_QUERY_SQL,
            MEMBER_PERMISSION_COLUMNS,
            (project_id, user_issuer, user_subject),
        )

    def read_project_memberships(
        self, project_id: str, *,
        limit: Optional[int] = None, after: Optional[Sequence[str]] = None,
    ) -> list[dict]:
        if limit is None:
            return self._query(MEMBERSHIP_QUERY_SQL_ALL, MEMBERSHIP_COLUMNS, (project_id,))
        if after is None:
            return self._query(MEMBERSHIP_QUERY_SQL, MEMBERSHIP_COLUMNS, (project_id, limit))
        return self._query(
            MEMBERSHIP_QUERY_SQL_PAGED, MEMBERSHIP_COLUMNS,
            (project_id, *after, limit),
        )

    def resolve_user_id(self, user_issuer: str, user_subject: str) -> Optional[str]:
        rows = self._query(USER_ID_BY_SUBJECT_SQL, ('id',), (user_issuer, user_subject))
        return rows[0]['id'] if rows else None

    def read_user_enabled(self, user_issuer: str, user_subject: str) -> Optional[bool]:
        rows = self._query(
            USER_ENABLED_BY_SUBJECT_SQL, ('enabled',), (user_issuer, user_subject)
        )
        if not rows:
            return None
        return _is_enabled(rows[0].get('enabled'))

    def _query(self, statement: str, columns: tuple[str, ...], params: tuple) -> list[dict]:
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001 — wrap as loud CentralRbacReadError
            raise CentralRbacReadError(f'central RBAC read connection failed: {exc}') from exc
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(statement, params)
                rows = list(cursor.fetchall())
            finally:
                cursor.close()
        except Exception as exc:  # noqa: BLE001 — wrap as loud CentralRbacReadError
            raise CentralRbacReadError(f'central RBAC read query failed: {exc}') from exc
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()
        return [dict(zip(columns, row)) for row in rows]


def _is_enabled(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {'0', 'false', 'f', 'no', 'off'}
