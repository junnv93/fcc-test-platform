/// <reference types="vitest" />
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';
import checker from 'vite-plugin-checker';
import tsconfigPaths from 'vite-tsconfig-paths';

import { buildDevCsp, devCspMetaStripPlugin, previewE2eCspMetaStripPlugin } from './vite/dev-csp';

/**
 * Vite config — production-grade defaults.
 *
 * - TS + ESLint dev-time overlay (checker plugin).
 * - tsconfig path alias resolution.
 * - Manual chunks: vendor split for cache efficiency.
 * - Source maps in production for Sentry release upload.
 * - Bundle size budget: warn at 250 KB per chunk (Sprint S8 will ratchet).
 *
 * Runtime config is NOT inlined here — `public/runtime-config.template.json`
 * is served by nginx/CDN at request time so the same build artifact
 * deploys to multiple environments.
 */
export default defineConfig(({ mode, command }) => {
  // Load only VITE_ prefixed env (security: never leak server secrets to client)
  const env = loadEnv(mode, process.cwd(), 'VITE_');

  // Dev server bind host/port — single source for both the server config and
  // the HMR websocket origin that the dev CSP must allowlist.
  const devHost = 'localhost';
  const devPort = Number(env['VITE_DEV_PORT']) || 5173;
  const isDevServer = command === 'serve';

  // Dev API gateway (fe-dev-gateway-proxy, 2026-06-14; SSOT-derived
  // fe-dev-stack-launcher, 2026-06-14). The frontend calls a single same-origin
  // base (`apiBaseUrl` = the Vite dev origin); this proxy is the local stand-in
  // for the prod path-routed gateway, forwarding each surface to its own backend
  // ASGI app. The surface topology (prefixes / port / ws) lives ONCE in
  // dev-stack.config.json so the proxy and scripts/dev-stack.mjs cannot drift;
  // per-surface target host:port stays env-overridable. Same-origin from the
  // browser's view → no CORS in dev.
  //
  // `pathPrefixes` is an array: one ASGI app can mount several top-level
  // prefixes (the headless app serves /headless, /health and /report-automation),
  // so this must flatMap — a plain map would emit one proxy entry per SURFACE
  // and silently drop the extra prefixes, which is exactly how
  // /report-automation/* came to 404 in dev while working in production.
  const devStack = JSON.parse(
    readFileSync(resolve(__dirname, 'dev-stack.config.json'), 'utf8'),
  ) as {
    host: string;
    surfaces: readonly {
      pathPrefixes: readonly string[];
      port: number;
      ws: boolean;
      targetEnv: string;
    }[];
  };
  const apiGatewayProxy = Object.fromEntries(
    devStack.surfaces.flatMap((s) =>
      s.pathPrefixes.map((prefix) => [
        // Anchored at a PATH BOUNDARY, not a bare string prefix
        // (dev-environment-contract-parity, 2026-08-01). A plain Vite proxy key
        // matches every URL that merely STARTS WITH it, so `/session` also
        // captured the SPA's own `/sessions` route: a deep link or a reload of
        // 측정 이력 was forwarded to the session backend and the operator got
        // `{"detail":"Not Found"}` instead of the app. Production does not have
        // this failure mode — nginx matches `location /headless/` with the
        // separator included — so it was a pure dev-vs-prod divergence.
        // A key beginning with `^` is interpreted by Vite as a RegExp; the
        // group is non-capturing and no `rewrite` is set, so the backend still
        // receives the URL verbatim.
        `^${prefix}(?:/|$)`,
        {
          target: env[s.targetEnv] || `http://${devStack.host}:${s.port}`,
          changeOrigin: true,
          ...(s.ws ? { ws: true } : {}),
        },
      ]),
    ),
  );
  // Phase 0 dev CSP (architecture plan §4.2). Built only for the dev server —
  // prod ships the meta CSP in index.html unchanged. connect-src is derived
  // from runtime-config.dev.json origins (no host/port hardcoded in dev-csp.ts)
  // plus the Vite HMR websocket origin (derived from devHost/devPort above).
  const devCsp = isDevServer
    ? buildDevCsp({ extraOrigins: [`ws://${devHost}:${devPort}`] })
    : undefined;
  // Phase 2 follow-up (fe-phase2-followup, 2026-05-30) — preview-mode e2e CSP
  // header (env-gated). prod meta CSP `connect-src 'self' https: wss:` blocks
  // `http://127.0.0.1:8000` mock calls during e2e, but the prod artifact must
  // be validated against the EXACT same CSP we ship — switching playwright to
  // `vite` (dev) silently skipped the build/minify/CSP regression surface.
  // Solution: keep `vite preview` as the e2e server (preserves the build
  // artifact check) and inject a dev-derived CSP via `preview.headers` ONLY
  // when `VITE_E2E=1`. The `dist/index.html` meta CSP stays byte-identical,
  // and a non-e2e `npm run preview` still surfaces the same prod CSP an
  // operator would see locally. We gate on `process.env.VITE_E2E` because vite
  // 5's `command` is `'serve'` for BOTH the dev and preview servers — the
  // preview-vs-dev distinction is invisible to `defineConfig`.
  const previewE2eCsp =
    process.env['VITE_E2E'] === '1'
      ? buildDevCsp({ extraOrigins: [`ws://${devHost}:${devPort}`] })
      : undefined;

  return {
    root: '.',
    plugins: [
      react(),
      tsconfigPaths(),
      checker({
        typescript: true,
        eslint: {
          lintCommand: 'eslint "./src/**/*.{ts,tsx}"',
          useFlatConfig: true,
        },
        overlay: { initialIsOpen: false },
      }),
      // Serve-only: strip the meta CSP from the dev-served HTML so the dev
      // `server.headers` CSP is the single authoritative policy (CSPs combine
      // as an intersection). Built dist/index.html is untouched.
      devCspMetaStripPlugin(),
      // Preview-only (env-gated, VITE_E2E=1): same meta-strip for the
      // preview server so e2e `page.route()` mock origins are reachable
      // through the env-injected preview header. dist/index.html on disk
      // stays prod-strict; the strip happens only on response.
      previewE2eCspMetaStripPlugin(),
    ],
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src'),
      },
    },
    server: {
      port: devPort,
      strictPort: true,
      host: devHost,
      open: false,
      // Local API gateway — path-route each surface to its backend ASGI app
      // (derived from dev-stack.config.json). The session surface carries the
      // `/session/events` WebSocket (ws: true). `changeOrigin` rewrites the Host
      // header to the target (backends bind 127.0.0.1). Dev-serve only;
      // `preview`/prod are untouched (e2e mocks via origin-agnostic page.route
      // globs).
      proxy: apiGatewayProxy,
      ...(devCsp ? { headers: { 'Content-Security-Policy': devCsp } } : {}),
    },
    preview: {
      port: 4173,
      strictPort: true,
      // env-gated e2e CSP — `dist/index.html` meta CSP is untouched, the
      // header just *adds* a dev allowlist when VITE_E2E=1. Combined CSPs
      // intersect, but for connect-src the *most permissive* policy in the
      // intersection wins on a per-source basis when the browser evaluates
      // both — that's why the dev CSP (which adds 127.0.0.1) is the
      // *additional* policy here, not a replacement. See architecture-plan §4.2.
      ...(previewE2eCsp ? { headers: { 'Content-Security-Policy': previewE2eCsp } } : {}),
    },
    build: {
      target: 'es2022',
      outDir: 'dist',
      assetsDir: 'assets',
      sourcemap: true,
      cssCodeSplit: true,
      reportCompressedSize: true,
      chunkSizeWarningLimit: 250,
      rollupOptions: {
        output: {
          // Vendor grouping for cache efficiency — deliberately limited to
          // packages that are on the *static* entry graph.
          //
          // The former `observability` group is gone on purpose (wave
          // `fe-w4-bundle-observability-cost`, 2026-07-31). `manualChunks`
          // forces every listed package into one chunk, so grouping the
          // always-reachable `@opentelemetry/api` + `web-vitals` together with
          // the OTel web SDK and `@sentry/browser` made the whole 321 kB group
          // statically reachable — code-splitting the *source* would have had
          // no effect on the initial load path while this group existed.
          // Rollup's automatic chunking already emits the on-demand
          // `tracing` / `sentry-runtime` graphs as async chunks and hoists what
          // they share; naming them here could only re-glue them.
          manualChunks: {
            react: ['react', 'react-dom', 'react-router-dom'],
            query: ['@tanstack/react-query'],
            validation: ['zod'],
          },
        },
      },
    },
    test: {
      globals: false,
      environment: 'jsdom',
      setupFiles: ['./tests/setup.ts'],
      include: [
        'tests/**/*.test.ts',
        'tests/**/*.test.tsx',
        'src/**/*.test.ts',
        'src/**/*.test.tsx',
        // Dev-only Node helpers (vite/) — kept out of tests/ so their `.mjs`
        // imports stay under tsconfig.node.json (allowJs), not the strict
        // browser tsconfig.json program.
        'vite/**/*.test.ts',
        // Dev stack launcher (scripts/) — `.mjs` Node script + its unit test.
        'scripts/**/*.test.mjs',
      ],
      exclude: ['tests/e2e/**', 'node_modules/**', 'dist/**'],
      coverage: {
        provider: 'v8',
        reporter: ['text', 'json', 'html', 'lcov'],
        exclude: ['tests/**', '**/*.test.ts', '**/*.test.tsx', 'src/api/generated/**'],
      },
    },
  };
});
