# `@fcc/web` — FCC Test Platform Frontend

React + TypeScript + Vite SPA for the FCC test platform. Production frontend SSOT under the multi-language monorepo (`apps/web/`). Operational routes — Projects (central coverage), Providers, Control (remote start/stop), Sessions (result browser), Reports (signed download) — are implemented behind OIDC PKCE auth + per-route RBAC. `npm run dev` boots into the Overview view; authenticated routes hang off the primary navigation shell.

ADRs that drove this scaffold:

- [ADR-0001](../../docs/architecture/frontend/adr/0001-frontend-repo-location.md) — monorepo `apps/web/`
- [ADR-0002](../../docs/architecture/frontend/adr/0002-stack-selection.md) — React 18 + Vite 5 + TS strict + RR v6 + TanStack Query v5 + Zod + vitest + Playwright
- [ADR-0003](../../docs/architecture/frontend/adr/0003-openapi-ts-client-generator.md) — `openapi-typescript` + `openapi-fetch`
- [ADR-0004](../../docs/architecture/frontend/adr/0004-distributed-tracing-sdk.md) — `@opentelemetry/sdk-trace-web`
- [ADR-0006](../../docs/architecture/frontend/adr/0006-observability-backend.md) — Sentry + web-vitals → OTel collector

## Prerequisites

- Node.js 22.13 LTS (`engines` pin in `package.json`: `node >=22.13 <23`)
- npm ≥ 10.9 (the version bundled with Node 22.13 LTS; the verified runtime uses npm 11.2.0. Project uses npm for now; `pnpm`/`yarn` work but the lockfile shipped to git is `package-lock.json`)

> **Windows PowerShell note.** Bare `npm` fails under Windows **PowerShell** when the
> execution policy blocks `npm.ps1` (`npm.ps1 cannot be loaded because running scripts
is disabled on this system`). Run the frontend gates as **`npm.cmd …`** in PowerShell;
> a normal shell / **Git Bash** can use plain **`npm …`**. Both forms are otherwise identical.

- Backend running locally on `http://127.0.0.1:8000` (Session API) for the dev `Overview` view to populate

## First-time setup

```bash
cd apps/web
npm ci                 # install pinned dependencies
npm run codegen        # generate src/api/generated/*.ts from backend OpenAPI artifacts
npm run typecheck      # tsc --noEmit
npm test               # vitest unit tests
npm run test:e2e:install
npm run test:e2e       # Playwright smoke
```

## Dev loop

```bash
npm run dev            # Vite dev server on http://localhost:5173
```

Vite serves `public/runtime-config.js` at boot. `src/main.tsx` reads
`window.__FCC_RUNTIME_CONFIG__` and Zod-validates before React mounts —
a malformed payload renders a `[data-testid="boot-error"]` block instead
of mounting the app (deliberate fail-fast).

## Build

```bash
npm run build          # tsc --noEmit && vite build (sourcemaps on)
npm run preview        # serve dist/ on http://127.0.0.1:4173
```

`vite.config.ts` manual-chunks splits vendor bundles for cache efficiency:
`react` / `query` / `observability` / `validation` chunks.

## Architecture map

```
src/
├── main.tsx               Boot sequence (config -> tracing -> sentry -> web-vitals -> React)
├── app.tsx                Router + QueryClientProvider + Devtools (dev-only)
├── api/
│   ├── session-client.ts  openapi-fetch wrapper for the Session API
│   └── generated/         AUTO-GENERATED (gitignored), produced by `npm run codegen`
├── config/
│   └── runtime.ts         Zod schema + RuntimeConfigError + getRuntimeConfig()
├── observability/
│   ├── tracing.ts         OTel WebTracerProvider + W3CTraceContextPropagator
│   ├── sentry.ts          Sentry SDK (init only when sentryDsn is non-null)
│   └── web-vitals.ts      CLS/INP/LCP/TTFB/FCP -> active OTel span attributes
├── routes/
│   ├── _layout.tsx        Shell with primary navigation
│   ├── overview.tsx       Sprint S1 baseline view
│   ├── projects.tsx       Central coverage matrix + claims (keyset pagination)
│   ├── providers.tsx      Provider UI descriptor viewer (schema-first)
│   ├── control.tsx        Remote start/stop + progress + live WS log
│   ├── sessions.tsx       Session result browser (attempt history, infinite scroll)
│   ├── reports.tsx        Report queue stats + signed artifact download
│   └── not-found.tsx      Fallback for unknown routes
├── shared/
│   ├── error-boundary.tsx Route-level boundary (forwards to Sentry)
│   └── query-client.ts    TanStack Query singleton with defaults
└── styles/
    └── global.css         Design tokens + light/dark variables
```

## SSOT chain

