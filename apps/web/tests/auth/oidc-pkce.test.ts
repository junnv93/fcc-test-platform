import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  __resetDiscoveryCacheForTests,
  OIDC_DISCOVERY_PATH,
  OIDC_STORAGE_PREFIX,
  OidcFlowError,
  completeLogin,
  discoverOidcConfig,
  generateNonce,
  generatePkcePair,
  refreshTokens,
  signOutAtIdp,
  startLogin,
  verifyIdToken,
  type DiscoveryDocument,
} from '@/auth/oidc-pkce';
import { OIDC_CLOCK_TOLERANCE_SECONDS } from '@/auth/session';
import {
  OIDC_FORCE_REAUTH_FLAG_VALUE,
  STORAGE_KEY_FORCE_REAUTH,
  STORAGE_KEY_NONCE,
} from '@/auth/storage-keys';
import { __resetRuntimeConfigCacheForTests } from '@/config/runtime';

// `jose` is exercised by the e2e Playwright spec against a real Keycloak.
// In unit tests we replace it with deterministic mocks so token verification
// becomes a pure function of the stubbed claims (no signing key handling).
//
// Sprint S2-β α-5 — `vi.hoisted` lifts the mock-state object alongside the
// hoisted `vi.mock` factory so per-test `.mockResolvedValueOnce` /
// `.mockRejectedValueOnce` overrides for failure modes are addressable.
// `JWTPayload`-like shape — keep loose so per-test mocks can include
// `auth_time`, omit `name`, etc., without TS narrowing complaints.
interface MockJWTVerifyResult {
  payload: Record<string, unknown>;
  protectedHeader: Record<string, unknown>;
}
const joseMockState = vi.hoisted(() => ({
  resolverSpy: vi.fn(() => 'mock-jwks-resolver'),
  verifySpy: vi.fn(
    (_token: string, _jwks: unknown, _opts: unknown): Promise<MockJWTVerifyResult> =>
      Promise.resolve({
        payload: { sub: 'u-1', name: 'Mock User', nonce: 'THE_NONCE' },
        protectedHeader: { alg: 'RS256' },
      }),
  ),
}));
vi.mock('jose', () => ({
  createRemoteJWKSet: joseMockState.resolverSpy,
  jwtVerify: joseMockState.verifySpy,
}));

const TEST_CONFIG = {
  apiBaseUrl: 'http://127.0.0.1:8000',
  platformApiBaseUrl: null,
  wsBaseUrl: 'ws://127.0.0.1:8000',
  sessionApiEnabled: true,
  oidcIssuer: 'http://localhost:8081/realms/fcc-dev',
  oidcClientId: 'fcc-platform-frontend',
  oidcRedirectUri: 'http://localhost:5173/auth/callback',
  oidcAudience: '',
  oidcScopes: ['openid', 'profile', 'email'],
  oidcPostLogoutRedirectUri: 'http://localhost:5173/',
  insecureTransportAllowed: false,
  authMode: 'oidc' as const,
  environmentName: 'test' as const,
  buildVersion: '0.1.0-test',
  buildSha256: '0'.repeat(64),
  sentryDsn: null,
  otelCollectorUrl: null,
  traceSampleRatio: 0,
  featureFlags: {
    providerDiagnosticMode: false,
    sessionReplay: false,
    betaResultBrowser: false,
  },
};

const DISCOVERY_DOC = {
  issuer: 'http://localhost:8081/realms/fcc-dev',
  authorization_endpoint: 'http://localhost:8081/realms/fcc-dev/protocol/openid-connect/auth',
  token_endpoint: 'http://localhost:8081/realms/fcc-dev/protocol/openid-connect/token',
  end_session_endpoint: 'http://localhost:8081/realms/fcc-dev/protocol/openid-connect/logout',
  jwks_uri: 'http://localhost:8081/realms/fcc-dev/protocol/openid-connect/certs',
  code_challenge_methods_supported: ['S256', 'plain'],
};

function jsonResponse(payload: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
}

