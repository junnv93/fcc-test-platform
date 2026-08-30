#!/usr/bin/env node
/**
 * Bundle size measurement — Sprint S2-β α-15 + S2-γ β-P1-3 β-P2-2 β-P2-3.
 *
 * Walks `dist/assets/*.js`, gzips each file in-memory, and reports the
 * gzipped size per chunk + total. Used as the input for the Sprint S8
 * Lighthouse CI gate's bundle budget.
 *
 * Output (stable, machine-readable JSON):
 *   {
 *     "timestamp": "...",
 *     "buildId": "<sha256 of all chunk contents>",
 *     "chunks": [{ "name": "main", "rawBytes": …, "gzipBytes": … }, …],
 *     "totalRawBytes": …,
 *     "totalGzipBytes": …
 *   }
 *
 * Usage:
 *   cd apps/web
 *   node scripts/measure-bundle.mjs            # measure stale dist/
 *   node scripts/measure-bundle.mjs --build    # vite build + measure
 *   node scripts/measure-bundle.mjs --output dist/bundle-size.json
 *
 * Default output path: dist/bundle-size.json (gitignored).
 */

import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { readdir, readFile, stat, writeFile } from 'node:fs/promises';
import { resolve, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { gzipSync } from 'node:zlib';

const execFileAsync = promisify(execFile);
const __filename = fileURLToPath(import.meta.url);
const APP_ROOT = resolve(dirname(__filename), '..');
const ASSETS_DIR = resolve(APP_ROOT, 'dist', 'assets');

/** Sprint S2-γ β-P2-3 — default output path SSOT. Sprint S8 Lighthouse
 *  CI consumes this filename; downstream tooling MUST NOT hardcode. */
const DEFAULT_OUTPUT_PATH = resolve(APP_ROOT, 'dist', 'bundle-size.json');

/** Sprint S2-γ β-P2-2 — gzip level. zlib default is 6 (balanced). We
 *  want the smallest possible gzip footprint as the measurement target
 *  so Lighthouse CI rachets against the floor (any optimisation we
 *  ship in the future strictly decreases this number).
 *  Ref: zlib(3) — level 9 == "best compression, slower". For measurement
 *  the extra CPU is irrelevant; it runs once per build. */
const GZIP_MAX_COMPRESSION_LEVEL = 9;

/** Sprint S2-γ β-P1-3 — staleness warning threshold. If dist/ was built
 *  more than N minutes ago, warn the operator (a measurement of a stale
 *  build silently misleads Sprint S8's ratchet logic). */
const STALENESS_WARN_MINUTES = 5;

/** Wave `fe-w4-bundle-observability-cost` (2026-07-31) — the *initial load
 *  path*: the entry module plus every chunk the browser is told to preload
 *  before the app can run.
 *
 *  Vite writes a `<link rel="modulepreload">` for exactly the entry chunk's
 *  static import graph, so reading `dist/index.html` gives that set without
 *  re-deriving Rollup's chunking here — and lazily `import()`ed route /
 *  observability chunks are excluded *by construction* rather than by a
 *  filename convention that would rot.
 *
 *  Why this is a separate number from `totalGzipBytes`: the total is blind to
 *  *when* a chunk is fetched. Moving 114 kB gzip out of the initial path and
 *  behind a runtime condition barely moves the total, so a total-only budget
 *  reports "no change" for both a large win and its exact regression. */
async function measureInitialLoadPath() {
  const htmlPath = resolve(APP_ROOT, 'dist', 'index.html');
  if (!existsSync(htmlPath)) return null;
  const html = await readFile(htmlPath, 'utf8');
  const refs = [...html.matchAll(/(?:src|href)="(\/[^"]+\.js)"/gu)].map((m) => m[1]);
  const unique = [...new Set(refs)];
  const chunks = [];
  for (const ref of unique) {
    const path = resolve(APP_ROOT, 'dist', ref.replace(/^\//u, ''));
    if (!existsSync(path)) continue;
    chunks.push(await measureChunk(path));
  }
  chunks.sort((a, b) => a.name.localeCompare(b.name));
  return {
    chunks,
    rawBytes: chunks.reduce((acc, c) => acc + c.rawBytes, 0),
    gzipBytes: chunks.reduce((acc, c) => acc + c.gzipBytes, 0),
  };
}

async function listJsFiles(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  return entries
    .filter((e) => e.isFile() && e.name.endsWith('.js'))
    .map((e) => resolve(dir, e.name));
}

async function measureChunk(path) {
  const raw = await readFile(path);
  const gz = gzipSync(raw, { level: GZIP_MAX_COMPRESSION_LEVEL });
  return {
    name: basename(path).replace(/-[A-Za-z0-9]+\.js$/, ''),
    rawBytes: raw.byteLength,
    gzipBytes: gz.byteLength,
  };
}

function parseArgs(argv) {
  const args = { build: false, output: DEFAULT_OUTPUT_PATH, toStdout: false };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--build') args.build = true;
    else if (a === '--output' && argv[i + 1] !== undefined) {
      args.output = resolve(argv[i + 1]);
      i += 1;
    } else if (a === '--stdout') args.toStdout = true;
  }
  return args;
}

async function runViteBuild() {
  process.stderr.write('bundle-size: running `vite build`...\n');
  // Sprint S2-ε δ-P1-4 — invoke vite via process.execPath but pass the
  // EXACT flags `package.json::scripts.build` would use, so measure-
  // bundle and `npm run build` never drift on production flags
  // (minify level, sourcemap, etc.). The build script is the source of
  // truth; we read it at runtime and exec the underlying `vite build`
  // sub-step directly (skipping the typecheck steps that are
  // irrelevant to bundle measurement).
  const pkg = JSON.parse(await readFile(resolve(APP_ROOT, 'package.json'), 'utf8'));
  const buildScript = pkg.scripts?.build;
  if (typeof buildScript !== 'string' || !buildScript.includes('vite build')) {
    throw new Error(
      `package.json scripts.build must end with 'vite build' (got: ${buildScript ?? '<missing>'})`,
    );
  }
  const VITE_CLI = resolve(APP_ROOT, 'node_modules', 'vite', 'bin', 'vite.js');
  if (!existsSync(VITE_CLI)) {
    throw new Error(`vite CLI not found at ${VITE_CLI}. Run \`npm install\` first.`);
  }
  // Forward any post-'vite build' args from the script (e.g. --base, --mode).
  const idx = buildScript.indexOf('vite build');
  const extraArgs = buildScript
    .slice(idx + 'vite build'.length)
    .trim()
    .split(/\s+/u)
    .filter((s) => s.length > 0);
  await execFileAsync(process.execPath, [VITE_CLI, 'build', ...extraArgs], {
    cwd: APP_ROOT,
    maxBuffer: 64 * 1024 * 1024,
  });
}

async function checkStaleness(files) {
  if (files.length === 0) return;
  const stats = await Promise.all(files.map((f) => stat(f)));
  const newest = Math.max(...stats.map((s) => s.mtimeMs));
  const ageMinutes = (Date.now() - newest) / 60_000;
  if (ageMinutes > STALENESS_WARN_MINUTES) {
    process.stderr.write(
      `bundle-size: WARNING — dist/assets/ is ${ageMinutes.toFixed(1)} minutes old ` +
        `(threshold: ${STALENESS_WARN_MINUTES}min). Pass --build for a fresh measurement.\n`,
    );
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  if (args.build) {
    await runViteBuild();
  }

  let files;
  try {
    files = await listJsFiles(ASSETS_DIR);
  } catch (err) {
    process.stderr.write(
      `bundle-size: ${ASSETS_DIR} not found — pass --build or run \`npm run build\` first (${
        err instanceof Error ? err.message : String(err)
      })\n`,
    );
    process.exitCode = 1;
    return;
  }
  if (files.length === 0) {
    process.stderr.write('bundle-size: no .js chunks under dist/assets\n');
    process.exitCode = 1;
    return;
  }

  if (!args.build) {
    await checkStaleness(files);
  }

  const chunks = await Promise.all(files.map(measureChunk));
  chunks.sort((a, b) => a.name.localeCompare(b.name));

  const totalRawBytes = chunks.reduce((acc, c) => acc + c.rawBytes, 0);
  const totalGzipBytes = chunks.reduce((acc, c) => acc + c.gzipBytes, 0);

  // Build ID = sha256 of every chunk content (sorted by name) AND the
  // Vite manifest.json if present. Sprint S2-ε δ-P2-9 — manifest
  // changes (chunk-split policy, code-splitting tweaks) produce
  // different deploy artefacts even when chunk byte-content is unchanged.
  const hash = createHash('sha256');
  for (const path of files.sort()) {
    hash.update(await readFile(path));
  }
  const manifestPath = resolve(APP_ROOT, 'dist', '.vite', 'manifest.json');
  if (existsSync(manifestPath)) {
    hash.update(await readFile(manifestPath));
  }

  const initialLoadPathJs = await measureInitialLoadPath();

  const payload = `${JSON.stringify(
    {
      timestamp: new Date().toISOString(),
      buildId: hash.digest('hex'),
      chunks,
      totalRawBytes,
      totalGzipBytes,
      initialLoadPathJs,
    },
    null,
    2,
  )}\n`;

  if (args.toStdout) {
    process.stdout.write(payload);
  } else {
    await writeFile(args.output, payload, 'utf8');
    process.stderr.write(
      `bundle-size: wrote ${args.output} (${totalGzipBytes} bytes gzip total)\n`,
    );
  }
}

main().catch((err) => {
  process.stderr.write(`${err instanceof Error ? err.message : String(err)}\n`);
  process.exitCode = 1;
});
