import { describe, expect, it } from 'vitest';

import { componentSchema, requestBodyViolations } from '../helpers/request-body-contract';

/**
 * Non-vacuity seal for the request-body ↔ artifact touchpoint
 * (test-plan-draft-create-422, 2026-08-01, D-2).
 *
 * A checker that returns `[]` for everything would make every call site pass
 * while proving nothing — which is exactly the failure mode the touchpoint
 * exists to correct (the create-draft route test asserted a payload the server
 * rejected 422 every time, and nothing noticed). So the checker's *detector
 * power* is asserted here directly: it must accept the real body, reject each
 * class of violation, and — the decisive case — reject the current body when
 * run against the pre-fix schema.
 */
describe('requestBodyViolations detector power', () => {
  const schema = () => componentSchema('headless-api.openapi.json', 'CreateTestPlanDraftRequest');

  it('accepts the manually-authored create body the route actually sends', () => {
    expect(requestBodyViolations({ created_by: 'author@corp' }, schema())).toEqual([]);
  });

  it('rejects removed generation fields on the manual create contract', () => {
    expect(requestBodyViolations({ created_by: 'a', scope_profile: null }, schema())).toContain(
      'property "scope_profile" is not declared (additionalProperties: false)',
    );
  });

  it('rejects a property the contract does not declare', () => {
    expect(requestBodyViolations({ created_by: 'a', bogus: 1 }, schema())).toContain(
      'property "bogus" is not declared (additionalProperties: false)',
    );
  });

  it('rejects a value that does not inhabit its declared type', () => {
    expect(requestBodyViolations({ created_by: 42 }, schema())).toHaveLength(1);
  });

  it('rejects a non-object body outright', () => {
    expect(requestBodyViolations('nope', schema())).toHaveLength(1);
    expect(requestBodyViolations([], schema())).toHaveLength(1);
  });

  it('is bound to the server declaration, not to the front end expectation', () => {
    // The schema as it stood before this wave: `scope_profile` required, and the
    // only value the generated `Record<string, never>` type permitted was `{}`.
    // Running today's body against yesterday's schema must fail — that is what
    // proves the checker reads the server's declaration rather than echoing the
    // front end's own expectation.
    //
    // Note what this does NOT claim: the old `{}` body was itself schema-VALID,
    // so this checker would not have caught the original defect. It failed one
    // layer down, in the domain snapshot decoder. The semantic half is sealed by
    // the backend route test (TestManuallyAuthoredDraftCreation).
    const preFix = {
      type: 'object',
      required: ['scope_profile'],
      properties: {
        scope_profile: { type: 'object' },
        created_by: { type: 'string' },
      },
      additionalProperties: false,
    };
    expect(requestBodyViolations({ created_by: 'author@corp' }, preFix)).toEqual([
      'missing required property "scope_profile"',
    ]);
  });

  it('fails loudly when the named component schema is absent', () => {
    // A silent `undefined` schema would make every check vacuously pass.
    expect(() => componentSchema('headless-api.openapi.json', 'NoSuchSchema')).toThrow(
      /declares no component schema/,
    );
  });
});
