import { expect, type BrowserContext, type Page, type Request } from '@playwright/test';

import { STORAGE_KEY_TOKENS } from '../../../src/auth/storage-keys';

import {
  installVisualFixture,
  isExternalResponseStatusAllowed,
  type ExternalRequestPolicy,
  type ExternalRequestPolicyDecision,
  type ExternalRequestStatusClass,
  type VisualFixtureHandle,
} from './visual-fixture';

const DEFAULT_KEYCLOAK_BASE_URL = 'http://localhost:8081';
const DEFAULT_APP_BASE_URL = 'http://localhost:5173';
export const REALM_PATH = '/realms/fcc-dev';
export const DISCOVERY_PATH = `${REALM_PATH}/.well-known/openid-configuration`;
export const JWKS_PATH = `${REALM_PATH}/protocol/openid-connect/certs`;
export const AUTH_PATH = `${REALM_PATH}/protocol/openid-connect/auth`;
export const TOKEN_PATH = `${REALM_PATH}/protocol/openid-connect/token`;
export const CREDENTIAL_SUBMIT_PATH = `${REALM_PATH}/login-actions/authenticate`;
export const RP_LOGOUT_PATH = `${REALM_PATH}/protocol/openid-connect/logout`;
export const KEYCLOAK_STATIC_ASSET_PREFIX = '/resources/';
const API_PREFIXES = ['/headless/', '/platform/', '/report-automation/', '/session/'];

const KEYCLOAK_BASE_URL = normalizeOrigin(
  process.env['KEYCLOAK_BASE_URL'] ?? DEFAULT_KEYCLOAK_BASE_URL,
);
const APP_BASE_URL = normalizeOrigin(process.env['E2E_BASE_URL'] ?? DEFAULT_APP_BASE_URL);
const PENDING_REQUEST_DRAIN_TIMEOUT_MS = 1_000;
const PENDING_REQUEST_DRAIN_POLL_INTERVAL_MS = 10;

export const REAL_AUTH_EVIDENCE_BOUNDARY =
  'real IdP and SPA callback/route guard proven; backend authorization not claimed.';

export type KeycloakRequestFamily =
  | 'discovery'
  | 'authorization'
  | 'token'
  | 'credential-submit'
  | 'rp-logout'
  | 'static-asset';

interface KeycloakTaxonomyRule {
  readonly family: KeycloakRequestFamily;
  readonly methods: readonly string[];
  readonly exactPaths?: readonly string[];
  readonly pathPrefixValue?: string;
  readonly acceptedStatusClasses: readonly ExternalRequestStatusClass[];
}

/** The only external requests admitted by the real-auth/visual evidence lane. */
export const KEYCLOAK_REQUEST_TAXONOMY: readonly KeycloakTaxonomyRule[] = [
  {
    family: 'discovery',
    methods: ['GET'],
    exactPaths: [DISCOVERY_PATH, JWKS_PATH],
    acceptedStatusClasses: ['2xx', '304'],
  },
  {
    family: 'authorization',
    methods: ['GET'],
    exactPaths: [AUTH_PATH],
    acceptedStatusClasses: ['2xx', '3xx'],
  },
  {
    family: 'token',
    methods: ['POST'],
    exactPaths: [TOKEN_PATH],
    acceptedStatusClasses: ['2xx'],
  },
  {
    family: 'credential-submit',
    methods: ['POST'],
    exactPaths: [CREDENTIAL_SUBMIT_PATH],
    acceptedStatusClasses: ['2xx', '3xx'],
  },
  {
    family: 'rp-logout',
    methods: ['GET'],
    exactPaths: [RP_LOGOUT_PATH],
    acceptedStatusClasses: ['2xx', '3xx'],
  },
  {
    family: 'static-asset',
    methods: ['GET', 'HEAD'],
    pathPrefixValue: KEYCLOAK_STATIC_ASSET_PREFIX,
    acceptedStatusClasses: ['2xx', '304'],
  },
];

