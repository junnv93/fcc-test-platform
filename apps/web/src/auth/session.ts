/**
 * OIDC session lifecycle — Sprint S2.
 *
 * Owns the token cache, the silent-refresh scheduler, and the subscriber
 * pattern that React components use to react to auth state changes.
 *
 * Storage policy (MUST):
 *   - sessionStorage only — tokens vanish on tab close. localStorage and
 *     document.cookie are forbidden (and grepped by
 *     `tests/test_apps_web_auth_scaffold.py::TestSessionStorageOnly`).
 *
 * Backend SSOT alignment (F-2-D4 — `application/common/auth_config.py`):
 *   - CLAIM_PERMISSIONS / CLAIM_SCOPE / CLAIM_ROLES mirror
 *     `HttpAuthConfig.oidc_permissions_claim / oidc_scope_claim /
 *     oidc_role_claim` defaults. If backend defaults change, both sides must
 *     change together — `test_apps_web_auth_scaffold.py` cross-checks.
 *
 * Refresh-margin SSOT:
 *   - MIN_REFRESH_MARGIN_SECONDS = 30  →  RFC 6749 § 5.1 implies servers
 *     SHOULD set expires_in conservatively, and Auth0 / Microsoft Identity /
 *     Keycloak all recommend 15-60 s pre-expiry refresh for SPA clients. 30 s
 *     is the median guidance and gives clients a single network round-trip
 *     budget before the access_token expires server-side.
 */

import { discoverOidcConfig, refreshTokens, verifyIdToken, type OidcTokenSet } from './oidc-pkce';
import { ALL_STORAGE_KEYS, STORAGE_KEY_AUTH_STRATEGY, STORAGE_KEY_TOKENS } from './storage-keys';

import type { JWTPayload } from 'jose';

// ─── SSOT constants (mirror backend HttpAuthConfig defaults) ──────────────

export const CLAIM_PERMISSIONS = 'permissions';
export const CLAIM_SCOPE = 'scope';
export const CLAIM_ROLES = 'roles';
export const CLAIM_SUBJECT = 'sub';
export const CLAIM_NAME = 'name';
export const CLAIM_EMAIL = 'email';

/** Seconds before access_token `exp` that silent-refresh fires. Industry
 *  median (Auth0 / Microsoft Identity / Keycloak guidance — 15–60s).
 *  Cross-tech contract — see docs/architecture/frontend/cross-tech-token-policy.md.
 *
 *  Sprint S2-δ γ-P1-1 — distinct from `OIDC_CLOCK_TOLERANCE_SECONDS`
 *  below. This is a CLIENT scheduling margin (when to fire refresh
 *  before exp). `OIDC_CLOCK_TOLERANCE_SECONDS` is a SERVER validation
 *  tolerance (how much skew between RP and IdP clocks to accept on
 *  exp/iat/nbf). They happen to be the same integer; they are NOT the
 *  same concept. Mirrors backend `OIDC_REFRESH_MARGIN_SECONDS`. */
export const MIN_REFRESH_MARGIN_SECONDS = 30;

/** Sprint S2-δ γ-P0-1 + γ-P2-12 + S2-ε δ-P1-1 — JWT ``exp``/``iat``/``nbf``
 *  clock skew tolerance for jose `jwtVerify`. Number (seconds), NOT
 *  string — jose accepts both but number is type-safe and unit-explicit.
 *
 *  Value 60s = Auth0 default. Deliberately distinct from
 *  `MIN_REFRESH_MARGIN_SECONDS` (30s) so the conceptual separation has
 *  a *behavioural* difference — pre-S2-ε both were 30 and the cross-
 *  check trivially passed without forcing the tuner to think.
 *
 *  Mirrors backend `application/headless/oidc_principal_resolver.py::OIDC_CLOCK_TOLERANCE_SECONDS`. */
export const OIDC_CLOCK_TOLERANCE_SECONDS = 60;

