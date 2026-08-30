import { defineConfig, devices } from '@playwright/test';

// The live-lane gate has exactly one definition; this file reads the env-var
// NAME from it rather than repeating the literal, which is how the same string
// came to live in two places before.
import { LIVE_STACK_ENV } from './scripts/live-lane-registry.mjs';

/**
 * Playwright config — Sprint S1 baseline.
 *
 * - 1 baseline browser (chromium) + 1 viewport (desktop 1280x720). Sprint
 *   S8 expands to mobile + firefox + webkit for browser-QA evidence.
 * - Dev server is reused across tests via `webServer` block so CI does
 *   not race the port.
 * - Trace + screenshot on retry only — keeps CI artefact size sane.
 */
const E2E_BASE_URL = process.env['E2E_BASE_URL'] ?? 'http://localhost:5173';
// A remote Docker browser reaches the host through its Docker-only alias, but
// Playwright's webServer lifecycle still runs on the host. Keep its readiness
// URL host-resolvable instead of polling the rewritten browser base URL.
const E2E_WEB_SERVER_URL = process.env['E2E_WEB_SERVER_URL'] ?? E2E_BASE_URL;
const E2E_WEB_SERVER_HOST_ARG = process.env['E2E_WEB_SERVER_HOST']
  ? ` --host ${process.env['E2E_WEB_SERVER_HOST']}`
  : '';
const LIVE_STACK_E2E = process.env[LIVE_STACK_ENV] === '1';
const LOCAL_AUTH_LIVE_CAPTURE_OFF = process.env['FCC_LOCAL_AUTH_LIVE_CAPTURE_OFF'] === '1';
// M10 (w4-entry-route-e2e-oidc-ci, 2026-08-01) — `webServer` used to hardcode
// its port independently of E2E_BASE_URL above, so overriding E2E_BASE_URL
// alone left the spawned preview server on the wrong port while Playwright
// polled the right one. Derive the port from the same value instead — the
// port literal on the line above is now the only one left (single source).
const E2E_SERVER_PORT = deriveE2eServerPort(E2E_WEB_SERVER_URL);
function deriveE2eServerPort(baseUrl: string): string {
  const { port, protocol } = new URL(baseUrl);
  return port || (protocol === 'https:' ? '443' : '80');
}