export type KeycloakRequestPolicyDecision = ExternalRequestPolicyDecision & {
  readonly family: KeycloakRequestFamily;
};

/**
 * `family: 'token'` 은 **엔드포인트**이지 grant 가 아니다 — 그 한 경로로 서로 다른
 * grant 가 온다. 그러므로 판정은 grant_type 별로 갈려야 한다.
 *
 * ⚠️ 이 함수가 존재하는 이유 (실측 2026-09-05). 그전 판정은 이 family 의 **모든**
 * POST 에 대해 `authorization_code` + `code_verifier` 를 요구하고, 아니면
 * *"missing authorization-code PKCE body"* 를 `unexpectedRequests` 에 넣었다.
 * 그런데 SPA 가 이 엔드포인트로 보내는 grant 는 **둘**이다(`oidc-pkce.ts`):
 *
 *   exchangeCode()   grant_type=authorization_code + code_verifier
 *   refreshTokens()  grant_type=refresh_token      + refresh_token, code_verifier 없음
 *
 * 후자에 `code_verifier` 가 없는 것은 결함이 아니라 **RFC 6749 § 6 그대로**다 —
 * 오히려 넣는 쪽이 틀렸다. 즉 옛 판정은 규격을 지킨 요청을 PKCE 결함으로 세고 있었고,
 * 그 진단명이 *"PKCE body 가 없다"* 였으므로 읽는 사람을 **인증 구현 쪽으로** 잘못
 * 보냈다. (전수 확인: `postToken` 의 호출자는 위 둘뿐이고, 앱 안에 토큰 엔드포인트로
 * 가는 다른 fetch 는 없다. 그러므로 그 메시지를 낼 수 있는 요청은 refresh 뿐이었다.)
 *
 * 새 판정은 **약해지지 않고 강해진다** — 각 grant 를 자기 규격으로 본다:
 *  · `authorization_code` — `code_verifier` 필수. 없으면 그것이 진짜 PKCE 결함이다.
 *  · `refresh_token` — `refresh_token` 필수, 그리고 `code_verifier` 는 **있으면 안 된다**.
 *  · 그 외 — 이 SPA 가 보내지 않는 grant 다. 이름을 대고 거부한다.
 */
function assertTokenGrant(
  fields: URLSearchParams,
  entry: RealAuthNetworkRequest,
  ledger: RealAuthNetworkLedger,
  addUnexpected: (description: string) => void,
): void {
  const where = `${entry.method} ${entry.origin}${entry.pathname}`;
  const grant = fields.get('grant_type');
  switch (grant) {
    case 'authorization_code':
      if (fields.has('code_verifier')) {
        ledger.authorizationCodePkceRequests += 1;
      } else {
        addUnexpected(`${where} authorization_code grant without PKCE code_verifier`);
      }
      return;
    case 'refresh_token':
      if (!fields.has('refresh_token')) {
        addUnexpected(`${where} refresh_token grant without a refresh_token`);
        return;
      }
      if (fields.has('code_verifier')) {
        // PKCE 는 authorization code 를 교환할 때의 증명이다. 갱신 요청에 실려 오면
        // 검증기가 유출되는 것이고 RFC 6749 § 6 이 요구하지도 않는다.
        addUnexpected(`${where} refresh_token grant carries a PKCE code_verifier`);
        return;
      }
      ledger.refreshTokenGrantRequests += 1;
      return;
    default:
      addUnexpected(`${where} unexpected grant_type: ${grant ?? '(absent)'}`);
  }
}

