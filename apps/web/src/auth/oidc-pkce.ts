/**
 * OIDC PKCE flow — Sprint S2 + S2-α security hardening.
 *
 * Single source of truth for the public-client (no client secret) PKCE
 * Authorization Code flow against any OIDC-compliant IdP (Keycloak in dev
 * per `infra/docker-compose.idp.yml`; Azure AD / Okta / Auth0 in prod).
 *
 * Industry standards anchored:
 *   - RFC 6749 § 4.1     (Authorization Code Grant)
 *   - RFC 6749 § 10.12   (state CSRF)
 *   - RFC 7636           (PKCE)
 *   - OIDC Core 1.0      (id_token + nonce — § 3.1.2.1, § 3.1.3.7)
 *   - OIDC Discovery 1.0 § 4  (`/.well-known/openid-configuration`)
 *   - RFC 8414           (Authorization Server Metadata)
 *
 * Architectural posture:
 *   - `jose` lib (industry-standard OIDC JWT verification, ~8KB gzip).
 *     "Don't roll your own crypto" — id_token signature/issuer/audience/
 *     exp/nonce checks all go through `jwtVerify` + `createRemoteJWKSet`.
 *   - hand-rolled OAuth2 message framing (URL/body serialisation, PKCE
 *     verifier generation) — keeps bundle pressure low and gives full
 *     control over the W3C `traceparent` chain that Sprint S4 will layer
 *     via `@opentelemetry/instrumentation-fetch`.
 *   - dynamic-discovery — `authorization_endpoint`/`token_endpoint`/
 *     `jwks_uri` are never hardcoded; fetched from the issuer's well-known
 *     URL and cached per page load.
 *   - sessionStorage-only — see `storage-keys.ts`. PKCE transaction state
 *     (state / verifier / nonce / return-to) lives there briefly between
 *     `startLogin` and `completeLogin`; the race-safe `completeLogin`
 *     clears it BEFORE the network round-trip so a page reload mid-flow
 *     cannot replay stale verifier+nonce against the IdP.
 *
 * SPRINT_S4_HOOK: replaced by @opentelemetry/instrumentation-fetch global
 * wrap — no code change required when S4 lands. Token-endpoint fetches +
 * the JWKS fetch (inside jose) all become traceparent-emitting.
 */

import { createRemoteJWKSet, jwtVerify, type JWTPayload } from 'jose';

import { getRuntimeConfig } from '@/config/runtime';

import { OIDC_CLOCK_TOLERANCE_SECONDS } from './session';
import {
  OIDC_FORCE_REAUTH_FLAG_VALUE,
  OIDC_STORAGE_PREFIX,
  STORAGE_KEY_FORCE_REAUTH,
  STORAGE_KEY_MAX_AGE,
  STORAGE_KEY_NONCE,
  STORAGE_KEY_RETURN_TO,
  STORAGE_KEY_STATE,
  STORAGE_KEY_VERIFIER,
} from './storage-keys';

// ─── SSOT constants ────────────────────────────────────────────────────────

/** RFC 8414 / OIDC Discovery 1.0 § 4 well-known path. */
export const OIDC_DISCOVERY_PATH = '/.well-known/openid-configuration';

// Re-export so existing test imports keep compiling without churn.
export { OIDC_STORAGE_PREFIX };

/** RFC 7636 § 4.1 — code_verifier is 43-128 ASCII chars (base64url unreserved
 *  set). 32 random bytes → 43 base64url chars (256-bit entropy). RFC 7636 § 7.1
 *  recommends "at least 256 bits of entropy" — we hit the floor. */
const VERIFIER_RANDOM_BYTES = 32;