```
backend `application/session/api_contracts.py`
   ↓ Sprint F-2-D3 (byte-identity invariant)
`docs/api/session-api.openapi.json`
   ↓ apps/web/scripts/codegen.mjs (openapi-typescript)
`apps/web/src/api/generated/session-api.types.ts`
   ↓ `apps/web/src/api/session-client.ts`
TanStack Query hooks in `apps/web/src/routes/**.tsx`
```

CI runs `npm run codegen:check` so backend OpenAPI changes without a frontend regen fail the build — column literals never drift.

## Backend interop notes

- Distributed tracing — `traceparent` headers are propagated to `apiBaseUrl`
  via `FetchInstrumentation`. Backend SSOT: `application/common/correlation.py`
  (P1-1) + `application/common/outbound_http.py::build_outbound_traceparent_headers` (P0-3).
- WebSocket events — `wsBaseUrl` connects to `/session/events`. Backend
  HIGH-3 supports `?traceparent=` query transport for browsers that cannot
  set headers on WS upgrade.
- AuthZ — runtime config carries `oidcIssuer`/`oidcClientId`/`oidcRedirectUri`;
  Sprint S2 wires the PKCE flow against `application/common/auth_config.py::HttpAuthConfig` (F-2-D4 SSOT).

## OIDC Quick Start (Sprint S2 → S2-α → S2-β → S2-γ)

The PKCE flow under `src/auth/` is production-ready against any
OIDC-compliant IdP. Dev uses the Keycloak compose under `infra/`.

```bash
# 1. Start the dev IdP (Keycloak with the fcc-dev realm pre-imported)
docker compose -f ../../infra/docker-compose.idp.yml up -d

# 2. Create public/runtime-config.js by wrapping the JSON template:
#    The template is JSON; the served file is a tiny JS that assigns
#    window.__FCC_RUNTIME_CONFIG__. Format:
#
#    window.__FCC_RUNTIME_CONFIG__ = {
#      "apiBaseUrl": "http://127.0.0.1:8000",
#      "wsBaseUrl": "ws://127.0.0.1:8000",
#      "oidcIssuer": "http://localhost:8081/realms/fcc-dev",
#      "oidcClientId": "fcc-platform-frontend",
#      "oidcRedirectUri": "http://localhost:5173/auth/callback",
#      "oidcAudience": "",
#      "oidcScopes": ["openid", "profile", "email"],
#      "oidcPostLogoutRedirectUri": "http://localhost:5173/",
#      "environmentName": "development",
#      "buildVersion": "0.1.0-dev",
#      "buildSha256": "0000000000000000000000000000000000000000000000000000000000000000",
#      "sentryDsn": null,
#      "otelCollectorUrl": null,
#      "traceSampleRatio": 1.0,
#      "featureFlags": { "providerDiagnosticMode": false, "sessionReplay": false, "betaResultBrowser": false }
#    };
#
#    Production deploys generate runtime-config.js from a CI template + env
#    secrets (see Sprint S9 GitHub Actions workflow when it lands).

# 3. Start the SPA
npm run dev

# 4. Visit http://localhost:5173/ — RequireAuth redirects to Keycloak,
# login (test users: viewer / operator / admin, password = username),
# and you land back on the Overview view.
```

The auth subsystem owns:

| Module                     | Role                                                                                                                                                                                           |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/auth/storage-keys.ts` | Single source of every sessionStorage key (`state` / `verifier` / `nonce` / `return-to` / `tokens` / `force-reauth` / `max-age`) + `purgeLegacyStorage` migration helper.                      |
| `src/auth/oidc-pkce.ts`    | OIDC Discovery + RFC 7636 PKCE + state CSRF + OIDC Core nonce + jose `jwtVerify` (signature + iss + aud + exp + nonce + 30s clock skew tolerance + optional `max_age`/`auth_time`).            |
| `src/auth/session.ts`      | sessionStorage-only token cache + silent refresh scheduler (`MIN_REFRESH_MARGIN_SECONDS = 30`, mirror of backend `OIDC_REFRESH_MARGIN_SECONDS`) + `useSyncExternalStore`-compatible subscribe. |
| `src/auth/route-guard.tsx` | `<RequireAuth>` + `<RequirePermission>` + `<AuthCallbackRoute>` + `<SignOutButton forceReauth>` + `useAuthSession()` hook.                                                                     |
| `src/auth/failure-ui.tsx`  | 5 OIDC failure kinds with WCAG-compliant alert panels.                                                                                                                                         |

Cross-tech contract: see `../../docs/architecture/frontend/cross-tech-token-policy.md` (RFC 6749 § 5.1 + NIST SP 800-63B § 7.2 + tuning runbook).

## Bundle measurement (Sprint S2-γ β-P1-3)

```bash
npm run measure:bundle              # measure current dist/, warn if stale
npm run measure:bundle -- --build   # vite build + measure
npm run measure:bundle -- --stdout  # pipe to stdout instead of dist/bundle-size.json
```

The JSON output (deterministic `buildId` = sha256 of all chunks) is the
input for Sprint S8 Lighthouse CI's bundle budget. `npm run codegen` →
`npm run build` → `npm run measure:bundle -- --build` is the full
release-candidate measurement chain.

## Status (2026-05-29)

Functionally release-candidate hardening. The S2-γ "out of scope" items below
are now **implemented**:

- ✅ Central DB read model — `/platform/projects/{id}/coverage|claims|sync-status` typed client (FE-P0d / FE-P2)
- ✅ Production routes — Projects / Control / Sessions / Reports / Providers
- ✅ Lighthouse CI gate + bundle size budget (FE-P7, `npm run measure:bundle` + `lighthouserc`)
- ✅ GitHub Actions workflows — `frontend-build.yml` / `frontend-e2e.yml` (codegen → check/typecheck/lint/test/build/budget)
- ✅ CI gates (codegen:check / typecheck / lint / test / build) green

### Remaining for production cutover (live evidence, not code)

These need real-environment execution evidence before declaring cutover complete:

- Live PostgreSQL — concurrent claim SERIALIZABLE race + materialized-view refresh latency
- Real IdP (Keycloak/operator) OIDC round-trip
- Deployed-frontend browser QA + hardware smoke
- Operational secret injection (`FCC_HEADLESS_DOWNLOAD_SIGNING_SECRET`) + download/audit flow smoke

## Lockfile

`package-lock.json` is committed once `npm install` is run for the first time. Until then this scaffold builds against the version-pinned ranges in `package.json`. CI must run `npm ci` (not `npm install`) so the lockfile is the source of truth.
