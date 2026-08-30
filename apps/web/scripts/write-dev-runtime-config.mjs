#!/usr/bin/env node
/**
 * Dev runtime-config stub generator (Phase 0 follow-up, 2026-05-30).
 *
 * Makes `public/runtime-config.dev.json` the SINGLE authored source for the dev
 * runtime config. The browser-served `public/runtime-config.js` is GENERATED
 * from it — the dev origins are no longer hand-copied into two files. The dev
 * CSP helper (vite/dev-csp.ts) also derives from the same JSON SSOT, so there
 * is exactly one place a dev origin is authored.
 *
 * Modes:
 *   (write)   node scripts/write-dev-runtime-config.mjs
 *             → regenerate public/runtime-config.js. Runs as the `predev` hook.
 *   --check   node scripts/write-dev-runtime-config.mjs --check
 *             → fail (exit 1) if the committed stub differs from the generation
 *               (CI drift gate; mirrors `codegen:check`).
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { loadRuntimeConfig, renderDevStubJs } from '../vite/runtime-config-source.mjs';

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const OUTPUT_PATH = resolve(APP_ROOT, 'public', 'runtime-config.js');

const generated = renderDevStubJs(loadRuntimeConfig('dev', APP_ROOT));
const checkMode = process.argv.includes('--check');

if (checkMode) {
  let current = '';
  try {
    current = readFileSync(OUTPUT_PATH, 'utf8');
  } catch {
    current = '';
  }
  // Normalize CRLF so a Windows checkout (autocrlf) does not false-trip.
  if (current.replace(/\r\n/g, '\n') !== generated) {
    console.error(
      '[dev-runtime-config:check] public/runtime-config.js is STALE vs ' +
        'runtime-config.dev.json. Run `node scripts/write-dev-runtime-config.mjs` ' +
        '(or `npm run dev`) and commit the regenerated stub.',
    );
    process.exit(1);
  }
  console.log('[dev-runtime-config:check] up to date');
} else {
  writeFileSync(OUTPUT_PATH, generated, 'utf8');
  console.log(`[dev-runtime-config] wrote ${OUTPUT_PATH} from runtime-config.dev.json`);
}
