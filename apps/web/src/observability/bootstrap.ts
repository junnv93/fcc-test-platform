import { isSentryEnabled, isTracingEnabled } from './enablement';
import { initWebVitals } from './web-vitals';

import type { RuntimeConfig } from '@/config/runtime';

/**
 * Observability boot — the one place that decides *what gets downloaded*
 * (wave `fe-w4-bundle-observability-cost`, 2026-07-31).
 *
 * ## Why this module exists
 *
 * The OpenTelemetry web SDK and `@sentry/browser` are activated by runtime
 * config, but were imported statically: the resulting `observability` chunk
 * (321.58 kB raw / 100.00 kB gzip) was the single largest asset in the build —
 * bigger than React, bigger than the app itself — and in every deployment
 * without a collector/DSN it was downloaded, parsed, and thrown away. A runtime
 * condition was being paid for at build time.
 *
 * `main.tsx` is the composition root; it should not have to *know* the
 * activation condition, only ask. So the gate + the load + the boot order live
 * here, in the observability domain, and `main.tsx` awaits one call.
 *
 * ## Boot-order contract (the real risk of this change)
 *
 * `main.tsx` documents two ordering guarantees. Both are preserved, by
 * different means:
 *
 *  1. **Tracing before any fetch / route render.** `initObservability` is
 *     `await`ed by `main()` *before* `createRoot().render()`, and the app makes
 *     no network call before React mounts. So the OTel `FetchInstrumentation`
 *     is registered before the first outbound request, exactly as before. The
 *     cost is a real one and worth naming: in a *tracing-enabled* deployment,
 *     first render now waits for one extra module fetch. That is paid only by
 *     deployments that asked for tracing, and it is the price of keeping the
 *     guarantee rather than quietly weakening it. In a disabled deployment
 *     nothing is fetched and `initObservability` settles on a resolved promise
 *     within the same task.
 *
 *  2. **Web Vitals subscribed before paint.** This one cannot be bought back by
 *     awaiting — FCP/LCP are wall-clock observations, and any deferral risks
 *     the metric firing before `onFCP` subscribes. So `web-vitals` stays a
 *     *static* import (decision 3 of the wave plan: partial split is a valid
 *     answer). It is cheap: `web-vitals` + `@opentelemetry/api` are ~5 kB gzip
 *     combined, versus ~95 kB for the SDK/Sentry pair that moved. Splitting on
 *     weight rather than on tidiness is the whole point.
 *
 * ## Failure isolation
 *
 * Observability is auxiliary. A failed chunk download (CDN hiccup, blocked by
 * an ad-blocker, corporate proxy) must not stop the app from rendering — but it
 * must not vanish either, or the deployment is blind and nobody knows. Each
 * stage is therefore isolated and reported to the console.
 */
export async function initObservability(config: RuntimeConfig): Promise<void> {
  // Order matches the pre-wave `main.tsx` sequence: tracing, then Sentry (so it
  // can pick up the active span), then Web Vitals.
  if (isTracingEnabled(config)) {
    await runStage('tracing', async () => {
      const { initTracing } = await import('./tracing');
      initTracing(config);
    });
  }

  if (isSentryEnabled(config)) {
    await runStage('sentry', async () => {
      const { initSentryRuntime } = await import('./sentry-runtime');
      initSentryRuntime(config);
    });
  }

  // Static + synchronous — see guarantee (2) above. Still isolated: a throwing
  // `web-vitals` subscription must not take the app's first render with it.
  try {
    initWebVitals();
  } catch (error) {
    console.error('[observability] web-vitals failed to initialise:', error);
  }
}

/**
 * Run one observability stage so that its failure is contained *and* visible.
 *
 * Swallowing would make a dead observability backend indistinguishable from a
 * healthy one; rethrowing would let a telemetry problem blank the screen.
 * `console.error` is on the eslint `no-console` allow list precisely for this
 * class of operator-facing diagnostic.
 */
async function runStage(name: string, stage: () => Promise<void>): Promise<void> {
  try {
    await stage();
  } catch (error) {
    console.error(`[observability] ${name} failed to initialise:`, error);
  }
}