/** Sprint S2-γ β-P2-4 + S2-δ γ-P1-4 — anti-replay token entropy SSOT.
 *
 *  Both `state` (RFC 6749 § 10.12 CSRF + RFC 6819 § 5.3.5) and `nonce`
 *  (OIDC Core 1.0 § 3.1.2.1 + § 15.5.2) are random anti-replay tokens
 *  serving structurally identical roles — only the cryptographic
 *  protocol layer that consumes them differs. They MUST share an
 *  entropy budget so a future tightening (e.g. 24 → 32 bytes) updates
 *  both atomically.
 *
 *  Why this is **24 bytes** but `VERIFIER_RANDOM_BYTES` is **32 bytes**:
 *    - state + nonce are anti-replay tokens — OAuth 2.0 Security BCP
 *      requires ≥128 bit entropy; 192-bit (24 bytes) is the industry-
 *      standard ceiling that fits in a single base64url 32-char URL
 *      param without bloating the authorization request.
 *    - PKCE verifier (RFC 7636 § 7.1) explicitly mandates "AT LEAST 256
 *      bits of entropy"; 32 bytes hits the spec floor. Cannot share
 *      the 24-byte budget without violating spec.
 *
 *  24 random bytes → 32 base64url chars (192-bit entropy). */
const OIDC_ANTI_REPLAY_TOKEN_BYTES = 24;
/** RFC 6749 § 10.12 + RFC 6819 § 5.3.5 — CSRF state token. */
const STATE_RANDOM_BYTES = OIDC_ANTI_REPLAY_TOKEN_BYTES;
/** OIDC Core 1.0 § 3.1.2.1 + § 15.5.2 — id_token replay defence. */
const NONCE_RANDOM_BYTES = OIDC_ANTI_REPLAY_TOKEN_BYTES;
/** OIDC PKCE method — only S256 is honoured (plain is deprecated). */
const CODE_CHALLENGE_METHOD = 'S256';

/** Sprint S2-β α-6 + S2-γ β-P1-2 — JWKS resolver options passed to
 *  jose's `createRemoteJWKSet`.
 *
 *  `cooldownDuration` (30s) rate-limits how often jose re-fetches the
 *  JWKS endpoint after encountering an unknown `kid`. Without it, an
 *  attacker (or a misbehaving IdP) could trigger a tight refetch loop
 *  and exhaust the IdP's request budget (OWASP API Security Top 10 —
 *  unrestricted resource consumption). jose's default is 30s — we lock
 *  it explicitly so a future jose major doesn't change behaviour.
 *
 *  `timeoutDuration` (5s) caps each JWKS HTTP fetch. Without it, a hung
 *  IdP stalls the login flow indefinitely. 5s is the median web app
 *  HTTP timeout (PSR-18 / NodeJS default).
 *
 *  Refs:
 *    - jose: https://github.com/panva/jose/blob/main/docs/jwks/remote/functions/createRemoteJWKSet.md
 *    - OWASP API Security Top 10 (2023) — API4 Unrestricted Resource
 *      Consumption: https://owasp.org/API-Security/editions/2023/en/0xa4-unrestricted-resource-consumption/ */
const JWKS_COOLDOWN_MS = 30_000;
const JWKS_TIMEOUT_MS = 5_000;

// Sprint S2-δ γ-P0-1 + γ-P2-12 — `OIDC_CLOCK_TOLERANCE_SECONDS` SSOT
// moved to `./session.ts` so backend cross-check invariant can grep one
// authoritative literal. Re-export for callers; jose accepts a number
// (seconds) directly — string `'30s'` polyfill purged.

// ─── Domain types ──────────────────────────────────────────────────────────

/** Subset of OIDC Discovery doc the PKCE flow needs. */
export interface DiscoveryDocument {
  readonly issuer: string;
  readonly authorization_endpoint: string;
  readonly token_endpoint: string;
  readonly jwks_uri: string;
  readonly end_session_endpoint: string | null;
  readonly code_challenge_methods_supported: readonly string[];
}

/** Decoded OAuth2 token response (RFC 6749 § 5.1). Client-side derived
 *  `issuedAt` is added so `session.ts` can compute the silent-refresh schedule
 *  without re-fetching wall-clock at refresh time. */
export interface OidcTokenSet {
  readonly accessToken: string;
  readonly refreshToken: string | null;
  readonly idToken: string | null;
  readonly tokenType: string;
  readonly expiresIn: number;
  readonly scope: string | null;
  readonly issuedAt: number;
}