export function classifyKeycloakRequest(
  requestUrl: string,
  method: string,
): KeycloakRequestPolicyDecision | null {
  let url: URL;
  try {
    url = new URL(requestUrl);
  } catch {
    return null;
  }
  if (url.origin !== new URL(KEYCLOAK_BASE_URL).origin) return null;
  const normalizedMethod = method.toUpperCase();
  const rule = KEYCLOAK_REQUEST_TAXONOMY.find((candidate) => {
    if (!candidate.methods.includes(normalizedMethod)) return false;
    if (candidate.exactPaths?.includes(url.pathname)) return true;
    return (
      candidate.pathPrefixValue !== undefined && url.pathname.startsWith(candidate.pathPrefixValue)
    );
  });
  if (rule === undefined) return null;
  return {
    family: rule.family,
    acceptedStatusClasses: rule.acceptedStatusClasses,
  };
}

export function isKeycloakResponseStatusAllowed(
  decision: KeycloakRequestPolicyDecision,
  status: number,
): boolean {
  return isExternalResponseStatusAllowed(decision, status);
}

/** Shared by real-auth and visual fixtures; no origin-only escape hatch exists. */
export const KEYCLOAK_EXTERNAL_REQUEST_POLICY: ExternalRequestPolicy = classifyKeycloakRequest;

const TRACKED_DEV_USERS = {
  viewer: { password: 'viewer', role: 'viewer', permission: 'platform:read' },
  operator: { password: 'operator', role: 'operator', permission: 'platform:read' },
  admin: { password: 'admin', role: 'admin', permission: 'platform:admin' },
} as const;

export type RealAuthUsername = keyof typeof TRACKED_DEV_USERS;

export interface RealAuthOptions {
  readonly username?: string;
  readonly password?: string;
  readonly expectedRole?: string;
  readonly expectedPermission?: string;
  readonly returnTo?: string;
  /** Install the existing deterministic API fixture after the callback URL is observed. */
  readonly installDeterministicFixture?: boolean;
}

export interface IssuedAccessTokenClaims {
  readonly issuer: string | null;
  readonly subject: string | null;
  readonly audience: readonly string[];
  readonly roles: readonly string[];
  readonly permissions: readonly string[];
}

export interface AuthorizationRequestEvidence {
  readonly issuer: string;
  readonly clientId: string;
  readonly redirectUri: string;
  readonly statePresent: boolean;
  readonly codeChallengePresent: boolean;
  readonly codeChallengeMethod: string | null;
  readonly callbackObserved: boolean;
}

export type RealAuthNetworkKind = 'keycloak' | 'app' | 'mock' | 'unexpected';
export type RealAuthNetworkStage = 'pre-fixture' | 'post-fixture' | 'after-release';

export interface RealAuthNetworkRequest {
  kind: RealAuthNetworkKind;
  readonly family: KeycloakRequestFamily | null;
  readonly method: string;
  readonly origin: string;
  readonly pathname: string;
  readonly stage: RealAuthNetworkStage;
  responseStatus: number | null;
}

export interface RealAuthApiResponse {
  readonly family: KeycloakRequestFamily | null;
  readonly method: string;
  readonly origin: string;
  readonly pathname: string;
  readonly stage: RealAuthNetworkStage;
  readonly status: number;
}

export interface RealAuthNetworkLedger {
  readonly requests: RealAuthNetworkRequest[];
  readonly unexpectedRequests: string[];
  readonly consoleErrors: string[];
  readonly pageErrors: string[];
  readonly appFontResponses: string[];
  readonly invalidFontResponses: string[];
  readonly apiResponses: RealAuthApiResponse[];
  readonly keycloakResponses: RealAuthApiResponse[];
  readonly failedResponses: string[];
  readonly authorizationRequestStates: string[];
  tokenEndpointRequests: number;
  authorizationCodePkceRequests: number;
  /** `grant_type=refresh_token` exchanges (RFC 6749 § 6). Counted SEPARATELY
   *  from the PKCE exchange because they are a different grant with different
   *  required fields — see `assertTokenGrant`. */
  refreshTokenGrantRequests: number;
}

