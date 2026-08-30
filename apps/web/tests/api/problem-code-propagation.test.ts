import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import createClient from 'openapi-fetch';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  fetchChamberProgress,
  fetchCoveragePage,
  fetchProjectDetail,
  platformClient,
} from '@/api/platform-client';
import { problemCode, toApiError } from '@/api/to-api-error';

import type { PlatformApiPaths } from '@/api/platform-client';
import type { ApiError, ErrorCode } from '@/shared/api-error';

/**
 * S6 — RFC 9457 `code` reaches `ApiError.code` (fe-data-layer-robustness M5/D5,
 * 2026-07-19).
 *
 * **The defect this file reproduces.** The backend has emitted
 * `application/problem+json` with a stable 16-member `code` since Increment B1,
 * and `openapi-fetch` already parses it (its content-type sniff matches
 * `…+json`). Every one of the 23 `toApiError` call sites in `platform-client.ts`
 * nevertheless passed only `response?.status`, so the FE discarded the one field
 * that separates `DRAFT_ROW_CONFLICT` / `PUBLISH_CONFLICT` / `CLAIM_CONFLICT`
 * (all HTTP 409) and had to guess an operator message from the status alone.
 *
 * Scope note: this wave seals the PRODUCER side. `describeApiError` switching
 * its branches from status to `code` is W2 (route/ui surface).
 */

const PLATFORM_TYPES_PATH = resolve(
  __dirname,
  '..',
  '..',
  'src',
  'api',
  'generated',
  'platform-api.types.ts',
);

/**
 * Stub the typed client's verb at the openapi-fetch boundary — the same idiom
 * `platform-client.test.ts` uses. `error` is what openapi-fetch hands back for a
 * non-2xx JSON body (asserted independently below against a real `Response`).
 */
function stubProblemResponse(status: number, body: Record<string, unknown>): void {
  vi.spyOn(platformClient, 'GET').mockResolvedValue({
    data: undefined,
    error: body,
    response: { status } as Response,
  } as never);
}