// ─── Domain types ─────────────────────────────────────────────────────────

export interface Principal {
  readonly subject: string;
  readonly name: string | null;
  readonly email: string | null;
  readonly permissions: readonly string[];
  readonly scopes: readonly string[];
  readonly roles: readonly string[];
  readonly raw: Readonly<Record<string, unknown>>;
}

export type SignOutReason = 'user_initiated' | 'refresh_failed' | 'token_expired';

export type AuthState =
  | { readonly kind: 'unauthenticated'; readonly reason: SignOutReason | null }
  | {
      readonly kind: 'authenticated';
      readonly principal: Principal;
      readonly accessToken: string;
      readonly expiresAt: number;
    };

/** `useSyncExternalStore`-compatible signature — the callback is invoked
 *  with no arguments when auth state has changed. Subscribers read the
 *  fresh state via {@link getAuthState}. Sprint S2-β α-2 — was previously
 *  `(state: AuthState) => void` (immediate-fire + state-arg), which is
 *  unsafe under React 18 concurrent rendering. */
export type AuthListener = () => void;

// ─── State ────────────────────────────────────────────────────────────────

let currentState: AuthState = { kind: 'unauthenticated', reason: null };
const listeners = new Set<AuthListener>();
let refreshTimer: ReturnType<typeof setTimeout> | null = null;

// ─── Public API ───────────────────────────────────────────────────────────

/** Read the validated payload from sessionStorage and rehydrate the in-memory
 *  state machine. Called once at app boot. */
export function restoreSession(): AuthState {
  const raw = readStoredTokens();
  if (raw === null) {
    currentState = { kind: 'unauthenticated', reason: null };
    return currentState;
  }
  if (isExpired(raw)) {
    clearStoredTokens();
    currentState = { kind: 'unauthenticated', reason: 'token_expired' };
    notify();
    return currentState;
  }
  applyTokenSet(raw);
  return currentState;
}

/** Which strategy minted a token set (identity axis, 2026-08-21). */
export type AuthStrategy = 'oidc' | 'local';

export interface ApplyTokenSetOptions {
  /** Verified id_token claims from `completeLogin` (jose `jwtVerify` —
   *  signature + iss + aud + exp + nonce all checked). When provided, these
   *  claims are trusted; when absent, `session.ts` falls back to a
   *  best-effort base64 decode of the access_token. */
  readonly idTokenClaims?: JWTPayload | null;
  /** Records which strategy issued these tokens so the silent refresh can pick
   *  the matching path. Omitted on a refresh (the strategy cannot change
   *  mid-session), supplied on login. */
  readonly strategy?: AuthStrategy;
}

/** The strategy that minted the CURRENT token set. Defaults to `'oidc'` so every
 *  pre-existing session — and every stored token set written before this axis
 *  existed — behaves exactly as before. */
export function currentAuthStrategy(): AuthStrategy {
  return globalThis.sessionStorage.getItem(STORAGE_KEY_AUTH_STRATEGY) === 'local'
    ? 'local'
    : 'oidc';
}

function writeAuthStrategy(strategy: AuthStrategy): void {
  globalThis.sessionStorage.setItem(STORAGE_KEY_AUTH_STRATEGY, strategy);
}

/** Persist a fresh token set and schedule the next silent refresh. Called
 *  from `completeLogin` (post-callback) and from the silent-refresh path. */
export function applyTokenSet(tokens: OidcTokenSet, options: ApplyTokenSetOptions = {}): void {
  // A token set in hand means the conversation succeeded; the previous
  // session's throttle bookkeeping must not follow the new one.
  resetThrottleState();
  writeStoredTokens(tokens);
  if (options.strategy !== undefined) {
    writeAuthStrategy(options.strategy);
  }
  const principal = decodePrincipal(tokens, options.idTokenClaims ?? null);
  const expiresAt = tokens.issuedAt + tokens.expiresIn * 1000;
  currentState = {
    kind: 'authenticated',
    principal,
    accessToken: tokens.accessToken,
    expiresAt,
  };
  scheduleRefresh(tokens);
  notify();
}