export interface RealAuthSession {
  readonly claims: IssuedAccessTokenClaims;
  readonly authorization: AuthorizationRequestEvidence;
  readonly network: RealAuthNetworkLedger;
  readonly fixture: VisualFixtureHandle | null;
  /** Merge the fixture fallback and response ledger into one fail-closed result. */
  readonly reconcileDeterministicFixtureNetwork: () => Promise<void>;
  /** Remove only the fixture's fail-closed fallback before a real logout. */
  readonly releaseDeterministicNetworkGuard: () => Promise<void>;
}

function normalizeOrigin(value: string): string {
  return value.replace(/\/+$/u, '');
}

function isApiPath(pathname: string): boolean {
  return API_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function classifyRequest(
  requestUrl: string,
  method: string,
  appOrigin: string,
  keycloakOrigin: string,
  stage: RealAuthNetworkStage,
): RealAuthNetworkRequest {
  const url = new URL(requestUrl);
  const keycloakPolicy = classifyKeycloakRequest(requestUrl, method);
  const kind: RealAuthNetworkKind =
    url.origin === keycloakOrigin
      ? keycloakPolicy === null
        ? 'unexpected'
        : 'keycloak'
      : url.origin !== appOrigin
        ? 'unexpected'
        : isApiPath(url.pathname)
          ? stage === 'post-fixture'
            ? 'mock'
            : 'unexpected'
          : 'app';
  return {
    kind,
    family: keycloakPolicy?.family ?? null,
    method,
    origin: url.origin,
    pathname: url.pathname,
    stage,
    responseStatus: null,
  };
}

interface NetworkLedgerController {
  readonly ledger: RealAuthNetworkLedger;
  readonly markFixtureInstalled: () => void;
  readonly markFixtureReleased: () => void;
  readonly reconcileFixture: (fixture: VisualFixtureHandle) => Promise<void>;
  readonly recordDirectResponse: (requestUrl: string, method: string, status: number) => void;
}

function createNetworkLedger(context: BrowserContext): NetworkLedgerController {
  const ledger: RealAuthNetworkLedger = {
    requests: [],
    unexpectedRequests: [],
    consoleErrors: [],
    pageErrors: [],
    appFontResponses: [],
    invalidFontResponses: [],
    apiResponses: [],
    keycloakResponses: [],
    failedResponses: [],
    authorizationRequestStates: [],
    tokenEndpointRequests: 0,
    authorizationCodePkceRequests: 0,
    refreshTokenGrantRequests: 0,
  };
  const appOrigin = new URL(APP_BASE_URL).origin;
  const keycloakOrigin = new URL(KEYCLOAK_BASE_URL).origin;
  let stage: RealAuthNetworkStage = 'pre-fixture';
  const pendingRequests = new Map<Request, RealAuthNetworkRequest>();

  const addUnexpected = (description: string): void => {
    if (!ledger.unexpectedRequests.includes(description))
      ledger.unexpectedRequests.push(description);
  };

  context.on('request', (request) => {
    const entry = classifyRequest(
      request.url(),
      request.method(),
      appOrigin,
      keycloakOrigin,
      stage,
    );
    ledger.requests.push(entry);
    const requestUrl = new URL(request.url());
    pendingRequests.set(request, entry);
    if (requestUrl.origin === appOrigin && isApiPath(requestUrl.pathname)) {
      if (entry.kind === 'unexpected') {
        addUnexpected(`${entry.method} ${entry.origin}${entry.pathname} requested ${entry.stage}`);
      }
    }
    if (entry.family === 'authorization') {
      const state = requestUrl.searchParams.get('state');
      if (state === null) {
        addUnexpected(`${entry.method} ${entry.origin}${entry.pathname} has no state parameter`);
      } else {
        ledger.authorizationRequestStates.push(state);
      }
    }
    if (entry.family === 'token') {
      ledger.tokenEndpointRequests += 1;
      const body = request.postData();
      if (body !== null) {
        assertTokenGrant(new URLSearchParams(body), entry, ledger, addUnexpected);
      } else {
        addUnexpected(`${entry.method} ${entry.origin}${entry.pathname} has no token request body`);
      }
    }
    if (entry.kind === 'unexpected') {
      addUnexpected(`${entry.method} ${entry.origin}${entry.pathname}`);
    }
  });
  context.on('response', (response) => {
    const request = response.request();
    const entry = pendingRequests.get(request);
    if (entry !== undefined) {
      pendingRequests.delete(request);
      const status = response.status();
      entry.responseStatus = status;
      const responseEvidence: RealAuthApiResponse = {
        family: entry.family,
        method: entry.method,
        origin: entry.origin,
        pathname: entry.pathname,
        stage: entry.stage,
        status,
      };
      if (entry.family !== null) {
        ledger.keycloakResponses.push(responseEvidence);
        const decision = classifyKeycloakRequest(response.url(), entry.method);
        if (decision === null || !isKeycloakResponseStatusAllowed(decision, status)) {
          entry.kind = 'unexpected';
          ledger.failedResponses.push(
            `${entry.method} ${entry.origin}${entry.pathname} returned HTTP ${status}`,
          );
          addUnexpected(
            `${entry.method} ${entry.origin}${entry.pathname} (${entry.family}) returned HTTP ${status}`,
          );
        }
      }
      if (isApiPath(entry.pathname) && entry.origin === appOrigin) {
        ledger.apiResponses.push(responseEvidence);
      }
      if (entry.kind === 'mock' && (entry.stage !== 'post-fixture' || !response.ok())) {
        entry.kind = 'unexpected';
        if (!response.ok()) {
          ledger.failedResponses.push(
            `${entry.method} ${entry.origin}${entry.pathname} returned HTTP ${status}`,
          );
        }
        addUnexpected(
          `${entry.method} ${entry.origin}${entry.pathname} returned HTTP ${status} at ${entry.stage}`,
        );
      }
    }
    if (request.resourceType() !== 'font') return;
    const url = new URL(response.url());
    if (url.origin !== appOrigin) return;
    if (url.pathname.endsWith('.woff2')) {
      ledger.appFontResponses.push(`${url.origin}${url.pathname}`);
    } else {
      ledger.invalidFontResponses.push(`${url.origin}${url.pathname}`);
    }
  });
  context.on('requestfailed', (request) => {
    const entry = pendingRequests.get(request);
    if (entry === undefined) return;
    pendingRequests.delete(request);
    entry.kind = 'unexpected';
    ledger.failedResponses.push(
      `${entry.method} ${entry.origin}${entry.pathname} failed at ${entry.stage}`,
    );
    addUnexpected(
      `${entry.method} ${entry.origin}${entry.pathname} failed at ${entry.stage}: ${request.failure()?.errorText ?? 'unknown error'}`,
    );
  });
  context.on('page', (page) => observePage(page, ledger));
  for (const page of context.pages()) observePage(page, ledger);

  return {
    ledger,
    markFixtureInstalled: () => {
      stage = 'post-fixture';
    },
    markFixtureReleased: () => {
      stage = 'after-release';
    },
    reconcileFixture: async (fixture) => {
      const deadline = Date.now() + PENDING_REQUEST_DRAIN_TIMEOUT_MS;
      while (pendingRequests.size > 0) {
        const remainingMs = deadline - Date.now();
        if (remainingMs <= 0) break;
        await new Promise<void>((resolve) => {
          setTimeout(resolve, Math.min(remainingMs, PENDING_REQUEST_DRAIN_POLL_INTERVAL_MS));
        });
      }
      for (const fallbackRequest of fixture.unexpectedRequests) {
        addUnexpected(`visual-fixture fallback: ${fallbackRequest}`);
      }
      for (const entry of pendingRequests.values()) {
        entry.kind = 'unexpected';
        addUnexpected(
          `${entry.method} ${entry.origin}${entry.pathname} had no response at ${entry.stage}`,
        );
      }
      pendingRequests.clear();
      for (const entry of ledger.requests) {
        if (
          entry.stage === 'post-fixture' &&
          isApiPath(entry.pathname) &&
          entry.responseStatus === null
        ) {
          entry.kind = 'unexpected';
          addUnexpected(
            `${entry.method} ${entry.origin}${entry.pathname} had no post-fixture response`,
          );
        }
      }
    },
    recordDirectResponse: (requestUrl, method, status) => {
      const url = new URL(requestUrl);
      const decision = classifyKeycloakRequest(requestUrl, method);
      const entry: RealAuthNetworkRequest = {
        kind: decision === null ? 'unexpected' : 'keycloak',
        family: decision?.family ?? null,
        method,
        origin: url.origin,
        pathname: url.pathname,
        stage,
        responseStatus: status,
      };
      ledger.requests.push(entry);
      if (decision === null) {
        addUnexpected(`${method} ${requestUrl} (unclassified direct response)`);
        return;
      }
      const responseEvidence: RealAuthApiResponse = {
        family: decision.family,
        method,
        origin: url.origin,
        pathname: url.pathname,
        stage,
        status,
      };
      ledger.keycloakResponses.push(responseEvidence);
      if (!isKeycloakResponseStatusAllowed(decision, status)) {
        entry.kind = 'unexpected';
        ledger.failedResponses.push(`${method} ${requestUrl} returned HTTP ${status}`);
        addUnexpected(`${method} ${requestUrl} (${decision.family}) returned HTTP ${status}`);
      }
    },
  };
}

function observePage(page: Page, ledger: RealAuthNetworkLedger): void {
  page.on('pageerror', (error) => ledger.pageErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') ledger.consoleErrors.push(message.text());
  });
}

