/**
 * E2E auth fixture (FE-P7-E2E, 2026-05-29).
 *
 * Lets `authenticated shell` smoke run without a real IdP (Keycloak): the app
 * rehydrates its session from `sessionStorage[STORAGE_KEY_TOKENS]` at boot
 * (`main.tsx::restoreSession`) and `restoreSession`/`validateStoredTokens`
 * (src/auth/session.ts) only structure-validate the token — the access_token
 * signature is verified authoritatively by the backend, never the SPA. So a
 * structurally-valid, non-expired JWT injected before boot is enough to put
 * the app in the `authenticated` state for shell/nav rendering tests.
 *
 * SSOT / no-hardcoding:
 *   - The storage key is imported from the auth SSOT (`STORAGE_KEY_TOKENS`),
 *     never the literal `fcc-oidc:tokens`.
 *   - The JWT is built programmatically from a declarative payload (no inline
 *     base64 token string baked into a spec).
 *
 * Scope: shell/nav rendering only. The real OIDC PKCE round-trip is covered by
 * `auth-flow.spec.ts` / `oidc-conformance.spec.ts` (opt-in, real Keycloak).
 */
import { STORAGE_KEY_TOKENS } from '../../../src/auth/storage-keys';

import type { Page } from '@playwright/test';

/** Permissions granted to the synthetic operator. Mirrors the RBAC tokens the
 *  web surfaces gate on (session/headless/report_automation/platform). Kept as
 *  a declarative constant so specs assert the symbol, not a baked string. */
export const TEST_OPERATOR_PERMISSIONS = [
  'session:read',
  'session:control',
  'session:events',
  'headless:read',
  'headless:control',
  'report_automation:read',
  'report_automation:control',
  'platform:read',
  'platform:claim',
] as const;

/** Admin operator = the base operator + `platform:admin` (the membership
 *  assign/revoke gate). Derived from the base set (no hardcoded re-list) so a
 *  change to the base permissions flows through. */
export const TEST_ADMIN_PERMISSIONS = [...TEST_OPERATOR_PERMISSIONS, 'platform:admin'] as const;

interface TestAccessTokenClaims {
  readonly sub: string;
  readonly name: string;
  readonly email: string;
  readonly permissions: readonly string[];
  readonly scope: string;
  readonly roles: readonly string[];
}

function base64url(input: string): string {
  // Node (Playwright runner) — Buffer base64url is RFC 7515 compliant.
  return Buffer.from(input, 'utf-8').toString('base64url');
}

/** Build a 3-part JWT whose payload carries the given claims. The signature
 *  segment is a non-empty placeholder — the SPA never verifies the
 *  access_token signature (`decodeJwtPayload` only needs 3 dot-separated
 *  parts and decodes the middle one). */
export function buildTestAccessToken(claims: TestAccessTokenClaims): string {
  const header = base64url(JSON.stringify({ alg: 'RS256', typ: 'JWT' }));
  const payload = base64url(JSON.stringify(claims));
  return `${header}.${payload}.test-signature-not-verified-by-spa`;
}

/** OidcTokenSet-shaped object accepted by `validateStoredTokens`. `issuedAt`
 *  is stamped now so the token is non-expired; `refreshToken: null` disables
 *  the silent-refresh scheduler (which would otherwise need a reachable IdP). */
export function buildTestTokenSet(
  nowMs: number,
  permissions: readonly string[] = TEST_OPERATOR_PERMISSIONS,
): Record<string, unknown> {
  const claims: TestAccessTokenClaims = {
    sub: 'e2e-operator',
    name: 'E2E Operator',
    email: 'e2e-operator@fcc.test',
    permissions,
    scope: 'openid profile',
    roles: ['operator'],
  };
  return {
    accessToken: buildTestAccessToken(claims),
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 3600,
    scope: 'openid profile',
    issuedAt: nowMs,
  };
}

/** Inject an authenticated session into `sessionStorage` before the app boots
 *  so `main.tsx::restoreSession` rehydrates into the `authenticated` state.
 *  Must be called before `page.goto`. */
export async function injectAuthenticatedSession(
  page: Page,
  opts: { readonly permissions?: readonly string[] } = {},
): Promise<void> {
  const tokens = buildTestTokenSet(Date.now(), opts.permissions ?? TEST_OPERATOR_PERMISSIONS);
  await page.addInitScript(
    ({ key, value }) => {
      window.sessionStorage.setItem(key, value);
    },
    { key: STORAGE_KEY_TOKENS, value: JSON.stringify(tokens) },
  );
}