/** Clear in-memory + persisted token state. Defaults to user-initiated; pass
 *  `refresh_failed` / `token_expired` when the cause is non-user. */
export function signOut(reason: SignOutReason = 'user_initiated'): void {
  cancelRefresh();
  resetThrottleState();
  // M4 — abandon the coalesced refresh slot. Without this, a caller arriving
  // after sign-out would be handed the pre-sign-out promise and, on its
  // success, `applyTokenSet` would resurrect a session the operator just ended.
  releaseSharedRefresh();
  sessionEpoch += 1;
  for (const key of ALL_STORAGE_KEYS) {
    globalThis.sessionStorage.removeItem(key);
  }
  currentState = { kind: 'unauthenticated', reason };
  notify();
}

export function getAuthState(): AuthState {
  return currentState;
}

/** Returns the current bearer token (or `null` if unauthenticated). Used by
 *  the openapi-fetch middleware in `src/api/session-client.ts`. */
export function currentAccessToken(): string | null {
  return currentState.kind === 'authenticated' ? currentState.accessToken : null;
}

/**
 * Whether the server-issued local session is still carrying the bootstrap
 * password-change requirement. The server remains authoritative and rejects
 * protected operations; this client predicate only prevents a direct URL from
 * rendering the application shell before the operator reaches the resolver.
 */
export function requiresPasswordChange(): boolean {
  return (
    currentState.kind === 'authenticated' &&
    currentState.principal.raw.force_password_change === true
  );
}

/** Return the current OIDC id_token for RP-Initiated Logout, when the IdP
 * issued one. The caller must capture this before {@link signOut} clears the
 * session; the token never enters the React auth snapshot or localStorage. */
export function currentIdToken(): string | null {
  return readStoredTokens()?.idToken ?? null;
}

/** The stored refresh token, or `null`.
 *
 * ⚠️ Must be read BEFORE {@link signOut}, which clears sessionStorage. Local
 * sign-out has to hand this to the server: retiring only the access token ends
 * nothing, because the refresh token keeps minting sessions for its full life.
 */
export function currentRefreshToken(): string | null {
  return readStoredTokens()?.refreshToken ?? null;
}

/** `useSyncExternalStore`-compatible subscribe. Returns the unsubscribe
 *  function. Does NOT fire immediately — `useSyncExternalStore` reads the
 *  initial value via its `getSnapshot` parameter, and React's
 *  concurrent-mode batching invokes the callback once per real change
 *  (avoiding tearing).
 *
 *  Sprint S2-γ β-P2-10 — IMPORTANT for non-React callers: you MUST call
 *  the returned unsubscribe function (typically in a `try/finally` or
 *  effect cleanup) when your subscriber is no longer needed. The
 *  `listeners` Set holds strong references; orphan subscribers leak.
 *
 *  React callers using {@link useAuthSession} (which wraps this via
 *  `useSyncExternalStore`) get automatic cleanup on unmount — no
 *  manual unsubscribe needed. */
