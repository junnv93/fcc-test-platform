/**
 * Why `HeadlessEmptyEnvelope` rides on **every** operation — as an observation
 * rather than a paragraph (`headless-envelope-consumption-axis`, 2026-09-12).
 *
 * ## The question this answers
 *
 * The sibling axis in this wave narrowed the blob member: which operations are
 * consumed as a file is a decision this codebase makes, so it was lifted into a
 * declaration. The obvious next move is to do the same to the empty member —
 * derive it from the schema, admit it only where a content-less 2xx is declared.
 *
 * **That derivation is wrong twice over, and both halves are measurable:**
 *
 * 1. **The derived set would be empty.** No operation on this surface declares a
 *    content-less 2xx (measured against `docs/api/headless-api.openapi.json`:
 *    zero). A schema-derived empty member would therefore be admissible nowhere,
 *    and every legitimate empty stub — the ones exercising `fetchHeadlessJobs`'
 *    `?? []` and `fetchSessionAttempts`' page fallback — becomes unwritable.
 * 2. **The wire disagrees with the schema.** `openapi-fetch` short-circuits on
 *    the *response*, not on the operation: a 204 or a `Content-Length: 0` yields
 *    `{ data: undefined }` for anything. Its own `FetchResponse` type does not
 *    model that state at all, which is exactly why the operation helpers in
 *    `headless-client.ts` carry `data ?? …` fallbacks — they are handling a
 *    reality the types deny.
 *
 * ## Why this is a test and not a comment
 *
 * Half (2) is a fact about a **dependency**, and a dependency can change. A
 * comment asserting it decays silently; this file goes red the day
 * `openapi-fetch` starts keying that branch on the schema, and whoever sees the
 * failure reads this and narrows the member on purpose.
 *
 * ⚠️ **The real client, not a stub of it.** The whole point is what the library
 * does, so the only thing faked here is `fetch`. Stubbing `openapi-fetch` and
 * then asserting on the stub would prove nothing — the shape this repository has
 * paid for more than once (`a-probe-can-answer-about-the-wrong-thing`).
 */
import createClient from 'openapi-fetch';
import { describe, expect, it } from 'vitest';

import type { HeadlessApiPaths } from '@/api/headless-client';

/** An operation that declares a JSON body for 200 — the case the derivation
 *  would have called "cannot be empty". */
const JSON_OPERATION = '/headless/jobs';

/**
 * A client whose only fake is `fetch`. `baseUrl` is arbitrary: nothing leaves
 * the process, and the stub answers whatever it is handed regardless of URL.
 */
function clientAnswering(response: Response) {
  return createClient<HeadlessApiPaths>({
    baseUrl: 'http://transport.invalid',
    fetch: () => Promise.resolve(response),
  });
}

describe('openapi-fetch resolves an empty success for any operation', () => {
  it('answers `{ data: undefined, error: undefined }` to a 204 on a JSON operation', async () => {
    const client = clientAnswering(new Response(null, { status: 204 }));

    const result = await client.GET(JSON_OPERATION, {});

    // Both members absent is the third state the envelope union models. Asserted
    // as `undefined` rather than falsy: `null` and `[]` would both be falsy and
    // neither is what the transport hands back.
    expect(result.data).toBeUndefined();
    expect(result.error).toBeUndefined();
    // …and the response really is there, which is what the stub envelope's
    // `response` member stands in for.
    expect(result.response.status).toBe(204);
  });

  it('does the same for a 200 whose body is declared empty by `Content-Length`', async () => {
    const client = clientAnswering(
      new Response(null, { status: 200, headers: { 'Content-Length': '0' } }),
    );

    const result = await client.GET(JSON_OPERATION, {});

    expect(result.data).toBeUndefined();
    expect(result.error).toBeUndefined();
    expect(result.response.status).toBe(200);
  });

  it('still parses a present body, so the two cases above are not vacuous', async () => {
    // ⚠️ The positive control. A client that answered `undefined` to everything
    // would pass both assertions above and prove the opposite of what they say.
    const body = [{ id: 1, status: 'queued', excel_path: 'alpha.xlsx' }];
    const client = clientAnswering(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    const result = await client.GET(JSON_OPERATION, {});

    expect(result.data).toEqual(body);
    expect(result.error).toBeUndefined();
  });
});