export type OidcFlowErrorKind =
  | 'discovery_unreachable'
  | 'discovery_invalid'
  | 'discovery_missing_field'
  | 'pkce_method_unsupported'
  | 'state_mismatch'
  | 'verifier_missing'
  | 'nonce_missing'
  | 'token_endpoint_error'
  | 'token_response_invalid'
  | 'id_token_verification_failed';

export class OidcFlowError extends Error {
  override readonly name = 'OidcFlowError';
  readonly kind: OidcFlowErrorKind;
  override readonly cause: unknown;
  constructor(kind: OidcFlowErrorKind, message: string, cause?: unknown) {
    super(message);
    this.kind = kind;
    this.cause = cause;
  }
}

// ─── Discovery (cached per page load) ──────────────────────────────────────

let discoveryCache: DiscoveryDocument | null = null;
let discoveryInFlight: Promise<DiscoveryDocument> | null = null;
/** JWKS resolver cache keyed by jwks_uri so jose reuses the fetched keys
 *  across multiple id_token verifications in the same page. */
const jwksResolverCache = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

/** Fetch + validate the OIDC discovery document. Result is cached for the
 *  page lifetime; call `__resetDiscoveryCacheForTests` from unit tests. */
export async function discoverOidcConfig(issuerOverride?: string): Promise<DiscoveryDocument> {
  if (discoveryCache && !issuerOverride) {
    return discoveryCache;
  }
  if (discoveryInFlight && !issuerOverride) {
    return discoveryInFlight;
  }
  const issuer = issuerOverride ?? getRuntimeConfig().oidcIssuer;
  const url = joinIssuerPath(issuer, OIDC_DISCOVERY_PATH);

  // Sprint S2-β α-3 — race-safe + reject-recovery. On reject we MUST
  // clear `discoveryInFlight` so a transient IdP outage (DNS / 5xx /
  // network) doesn't permanently poison the cache with a rejected
  // Promise. Without the .catch the next caller would re-throw the
  // cached rejection without retrying.
  const promise = fetchDiscovery(url).then(
    (doc) => {
      if (!issuerOverride) {
        discoveryCache = doc;
      }
      discoveryInFlight = null;
      return doc;
    },
    (err: unknown) => {
      discoveryInFlight = null;
      throw err;
    },
  );
  if (!issuerOverride) {
    discoveryInFlight = promise;
  }
  return promise;
}

async function fetchDiscovery(url: string): Promise<DiscoveryDocument> {
  let response: Response;
  try {
    response = await fetch(url, { headers: { Accept: 'application/json' } });
  } catch (err) {
    throw new OidcFlowError('discovery_unreachable', `OIDC discovery fetch failed: ${url}`, err);
  }
  if (!response.ok) {
    throw new OidcFlowError(
      'discovery_unreachable',
      `OIDC discovery HTTP ${response.status} from ${url}`,
    );
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch (err) {
    throw new OidcFlowError('discovery_invalid', 'OIDC discovery body is not JSON', err);
  }
  return validateDiscovery(payload);
}

function validateDiscovery(payload: unknown): DiscoveryDocument {
  if (!isRecord(payload)) {
    throw new OidcFlowError('discovery_invalid', 'OIDC discovery payload is not an object');
  }
  const required = ['issuer', 'authorization_endpoint', 'token_endpoint', 'jwks_uri'] as const;
  for (const field of required) {
    if (typeof payload[field] !== 'string' || payload[field].length === 0) {
      throw new OidcFlowError(
        'discovery_missing_field',
        `OIDC discovery missing required field: ${field}`,
      );
    }
  }
  const methods = readStringArray(payload['code_challenge_methods_supported']);
  if (methods.length > 0 && !methods.includes(CODE_CHALLENGE_METHOD)) {
    throw new OidcFlowError(
      'pkce_method_unsupported',
      `IdP does not advertise ${CODE_CHALLENGE_METHOD} support`,
    );
  }
  const endSession = payload['end_session_endpoint'];
  return Object.freeze({
    issuer: payload['issuer'] as string,
    authorization_endpoint: payload['authorization_endpoint'] as string,
    token_endpoint: payload['token_endpoint'] as string,
    jwks_uri: payload['jwks_uri'] as string,
    end_session_endpoint:
      typeof endSession === 'string' && endSession.length > 0 ? endSession : null,
    code_challenge_methods_supported: Object.freeze(
      methods.length > 0 ? methods : [CODE_CHALLENGE_METHOD],
    ),
  });
}

// ─── PKCE primitives ───────────────────────────────────────────────────────

/** Generate a {verifier, challenge} pair per RFC 7636. */
export async function generatePkcePair(): Promise<{ verifier: string; challenge: string }> {
  const verifier = base64UrlEncode(randomBytes(VERIFIER_RANDOM_BYTES));
  const challenge = await sha256Base64Url(verifier);
  return { verifier, challenge };
}

/** Generate an OIDC Core 1.0 § 3.1.2.1 nonce. */
export function generateNonce(): string {
  return base64UrlEncode(randomBytes(NONCE_RANDOM_BYTES));
}

function randomBytes(length: number): Uint8Array {
  const buffer = new Uint8Array(length);
  globalThis.crypto.getRandomValues(buffer);
  return buffer;
}

async function sha256Base64Url(value: string): Promise<string> {
  const encoded = new TextEncoder().encode(value);
  const digest = await globalThis.crypto.subtle.digest('SHA-256', encoded);
  return base64UrlEncode(new Uint8Array(digest));
}

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return globalThis.btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/u, '');
}

