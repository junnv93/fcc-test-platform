/**
 * PII scrubbing for Sentry payloads — pure, dependency-free.
 *
 * Extracted verbatim from `observability/sentry.ts` (wave
 * `fe-w4-bundle-observability-cost`, 2026-07-31) so that both the light
 * capture façade (initial load path) and the heavy Sentry runtime (loaded only
 * when a DSN is configured) can scrub without either of them pulling
 * `@sentry/browser` in. The matching rules are unchanged.
 *
 * Sprint S2-δ γ-P0-3 — match conservative PII patterns. The matches are
 * intentionally aggressive (over-scrub) because Sentry data lives forever and
 * any false-negative is a permanent leak.
 */
const EMAIL_PATTERN = /[\w.+-]+@[\w-]+\.[\w.-]+/g;
const NUMERIC_ID_PATTERN = /\b\d{4,}\b/g;
const UUID_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;

function scrubPiiFromString(value: string): string {
  return value
    .replace(EMAIL_PATTERN, '<email-redacted>')
    .replace(UUID_PATTERN, '<uuid-redacted>')
    .replace(NUMERIC_ID_PATTERN, '<id-redacted>');
}

export function scrubPiiFromValue(value: unknown): unknown {
  // Sprint S2-ε δ-P0-2 — circular reference defence. Sentry events can
  // legitimately contain shared sub-objects (e.g. a `context` referenced
  // by multiple breadcrumbs). Without the WeakSet, the naive recursive
  // walk stack-overflows on any cycle — silently in dev, SIGSEGV in
  // prod. Industry standard pattern is the same as `JSON.stringify`'s
  // built-in cycle detection.
  return scrubPiiFromValueImpl(value, new WeakSet<object>());
}

function scrubPiiFromValueImpl(value: unknown, seen: WeakSet<object>): unknown {
  if (typeof value === 'string') return scrubPiiFromString(value);
  if (value === null || typeof value !== 'object') return value;
  // Cycle guard — every object/array entry is at most visited once.
  if (seen.has(value)) return '<circular>';
  seen.add(value);
  if (Array.isArray(value)) return value.map((item) => scrubPiiFromValueImpl(item, seen));
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    out[k] = scrubPiiFromValueImpl(v, seen);
  }
  return out;
}

/** Exported for unit tests + direct caller use (callers can pre-scrub
 *  data they're about to attach as Sentry context). */
export function scrubAuthPiiFromEvent<T extends { extra?: Record<string, unknown> }>(event: T): T {
  if (event.extra) {
    event.extra = scrubPiiFromValue(event.extra) as Record<string, unknown>;
  }
  return event;
}
