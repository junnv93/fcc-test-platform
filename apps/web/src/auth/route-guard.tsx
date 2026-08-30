import {
  useEffect,
  useState,
  useSyncExternalStore,
  type ReactElement,
  type ReactNode,
} from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { getRuntimeConfig } from '@/config/runtime';
import { useT } from '@/i18n';
import { captureException } from '@/observability/sentry';
import { Button } from '@/ui';

import { OidcFailureView, type OidcFailureKind } from './failure-ui';
import { localLogout } from './local-login';
import { signOutEverywhere } from './multi-tab-sync';
import { completeLogin, OidcFlowError, signOutAtIdp, startLogin } from './oidc-pkce';
import {
  applyTokenSet,
  currentAccessToken,
  currentRefreshToken,
  currentIdToken,
  getAuthState,
  hasPermission,
  requiresPasswordChange,
  subscribeAuth,
  type AuthState,
  type Principal,
  type SignOutReason,
} from './session';

/**
 * Route guard + auth lifecycle components — Sprint S2.
 *
 * Three responsibilities are deliberately kept here so view code can stay
 * unaware of auth wiring:
 *
 *   1. `useAuthSession()`        — React-friendly subscription to session.ts
 *   2. `<RequireAuth>`           — gate a subtree behind authentication
 *   3. `<RequirePermission>`     — gate a subtree behind a permission token
 *   4. `<AuthCallbackRoute />`   — handle the OIDC redirect-back URL
 *   5. `<SignOutButton />`       — local + optional RP-Initiated Logout
 *
 * `OidcFailureView` is rendered in error paths so the 5 failure kinds the
 * contract enumerates surface to the operator instead of a blank screen.
 */

// ─── Hook ─────────────────────────────────────────────────────────────────

/** Subscribe to session.ts and re-render the calling component whenever the
 *  auth state changes (login / silent refresh / signOut).
 *
 *  Sprint S2-β α-2 — uses React 18's `useSyncExternalStore` instead of
 *  `useState + useEffect`. This is the concurrent-safe pattern for
 *  external mutable state: React guarantees no tearing across rendered
 *  components even with `startTransition` / Suspense in flight, and the
 *  snapshot is stable for the entire render pass. */
export function useAuthSession(): AuthState {
  return useSyncExternalStore(subscribeAuth, getAuthState, getAuthState);
}

// ─── Gates ────────────────────────────────────────────────────────────────

export interface RequireAuthProps {
  readonly children: ReactNode;
}

/** Redirect to the IdP if the current session is unauthenticated. */
export function RequireAuth({ children }: RequireAuthProps): ReactElement {
  const state = useAuthSession();
  const location = useLocation();
  const navigate = useNavigate();
  const [failure, setFailure] = useState<OidcFailureKind | null>(null);
  const unauthenticatedReason = state.kind === 'unauthenticated' ? state.reason : null;
  const passwordChangeRequired = requiresPasswordChange();

  useEffect(() => {
    // A user-initiated sign-out clears local auth synchronously before the
    // RP-Initiated Logout request reaches the IdP. Do not let this document's
    // guard start a competing authorization redirect; a fresh post-logout
    // document restores the SSOT state with no user-initiated reason and can
    // reauthorize normally.
    if (state.kind !== 'unauthenticated') return;
    // Local auth has an in-app sign-in route. A user-initiated sign-out must
    // navigate there explicitly because no document reload or IdP redirect
    // follows the synchronous local session clear. OIDC keeps its existing
    // post-logout handoff semantics.
    if (getRuntimeConfig().authMode === 'local' && unauthenticatedReason === 'user_initiated') {
      navigate('/login', { replace: true });
      return;
    }
    if (unauthenticatedReason === 'user_initiated') return;
    // returnTo captures the current path so completeLogin restores it.
    const returnTo = `${location.pathname}${location.search}${location.hash}`;
    // 신원 축 EMS 정합 (2026-08-21) — in local mode there is no IdP to redirect to.
    // The sign-in form is a route in this document, so navigate instead of leaving
    // the origin. `startLogin` is not even reached: calling it would throw the very
    // `crypto.subtle` TypeError this mode exists to avoid.
    if (getRuntimeConfig().authMode === 'local') {
      navigate('/login', { state: { returnTo }, replace: true });
      return;
    }
    startLogin({ returnTo }).catch((err: unknown) => {
      // S2-γ β-P2-7 — auth flow errors surface to Sentry so operations
      // sees the failure rate even when the operator just refreshes.
      captureException(err, { authFlow: 'startLogin', returnTo });
      setFailure(translateFlowError(err));
    });
  }, [
    state.kind,
    unauthenticatedReason,
    navigate,
    location.pathname,
    location.search,
    location.hash,
  ]);

  useEffect(() => {
    if (!passwordChangeRequired || location.pathname === '/change-password') return;
    navigate('/change-password', { replace: true });
  }, [location.pathname, navigate, passwordChangeRequired]);

  if (failure !== null) {
    return <OidcFailureView kind={failure} />;
  }
  if (state.kind === 'unauthenticated') {
    return <RedirectingToIdpView reason={state.reason} />;
  }
  if (passwordChangeRequired && location.pathname !== '/change-password') {
    return <PasswordChangeRedirectView />;
  }
  return <>{children}</>;
}