let assignedUrls: string[];

beforeEach(() => {
  // Sprint S2-γ β-P1-4 + S2-δ γ-P1-5 — reset jose mock state between
  // tests so `mockResolvedValueOnce` / `mockRejectedValueOnce` queues
  // from a previous test cannot leak into the current one. We use
  // `mockReset` (not `mockClear`) because we must wipe BOTH the call
  // history AND the implementation queue — `mockClear` only clears
  // call history, leaving stacked `mockResolvedValueOnce` overrides
  // intact. After reset, we explicitly re-install the default success
  // implementation so any test that doesn't override gets the canonical
  // verified-claims payload.
  joseMockState.verifySpy.mockReset();
  joseMockState.verifySpy.mockImplementation(
    (_token: string, _jwks: unknown, _opts: unknown): Promise<MockJWTVerifyResult> =>
      Promise.resolve({
        payload: { sub: 'u-1', name: 'Mock User', nonce: 'THE_NONCE' },
        protectedHeader: { alg: 'RS256' },
      }),
  );
  joseMockState.resolverSpy.mockReset();
  joseMockState.resolverSpy.mockImplementation(() => 'mock-jwks-resolver');

  __resetRuntimeConfigCacheForTests(TEST_CONFIG);
  __resetDiscoveryCacheForTests();
  globalThis.sessionStorage.clear();
  assignedUrls = [];
  // jsdom's window.location.assign is a noop that just records the URL.
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: {
      href: 'http://localhost:5173/',
      pathname: '/',
      search: '',
      hash: '',
      assign: (url: string) => assignedUrls.push(url),
      replace: (url: string) => assignedUrls.push(url),
    },
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  __resetRuntimeConfigCacheForTests();
});

describe('SSOT constants', () => {
  it('OIDC_DISCOVERY_PATH matches RFC 8414', () => {
    expect(OIDC_DISCOVERY_PATH).toBe('/.well-known/openid-configuration');
  });

  it("OIDC_STORAGE_PREFIX is the new 'fcc-oidc' prefix (legacy fcc-platform-shell-oidc is purged)", () => {
    expect(OIDC_STORAGE_PREFIX).toBe('fcc-oidc');
    expect(OIDC_STORAGE_PREFIX).not.toContain('platform-shell');
  });
});

describe('discoverOidcConfig', () => {
  it('fetches and validates the discovery document', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(DISCOVERY_DOC));
    const doc = await discoverOidcConfig();
    expect(doc.authorization_endpoint).toBe(DISCOVERY_DOC.authorization_endpoint);
    expect(doc.token_endpoint).toBe(DISCOVERY_DOC.token_endpoint);
    expect(doc.code_challenge_methods_supported).toContain('S256');
  });

  it('caches discovery for the page lifetime', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(DISCOVERY_DOC));
    await discoverOidcConfig();
    await discoverOidcConfig();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('throws OidcFlowError with kind=discovery_unreachable on network failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('net'));
    await expect(discoverOidcConfig()).rejects.toBeInstanceOf(OidcFlowError);
    await expect(discoverOidcConfig()).rejects.toMatchObject({ kind: 'discovery_unreachable' });
  });

  it('throws discovery_missing_field when required keys are missing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ issuer: 'x' }));
    await expect(discoverOidcConfig()).rejects.toMatchObject({ kind: 'discovery_missing_field' });
  });

  it('throws pkce_method_unsupported when S256 is not advertised', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ ...DISCOVERY_DOC, code_challenge_methods_supported: ['plain'] }),
    );
    await expect(discoverOidcConfig()).rejects.toMatchObject({ kind: 'pkce_method_unsupported' });
  });

  it('builds the discovery URL by joining the issuer with the well-known path', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(DISCOVERY_DOC));
    await discoverOidcConfig();
    const url = spy.mock.calls[0]?.[0];
    expect(url).toBe(`${TEST_CONFIG.oidcIssuer}/.well-known/openid-configuration`);
  });
});

