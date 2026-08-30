"""Central PostgreSQL reads/writes for LOCAL (password) identities.

``central_user_write_adapter.py`` owns the OIDC JIT upsert and is **not touched**
by this module — that path stays byte-identical (계약 M-2). This one owns the
queries local login needs: find-by-email, the lockout counters, the password
write, and the global permission resolution.

⚠️ **Every query here is scoped to the local issuer.** ``users`` holds OIDC rows
and local rows in the same table, and an unscoped lookup by email would let a
password check target a row that has no password — or, worse, let a local login
resolve onto an OIDC identity and inherit its grants. The scoping is not an
optimisation; it is the boundary.
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional, Sequence

from fcc_test_contracts.common.identity import LOCAL_IDENTITY_ISSUER, local_identity_key
from domain.ports.output.platform_database_port import DbConnection
from fcc_test_contracts.common.login_throttle_policy import (
    LOCKOUT_DURATION_MINUTES,
    LOCKOUT_MAX_ATTEMPTS,
    LOCKOUT_WINDOW_MINUTES,
)


__all__ = [
    'ACCOUNT_UNLOCKED_EVENT_TYPE',
    'FIND_LOCAL_SUBJECT_SQL',
    'LOCAL_USER_COLUMNS',
    'UNLOCK_ACCOUNT_SQL',
    'LocalUserStoreError',
    'PostgresLocalUserStore',
]


class LocalUserStoreError(RuntimeError):
    """Central users read/write failed. Mapped to 503 at the route boundary.

    ⚠️ **이 예외의 message 는 클라이언트에 그대로 나간다** — 플랫폼 경계가
    ``problem+json`` 의 ``detail`` 로 렌더한다. 그래서 드라이버 예외 텍스트를 여기에
    끼워 넣지 않는다: 그것은 스키마 조각·DSN·서버 버전을 로그인 표면에서 읽을 수 있는
    일반 에코 채널로 만든다(적대 평가 2026-08-22 실측 — NUL 이 담긴 이메일 하나로
    ``PostgreSQL text fields cannot contain NUL (0x00) bytes`` 가 응답에 실렸다).
    원인은 ``raise ... from exc`` 로 서버 트레이스백에 그대로 남으므로 진단은 잃지 않는다.
    """


#: The projection every local-login decision is made from. Ordered, and the SQL
#: is BUILT from this tuple — a hand-written SELECT list is how a column gets
#: added to one and not the other, and ``dict(zip(...))`` then silently shifts
#: every value one column to the left.
LOCAL_USER_COLUMNS: tuple[str, ...] = (
    'id',
    'subject',
    'display_name',
    'email',
    'enabled',
    'password_hash',
    'force_password_change',
    'password_changed_at',
    'session_version',
    'failed_login_attempts',
    'locked_until',
)


def _select_list(columns: Sequence[str], alias: str = '') -> str:
    prefix = f'{alias}.' if alias else ''
    return ', '.join(f'{prefix}"{column}"' for column in columns)


FIND_LOCAL_USER_SQL = (
    f'SELECT {_select_list(LOCAL_USER_COLUMNS)} FROM "users" '
    'WHERE "issuer" = %s AND "subject" = %s'
)

#: ⚠️⚠️ **The two CASE expressions are duplicated on purpose. Do not "tidy" this.**
#:
#: The obvious refactor is to compute the folded count once in a ``FROM (SELECT
#: ... FROM users WHERE id = %s) folded`` subquery and reference it twice. That
#: version is WRONG under concurrency, and wrong in the exact way that matters:
#:
#: PostgreSQL READ COMMITTED re-evaluates an ``UPDATE``'s target row after
#: blocking on a concurrent writer's row lock (EvalPlanQual), so a CASE written
#: inline in ``SET`` sees the OTHER transaction's committed value. A scalar
#: pulled from a ``FROM`` subquery does NOT get re-evaluated — it keeps the
#: original snapshot. So two simultaneous failed logins would both read
#: ``failed_login_attempts = 0`` and both write ``1``, and an attacker running
#: requests in parallel would never reach the lockout threshold at all.
#:
#: This is also why the fold is one statement rather than SELECT-then-UPDATE in
#: Python: read-modify-write opens the same window, only wider.
#:
#: ``WHERE ... AND (locked_until IS NULL OR locked_until <= now)`` is the second
#: half of the design: an ALREADY-LOCKED row is not touched (0 rows updated), so
#: the lock cannot be pushed forward by continued hammering. Without it an
#: attacker who does not know the password can keep a victim locked out forever —
#: a denial-of-service against a legitimate user. See
#: ``login_throttle_policy.should_extend_lock``.
_FOLDED_ATTEMPTS = (
    'CASE WHEN "users"."last_failed_at" IS NULL '
    'OR "users"."last_failed_at" < %s::timestamptz - make_interval(mins => %s) '
    'THEN 1 ELSE COALESCE("users"."failed_login_attempts", 0) + 1 END'
)

RECORD_FAILED_LOGIN_SQL = (
    'UPDATE "users" SET '
    f'"failed_login_attempts" = {_FOLDED_ATTEMPTS}, '
    '"last_failed_at" = %s, '
    f'"locked_until" = CASE WHEN ({_FOLDED_ATTEMPTS}) >= %s '
    'THEN %s::timestamptz + make_interval(mins => %s) '
    'ELSE "users"."locked_until" END, '
    '"updated_at" = %s '
    'WHERE "id" = %s AND "issuer" = %s '
    'AND ("locked_until" IS NULL OR "locked_until" <= %s::timestamptz) '
    'RETURNING "failed_login_attempts", "locked_until"'
)

RECORD_SUCCESSFUL_LOGIN_SQL = (
    'UPDATE "users" SET "failed_login_attempts" = 0, "last_failed_at" = NULL, '
    '"locked_until" = NULL, "last_login" = %s, "updated_at" = %s '
    'WHERE "id" = %s AND "issuer" = %s'
)

#: ⚠️ ``session_version`` is incremented on EVERY password write. That counter is
#: what makes an already-issued token stop working — JWTs cannot be recalled, so
#: "change your password" would otherwise leave every stolen token valid for its
#: full remaining lifetime. ``COALESCE`` because the column is nullable on rows
#: that predate migration 026 (the exporter forbids NOT NULL on additive columns).
UPDATE_PASSWORD_SQL = (
    'UPDATE "users" SET "password_hash" = %s, "password_changed_at" = %s, '
    '"force_password_change" = FALSE, '
    '"session_version" = COALESCE("session_version", 0) + 1, "updated_at" = %s '
    'WHERE "id" = %s AND "issuer" = %s '
    'RETURNING COALESCE("session_version", 0)'
)

#: The audit vocabulary member this module owns (2026-08-23).
#:
#: ⚠️ Sibling event types are inline literals in their own services
#: (``membership.assigned`` in ``central_membership_write_service`` …), so this
#: constant does **not** claim to be a registry for the whole vocabulary — it is
#: the single owner of *this* value, next to the SQL that writes it. It is sealed
#: against ``central_db_schema.v1.json``'s ``allowed_values``, because a value the
#: CHECK does not know is not a weaker audit trail: the audit INSERT shares the
#: unlock's transaction, so it would roll the unlock back.
ACCOUNT_UNLOCKED_EVENT_TYPE = 'account.unlocked'

#: Lift a login lockout, atomically, and end every existing session.
#:
#: ⚠️ **``session_version`` is incremented unconditionally, and that is a judged
#: trade-off — not an oversight.** The sibling system this design is modelled on
#: deliberately leaves it alone ("unlock is not session invalidation"), and that
#: is right *there* because its lockouts come from typos. FCC's P1 scenario is
#: different: the lock is reached through the **change-password door by a holder
#: of a stolen access token**, and logout does not bump the counter, so the
#: stolen token outlives the lock. Unlocking without bumping hands the account
#: straight back to the attacker, who re-locks it — an administrator pressing
#: "unlock" forever.
#:
#: ⚠️ **The cost is real and is not hidden**: the victim's own sessions on other
#: devices end too, which is exactly why *logout* does not bump this counter. The
#: difference is the nature of the act — logout is a routine per-tab action,
#: unlock is an administrator intervening in an incident, and the account is
#: already unusable for login at that moment. A ``revoke_sessions`` flag was
#: rejected: whichever default it carried would become the real policy while
#: appearing to be the caller's choice. One behaviour, one documented consequence.
#:
#: ⚠️ **What it still does NOT do**: the attacker's *access* token keeps working
#: for its remaining lifetime because JWTs cannot be recalled. So this makes
#: re-locking **finite**, not impossible.
#:
#: ⚠️ **That window is the access-token TTL, which the deployment configures** —
#: ``DEFAULT_LOCAL_JWT_ACCESS_TTL_SECONDS`` (900 s = 15 min) is only the default,
#: and the local-JWT TTL setting raises it. An earlier draft stated «≤15 min» as
#: an absolute, which would be a security residual asserted as a constant while a
#: deployment quietly changed it (adversarial review, 2026-08-23). Recorded in
#: ADR-0021 and the ledger.
#:
#: ⚠️ **One statement, no read-modify-write.** The sibling ``RECORD_FAILED_LOGIN_SQL``
#: explains at length why a fold must not be SELECT-then-UPDATE; the same rule
#: applies here for the same reason, and it costs nothing because none of these
#: assignments depend on a value Python has to see first.
#: ⚠️ **``locked_until IS NOT NULL`` 이 없으면 이 operation 은 «아무나 강제
#: 로그아웃시키는 버튼» 이 된다** (적대 평가 실측 2026-08-23). 잠기지 않은 계정에
#: 대해서도 UPDATE 가 매칭돼 세션이 끊기고, *일어나지 않은 해제*를 주장하는 감사
#: 행이 남았다 — 이 모듈이 스스로 *"미스에는 감사 0"* 이라고 적어 둔 원칙을 어기는
#: 형태다. 술어가 붙은 뒤로는 **잠금 하나당 최대 한 번**만 발화한다(해제 직후
#: ``locked_until`` 이 NULL 이므로 이어지는 호출은 0행 = 무-op).
#:
#: ⚠️ **만료된 잠금 표식도 «잠금» 으로 읽는다.** ``> now`` 로 좁히면 만료된 표식이
#: 행에 영구히 남고, 관리자가 "해제" 를 눌렀는데 아무것도 정리되지 않는다. 대가는
#: 그 경우에도 세션이 끊기는 것이고, 그것은 관리자가 명시적으로 요청한 조치다.
UNLOCK_ACCOUNT_SQL = (
    'UPDATE "users" SET "failed_login_attempts" = 0, "last_failed_at" = NULL, '
    '"locked_until" = NULL, '
    '"session_version" = COALESCE("session_version", 0) + 1, "updated_at" = %s '
    'WHERE "issuer" = %s AND "subject" = %s AND "locked_until" IS NOT NULL '
    'RETURNING "subject", COALESCE("session_version", 0)'
)

#: 0행이 «그런 계정이 없다» 인지 «잠기지 않았다» 인지 가르는 읽기. 두 답은 관리자가
#: 할 일이 다르다 — 전자는 오타이고 후자는 이미 끝난 일이다.
FIND_LOCAL_SUBJECT_SQL = (
    'SELECT "subject" FROM "users" WHERE "issuer" = %s AND "subject" = %s'
)

#: Global (non project-scoped) permissions. The project-scoped path already
#: exists (``CentralRbacReadService.effective_permissions``) and is untouched.
#: Do not join ``role_permissions`` here: that table describes project roles,
#: while ``global_role_grants`` is the explicit boundary for operations such as
#: sample hard delete. A project_admin membership must never become a global
#: system-admin capability merely because both use ``roles`` as their catalog.
GLOBAL_PERMISSIONS_SQL = (
    'SELECT DISTINCT gr."permission_key" FROM "user_roles" ur '
    'JOIN "roles" r ON r."id" = ur."role_id" '
    'JOIN "global_role_grants" gr ON gr."role_key" = r."role_key" '
    'WHERE ur."user_id" = %s ORDER BY gr."permission_key"'
)

# Local-password bootstrap assigns the historical ``project_admin`` role in
# ``user_roles``.  That table is global membership for this identity path, but
# the role's ordinary operation grants still come from the canonical
# ``role_permissions`` graph.  Keep the dedicated global-role query above
# narrow; this union is the single materialization boundary for a local JWT.
LOCAL_PERMISSIONS_SQL = (
    'SELECT DISTINCT p."permission_key" FROM "user_roles" ur '
    'JOIN "users" u ON u."id" = ur."user_id" '
    'JOIN "roles" r ON r."id" = ur."role_id" '
    'JOIN "role_permissions" rp ON rp."role_id" = r."id" '
    'JOIN "permissions" p ON p."id" = rp."permission_id" '
    'WHERE ur."user_id" = %s AND u."issuer" = %s '
    'UNION '
    'SELECT DISTINCT gr."permission_key" FROM "user_roles" ur '
    'JOIN "users" u ON u."id" = ur."user_id" '
    'JOIN "roles" r ON r."id" = ur."role_id" '
    'JOIN "global_role_grants" gr ON gr."role_key" = r."role_key" '
    'WHERE ur."user_id" = %s AND u."issuer" = %s '
    'ORDER BY "permission_key"'
)

INSERT_LOCAL_USER_SQL = (
    'INSERT INTO "users" ("id", "issuer", "subject", "display_name", "email", '
    '"enabled", "password_hash", "force_password_change", "session_version", '
    '"failed_login_attempts", "created_at", "updated_at") '
    'VALUES (%s, %s, %s, %s, %s, TRUE, %s, %s, 0, 0, %s, %s) '
    'ON CONFLICT ("issuer", "subject") DO NOTHING '
    f'RETURNING {_select_list(LOCAL_USER_COLUMNS)}'
)

#: Grant a catalog role GLOBALLY (``user_roles`` is not project-scoped). Used only
#: by bootstrap. ``SELECT`` from ``roles`` rather than taking a role id so the caller
#: names the stable natural key and cannot pass a surrogate id from another
#: deployment. ``ON CONFLICT DO NOTHING`` makes a restart a no-op.
GRANT_ROLE_SQL = (
    'INSERT INTO "user_roles" ("user_id", "role_id") '
    'SELECT %s, r."id" FROM "roles" r WHERE r."role_key" = %s '
    'ON CONFLICT DO NOTHING'
)

COUNT_LOCAL_USERS_SQL = (
    'SELECT COUNT(*) FROM "users" WHERE "issuer" = %s AND "password_hash" IS NOT NULL'
)


class PostgresLocalUserStore:
    """``users`` access for the local-password identity path."""

    def __init__(
        self,
        connection_factory: Callable[[], DbConnection],
        *,
        audit_writer: object = None,
    ) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory
        # ⚠️ **Optional here, required by the one method that audits.** Making it a
        # required constructor argument would force every existing caller (login,
        # refresh, bootstrap) to supply a writer for a path they never take. Making
        # it silently optional *at the audit site* would be worse — the sibling
        # membership adapter refuses construction for exactly that reason. So the
        # rule is enforced where it means something: :meth:`unlock_account` raises
        # rather than performing an unaudited unlock.
        self._audit_writer = audit_writer

    # ── reads ────────────────────────────────────────────────────────────────

    def find_by_email(self, email: object) -> Optional[dict]:
        """The local row for ``email``, or ``None``.

        ⚠️ ``None`` covers "no such local user" AND "that email is not a usable
        identity key at all" (empty, over-long). The caller must treat both the
        same way it treats a wrong password — see ``local_auth_service``.
        """
        try:
            issuer, subject = local_identity_key(email)
        except ValueError:
            return None
        rows = self._query(FIND_LOCAL_USER_SQL, (issuer, subject))
        if not rows:
            return None
        return dict(zip(LOCAL_USER_COLUMNS, rows[0]))

    def global_permissions(self, user_id: object) -> tuple[str, ...]:
        rows = self._query(GLOBAL_PERMISSIONS_SQL, (user_id,))
        return tuple(str(row[0]) for row in rows if row and row[0])

    def local_permissions(self, user_id: object) -> tuple[str, ...]:
        """Resolve the complete permission set carried by a local JWT.

        Project-role grants and global-role grants are separate SSOT axes;
        local bootstrap needs their union, while callers that specifically
        need global-only capability checks continue using ``global_permissions``.
        """
        rows = self._query(
            LOCAL_PERMISSIONS_SQL,
            (user_id, LOCAL_IDENTITY_ISSUER, user_id, LOCAL_IDENTITY_ISSUER),
        )
        return tuple(str(row[0]) for row in rows if row and row[0])

    def count_local_users(self) -> int:
        rows = self._query(COUNT_LOCAL_USERS_SQL, (LOCAL_IDENTITY_ISSUER,))
        return int(rows[0][0]) if rows else 0

    # ── writes ───────────────────────────────────────────────────────────────

    def record_failed_login(self, user_id: object, *, now) -> dict:
        """Fold one failure into the counters, atomically. See the SQL comment.

        Returns the post-update counters, or ``{}`` when the row was already
        locked (the ``WHERE`` guard matched nothing) — an empty result is a
        normal outcome here, not an error.
        """
        params = (
            now, LOCKOUT_WINDOW_MINUTES,             # _FOLDED_ATTEMPTS (SET)
            now,                                      # last_failed_at
            now, LOCKOUT_WINDOW_MINUTES,             # _FOLDED_ATTEMPTS (locked_until)
            LOCKOUT_MAX_ATTEMPTS,
            now, LOCKOUT_DURATION_MINUTES,
            now,                                      # updated_at
            user_id, LOCAL_IDENTITY_ISSUER,
            now,                                      # WHERE locked_until <= now
        )

        def _txn(cursor) -> dict:
            cursor.execute(RECORD_FAILED_LOGIN_SQL, params)
            rows = list(cursor.fetchall() or ())
            if not rows:
                return {}
            return {
                'failed_login_attempts': rows[0][0],
                'locked_until': rows[0][1],
            }

        return self._in_transaction(_txn)

    def record_successful_login(self, user_id: object, *, now) -> None:
        def _txn(cursor) -> None:
            cursor.execute(
                RECORD_SUCCESSFUL_LOGIN_SQL,
                (now, now, user_id, LOCAL_IDENTITY_ISSUER),
            )

        self._in_transaction(_txn)

    def update_password(self, user_id: object, *, password_hash: str, now) -> int:
        """Store a new hash and bump ``session_version``. Returns the new version."""
        def _txn(cursor) -> int:
            cursor.execute(
                UPDATE_PASSWORD_SQL,
                (password_hash, now, now, user_id, LOCAL_IDENTITY_ISSUER),
            )
            rows = list(cursor.fetchall() or ())
            if not rows:
                raise LocalUserStoreError(
                    'password update matched no local user row'
                )
            return int(rows[0][0] or 0)

        return self._in_transaction(_txn)

    def unlock_account(self, subject: object, *, now, audit_record: Mapping) -> dict:
        """Lift the lockout for ``subject`` and audit it in the **same transaction**.

        Returns ``{'subject': …, 'session_version': int, 'was_locked': True}``
        when a lock was lifted, ``{'subject': …, 'session_version': None,
        'was_locked': False}`` when the account exists but held no lock, and
        ``{}`` when no local row matched — the empty result is how the caller
        answers 404, and it is the reason the audit INSERT is *inside* the same
        body: a miss must not leave an audit row claiming an unlock that never
        happened.

        ⚠️ **The no-op case writes no audit row and bumps no session counter.**
        Without that, ``platform:admin`` holds an unbounded force-logout primitive
        whose ledger entries all read "administrative unlock", so the audit trail
        cannot tell an incident response from harassment (adversarial review,
        2026-08-23).

        ⚠️ **The UPDATE binds the target by identity key**, so no other user's row
        is touched — the failure this guards against is an unlock that quietly
        clears the wrong account's counters.

        ⚠️ **An unaudited unlock is refused, loudly.** ``audit_events`` states that
        a platform write is durable iff its audit is; a path that silently skips
        the audit would break that sentence for the one operation whose entire
        purpose is administrative intervention.
        """
        if self._audit_writer is None:
            raise LocalUserStoreError(
                'unlock_account requires an audit_writer — an unaudited '
                'administrative unlock is forbidden (audit atomicity)'
            )
        issuer, target = local_identity_key(subject)
        record = dict(audit_record or {})
        record['event_type'] = ACCOUNT_UNLOCKED_EVENT_TYPE
        record['target_user_subject'] = target

        def _txn(cursor) -> dict:
            cursor.execute(UNLOCK_ACCOUNT_SQL, (now, issuer, target))
            rows = list(cursor.fetchall() or ())
            if not rows:
                # 잠기지 않았거나 없는 계정. 같은 트랜잭션에서 어느 쪽인지 가른다 —
                # 두 답은 관리자가 할 일이 다르고, «없다» 를 «이미 풀렸다» 로 보여
                # 주면 오타 한 글자가 «해제됐다» 로 읽힌다.
                cursor.execute(FIND_LOCAL_SUBJECT_SQL, (issuer, target))
                existing = list(cursor.fetchall() or ())
                if not existing:
                    return {}
                return {
                    'subject': existing[0][0],
                    'session_version': None,
                    'was_locked': False,
                }
            self._audit_writer.append_event_in_transaction(cursor, record)
            return {
                'subject': rows[0][0],
                'session_version': int(rows[0][1] or 0),
                'was_locked': True,
            }

        return self._in_transaction(_txn)

    def grant_role(self, user_id: object, role_key: str) -> None:
        def _txn(cursor) -> None:
            cursor.execute(GRANT_ROLE_SQL, (user_id, role_key))

        self._in_transaction(_txn)

    def grant_global_role(self, user_id: object, role_key: str) -> None:
        """Assign a role whose permissions are resolved only via global grants."""
        self.grant_role(user_id, role_key)

    def create_local_user(
        self,
        *,
        user_id: str,
        email: object,
        display_name: str,
        password_hash: str,
        force_password_change: bool,
        now,
    ) -> Optional[dict]:
        """Insert a local user, or ``None`` when one already exists.

        ``ON CONFLICT DO NOTHING`` rather than an upsert: this is only reached by
        bootstrap, and an upsert would let a restart with a changed environment
        variable silently RESET an existing administrator's password. A bootstrap
        path that can overwrite a live credential is a backdoor.
        """
        issuer, subject = local_identity_key(email)

        def _txn(cursor) -> Optional[dict]:
            cursor.execute(
                INSERT_LOCAL_USER_SQL,
                (
                    user_id, issuer, subject, display_name, subject,
                    password_hash, bool(force_password_change), now, now,
                ),
            )
            rows = list(cursor.fetchall() or ())
            if not rows:
                return None
            return dict(zip(LOCAL_USER_COLUMNS, rows[0]))

        return self._in_transaction(_txn)

    # ── plumbing (shape mirrors central_chamber_read/write adapters) ─────────

    def _query(self, statement: str, params: tuple) -> list:
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001 — loud, never a silent empty list
            raise LocalUserStoreError(
                'central users read connection failed'
            ) from exc
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(statement, params)
                return list(cursor.fetchall() or ())
            finally:
                cursor.close()
        except LocalUserStoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise LocalUserStoreError('central users read failed') from exc
        finally:
            _close(connection)

    def _in_transaction(self, body: Callable[[object], object]):
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001
            raise LocalUserStoreError(
                'central users write connection failed'
            ) from exc
        try:
            cursor = connection.cursor()
            try:
                result = body(cursor)
            finally:
                cursor.close()
            connection.commit()
            return result
        except LocalUserStoreError:
            _safe_rollback(connection)
            raise
        except Exception as exc:  # noqa: BLE001
            _safe_rollback(connection)
            raise LocalUserStoreError('central users write failed') from exc
        finally:
            _close(connection)


def _close(connection) -> None:
    close = getattr(connection, 'close', None)
    if callable(close):
        close()


def _safe_rollback(connection) -> None:
    rollback = getattr(connection, 'rollback', None)
    if callable(rollback):
        try:
            rollback()
        except Exception:  # noqa: BLE001
            pass