export function subscribeAuth(listener: AuthListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** RBAC check — does the current principal carry the named permission? */
export function hasPermission(permission: string): boolean {
  if (currentState.kind !== 'authenticated') return false;
  return currentState.principal.permissions.includes(permission);
}

// ─── Silent refresh ───────────────────────────────────────────────────────

function scheduleRefresh(tokens: OidcTokenSet): void {
  cancelRefresh();
  if (!tokens.refreshToken) {
    // No refresh_token (e.g. IdP didn't issue one) — let the access_token
    // expire and surface `token_expired` on next protected request.
    return;
  }
  const expiresAtMs = tokens.issuedAt + tokens.expiresIn * 1000;
  const marginMs = MIN_REFRESH_MARGIN_SECONDS * 1000;
  const delayMs = Math.max(0, expiresAtMs - marginMs - Date.now());
  const captured = tokens.refreshToken;
  refreshTimer = setTimeout(() => {
    void performSilentRefresh(captured);
  }, delayMs);
}

function cancelRefresh(): void {
  if (refreshTimer !== null) {
    clearTimeout(refreshTimer);
    refreshTimer = null;
  }
}

/** Core refresh primitive — exchange a refresh_token for a fresh token set and
 *  apply it. Returns `true` on success, `false` on any failure (network, token
 *  endpoint error, id_token re-verify failure). The SOLE refresh implementation
 *  so the scheduled silent refresh and the on-demand 401 retry path cannot
 *  drift (Increment 6). */
/**
 * Monotonic session generation, bumped on every sign-out (M4 follow-through).
 *
 * A refresh is a multi-await sequence (token exchange → discovery → id_token
 * verify). If the operator signs out — or the 401 path forces `token_expired` —
 * while it is in flight, the sequence still finishes and its `applyTokenSet`
 * would re-persist tokens and flip the store back to `authenticated`: a session
 * the user just ended silently resurrects. Comparing the captured generation
 * before applying makes the outcome of a refresh valid only for the session that
 * asked for it.
 */
let sessionEpoch = 0;

async function attemptRefresh(refreshToken: string): Promise<boolean> {
  const epoch = sessionEpoch;
  // ⚠️ The strategy branch has to be HERE, not at the call site. `restoreSession()`
  // rehydrates tokens after a page reload and schedules a refresh with no idea
  // where they came from; without this, a locally-issued refresh_token would be
  // presented to the IdP, rejected, and the operator signed out — roughly fifteen
  // minutes after a reload, which reads as a network fault rather than a bug.
  if (currentAuthStrategy() === 'local') {
    try {
      const { localRefresh } = await import('./local-login');
      const next = await localRefresh(refreshToken);
      if (sessionEpoch !== epoch) return false;
      applyTokenSet(next);
      return true;
    } catch (error) {
      // ⚠️ A THROTTLE IS NOT AN AUTHENTICATION FAILURE.
      //
      // The platform gained a per-subject rotation budget (2026-08-23); its
      // denial is a 429 carrying `Retry-After`. Swallowing the kind here made
      // the scheduled refresh sign the operator out on the FIRST 429 — which is
      // precisely the failure the budget was sized to avoid ("signed out
      // mid-measurement"), delivered by the client rather than the server.
      // Adversarial review measured it.
      //
      // The hint is stashed for `performSilentRefresh`, which reschedules
      // instead of signing out. Module-level is safe because `sharedRefresh`
      // coalesces: at most one refresh is ever in flight.
      throttledRetryAfterSeconds = throttledRetryOf(error);
      return false;
    }
  }
  try {
    const next = await refreshTokens(refreshToken);
    // Sprint S2-β α-4 — re-verify the new id_token (jose: signature + iss +
    // aud + exp). The silent refresh has no fresh authorization round-trip,
    // so there's no nonce to check (`skipNonce: true`). Without this
    // re-verify, the JWT decode in `decodePrincipal` would happen via the
    // best-effort base64 fallback, eroding the trust guarantee that
    // `completeLogin` established for the verified-claim path.
    let verifiedClaims: JWTPayload | null = null;
    if (next.idToken !== null) {
      const discovery = await discoverOidcConfig();
      verifiedClaims = await verifyIdToken(next.idToken, discovery, { skipNonce: true });
    }
    if (sessionEpoch !== epoch) {
      // Signed out while this refresh was in flight — discard the result rather
      // than resurrecting the ended session.
      return false;
    }
    applyTokenSet(next, { idTokenClaims: verifiedClaims });
    return true;
  } catch {
    return false;
  }
}

/**
 * In-flight refresh, shared by every caller (fe-data-layer-robustness M4,
 * 2026-07-19).
 *
 * `refreshAuthSession` is called from `authRetryMiddleware.onResponse`, which
 * runs **per request**. A dashboard mounts several queries at once, so an
 * access_token that expires while the tab is backgrounded produces N
 * simultaneous 401s and, before this, N concurrent `POST /token` exchanges with
 * the SAME refresh_token. Under refresh-token rotation (Keycloak's default, and
 * mandatory for public SPA clients per OAuth 2.1 / BCP) the IdP invalidates the
 * presented token on first use: the first exchange succeeds and every later one
 * returns `invalid_grant`, so the middleware signs the operator out mid-run —
 * the more the UI polls, the likelier the logout.
 *
 * Same idiom as `oidc-pkce.ts::discoveryInFlight`: coalesce onto one promise and
 * clear it once settled, so a *later* refresh is never blocked by a stale slot.
 * `attemptRefresh` swallows its own failures (returns `false`), but the slot is
 * released via `.finally` so even a future throwing implementation cannot wedge
 * it. The identity check guards against clearing a slot a newer call installed.
 */
let refreshInFlight: Promise<boolean> | null = null;

/** Seconds the server asked us to wait, set by the local branch of
 *  {@link attemptRefresh} when a rotation was throttled (HTTP 429). `null` means
 *  the last failure was not a throttle. Read-and-cleared by
 *  {@link performSilentRefresh}. */
let throttledRetryAfterSeconds: number | null = null;

/** How long to wait when a 429 carries no readable `Retry-After`.
 *
 * ⚠️ **This used to be one second, and one second is a denial-of-service on
 * ourselves.** Adversarial review measured it: a 429 without the header
 * produced ~60 refresh attempts per minute per tab, unbounded — begun exactly
 * when something upstream is already shedding load. And the header-less 429 is
 * not hypothetical: the deployed gateway's own `limit_req` returns a plain
 * HTML 429 with no `Retry-After` at all, so this is the *normal* shape of the
 * failure whenever nginx sheds before the application does.
 *
 * The window the server actually enforces is the honest fallback: retrying
 * sooner than the window cannot succeed, it can only keep the window pinned. */
const THROTTLE_FALLBACK_SECONDS = 60;

/** Consecutive throttle reschedules, so a peer that answers 429 forever cannot
 *  keep us retrying forever. Reset by any successful refresh. */
let consecutiveThrottles = 0;

/** Monotonic ms timestamp until which the server has told us not to rotate, or
 *  `0`. Read by {@link refreshAuthSession} so the 401 interceptor does not
 *  hammer a door the server has already closed.
 *
 * ⚠️ **The reschedule bound bounds the TIMER, not the requests.** Adversarial
 * review measured it: after the timer bound was exhausted, thirty ordinary API
 * calls meeting 401s produced thirty more refresh POSTs, because each one
 * entered through the interceptor rather than the timer. The self-inflicted
 * flood came back, paced by the application's own request rate instead of by a
 * clock — and pointed at the very endpoint that is rate-limiting us. */
let throttledUntilMs = 0;

/** Forget every throttle bookkeeping value. Called when a session begins or
 *  ends, because these counters describe *this* session's conversation with the
 *  server. Carrying them across a sign-out meant a freshly logged-in operator
 *  could be signed out by their FIRST 429 (measured). */
function resetThrottleState(): void {
  throttledRetryAfterSeconds = null;
  consecutiveThrottles = 0;
  throttledUntilMs = 0;
}

/** After this many consecutive throttles, stop rescheduling and let the session
 *  end the ordinary way. ⚠️ Bounded rather than infinite: "never sign out" is a
 *  different defect from "sign out on the first 429", not the opposite of it. */
const MAX_CONSECUTIVE_THROTTLES = 5;

/** Extract the `Retry-After` hint from a `LocalLoginError` of kind `throttled`,
 *  or `null` for every other failure. Structural rather than `instanceof` so a
 *  lazily-imported module boundary cannot make the check silently false. */
function throttledRetryOf(error: unknown): number | null {
  if (typeof error !== 'object' || error === null) return null;
  const candidate = error as { kind?: unknown; retryAfterSeconds?: unknown };
  if (candidate.kind !== 'throttled') return null;
  const hint = candidate.retryAfterSeconds;
  return typeof hint === 'number' && Number.isFinite(hint) && hint > 0
    ? Math.ceil(hint)
    : THROTTLE_FALLBACK_SECONDS;
}

/** Drop the coalescing slot (sign-out / test reset). Callers already holding
 *  the promise still settle normally — only future callers are affected. */
function releaseSharedRefresh(): void {
  refreshInFlight = null;
}

function sharedRefresh(refreshToken: string): Promise<boolean> {
  if (refreshInFlight !== null) {
    return refreshInFlight;
  }
  const shared = attemptRefresh(refreshToken).finally(() => {
    if (refreshInFlight === shared) {
      refreshInFlight = null;
    }
  });
  refreshInFlight = shared;
  return shared;
}

async function performSilentRefresh(refreshToken: string): Promise<void> {
  const epoch = sessionEpoch;
  const ok = await sharedRefresh(refreshToken);
  if (ok) {
    consecutiveThrottles = 0;
    return;
  }
  const retryAfter = takeThrottleHint();
  // ⚠️ The session may have ended while the request was in flight. Without this
  // the throttle branch schedules work for an operator who has already signed
  // out — a timer holding their refresh token, on a shared chamber PC. The OIDC
  // branch of `attemptRefresh` already checks the epoch for the same reason;
  // this path did not, and adversarial review measured the surviving timer.
  if (sessionEpoch !== epoch) return;
  if (retryAfter !== null && consecutiveThrottles <= MAX_CONSECUTIVE_THROTTLES) {
    throttledUntilMs = Date.now() + Math.max(1, retryAfter) * 1000;
    // Keep the session and come back after the window the server named. The
    // access_token may expire in the meantime; the 401 interceptor then decides,
    // and that residual is a ledger item rather than a silent sign-out here.
    refreshTimer = setTimeout(
      () => {
        void performSilentRefresh(refreshToken);
      },
      Math.max(1, retryAfter) * 1000,
    );
    return;
  }
  signOut('refresh_failed');
}

/** Read and clear the throttle hint, counting consecutive throttles.
 *
 * ⚠️ Shared by both refresh paths on purpose. The scheduled path and the 401
 * interceptor are two doors onto the same server behaviour, and the first
 * version of this feature fixed only one of them — so a throttle still signed
 * the operator out, just through the other door. Adversarial review found that
 * by driving the interceptor rather than by reading the code. */
function takeThrottleHint(): number | null {
  const hint = throttledRetryAfterSeconds;
  throttledRetryAfterSeconds = null;
  if (hint === null) {
    consecutiveThrottles = 0;
    return null;
  }
  consecutiveThrottles += 1;
  return hint;
}

/** Was the most recent failed refresh a throttle rather than a dead token?
 *
 * The 401 interceptor needs this to tell "wait, the server is rate-limiting
 * this account" from "this session is over". Signing out on the former hands an
 * attacker who holds a captured refresh token a way to sign the victim out on
 * demand: burn their rotation budget, and the victim's next API call ends
 * their session. */
export function lastRefreshWasThrottled(): boolean {
  // ⚠️ The window matters as much as the last outcome: once we short-circuit
  // inside an open window, `throttledRetryAfterSeconds` has been consumed by
  // the scheduled retry, and answering `false` there would send the middleware
  // straight back to signing out — the defect this function exists to prevent,
  // reappearing one call later.
  return throttledRetryAfterSeconds !== null || throttledUntilMs > Date.now();
}

/** On-demand silent refresh for the 401 retry-once interceptor (Increment 6).
 *  Reads the persisted refresh_token, attempts a refresh, and returns whether a
 *  fresh token set was applied. Unlike {@link performSilentRefresh} it does NOT
 *  sign out on failure — the caller (the 401 middleware) decides whether a
 *  failed refresh means `token_expired` sign-out. Returns `false` immediately
 *  when no refresh_token is available. */
export async function refreshAuthSession(): Promise<boolean> {
  const stored = readStoredTokens();
  const refreshToken = stored?.refreshToken ?? null;
  if (refreshToken === null) {
    return false;
  }
  // ⚠️ **Do not knock while the door is closed.** The scheduled retry is
  // already armed for the moment the window ends; asking again before then
  // cannot succeed and only adds load to the endpoint that is shedding.
  if (throttledUntilMs > Date.now()) {
    return false;
  }
  const epoch = sessionEpoch;
  const ok = await sharedRefresh(refreshToken);
  if (ok) {
    consecutiveThrottles = 0;
    throttledUntilMs = 0;
    return true;
  }
  // ⚠️ **Arm the retry here, or `lastRefreshWasThrottled()` is a promise the
  // client does not keep.** The 401 middleware surfaces the 401 and keeps the
  // session on the strength of "we will come back after the window" — nothing
  // else in this path schedules that. Leaving the hint set (rather than
  // clearing it) is deliberate: the middleware reads it immediately after this
  // returns, and `takeThrottleHint` inside the scheduled retry clears it.
  if (
    throttledRetryAfterSeconds !== null &&
    sessionEpoch === epoch &&
    consecutiveThrottles <= MAX_CONSECUTIVE_THROTTLES
  ) {
    const retryAfter = throttledRetryAfterSeconds;
    consecutiveThrottles += 1;
    throttledUntilMs = Date.now() + Math.max(1, retryAfter) * 1000;
    cancelRefresh();
    refreshTimer = setTimeout(
      () => {
        throttledRetryAfterSeconds = null;
        void performSilentRefresh(refreshToken);
      },
      Math.max(1, retryAfter) * 1000,
    );
  }
  return false;
}

// ─── Storage helpers ──────────────────────────────────────────────────────

function readStoredTokens(): OidcTokenSet | null {
  const raw = globalThis.sessionStorage.getItem(STORAGE_KEY_TOKENS);
  if (raw === null) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return validateStoredTokens(parsed);
  } catch {
    clearStoredTokens();
    return null;
  }
}

