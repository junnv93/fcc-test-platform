import { trace } from '@opentelemetry/api';
import { onCLS, onINP, onLCP, onTTFB, onFCP, type Metric } from 'web-vitals';

/**
 * Core Web Vitals collection — ADR-0006 RUM layer SSOT.
 *
 * Exports 5 standard metrics (CLS / INP / LCP / TTFB / FCP) as
 * attributes on the active OpenTelemetry span. The OTel collector
 * fan-outs to Sentry / Datadog / Honeycomb (vendor-neutral).
 *
 * If OTel tracing is disabled (no collectorUrl), metrics fall back to
 * `console.warn` so dev still surfaces budget regressions locally.
 *
 * Sprint S8 (production checklist) will ratchet thresholds with Lighthouse CI.
 * The thresholds below mirror current web-vitals lib `rating` boundaries:
 *   - CLS:  good ≤ 0.1   needs-improvement ≤ 0.25
 *   - INP:  good ≤ 200ms needs-improvement ≤ 500ms
 *   - LCP:  good ≤ 2500ms needs-improvement ≤ 4000ms
 *   - TTFB: good ≤ 800ms needs-improvement ≤ 1800ms
 *   - FCP:  good ≤ 1800ms needs-improvement ≤ 3000ms
 */
export function initWebVitals(): void {
  onCLS(reportMetric);
  onINP(reportMetric);
  onLCP(reportMetric);
  onTTFB(reportMetric);
  onFCP(reportMetric);
}

/**
 * Logical screen (route pattern) the current metric belongs to, e.g.
 * "/sessions" or "/projects/:id". Module-level so the route-aware effect in
 * app.tsx can push the active pattern without threading it through web-vitals'
 * own callbacks (which fire outside React render).
 *
 * PRIVACY: this MUST never hold a raw URL, query string, or concrete id — only
 * a normalized pathname pattern. `setActiveScreen` runs every value through
 * `normalizeScreen` so callers cannot accidentally leak ids.
 */
let activeScreen = '/';

/**
 * Normalize a pathname (and optional query string) into a privacy-safe route
 * pattern that can be safely attached to telemetry attributes.
 *
 * Hard rules:
 *  - Query string / hash are always stripped (they can carry ids/PII).
 *  - Numeric segments and UUID segments are replaced with `:id`.
 *  - Everything else (the static route names) is preserved.
 *
 * Examples:
 *   "/sessions?session=123"                          -> "/sessions"
 *   "/projects/42"                                   -> "/projects/:id"
 *   "/projects/42/sessions/7"                        -> "/projects/:id/sessions/:id"
 *   "/projects/3fa85f64-5717-4562-b3fc-2c963f66afa6" -> "/projects/:id"
 *   "/"                                              -> "/"
 *   ""                                               -> "/"
 */
export function normalizeScreen(input: string): string {
  // Drop query string and hash up front — never let them reach attributes.
  // `split` always yields at least one element; the `?? ''` keeps TS strict happy.
  const pathOnly = (input.split('?')[0] ?? '').split('#')[0] ?? '';
  if (pathOnly === '' || pathOnly === '/') {
    return '/';
  }

  const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const NUMERIC_RE = /^\d+$/;

  const normalizedSegments = pathOnly.split('/').map((segment) => {
    if (segment === '') {
      return segment;
    }
    if (NUMERIC_RE.test(segment) || UUID_RE.test(segment)) {
      return ':id';
    }
    return segment;
  });

  const joined = normalizedSegments.join('/');
  // Collapse a possible trailing slash (but keep the root "/").
  return joined.length > 1 && joined.endsWith('/') ? joined.slice(0, -1) : joined;
}

/**
 * Set the active logical screen for subsequent web-vitals reports. The value is
 * always normalized first, so even if a caller passes a raw pathname with ids
 * or query params, only the safe pattern is stored.
 */
export function setActiveScreen(screen: string): void {
  activeScreen = normalizeScreen(screen);
}

/** Current normalized screen — exported for tests/diagnostics. */
export function getActiveScreen(): string {
  return activeScreen;
}

function reportMetric(metric: Metric): void {
  const span = trace.getActiveSpan();
  if (span) {
    span.setAttributes({
      [`web_vitals.${metric.name.toLowerCase()}.value`]: metric.value,
      [`web_vitals.${metric.name.toLowerCase()}.rating`]: metric.rating,
      [`web_vitals.${metric.name.toLowerCase()}.id`]: metric.id,
      [`web_vitals.${metric.name.toLowerCase()}.delta`]: metric.delta,
      // Privacy-safe route pattern only — see normalizeScreen / setActiveScreen.
      'web_vitals.screen': activeScreen,
    });
  } else if (metric.rating !== 'good') {
    // Dev fallback — no tracing yet, but a non-good metric still deserves attention.

    console.warn(
      `[web-vitals] ${metric.name}=${metric.value} (${metric.rating}) screen=${activeScreen}`,
    );
  }
}
