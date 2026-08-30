// Dev Content-Security-Policy helper — Node runtime only, NOT bundled into the
// browser app (lives in vite/, outside src/). Phase 0 (2026-05-29).
//
// PROBLEM (architecture plan §4.2): the prod meta CSP in index.html pins a
// conservative connect-src that blocks the local dev API/IdP loopback http
// origins — so dev shows connection failures instead of data.
//
// SOLUTION — dev/prod source separation, NOT a loosened prod policy:
//   - prod  : the conservative meta CSP in index.html stays byte-unchanged;
//             nginx/CDN override it with the same/stricter header (S8).
//   - dev   : the Vite dev server sends a `Content-Security-Policy` HTTP header
//             whose connect-src is DERIVED from the dev runtime-config origins.
//             This module hardcodes NO host/port — every origin comes from
//             runtime-config-source.mjs (the dev SSOT JSON).
//
// CSP COMBINATION CAVEAT: a browser enforces the INTERSECTION of every CSP it
// receives (meta + header). Leaving the strict meta in the dev-served HTML
// would re-block the loopback origins even though the header allows them. So in
// dev (serve only) we strip the meta tag from the served HTML
// (`devCspMetaStripPlugin`) and let the header be the single authoritative
// policy. The built dist/ and the source index.html keep the meta untouched.
//
// The non-connect directives (default-src/script-src/style-src/...) are taken
// from the prod meta itself — index.html is the SSOT for the directive set, so
// dev cannot drift from prod except in the two dev-only relaxations documented
// on `withConnectSrc` / `withDevScriptSrc`.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import type { Plugin } from 'vite';

import { deriveConnectSrcOrigins, loadRuntimeConfig } from './runtime-config-source.mjs';