function trackedUserDefaults(username: string): {
  readonly password: string | undefined;
  readonly role: string | undefined;
  readonly permission: string | undefined;
} {
  if (username in TRACKED_DEV_USERS) {
    const user = TRACKED_DEV_USERS[username as RealAuthUsername];
    return user;
  }
  return { password: undefined, role: undefined, permission: undefined };
}

function resolveCredential(options: RealAuthOptions): {
  readonly username: string;
  readonly password: string;
  readonly expectedRole: string;
  readonly expectedPermission: string;
} {
  const username = options.username ?? process.env['E2E_OIDC_USERNAME'] ?? 'operator';
  const defaults = trackedUserDefaults(username);
  const password = options.password ?? process.env['E2E_OIDC_PASSWORD'] ?? defaults.password;
  if (!password) {
    throw new Error(
      'Real OIDC credentials are unavailable: set E2E_OIDC_USERNAME and E2E_OIDC_PASSWORD or use a tracked dev-realm account.',
    );
  }
  return {
    username,
    password,
    expectedRole:
      options.expectedRole ?? process.env['E2E_OIDC_EXPECTED_ROLE'] ?? defaults.role ?? 'operator',
    expectedPermission:
      options.expectedPermission ??
      process.env['E2E_OIDC_EXPECTED_PERMISSION'] ??
      defaults.permission ??
      'platform:read',
  };
}

