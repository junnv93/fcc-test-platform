// Shared runtime-config "origin source" loader — Node runtime only, NOT bundled
// into the browser app (lives in vite/, outside src/).
//
// Phase 0 (2026-05-29) — Single source for "where runtime-config values come
// from + how an allowlist of connect-src origins is derived from them". Both
// the dev CSP helper (vite/dev-csp.ts) and the Lighthouse stub writer
// (scripts/write-runtime-config-stub.mjs) import this so the dev/CSP origin
// input is shared, never duplicated (architecture plan §4.2: "stub 생성기와
// dev CSP 생성기가 동일 단일 입력").
//
// We do NOT eval/parse the served JS stub (public/runtime-config.js). The
// parseable JSON sources below are the SSOT:
//   - dev      → public/runtime-config.dev.json  (http://127.0.0.1 origins)
//   - template → public/runtime-config.template.json (prod https origins; the
//                Lighthouse CI stub source)
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/** @typedef {'dev' | 'template'} RuntimeConfigWhich */

/** Relative paths (from the app root) of the parseable runtime-config sources. */
export const RUNTIME_CONFIG_SOURCES = Object.freeze({
  dev: 'public/runtime-config.dev.json',
  template: 'public/runtime-config.template.json',
});

// JSON-only meta keys that are not part of the RuntimeConfig schema and must be
// stripped before the object is treated as a RuntimeConfig payload.
const META_KEYS = Object.freeze(['$schema', '_comment']);

// RuntimeConfig fields that carry a URL the browser actually connects to. Each
// non-null value contributes its origin to the dev connect-src allowlist. This
// list is intentionally explicit (not "every string that parses as a URL") so a
// future non-connect URL field cannot silently widen the dev CSP.
const CONNECT_URL_FIELDS = Object.freeze([
  'apiBaseUrl',
  'platformApiBaseUrl',
  'wsBaseUrl',
  'oidcIssuer',
  'otelCollectorUrl',
  'sentryDsn',
]);

/**
 * Load + parse a runtime-config source, stripping JSON-only meta keys.
 *
 * @param {RuntimeConfigWhich} which
 * @param {string} appRoot - absolute path to apps/web (defaults to cwd; npm
 *   scripts run from apps/web). Callers run directly via `node` (cwd-stable)
 *   or are bundled into the Vite config (cwd = apps/web at dev start).
 * @returns {Record<string, unknown>}
 */
export function loadRuntimeConfig(which, appRoot = process.cwd()) {
  const rel = RUNTIME_CONFIG_SOURCES[which];
  if (!rel) {
    throw new Error(`unknown runtime-config source: ${String(which)}`);
  }
  const parsed = JSON.parse(readFileSync(resolve(appRoot, rel), 'utf8'));
  for (const key of META_KEYS) {
    delete parsed[key];
  }
  return parsed;
}

/**
 * Derive a sorted, de-duplicated list of connect-src origins from a
 * runtime-config object. Uses the WHATWG URL parser (`new URL(...).origin`) so
 * the host/port are never re-hardcoded in the CSP helper.
 *
 * @param {Record<string, unknown>} config
 * @param {readonly string[]} [extraOrigins] - additional origins to allow
 *   (e.g. the Vite HMR websocket origin), already in `scheme://host:port` form.
 * @returns {string[]}
 */
export function deriveConnectSrcOrigins(config, extraOrigins = []) {
  const origins = new Set();
  for (const field of CONNECT_URL_FIELDS) {
    const value = config[field];
    if (typeof value !== 'string' || value === '') continue;
    try {
      origins.add(new URL(value).origin);
    } catch {
      // A malformed URL in a dev/template config is a config bug surfaced by
      // the Zod schema (runtime.ts) / the stub drift guard — not the CSP
      // helper's job to validate. Skip rather than crash the dev server.
    }
  }
  for (const extra of extraOrigins) {
    if (typeof extra === 'string' && extra !== '') origins.add(extra);
  }
  return [...origins].sort();
}

// ── Dev stub generation (single-source — runtime-config.dev.json is the SSOT) ──
//
// The browser-served dev stub (public/runtime-config.js) is GENERATED from
// runtime-config.dev.json so the dev origins are authored in exactly ONE place.
// The CSP helper and the served stub both derive from the same JSON SSOT — no
// value is hand-copied into two files.

const DEV_STUB_BANNER =
  '// AUTO-GENERATED — do not edit by hand.\n' +
  '// Source of truth: public/runtime-config.dev.json (single dev runtime-config SSOT).\n' +
  '// Regenerate: `npm run dev` (predev hook) or `node scripts/write-dev-runtime-config.mjs`.\n' +
  '// Drift-checked: `node scripts/write-dev-runtime-config.mjs --check` (CI) +\n' +
  '// vitest vite/dev-runtime-config-gen.test.ts (byte-equality vs runtime-config.dev.json).\n';

/**
 * Serialize a JSON-safe value as a JS literal in the canonical dev-stub style
 * (single-quoted strings, unquoted identifier keys, trailing commas). Only the
 * value shapes a RuntimeConfig contains (string / number / boolean / null /
 * string[] / one-level object) are supported — anything else throws so a schema
 * change cannot silently emit malformed JS.
 *
 * @param {unknown} value
 * @param {string} indent - current indent for nested object members
 * @returns {string}
 */
function formatJsLiteral(value, indent) {
  if (value === null) return 'null';
  if (typeof value === 'boolean' || typeof value === 'number') return String(value);
  if (typeof value === 'string') {
    return `'${value.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => formatJsLiteral(item, indent)).join(', ')}]`;
  }
  if (typeof value === 'object') {
    const inner = Object.entries(value)
      .map(([key, member]) => `${indent}  ${key}: ${formatJsLiteral(member, `${indent}  `)}`)
      .join(',\n');
    return `{\n${inner},\n${indent}}`;
  }
  throw new Error(`unsupported runtime-config value type: ${typeof value}`);
}

/**
 * Render the deterministic `public/runtime-config.js` dev stub text from a
 * runtime-config object (meta keys already stripped). Deterministic: same
 * input → byte-identical output, so a drift `--check` is exact.
 *
 * @param {Record<string, unknown>} config
 * @returns {string}
 */
export function renderDevStubJs(config) {
  const body = Object.entries(config)
    .map(([key, value]) => `  ${key}: ${formatJsLiteral(value, '  ')}`)
    .join(',\n');
  return `${DEV_STUB_BANNER}window.__FCC_RUNTIME_CONFIG__ = {\n${body},\n};\n`;
}
