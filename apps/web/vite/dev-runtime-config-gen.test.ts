import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { loadRuntimeConfig, renderDevStubJs } from './runtime-config-source.mjs';

/**
 * Single-source gate (Phase 0 follow-up, 2026-05-30): the browser-served dev
 * stub `public/runtime-config.js` MUST be a byte-exact generation from
 * `public/runtime-config.dev.json` (the single authored dev SSOT). A hand-edit
 * to the .js, or an edit to the .dev.json without regenerating, fails here.
 *
 * Lives under vite/ (not tests/) so the `.mjs` import stays under
 * tsconfig.node.json (allowJs), out of the strict browser tsconfig.json program.
 */

const STUB_PATH = resolve(process.cwd(), 'public', 'runtime-config.js');

describe('dev runtime-config stub generation (single source)', () => {
  it('committed runtime-config.js is byte-identical to the generation from runtime-config.dev.json', () => {
    const committed = readFileSync(STUB_PATH, 'utf8').replace(/\r\n/g, '\n');
    const generated = renderDevStubJs(loadRuntimeConfig('dev', process.cwd()));
    expect(committed).toBe(generated);
  });

  it('the generated stub carries the AUTO-GENERATED banner (not hand-authored)', () => {
    const generated = renderDevStubJs(loadRuntimeConfig('dev', process.cwd()));
    expect(generated.startsWith('// AUTO-GENERATED')).toBe(true);
    expect(generated).toContain('runtime-config.dev.json');
  });

  it('generation is deterministic (same input → byte-identical output)', () => {
    const a = renderDevStubJs(loadRuntimeConfig('dev', process.cwd()));
    const b = renderDevStubJs(loadRuntimeConfig('dev', process.cwd()));
    expect(a).toBe(b);
  });
});
