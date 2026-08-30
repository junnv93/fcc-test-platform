import * as Sentry from '@sentry/browser';

import { setCaptureSink } from './capture-sink';
import { isSentryEnabled } from './enablement';
import { scrubAuthPiiFromEvent } from './pii-scrub';

import type { RuntimeConfig } from '@/config/runtime';
import type { Integration } from '@sentry/types';

/**
 * Sentry browser SDK bootstrap — ADR-0006 SSOT.
 *
 * **Loaded on demand.** This module is the only static importer of
 * `@sentry/browser`; `observability/bootstrap.ts` `import()`s it, and only when
 * `isSentryEnabled(config)` says a DSN is configured. Everything on the initial
 * load path talks to the light façade in `observability/sentry.ts` instead
 * (wave `fe-w4-bundle-observability-cost`, 2026-07-31).
 *
 * The `isSentryEnabled` guard below is retained as a *defence in depth*, not as
 * a second copy of the gate: both this module and the loader delegate to the
 * same predicate, so there is exactly one place where "is Sentry on?" is
 * decided. A direct caller that skips the loader still cannot init a
 * DSN-less Sentry.
 *
 * Privacy:
 *   - `maskAllText: true` — DOM text is redacted in session replays
 *   - `blockAllMedia: true` — `<img>`/`<video>` content blocked
 *   - `sendDefaultPii: false` — IP / cookies / user agent are not auto-attached
 *   - feature-flag-gated: session replay only when `featureFlags.sessionReplay`
 *
 * Tracing interop:
 *   - `tracePropagationTargets` covers backend origin so Sentry inserts its
 *     own headers — OTel `W3CTraceContextPropagator` still wins on traceparent
 *     because Sentry honours the existing header.
 */
export function initSentryRuntime(config: RuntimeConfig): void {
  if (!isSentryEnabled(config)) {
    return;
  }

  // @sentry/browser v8 re-exports the canonical `Integration` interface
  // via @sentry/core (which @sentry/browser depends on). This is the
  // upstream-recommended type for an array that mixes browserTracing /
  // replay / etc. integrations.
  const integrations: Integration[] = [Sentry.browserTracingIntegration()];

  if (config.featureFlags.sessionReplay) {
    integrations.push(
      Sentry.replayIntegration({
        maskAllText: true,
        blockAllMedia: true,
        maskAllInputs: true,
      }),
    );
  }

  Sentry.init({
    dsn: config.sentryDsn,
    environment: config.environmentName,
    release: `${config.buildVersion}+${config.buildSha256.slice(0, 12)}`,
    integrations,
    tracesSampleRate: config.traceSampleRatio,
    replaysSessionSampleRate: config.featureFlags.sessionReplay ? 0.1 : 0,
    replaysOnErrorSampleRate: config.featureFlags.sessionReplay ? 1.0 : 0,
    sendDefaultPii: false,
    tracePropagationTargets: [new URL(config.apiBaseUrl).origin, new URL(config.wsBaseUrl).origin],
    // Sprint S2-δ γ-P0-3 + S2-ε δ-P1-2 + δ-P1-3 — PII scrubbing for
    // auth-flow events ONLY. White-list on `event.extra.authFlow` so
    // unrelated React errors don't pay the recursive-string-traversal
    // cost. `scrubAuthPiiFromEvent` is the single caller (δ-P1-3 dead-
    // export resolved).
    beforeSend(event) {
      const extra = event.extra as { authFlow?: unknown } | undefined;
      if (extra?.authFlow !== undefined) {
        return scrubAuthPiiFromEvent(event);
      }
      return event;
    },
  });

  // Hand the façade a live forward. Until this runs (DSN absent, chunk still in
  // flight, or `Sentry.init` threw) `captureException` keeps its console
  // fallback, so a report is never silently dropped.
  //
  // The `isInitialized` re-check mirrors the pre-wave `captureException` body
  // exactly: if Sentry is somehow not live the sink reports "not delivered" and
  // the façade falls back to `console.error` rather than swallowing the event.
  // The context arriving here is already scrubbed by the façade — Sentry's own
  // `beforeSend` above is the defence-in-depth second pass.
  setCaptureSink((error, scrubbedContext) => {
    if (!Sentry.isInitialized()) {
      return false;
    }
    Sentry.captureException(error, scrubbedContext ? { extra: scrubbedContext } : undefined);
    return true;
  });
}
