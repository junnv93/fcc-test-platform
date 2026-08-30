import { z } from 'zod';

/**
 * Runtime config SSOT — Zod schema validates the payload injected by
 * nginx/CDN at request time (see public/runtime-config.template.json and
 * docs/architecture/frontend/adr/0001-frontend-repo-location.md).
 *
 * Boot-time fail-fast: getRuntimeConfig() throws synchronously if the
 * payload is missing or violates the schema, so a misconfigured deployment
 * surfaces in the browser console *before* user-visible UI renders.
 *
 * Forbidden under the public-client policy (ADR-0002):
 *   - oidcClientSecret (PKCE only — no client secret in the bundle)
 *   - any backend service credentials
 *   - per-user tokens (those live in sessionStorage post-login, not config)
 */

// Zod 3.23 `.url()` does not call WHATWG `new URL()` internally — it
// regex-validates and lets the refine see strings that still fail to parse.
// We wrap `new URL` in try/catch so a malformed value fails the refine
// (returning a ZodIssue) instead of bubbling an unwrapped TypeError out of
// `safeParse`.
function tryParseUrl(value: string): URL | null {
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

function isLocalDevHostname(hostname: string): boolean {
  // `hostmachine` is Playwright's documented Docker host-gateway alias for
  // remote-browser tests that need to reach a development server on the host.
  return hostname === '127.0.0.1' || hostname === 'localhost' || hostname === 'hostmachine';
}

// Transport rules live in `enforceTransportPolicy` (object level) rather than on
// these field schemas, because the verdict depends on a SIBLING field
// (`insecureTransportAllowed`) and a per-field `.refine` cannot see siblings.
// These two only assert "parseable absolute URL"; the scheme check follows.
const absoluteUrl = z
  .string()
  .url()
  .refine((value) => tryParseUrl(value) !== null, { message: 'must be a parseable absolute URL' });

const HTTPS_MESSAGE = 'must be https:// (or an approved HTTP local-dev host)';
const WSS_MESSAGE = 'must be wss:// (or an approved WS local-dev host)';

function isSecureHttp(value: string): boolean {
  const parsed = tryParseUrl(value);
  if (!parsed) return false;
  return parsed.protocol === 'https:' || isLocalDevHostname(parsed.hostname);
}

function isSecureWs(value: string): boolean {
  const parsed = tryParseUrl(value);
  if (!parsed) return false;
  return (
    parsed.protocol === 'wss:' || (parsed.protocol === 'ws:' && isLocalDevHostname(parsed.hostname))
  );
}

/**
 * Which fields carry which transport rule. Declared once so a new URL field
 * cannot silently escape the policy — adding it here is the only wiring needed.
 */
const HTTP_TRANSPORT_FIELDS = [
  'apiBaseUrl',
  'platformApiBaseUrl',
  'oidcIssuer',
  'oidcRedirectUri',
  'oidcPostLogoutRedirectUri',
] as const;
const WS_TRANSPORT_FIELDS = ['wsBaseUrl'] as const;

const sha256Hex = z.string().regex(/^[0-9a-f]{64}$/i, 'must be 64 lowercase hex characters');

const featureFlagsSchema = z
  .object({
    providerDiagnosticMode: z.boolean(),
    sessionReplay: z.boolean(),
    betaResultBrowser: z.boolean(),
  })
  .strict();

// OIDC scopes — OpenID Connect Core 1.0 § 5.4 mandates 'openid'; remaining
// scopes are IdP-specific (Keycloak/Auth0 commonly use 'profile' + 'email').
// The 'openid' scope is enforced at the schema level so a misconfigured
// runtime payload fails Zod parse before the PKCE flow runs.
const oidcScopesSchema = z
  .array(z.string().min(1))
  .min(1)
  .refine((scopes) => scopes.includes('openid'), {
    message: "oidcScopes must include 'openid' (OIDC Core 1.0 § 5.4)",
  });

// Base object, exported so tooling/tests can read `.shape` — the exported
// `runtimeConfigSchema` below is a ZodEffects wrapper (superRefine) and does
// not expose `.shape` directly.
export const runtimeConfigObjectSchema = z
  .object({
    apiBaseUrl: absoluteUrl,
    // Optional dedicated base URL for the platform read surface (`/platform/*`).
    // The platform API is a separately deployable ASGI app
    // (platform_api_app:create_app); when the deployment splits it off the main
    // gateway, set this. `null` (the default) reuses `apiBaseUrl` (current
    // single-gateway, path-routed topology). Backward compatible — mirrors the
    // nullable-optional pattern of sentryDsn / oidcPostLogoutRedirectUri.
    platformApiBaseUrl: absoluteUrl.nullable(),
    wsBaseUrl: absoluteUrl,
    // Topology capability flag — is the per-node Session API (`/session/*` HTTP
    // + the `/session/events` WebSocket) reachable through `apiBaseUrl`/`wsBaseUrl`
    // (the gateway origin)? The session surface is a SINGLE chamber-node concern:
    // dev runs one local node (vite proxies `/session` -> :8000) so it is `true`,
    // but the CENTRAL hub serves only `/headless` + `/platform` (measurement is
    // distributed across native chamber nodes via the Platform chamber API), so
    // its runtime-config sets this `false`. Default `true` keeps every existing
    // deployment unchanged (backward compatible) — only central opts out. When
    // `false`, the SPA must not issue `/session/*` calls (they would hit the
    // gateway and 404), so the default landing skips the session panel and the
    // Control route is hidden. Sealed cross-topology by
    // tests/test_central_docker_compose.py::TestCentralSessionSurfaceBoundary.
    sessionApiEnabled: z.boolean().default(true),
    oidcIssuer: absoluteUrl,
    oidcClientId: z.string().min(1),
    oidcRedirectUri: absoluteUrl,
    // Optional — set when the IdP issues access tokens scoped to a specific
    // audience (Auth0 / Azure AD pattern). Keycloak default deployments leave
    // this empty and rely on the client_id as audience.
    oidcAudience: z.string().default(''),
    oidcScopes: oidcScopesSchema,
    // Optional — RP-Initiated Logout (OIDC Session Management 1.0 § 5).
    // When null, signOut clears the local session only.
    oidcPostLogoutRedirectUri: absoluteUrl.nullable(),
    // Transport policy escape hatch for an on-prem LAN deployment that cannot
    // obtain a TLS certificate. Default `false` keeps the secure rule in force,
    // so a deployment must DECLARE the deviation rather than inherit it — and a
    // future TLS rollout still gets its misconfiguration caught.
    //
    // ⚠ This is a deliberate deviation from the specification, not a supported
    // mode. RFC 6749 §3.1/§3.2 say the authorization server "MUST require the
    // use of TLS" for the authorization and token endpoints, and RFC 9700 says
    // authorization responses "MUST NOT be transmitted over unencrypted network
    // connections". Enabling this means bearer tokens travel in clear text and
    // anyone on the network path can replay them. Only set it where the network
    // is closed and the risk is accepted in writing.
    insecureTransportAllowed: z.boolean().default(false),
    // Which login strategy this deployment runs (identity axis, 2026-08-21).
    //
    // `'oidc'` (default, every pre-existing deployment) redirects to the IdP and
    // completes PKCE in the browser. `'local'` posts an email + password to
    // `POST /platform/auth/login` and the SERVER mints the token — the browser
    // performs no cryptography at all.
    //
    // ⚠️ That difference is the entire point. PKCE needs `crypto.subtle`, which
    // browsers expose ONLY in a secure context (https, or localhost). A central
    // PC published as `http://10.206.34.233:8080` therefore cannot complete an
    // OIDC login at all — not a misconfiguration, an impossibility. See D-6
    // below for why the two flags cannot disagree silently.
    authMode: z.enum(['oidc', 'local']).default('oidc'),
    environmentName: z.enum(['development', 'staging', 'production', 'test']),
    buildVersion: z.string().min(1),
    buildSha256: sha256Hex,
    sentryDsn: z.string().url().nullable(),
    otelCollectorUrl: z.string().url().nullable(),
    traceSampleRatio: z.number().min(0).max(1),
    featureFlags: featureFlagsSchema,
  })
  .strict();

export const runtimeConfigSchema = runtimeConfigObjectSchema.superRefine((config, ctx) => {
  // D-6 — make `insecureTransportAllowed` mean what its name says.
  //
  // Until the identity axis this flag only silenced THIS validator; it could
  // not give the browser back `crypto.subtle`. So a deployment could set it,
  // boot cleanly, and still be unable to log in — with the flag's own name
  // suggesting the opposite. In `local` mode the flag is finally truthful
  // (plaintext HTTP login genuinely works). In `oidc` mode it still is not, and
  // that combination is refused AT BOOT rather than failing later inside a
  // redirect the operator cannot read.
  //
  // ⚠️ Refusing is the whole value. A warning here would be read by nobody: the
  // symptom is a TypeError deep in the PKCE code path, and diagnosing it from
  // there cost this project a day.
  if (config.authMode === 'oidc' && config.insecureTransportAllowed) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['authMode'],
      message:
        'authMode "oidc" cannot be combined with insecureTransportAllowed: OIDC ' +
        'login needs crypto.subtle, which browsers expose only in a secure ' +
        'context (https or localhost), so login would be impossible rather than ' +
        'merely insecure. Use authMode "local" for a plaintext-HTTP deployment, ' +
        'or serve the app over https.',
    });
  }
  if (config.insecureTransportAllowed) return;
  for (const field of HTTP_TRANSPORT_FIELDS) {
    const value = config[field];
    if (typeof value === 'string' && !isSecureHttp(value)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: [field], message: HTTPS_MESSAGE });
    }
  }
  for (const field of WS_TRANSPORT_FIELDS) {
    const value = config[field];
    if (typeof value === 'string' && !isSecureWs(value)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: [field], message: WSS_MESSAGE });
    }
  }
});