// ─── High-level flow ───────────────────────────────────────────────────────

export interface StartLoginOptions {
  /** App-relative URL to navigate back to after `completeLogin`. */
  readonly returnTo?: string;
  /** Sprint S2-γ β-P0-2 — OIDC Core 1.0 § 3.1.2.1 `max_age` (in seconds).
   *  When set, the IdP MUST re-authenticate the user if more than `max_age`
   *  seconds have passed since the last authentication, and MUST emit the
   *  `auth_time` claim so `completeLogin` can verify it. Pass when the RP
   *  has a sensitivity boundary (e.g., financial transaction, settings
   *  change) that requires recent authentication. */
  readonly maxAge?: number;
}

/** Begin the Authorization Code + PKCE flow by redirecting to the IdP. */
export async function startLogin(options: StartLoginOptions = {}): Promise<void> {
  const config = getRuntimeConfig();
  const discovery = await discoverOidcConfig(config.oidcIssuer);
  const { verifier, challenge } = await generatePkcePair();
  const state = base64UrlEncode(randomBytes(STATE_RANDOM_BYTES));
  const nonce = generateNonce();

  // S2-α #13 + S2-β α-12: when a previous sign-out asked for IdP
  // re-authentication, add `prompt=login` (OIDC Core 1.0 § 3.1.2.1) so the
  // IdP ignores its SSO cookie. The flag value is a named SSOT constant
  // (`OIDC_FORCE_REAUTH_FLAG_VALUE`) — no magic '1'.
  const forceReauth =
    globalThis.sessionStorage.getItem(STORAGE_KEY_FORCE_REAUTH) === OIDC_FORCE_REAUTH_FLAG_VALUE;
  if (forceReauth) {
    globalThis.sessionStorage.removeItem(STORAGE_KEY_FORCE_REAUTH);
  }

  globalThis.sessionStorage.setItem(STORAGE_KEY_STATE, state);
  globalThis.sessionStorage.setItem(STORAGE_KEY_VERIFIER, verifier);
  globalThis.sessionStorage.setItem(STORAGE_KEY_NONCE, nonce);
  if (options.returnTo) {
    globalThis.sessionStorage.setItem(STORAGE_KEY_RETURN_TO, options.returnTo);
  } else {
    globalThis.sessionStorage.removeItem(STORAGE_KEY_RETURN_TO);
  }
  // S2-γ β-P0-2 — persist max_age so completeLogin can echo it through
  // verifyIdToken without the RP having to re-pass the value.
  if (options.maxAge !== undefined) {
    globalThis.sessionStorage.setItem(STORAGE_KEY_MAX_AGE, String(options.maxAge));
  } else {
    globalThis.sessionStorage.removeItem(STORAGE_KEY_MAX_AGE);
  }

  const params = new URLSearchParams({
    response_type: 'code',
    client_id: config.oidcClientId,
    redirect_uri: config.oidcRedirectUri,
    scope: config.oidcScopes.join(' '),
    state,
    nonce,
    code_challenge: challenge,
    code_challenge_method: CODE_CHALLENGE_METHOD,
  });
  if (forceReauth) {
    params.set('prompt', 'login');
  }
  if (options.maxAge !== undefined) {
    params.set('max_age', String(options.maxAge));
  }
  if (config.oidcAudience) {
    // Auth0 / Azure AD extension — optional and only sent when present so
    // Keycloak default deployments are not affected.
    params.set('audience', config.oidcAudience);
  }

  globalThis.location.assign(`${discovery.authorization_endpoint}?${params.toString()}`);
}

