import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Script, createContext } from 'node:vm';

import { describe, expect, it } from 'vitest';

import { runtimeConfigSchema, runtimeConfigObjectSchema } from '@/config/runtime';

/**
 * Sprint S2-ζ ζ-1 — drift guard for the dev stub at
 * `apps/web/public/runtime-config.js`.
 *
 * Background: S2-α (2026-05-23) added 3 fields to `runtimeConfigSchema`
 * (oidcAudience / oidcScopes / oidcPostLogoutRedirectUri), but the dev stub
 * was never synced. The mismatch silently failed Zod validation on every
 * `npm run dev` / `npm run preview` boot. The defect lay dormant for 5
 * sprints and only surfaced in S2-EXT-1 when an axe-core a11y spec
 * actually drove the SPA in a browser. Source-of-truth lesson:
 *
 *   typecheck + unit tests do NOT cover dev-stub-vs-Zod-schema drift.
 *
 * This spec evaluates the dev stub in a Node `vm` sandbox (mirroring how a
 * browser would execute it) and asserts `runtimeConfigSchema.safeParse`
 * succeeds. Adding a Zod field without syncing the stub now fails this
 * test in CI — no more 5-sprint dormancy.
 */

// vitest runs from the apps/web/ directory (package.json scripts cwd).
// Avoid `import.meta.url` because jsdom hands back an `http://localhost/...`
// URL — `fileURLToPath` rejects non-`file:` schemes. `process.cwd()` is
// stable across both Node and jsdom test environments.
const STUB_PATH = join(process.cwd(), 'public', 'runtime-config.js');

function loadDevStubPayload(): unknown {
  const source = readFileSync(STUB_PATH, 'utf8');
  const sandbox: { window: Record<string, unknown> } = { window: {} };
  const context = createContext(sandbox);
  new Script(source, { filename: 'runtime-config.js' }).runInContext(context);
  return sandbox.window['__FCC_RUNTIME_CONFIG__'];
}

describe('runtime-config.js dev stub — Zod schema drift guard', () => {
  it('evaluates without runtime error and sets window.__FCC_RUNTIME_CONFIG__', () => {
    const payload = loadDevStubPayload();
    expect(payload).toBeDefined();
    expect(typeof payload).toBe('object');
  });

  it('payload passes runtimeConfigSchema.safeParse — no drift since 2026-05-24', () => {
    const payload = loadDevStubPayload();
    const result = runtimeConfigSchema.safeParse(payload);
    if (!result.success) {
      const issues = result.error.errors
        .map((e) => `  - ${e.path.join('.') || '<root>'}: ${e.message}`)
        .join('\n');
      throw new Error(
        `dev stub at apps/web/public/runtime-config.js drifted from runtimeConfigSchema.\n` +
          `When you add or rename a Zod field in src/config/runtime.ts, also update the stub.\n` +
          `Zod issues:\n${issues}`,
      );
    }
    expect(result.success).toBe(true);
  });

  it('payload covers every required top-level Zod field (cross-language SSOT)', () => {
    const payload = loadDevStubPayload() as Record<string, unknown>;
    const schemaShape = runtimeConfigObjectSchema.shape;
    const schemaKeys = Object.keys(schemaShape).sort();
    const stubKeys = Object.keys(payload).sort();
    expect(stubKeys).toEqual(schemaKeys);
  });

  it('explicitly carries the S2-α fields that were missing for 5 sprints', () => {
    const payload = loadDevStubPayload() as Record<string, unknown>;
    expect(payload).toHaveProperty('oidcAudience');
    expect(payload).toHaveProperty('oidcScopes');
    expect(payload).toHaveProperty('oidcPostLogoutRedirectUri');
    expect(Array.isArray(payload['oidcScopes'])).toBe(true);
    expect((payload['oidcScopes'] as string[]).includes('openid')).toBe(true);
  });
});