async function readIssuedAccessTokenClaims(page: Page): Promise<IssuedAccessTokenClaims> {
  return page.evaluate((storageKey) => {
    const raw = globalThis.sessionStorage.getItem(storageKey);
    if (raw === null) throw new Error('real OIDC callback did not persist a token set');
    const tokenSet = JSON.parse(raw) as Record<string, unknown>;
    const accessToken = tokenSet['accessToken'];
    if (typeof accessToken !== 'string') {
      throw new Error('real OIDC callback persisted no accessToken');
    }
    const payloadSegment = accessToken.split('.')[1];
    if (!payloadSegment) throw new Error('issued accessToken is not a JWT');
    const padded = payloadSegment.padEnd(
      payloadSegment.length + ((4 - (payloadSegment.length % 4)) % 4),
      '=',
    );
    const binary = globalThis.atob(padded.replace(/-/gu, '+').replace(/_/gu, '/'));
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    const claims = JSON.parse(new TextDecoder().decode(bytes)) as Record<string, unknown>;
    const strings = (value: unknown): string[] =>
      Array.isArray(value)
        ? value.filter((entry): entry is string => typeof entry === 'string')
        : typeof value === 'string'
          ? [value]
          : [];
    return {
      issuer: typeof claims['iss'] === 'string' ? claims['iss'] : null,
      subject: typeof claims['sub'] === 'string' ? claims['sub'] : null,
      audience: strings(claims['aud']),
      roles: strings(claims['roles']),
      permissions: strings(claims['permissions']),
    };
  }, STORAGE_KEY_TOKENS);
}

