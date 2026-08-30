import { expect, test } from '@playwright/test';

import {
  AUTH_PATH,
  TOKEN_PATH,
  classifyKeycloakRequest,
  isKeycloakResponseStatusAllowed,
} from './helpers/real-auth-fixture';

/**
 * Sprint S2-EXT-3 — Panva OIDC conformance smoke.
 *
 * Exercises the parts of OIDC Core 1.0 / OIDC Discovery 1.0 / RFC 7636
 * PKCE / RFC 6749 § 10.12 (state CSRF) that the SPA's auth subsystem
 * implements, by driving a real flow against the local Keycloak compose
 * (`infra/docker-compose.idp.yml`).
 *
 * Opt-in: skipped only when `E2E_OIDC` is not enabled. Once enabled, an
 * unavailable or malformed IdP is a hard failure, never a skip.
 *
 * Full Panva OIDC conformance suite (https://www.certification.openid.net)
 * is an online certification — out of scope for local CI. This spec
 * checks the subset that matters operationally: discovery shape,
 * S256 PKCE method advertised, JWKS keys present.
 */

const KEYCLOAK_BASE_URL = process.env['KEYCLOAK_BASE_URL'] ?? 'http://localhost:8081';
const OIDC_E2E_ENABLED = process.env['E2E_OIDC'] === '1';

test.describe('OIDC conformance smoke (Sprint S2-EXT-3)', () => {
  test.skip(!OIDC_E2E_ENABLED, 'E2E_OIDC=1 not set — Keycloak compose required');

  test.beforeAll(() => {
    const keycloakBase = KEYCLOAK_BASE_URL.replace(/\/+$/u, '');
    const tokenUrl = `${keycloakBase}${TOKEN_PATH}`;
    const tokenDecision = classifyKeycloakRequest(tokenUrl, 'POST');
    if (tokenDecision === null) {
      throw new Error('token policy control unexpectedly classified as null');
    }

    // These are executable red controls for the exact method/path/status
    // policy. A source-text-only guard cannot detect an origin-wide or
    // always-true regression here.
    expect(classifyKeycloakRequest(`${keycloakBase}/admin/realms`, 'GET')).toBeNull();
    expect(classifyKeycloakRequest(`${keycloakBase}${TOKEN_PATH}`, 'GET')).toBeNull();
    expect(isKeycloakResponseStatusAllowed(tokenDecision, 500)).toBe(false);
    expect(isKeycloakResponseStatusAllowed(tokenDecision, 200)).toBe(true);
    expect(classifyKeycloakRequest(`https://untrusted.example${AUTH_PATH}`, 'GET')).toBeNull();
  });

  test('discovery document conforms to OIDC Core 1.0 § 4', async ({ request }) => {
    const resp = await request.get(
      `${KEYCLOAK_BASE_URL}/realms/fcc-dev/.well-known/openid-configuration`,
    );
    expect(
      resp.ok(),
      `Keycloak discovery failed: HTTP ${resp.status()} from ${KEYCLOAK_BASE_URL}`,
    ).toBe(true);
    const discovery = (await resp.json()) as Record<string, unknown>;

    // OIDC Core 1.0 § 4 — required fields the SPA depends on.
    expect(discovery['issuer']).toBeTruthy();
    expect(discovery['authorization_endpoint']).toBeTruthy();
    expect(discovery['token_endpoint']).toBeTruthy();
    expect(discovery['jwks_uri']).toBeTruthy();

    // RFC 7636 § 4.4 — IdP MUST advertise S256 for PKCE flow.
    const methods = discovery['code_challenge_methods_supported'] as string[] | undefined;
    expect(methods, 'IdP must advertise PKCE methods').toBeDefined();
    expect(methods).toContain('S256');

    // OIDC Discovery 1.0 § 3 — required for verifyIdToken signature check.
    const sigAlgs = discovery['id_token_signing_alg_values_supported'] as string[] | undefined;
    expect(sigAlgs).toBeDefined();
    expect(sigAlgs).toContain('RS256');
  });

  test('jwks_uri returns at least one signing key', async ({ request }) => {
    const discoveryResp = await request.get(
      `${KEYCLOAK_BASE_URL}/realms/fcc-dev/.well-known/openid-configuration`,
    );
    expect(
      discoveryResp.ok(),
      `Keycloak discovery failed: HTTP ${discoveryResp.status()} from ${KEYCLOAK_BASE_URL}`,
    ).toBe(true);
    const discovery = (await discoveryResp.json()) as { jwks_uri: string };

    const jwksResp = await request.get(discovery.jwks_uri);
    expect(jwksResp.ok()).toBe(true);
    const jwks = (await jwksResp.json()) as { keys: { kty: string; use?: string }[] };
    expect(Array.isArray(jwks.keys)).toBe(true);
    expect(jwks.keys.length).toBeGreaterThan(0);
    // At least one RSA signing key.
    const sigKey = jwks.keys.find(
      (k) => k.kty === 'RSA' && (k.use === undefined || k.use === 'sig'),
    );
    expect(sigKey, 'JWKS must contain at least one RSA signing key').toBeTruthy();
  });
});
