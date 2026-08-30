import { createRoot } from 'react-dom/client';

import { App } from './app';
import { initMultiTabAuthSync } from './auth/multi-tab-sync';
import { restoreSession } from './auth/session';
import { purgeLegacyStorage } from './auth/storage-keys';
import { getRuntimeConfig, RuntimeConfigError } from './config/runtime';
import { t } from './i18n';
import { initObservability } from './observability/bootstrap';
import '@fontsource-variable/noto-sans-kr';
import './styles/global.css';

import type { RuntimeConfig } from './config/runtime';

/**
 * Bootstrap order is intentional:
 *
 *   1. Runtime config (Zod-validated, fail-fast on misconfig).
 *   2. Tracing (must be initialised *before* any fetch / route renders so
 *      the W3C TraceContext propagator wraps every outbound request).
 *   3. Sentry (after tracing so Sentry can pick up the active span).
 *   4. Web Vitals (collects FCP/LCP/INP — must subscribe before paint).
 *   5. React render.
 *
 * Steps 2–4 are owned by `observability/bootstrap.ts`, which also decides which
 * of them are *downloaded at all* — the OTel SDK and Sentry are activated by
 * runtime config, so shipping them unconditionally cost every deployment
 * 100 kB gzip of code that early-returns (wave
 * `fe-w4-bundle-observability-cost`, 2026-07-31). The composition root asks;
 * it does not hold a copy of the activation condition. `await` keeps step 2
 * ahead of the first render, and therefore ahead of the first fetch.
 *
 * If runtime config is missing/invalid we render a fallback message
 * directly into #root — React never mounts. This guarantees a
 * misconfigured deployment surfaces visibly instead of silently rendering
 * an empty page.
 */
function main(): void {
  const rootElement = document.getElementById('root');
  if (!rootElement) {
    throw new Error('Application root element (#root) missing from index.html');
  }

  let config;
  try {
    config = getRuntimeConfig();
  } catch (error) {
    renderBootError(rootElement, error);
    return;
  }

  // `main` stays synchronous so the two failure paths above keep their exact
  // pre-wave semantics: a missing `#root` throws *synchronously* (not as an
  // unhandled rejection), and a `RuntimeConfigError` still paints before any
  // microtask runs. Only the tail — which now waits on a conditional module
  // load — becomes asynchronous.
  void boot(rootElement, config);
}

async function boot(rootElement: HTMLElement, config: RuntimeConfig): Promise<void> {
  // Never rejects — every stage is isolated inside `initObservability`, because
  // telemetry failing is not a reason for the operator to see a blank screen.
  await initObservability(config);
  // Sprint S2-β α-7 — drop any sessionStorage keys left behind by the
  // legacy `web/platform-shell/auth.js` prototype before the new auth
  // subsystem rehydrates. Idempotent — runs once per page load.
  purgeLegacyStorage();
  // Rehydrate any persisted OIDC session before React mounts so the first
  // render of <RequireAuth> sees the correct authenticated/unauthenticated
  // state without flicker.
  restoreSession();
  // Increment 6 — subscribe to cross-tab sign-out broadcasts so a logout in any
  // tab clears this tab's (per-tab) session too. Boot-scoped: the listener
  // lives for the page lifetime, so the teardown handle is intentionally unused.
  initMultiTabAuthSync();

  createRoot(rootElement).render(<App />);
}

function renderBootError(target: HTMLElement, error: unknown): void {
  const message = error instanceof RuntimeConfigError ? error.message : t('shared.bootError');
  // Plain DOM — never depend on React when the failure may be inside React init.
  // Styling flows from the `.boot-error` token class (global.css) rather than
  // imperative element.style assignments, so colour/spacing/font stay in the
  // design-token SSOT and the conformance gate (card F3) covers this path too.
  const pre = document.createElement('pre');
  pre.setAttribute('role', 'alert');
  pre.setAttribute('data-testid', 'boot-error');
  pre.className = 'boot-error';
  pre.textContent = `${t('shared.bootErrorHeading')}\n\n${message}`;
  target.replaceChildren(pre);
}

main();