export interface CompleteLoginResult {
  readonly tokens: OidcTokenSet;
  /** App-relative URL to navigate to after successful login. */
  readonly returnTo: string;
  /** id_token claims after jose `jwtVerify` (signature + iss + aud + exp +
   *  nonce all checked). `null` when the IdP did not return an id_token
   *  (e.g. when only `access_token` scope was requested). */
  readonly idTokenClaims: JWTPayload | null;
}

/** Parse the redirect callback URL and exchange the auth code for tokens. */
export async function completeLogin(
  callbackUrl: string = globalThis.location.href,
): Promise<CompleteLoginResult> {
  const config = getRuntimeConfig();
  const params = new URL(callbackUrl).searchParams;
  const code = params.get('code');
  const state = params.get('state');
  const error = params.get('error');

  if (error) {
    throw new OidcFlowError(
      'token_endpoint_error',
      `IdP returned error on authorization redirect: ${error}`,
    );
  }
  if (!code || !state) {
    throw new OidcFlowError('state_mismatch', 'Authorization callback missing code/state');
  }

  // S2-α #12 — RACE FIX. Read and validate every piece of transaction state
  // FIRST, then capture to local consts, then clear sessionStorage
  // IMMEDIATELY, before any network round-trip. A page reload after this
  // point cannot replay the verifier/nonce against the IdP.
  const expectedState = globalThis.sessionStorage.getItem(STORAGE_KEY_STATE);
  const verifier = globalThis.sessionStorage.getItem(STORAGE_KEY_VERIFIER);
  const expectedNonce = globalThis.sessionStorage.getItem(STORAGE_KEY_NONCE);
  const returnTo = globalThis.sessionStorage.getItem(STORAGE_KEY_RETURN_TO) ?? '/';
  // S2-γ β-P0-2 + S2-δ γ-P0-4 + S2-ε δ-P1-5 — echo max_age (if
  // startLogin set it) for verifyIdToken. Strong validation: positive
  // integer + upper bound (30 days). A malformed or absurdly large
  // value is treated as absent rather than silently propagated to the
  // auth_time comparison.
  const MAX_AGE_UPPER_BOUND_SECONDS = 86_400 * 30; // 30 days
  const maxAgeRaw = globalThis.sessionStorage.getItem(STORAGE_KEY_MAX_AGE);
  let maxAge: number | undefined;
  if (maxAgeRaw !== null) {
    const parsed = Number.parseInt(maxAgeRaw, 10);
    if (Number.isInteger(parsed) && parsed > 0 && parsed <= MAX_AGE_UPPER_BOUND_SECONDS) {
      maxAge = parsed;
    } else {
      // Defensive: corrupted sessionStorage. Clear it so a subsequent
      // startLogin doesn't propagate the garbage value.
      globalThis.sessionStorage.removeItem(STORAGE_KEY_MAX_AGE);
    }
  }

  if (!expectedState || state !== expectedState) {
    clearTransactionState();
    throw new OidcFlowError('state_mismatch', 'Authorization state mismatch — possible CSRF');
  }
  if (!verifier) {
    clearTransactionState();
    throw new OidcFlowError('verifier_missing', 'PKCE verifier missing from sessionStorage');
  }
  if (!expectedNonce) {
    clearTransactionState();
    throw new OidcFlowError('nonce_missing', 'OIDC nonce missing from sessionStorage');
  }

  // ── CAPTURED → CLEAR (race-safe) ──
  clearTransactionState();

  // ── NETWORK ROUND-TRIP (post-clear) ──
  const discovery = await discoverOidcConfig(config.oidcIssuer);
  const tokens = await exchangeCode({
    tokenEndpoint: discovery.token_endpoint,
    clientId: config.oidcClientId,
    redirectUri: config.oidcRedirectUri,
    code,
    verifier,
  });

  // ── id_token verification (jose: signature + iss + aud + exp + nonce +
  //    clockTolerance + optional max_age/auth_time) ──
  let idTokenClaims: JWTPayload | null = null;
  if (tokens.idToken !== null) {
    try {
      idTokenClaims = await verifyIdToken(tokens.idToken, discovery, {
        nonce: expectedNonce,
        ...(maxAge !== undefined && Number.isFinite(maxAge) ? { maxAge } : {}),
      });
    } catch (err) {
      throw new OidcFlowError(
        'id_token_verification_failed',
        `id_token verification failed: ${err instanceof Error ? err.message : String(err)}`,
        err,
      );
    }
  }

  return { tokens, returnTo, idTokenClaims };
}

