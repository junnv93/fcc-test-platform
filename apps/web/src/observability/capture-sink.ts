/**
 * Late-binding sink for `captureException` (wave
 * `fe-w4-bundle-observability-cost`, 2026-07-31).
 *
 * `captureException` is called from the error boundaries and the auth route
 * guard — modules that are on the *initial* load path. Before this wave it
 * imported `@sentry/browser` directly, which is why the 321 kB observability
 * chunk stayed in the entry graph no matter what `main.tsx` did.
 *
 * The inversion: `observability/sentry.ts` (light, always loaded) owns the
 * *call site* and knows nothing about Sentry; `observability/sentry-runtime.ts`
 * (heavy, loaded only when a DSN is configured) registers itself here after
 * `Sentry.init` succeeds. With no sink registered the façade falls back to
 * `console.error` — which is byte-identical to the pre-wave behaviour when
 * `sentryDsn === null`, because `initSentry` returned early and
 * `Sentry.isInitialized()` was false.
 */

/**
 * What the heavy runtime registers. The façade has already scrubbed
 * `scrubbedContext`; the sink returns whether the event was actually delivered
 * so a "registered but not live" Sentry still falls back to the console instead
 * of silently dropping the report.
 */
export type CaptureSink = (error: unknown, scrubbedContext?: Record<string, unknown>) => boolean;

let sink: CaptureSink | undefined;

/** Registered by `sentry-runtime.ts` once `Sentry.init` has run. */
export function setCaptureSink(next: CaptureSink): void {
  sink = next;
}

/** `undefined` until (and unless) the Sentry runtime is loaded + initialised. */
export function getCaptureSink(): CaptureSink | undefined {
  return sink;
}

/** Test-only reset — the sink is module-level process state. */
export function __resetCaptureSinkForTests(): void {
  sink = undefined;
}
