"""Platform RBAC read service (FE-P8, 2026-05-28).

``CentralRbacReadService`` turns raw ``project_member_permissions`` view rows
into the two shapes the platform driving adapter needs:

1. :meth:`effective_permissions` — ``frozenset[str]`` of permission keys for a
   given (project_id, user_subject), with expired rows filtered against an
   injected clock (deterministic tests; no DB-side ``now()``). This is the
   primary authz integration point — ``PlatformApiAdapter.authorize`` unions
   this set with the principal's token permissions to decide allow/deny.

2. :meth:`list_memberships` — keyset-paginated ``MembershipEnvelope`` page for
   the FE-P2 RBAC roster UI. Same opt-in pagination contract as the coverage
   / claims reads (``limit=None`` → unbounded array; ``cursor`` → resume).

The service also offers :meth:`resolve_user_id` (subject → uuid) used by the
membership write service to translate API inputs into ``project_membership.user_id``
FKs. Resolution is a single SELECT — the membership write path joins both
queries in its single transaction via the shared connection factory.

``project_id`` is canonicalized to a 36-char uuid at the boundary (rejecting
"uuid-ish" strings) so a malformed id surfaces as 400 rather than reaching
PostgreSQL as ``invalid input syntax for type uuid``.

dependency-free of infrastructure / FastAPI / SQL — only the domain port +
stdlib ``datetime`` / ``uuid``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from fcc_test_contracts.common.identity import canonical_issuer
from fcc_test_platform.application.central_rbac_read_adapter import (
    MEMBERSHIP_KEYSET,
    MEMBERSHIP_KEYSET_DOMAINS,
)
from fcc_test_kernel.application.central_contract.envelope_helpers import (
    optional_text,
    parse_timestamp,
    require_uuid,
    text,
)
from fcc_test_kernel.application.central_contract.pagination import (
    CursorError,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from fcc_test_kernel.domain.ports.output.central_rbac_read_port import CentralRbacReadPort


__all__ = ['CentralRbacReadService']


def _text_keyset(values: tuple[Optional[str], ...]) -> tuple[str, ...]:
    """`decode_cursor` 의 «넓은» 반환을 이 키셋이 아는 만큼 좁힌다.

    `decode_cursor` 는 `tuple[str | None, ...]` 를 돌려준다. 그 넓이는 이 키셋
    때문이 아니라 `NULLABLE_TIMESTAMP` 도메인을 쓰는 «다른» 키셋 때문이고, 반환
    타입은 `domains` **인자**에 의존할 수 없다 — 그러므로 그 선언은 거짓말이 아니라
    필요한 상한이다.

    이 자리의 사실은 다르다. 실측 2026-09-06 (`kernel-v0.5.1`):
    `MEMBERSHIP_KEYSET_DOMAINS` 는 세 열 모두 `TEXT` 이고, 커널의 도메인 검사가
    TEXT 열의 JSON `null` 을 «이미» 거절한다 —
    `CursorError: null is not allowed for this key`. 즉 여기서 `None` 은 올 수 없다.
    그것은 **적히지 않은 앎**이었고, 이 함수가 그 앎에 이름을 붙인다.

    ⚠️ `cast` 로 적지 않는 이유: 도메인이 언젠가 `NULLABLE_TIMESTAMP` 로 바뀌면
    `cast` 는 **거짓말이 되고 조용하다.** 아래 가드는 그날 400 으로 말한다.

    ⚠️ 그 침묵이 왜 비싼지는 하류에 있다. 어댑터가
    `(project_id, *after, limit)` 로 값을 SQL 에 펼치므로(`central_rbac_read_adapter`)
    `None` 하나는 예외가 아니라 **빈 페이지**가 된다 — NULL 과의 `>` 비교는 참이
    되지 않는다. 즉 실패 모드가 「터진다」가 아니라 **「조용히 틀린 답」**이고,
    포트가 `after: Optional[Sequence[str]]` 로 좁게 선언한 이유가 그것이다.
    """
    if any(value is None for value in values):
        raise CursorError('invalid pagination cursor: null is not allowed for this key')
    return tuple(value for value in values if value is not None)


class CentralRbacReadService:
    def __init__(
        self,
        read_port: CentralRbacReadPort,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._read = read_port
        # Injectable for deterministic expiry tests; defaults to wall-clock UTC.
        # The expiry boundary is enforced HERE (service) rather than in SQL so
        # tests can freeze "now" without touching the DB.
        self._clock = clock or _utcnow

    def effective_permissions(
        self,
        project_id: str,
        user_subject: str,
        *,
        user_issuer: Optional[str] = None,
    ) -> frozenset[str]:
        """Return the set of platform permissions granted by membership.

        Empty set ⇒ no membership / all expired. The caller (authz) unions
        this with the principal's token permissions, so an empty set is
        the (correct) zero-element for the UNION (backward-compatible with
        the token-only path).
        """
        pid = require_uuid(project_id, 'project_id')
        subject = (user_subject or '').strip()
        issuer_text = _required_issuer(user_issuer)
        if not subject or issuer_text is None:
            return frozenset()
        issuer = canonical_issuer(issuer_text)
        now = self._clock()
        rows = self._read.read_member_permissions(pid, issuer, subject)
        return frozenset(
            text(row.get('permission_key'))
            for row in rows
            if _is_enabled(row.get('user_enabled'))
            and not _is_expired(row.get('expires_at'), now)
            and text(row.get('permission_key'))
        )

    def list_memberships(
        self, project_id: str, *,
        limit: Optional[int] = None, cursor: Optional[str] = None,
    ) -> dict:
        """Membership roster page (FE-P2 admin UI source).

        Returns ``{'items': [MembershipEnvelope, ...], 'next_cursor': str | None}``.
        Same opt-in keyset pagination contract as ``project_coverage`` /
        ``project_claims``.
        """
        pid = require_uuid(project_id, 'project_id')
        if limit is None and not cursor:
            rows = self._read.read_project_memberships(pid, limit=None)
            return {'items': [_membership_envelope(row) for row in rows], 'next_cursor': None}
        size = clamp_limit(limit)
        after = (
            _text_keyset(decode_cursor(
                cursor, arity=len(MEMBERSHIP_KEYSET), domains=MEMBERSHIP_KEYSET_DOMAINS,
            ))
            if cursor else None
        )
        rows = self._read.read_project_memberships(pid, limit=size + 1, after=after)
        has_more = len(rows) > size
        items = [_membership_envelope(row) for row in rows[:size]]
        next_cursor = None
        if has_more and items:
            next_cursor = encode_cursor([items[-1][column] for column in MEMBERSHIP_KEYSET])
        return {'items': items, 'next_cursor': next_cursor}

    def resolve_user_id(
        self,
        user_subject: str,
        *,
        user_issuer: Optional[str] = None,
    ) -> Optional[str]:
        """Resolve a subject to a central users.id uuid (or None when unknown)."""
        subject = (user_subject or '').strip()
        issuer_text = _required_issuer(user_issuer)
        if not subject or issuer_text is None:
            return None
        issuer = canonical_issuer(issuer_text)
        return self._read.resolve_user_id(issuer, subject)

    def user_enabled(
        self,
        user_subject: str,
        *,
        user_issuer: Optional[str] = None,
    ) -> Optional[bool]:
        """Return central enabled state for a principal, or None when unknown."""
        subject = (user_subject or '').strip()
        issuer_text = _required_issuer(user_issuer)
        if not subject or issuer_text is None:
            return None
        reader = getattr(self._read, 'read_user_enabled', None)
        if not callable(reader):
            return None
        issuer = canonical_issuer(issuer_text)
        return reader(issuer, subject)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(value: object, now: datetime) -> bool:
    """True when ``value`` parses to a timestamp strictly older than ``now``.

    ``None`` / empty / unparseable → not expired (NULL means "no expiry" — the
    membership stays valid; a malformed timestamp is preserved as valid so a
    bad row in central never silently denies a legitimately-authorized user).
    That "unparseable ⇒ valid" fallback is this function's own policy; *what
    counts as parseable* is the shared :func:`parse_timestamp` SSOT (this module
    used to carry its own copy of the tolerant parse loop).
    """
    parsed = parse_timestamp(value)
    if parsed is None:
        return False
    return parsed <= now


def _is_enabled(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {'0', 'false', 'f', 'no', 'off'}


def _required_issuer(value: object) -> Optional[str]:
    cleaned = '' if value is None else str(value).strip()
    return cleaned or None


def _membership_envelope(row: dict) -> dict:
    return {
        'project_id': text(row.get('project_id')),
        'user_issuer': text(row.get('user_issuer')),
        'user_subject': text(row.get('user_subject')),
        'role_key': text(row.get('role_key')),
        'assigned_at': text(row.get('assigned_at')),
        'expires_at': optional_text(row.get('expires_at')),
        'team': optional_text(row.get('team')),
    }