describe('generatePkcePair', () => {
  it('produces a verifier ≥43 chars and a SHA-256 challenge that matches it', async () => {
    const { verifier, challenge } = await generatePkcePair();
    expect(verifier.length).toBeGreaterThanOrEqual(43);
    // Recompute the challenge independently and assert byte-identity.
    const encoded = new TextEncoder().encode(verifier);
    const digest = await globalThis.crypto.subtle.digest('SHA-256', encoded);
    const bytes = new Uint8Array(digest);
    let binary = '';
    bytes.forEach((b) => (binary += String.fromCharCode(b)));
    const expected = globalThis
      .btoa(binary)
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/u, '');
    expect(challenge).toBe(expected);
  });

  it('produces a different verifier on each call (entropy check)', async () => {
    const a = await generatePkcePair();
    const b = await generatePkcePair();
    expect(a.verifier).not.toBe(b.verifier);
    expect(a.challenge).not.toBe(b.challenge);
  });
});

describe('startLogin', () => {
  beforeEach(() => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(DISCOVERY_DOC));
  });

  it('persists state + verifier + nonce in sessionStorage and redirects to authorization_endpoint', async () => {
    await startLogin({ returnTo: '/sessions' });
    expect(sessionStorage.getItem(`${OIDC_STORAGE_PREFIX}:state`)).toBeTruthy();
    expect(sessionStorage.getItem(`${OIDC_STORAGE_PREFIX}:verifier`)).toBeTruthy();
    expect(sessionStorage.getItem(STORAGE_KEY_NONCE)).toBeTruthy();
    expect(sessionStorage.getItem(`${OIDC_STORAGE_PREFIX}:return-to`)).toBe('/sessions');
    expect(assignedUrls).toHaveLength(1);
    const target = new URL(assignedUrls[0] ?? '');
    expect(target.origin + target.pathname).toBe(DISCOVERY_DOC.authorization_endpoint);
    expect(target.searchParams.get('response_type')).toBe('code');
    expect(target.searchParams.get('client_id')).toBe(TEST_CONFIG.oidcClientId);
    expect(target.searchParams.get('redirect_uri')).toBe(TEST_CONFIG.oidcRedirectUri);
    expect(target.searchParams.get('code_challenge_method')).toBe('S256');
    expect(target.searchParams.get('scope')).toBe('openid profile email');
    expect(target.searchParams.get('audience')).toBeNull();
    // S2-α #10 — nonce SHALL be present (OIDC Core 1.0 § 3.1.2.1).
    expect(target.searchParams.get('nonce')).toBe(sessionStorage.getItem(STORAGE_KEY_NONCE));
    // S2-α #13 — prompt param absent on a normal startLogin.
    expect(target.searchParams.get('prompt')).toBeNull();
  });

  it('appends `audience` only when configured', async () => {
    __resetRuntimeConfigCacheForTests({ ...TEST_CONFIG, oidcAudience: 'https://api.fcc.test' });
    await startLogin();
    const target = new URL(assignedUrls[0] ?? '');
    expect(target.searchParams.get('audience')).toBe('https://api.fcc.test');
  });

  it('adds `prompt=login` and consumes the flag when force-reauth is set (S2-α #13 + S2-β α-12 semantic)', async () => {
    sessionStorage.setItem(STORAGE_KEY_FORCE_REAUTH, OIDC_FORCE_REAUTH_FLAG_VALUE);
    await startLogin();
    const target = new URL(assignedUrls[0] ?? '');
    expect(target.searchParams.get('prompt')).toBe('login');
    // Flag is consumed so a subsequent normal login is silent.
    expect(sessionStorage.getItem(STORAGE_KEY_FORCE_REAUTH)).toBeNull();
  });
});

describe('generateNonce (S2-α #10)', () => {
  it('produces a high-entropy nonce of at least 32 base64url chars', () => {
    const nonce = generateNonce();
    expect(nonce.length).toBeGreaterThanOrEqual(32);
    expect(nonce).toMatch(/^[A-Za-z0-9_-]+$/u);
  });

  it('returns a different value on each call', () => {
    expect(generateNonce()).not.toBe(generateNonce());
  });
});

