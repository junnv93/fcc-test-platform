/**
 * @fcc/api-artifacts — programmatic access to the shared FCC API contract
 * artifacts (B3 / P14).
 *
 * `manifest.json` is the single artifact SSOT: which files belong to the
 * package, their kind, whether codegen consumes them, and the canonical
 * `docsSource` they are mirrored from. Consumers (e.g. apps/web codegen) read
 * this module instead of hardcoding artifact paths, so adding/removing an
 * artifact is a one-line manifest edit that fans out automatically.
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const PKG_ROOT = dirname(fileURLToPath(import.meta.url));
const ARTIFACTS_DIR = resolve(PKG_ROOT, 'artifacts');

/** Raw manifest object (single artifact SSOT). */
export const MANIFEST = JSON.parse(readFileSync(resolve(PKG_ROOT, 'manifest.json'), 'utf8'));

/**
 * All artifacts with their absolute on-disk path inside the package.
 * @type {ReadonlyArray<{name:string,file:string,kind:string,codegen:boolean,typesBasename:string|null,docsSource:string,path:string}>}
 */
export const ARTIFACTS = MANIFEST.artifacts.map((entry) => ({
  ...entry,
  path: resolve(ARTIFACTS_DIR, entry.file),
}));

/**
 * OpenAPI specs consumed by codegen (manifest `codegen: true`). Each carries
 * the absolute spec path and the TypeScript output basename (SSOT for the
 * spec -> generated-file mapping).
 * @type {ReadonlyArray<{name:string,file:string,path:string,typesBasename:string}>}
 */
export const OPENAPI_SPECS = ARTIFACTS.filter((a) => a.codegen).map((a) => ({
  name: a.name,
  file: a.file,
  path: a.path,
  typesBasename: a.typesBasename,
}));

/** Resolve an artifact's absolute path by manifest `name`. */
export function resolveArtifact(name) {
  const entry = ARTIFACTS.find((a) => a.name === name);
  if (!entry) {
    throw new Error(`@fcc/api-artifacts: unknown artifact "${name}"`);
  }
  return entry.path;
}

/** Load and JSON-parse an artifact by manifest `name`. */
export function loadArtifact(name) {
  return JSON.parse(readFileSync(resolveArtifact(name), 'utf8'));
}