export default defineConfig({
  testDir: './tests/e2e',
  globalSetup: './playwright.global-setup.cjs',
  fullyParallel: true,
  forbidOnly: !!process.env['CI'],
  retries: process.env['CI'] ? 2 : 0,
  workers: process.env['CI'] ? 1 : undefined,
  reporter: LOCAL_AUTH_LIVE_CAPTURE_OFF
    ? [['list']]
    : process.env['CI']
      ? [['github'], ['html', { outputFolder: 'playwright-report', open: 'never' }]]
      : [['list'], ['html', { open: 'never' }]],
  // Keep the visual-regression snapshot path aligned with the tracked SSOT
  // names. Without a project-independent template, the default Chromium
  // project appends `-chromium-desktop-linux` while the visual project does
  // not, so the same matrix is compared against two different paths.
  snapshotPathTemplate: '{testDir}/ui-visual-regression.spec.ts-snapshots/{arg}{ext}',
  use: {
    baseURL: E2E_BASE_URL,
    trace: LOCAL_AUTH_LIVE_CAPTURE_OFF ? 'off' : 'on-first-retry',
    screenshot: LOCAL_AUTH_LIVE_CAPTURE_OFF ? 'off' : 'only-on-failure',
    video: LOCAL_AUTH_LIVE_CAPTURE_OFF ? 'off' : 'retain-on-failure',
    actionTimeout: 5000,
    navigationTimeout: 15000,
    // Production is English-first (`DEFAULT_LOCALE = 'en'`), but the e2e specs
    // assert the Korean rendered copy. Seed `fcc-locale='ko'` into localStorage
    // for the base origin so every context renders Korean before any page
    // script runs — keeps the Korean assertions decoupled from the production
    // default without editing each spec. (Origin must match `baseURL`; an
    // `E2E_BASE_URL` override pointed at a different origin would not be seeded.)
    storageState: {
      cookies: [],
      origins: [
        {
          origin: new URL(E2E_BASE_URL).origin,
          localStorage: [{ name: 'fcc-locale', value: 'ko' }],
        },
      ],
    },
  },
  projects: [
    {
      name: 'chromium-desktop',
      // The visual matrix belongs to `chromium-visual` alone
      // (e2e-visual-lane-repair, 2026-08-10). `snapshotPathTemplate` below is
      // deliberately project-independent, so both projects resolved the SAME
      // golden file — but only `chromium-visual` pins what makes a screenshot
      // reproducible (`deviceScaleFactor: 1`, `locale`, `timezoneId`, and
      // `toHaveScreenshot.threshold: 0.01`). This project pins none of them, so
      // it was comparing against that project's goldens under a laxer default
      // threshold and an unpinned scale/locale: a duplicate run that could fail
      // where the owning project passes, or pass where it fails.
      //
      // `test:e2e:visual:update` regenerates goldens with `--project=chromium-visual`,
      // which is the other half of the same statement: one project authors the
      // goldens, so one project must verify them.
      testIgnore: /ui-visual-regression\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 720 } },
    },
    {
      name: 'chromium-visual',
      testMatch: /ui-visual-regression\.spec\.ts/,
      expect: {
        toHaveScreenshot: {
          animations: 'disabled',
          caret: 'hide',
          threshold: 0.01,
        },
      },
      use: {
        ...devices['Desktop Chrome'],
        contextOptions: { reducedMotion: 'reduce' },
        deviceScaleFactor: 1,
        locale: 'ko-KR',
        timezoneId: 'Asia/Seoul',
        viewport: { width: 1280, height: 900 },
      },
    },
  ],
  webServer: LIVE_STACK_E2E
    ? undefined
    : {
        // Phase 2 follow-up (fe-phase2-followup, 2026-05-30) — restored `vite
        // preview` so e2e validates the actual prod build artifact (build/minify/
        // chunking) instead of silently sidestepping it via dev mode. The CSP
        // blocking the localhost API origin under preview is solved on the *server*
        // side: `VITE_E2E=1` triggers `previewE2eCspMetaStripPlugin` to strip the
        // prod meta CSP from the preview-served HTML AND `preview.headers` to inject
        // a dev-derived `Content-Security-Policy` that allowlists the mock origin.
        // `dist/index.html` on disk stays byte-identical to the prod artifact.
        command: `npm run preview -- --port ${E2E_SERVER_PORT}${E2E_WEB_SERVER_HOST_ARG}`,
        url: E2E_WEB_SERVER_URL,
        // Opt-IN, not opt-out (gate-and-deploy-path-parity, 2026-08-01). This was
        // `!process.env['CI']`, which contradicted the paragraph above: locally,
        // anything already listening on the port was reused — and `npm run dev`
        // binds the SAME port this config derives from `E2E_BASE_URL`, so a running
        // dev server silently replaced the prod build this block exists to
        // validate. Every spec then ran against
        // unminified, unchunked, unhashed dev output with `VITE_E2E` never set;
        // dev strips the meta CSP by itself, so the suite went green while
        // validating nothing it claimed to. `route-resilience.spec.ts` aborts
        // `**/assets/jobs-*.js`, a pattern only the built hash chunks produce, so
        // its assertion was not even expressible under dev.
        //
        // Now the preview server is always spawned. If something else holds the
        // port, `preview.strictPort` fails immediately and loudly instead of
        // quietly mis-validating — that swap is the whole point. Set
        // `E2E_REUSE_SERVER=1` when the listener really is your own `npm run
        // preview` and you want the fast loop back.
        //
        // CI behaviour is unchanged: it never reused a server.
        reuseExistingServer: process.env['E2E_REUSE_SERVER'] === '1' && !process.env['CI'],
        timeout: 60_000,
        stdout: 'pipe',
        stderr: 'pipe',
        // Playwright forwards this env to the spawned webServer process, so the
        // VITE_E2E gate fires WITHOUT a `cross-env` devDependency or a brittle
        // Unix-style `VITE_E2E=1 npm ...` command (which Windows shells reject).
        env: { VITE_E2E: '1' },
      },
});
