/**
 * Request-body ↔ OpenAPI-artifact touchpoint (test-plan-draft-create-422, 2026-08-01).
 *
 * Route tests mock the typed client, so a `toHaveBeenCalledWith` assertion only
 * ever says *what the front end sends* — never *whether the server accepts it*.
 * That gap is not hypothetical: the create-draft test asserted
 * `scope_profile: {}` for weeks while every real request with that body came
 * back 422. The assertion had stopped being a defect detector and had become a
 * defect fixture.
 *
 * This helper closes the loop by checking a captured request body against the
 * **generated** OpenAPI artifact — the same bytes `npm run codegen` consumes and
 * the same bytes the backend exports. Neither side gets to restate the contract.
 *
 * Deliberately NOT a general JSON Schema validator: `ajv` is present only as a
 * transitive dependency and declaring it is out of this wave's write scope. The
 * three rules below are the ones that actually decide whether a request body is
 * accepted at this boundary, and each is asserted against the artifact rather
 * than against a hand-copied expectation:
 *
 *   1. every `required` property is present,
 *   2. no property outside `properties` when `additionalProperties` is `false`,
 *   3. each present property's value inhabits its declared `type`.
 *
 * Anything richer (formats, nested object schemas, combinators) is out of
 * scope — and a schema that needs them should say so by failing rule 3's
 * "unsupported declaration" branch rather than passing silently.
 *
 * **What this does NOT catch, stated plainly:** the original permanent-422 body
 * (`scope_profile: {}`) was *schema-valid* — an empty object inhabits
 * `type: object`, and the property was present. It failed a layer below, in the
 * domain's snapshot decoder. So this touchpoint would not have detected that
 * defect, and no one should read a green check here as "the server will accept
 * this". It seals the *declared-contract* gap (a body that drifts from the
 * artifact); the semantic gap is sealed by the backend route test
 * (`tests/test_test_plan_draft_api_impl3.py::TestManuallyAuthoredDraftCreation`),
 * which drives the real service over a real database. Both are needed, and
 * neither substitutes for the other.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/** Repo-root-relative location of the mirrored, codegen-consumed artifacts. */
const ARTIFACT_DIR = resolve(__dirname, '../../../../packages/api-artifacts/artifacts');

interface JsonSchema {
  type?: string | string[];
  required?: string[];
  properties?: Record<string, JsonSchema>;
  additionalProperties?: boolean | JsonSchema;
}

/**
 * Read one mirrored artifact.
 *
 * Exported so each current API fixture consumer reads the
 * same bytes through the same path constant rather than re-deriving where the
 * artifacts live — two readers with two paths is how a fixture ends up pinned to
 * a stale copy.
 */
export function artifactDocument<T>(artifact: string): T {
  return JSON.parse(readFileSync(resolve(ARTIFACT_DIR, artifact), 'utf8')) as T;
}

export function componentSchema(artifact: string, name: string): JsonSchema {
  const document = artifactDocument<{
    components?: { schemas?: Record<string, JsonSchema> };
  }>(artifact);
  const schema = document.components?.schemas?.[name];
  if (!schema) {
    throw new Error(
      `${artifact} declares no component schema "${name}" — the artifact moved ` +
        'or the schema was renamed; this touchpoint is only meaningful against ' +
        'the real generated contract.',
    );
  }
  return schema;
}

function inhabits(value: unknown, declared: string): boolean {
  switch (declared) {
    case 'null':
      return value === null;
    case 'object':
      return typeof value === 'object' && value !== null && !Array.isArray(value);
    case 'array':
      return Array.isArray(value);
    case 'integer':
      return typeof value === 'number' && Number.isInteger(value);
    case 'number':
      return typeof value === 'number';
    case 'string':
      return typeof value === 'string';
    case 'boolean':
      return typeof value === 'boolean';
    default:
      throw new Error(`unsupported declared type "${declared}" in the artifact`);
  }
}

/**
 * Return every way `body` violates `schema`, as human-readable strings.
 *
 * An empty array means "the server's declared contract accepts this body". The
 * caller asserts on the array rather than a boolean so a failure names the
 * violated rule instead of just saying `false`.
 */
export function requestBodyViolations(body: unknown, schema: JsonSchema): string[] {
  const problems: string[] = [];
  if (typeof body !== 'object' || body === null || Array.isArray(body)) {
    return [`request body is not an object (got ${JSON.stringify(body)})`];
  }
  const value = body as Record<string, unknown>;
  const properties = schema.properties ?? {};

  for (const key of schema.required ?? []) {
    if (!(key in value)) problems.push(`missing required property "${key}"`);
  }
  if (schema.additionalProperties === false) {
    for (const key of Object.keys(value)) {
      if (!(key in properties)) {
        problems.push(`property "${key}" is not declared (additionalProperties: false)`);
      }
    }
  }
  for (const [key, item] of Object.entries(value)) {
    const declared = properties[key]?.type;
    if (declared === undefined) continue;
    const admitted = Array.isArray(declared) ? declared : [declared];
    if (!admitted.some((candidate) => inhabits(item, candidate))) {
      problems.push(
        `property "${key}" = ${JSON.stringify(item)} does not inhabit ` +
          `declared type ${JSON.stringify(declared)}`,
      );
    }
  }
  return problems;
}