describe('completeLogin', () => {
  const callbackUrl = 'http://localhost:5173/auth/callback?code=AUTH_CODE&state=THE_STATE';

  beforeEach(() => {
    sessionStorage.setItem(`${OIDC_STORAGE_PREFIX}:state`, 'THE_STATE');
    sessionStorage.setItem(`${OIDC_STORAGE_PREFIX}:verifier`, 'THE_VERIFIER');
    sessionStorage.setItem(STORAGE_KEY_NONCE, 'THE_NONCE');
    sessionStorage.setItem(`${OIDC_STORAGE_PREFIX}:return-to`, '/sessions');
  });

  it('exchanges code for tokens, verifies id_token, returns captured returnTo + verified claims', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(DISCOVERY_DOC))
      .mockResolvedValueOnce(
        jsonResponse({
          access_token: 'ACC',
          refresh_token: 'REF',
          id_token: 'IDT',
          token_type: 'Bearer',
          expires_in: 600,
          scope: 'openid profile',
        }),
      );
    const result = await completeLogin(callbackUrl);
    expect(result.tokens.accessToken).toBe('ACC');
    expect(result.tokens.refreshToken).toBe('REF');
    expect(result.tokens.expiresIn).toBe(600);
    expect(result.returnTo).toBe('/sessions');
    // S2-α #11 — verified id_token claims surface to caller.
    expect(result.idTokenClaims).not.toBeNull();
    expect(result.idTokenClaims?.sub).toBe('u-1');
    // S2-α #12 — transaction state cleared BEFORE the token-exchange fetch.
    expect(sessionStorage.getItem(`${OIDC_STORAGE_PREFIX}:state`)).toBeNull();
    expect(sessionStorage.getItem(`${OIDC_STORAGE_PREFIX}:verifier`)).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY_NONCE)).toBeNull();
    expect(sessionStorage.getItem(`${OIDC_STORAGE_PREFIX}:return-to`)).toBeNull();
    // Token request shape.
    const tokenCall = fetchSpy.mock.calls[1];
    expect(tokenCall?.[1]?.method).toBe('POST');
    const body = (tokenCall?.[1]?.body as URLSearchParams) ?? new URLSearchParams();
    expect(body.get('grant_type')).toBe('authorization_code');
    expect(body.get('code')).toBe('AUTH_CODE');
    expect(body.get('code_verifier')).toBe('THE_VERIFIER');
  });

  it('clears transaction state BEFORE the network round-trip (S2-α #12 race fix)', async () => {
    // Hook the token-exchange fetch with a deferred resolver so we can
    // inspect sessionStorage *during* the await and prove the clear happens
    // before the network call resolves.
    let resolveTokenFetch: ((value: Response) => void) | undefined;
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(DISCOVERY_DOC))
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            // At this point, the race-fix code path has already cleared
            // sessionStorage. We assert here, before resolving the response.
            expect(sessionStorage.getItem(`${OIDC_STORAGE_PREFIX}:state`)).toBeNull();
            expect(sessionStorage.getItem(`${OIDC_STORAGE_PREFIX}:verifier`)).toBeNull();
            expect(sessionStorage.getItem(STORAGE_KEY_NONCE)).toBeNull();
            resolveTokenFetch = resolve;
          }),
      );
    const completion = completeLogin(callbackUrl);
    // Yield to allow the inner async to reach the deferred fetch.
    await new Promise((r) => setTimeout(r, 10));
    expect(resolveTokenFetch).toBeDefined();
    resolveTokenFetch?.(jsonResponse({ access_token: 'ACC', expires_in: 60, id_token: 'IDT' }));
    await completion;
  });

  it('rejects with nonce_missing when sessionStorage nonce is absent (S2-α #10)', async () => {
    sessionStorage.removeItem(STORAGE_KEY_NONCE);
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(DISCOVERY_DOC));
    await expect(completeLogin(callbackUrl)).rejects.toMatchObject({ kind: 'nonce_missing' });
    // Even on failure, sessionStorage is purged so a replay cannot succeed.
    expect(sessionStorage.getItem(`${OIDC_STORAGE_PREFIX}:state`)).toBeNull();
  });

  it('rejects with state_mismatch when the callback state does not match', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(DISCOVERY_DOC));
    const bad = 'http://localhost:5173/auth/callback?code=AUTH_CODE&state=WRONG';
    await expect(completeLogin(bad)).rejects.toMatchObject({ kind: 'state_mismatch' });
    // Transaction state is purged so a replay is impossible.
    expect(sessionStorage.getItem(`${OIDC_STORAGE_PREFIX}:state`)).toBeNull();
  });

  it('rejects when the IdP returned an error parameter', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(DISCOVERY_DOC));
    const bad = 'http://localhost:5173/auth/callback?error=access_denied';
    await expect(completeLogin(bad)).rejects.toMatchObject({ kind: 'token_endpoint_error' });
  });

  it('rejects with token_endpoint_error on non-2xx token response', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(DISCOVERY_DOC))
      .mockResolvedValueOnce(new Response('invalid_grant', { status: 400 }));
    await expect(completeLogin(callbackUrl)).rejects.toMatchObject({
      kind: 'token_endpoint_error',
    });
  });

  it('rejects with token_response_invalid when access_token is missing', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(DISCOVERY_DOC))
      .mockResolvedValueOnce(jsonResponse({ token_type: 'Bearer', expires_in: 60 }));
    await expect(completeLogin(callbackUrl)).rejects.toMatchObject({
      kind: 'token_response_invalid',
    });
  });
});

