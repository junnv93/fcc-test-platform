#!/usr/bin/env node
/**
 * Dev-stack OIDC env derivation (dev-oidc-ssot-standardization, 2026-06-26).
 *
 * Single source of truth for the backend OIDC env the local dev-stack injects
 * into the three ASGI surfaces (session / headless / platform). Mirrors the way
 * `dev-stack.mjs` already derives the proxy topology from `dev-stack.config.json`
 * and the way `infra/docker-compose.central.yml` derives the same OIDC values
 * from `central.env` — but for the local HOST-process backends, whose JWKS host
 * is the PUBLISHED Keycloak port (in `oidcIssuer`), NOT the compose-internal
 * `keycloak:8080`. The browser-facing SSOT is `runtime-config.dev.json`, the
 * official dev origin source (docs/development/dev-preview.md, principle #1) — so
 * the SPA and the backends validate the SAME issuer/audience and cannot drift.
 *
 * Pure + dependency-free: every value is derived from the two SSOT fields
 * (`oidcIssuer`, `oidcClientId`). No origin/port/realm/audience literal lives
 * here except the protocol-standard JWKS discovery path suffix (one constant).
 * Unit-tested by `dev-stack.test.mjs`; behaviourally sealed (Python) by
 * `tests/test_dev_stack_oidc_parity.py`.
 */

/** OIDC discovery JWKS path appended to the realm issuer (RFC 8414 convention). */
export const JWKS_PATH_SUFFIX = '/protocol/openid-connect/certs';

/** The three backend surfaces whose env carries the `FCC_<SURFACE>_` prefix. */
export const OIDC_SURFACES = ['SESSION', 'HEADLESS', 'PLATFORM'];

/** Auth mode each surface defaults to when the developer hasn't chosen one. */
export const DEFAULT_AUTH_MODE = 'oidc_jwt';

/**
 * Pure: derive the `FCC_<SURFACE>_OIDC_{ISSUER,AUDIENCE,JWKS_URI}` map from the
 * runtime-config SSOT. Returns an EMPTY map when the SSOT carries no issuer or
 * client id (a dev who runs without an IdP), so the caller never forces auth on.
 *
 *   ISSUER    = oidcIssuer
 *   AUDIENCE  = oidcClientId      (Keycloak emits the client id as the token aud)
 *   JWKS_URI  = oidcIssuer + JWKS_PATH_SUFFIX
 */
export function deriveOidcEnv(runtimeConfig, { surfaces = OIDC_SURFACES } = {}) {
  const issuer = (runtimeConfig && runtimeConfig.oidcIssuer) || '';
  const audience = (runtimeConfig && runtimeConfig.oidcClientId) || '';
  if (!issuer || !audience) return {};
  const jwksUri = issuer.replace(/\/+$/, '') + JWKS_PATH_SUFFIX;
  const env = {};
  for (const surface of surfaces) {
    env[`FCC_${surface}_OIDC_ISSUER`] = issuer;
    env[`FCC_${surface}_OIDC_AUDIENCE`] = audience;
    env[`FCC_${surface}_OIDC_JWKS_URI`] = jwksUri;
  }
  return env;
}

/**
 * Pure: merge the derived OIDC defaults onto a base env WITHOUT clobbering the
 * developer's explicit choices (secure-by-default + override-able, mirroring
 * compose's `${FCC_*_AUTH_MODE:-oidc_jwt}`):
 *   - OIDC_* values fill only when absent/empty.
 *   - `<SURFACE>_AUTH_MODE` is set to oidc_jwt ONLY when that surface has neither
 *     an explicit AUTH_MODE nor an ALLOW_INSECURE flag — so a developer can still
 *     opt a surface out (insecure / trusted_headers / disabled) from their env.
 * When the SSOT yields no OIDC values, auth mode is left untouched.
 */
export function applyOidcDefaults(baseEnv, runtimeConfig, { surfaces = OIDC_SURFACES } = {}) {
  const derived = deriveOidcEnv(runtimeConfig, { surfaces });
  const out = { ...baseEnv };
  for (const [key, value] of Object.entries(derived)) {
    if (out[key] === undefined || out[key] === '') out[key] = value;
  }
  if (Object.keys(derived).length === 0) return out;
  for (const surface of surfaces) {
    const hasMode = baseEnv[`FCC_${surface}_AUTH_MODE`];
    const hasInsecure = baseEnv[`FCC_${surface}_ALLOW_INSECURE`];
    if (!hasMode && !hasInsecure) out[`FCC_${surface}_AUTH_MODE`] = DEFAULT_AUTH_MODE;
  }
  return out;
}
