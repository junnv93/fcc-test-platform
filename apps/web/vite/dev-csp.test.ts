import { describe, expect, it } from 'vitest';

import {
  buildDevCsp,
  deriveConnectSrcDirective,
  extractMetaCsp,
  stripCspMeta,
  withConnectSrc,
  withDevScriptSrc,
} from './dev-csp';

/**
 * Dev CSP helper unit test (architecture plan §4.2 Acceptance ① — "dev CSP
 * header 생성 헬퍼 단위 테스트: origin → connect-src 파생").
 *
 * Lives under vite/ (not tests/) so the `.mjs` shared loader it transitively
 * imports stays under tsconfig.node.json (allowJs) and never enters the
 * no-allowJs tsconfig.json program. Registered into vitest `include` via
 * `vite/**\/*.test.ts`.
 */

// Mirrors public/runtime-config.dev.json origin-bearing fields. Injected so the
// pure derivation is tested without file I/O.
const DEV_CONFIG = {
  apiBaseUrl: 'http://127.0.0.1:8000',
  platformApiBaseUrl: null,
  wsBaseUrl: 'ws://127.0.0.1:8000',
  oidcIssuer: 'http://localhost:8081/realms/fcc-dev',
  otelCollectorUrl: null,
  sentryDsn: null,
} as const;

const PROD_META_CSP =
  "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
  "img-src 'self' data: blob:; font-src 'self' data:; " +
  "connect-src 'self' https: wss:; frame-ancestors 'none'; base-uri 'self'; " +
  "form-action 'self'; object-src 'none'";

describe('deriveConnectSrcDirective', () => {
  it('derives connect-src origins from the runtime-config URL fields', () => {
    const directive = deriveConnectSrcDirective(DEV_CONFIG);
    expect(directive).toContain("connect-src 'self'");
    expect(directive).toContain('http://127.0.0.1:8000'); // apiBaseUrl
    expect(directive).toContain('ws://127.0.0.1:8000'); // wsBaseUrl
    expect(directive).toContain('http://localhost:8081'); // oidcIssuer origin
  });

  it('is host-limited — no broad http:/wildcard scheme sources', () => {
    const directive = deriveConnectSrcDirective(DEV_CONFIG);
    const tokens = directive.split(/\s+/);
    expect(tokens).not.toContain('*');
    expect(tokens).not.toContain('http:');
    expect(tokens).not.toContain('https:');
    expect(tokens).not.toContain('ws:');
    expect(tokens).not.toContain('wss:');
  });

  it('appends extra origins (e.g. the Vite HMR websocket)', () => {
    const directive = deriveConnectSrcDirective(DEV_CONFIG, ['ws://localhost:5173']);
    expect(directive).toContain('ws://localhost:5173');
  });

  it('collapses duplicate origins from different fields', () => {
    const directive = deriveConnectSrcDirective({
      apiBaseUrl: 'http://127.0.0.1:8000/a',
      wsBaseUrl: 'http://127.0.0.1:8000/b',
    });
    const occurrences = directive.split('http://127.0.0.1:8000').length - 1;
    expect(occurrences).toBe(1);
  });
});

describe('withConnectSrc', () => {
  it('replaces the prod connect-src with the dev allowlist', () => {
    const out = withConnectSrc(PROD_META_CSP, ['http://127.0.0.1:8000']);
    expect(out).toContain("connect-src 'self' http://127.0.0.1:8000");
    // The broad prod connect-src sources are gone.
    expect(out).not.toMatch(/connect-src[^;]*\bhttps:/);
    expect(out).not.toMatch(/connect-src[^;]*\bwss:/);
    // Other directives are preserved verbatim.
    expect(out).toContain("frame-ancestors 'none'");
    expect(out).toContain("object-src 'none'");
  });
});

describe('withDevScriptSrc', () => {
  it("adds 'unsafe-inline' to script-src for the Vite/React dev preamble", () => {
    const out = withDevScriptSrc(PROD_META_CSP);
    expect(out).toMatch(/script-src[^;]*'unsafe-inline'/);
  });

  it('is idempotent when unsafe-inline already present', () => {
    const once = withDevScriptSrc(PROD_META_CSP);
    expect(withDevScriptSrc(once)).toBe(once);
  });
});

describe('extractMetaCsp / stripCspMeta', () => {
  const HTML = `<!doctype html><html><head>
    <meta
      http-equiv="Content-Security-Policy"
      content="default-src 'self'; connect-src 'self' https: wss:"
    />
    <title>x</title></head><body></body></html>`;

  it('extracts the meta CSP content attribute (multi-line tag)', () => {
    expect(extractMetaCsp(HTML)).toBe("default-src 'self'; connect-src 'self' https: wss:");
  });

  it('removes the meta CSP tag', () => {
    const stripped = stripCspMeta(HTML);
    expect(stripped).not.toMatch(/Content-Security-Policy/);
    expect(stripped).toContain('<title>x</title>');
  });
});

describe('buildDevCsp (injected — no file I/O)', () => {
  it('produces a dev CSP with localhost connect-src + inline script-src', () => {
    const csp = buildDevCsp({
      prodCsp: PROD_META_CSP,
      config: DEV_CONFIG,
      extraOrigins: ['ws://localhost:5173'],
    });
    expect(csp).toContain("connect-src 'self'");
    expect(csp).toContain('http://127.0.0.1:8000');
    expect(csp).toContain('ws://localhost:5173');
    expect(csp).toMatch(/script-src[^;]*'unsafe-inline'/);
    expect(csp).not.toMatch(/connect-src[^;]*\bhttps:/);
    expect(csp).not.toContain('connect-src *');
  });
});