describe('id_token verification failure modes (S2-β α-5)', () => {
  const callbackUrl = 'http://localhost:5173/auth/callback?code=AUTH_CODE&state=THE_STATE';

  beforeEach(() => {
    sessionStorage.setItem(`${OIDC_STORAGE_PREFIX}:state`, 'THE_STATE');
    sessionStorage.setItem(`${OIDC_STORAGE_PREFIX}:verifier`, 'THE_VERIFIER');
    sessionStorage.setItem(STORAGE_KEY_NONCE, 'THE_NONCE');
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(DISCOVERY_DOC))
      .mockResolvedValueOnce(
        jsonResponse({
          access_token: 'ACC',
          refresh_token: 'REF',
          id_token: 'IDT',
          token_type: 'Bearer',
          expires_in: 600,
        }),
      );
  });

  it('rejects with id_token_verification_failed when jose throws (signature invalid)', async () => {
    joseMockState.verifySpy.mockRejectedValueOnce(new Error('signature verification failed'));
    await expect(completeLogin(callbackUrl)).rejects.toMatchObject({
      kind: 'id_token_verification_failed',
    });
  });

  it('rejects with id_token_verification_failed when nonce claim mismatches', async () => {
    joseMockState.verifySpy.mockResolvedValueOnce({
      payload: { sub: 'u-1', name: 'Mock', nonce: 'WRONG_NONCE' },
      protectedHeader: { alg: 'RS256' },
    });
    await expect(completeLogin(callbackUrl)).rejects.toMatchObject({
      kind: 'id_token_verification_failed',
    });
  });

  it('rejects with id_token_verification_failed when jose throws on expired iat', async () => {
    joseMockState.verifySpy.mockRejectedValueOnce(
      Object.assign(new Error('"exp" claim timestamp check failed'), { code: 'ERR_JWT_EXPIRED' }),
    );
    await expect(completeLogin(callbackUrl)).rejects.toMatchObject({
      kind: 'id_token_verification_failed',
    });
  });
});