function writeStoredTokens(tokens: OidcTokenSet): void {
  globalThis.sessionStorage.setItem(STORAGE_KEY_TOKENS, JSON.stringify(tokens));
}

function clearStoredTokens(): void {
  globalThis.sessionStorage.removeItem(STORAGE_KEY_TOKENS);
}

function validateStoredTokens(value: unknown): OidcTokenSet | null {
  if (!isRecord(value)) return null;
  const accessToken = value['accessToken'];
  const expiresIn = value['expiresIn'];
  const issuedAt = value['issuedAt'];
  if (typeof accessToken !== 'string' || accessToken.length === 0) return null;
  if (typeof expiresIn !== 'number' || expiresIn <= 0) return null;
  if (typeof issuedAt !== 'number' || issuedAt <= 0) return null;
  const refreshToken = value['refreshToken'];
  const idToken = value['idToken'];
  const tokenType = value['tokenType'];
  const scope = value['scope'];
  return Object.freeze({
    accessToken,
    refreshToken: typeof refreshToken === 'string' && refreshToken.length > 0 ? refreshToken : null,
    idToken: typeof idToken === 'string' && idToken.length > 0 ? idToken : null,
    tokenType: typeof tokenType === 'string' && tokenType.length > 0 ? tokenType : 'Bearer',
    expiresIn,
    scope: typeof scope === 'string' && scope.length > 0 ? scope : null,
    issuedAt,
  });
}

