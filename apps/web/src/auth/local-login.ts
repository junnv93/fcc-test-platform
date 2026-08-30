/**
 * Local (email + password) login — the SECOND auth strategy.
 *
 * ⚠️ **`oidc-pkce.ts` is not imported and not touched.** OIDC remains byte-for-byte
 * what it was; this module sits beside it, and `runtimeConfig.authMode` decides
 * which one a deployment uses. Keycloak is not being replaced — Azure SSO is still
 * the destination, and until the company approves it the two live side by side.
 *
 * ## Why this exists at all
 *
 * `oidc-pkce.ts` builds its PKCE `code_challenge` with
 * `globalThis.crypto.subtle.digest`, and **browsers expose `crypto.subtle` only in
 * a secure context** — `https://`, or `localhost`. A central PC published as
 * `http://10.206.34.233:8080` therefore has `crypto.subtle === undefined` and the
 * login throws `TypeError: Cannot read properties of undefined (reading 'digest')`.
 * That is not a misconfiguration; it is an impossibility, and no setting fixes it.
 *
 * Here the browser performs no cryptography: it posts the password and the SERVER
 * mints the token. That is exactly why EMS runs over plaintext HTTP on the same
 * corporate LAN — its design has the same shape, not by luck.
 *
 * ⚠️ **Plaintext HTTP is not made safe by this.** The browser merely stops
 * refusing. Passwords and bearer tokens travel the LAN in clear text and anyone
 * on the path can read or replay them. TLS remains the only real fix; this module
 * does not substitute for it.
 */
import { getRuntimeConfig } from '@/config/runtime';

import type { OidcTokenSet } from './oidc-pkce';
import type { components } from '@/api/generated/platform-api.types';

/** The subset of `POST /platform/auth/login`'s envelope this module consumes. */
type LocalTokenEnvelope = components['schemas']['LocalAuthTokenEnvelope'];
type LocalLoginRequest = components['schemas']['LocalLoginRequest'];
type LocalChangePasswordRequest = components['schemas']['LocalChangePasswordRequest'];
type LocalLogoutRequest = components['schemas']['LocalLogoutRequest'];

export type LocalLoginFailureKind =
  /** Server said the credentials do not authenticate (401). Covers four cases on
   *  purpose — unknown email, wrong password, disabled and locked account — because
   *  telling them apart would let an attacker enumerate the staff directory. */
  | 'invalid_credentials'
  /** The new password failed the server's password policy (400). */
  | 'password_policy'
  /** The request never got an answer (server down, wrong base URL, DNS). */
  | 'unreachable'
  /** Too many attempts for this account in the current window (429).
   *
   *  ⚠️ Distinct from `unexpected` on purpose. A 429 here is **self-healing in
   *  under a minute** and is not something an administrator can fix, so folding it
   *  into "contact an administrator" both misdirects the tester and — because the
   *  message offers no wait — invites the immediate retry that keeps the window
   *  pinned. The server sends `Retry-After`; this client reads it. */
  | 'throttled'
  /** The server answered, but with something this client cannot act on. */
  | 'unexpected';

export class LocalLoginError extends Error {
  readonly kind: LocalLoginFailureKind;
  /** Whole seconds to wait, for `throttled` only (RFC 9110 §10.2.3).
   *
   *  ⚠️ Declared `| undefined` rather than optional: `exactOptionalPropertyTypes`
   *  is on, so an optional field may not be *assigned* `undefined`. */
  readonly retryAfterSeconds: number | undefined;

