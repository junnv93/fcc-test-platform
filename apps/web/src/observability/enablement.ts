import type { RuntimeConfig } from '@/config/runtime';

/**
 * Observability enablement gate — SSOT (wave `fe-w4-bundle-observability-cost`,
 * 2026-07-31).
 *
 * Each observability backend is activated by *runtime* config, not by a build
 * flag: tracing when an OTel collector URL is configured, Sentry when a DSN is.
 * Those conditions already existed inside `tracing.ts` / `sentry-runtime.ts` as
 * early-return no-ops. Once the modules became *conditionally loaded*, the same
 * condition had to be answered a second time — at the load site — and a copied
 * condition drifts in exactly two directions:
 *
 *   - load site loosens  → the chunk downloads and then no-ops (the defect this
 *     wave exists to remove: 100 kB gzip of dead weight),
 *   - module loosens     → the feature is configured but never loaded, and the
 *     deployment is silently blind.
 *
 * So the predicate lives here once and *both* the loader (`bootstrap.ts`) and
 * the module itself delegate to it. No new activation flag is invented — these
 * functions are a reading of the existing runtime config, not a new axis of it.
 *
 * Pure: no side effects, no imports beyond the config *type*. Safe to call
 * before anything is initialised.
 */

/** Narrowed config views — these predicates read one field each, and taking the
 *  whole `RuntimeConfig` would make them impossible to unit-test without
 *  constructing a full valid config. */
type TracingConfig = Pick<RuntimeConfig, 'otelCollectorUrl'>;
type SentryConfig = Pick<RuntimeConfig, 'sentryDsn'>;

/**
 * True when an OTel collector is configured, i.e. when the OpenTelemetry web
 * SDK would actually export spans. `null` (dev / self-hosted deployment without
 * a collector) and the empty string both mean "off".
 *
 * Declared as a **type predicate** so the gate carries its own consequence: a
 * caller that passed the gate gets `otelCollectorUrl: string` for free and has
 * no reason to reach for a `!` assertion (which is exactly how a second,
 * divergent copy of the condition gets written).
 */
export function isTracingEnabled<T extends TracingConfig>(
  config: T,
): config is T & { otelCollectorUrl: string } {
  return Boolean(config.otelCollectorUrl);
}

/**
 * True when a Sentry DSN is configured, i.e. when the Sentry browser SDK would
 * actually transmit events. `null` (dev / air-gapped deployment) and the empty
 * string both mean "off". Type predicate for the same reason as above.
 */
export function isSentryEnabled<T extends SentryConfig>(
  config: T,
): config is T & { sentryDsn: string } {
  return Boolean(config.sentryDsn);
}
