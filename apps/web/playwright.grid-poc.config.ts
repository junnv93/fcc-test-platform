import { defineConfig, devices } from '@playwright/test';

/**
 * Sprint 3 — grid-poc evidence collection 전용 Playwright config.
 *
 * 표준 `playwright.config.ts` 는 production build artifact 를 `npm run preview`
 * 로 serve 한다. ADR-0008 PoC 는 `apps/web/src/app.tsx` 의 dev-only dynamic
 * import gate (`gridPocModulePath` + `@vite-ignore`) 로 들어가는데, 이 패턴은
 * Vite build-time static analysis 를 우회하므로 PoC module 코드 자체가 production
 * bundle 에 미포함된다. → preview 에서 `/grid-poc` 는 404 (NotFoundRoute fallback).
 *
 * 본 config 는 PoC route 가 실제 활성화되는 dev server 를 webServer 로 띄워
 * 측정한다. 본 sprint 의 evidence 는 dev mode 측정값이며, production 적용 시
 * 측정은 더 빨라야 한다 (lower bound 검증).
 *
 * 실행:
 *   $env:GRID_POC_E2E = '1'
 *   npx playwright test --config=playwright.grid-poc.config.ts
 */
export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'grid-poc.spec.ts',
  fullyParallel: false,
  forbidOnly: !!process.env['CI'],
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: process.env['E2E_BASE_URL'] ?? 'http://127.0.0.1:5174',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    actionTimeout: 10_000,
    navigationTimeout: 30_000,
  },
  projects: [
    {
      name: 'chromium-desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 720 } },
    },
  ],
  webServer: {
    // VITE_GRID_POC=1 → app.tsx 의 dev-gate 활성화. 표준 preview port (5173)
    // 와 분리 (5174) → 표준 config 와 동시 실행 가능 + reuseExistingServer 충돌 회피.
    command: 'npm run dev -- --port 5174 --strictPort',
    url: 'http://127.0.0.1:5174',
    reuseExistingServer: !process.env['CI'],
    timeout: 90_000,
    stdout: 'pipe',
    stderr: 'pipe',
    env: {
      VITE_GRID_POC: '1',
    },
  },
});