function assertAuthorizationRequest(
  authorizationUrl: URL,
  appOrigin: string,
  expectedClientId: string,
): AuthorizationRequestEvidence {
  const state = authorizationUrl.searchParams.get('state');
  const challenge = authorizationUrl.searchParams.get('code_challenge');
  const method = authorizationUrl.searchParams.get('code_challenge_method');
  expect(authorizationUrl.pathname).toBe(AUTH_PATH);
  expect(authorizationUrl.searchParams.get('response_type')).toBe('code');
  expect(authorizationUrl.searchParams.get('client_id')).toBe(expectedClientId);
  expect(authorizationUrl.searchParams.get('redirect_uri')).toBe(`${appOrigin}/auth/callback`);
  expect(state).toMatch(/^[A-Za-z0-9_-]{20,}$/u);
  expect(challenge).toMatch(/^[A-Za-z0-9_-]{20,}$/u);
  expect(method).toBe('S256');
  return {
    issuer: KEYCLOAK_BASE_URL + REALM_PATH,
    clientId: expectedClientId,
    redirectUri: `${appOrigin}/auth/callback`,
    statePresent: state !== null,
    codeChallengePresent: challenge !== null,
    codeChallengeMethod: method,
    callbackObserved: false,
  };
}

function assertIssuedClaims(
  claims: IssuedAccessTokenClaims,
  expectedIssuer: string,
  expectedAudience: string,
  expectedRole: string,
  expectedPermission: string,
): void {
  expect(claims.issuer, 'access token issuer claim').toBe(expectedIssuer);
  expect(claims.subject, 'access token subject claim').toEqual(expect.any(String));
  expect(claims.subject?.length, 'access token subject must be non-empty').toBeGreaterThan(0);
  expect(claims.audience, 'access token audience claim').toContain(expectedAudience);
  expect(claims.roles, 'access token role claim').toContain(expectedRole);
  expect(claims.permissions, 'access token permission claim').toContain(expectedPermission);
}

/**
 * Drive the real authorization-code + S256 PKCE flow. The deterministic route
 * fixture is installed only after the real callback URL is observed, and never
 * writes or replaces the issued session token.
 */