describe('JWKS resolver cache (S2-β α-5 + α-6)', () => {
  it('reuses the same JWKS resolver instance for repeated verifications', async () => {
    // Call verifyIdToken directly so the test isolates the resolver cache
    // from the unrelated state/verifier/discovery/token-exchange machinery.
    joseMockState.resolverSpy.mockClear();
    const discovery: DiscoveryDocument = Object.freeze({
      issuer: 'http://test/issuer',
      authorization_endpoint: 'http://test/auth',
      token_endpoint: 'http://test/token',
      jwks_uri: 'http://test/jwks',
      end_session_endpoint: null,
      code_challenge_methods_supported: Object.freeze(['S256']),
    });
    await verifyIdToken('TOKEN_A', discovery, { nonce: 'THE_NONCE' });
    await verifyIdToken('TOKEN_B', discovery, { nonce: 'THE_NONCE' });
    expect(joseMockState.resolverSpy).toHaveBeenCalledTimes(1);
  });

  it('configures createRemoteJWKSet with cooldown + timeout options (α-6)', async () => {
    joseMockState.resolverSpy.mockClear();
    const discovery: DiscoveryDocument = Object.freeze({
      issuer: 'http://other/issuer',
      authorization_endpoint: 'http://other/auth',
      token_endpoint: 'http://other/token',
      jwks_uri: 'http://other/jwks-fresh',
      end_session_endpoint: null,
      code_challenge_methods_supported: Object.freeze(['S256']),
    });
    await verifyIdToken('TOKEN', discovery, { nonce: 'THE_NONCE' });
    // Inspect the second positional arg jose received — the options bag.
    // resolverSpy's mocked signature is `(url) => ...`; the actual call
    // also passes options, so we read it back through `mock.calls`.
    const call: unknown[] = joseMockState.resolverSpy.mock.calls[0] ?? [];
    const opts = call[1] as { cooldownDuration?: number; timeoutDuration?: number } | undefined;
    expect(opts?.cooldownDuration).toBeGreaterThan(0);
    expect(opts?.timeoutDuration).toBeGreaterThan(0);
  });
});

describe('clock skew + max_age + auth_time (S2-γ β-P0-1 β-P0-2)', () => {
  const baseDiscovery: DiscoveryDocument = Object.freeze({
    issuer: 'http://test/issuer',
    authorization_endpoint: 'http://test/auth',
    token_endpoint: 'http://test/token',
    jwks_uri: 'http://test/jwks',
    end_session_endpoint: null,
    code_challenge_methods_supported: Object.freeze(['S256']),
  });

  it('passes clockTolerance to jose.jwtVerify (β-P0-1 + S2-δ γ-P0-1)', async () => {
    joseMockState.verifySpy.mockClear();
    joseMockState.verifySpy.mockResolvedValueOnce({
      payload: { sub: 'u-1', name: 'M', nonce: 'N' },
      protectedHeader: { alg: 'RS256' },
    });
    await verifyIdToken('TOK', baseDiscovery, { nonce: 'N' });
    const opts = joseMockState.verifySpy.mock.calls[0]?.[2] as
      | { clockTolerance?: number }
      | undefined;
    // S2-δ γ-P2-12 — number (seconds), not string. jose accepts both
    // but number is type-safe and unit-explicit.
    //
    // S2-ζ ζ-3 — import the SSOT constant instead of a hardcoded literal.
    // γ-P1-1 changed the value 30→60 (Auth0 default) but this test was
    // missed in that sprint — caught now during the first actual vitest
    // run inside the harness lifecycle. The constant import keeps test
    // and source aligned through any future tuning.
    expect(opts?.clockTolerance).toBe(OIDC_CLOCK_TOLERANCE_SECONDS);
    expect(typeof opts?.clockTolerance).toBe('number');
  });

  it('startLogin echoes max_age into the authorization URL (β-P0-2)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(DISCOVERY_DOC));
    await startLogin({ maxAge: 300 });
    const target = new URL(assignedUrls[0] ?? '');
    expect(target.searchParams.get('max_age')).toBe('300');
  });

  it('startLogin without maxAge does not include the param', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(DISCOVERY_DOC));
    await startLogin();
    const target = new URL(assignedUrls[0] ?? '');
    expect(target.searchParams.get('max_age')).toBeNull();
  });

  it('verifyIdToken rejects when auth_time is older than max_age', async () => {
    // auth_time = "1 hour ago", max_age = 60s → must reject.
    const nowSec = Math.floor(Date.now() / 1000);
    joseMockState.verifySpy.mockResolvedValueOnce({
      payload: { sub: 'u-1', nonce: 'N', auth_time: nowSec - 3600 },
      protectedHeader: { alg: 'RS256' },
    });
    await expect(
      verifyIdToken('TOK', baseDiscovery, { nonce: 'N', maxAge: 60 }),
    ).rejects.toMatchObject({ kind: 'id_token_verification_failed' });
  });

  it('verifyIdToken rejects when auth_time is missing while maxAge is set', async () => {
    joseMockState.verifySpy.mockResolvedValueOnce({
      payload: { sub: 'u-1', nonce: 'N' }, // no auth_time
      protectedHeader: { alg: 'RS256' },
    });
    await expect(
      verifyIdToken('TOK', baseDiscovery, { nonce: 'N', maxAge: 60 }),
    ).rejects.toMatchObject({ kind: 'id_token_verification_failed' });
  });

  it('verifyIdToken accepts auth_time inside the max_age window', async () => {
    const nowSec = Math.floor(Date.now() / 1000);
    joseMockState.verifySpy.mockResolvedValueOnce({
      payload: { sub: 'u-1', nonce: 'N', auth_time: nowSec - 30 },
      protectedHeader: { alg: 'RS256' },
    });
    const claims = await verifyIdToken('TOK', baseDiscovery, { nonce: 'N', maxAge: 60 });
    expect(claims.sub).toBe('u-1');
  });
});