  constructor(kind: LocalLoginFailureKind, message: string, retryAfterSeconds?: number) {
    super(message);
    this.name = 'LocalLoginError';
    this.kind = kind;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

/** `Retry-After` in whole seconds, or `undefined` when absent/unparseable.
 *
 *  Only the delta-seconds form is read — that is what this server sends. An
 *  HTTP-date would parse to `NaN`, so the guard keeps the field absent rather
 *  than rendering "wait NaN seconds". */
function retryAfterSecondsOf(response: Response): number | undefined {
  const raw = response.headers.get('Retry-After');
  if (raw === null) return undefined;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

function endpoint(path: string): string {
  const config = getRuntimeConfig();
  const base = config.platformApiBaseUrl ?? config.apiBaseUrl;
  return `${base.replace(/\/+$/u, '')}${path}`;
}

/**
 * ⚠️ **Deliberately a bare `fetch`, not the typed `platformClient`.**
 *
 * Every generated client carries `authRetryMiddleware`, whose `onResponse` treats
 * **any** 401 as "the access token expired": it calls `refreshAuthSession()` and,
 * when that fails, `signOut('token_expired')`. A wrong password is a 401. Routing
 * login through that client would therefore turn a mistyped password into a forced
 * sign-out — and, on the login screen itself, into an error the user cannot read.
 *
 * `signed-download.ts` opts out of the same middleware for the same class of
 * reason, so this is the established idiom rather than a new one.
 */
async function postJson(path: string, body: unknown): Promise<Response> {
  try {
    return await fetch(endpoint(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
    });
  } catch (cause) {
    throw new LocalLoginError('unreachable', `login request failed: ${String(cause)}`);
  }
}

async function readTokenEnvelope(response: Response): Promise<LocalTokenEnvelope> {
  if (response.status === 401) {
    throw new LocalLoginError('invalid_credentials', 'invalid email or password');
  }
  if (response.status === 429) {
    throw new LocalLoginError(
      'throttled',
      'too many attempts for this account',
      retryAfterSecondsOf(response),
    );
  }
  if (!response.ok) {
    throw new LocalLoginError('unexpected', `login failed with status ${response.status}`);
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch (cause) {
    throw new LocalLoginError('unexpected', `login response was not JSON: ${String(cause)}`);
  }
  const envelope = payload as Partial<LocalTokenEnvelope> | null;
  if (
    envelope === null ||
    typeof envelope.access_token !== 'string' ||
    typeof envelope.refresh_token !== 'string' ||
    typeof envelope.expires_in !== 'number'
  ) {
    // ⚠️ Validate before trusting. A malformed envelope that reached
    // `applyTokenSet` would install an "authenticated" state carrying
    // `undefined` as the bearer token, and every later request would 401 with no
    // hint that the LOGIN response was the problem.
    throw new LocalLoginError('unexpected', 'login response did not carry a token pair');
  }
  return envelope as LocalTokenEnvelope;
}

/**
 * Shape a local token envelope as the `OidcTokenSet` the session store speaks.
 *
 * ⚠️ This is an adapter, not a pretence that the token is an OIDC one. Keeping one
 * internal token shape is what lets local login inherit — with no call-site
 * changes — the HTTP middleware, both WebSocket paths, the silent-refresh
 * scheduler and the multi-tab sign-out broadcast. `idToken` is `null` because
 * there is no id_token: nothing in this flow is an OIDC assertion, and claiming
 * otherwise would send `session.ts` down the id_token verification path.
 */
export function toTokenSet(envelope: LocalTokenEnvelope, issuedAt: number): OidcTokenSet {
  return {
    accessToken: envelope.access_token,
    refreshToken: envelope.refresh_token,
    idToken: null,
    tokenType: envelope.token_type || 'Bearer',
    expiresIn: envelope.expires_in,
    scope: null,
    issuedAt,
  };
}

/** Route a signed-in operator must land on when a password change is pending. */
export const LOCAL_PASSWORD_CHANGE_ROUTE = '/change-password';

/**
 * Where to send a freshly signed-in operator. **Pure** — no navigation here.
 *
 * Separating the decision from the side effect is EMS's own lesson
 * (`apps/frontend/lib/auth/login-destination.ts`): jsdom cannot observe a
 * navigation, so a decision fused with `navigate()` is untestable exactly where
 * the security rules live.
 *
 * Two rules, both load-bearing:
 *
 * 1. **A pending password change wins over everything.** It is not a suggestion —
 *    the bootstrap password sits in an environment variable, a compose file and
 *    the deploy log, so an operator allowed to skip past this screen would keep
 *    that credential forever. The server enforces it too (every other operation
 *    answers `AUTH_PASSWORD_CHANGE_REQUIRED`); this is the half that makes the
 *    enforcement navigable instead of a dead end.
 * 2. **`returnTo` is only ever a same-document path.** Trusting it would make the
 *    login screen an open redirect: a crafted link could bounce a
 *    just-authenticated operator, bearer token in hand, to an attacker's page.
 *    `//evil.example` is protocol-relative and leaves the origin, so it is
 *    refused alongside absolute URLs.
 */
export function resolveLoginDestination(forcePasswordChange: boolean, returnTo: unknown): string {
  if (forcePasswordChange) return LOCAL_PASSWORD_CHANGE_ROUTE;
  if (typeof returnTo !== 'string') return '/';
  // ⚠️ Backslashes are normalised to '/' by browsers, so '/\\evil.example' is a
  // protocol-relative URL wearing a disguise — a slash-check alone lets it past.
  // Not reachable today (returnTo comes from router state, not the URL), but the
  // filter must not be relied on while carrying a hole.
  const normalised = returnTo.replace(/\\/gu, '/');
  // ⚠️ `charAt(1)` rather than a literal two-slash prefix check: the same
  // predicate, but the string form trips a repo-wide TS lexer seal that reads an
  // in-string slash pair as a surviving comment. Same meaning — a one-character
  // path has no index 1, so a bare '/' is still accepted.
  if (!normalised.startsWith('/') || normalised.charAt(1) === '/') return '/';
  return returnTo;
}

/** Exchange credentials for a token set. Throws {@link LocalLoginError}. */
export interface LocalLoginResult {
  readonly tokens: OidcTokenSet;
  /** Server's verdict, not the client's guess — the token carries it as a claim
   *  and every protected operation enforces it independently. */
  readonly forcePasswordChange: boolean;
}

export async function localLogin(
  email: string,
  password: string,
  now: () => number = Date.now,
): Promise<LocalLoginResult> {
  const body: LocalLoginRequest = { email, password };
  const response = await postJson('/platform/auth/login', body);
  const envelope = await readTokenEnvelope(response);
  return {
    tokens: toTokenSet(envelope, now()),
    forcePasswordChange: envelope.force_password_change === true,
  };
}

/** Change the caller's own password; returns the fresh token pair. */
export async function localChangePassword(
  accessToken: string,
  currentPassword: string,
  newPassword: string,
  now: () => number = Date.now,
): Promise<OidcTokenSet> {
  let response: Response;
  try {
    response = await fetch(endpoint('/platform/auth/change-password'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      } satisfies LocalChangePasswordRequest),
    });
  } catch (cause) {
    throw new LocalLoginError('unreachable', `change-password failed: ${String(cause)}`);
  }
  if (response.status === 400) {
    // The new password did not satisfy the policy. Distinct from 401 (the CURRENT
    // password was wrong) — telling the user which of the two they got wrong is
    // safe here, because they have already authenticated.
    throw new LocalLoginError('password_policy', 'the new password does not satisfy the policy');
  }
  return toTokenSet(await readTokenEnvelope(response), now());
}

/** Exchange a refresh token for a fresh pair. Throws {@link LocalLoginError}. */
export async function localRefresh(
  refreshToken: string,
  now: () => number = Date.now,
): Promise<OidcTokenSet> {
  const response = await postJson('/platform/auth/refresh', {
    refresh_token: refreshToken,
  });
  return toTokenSet(await readTokenEnvelope(response), now());
}

/**
 * Tell the server to revoke the presented access token.
 *
 * ⚠️ **Never throws.** A logout that can fail leaves the user believing they are
 * signed out while they are not — the worst possible outcome for this operation.
 * The local session is cleared by the caller regardless of what the server says.
 */
export async function localLogout(
  accessToken: string,
  refreshToken: string | null = null,
): Promise<void> {
  try {
    await fetch(endpoint('/platform/auth/logout'), {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${accessToken}`,
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      // ⚠️ The refresh token MUST be sent. Retiring only the access token ends
      // nothing: a refresh token captured off the plaintext LAN keeps minting
      // fresh sessions for its whole 7-day life, straight through a sign-out.
      body: JSON.stringify({ refresh_token: refreshToken ?? '' } satisfies LocalLogoutRequest),
    });
  } catch {
    // Intentionally swallowed — see the note above.
  }
}