function problem(code: ErrorCode, status: number): Record<string, unknown> {
  return {
    type: 'about:blank',
    title: code,
    status,
    detail: 'seeded by S6',
    code,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('S6 — problem+json `code` propagates through the platform client', () => {
  // One representative per read/detail/proxy family. All 23 call sites go
  // through the identical `toApiError(msg, response?.status, problemCode(error))`
  // expression, and the source-level audit below proves none was missed.
  const CASES: readonly (readonly [string, ErrorCode, number, () => Promise<unknown>])[] = [
    ['coverage page', 'FORBIDDEN', 403, () => fetchCoveragePage('p-1')],
    ['project detail', 'NOT_FOUND', 404, () => fetchProjectDetail('p-1')],
    [
      'chamber progress (central proxy)',
      'UPSTREAM_UNAVAILABLE',
      503,
      () => fetchChamberProgress('cham-1'),
    ],
  ];

  it.each(CASES)('%s surfaces `%s`', async (_label, code, status, call) => {
    stubProblemResponse(status, problem(code, status));
    await expect(call()).rejects.toMatchObject({ status, code } satisfies Partial<ApiError>);
  });

  it('still carries the status when the body is NOT a problem document', async () => {
    // Legacy `{detail: "..."}` responses must degrade to status-only, never throw.
    stubProblemResponse(409, { detail: 'legacy shape' });
    await expect(fetchCoveragePage('p-1')).rejects.toMatchObject({ status: 409 });
    await expect(fetchCoveragePage('p-1')).rejects.toSatisfy(
      (error: unknown) => (error as ApiError).code === undefined,
    );
  });
});

describe('S6 — the load-bearing openapi-fetch assumption is verified, not assumed', () => {
  it('surfaces an application/problem+json body in `error` (not swallowed)', async () => {
    // The whole M5 fix rests on openapi-fetch parsing `…+json`, not only
    // `application/json`. Proven here against a REAL Response through a REAL
    // client, so a dependency bump that narrows the sniff fails loudly.
    const client = createClient<PlatformApiPaths>({
      baseUrl: 'https://platform.test',
      fetch: () =>
        Promise.resolve(
          new Response(JSON.stringify(problem('CLAIM_CONFLICT', 409)), {
            status: 409,
            headers: { 'Content-Type': 'application/problem+json' },
          }),
        ),
    });
    const { error, response } = await client.GET('/platform/projects/{project_id}', {
      params: { path: { project_id: 'p-1' } },
    });
    expect(response.status).toBe(409);
    expect(problemCode(error)).toBe('CLAIM_CONFLICT');
  });
});

describe('S6 — problemCode extraction is total (never throws)', () => {
  it.each([
    ['null', null],
    ['undefined', undefined],
    ['a string body', 'boom'],
    ['a number body', 500],
    ['an array body', []],
    ['an object without code', { detail: 'x' }],
    ['a non-string code', { code: 42 }],
  ])('returns undefined for %s', (_label, body) => {
    expect(problemCode(body)).toBeUndefined();
  });

  it('extracts the code from a well-formed problem document', () => {
    expect(problemCode(problem('CLAIM_CONFLICT', 409))).toBe('CLAIM_CONFLICT');
  });

  it('forwards a server code the FE types do not know yet (forward compatible)', () => {
    // A backend that ships a new ErrorCode before the FE regenerates types must
    // still surface it — the compile-time union governs FE-authored literals.
    expect(problemCode({ code: 'BRAND_NEW_CODE' })).toBe('BRAND_NEW_CODE');
  });
});

describe('S6 — the ErrorCode union is generated, not hand-written', () => {
  it('is sourced from the codegen artifact', () => {
    const generated = readFileSync(PLATFORM_TYPES_PATH, 'utf-8');
    expect(generated).toContain('ErrorCode:');
    for (const code of ['CLAIM_CONFLICT', 'UPSTREAM_UNAVAILABLE', 'VALIDATION_ERROR']) {
      expect(generated).toContain(code);
    }
  });

  it('attaches `code` only when supplied (no spurious undefined key)', () => {
    expect(Object.keys(toApiError('x', 409, 'CLAIM_CONFLICT')).sort()).toEqual(['code', 'status']);
    expect(Object.keys(toApiError('x', 409))).toEqual(['status']);
  });
});

describe('S6 — every platform-client throw site forwards the code', () => {
  /** Source-level completeness audit: a NEW endpoint added with a code-dropping
   *  shape is the exact regression this seal removes, and a behavioural test per
   *  endpoint would not cover one that does not exist yet.
   *
   *  boundary-plumbing-and-node-liveness (2026-08-19) — this used to require that
   *  every `toApiError(` call in the file also mentioned `problemCode(error)`.
   *  That named a *mechanism*: it could only ever check the sites that spelled
   *  the arguments out, and it said nothing about `params`, which all 48 sites
   *  were in fact dropping. The sites now go through `apiErrorFromResponse`,
   *  which takes the failure whole and extracts both members itself — so the
   *  property is stronger and the assertion is that the droppable factory is
   *  gone from the file entirely.
   */
  const clientSource = (name: string) =>
    readFileSync(resolve(__dirname, '..', '..', 'src', 'api', name), 'utf-8');

  it.each(['platform-client.ts', 'session-client.ts', 'headless-client.ts'])(
    'builds every failure through the non-droppable factory — %s',
    (name) => {
      const source = clientSource(name);
      // ⚠️ `toApiError(` only — NOT `Object.assign(new Error`. `session-client.ts`
      // carries prose explaining the hand-rolled shape this wave removed, and a
      // raw-source scan flags its own documentation. The comment-aware version of
      // that scan is owned by
      // `tests/test_frontend_architecture_conformance.py::TestApiCallsKeepTheProblemBody`,
      // which strips comments before counting; duplicating it here without a
      // stripper would teach authors to delete the explanation.
      expect(source.match(/\btoApiError\s*\(/g) ?? []).toEqual([]);
    },
  );

  it('platform-client actually throws through the factory (non-vacuity)', () => {
    // Without this, the assertion above is satisfied by a file that throws
    // nothing at all.
    const calls = clientSource('platform-client.ts').match(/apiErrorFromResponse\(/g) ?? [];
    expect(calls.length).toBeGreaterThan(20);
  });

  it('every factory call hands over the response body, never just a status', () => {
    for (const name of ['platform-client.ts', 'session-client.ts']) {
      const source = clientSource(name);
      const calls = source.match(/apiErrorFromResponse\([^;]*?\)/g) ?? [];
      expect(calls.length).toBeGreaterThan(0);
      for (const call of calls) {
        expect(call).toContain('error');
        expect(call).toContain('response');
      }
    }
  });
});
