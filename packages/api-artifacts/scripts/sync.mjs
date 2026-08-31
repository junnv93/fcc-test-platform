#!/usr/bin/env node
/**
 * Sync the @fcc/api-artifacts mirror from the canonical docs/ SSOT — B3 (P14).
 *
 * The shared API contract artifacts have a single canonical writer each:
 *   - docs/api/*.openapi.json          ← scripts/export_session_api_schemas.py (Python)
 *   - docs/platform/central_db_schema.v1.json  ← hand-maintained contract source
 *
 * This script is the SINGLE WRITER of the package mirror
 * (`packages/api-artifacts/artifacts/*.json`). It copies the canonical bytes
 * verbatim so the mirror stays byte-identical to docsSource. No file gains a
 * second writer: docs/ is written by its own canonical tool, the mirror only
 * by this script.
 *
 * Modes:
 *   node scripts/sync.mjs           copy docs/* -> artifacts/* (write)
 *   node scripts/sync.mjs --check   exit non-zero if any mirror byte-differs
 *                                    from its docsSource (CI drift gate)
 *
 * Byte-exact: reads/writes as raw Buffers (no decode/re-encode, no EOL
 * translation) so LF stays LF and the mirror is a literal copy.
 */
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const PKG_ROOT = resolve(dirname(__filename), '..');
// packages/api-artifacts -> packages -> repo root
const REPO_ROOT = resolve(PKG_ROOT, '..', '..');
const ARTIFACTS_DIR = resolve(PKG_ROOT, 'artifacts');

const manifest = JSON.parse(readFileSync(resolve(PKG_ROOT, 'manifest.json'), 'utf8'));
const checkMode = process.argv.includes('--check');

if (!checkMode) {
  mkdirSync(ARTIFACTS_DIR, { recursive: true });
}

let drift = false;
let missing = false;

for (const entry of manifest.artifacts) {
  const source = resolve(REPO_ROOT, ...entry.docsSource.split('/'));
  const dest = resolve(ARTIFACTS_DIR, entry.file);

  if (!existsSync(source)) {
    console.error(`[api-artifacts:sync] canonical source missing: ${entry.docsSource}`);
    missing = true;
    continue;
  }

  const sourceBytes = readFileSync(source);

  if (checkMode) {
    if (!existsSync(dest)) {
      console.error(`[api-artifacts:sync] mirror missing: artifacts/${entry.file} — run \`node scripts/sync.mjs\`.`);
      drift = true;
      continue;
    }
    const destBytes = readFileSync(dest);
    if (!sourceBytes.equals(destBytes)) {
      console.error(
        `[api-artifacts:sync] DRIFT: artifacts/${entry.file} differs from ${entry.docsSource}. ` +
          'Run `node scripts/sync.mjs` to re-mirror.',
      );
      drift = true;
    } else {
      console.log(`[api-artifacts:sync:check] ${entry.name}: up to date`);
    }
    continue;
  }

  writeFileSync(dest, sourceBytes);
  console.log(`[api-artifacts:sync] ${entry.name}: mirrored artifacts/${entry.file} (${sourceBytes.length} bytes)`);
}

if (missing || drift) {
  process.exitCode = 1;
}