export interface RequirePermissionProps {
  readonly permission: string;
  readonly children: ReactNode;
}

/** Render `children` only if the principal carries `permission`. Renders a
 *  `permission_denied` failure view otherwise. Always wrap inside
 *  `<RequireAuth>` so the auth gate runs first. */
export function RequirePermission({ permission, children }: RequirePermissionProps): ReactElement {
  const state = useAuthSession();
  if (state.kind !== 'authenticated') {
    // Caller forgot to wrap in <RequireAuth> — treat as denied so the
    // operator sees a clear UX instead of a blank page.
    return (
      <OidcFailureView kind="permission_denied" detail={`missing permission: ${permission}`} />
    );
  }
  if (!hasPermission(permission)) {
    return (
      <OidcFailureView kind="permission_denied" detail={`missing permission: ${permission}`} />
    );
  }
  return <>{children}</>;
}

// ─── Auth callback ────────────────────────────────────────────────────────

interface CallbackStatus {
  readonly kind: 'pending' | 'success' | 'failure';
  readonly failure?: OidcFailureKind;
  readonly detail?: string;
}

/** Module-level guard against React 18 StrictMode's dev-only double-invoke of
 *  the callback effect. `completeLogin()` reads AND CLEARS the one-shot PKCE
 *  transaction state (state / verifier / nonce) from sessionStorage, so a naive
 *  second invocation finds empty state and throws `state_mismatch` ("possible
 *  CSRF"). Sharing ONE in-flight promise across the unmount/remount makes the
 *  transaction read+cleared exactly once. Reset on settle so a later re-login
 *  starts fresh. Mirrors the existing `discoveryInFlight` idiom in oidc-pkce.ts.
 *  No-op in production (StrictMode does not double-invoke there). */
let callbackInFlight: ReturnType<typeof completeLogin> | null = null;