export type RuntimeConfig = z.infer<typeof runtimeConfigSchema>;
export type FeatureFlags = z.infer<typeof featureFlagsSchema>;
export type EnvironmentName = RuntimeConfig['environmentName'];

declare global {
  interface Window {
    __FCC_RUNTIME_CONFIG__?: unknown;
  }
}

let cached: RuntimeConfig | undefined;

/**
 * Returns the validated runtime config. Throws synchronously if the
 * injected payload is missing or invalid — this is intentional: a
 * misconfigured deployment must not silently fall back to defaults.
 */
export function getRuntimeConfig(): RuntimeConfig {
  if (cached) return cached;
  const raw = typeof window !== 'undefined' ? window.__FCC_RUNTIME_CONFIG__ : undefined;
  if (raw === undefined) {
    throw new RuntimeConfigError(
      'Runtime config missing: window.__FCC_RUNTIME_CONFIG__ was not set before the app bundle loaded. ' +
        'Check that /runtime-config.js is served before /src/main.tsx (index.html order).',
    );
  }
  const result = runtimeConfigSchema.safeParse(raw);
  if (!result.success) {
    throw new RuntimeConfigError(
      'Runtime config invalid:\n' +
        result.error.errors.map((e) => `  - ${e.path.join('.')}: ${e.message}`).join('\n'),
    );
  }
  cached = result.data;
  return cached;
}

/**
 * Test-only helper that lets unit tests inject a custom payload without
 * mutating window. Production code MUST NOT call this.
 */
export function __resetRuntimeConfigCacheForTests(value?: RuntimeConfig): void {
  cached = value;
}

export class RuntimeConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'RuntimeConfigError';
  }
}