describe('verifyIdToken skipNonce path (S2-β α-4)', () => {
  it('skips nonce check when skipNonce=true (silent-refresh path)', async () => {
    joseMockState.verifySpy.mockResolvedValueOnce({
      payload: { sub: 'u-1', name: 'Refresh', nonce: 'WHATEVER' },
      protectedHeader: { alg: 'RS256' },
    });
    const discovery: DiscoveryDocument = Object.freeze({
      issuer: 'http://refresh/issuer',
      authorization_endpoint: 'http://refresh/auth',
      token_endpoint: 'http://refresh/token',
      jwks_uri: 'http://refresh/jwks',
      end_session_endpoint: null,
      code_challenge_methods_supported: Object.freeze(['S256']),
    });
    const claims = await verifyIdToken('TOKEN', discovery, { skipNonce: true });
    expect(claims.sub).toBe('u-1');
    // No throw even though nonce mismatches (because we skipped the check).
  });

  it('throws when nonce option is missing AND skipNonce is false', async () => {
    const discovery: DiscoveryDocument = Object.freeze({
      issuer: 'http://miss/issuer',
      authorization_endpoint: 'http://miss/auth',
      token_endpoint: 'http://miss/token',
      jwks_uri: 'http://miss/jwks',
      end_session_endpoint: null,
      code_challenge_methods_supported: Object.freeze(['S256']),
    });
    await expect(verifyIdToken('TOKEN', discovery, {})).rejects.toMatchObject({
      kind: 'id_token_verification_failed',
    });
  });
});

