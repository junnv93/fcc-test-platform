import { describe, expect, it } from 'vitest';

import {
  apiErrorFromResponse,
  clientOriginatedApiError,
  problemCode,
  problemParams,
  toApiError,
} from '@/api/to-api-error';

/**
 * toApiError SSOT tests — proves the factory is byte-identical to the inlined
 * `Object.assign(new Error(msg), { status }) as ApiError` expression it replaced
 * across `platform-client.ts` and the chambers/test-plans routes, and that the
 * optional RFC 9457 `code` extension is only attached when supplied (so the
 * common status-only error keeps `Object.keys(error) === ['status']`).
 */
describe('toApiError', () => {
  it('produces a real Error carrying the message', () => {
    const error = toApiError('coverage lookup failed', 503);
    expect(error).toBeInstanceOf(Error);
    expect(error.message).toBe('coverage lookup failed');
  });

  it('decorates the HTTP status', () => {
    expect(toApiError('boom', 409).status).toBe(409);
    expect(toApiError('boom', 404).status).toBe(404);
  });

  it('keeps status === undefined for a network error (no response)', () => {
    const error = toApiError('network down', undefined);
    expect(error.status).toBeUndefined();
    // Mirrors Object.assign: the `status` key is always present (value
    // undefined), `code` is not.
    expect(Object.keys(error)).toEqual(['status']);
  });

  it('is byte-identical to the inlined Object.assign expression', () => {
    const message = 'draft create failed';
    const status = 422;
    const inlined = Object.assign(new Error(message), { status });
    const factory = toApiError(message, status);
    expect(factory.message).toBe(inlined.message);
    expect(factory.status).toBe(inlined.status);
    expect(Object.keys(factory)).toEqual(Object.keys(inlined));
  });

  it('attaches the optional code extension only when supplied', () => {
    const withCode = toApiError('conflict', 409, 'CLAIM_CONFLICT');
    expect(withCode.code).toBe('CLAIM_CONFLICT');
    expect(Object.keys(withCode).sort()).toEqual(['code', 'status']);

    const withoutCode = toApiError('conflict', 409);
    expect(withoutCode.code).toBeUndefined();
    expect(Object.keys(withoutCode)).toEqual(['status']);
  });
});

/**
 * apiErrorFromResponse / clientOriginatedApiError
 * (boundary-plumbing-and-node-liveness, 2026-08-19).
 *
 * The property under test is not "it copies four fields" — it is that a route
 * **cannot forget** the machine-readable half of a problem body. 26 headless call
 * sites did forget it, and the cost was measurable: four refined-copy arms in
 * `ui/errors.ts` were unreachable because the code never crossed this boundary.
 */
describe('apiErrorFromResponse', () => {
  const problem = {
    type: 'about:blank',
    title: 'Unprocessable Entity',
    status: 422,
    code: 'DRAFT_EMPTY',
    params: { field: 'rows' },
  };

  it('carries the code AND the params without being asked', () => {
    const error = apiErrorFromResponse('draft publish failed', {
      error: problem,
      response: { status: 422 },
    });
    expect(error.status).toBe(422);
    expect(error.code).toBe('DRAFT_EMPTY');
    expect(error.params).toEqual({ field: 'rows' });
  });

  it('has no argument through which a caller could drop the code', () => {
    // The signature takes the failure whole. Passing only the message is a type
    // error, and passing the failure necessarily passes the body — that is the
    // entire design, so it is asserted rather than left to review.
    expect(apiErrorFromResponse.length).toBe(2);
  });

  it('omits absent extensions rather than setting them undefined', () => {
    // The own-property rule the ~53 existing toApiError call sites depend on.
    const error = apiErrorFromResponse('list failed', {
      error: { detail: 'legacy body' },
      response: { status: 500 },
    });
    expect(Object.keys(error)).toEqual(['status']);
    expect(error.code).toBeUndefined();
    expect(error.params).toBeUndefined();
  });

  it('keeps status undefined when no response was produced', () => {
    const error = apiErrorFromResponse('network down', { error: undefined });
    expect(error.status).toBeUndefined();
    expect(Object.keys(error)).toEqual(['status']);
  });

  it('survives a non-object error body', () => {
    for (const body of ['a string', 0, null, undefined, [1, 2]]) {
      const error = apiErrorFromResponse('boom', { error: body, response: { status: 400 } });
      expect(error.status).toBe(400);
      expect(error.code).toBeUndefined();
    }
  });

  it('is byte-identical to the fully-spelled toApiError call it replaces', () => {
    const failure = { error: problem, response: { status: 422 } };
    const explicit = toApiError(
      'draft publish failed',
      422,
      problemCode(problem),
      problemParams(problem),
    );
    const derived = apiErrorFromResponse('draft publish failed', failure);
    expect(derived.message).toBe(explicit.message);
    expect(derived.status).toBe(explicit.status);
    expect(derived.code).toBe(explicit.code);
    expect(derived.params).toEqual(explicit.params);
    expect(Object.keys(derived).sort()).toEqual(Object.keys(explicit).sort());
  });
});

describe('clientOriginatedApiError', () => {
  it('models a failure that never met a response', () => {
    const error = clientOriginatedApiError('download unreachable');
    expect(error.status).toBeUndefined();
    expect(Object.keys(error)).toEqual(['status']);
  });

  it('carries a client-minted status and code when the client knows them', () => {
    // `status` is OMITTED, not set to undefined: `exactOptionalPropertyTypes`
    // makes the explicit-undefined spelling a compile error, so the tsconfig
    // enforces the same own-property rule the factory documents.
    const error = clientOriginatedApiError('grant expired before use', {
      code: 'DOWNLOAD_EXPIRED',
    });
    expect(error.code).toBe('DOWNLOAD_EXPIRED');

    const local = clientOriginatedApiError('bulk import invalid', { status: 400 });
    expect(local.status).toBe(400);
    expect(Object.keys(local)).toEqual(['status']);
  });

  it('is a different fact from a response-derived failure', () => {
    // Both can end up with `status: undefined`, which is exactly why the two
    // factories are named separately: the reader of a call site can tell whether
    // the server was reached.
    const fromResponse = apiErrorFromResponse('boom', { error: undefined });
    const fromClient = clientOriginatedApiError('boom');
    expect(fromResponse.status).toBeUndefined();
    expect(fromClient.status).toBeUndefined();
    expect(apiErrorFromResponse).not.toBe(clientOriginatedApiError);
  });
});
