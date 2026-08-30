#!/usr/bin/env node
/**
 * Bundle budget gate — FE-P7 (2026-05-26).
 *
 * Compares the measured `dist/bundle-size.json` (produced by
 * `measure-bundle.mjs --build`) against the committed `bundle-budget.json`
 * SSOT and exits non-zero if the total gzipped bundle exceeds `maxGzipBytes`.
 *
 * The budget is NOT a magic number: this script also re-verifies that
 * `maxGzipBytes === ceil(measuredGzipBytes * headroomFactor)` so a hand-edited
 * inflated ceiling (decoupled from the measured baseline) fails the gate. To
 * legitimately raise the budget, re-measure and bump `measuredGzipBytes`.
 *
 * Usage (from apps/web):
 *   node scripts/measure-bundle.mjs --build
 *   node scripts/check-bundle-budget.mjs
 */
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const BUDGET_PATH = resolve(APP_ROOT, 'bundle-budget.json');
const MEASURE_PATH = resolve(APP_ROOT, 'dist', 'bundle-size.json');

function fail(message) {
  console.error(`[bundle-budget] FAIL: ${message}`);
  process.exit(1);
}

const budget = JSON.parse(readFileSync(BUDGET_PATH, 'utf8'));
// Symmetric with the Python seal (TestFeP7ProductionGate): without these guards
// a hand-edited measuredGzipBytes=0 / headroomFactor=0 would pass the derivation
// (ceil(0*0)===0) and zero out the ceiling. A fork running only the JS gate
// (no Python tests) must still be protected.
if (typeof budget.measuredGzipBytes !== 'number' || budget.measuredGzipBytes <= 0) {
  fail(`measuredGzipBytes must be a positive number (got ${budget.measuredGzipBytes}).`);
}
if (typeof budget.headroomFactor !== 'number' || budget.headroomFactor < 1) {
  fail(`headroomFactor must be a number >= 1.0 (got ${budget.headroomFactor}).`);
}
const derived = Math.ceil(budget.measuredGzipBytes * budget.headroomFactor);
if (derived !== budget.maxGzipBytes) {
  fail(
    `maxGzipBytes (${budget.maxGzipBytes}) is not derived from the measured ` +
      `baseline: ceil(${budget.measuredGzipBytes} * ${budget.headroomFactor}) = ${derived}. ` +
      `Re-measure and re-derive (see performance-budget.md) instead of hand-editing the ceiling.`,
  );
}

if (!existsSync(MEASURE_PATH)) {
  fail(`missing ${MEASURE_PATH}. Run \`node scripts/measure-bundle.mjs --build\` first.`);
}
const measured = JSON.parse(readFileSync(MEASURE_PATH, 'utf8'));
const total = measured.totalGzipBytes;

if (typeof total !== 'number') {
  fail('measured bundle report is missing a numeric totalGzipBytes.');
}

if (total > budget.maxGzipBytes) {
  fail(
    `total gzipped bundle ${total} B exceeds budget ${budget.maxGzipBytes} B ` +
      `(measured baseline ${budget.measuredGzipBytes} B + ${Math.round(
        (budget.headroomFactor - 1) * 100,
      )}% headroom). Investigate the regression or re-derive the baseline.`,
  );
}

const pct = ((total / budget.maxGzipBytes) * 100).toFixed(1);
console.log(`[bundle-budget] OK: ${total} B / ${budget.maxGzipBytes} B (${pct}% of budget).`);

// ── Initial load path budget ────────────────────────────────────────────────
// Wave `fe-w4-bundle-observability-cost` (2026-07-31). The total above is blind
// to *when* a chunk is fetched, so it scored a 114 kB gzip improvement as noise
// — and would score the regression the same way. This second gate measures only
// what the browser must download before the app runs (see measure-bundle.mjs
// `measureInitialLoadPath` for the definition) and applies the identical
// "derived, not hand-edited" discipline.
const initialBudget = budget.initialLoadPathJs;
if (initialBudget === undefined) {
  fail(
    'bundle-budget.json is missing the `initialLoadPathJs` budget. It is not optional: ' +
      'the total-only budget cannot see code moving on/off the initial load path.',
  );
}
if (typeof initialBudget.measuredGzipBytes !== 'number' || initialBudget.measuredGzipBytes <= 0) {
  fail(
    `initialLoadPathJs.measuredGzipBytes must be a positive number (got ${initialBudget.measuredGzipBytes}).`,
  );
}
if (typeof initialBudget.headroomFactor !== 'number' || initialBudget.headroomFactor < 1) {
  fail(
    `initialLoadPathJs.headroomFactor must be a number >= 1.0 (got ${initialBudget.headroomFactor}).`,
  );
}
const initialDerived = Math.ceil(initialBudget.measuredGzipBytes * initialBudget.headroomFactor);
if (initialDerived !== initialBudget.maxGzipBytes) {
  fail(
    `initialLoadPathJs.maxGzipBytes (${initialBudget.maxGzipBytes}) is not derived from the ` +
      `measured baseline: ceil(${initialBudget.measuredGzipBytes} * ${initialBudget.headroomFactor}) = ` +
      `${initialDerived}. Re-measure and re-derive instead of hand-editing the ceiling.`,
  );
}

const initialMeasured = measured.initialLoadPathJs;
if (initialMeasured === undefined || initialMeasured === null) {
  fail(
    'measured bundle report has no `initialLoadPathJs` section — dist/index.html was missing ' +
      'when measure-bundle.mjs ran. Re-run it against a complete build.',
  );
}
if (typeof initialMeasured.gzipBytes !== 'number') {
  fail('measured initialLoadPathJs is missing a numeric gzipBytes.');
}
if (initialMeasured.gzipBytes > initialBudget.maxGzipBytes) {
  fail(
    `initial load path ${initialMeasured.gzipBytes} B gzip exceeds budget ` +
      `${initialBudget.maxGzipBytes} B (measured baseline ${initialBudget.measuredGzipBytes} B + ` +
      `${Math.round((initialBudget.headroomFactor - 1) * 100)}% headroom). Something that used to be ` +
      `lazily loaded is now in the entry graph — check for a new static import of a heavy SDK, or a ` +
      `manualChunks group that glued an on-demand package to a statically reachable one.`,
  );
}

const initialPct = ((initialMeasured.gzipBytes / initialBudget.maxGzipBytes) * 100).toFixed(1);
console.log(
  `[bundle-budget] OK: initial load path ${initialMeasured.gzipBytes} B / ` +
    `${initialBudget.maxGzipBytes} B (${initialPct}% of budget), ` +
    `${initialMeasured.chunks?.length ?? '?'} chunks.`,
);