function isExpired(tokens: OidcTokenSet): boolean {
  const expiresAt = tokens.issuedAt + tokens.expiresIn * 1000;
  return Date.now() >= expiresAt;
}

// ─── Claim decoding (best-effort — backend re-validates signature) ────────

function decodePrincipal(tokens: OidcTokenSet, verifiedIdClaims: JWTPayload | null): Principal {
  const accessClaims = decodeJwtPayload(tokens.accessToken);
  // S2-α #11 — when the caller (typically `completeLogin`) supplies
  // jose-verified id_token claims, treat them as the trusted source.
  // Otherwise fall back to a best-effort base64 decode (silent refresh path
  // does not re-verify; `restoreSession` rehydrates from a prior page load).
  const idClaims: Record<string, unknown> | null =
    verifiedIdClaims ?? (tokens.idToken !== null ? decodeJwtPayload(tokens.idToken) : null);

  // OIDC division of responsibility:
  //   - id_token  → user identity (sub, name, email)  ← prefer verified
  //   - access_token → RBAC vocabulary (permissions, scope, roles)
  //                    matching backend HttpAuthConfig claim names.
  // When only one of the two is decodable, fall through to it.
  const identitySource: Record<string, unknown> = idClaims ?? accessClaims ?? {};
  const permissionSource: Record<string, unknown> = accessClaims ?? idClaims ?? {};

  const subject = readString(identitySource[CLAIM_SUBJECT]);
  const name = readNullableString(identitySource[CLAIM_NAME]);
  const email = readNullableString(identitySource[CLAIM_EMAIL]);
  const permissions = readClaimList(permissionSource[CLAIM_PERMISSIONS]);
  const scopes = readClaimList(permissionSource[CLAIM_SCOPE]);
  const roles = readClaimList(permissionSource[CLAIM_ROLES]);

  return Object.freeze({
    subject: subject ?? 'anonymous',
    name,
    email,
    permissions: Object.freeze(permissions),
    scopes: Object.freeze(scopes),
    roles: Object.freeze(roles),
    raw: Object.freeze(identitySource),
  });
}

