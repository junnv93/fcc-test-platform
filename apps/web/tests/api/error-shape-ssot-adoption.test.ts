import { readFileSync, readdirSync, statSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { categorizeQueryStatus } from '@/api/query-status';
import { toApiError } from '@/api/to-api-error';
import { useChamberProgressPolling } from '@/api/use-chamber-progress';

/**
 * Error-shape SSOT adoption gate (C2 query-hooks-ssot, 2026-06-17;
 * C4 route-component-decomposition, 2026-06-17).
 *
 * Proves the three extracted SSOT modules are actually ADOPTED (no shelfware) and
 * that the inlined patterns they replaced do not drift back:
 *
 *  1. The decorated-`Error` factory `toApiError` replaced every
 *     `Object.assign(new Error(...), { status }) as ApiError` site in the
 *     in-scope files — the literal must be gone and `toApiError` referenced.
 *  2. The chamber-progress polling hook + self-healing categorizer are exported
 *     and importable (the symbols exist, not just the files).
 *
 * C4 split `routes/chambers.tsx` / `routes/test-plans.tsx` into route
 * sub-directories (`routes/chambers/` / `routes/test-plans/`). This gate now
 * scans the whole route directory (recursively) instead of a single file so the
 * adoption claim survives the decomposition and keeps holding across the new
 * sibling modules.
 */
const APPS_WEB_ROOT = resolve(__dirname, '..', '..');
const SRC = resolve(APPS_WEB_ROOT, 'src');

/** Read a `.ts`/`.tsx` file, or — when `rel` is a directory — concatenate every
 *  `.ts`/`.tsx` source under it (recursively). The route entrypoint is now a
 *  directory of sibling component/util modules, so the adoption assertions hold
 *  over the directory as a whole rather than one file. */
function readSource(rel: string): string {
  const abs = resolve(SRC, rel);
  if (statSync(abs).isDirectory()) {
    return readdirSync(abs)
      .map((entry) => {
        const child = resolve(abs, entry);
        if (statSync(child).isDirectory()) return readSource(`${rel}/${entry}`);
        return /\.tsx?$/.test(entry) ? readFileSync(child, 'utf8') : '';
      })
      .join('\n');
  }
  return readFileSync(abs, 'utf8');
}

/**
 * Modules that construct their failures through the shared factory module.
 *
 * ⚠️ **`routes/chambers` and `routes/test-plans` left this list on 2026-08-19,
 * and that is the property getting *stronger*, not weaker.** The
 * `headless-client-helper-layer` wave moved the request plumbing out of the
 * routes: they no longer build failures because they no longer make requests.
 * A positive "mentions a shared factory" assertion over a directory that
 * constructs nothing is vacuous — it would pass on a route that had simply lost
 * its error handling.
 *
 * So the claim splits. This list keeps the **positive** half and now names the
 * three modules that really do own requests. The routes keep the **negative**
 * half below (no inlined `Object.assign(new Error`), and the fact that they own
 * no transport at all is sealed where the shared TypeScript comment lexer lives:
 * `tests/test_frontend_architecture_conformance.py`. A source-grep here would
 * have to re-solve comment stripping, and this repo's ledger already records a
 * seal in this very file failing because comment prose mentioned a token.
 */
const ADOPTING_FILES = [
  'api/platform-client.ts',
  'api/session-client.ts',
  'api/headless-client.ts',
] as const;

/**
 * Route trees that must never re-grow an inlined, RFC-9457-dropping failure.
 *
 * ⚠️ **Exactly the two trees this gate already covered, and widening it to all of
 * `routes` is a trap this repository has already recorded.** These greps do not
 * strip comments (ledger, 2026-07-28: a seal in *this file* failed because
 * comment prose mentioned the token it forbade). Widening the sweep immediately
 * went red on documentary prose — `session-client.ts` explains the pattern it
 * replaced by naming it, and a route comment does the same. The whole-tree sweep
 * therefore belongs on the Python axis, where the shared TypeScript comment
 * lexer (`tests/support/parity.py::strip_ts_comments`) already exists.
 */
const NO_INLINE_ERROR_TREES = ['routes/chambers', 'routes/test-plans'] as const;

/**
 * The factories that ARE the SSOT, as of boundary-plumbing-and-node-liveness
 * (2026-08-19).
 *
 * ⚠️ This used to require the literal `toApiError(`, which named a *mechanism*
 * rather than the property being protected. When the route layer moved to
 * `apiErrorFromResponse` — a factory that takes the failure whole so a caller
 * cannot drop the RFC 9457 `code`/`params` — this gate went red on files that had
 * just been made *stricter*. The property is "failures come from the shared
 * module", so the assertion is now satisfied by any of its factories.
 */
const SHARED_FACTORIES = [
  'toApiError(',
  'apiErrorFromResponse(',
  'clientOriginatedApiError(',
] as const;

describe('error-shape SSOT adoption gate', () => {
  it.each(ADOPTING_FILES)('builds failures through the shared factory module — %s', (relPath) => {
    const source = readSource(relPath);
    expect(SHARED_FACTORIES.some((factory) => source.includes(factory))).toBe(true);
  });

  it.each(NO_INLINE_ERROR_TREES)('builds no inlined, RFC-9457-dropping failure — %s', (relPath) => {
    expect(readSource(relPath)).not.toContain('Object.assign(new Error');
  });
});

describe('SSOT module symbols are exported (adopted, not shelfware)', () => {
  it('exposes the three extracted SSOT entry points', () => {
    expect(typeof toApiError).toBe('function');
    expect(typeof categorizeQueryStatus).toBe('function');
    expect(typeof useChamberProgressPolling).toBe('function');
  });

  it('chambers route consumes the shared polling hook (no inline progress useQuery)', () => {
    const source = readSource('routes/chambers');
    expect(source).toContain('useChamberProgressPolling(');
    // The duplicated refetchInterval polling closure now lives in the hook only.
    expect(source).not.toContain('refetchInterval');
  });
});
