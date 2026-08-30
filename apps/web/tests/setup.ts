import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach } from 'vitest';

import '@testing-library/jest-dom/vitest';

import { setLocale } from '@/i18n';

// vitest does not run @testing-library/react's auto-cleanup detection, so
// React component tests would leak mounted nodes between cases (a single
// `getByTestId` then matches multiples — see Sprint S2 route-guard.test.tsx).
afterEach(() => {
  cleanup();
});

// Production is English-first (`DEFAULT_LOCALE = 'en'`), but the component/route
// test suites assert the Korean rendered copy. Pin the rendered locale to `ko`
// before every test so those assertions stay decoupled from the production
// default. Files that exercise locale switching itself (i18n-parity,
// control.test) override this in their own `beforeEach`/case body.
//
// `setLocale` persists to localStorage, but the locale is only read FROM
// localStorage once at module load (`resolveInitialLocale`); the in-memory
// `currentLocale` is what `t()`/`getLocale()` read at runtime. So we remove the
// persisted key right after pinning — the in-memory `ko` stays in effect, and
// localStorage stays clean for tests that assert it is empty (e.g. the auth
// session security test that tokens are never written to localStorage).
beforeEach(() => {
  setLocale('ko');
  try {
    globalThis.localStorage?.removeItem('fcc-locale');
  } catch {
    // localStorage may be unavailable in a non-DOM env — pinning still holds.
  }
});

/**
 * Vitest setup — applied before each test file.
 *
 * - `@testing-library/jest-dom` matchers for accessibility / DOM assertions.
 * - Runtime config stub: tests inject their own payload via
 *   `__resetRuntimeConfigCacheForTests`. Tests that need a different shape
 *   override per-test.
 */

declare global {
  // eslint-disable-next-line no-var
  var __FCC_TEST_DEFAULT_RUNTIME_CONFIG__: unknown;
}

globalThis.__FCC_TEST_DEFAULT_RUNTIME_CONFIG__ = {
  apiBaseUrl: 'http://127.0.0.1:8000',
  platformApiBaseUrl: null,
  wsBaseUrl: 'ws://127.0.0.1:8000',
  oidcIssuer: 'http://localhost:8081/realms/fcc-test',
  oidcClientId: 'fcc-platform-frontend',
  oidcRedirectUri: 'http://localhost:5173/auth/callback',
  oidcAudience: '',
  oidcScopes: ['openid', 'profile', 'email'],
  oidcPostLogoutRedirectUri: 'http://localhost:5173/',
  environmentName: 'test',
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

// jsdom (vitest env: 'jsdom') already provides a `window`, so the previous
// `??=` form on `globalThis.window` was a no-op and `__FCC_RUNTIME_CONFIG__`
// never landed on the real `window`. That broke any test file whose import
// graph touched `getRuntimeConfig()` at module load (e.g. api clients).
// Seed the existing `window` directly so production-shaped modules can be
// imported without per-test boilerplate.
type RuntimeWindow = Window & { __FCC_RUNTIME_CONFIG__?: unknown };
if (typeof window !== 'undefined') {
  const w = window as RuntimeWindow;
  if (w.__FCC_RUNTIME_CONFIG__ === undefined) {
    w.__FCC_RUNTIME_CONFIG__ = globalThis.__FCC_TEST_DEFAULT_RUNTIME_CONFIG__;
  }
} else {
  // Non-DOM test environment fallback (e.g. node:test runners). Keeps the
  // module evaluable even without a jsdom-provided window.
  (globalThis as { window?: RuntimeWindow }).window = {
    __FCC_RUNTIME_CONFIG__: globalThis.__FCC_TEST_DEFAULT_RUNTIME_CONFIG__,
  } as RuntimeWindow;
}
