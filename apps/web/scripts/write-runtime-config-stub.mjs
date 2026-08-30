#!/usr/bin/env node
/**
 * Lighthouse runtime-config stub writer — FE-P7 review P0-1 fix (2026-05-27).
 *
 * `index.html` loads `/runtime-config.js` (a deployment-injected script that
 * sets `window.__FCC_RUNTIME_CONFIG__`). `getRuntimeConfig()` THROWS at boot if
 * it is absent. `dist/` does not contain it, so serving `dist/` statically to
 * Lighthouse (lhci `staticDistDir`) would audit a crashed shell — making the
 * accessibility (WCAG AA) score meaningless.
 *
 * This writes a non-secret stub `dist/runtime-config.js` from the committed
 * `public/runtime-config.template.json` SSOT (no duplicated config; the
 * `$schema`/`_comment` meta keys are stripped) so Lighthouse audits the real
 * rendered application. The stub is for CI auditing only — never a deployment
 * artifact (real values are injected per environment).
 *
 * Usage (from apps/web, after `npm run build`):
 *   node scripts/write-runtime-config-stub.mjs
 */
import { writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { loadRuntimeConfig } from '../vite/runtime-config-source.mjs';

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const OUTPUT_PATH = resolve(APP_ROOT, 'dist', 'runtime-config.js');

// Shared origin-source loader (Phase 0) — same parser the dev CSP helper uses,
// so the runtime-config source mechanism is single-sourced. The Lighthouse
// stub is built from the production template; meta keys ($schema/_comment) are
// stripped by loadRuntimeConfig.
const template = loadRuntimeConfig('template', APP_ROOT);

const banner =
  '// AUTO-GENERATED stub for Lighthouse CI (FE-P7). Sourced from\n' +
  '// public/runtime-config.template.json. Non-secret, CI-audit-only — NOT a\n' +
  '// deployment artifact. See scripts/write-runtime-config-stub.mjs.\n';
const body = `window.__FCC_RUNTIME_CONFIG__ = ${JSON.stringify(template, null, 2)};\n`;

writeFileSync(OUTPUT_PATH, banner + body, 'utf8');
console.log(`[lighthouse-stub] wrote ${OUTPUT_PATH} from runtime-config.template.json`);
