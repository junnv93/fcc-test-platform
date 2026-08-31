"""Frontend multi-tab auth robustness seal (fe-multitab-auth-robustness, Inc 6).

`apps/web` originally had silent refresh (fire 30s before `exp`) but no
cross-tab logout propagation and no 401 recovery: `sessionStorage` is per-tab,
so a sign-out in one tab left siblings authenticated, and an access_token that
expired mid-flight (tab backgrounded past the refresh timer) failed the request
outright. Increment 6 adds:

  1. ``auth/multi-tab-sync.ts`` — a ``BroadcastChannel`` (SSOT channel name)
     that propagates a user-initiated sign-out to sibling tabs.
  2. ``api/auth-middleware.ts`` — a shared openapi-fetch middleware that, on a
     401, performs a single silent refresh + replay (loop-guarded), or signs
     out with ``token_expired``. It is the ONE SSOT the three typed clients
     adopt (the previously triplicated inline ``authorizationMiddleware``).

This Python invariant seals the wiring from the backend-only CI lane so a
refactor that drops the broadcast, the 401 interceptor, the loop guard, or the
single-middleware SSOT fails CI even when ``npm run test``/``typecheck`` is
skipped. It is complementary to the ``apps/web`` vitest suites
(``tests/auth/multi-tab-sync.test.ts`` + ``tests/api/auth-middleware.test.ts``).

Scans are docstring-aware: ``/* … */`` and ``//`` comments are stripped before
the literal scan so a comment that merely mentions a token is not a false
positive. Companion skill: ``/verify-frontend-auth-robustness``.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from support.parity import strip_ts_comments


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_SRC = PROJECT_ROOT / "apps" / "web" / "src"
AUTH_DIR = WEB_SRC / "auth"
API_DIR = WEB_SRC / "api"

MULTI_TAB = AUTH_DIR / "multi-tab-sync.ts"
AUTH_MIDDLEWARE = API_DIR / "auth-middleware.ts"
SESSION = AUTH_DIR / "session.ts"
ROUTE_GUARD = AUTH_DIR / "route-guard.tsx"
MAIN = WEB_SRC / "main.tsx"

CLIENTS = ("session-client.ts", "headless-client.ts", "platform-client.ts")

CHANNEL_NAME = "fcc-oidc:auth-sync"


def _strip_ts_comments(src: str) -> str:
    """Delegate to the shared lexer — see
    ``tests/test_ts_comment_stripper_ssot.py``."""
    return strip_ts_comments(src)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_stripped(path: Path) -> str:
    return _strip_ts_comments(_read(path))


class TestMultiTabSyncModule(unittest.TestCase):
    """The cross-tab logout sync module exists with the right surface + SSOT."""

    def test_module_present(self) -> None:
        self.assertTrue(MULTI_TAB.is_file(), f"missing multi-tab sync SSOT: {MULTI_TAB}")

    def test_channel_name_is_ssot_constant(self) -> None:
        src = _read(MULTI_TAB)
        self.assertRegex(
            src,
            r"export const AUTH_BROADCAST_CHANNEL\s*=\s*'fcc-oidc:auth-sync'",
            "multi-tab-sync.ts must define AUTH_BROADCAST_CHANNEL = "
            "'fcc-oidc:auth-sync' as the single source for the channel name",
        )

    def test_channel_name_literal_not_scattered(self) -> None:
        # The raw channel-name literal must appear exactly once (the SSOT const).
        offenders: list[str] = []
        for ts in sorted(WEB_SRC.rglob("*.ts")) + sorted(WEB_SRC.rglob("*.tsx")):
            if ts.parent.name == "generated":
                continue
            count = _strip_ts_comments(_read(ts)).count(f"'{CHANNEL_NAME}'")
            if count and ts != MULTI_TAB:
                offenders.append(f"{ts.relative_to(WEB_SRC).as_posix()} ({count})")
            if ts == MULTI_TAB and count != 1:
                offenders.append(f"{ts.name} ({count}, expected 1)")
        self.assertEqual(
            offenders,
            [],
            "the '%s' channel-name literal must appear once (the "
            "AUTH_BROADCAST_CHANNEL SSOT) — offenders: %s" % (CHANNEL_NAME, offenders),
        )

    def test_uses_broadcast_channel(self) -> None:
        src = _read_stripped(MULTI_TAB)
        self.assertIn(
            "new BroadcastChannel(",
            src,
            "multi-tab-sync.ts must construct a BroadcastChannel",
        )

    def test_exports_broadcast_and_init(self) -> None:
        src = _read_stripped(MULTI_TAB)
        self.assertRegex(
            src,
            r"export function signOutEverywhere\b",
            "multi-tab-sync.ts must export signOutEverywhere (broadcast + local)",
        )
        self.assertRegex(
            src,
            r"export function initMultiTabAuthSync\b",
            "multi-tab-sync.ts must export initMultiTabAuthSync (receive wiring)",
        )

    def test_receive_handler_uses_plain_signout_echo_guard(self) -> None:
        # The receive side must call the plain `signOut` (not signOutEverywhere)
        # so applying a remote sign-out does not re-broadcast (echo guard).
        src = _read_stripped(MULTI_TAB)
        self.assertRegex(
            src,
            r"import\s*\{[^}]*\bsignOut\b[^}]*\}\s*from\s*'\./session'",
            "multi-tab-sync.ts must import the plain signOut from ./session for "
            "the echo-safe receive path",
        )

    def test_cross_tab_reason_is_user_initiated_only_ssot(self) -> None:
        # The iter-2 decision (contract): only `user_initiated` may cross tabs.
        # That decision must be enforced at the type/API boundary, not just by
        # convention — an SSOT const + a narrowed `CrossTabSignOutReason` type
        # whose only member is 'user_initiated', and the broadcast API typed to
        # that narrowed reason so a caller cannot pass token_expired.
        src = _read_stripped(MULTI_TAB)
        self.assertRegex(
            src,
            r"export const CROSS_TAB_SIGN_OUT_REASON\s*=\s*'user_initiated'\s*satisfies\s*SignOutReason",
            "multi-tab-sync.ts must define CROSS_TAB_SIGN_OUT_REASON = "
            "'user_initiated' satisfies SignOutReason as the SSOT cross-tab reason",
        )
        self.assertRegex(
            src,
            r"export type CrossTabSignOutReason\s*=\s*typeof CROSS_TAB_SIGN_OUT_REASON",
            "multi-tab-sync.ts must derive CrossTabSignOutReason from the "
            "CROSS_TAB_SIGN_OUT_REASON const (single source) — narrowing to the "
            "'user_initiated' literal (no token_expired / refresh_failed)",
        )
        self.assertRegex(
            src,
            r"export function signOutEverywhere\(\s*reason\s*:\s*CrossTabSignOutReason",
            "signOutEverywhere must type its reason as CrossTabSignOutReason so "
            "the public broadcast API cannot widen to token_expired",
        )

    def test_receive_validation_rejects_non_user_reason(self) -> None:
        # Defense in depth: the receive-side type guard must validate the reason
        # equals the SSOT cross-tab reason, so a remote token_expired /
        # refresh_failed broadcast (buggy/older tab or hand-crafted post) is
        # rejected and cannot sign out this tab.
        src = _read_stripped(MULTI_TAB)
        self.assertRegex(
            src,
            r"\.reason\s*===\s*CROSS_TAB_SIGN_OUT_REASON",
            "the multi-tab-sync receive guard (isSignOutMessage) must validate "
            "the message reason === CROSS_TAB_SIGN_OUT_REASON so non-user reasons "
            "are not honored cross-tab",
        )
        # The on-wire message type must carry the narrowed reason, not the wide
        # SignOutReason — a re-widening is the regression this seals.
        self.assertRegex(
            src,
            r"interface AuthSyncSignOutMessage\b[^}]*\breason\s*:\s*CrossTabSignOutReason",
            "AuthSyncSignOutMessage.reason must be the narrowed "
            "CrossTabSignOutReason (not the wide SignOutReason)",
        )


class TestAuthRetryMiddleware(unittest.TestCase):
    """The shared 401 retry-once interceptor exists with the right surface."""

    def test_module_present(self) -> None:
        self.assertTrue(
            AUTH_MIDDLEWARE.is_file(), f"missing auth middleware SSOT: {AUTH_MIDDLEWARE}"
        )

    def test_exports_middleware(self) -> None:
        src = _read_stripped(AUTH_MIDDLEWARE)
        self.assertRegex(
            src,
            r"export const authRetryMiddleware\b",
            "auth-middleware.ts must export authRetryMiddleware",
        )

    def test_implements_request_and_response(self) -> None:
        src = _read_stripped(AUTH_MIDDLEWARE)
        for member in ("onRequest", "onResponse"):
            self.assertIn(
                member, src, f"auth-middleware.ts must implement `{member}`"
            )

    def test_handles_401(self) -> None:
        src = _read_stripped(AUTH_MIDDLEWARE)
        self.assertIn(
            "401",
            src,
            "auth-middleware.ts must branch on a 401 status",
        )

    def test_refreshes_and_signs_out(self) -> None:
        src = _read_stripped(AUTH_MIDDLEWARE)
        self.assertIn(
            "refreshAuthSession",
            src,
            "auth-middleware.ts must call refreshAuthSession on a 401",
        )
        self.assertRegex(
            src,
            r"signOut\(\s*'token_expired'\s*\)",
            "auth-middleware.ts must signOut('token_expired') when refresh fails",
        )

    def test_loop_guard_present(self) -> None:
        # A retry flag (header) + a guarded clone path are the loop guard: a
        # replay must never itself be retried.
        src = _read_stripped(AUTH_MIDDLEWARE)
        self.assertIn(
            "X-Auth-Retry",
            src,
            "auth-middleware.ts must carry a retry-guard header on the replay",
        )
        self.assertIn(
            "clone",
            src,
            "auth-middleware.ts must replay via a pristine request clone",
        )

    def test_401_token_expired_does_not_broadcast_cross_tab(self) -> None:
        # Supervisor iter-2 decision (documented in the contract + evaluation
        # rationale): a 401 refresh-failure signs out the CURRENT tab only —
        # it MUST NOT broadcast `token_expired` cross-tab via signOutEverywhere.
        #
        # Why per-tab, not global: sessionStorage is per-tab, so each tab runs
        # its own silent-refresh / 401 lifecycle independently. A transient
        # network blip that 401s + fails refresh in a backgrounded tab must not
        # forcibly terminate an ACTIVE tab mid-operation (data-loss risk for the
        # long-running FCC test sessions). Only a deliberate USER-initiated
        # logout warrants a global broadcast. This seals that exclusion so a
        # refactor cannot silently widen the 401 path to a cross-tab broadcast.
        src = _read_stripped(AUTH_MIDDLEWARE)
        self.assertNotIn(
            "signOutEverywhere",
            src,
            "auth-middleware.ts must use the plain per-tab signOut('token_expired') "
            "on a failed 401 refresh — NOT the cross-tab signOutEverywhere "
            "(token expiry is a per-tab lifecycle event; only user-initiated "
            "logout broadcasts)",
        )


class TestSessionOnDemandRefresh(unittest.TestCase):
    """session.ts exposes an on-demand refresh for the 401 interceptor."""

    def test_exports_refresh_auth_session(self) -> None:
        src = _read_stripped(SESSION)
        self.assertRegex(
            src,
            r"export async function refreshAuthSession\b",
            "session.ts must export refreshAuthSession for the 401 interceptor",
        )

    def test_single_refresh_core(self) -> None:
        # Both the scheduled silent refresh and the on-demand path must share one
        # refresh core (attemptRefresh) so they cannot drift.
        src = _read_stripped(SESSION)
        self.assertIn(
            "attemptRefresh",
            src,
            "session.ts must funnel both refresh paths through a shared "
            "attemptRefresh core (no duplicated refresh logic)",
        )


class TestClientsAdoptSharedMiddleware(unittest.TestCase):
    """The three typed clients adopt the ONE middleware SSOT — no inline copies."""

    def test_clients_import_and_use_shared_middleware(self) -> None:
        missing: list[str] = []
        for name in CLIENTS:
            src = _read_stripped(API_DIR / name)
            imports = "authRetryMiddleware" in src and "from './auth-middleware'" in src
            uses = ".use(authRetryMiddleware)" in src
            if not (imports and uses):
                missing.append(f"{name} (import={imports}, use={uses})")
        self.assertEqual(
            missing,
            [],
            "every typed client must import AND .use(authRetryMiddleware): "
            f"{missing}",
        )

    def test_no_inline_authorization_middleware(self) -> None:
        # The previously triplicated inline `authorizationMiddleware` must be
        # gone — a re-introduction is the SSOT-drift regression this seals.
        offenders: list[str] = []
        for name in CLIENTS:
            src = _read_stripped(API_DIR / name)
            if re.search(r"const\s+authorizationMiddleware\b", src):
                offenders.append(name)
        self.assertEqual(
            offenders,
            [],
            "inline authorizationMiddleware re-introduced (use the shared "
            f"authRetryMiddleware SSOT instead): {offenders}",
        )


class TestWiring(unittest.TestCase):
    """Sign-out path broadcasts; app boot subscribes."""

    def test_sign_out_button_broadcasts(self) -> None:
        src = _read_stripped(ROUTE_GUARD)
        self.assertIn(
            "signOutEverywhere",
            src,
            "route-guard.tsx SignOutButton must call signOutEverywhere so a "
            "user sign-out propagates to sibling tabs",
        )
        # The bare signOut('user_initiated') call must be gone from the button
        # path (replaced by signOutEverywhere).
        self.assertNotRegex(
            src,
            r"\bsignOut\(\s*'user_initiated'\s*\)",
            "route-guard.tsx must not call the non-broadcasting signOut for the "
            "user sign-out path — use signOutEverywhere",
        )

    def test_sign_out_button_broadcasts_unconditionally(self) -> None:
        # Supervisor iter-2 P0 seal — the broadcast must fire on EVERY sign-out
        # path, including a successful RP-Initiated Logout redirect
        # (`redirected=true`). The regression was a broadcast gated inside the
        # `signOutAtIdp(...).then((redirected) => { if (!redirected) {...} })`
        # callback, so the redirect-success path sent no BroadcastChannel
        # message and sibling tabs stayed authenticated. This test would PASS
        # under that bug (signOutEverywhere still textually present), so it must
        # additionally pin the UNCONDITIONAL ordering — broadcast before the IdP
        # call and not gated on the redirect outcome.
        src = _read_stripped(ROUTE_GUARD)
        broadcast_idx = src.find("signOutEverywhere('user_initiated')")
        idp_idx = src.find("signOutAtIdp(")
        self.assertNotEqual(
            broadcast_idx, -1, "route-guard.tsx must broadcast via signOutEverywhere('user_initiated')"
        )
        self.assertNotEqual(idp_idx, -1, "route-guard.tsx SignOutButton must still call signOutAtIdp")
        self.assertLess(
            broadcast_idx,
            idp_idx,
            "the broadcast must run BEFORE signOutAtIdp so a successful IdP "
            "logout redirect (redirected=true) still notifies sibling tabs — "
            "do not gate it on the signOutAtIdp result",
        )
        # The broadcast must not be re-gated on the `redirected` return value
        # (the exact shape of the regression).
        self.assertNotRegex(
            src,
            r"if\s*\(\s*!\s*redirected\s*\)",
            "the sign-out broadcast must not be conditioned on `!redirected` — "
            "broadcast unconditionally so the IdP-redirect-success path syncs tabs",
        )
        # Exactly one broadcast call — not one per .then/.catch branch, which is
        # how the conditional regression looked.
        self.assertEqual(
            src.count("signOutEverywhere('user_initiated')"),
            1,
            "expected exactly one unconditional signOutEverywhere('user_initiated') "
            "call (the conditional regression duplicated it across branches)",
        )

    def test_main_initializes_multi_tab_sync(self) -> None:
        src = _read_stripped(MAIN)
        self.assertIn(
            "initMultiTabAuthSync()",
            src,
            "main.tsx must call initMultiTabAuthSync() at app boot",
        )


if __name__ == "__main__":
    unittest.main()
