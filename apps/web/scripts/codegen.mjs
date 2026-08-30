#!/usr/bin/env node
/**
 * OpenAPI -> TypeScript codegen — Sprint S1 (ADR-0003) / B3 (P14).
 *
 * Reads the OpenAPI specs from the `@fcc/api-artifacts` package (B3, P14) and
 * writes typed clients to `src/api/generated/*.types.ts`. The package's
 * `manifest.json` is the single artifact SSOT — which specs exist, their path,
 * and the TypeScript output basename — so this script no longer hardcodes the
 * artifact path list. The package mirror is kept byte-identical to the
 * canonical `docs/api/*.openapi.json` SSOT (sealed by sync.mjs --check +
 * tests/test_api_artifacts_package.py), so codegen output stays byte-identical.
 *
 * Modes:
 *   - default (npm run codegen)     write generated files
 *   - --check (npm run codegen:check) fail if generated output is out of date
 *
 * The `--check` mode is for CI: a backend OpenAPI artifact change must be
 * accompanied by a regenerated frontend type bundle in the same PR, otherwise
 * CI fails. This enforces the SSOT chain (backend DTO -> OpenAPI -> TS type).
 */

import { execFile } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

// B3 (P14) — consume the @fcc/api-artifacts package by relative path. The
// package owns the artifact SSOT (manifest.json -> OPENAPI_SPECS); importing it
// here removes the previously hardcoded docs/api/*.openapi.json path list. A
// relative import (rather than a bare `@fcc/api-artifacts` specifier) avoids
// install/lockfile coupling while still consuming the package as the single
// artifact source. A bare-specifier `file:` devDependency is deferred to the
// repo-split milestone (it would require a package-lock.json regeneration —
// a separate staging gate); see packages/api-artifacts/README.md.
import { OPENAPI_SPECS } from '../../../packages/api-artifacts/index.mjs';

const execFileAsync = promisify(execFile);
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const APP_ROOT = resolve(__dirname, '..');
const REPO_ROOT = resolve(APP_ROOT, '..', '..');
// `node_modules/.bin/openapi-typescript` is a shell wrapper on Unix and a
// `.cmd` shim on Windows — neither is directly spawnable from Node via
// `execFile` (the Windows shim is not a valid PE, the Unix script confuses
// non-shell spawn). Resolve the actual CLI JavaScript entrypoint instead
// and invoke it via `process.execPath` so the same call works on Windows /
// macOS / Linux without shell:true (which would open command-injection risk
// on caller-controlled paths).
const OPENAPI_TS_CLI = resolve(APP_ROOT, 'node_modules', 'openapi-typescript', 'bin', 'cli.js');

// Sprint S2-β α-16 — Node.js execFile default maxBuffer is 1 MB (see
// https://nodejs.org/api/child_process.html#child_processexecfilefile).
// openapi-typescript can emit 5-15 MB of TS for large OpenAPI documents
// (esp. with --enum off + verbose type unions). 16 MB ceiling gives 8×
// headroom over the largest spec we've observed in this monorepo, with
// a hard cap that prevents a malformed spec from exhausting Node memory.
const OPENAPI_TS_OUTPUT_BUFFER_BYTES = 16 * 1024 * 1024;

// Derived from the @fcc/api-artifacts manifest (single artifact SSOT). Each
// codegen spec (session / headless / platform OpenAPI 3.1) carries its absolute
// path inside the package and the TypeScript output basename. Adding/removing a
// spec is a one-line manifest edit in packages/api-artifacts/manifest.json — no
// change needed here.
//
// Backend provenance (unchanged): the OpenAPI artifacts are produced from
// src/application/**/api_contracts.py SSOTs via
// scripts/export_session_api_schemas.py and mirrored into the package by
// packages/api-artifacts/scripts/sync.mjs (byte-identical). The legacy custom
// envelope headless_api_contract.v1.json is retained for provider compatibility
// self-checks but is NOT a codegen source.
const SPECS = OPENAPI_SPECS.map((spec) => ({
  name: spec.name,
  source: spec.path,
  output: resolve(APP_ROOT, 'src', 'api', 'generated', spec.typesBasename),
}));

const checkMode = process.argv.includes('--check');

/**
 * Spawn the openapi-typescript CLI via the current Node executable. Cross-
 * platform safe — does not depend on the `.bin` shim layout that differs
 * between Windows (`.cmd`) and Unix (shell script).
 *
 * @param {string} source   OpenAPI spec path
 */
async function runOpenapiTypescript(source) {
  if (!existsSync(OPENAPI_TS_CLI)) {
    throw new Error(
      `openapi-typescript CLI not found at ${OPENAPI_TS_CLI}. Run \`npm install\` first.`,
    );
  }
  const tempDir = await mkdtemp(resolve(tmpdir(), 'fcc-openapi-ts-'));
  const tempOutput = resolve(tempDir, 'types.ts');
  try {
    await execFileAsync(
      process.execPath,
      [OPENAPI_TS_CLI, source, '--enum', 'false', '--output', tempOutput],
      { cwd: APP_ROOT, maxBuffer: OPENAPI_TS_OUTPUT_BUFFER_BYTES },
    );
    return await readFile(tempOutput, 'utf8');
  } finally {
    await rm(tempDir, { recursive: true, force: true });
  }
}

async function processSpec(spec) {
  if (!existsSync(spec.source)) {
    throw new Error(`OpenAPI source missing: ${spec.source}`);
  }
  const generated = await runOpenapiTypescript(spec.source);
  // Derive the banner source path from `spec.source` directly so new entries
  // (e.g. FE-P1 headless) inherit the correct file name without bespoke
  // string-template branches.
  const sourceRel = spec.source
    .slice(REPO_ROOT.length + 1)
    .split(/[\\/]/)
    .join('/');
  const banner = [
    '/**',
    ' * AUTO-GENERATED — DO NOT EDIT.',
    ` * Source: ${sourceRel}`,
    ` * Generator: openapi-typescript (apps/web/scripts/codegen.mjs)`,
    ' * Regenerate via `npm run codegen` from apps/web/.',
    ' */',
    '',
  ].join('\n');
  const payload = banner + generated;

  await mkdir(dirname(spec.output), { recursive: true });

  if (checkMode) {
    if (!existsSync(spec.output)) {
      console.error(`[codegen:check] ${spec.name}: generated file missing at ${spec.output}`);
      process.exitCode = 1;
      return;
    }
    const existing = await readFile(spec.output, 'utf8');
    if (existing !== payload) {
      console.error(
        `[codegen:check] ${spec.name}: generated file is stale — run \`npm run codegen\`.`,
      );
      process.exitCode = 1;
      return;
    }
    console.log(`[codegen:check] ${spec.name}: up to date`);
    return;
  }

  await writeFile(spec.output, payload, 'utf8');
  console.log(`[codegen] ${spec.name}: wrote ${spec.output}`);
}

async function main() {
  for (const spec of SPECS) {
    await processSpec(spec);
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