export async function loginWithRealKeycloak(
  page: Page,
  context: BrowserContext,
  options: RealAuthOptions = {},
): Promise<RealAuthSession> {
  if (process.env['E2E_OIDC'] !== '1') {
    throw new Error('Real OIDC E2E requires E2E_OIDC=1; refusing to skip the IdP lane.');
  }

  const credentials = resolveCredential(options);
  const appOrigin = new URL(APP_BASE_URL).origin;
  const keycloakOrigin = new URL(KEYCLOAK_BASE_URL).origin;
  const networkController = createNetworkLedger(context);
  const network = networkController.ledger;
  const clientId = process.env['E2E_OIDC_CLIENT_ID'] ?? 'fcc-platform-frontend';

  const discoveryResponse = await page.request.get(`${KEYCLOAK_BASE_URL}${DISCOVERY_PATH}`);
  networkController.recordDirectResponse(
    `${KEYCLOAK_BASE_URL}${DISCOVERY_PATH}`,
    'GET',
    discoveryResponse.status(),
  );
  if (!discoveryResponse.ok()) {
    throw new Error(
      `Keycloak discovery failed with HTTP ${discoveryResponse.status()} at the configured IdP origin.`,
    );
  }
  const discovery = (await discoveryResponse.json()) as Record<string, unknown>;
  const issuer = discovery['issuer'];
  const authorizationEndpoint = discovery['authorization_endpoint'];
  if (typeof issuer !== 'string' || typeof authorizationEndpoint !== 'string') {
    throw new Error('Keycloak discovery is missing issuer or authorization_endpoint.');
  }
  expect(issuer).toBe(`${KEYCLOAK_BASE_URL}${REALM_PATH}`);
  expect(authorizationEndpoint).toContain(AUTH_PATH);

  // Each Playwright test receives a fresh browser context, so the IdP has no
  // SSO cookie to bypass. Avoid a persistent init script here: it would seed
  // the re-auth flag again on the post-logout app navigation and make a clean
  // logout appear to retain sessionStorage state.
  await page.goto(options.returnTo ?? '/', { waitUntil: 'domcontentloaded' });
  await page.waitForURL((url) => url.origin === keycloakOrigin && url.pathname === AUTH_PATH, {
    timeout: 15_000,
  });
  const authorization = assertAuthorizationRequest(new URL(page.url()), appOrigin, clientId);

  await page.getByLabel(/username|email/iu).fill(credentials.username);
  // Keycloak's show-password control also exposes an aria-label containing
  // "password". Exact accessible-name matching keeps the locator bound to
  // the credential input rather than the adjacent toggle button.
  await page.getByLabel('Password', { exact: true }).fill(credentials.password);
  const callbackUrl = page.waitForURL(
    (url) => url.origin === appOrigin && url.pathname === '/auth/callback',
    { timeout: 20_000 },
  );
  await page.locator('input[type="submit"], button[type="submit"]').first().click();
  await callbackUrl;

  let fixture: VisualFixtureHandle | null = null;
  if (options.installDeterministicFixture) {
    // The callback page performs the code exchange before the route guard
    // navigates to returnTo. The shared policy admits only the contracted
    // Keycloak method/path/status families; every same-origin API request is
    // still fail-closed by the fixture.
    fixture = await installVisualFixture(context, appOrigin, KEYCLOAK_EXTERNAL_REQUEST_POLICY);
    networkController.markFixtureInstalled();
  }

  await expect
    .poll(
      async () =>
        page.evaluate(
          (storageKey) => globalThis.sessionStorage.getItem(storageKey) !== null,
          STORAGE_KEY_TOKENS,
        ),
      { timeout: 20_000 },
    )
    .toBe(true);

  const claims = await readIssuedAccessTokenClaims(page);
  assertIssuedClaims(
    claims,
    issuer,
    clientId,
    credentials.expectedRole,
    credentials.expectedPermission,
  );

  await page.waitForURL((url) => url.origin === appOrigin && url.pathname !== '/auth/callback', {
    timeout: 20_000,
  });

  const authorizationEvidence: AuthorizationRequestEvidence = {
    ...authorization,
    issuer,
    callbackObserved: true,
  };
  const reconcileDeterministicFixtureNetwork = async (): Promise<void> => {
    if (fixture !== null) await networkController.reconcileFixture(fixture);
  };
  const releaseDeterministicNetworkGuard = async (): Promise<void> => {
    if (fixture === null) return;
    networkController.markFixtureReleased();
    await context.unroute('**/*');
  };
  return {
    claims,
    authorization: authorizationEvidence,
    network,
    fixture,
    reconcileDeterministicFixtureNetwork,
    releaseDeterministicNetworkGuard,
  };
}