export interface VerifyIdTokenOptions {
  /** Sprint S2-β α-4 — silent-refresh path has no fresh authorization
   *  round-trip and therefore no nonce to check. Set `true` to skip nonce
   *  verification (signature + issuer + audience + exp/iat still
   *  enforced by jose). Default `false` — login callback MUST pass nonce. */
  readonly skipNonce?: boolean;
  /** Required when `skipNonce` is false (default). The value previously
   *  written to `STORAGE_KEY_NONCE` by `startLogin`. */
  readonly nonce?: string;
  /** Sprint S2-γ β-P0-2 — OIDC Core 1.0 § 3.1.2.1 `max_age` parameter
   *  echo. When set, the `auth_time` claim in the verified id_token MUST
   *  satisfy `auth_time >= now - maxAge`. Otherwise the user authenticated
   *  too long ago and a fresh login (with `prompt=login`) is required.
   *  Defends against stolen-refresh-token infinite session extension. */
  readonly maxAge?: number;
}

/** Verify an id_token via jose (signature + iss + aud + exp + iat + nbf
 *  with clock-skew tolerance) and optionally the OIDC nonce + max_age
 *  claims. Exported so `session.ts` can re-verify on the silent-refresh
 *  path (no nonce). */
export async function verifyIdToken(
  idToken: string,
  discovery: DiscoveryDocument,
  options: VerifyIdTokenOptions = {},
): Promise<JWTPayload> {
  const jwks = getOrCreateJwksResolver(discovery.jwks_uri);
  const { payload } = await jwtVerify(idToken, jwks, {
    issuer: discovery.issuer,
    audience: getRuntimeConfig().oidcClientId,
    // S2-γ β-P0-1 — clock skew tolerance (jose default is 0s — brittle).
    clockTolerance: OIDC_CLOCK_TOLERANCE_SECONDS,
  });
  if (!options.skipNonce) {
    // OIDC Core 1.0 § 3.1.3.7 #11 — nonce claim must equal the value sent
    // in the authorization request. jose does not verify nonce.
    if (options.nonce === undefined) {
      throw new OidcFlowError(
        'id_token_verification_failed',
        'verifyIdToken called without nonce while skipNonce=false',
      );
    }
    if (payload['nonce'] !== options.nonce) {
      throw new OidcFlowError(
        'id_token_verification_failed',
        'id_token nonce claim does not match the authorization request',
      );
    }
  }
  if (options.maxAge !== undefined) {
    // S2-γ β-P0-2 — OIDC Core 1.0 § 3.1.2.1 + § 3.1.3.7 #12 — when the RP
    // sent max_age in the authorization request, the id_token MUST contain
    // `auth_time` and that timestamp MUST satisfy:
    //     auth_time + max_age >= now
    // Otherwise the user authenticated too long ago — defense against
    // stolen-refresh-token infinite session extension.
    const authTime = payload['auth_time'];
    if (typeof authTime !== 'number') {
      throw new OidcFlowError(
        'id_token_verification_failed',
        'id_token auth_time claim missing while max_age was requested',
      );
    }
    const nowSeconds = Math.floor(Date.now() / 1000);
    // S2-δ γ-P0-2 — apply the SAME clock-skew tolerance to the auth_time
    // comparison. Server-issued `auth_time` (server clock) vs client
    // `nowSeconds` (client clock) can drift; without the tolerance, a
    // client clock 30s ahead would false-reject a session that just
    // re-authenticated within the max_age window.
    if (authTime + options.maxAge + OIDC_CLOCK_TOLERANCE_SECONDS < nowSeconds) {
      throw new OidcFlowError(
        'id_token_verification_failed',
        `id_token auth_time (${authTime}) older than max_age window (${options.maxAge}s); fresh login required`,
      );
    }
  }
  return payload;
}