/** OIDC redirect endpoint — handles `?code=&state=` and bootstraps session. */
export function AuthCallbackRoute(): ReactElement {
  const { t } = useT();
  const navigate = useNavigate();
  const [status, setStatus] = useState<CallbackStatus>({ kind: 'pending' });

  useEffect(() => {
    let cancelled = false;
    (callbackInFlight ??= completeLogin())
      .then((result) => {
        if (cancelled) return;
        applyTokenSet(result.tokens, { idTokenClaims: result.idTokenClaims });
        setStatus({ kind: 'success' });
        navigate(result.returnTo, { replace: true });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // S2-γ β-P2-7 — callback errors are higher-signal than login
        // kick-off errors (we have a code+state from the IdP). Always
        // capture so silent OIDC misconfig surfaces without operator
        // having to read the panel.
        captureException(err, { authFlow: 'completeLogin' });
        setStatus({
          kind: 'failure',
          failure: translateFlowError(err),
          detail: err instanceof Error ? err.message : String(err),
        });
      })
      .finally(() => {
        // Allow a later (post-logout) re-login to run a fresh transaction.
        callbackInFlight = null;
      });
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  if (status.kind === 'failure' && status.failure) {
    return <OidcFailureView kind={status.failure} detail={status.detail ?? null} />;
  }
  return (
    <section className="auth-callback" aria-busy="true" aria-live="polite">
      <h1>{t('auth.routeGuard.callbackTitle')}</h1>
      <p>{t('auth.routeGuard.callbackBody')}</p>
    </section>
  );
}

// ─── Sign out ─────────────────────────────────────────────────────────────

export interface SignOutButtonProps {
  readonly children?: ReactNode;
  /** When `true`, the next `startLogin` adds `prompt=login` so the IdP
   *  forces re-authentication even if its SSO cookie survives this sign-out
   *  (S2-α #13). Default `false` — typical operator sign-out lets the IdP
   *  honour SSO so the next login does not require credentials. */
  readonly forceReauth?: boolean;
}

export function SignOutButton({ children, forceReauth = false }: SignOutButtonProps): ReactElement {
  const { t } = useT();
  const [busy, setBusy] = useState(false);
  return (
    <Button
      type="button"
      variant="ghost"
      className="auth-signout-button"
      onClick={() => {
        if (busy) return;
        setBusy(true);
        // Increment 6 P0 (supervisor iter 2) — broadcast + local-clear on
        // EVERY sign-out path, BEFORE the IdP round-trip. A successful
        // RP-Initiated Logout (`redirected=true`) navigates THIS tab away to
        // the IdP's end_session_endpoint; the prior code only broadcast on the
        // `redirected=false` / catch branches, so a redirect success sent no
        // BroadcastChannel message and sibling tabs stayed authenticated.
        // Broadcasting first makes "log out" mean "log out everywhere"
        // regardless of the IdP outcome. Capture the id_token before the local
        // clear so RP-Initiated Logout can identify the IdP session without an
        // avoidable re-authentication screen; the token is never retained by
        // the React auth snapshot or localStorage.
        const idTokenHint = currentIdToken();
        // 신원 축 EMS 정합 (2026-08-21) — in local mode there is no IdP session
        // to end, and `signOutAtIdp` would throw on discovery. What there IS is a
        // server-side session, and telling the server is the ONLY thing that ends
        // it.
        //
        // ⚠️ **This branch is the whole point.** Adversarial review measured the
        // shipped local flow: one network request, to the IdP's discovery
        // endpoint, and `localLogout` call count ZERO. Rotation, dual revocation
        // and the shared revocation list were all unreachable from the product —
        // sign-out was a sessionStorage wipe, and a refresh token captured off the
        // plaintext LAN kept minting sessions for seven days straight through it.
        //
        // ⚠️ Both tokens are captured BEFORE `signOutEverywhere`, which clears
        // sessionStorage. Reading them after would send empty strings, and the
        // server would answer 200 having revoked nothing.
        //
        // ⚠️ Captured BEFORE the broadcast below, which clears sessionStorage.
        // Reading them after would send empty strings and the server would answer
        // 200 having revoked nothing.
        const localSession =
          getRuntimeConfig().authMode === 'local'
            ? { accessToken: currentAccessToken(), refreshToken: currentRefreshToken() }
            : null;
        // ⚠️ ONE unconditional broadcast, above the branch. An earlier draft put a
        // copy inside each arm and tripped `test_sign_out_button_broadcasts_
        // unconditionally` — the seal exists precisely because a per-branch copy
        // is how one arm silently loses it.
        signOutEverywhere('user_initiated');
        if (localSession !== null) {
          const revoked =
            localSession.accessToken === null
              ? Promise.resolve()
              : localLogout(localSession.accessToken, localSession.refreshToken);
          // ⚠️ `void` — the result is deliberately unawaited (sign-out must not
          // block on the revocation round-trip), and saying so explicitly is
          // what `no-floating-promises` asks for. Without it `npm run build`
          // fails, which is how the SPA image came to be unbuildable.
          void revoked.finally(() => setBusy(false));
          return;
        }
        signOutAtIdp({
          forceReauth,
          ...(idTokenHint === null ? {} : { idTokenHint }),
        })
          .catch(() => {
            // Discovery / redirect failed — local + sibling state already
            // cleared above; nothing further to do but stop the busy spinner.
          })
          .finally(() => setBusy(false));
      }}
      disabled={busy}
      /* Sprint S2-γ β-P2-8 — a11y. When busy, screen readers announce the
         in-progress state and ignore further interaction prompts. */
      aria-busy={busy}
      aria-live="polite"
      aria-label={busy ? t('auth.routeGuard.signOutBusy') : undefined}
    >
      {children ?? t('auth.routeGuard.signOut')}
    </Button>
  );
}

// ─── Utility (exported for the openapi-fetch middleware) ──────────────────

/** Builder used by `src/api/session-client.ts` middleware. Returns `null` so
 *  the middleware can leave the request header untouched when unauthenticated. */
export function authorizationHeader(): string | null {
  const token = currentAccessToken();
  return token ? `Bearer ${token}` : null;
}

// ─── Helpers ──────────────────────────────────────────────────────────────

function translateFlowError(err: unknown): OidcFailureKind {
  if (err instanceof OidcFlowError) {
    if (err.kind === 'discovery_unreachable' || err.kind === 'discovery_invalid') {
      return 'idp_unreachable';
    }
    if (err.kind === 'discovery_missing_field' || err.kind === 'pkce_method_unsupported') {
      return 'idp_config_missing';
    }
    if (err.kind === 'token_endpoint_error' || err.kind === 'token_response_invalid') {
      return 'idp_unreachable';
    }
    if (err.kind === 'state_mismatch' || err.kind === 'verifier_missing') {
      return 'token_expired';
    }
  }
  return 'idp_unreachable';
}

interface RedirectingProps {
  readonly reason: SignOutReason | null;
}

function RedirectingToIdpView({ reason }: RedirectingProps): ReactElement {
  const { t } = useT();
  const message =
    reason === 'token_expired'
      ? t('auth.routeGuard.redirectTokenExpired')
      : reason === 'refresh_failed'
        ? t('auth.routeGuard.redirectRefreshFailed')
        : t('auth.routeGuard.redirectDefault');
  return (
    <section className="auth-redirect" aria-busy="true" aria-live="polite">
      <h1>{message}</h1>
    </section>
  );
}

function PasswordChangeRedirectView(): ReactElement {
  const { t } = useT();
  return (
    <section className="auth-redirect" aria-busy="true" aria-live="polite">
      <h1>{t('auth.local.passwordChangeTitle')}</h1>
    </section>
  );
}

/** Re-export so view code only has to import from `@/auth/route-guard`. */
export type { AuthState, Principal };
