import { beforeEach, describe, expect, it } from 'vitest';

import { headlessClient } from '@/api/headless-client';

import { headlessOk, problemDetails } from './headless-contract';
import { headlessRequest, headlessRequests, spyHeadlessTransport } from './headless-transport';

/**
 * Executed witnesses for the router's two unrouted modes.
 *
 * ⚠️ **These exist because the structural seal could not see the behaviour.** An
 * independent review replaced the loud rejection in `lookupRoute` with the same
 * quiet 404 the opt-in produces and **every gate stayed green** — `tsc`,
 * `eslint`, `vitest` and the pytest conformance file. The only guard was a rule
 * counting the string `mode: 'reject'` twice, and that mutation left both
 * strings in place. "A seal that asks about spelling", landed verbatim.
 *
 * Aggravating at the time: `reports.test.tsx` — the largest migrated suite —
 * opts its whole file into `{ unrouted: 'not-found' }`, so the loud default had
 * **zero** executable witnesses anywhere in the tree.
 *
 * Both cases assert an *effect*, not a spelling: one that an unrouted call
 * rejects, one that the opt-in resolves a 404 instead. Neither depends on the
 * wording of the message beyond the substring a maintainer would grep for.
 */
describe('spyHeadlessTransport routing', () => {
  const transport = spyHeadlessTransport();

  it('rejects an unrouted path loudly by default', async () => {
    transport.routes({
      '/headless/status': {
        get: () =>
          headlessOk('get', '/headless/status', {
            measurement_jobs: {
              counts: { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 },
              recent: [],
            },
            workers: [],
            report_automation: { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 },
          }),
      },
    });

    await expect(headlessClient.GET('/headless/jobs', {})).rejects.toThrow(/no route for GET/u);
    // The declared route still answers — otherwise "it rejects" would be
    // satisfied by a router that rejects everything.
    await expect(headlessClient.GET('/headless/status', {})).resolves.toMatchObject({
      response: { status: 200 },
    });
  });

  it('answers a 404 instead when the caller opts in', async () => {
    transport.routes({}, { unrouted: 'not-found' });

    const result = await headlessClient.GET('/headless/jobs', {});
    expect(result.error).toMatchObject({ status: 404, code: 'NOT_FOUND' });
    expect(result.data).toBeUndefined();
  });

  it('does not carry the opt-in from one test into the next', async () => {
    // The mode is reset per test alongside the table. Without that, one test
    // opting in would silently disarm the loud default for every later test in
    // the same file — the cross-test coupling the reset exists to prevent.
    await expect(headlessClient.GET('/headless/jobs', {})).rejects.toThrow(/no route for GET/u);
  });

  it('routes by method, not just by path', async () => {
    transport.routes({
      '/headless/jobs/{job_uuid}/stop': {
        post: () =>
          headlessOk('post', '/headless/jobs/{job_uuid}/stop', {
            job_uuid: 'j-1',
            stop_requested: true,
          }),
      },
    });

    await expect(
      headlessClient.POST('/headless/jobs/{job_uuid}/stop', {
        params: { path: { job_uuid: 'j-1' } },
      }),
    ).resolves.toMatchObject({ data: { job_uuid: 'j-1' } });
    // Same path, undeclared method → still loud.
    await expect(
      headlessClient.GET('/headless/jobs/{job_uuid}/stop' as '/headless/jobs', {}),
    ).rejects.toThrow(/no route for GET/u);
  });

  it('builds a problem document the FE can branch on', () => {
    // `to-api-error.ts` reads `code` to tell three 409s apart; a builder that
    // dropped it would leave every error branch code-less.
    expect(problemDetails(409, 'DRAFT_ROW_CONFLICT')).toMatchObject({
      type: 'about:blank',
      status: 409,
      code: 'DRAFT_ROW_CONFLICT',
    });
  });
});

/**
 * Executed witnesses for the **request accessors**, for the same reason the
 * routing witnesses above exist: the structural seal cannot see behaviour.
 *
 * ⚠️ An independent adversarial review changed `if (matches.length !== 1)` to
 * `if (matches.length === -1)` — so `headlessRequest` **never throws** — and
 * every gate stayed green: `tsc`, `eslint`, all 1392 vitest tests, all 304
 * pytest conformance cases. The contract's own counterexample for that MUST
 * (*"call the same operation twice and the accessor still returns a value"*)
 * had no seal at all. That is the mutation these cases kill.
 *
 * ⚠️ **`mockReset()` per case is load-bearing, not hygiene.** `vite.config.ts`
 * sets neither `clearMocks` nor `mockReset`, and `spyHeadlessTransport`'s
 * module-scope `beforeEach` re-installs the spies without clearing their call
 * history — so calls accumulate across a file. `headlessRequest`'s
 * exactly-one contract is therefore coupled to each suite resetting its own
 * mocks. It fails loudly (`saw 4`) rather than silently, which is why this is a
 * usability hazard rather than a correctness one, but a helper whose contract
 * depends on caller discipline should say so where the discipline is applied.
 */
describe('request accessors', () => {
  const transport = spyHeadlessTransport();
  const ATTEMPTS = '/headless/sessions/{session_id}/attempts';
  const JOBS = '/headless/jobs';

  const attemptsOk = (): ReturnType<typeof headlessOk<'get', typeof ATTEMPTS>> =>
    headlessOk('get', ATTEMPTS, { items: [], next_cursor: null });

  beforeEach(() => {
    transport.GET.mockReset();
    transport.GET.mockResolvedValue(attemptsOk());
  });

  it('refuses to guess when the operation was called more than once', async () => {
    await headlessClient.GET(ATTEMPTS, { params: { path: { session_id: 1 } } });
    await headlessClient.GET(ATTEMPTS, { params: { path: { session_id: 2 } } });

    // `mock.calls[0]` silently answered "the first one". Naming the count is
    // the difference between an assertion about *a* request and one about
    // *the* request.
    expect(() => headlessRequest(transport.GET, 'get', ATTEMPTS)).toThrow(/saw 2/u);
  });

  it('refuses when the operation was never called', () => {
    expect(() => headlessRequest(transport.GET, 'get', ATTEMPTS)).toThrow(/saw 0/u);
  });

  it('names the operation in the refusal', () => {
    expect(() => headlessRequest(transport.GET, 'get', ATTEMPTS)).toThrow(/GET .*attempts/u);
  });

  it('returns the single request when there is exactly one', async () => {
    await headlessClient.GET(ATTEMPTS, { params: { path: { session_id: 7 } } });

    expect(headlessRequest(transport.GET, 'get', ATTEMPTS).params.path).toEqual({ session_id: 7 });
  });

  it('filters by operation rather than by call order', async () => {
    // Interleaved on purpose: an implementation that indexed instead of
    // filtering would answer the jobs call here.
    await headlessClient.GET(JOBS, {});
    await headlessClient.GET(ATTEMPTS, { params: { path: { session_id: 9 } } });
    await headlessClient.GET(JOBS, {});

    expect(headlessRequest(transport.GET, 'get', ATTEMPTS).params.path).toEqual({ session_id: 9 });
    expect(headlessRequests(transport.GET, 'get', JOBS)).toHaveLength(2);
    expect(headlessRequests(transport.GET, 'get', ATTEMPTS)).toHaveLength(1);
  });

  it('answers an empty list rather than throwing when a plural read finds nothing', () => {
    // The plural accessor is what a suite reaches for when zero calls is a
    // legitimate outcome — asserting an endpoint was *not* hit.
    expect(headlessRequests(transport.GET, 'get', ATTEMPTS)).toEqual([]);
  });
});