function getOrCreateJwksResolver(jwksUri: string) {
  const cached = jwksResolverCache.get(jwksUri);
  if (cached !== undefined) return cached;
  // Sprint S2-β α-6 — JWKS key rotation defence + production timeout.
  // `cooldownDuration` rate-limits JWKS re-fetches when jose encounters
  // an unknown `kid` (preventing DoS against the IdP). `timeoutDuration`
  // bounds the per-fetch latency so a hung IdP doesn't stall login.
  const resolver = createRemoteJWKSet(new URL(jwksUri), {
    cooldownDuration: JWKS_COOLDOWN_MS,
    timeoutDuration: JWKS_TIMEOUT_MS,
  });
  jwksResolverCache.set(jwksUri, resolver);
  return resolver;
}

interface ExchangeArgs {
  readonly tokenEndpoint: string;
  readonly clientId: string;
  readonly redirectUri: string;
  readonly code: string;
  readonly verifier: string;
}

async function exchangeCode(args: ExchangeArgs): Promise<OidcTokenSet> {
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: args.clientId,
    code: args.code,
    redirect_uri: args.redirectUri,
    code_verifier: args.verifier,
  });
  return postToken(args.tokenEndpoint, body);
}

/** Exchange a refresh_token for a new access_token (RFC 6749 § 6). */
export async function refreshTokens(refreshToken: string): Promise<OidcTokenSet> {
  const config = getRuntimeConfig();
  const discovery = await discoverOidcConfig(config.oidcIssuer);
  const body = new URLSearchParams({
    grant_type: 'refresh_token',
    client_id: config.oidcClientId,
    refresh_token: refreshToken,
    // Preserve original scope so the IdP returns a token with the same
    // access surface (Azure AD recommendation; harmless for Keycloak/Auth0).
    scope: config.oidcScopes.join(' '),
  });
  return postToken(discovery.token_endpoint, body);
}

async function postToken(endpoint: string, body: URLSearchParams): Promise<OidcTokenSet> {
  let response: Response;
  try {
    response = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        Accept: 'application/json',
      },
      body,
    });
  } catch (err) {
    throw new OidcFlowError('token_endpoint_error', `Token endpoint unreachable: ${endpoint}`, err);
  }
  if (!response.ok) {
    let detail = '';
    try {
      detail = await response.text();
    } catch {
      // ignore — fall through with empty detail
    }
    throw new OidcFlowError(
      'token_endpoint_error',
      `Token endpoint HTTP ${response.status}: ${detail}`.trim(),
    );
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch (err) {
    throw new OidcFlowError('token_response_invalid', 'Token response is not JSON', err);
  }
  return parseTokenResponse(payload);
}