/** Decode JWT payload without verifying signature — backend (PyJWT) verifies
 *  authoritatively. Returns `null` for opaque (non-JWT) tokens. */
function decodeJwtPayload(token: string): Record<string, unknown> | null {
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  const segment = parts[1];
  if (segment === undefined || segment.length === 0) return null;
  try {
    const padded = segment.padEnd(segment.length + ((4 - (segment.length % 4)) % 4), '=');
    const base64 = padded.replace(/-/g, '+').replace(/_/g, '/');
    const binary = globalThis.atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i);
    }
    const json = new TextDecoder().decode(bytes);
    const parsed: unknown = JSON.parse(json);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function readClaimList(value: unknown): string[] {
  if (value === null || value === undefined || value === '') return [];
  if (typeof value === 'string') {
    return value
      .replace(/,/gu, ' ')
      .split(/\s+/u)
      .map((part) => part.trim())
      .filter((part) => part.length > 0);
  }
  if (Array.isArray(value)) {
    const out: string[] = [];
    for (const item of value) {
      if (typeof item === 'string' && item.length > 0) {
        out.push(item);
      }
    }
    return out;
  }
  return [];
}

function readString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function readNullableString(value: unknown): string | null {
  return readString(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function notify(): void {
  // Snapshot the listener set so a subscriber that unsubscribes during
  // notification (e.g. React effect cleanup) doesn't mutate while iterating.
  for (const listener of [...listeners]) {
    listener();
  }
}

// ─── Test helpers ─────────────────────────────────────────────────────────

/** Test-only — reset the in-memory state and listener set without touching
 *  sessionStorage. Production code must not call this. */
export function __resetAuthStateForTests(value?: AuthState): void {
  cancelRefresh();
  releaseSharedRefresh(); // M4 — module-level slot must not leak across tests.
  sessionEpoch += 1;
  listeners.clear();
  currentState = value ?? { kind: 'unauthenticated', reason: null };
}