describe('legacy storage migration (S2-β α-7 + S2-ε δ-P0-1)', () => {
  it('purgeLegacyStorage removes every fcc-platform-shell-oidc:* key', async () => {
    const { purgeLegacyStorage, LEGACY_OIDC_STORAGE_PREFIX } = await import('@/auth/storage-keys');
    sessionStorage.setItem(`${LEGACY_OIDC_STORAGE_PREFIX}:state`, 'old-state');
    sessionStorage.setItem(`${LEGACY_OIDC_STORAGE_PREFIX}:verifier`, 'old-verifier');
    sessionStorage.setItem(`${LEGACY_OIDC_STORAGE_PREFIX}:bearer-token`, 'old-token');
    sessionStorage.setItem('unrelated-key', 'keep-me');

    const removed = purgeLegacyStorage();
    expect(removed).toBe(3);
    expect(sessionStorage.getItem(`${LEGACY_OIDC_STORAGE_PREFIX}:state`)).toBeNull();
    expect(sessionStorage.getItem(`${LEGACY_OIDC_STORAGE_PREFIX}:verifier`)).toBeNull();
    expect(sessionStorage.getItem(`${LEGACY_OIDC_STORAGE_PREFIX}:bearer-token`)).toBeNull();
    expect(sessionStorage.getItem('unrelated-key')).toBe('keep-me');
  });

  it('purgeLegacyStorage is a no-op when nothing legacy exists', async () => {
    const { purgeLegacyStorage } = await import('@/auth/storage-keys');
    sessionStorage.setItem('current-key', 'value');
    expect(purgeLegacyStorage()).toBe(0);
    expect(sessionStorage.getItem('current-key')).toBe('value');
  });

  it('S2-ε δ-P0-1 — purges renamed modern keys (max-age → auth-max-age)', async () => {
    const { purgeLegacyStorage, OIDC_STORAGE_PREFIX } = await import('@/auth/storage-keys');
    // The S2-γ-era key that S2-γ renamed in β-P2-10.
    sessionStorage.setItem(`${OIDC_STORAGE_PREFIX}:max-age`, '600');
    sessionStorage.setItem(`${OIDC_STORAGE_PREFIX}:tokens`, 'keep-this-current-key');

    const removed = purgeLegacyStorage();
    expect(removed).toBe(1);
    expect(sessionStorage.getItem(`${OIDC_STORAGE_PREFIX}:max-age`)).toBeNull();
    expect(sessionStorage.getItem(`${OIDC_STORAGE_PREFIX}:tokens`)).toBe('keep-this-current-key');
  });
});

describe('refreshTokens', () => {
  it('posts grant_type=refresh_token and returns a fresh token set', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(DISCOVERY_DOC))
      .mockResolvedValueOnce(
        jsonResponse({ access_token: 'NEW_ACC', expires_in: 60, refresh_token: 'NEW_REF' }),
      );
    const tokens = await refreshTokens('OLD_REF');
    expect(tokens.accessToken).toBe('NEW_ACC');
    expect(tokens.refreshToken).toBe('NEW_REF');
    const body = (fetchSpy.mock.calls[1]?.[1]?.body as URLSearchParams) ?? new URLSearchParams();
    expect(body.get('grant_type')).toBe('refresh_token');
    expect(body.get('refresh_token')).toBe('OLD_REF');
  });
});

describe('signOutAtIdp', () => {
  it('redirects to end_session_endpoint when discovery exposes it', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(DISCOVERY_DOC));
    const redirected = await signOutAtIdp({ idTokenHint: 'IDT' });
    expect(redirected).toBe(true);
    const target = new URL(assignedUrls[0] ?? '');
    expect(target.origin + target.pathname).toBe(DISCOVERY_DOC.end_session_endpoint);
    expect(target.searchParams.get('client_id')).toBe(TEST_CONFIG.oidcClientId);
    expect(target.searchParams.get('post_logout_redirect_uri')).toBe(
      TEST_CONFIG.oidcPostLogoutRedirectUri,
    );
    expect(target.searchParams.get('id_token_hint')).toBe('IDT');
  });

  it('returns false when end_session_endpoint is absent', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ ...DISCOVERY_DOC, end_session_endpoint: undefined }),
    );
    const redirected = await signOutAtIdp();
    expect(redirected).toBe(false);
    expect(assignedUrls).toHaveLength(0);
  });

  it('returns false when post-logout redirect is null and no override is given', async () => {
    __resetRuntimeConfigCacheForTests({ ...TEST_CONFIG, oidcPostLogoutRedirectUri: null });
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(DISCOVERY_DOC));
    const redirected = await signOutAtIdp();
    expect(redirected).toBe(false);
  });
});