function parseTokenResponse(payload: unknown): OidcTokenSet {
  if (!isRecord(payload)) {
    throw new OidcFlowError('token_response_invalid', 'Token response is not an object');
  }
  const accessToken = payload['access_token'];
  if (typeof accessToken !== 'string' || accessToken.length === 0) {
    throw new OidcFlowError('token_response_invalid', 'access_token missing or empty');
  }
  const expiresIn = payload['expires_in'];
  if (typeof expiresIn !== 'number' || !Number.isFinite(expiresIn) || expiresIn <= 0) {
    throw new OidcFlowError(
      'token_response_invalid',
      'expires_in missing or not a positive number',
    );
  }
  const refreshToken = payload['refresh_token'];
  const idToken = payload['id_token'];
  const tokenType = payload['token_type'];
  const scope = payload['scope'];
  return Object.freeze({
    accessToken,
    refreshToken: typeof refreshToken === 'string' && refreshToken.length > 0 ? refreshToken : null,
    idToken: typeof idToken === 'string' && idToken.length > 0 ? idToken : null,
    tokenType: typeof tokenType === 'string' && tokenType.length > 0 ? tokenType : 'Bearer',
    expiresIn,
    scope: typeof scope === 'string' && scope.length > 0 ? scope : null,
    issuedAt: Date.now(),
  });
}

export interface SignOutOptions {
  /** id_token to send as `id_token_hint` in the end-session request — lets
   *  the IdP know which session to invalidate. */
  readonly idTokenHint?: string;
  /** Override post-logout redirect; defaults to runtime config value. */
  readonly postLogoutRedirectUri?: string | null;
  /** When `true`, mark the next `startLogin` to send `prompt=login` so the
   *  IdP forces re-authentication even if its SSO cookie survives the
   *  end-session redirect (S2-α #13). */
  readonly forceReauth?: boolean;
}

/** Redirect to the IdP's end_session_endpoint when available, or return false
 *  so the caller can perform a local-only sign-out. */
export async function signOutAtIdp(options: SignOutOptions = {}): Promise<boolean> {
  if (options.forceReauth) {
    globalThis.sessionStorage.setItem(STORAGE_KEY_FORCE_REAUTH, OIDC_FORCE_REAUTH_FLAG_VALUE);
  }
  const config = getRuntimeConfig();
  const discovery = await discoverOidcConfig(config.oidcIssuer);
  if (!discovery.end_session_endpoint) {
    return false;
  }
  const target =
    options.postLogoutRedirectUri === undefined
      ? config.oidcPostLogoutRedirectUri
      : options.postLogoutRedirectUri;
  if (!target) {
    return false;
  }
  const params = new URLSearchParams({
    client_id: config.oidcClientId,
    post_logout_redirect_uri: target,
  });
  if (options.idTokenHint) {
    params.set('id_token_hint', options.idTokenHint);
  }
  globalThis.location.assign(`${discovery.end_session_endpoint}?${params.toString()}`);
  return true;
}

// ─── helpers ───────────────────────────────────────────────────────────────

function joinIssuerPath(issuer: string, path: string): string {
  const trimmed = issuer.endsWith('/') ? issuer.slice(0, -1) : issuer;
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${trimmed}${suffix}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const out: string[] = [];
  for (const item of value) {
    if (typeof item === 'string' && item.length > 0) {
      out.push(item);
    }
  }
  return out;
}

function clearTransactionState(): void {
  globalThis.sessionStorage.removeItem(STORAGE_KEY_STATE);
  globalThis.sessionStorage.removeItem(STORAGE_KEY_VERIFIER);
  globalThis.sessionStorage.removeItem(STORAGE_KEY_NONCE);
  // returnTo is preserved by completeLogin's return value, then cleared
  // here so a subsequent failed login cannot replay an old destination.
  globalThis.sessionStorage.removeItem(STORAGE_KEY_RETURN_TO);
  // S2-γ β-P0-2 — max_age echoed for verifyIdToken's auth_time cross-check.
  globalThis.sessionStorage.removeItem(STORAGE_KEY_MAX_AGE);
}

/** Test-only — clear in-memory discovery cache. Production code must not call this. */
export function __resetDiscoveryCacheForTests(value?: DiscoveryDocument): void {
  discoveryCache = value ?? null;
  discoveryInFlight = null;
  jwksResolverCache.clear();
}