const CSP_META_RE = /<meta[^>]*http-equiv=["']Content-Security-Policy["'][^>]*>/i;
// Backreference \1 so the value (which contains single quotes like 'self')
// terminates only on the matching outer quote, not the first inner quote.
const CSP_CONTENT_RE = /content=(["'])([\s\S]*?)\1/i;
const CONNECT_SRC_RE = /connect-src[^;]*/i;
const SCRIPT_SRC_RE = /script-src([^;]*)/i;

/** Read the prod meta CSP `content="..."` value from an index.html string. */
export function extractMetaCsp(html: string): string {
  const tag = CSP_META_RE.exec(html);
  if (!tag) {
    throw new Error('index.html has no Content-Security-Policy meta tag');
  }
  const content = CSP_CONTENT_RE.exec(tag[0]);
  if (!content || content[2] === undefined) {
    throw new Error('Content-Security-Policy meta tag has no content attribute');
  }
  return content[2].trim();
}

/**
 * Replace the connect-src directive with a host-limited dev allowlist.
 * DEV-ONLY RELAXATION #1: prod's broad connect-src scheme sources are replaced
 * by `'self' <derived origins>` so the dev API/WS/IdP loopback origins are
 * reachable without a broad scheme source or wildcard.
 */
export function withConnectSrc(csp: string, origins: readonly string[]): string {
  const directive = ['connect-src', "'self'", ...origins].join(' ');
  if (CONNECT_SRC_RE.test(csp)) {
    return csp.replace(CONNECT_SRC_RE, directive);
  }
  return `${csp.replace(/;\s*$/, '')}; ${directive}`;
}

/**
 * Ensure script-src allows inline scripts in dev.
 * DEV-ONLY RELAXATION #2: @vitejs/plugin-react injects an inline module
 * preamble (React Fast Refresh) that prod's strict `script-src 'self'` would
 * block. Added ONLY to the dev header; the prod meta stays strict.
 */
export function withDevScriptSrc(csp: string): string {
  const match = SCRIPT_SRC_RE.exec(csp);
  if (!match || match[1] === undefined) return csp;
  if (match[1].includes("'unsafe-inline'")) return csp;
  return csp.replace(SCRIPT_SRC_RE, `script-src${match[1]} 'unsafe-inline'`);
}

/** Pure connect-src directive builder (unit-tested without file I/O). */
export function deriveConnectSrcDirective(
  config: Record<string, unknown>,
  extraOrigins: readonly string[] = [],
): string {
  const origins = deriveConnectSrcOrigins(config, extraOrigins);
  return ['connect-src', "'self'", ...origins].join(' ');
}

/** Remove the CSP meta tag from an HTML string (dev-served HTML only). */
export function stripCspMeta(html: string): string {
  return html.replace(CSP_META_RE, '');
}

export interface BuildDevCspOptions {
  /** Override the prod meta CSP (test injection). Defaults to reading index.html. */
  prodCsp?: string;
  /** Override the runtime-config object (test injection). Defaults to dev source. */
  config?: Record<string, unknown>;
  /** App root for file reads (defaults to cwd = apps/web at dev start). */
  appRoot?: string;
  /** Extra origins to allow (e.g. the Vite HMR websocket origin). */
  extraOrigins?: readonly string[];
}

/**
 * Compose the dev CSP string from the prod meta directive set + the derived
 * dev connect-src + the dev script-src relaxation.
 */
export function buildDevCsp(options: BuildDevCspOptions = {}): string {
  const appRoot = options.appRoot ?? process.cwd();
  const prodCsp =
    options.prodCsp ?? extractMetaCsp(readFileSync(resolve(appRoot, 'index.html'), 'utf8'));
  const config = options.config ?? loadRuntimeConfig('dev', appRoot);
  const origins = deriveConnectSrcOrigins(config, options.extraOrigins ?? []);
  return withDevScriptSrc(withConnectSrc(prodCsp, origins));
}

/**
 * Vite plugin (serve only) that strips the CSP meta tag from the dev-served
 * index.html so the dev `server.headers` CSP is the single authoritative
 * policy (CSPs combine as an intersection — see file header). Does NOT touch
 * the built dist/index.html.
 */
export function devCspMetaStripPlugin(): Plugin {
  return {
    name: 'fcc-dev-csp-meta-strip',
    apply: 'serve',
    transformIndexHtml: {
      order: 'pre',
      handler(html: string): string {
        return stripCspMeta(html);
      },
    },
  };
}

/**
 * Vite plugin (preview only) that strips the CSP meta tag from the
 * preview-served index.html when `VITE_E2E=1`. Mirrors devCspMetaStripPlugin
 * for the preview mode (which serves the built dist/ artifact). NEEDED because
 * CSPs combine as an intersection — leaving the strict prod meta in the
 * served HTML would block the e2e `page.route()` mock origins even though
 * the preview server's `Content-Security-Policy` HTTP header allowlists them.
 *
 * The on-disk `dist/index.html` artifact stays byte-identical — the strip
 * happens only when the preview server emits the response. A non-e2e
 * `npm run preview` (no VITE_E2E) ships the prod meta unchanged so an operator
 * still sees the exact CSP the prod build will enforce.
 */
export function previewE2eCspMetaStripPlugin(): Plugin {
  return {
    name: 'fcc-preview-e2e-csp-meta-strip',
    configurePreviewServer(server) {
      if (process.env['VITE_E2E'] !== '1') return;
      const indexPath = resolve(process.cwd(), 'dist', 'index.html');
      server.middlewares.use((req, res, next) => {
        const url = req.url ?? '';
        // OAuth callback query strings contain opaque codes with dots. Match
        // the route by pathname so a code value cannot bypass the preview-only
        // CSP-meta strip and restore the production policy.
        const pathname = new URL(url, 'http://vite-preview.invalid').pathname;
        // The Vite preview server falls through to index.html for the SPA
        // history-fallback paths (`/`, `/projects`, `/sessions`, ...). We
        // intercept those + the explicit `/index.html` so every served
        // boot of the SPA gets the meta CSP stripped.
        const wantsHtml =
          req.method === 'GET' &&
          (pathname === '/' ||
            pathname === '/index.html' ||
            (!pathname.includes('.') && !pathname.startsWith('/assets/')));
        if (!wantsHtml) return next();
        try {
          const html = readFileSync(indexPath, 'utf8');
          res.setHeader('Content-Type', 'text/html; charset=utf-8');
          res.end(stripCspMeta(html));
        } catch {
          // Fall through if dist/ is missing — preview will 404 naturally.
          next();
        }
      });
    },
  };
}
