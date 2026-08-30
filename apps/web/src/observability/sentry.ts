import { getCaptureSink } from './capture-sink';
import { scrubPiiFromValue } from './pii-scrub';

export { scrubAuthPiiFromEvent } from './pii-scrub';

/**
 * Sentry *capture façade* — light, always on the initial load path.
 *
 * Wave `fe-w4-bundle-observability-cost` (2026-07-31): this module used to be
 * the Sentry bootstrap and statically imported `@sentry/browser`. Because
 * `shared/error-boundary.tsx` and `auth/route-guard.tsx` import
 * `captureException` from here, that single import kept the whole 321 kB
 * observability chunk inside the entry graph — deleting the `main.tsx` import
 * alone would not have moved it. So the split runs along the *weight* line, not
 * the file line:
 *
 *   sentry.ts          (here)  — call site, zero SDK weight, always loaded
 *   sentry-runtime.ts          — `@sentry/browser` + `Sentry.init`, loaded only
 *                                when `isSentryEnabled(config)`
 *
 * The two are joined at runtime by `capture-sink.ts`. The public surface of
 * this module (`captureException`, `scrubAuthPiiFromEvent`) is unchanged, so
 * every existing caller and test keeps importing `@/observability/sentry`.
 *
 * `initSentry` moved to `sentry-runtime.ts` as `initSentryRuntime`; boot goes
 * through `observability/bootstrap.ts::initObservability`.
 */

/** Sprint S2-δ γ-P0-5 — `captureException` no longer silently drops
 *  events when Sentry isn't initialised (no DSN configured, or the runtime
 *  chunk has not finished loading). In that case we fall back to
 *  `console.error` so the operator still sees the failure in browser
 *  devtools instead of an invisible no-op.
 *
 *  When initialised, the context is pre-scrubbed via `scrubPiiFromValue`
 *  *and* the `beforeSend` Sentry hook applies the same scrubber as a
 *  defence-in-depth — either path alone catches the leak. */
export function captureException(error: unknown, context?: Record<string, unknown>): void {
  const scrubbedContext = context
    ? (scrubPiiFromValue(context) as Record<string, unknown>)
    : undefined;
  const sink = getCaptureSink();
  if (sink?.(error, scrubbedContext) === true) {
    return;
  }
  // Defensive console fallback. Strip PII even here — devtools logs can
  // leak via screenshots / pair programming. `console.error` is on the
  // eslint `allow` list (see eslint.config.js `no-console` rule).
  console.error('[auth] captureException (Sentry not initialised):', error, scrubbedContext ?? '');
}
