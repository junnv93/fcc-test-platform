import { beforeEach, describe, expect, it } from 'vitest';

import { headlessClient } from '@/api/headless-client';
import {
  exportTestPlanDraft,
  fetchHeadlessJobs,
  fetchSessionAttempts,
  submitTestPlanGeneration,
} from '@/api/headless-client';

import {
  headlessDownload,
  headlessEmptyOk,
  headlessOk,
  headlessProblem,
  problemDetails,
} from './helpers/headless-contract';
import {
  headlessRequest,
  headlessRequests,
  spyHeadlessTransport,
} from './helpers/headless-transport';

import type {
  TestPlanGenerationPreviewResponse,
  TestPlanGenerationRequest,
} from '@/api/headless-client';

/**
 * The headless **operation helpers**, judged by what they do — not by what
 * their source says.
 *
 * ## Why this file exists
 *
 * `tests/headless-client.test.ts` is a drift gate over the *generated types*:
 * it proves the codegen chain and the exported surface, and it would stay green
 * if every helper's body were replaced. The route suites
 * (`jobs.test.tsx`, `sessions.test.tsx`, …) drive the helpers, but they stub the
 * transport with well-formed responses, so the decisions the helpers make **on
 * behalf of** the caller are never observed.
 *
 * A mutation battery over `headless-client.ts` found that concretely: six
 * mutations of the helper bodies, and only two died. Three survivors, each of
 * which is a real behaviour the screen depends on:
 *
 * | survivor | what it silently becomes |
 * |---|---|
 * | drop `?? []` in `fetchHeadlessJobs` | `undefined.flatMap` at the call site of a 204/empty body |
 * | drop `?? { items: [], next_cursor: null }` in `fetchSessionAttempts` | `getNextPageParam` reads `undefined.next_cursor` |
 * | replace the forwarded `Idempotency-Key` with a constant | two different submissions dedupe onto one job |
 *
 * ⚠️ The third is the dangerous one: a constant key makes the *retry* path look
 * perfect while making two genuinely different submissions collide, and nothing
 * downstream can tell the difference. It is exactly the shape a type checker
 * cannot see — the header is a `string` either way.
 *
 * ## The battery, in the tree
 *
 * Measured on this commit, against `src/api/headless-client.ts`. Each row is a
 * one-line edit; the harness asserted the edit **applied** before running (an
 * unapplied mutation and a surviving one produce the same output), restored
 * from a byte copy afterwards, and `git status` was clean at the end.
 *
 * ```
 * KILLED | return data ?? [];                     -> return data as never;
 * KILLED | return data ?? { items: [], next_cursor: null };  -> return data as never;
 * KILLED | 'Idempotency-Key': idempotencyKey      -> 'Idempotency-Key': 'constant-key'
 * KILLED | cursor === undefined ? undefined : {cursor} -> { cursor }
 * KILLED | { cursor }                             -> { cursor: encodeURIComponent(cursor) }
 * KILLED | body: { request, preview }             -> body: { request }
 * KILLED | return data ?? [];                     -> return [...(data ?? [])]
 * ```
 *
 * 7/7. The last row is the one worth keeping: it is not a defect, it is the
 * *shape of a test that would pass for the wrong reason* — an operation that
 * always allocates satisfies "answers an empty list" while quietly breaking
 * identity, so the pass-through case is asserted with `toBe`.
 *
 * ⚠️ What this file still does not cover: the error branches
 * (`apiErrorFromResponse`) and the other ~30 operations in the module. It seals
 * the three decisions a battery found unsealed, and says so rather than
 * implying the module is covered.
 */

const transport = spyHeadlessTransport();

/** A 2xx with no body — what `openapi-fetch` hands back for an empty response. */
const emptyOk = headlessEmptyOk();

const JOBS_PATH = '/headless/jobs';
const SUBMIT_PATH = '/headless/projects/{project_id}/test-plan/generations';
const ATTEMPTS_PATH = '/headless/sessions/{session_id}/attempts';

const GENERATION_REQUEST = {
  technologies: ['WLAN'],
} as unknown as TestPlanGenerationRequest;

/**
 * A stand-in preview proof. Cast through `unknown` rather than `any` so the
 * looseness is confined to this one binding — the helper's real parameter type
 * still constrains every call site below.
 */
const PREVIEW = { row_count: 3 } as unknown as TestPlanGenerationPreviewResponse;

describe('fetchHeadlessJobs — the empty-body fallback belongs to the operation', () => {
  beforeEach(() => {
    transport.GET.mockReset();
  });

  it('answers an empty list when the body is absent', async () => {
    transport.GET.mockResolvedValue(emptyOk);

    const jobs = await fetchHeadlessJobs();

    // Not `toEqual([])` alone: the point is that callers may `flatMap` it.
    expect(Array.isArray(jobs)).toBe(true);
    expect(jobs).toEqual([]);
    expect(() => jobs.flatMap((job) => [job])).not.toThrow();
  });

  it('passes a present body through unchanged', async () => {
    const body = [{ id: 1, status: 'queued', excel_path: 'alpha.xlsx' }];
    transport.GET.mockResolvedValue(headlessOk('get', JOBS_PATH, body));

    // The fallback must not be a rewrite: an operation that *always* returns a
    // fresh array would also pass the test above.
    await expect(fetchHeadlessJobs()).resolves.toBe(body);
  });
});

describe('fetchSessionAttempts — page shape and the first-page request', () => {
  beforeEach(() => {
    transport.GET.mockReset();
  });

  it('answers an empty page when the body is absent', async () => {
    transport.GET.mockResolvedValue(emptyOk);

    const page = await fetchSessionAttempts(7);

    expect(page).toEqual({ items: [], next_cursor: null });
    // `next_cursor` must be `null`, not `undefined`: `getNextPageParam` reads it
    // to decide whether another page exists, and `undefined` there means "no
    // answer" rather than "no more pages".
    expect(page.next_cursor).toBeNull();
  });

  it('omits the query object entirely on the first page', async () => {
    transport.GET.mockResolvedValue(emptyOk);

    await fetchSessionAttempts(7);

    const options = headlessRequest(transport.GET, 'get', ATTEMPTS_PATH);
    expect(options.params).toEqual({ path: { session_id: 7 } });
    expect('query' in options.params).toBe(false);
  });

  it('sends the opaque cursor verbatim on later pages', async () => {
    transport.GET.mockResolvedValue(emptyOk);

    await fetchSessionAttempts(7, 'opaque-cursor-token');

    const options = headlessRequest(transport.GET, 'get', ATTEMPTS_PATH);
    // Verbatim: the cursor is the server's own token and the client must not
    // parse, trim or re-encode it.
    expect(options.params.query).toEqual({ cursor: 'opaque-cursor-token' });
  });

  it('names the session in the path', async () => {
    transport.GET.mockResolvedValue(emptyOk);

    await fetchSessionAttempts(42, 'c');

    // The path no longer needs asserting separately: `headlessRequest` finds
    // the call *by* operation, so reaching a value at all is the assertion.
    const options = headlessRequest(transport.GET, 'get', ATTEMPTS_PATH);
    expect(options.params.path).toEqual({ session_id: 42 });
  });
});

describe('submitTestPlanGeneration — the idempotency key is the caller’s', () => {
  beforeEach(() => {
    transport.POST.mockReset();
  });

  it('forwards the key it was given', async () => {
    transport.POST.mockResolvedValue(
      headlessOk('post', SUBMIT_PATH, {
        job_id: 'j1',
        project_id: 'p1',
        status: 'queued',
        request_sha256: 'a'.repeat(64),
        matrix_revision: 'db-v1',
      }),
    );

    await submitTestPlanGeneration('p1', 'key-alpha', GENERATION_REQUEST, PREVIEW);

    const options = headlessRequest(transport.POST, 'post', SUBMIT_PATH);
    expect(options.params.header).toEqual({ 'Idempotency-Key': 'key-alpha' });
  });

  it('sends a different key for a different submission', async () => {
    transport.POST.mockResolvedValue(
      headlessOk('post', SUBMIT_PATH, {
        job_id: 'j1',
        project_id: 'p1',
        status: 'queued',
        request_sha256: 'a'.repeat(64),
        matrix_revision: 'db-v1',
      }),
    );

    // The single assertion a constant key cannot satisfy. Asserting one call in
    // isolation does not distinguish "forwards the argument" from "always sends
    // this string" — two calls do.
    await submitTestPlanGeneration('p1', 'key-alpha', GENERATION_REQUEST, PREVIEW);
    await submitTestPlanGeneration('p1', 'key-beta', GENERATION_REQUEST, PREVIEW);

    const keys = headlessRequests(transport.POST, 'post', SUBMIT_PATH).map(
      (init) => init.params.header,
    );
    expect(keys).toEqual([{ 'Idempotency-Key': 'key-alpha' }, { 'Idempotency-Key': 'key-beta' }]);
  });

  it('carries the request and its preview proof in the body', async () => {
    transport.POST.mockResolvedValue(
      headlessOk('post', SUBMIT_PATH, {
        job_id: 'j1',
        project_id: 'p1',
        status: 'queued',
        request_sha256: 'a'.repeat(64),
        matrix_revision: 'db-v1',
      }),
    );
    await submitTestPlanGeneration('p1', 'key-alpha', GENERATION_REQUEST, PREVIEW);

    const options = headlessRequest(transport.POST, 'post', SUBMIT_PATH);
    // The preview is a *proof* the server re-checks; dropping it would turn a
    // rejected submission into an accepted one against stale axes.
    expect(options.body.request).toBe(GENERATION_REQUEST);
    expect(options.body.preview).toBe(PREVIEW);
    expect(options.params.path).toEqual({ project_id: 'p1' });
  });
});

/* ------------------------------------------------------------------ downloads
 *
 * ⚠️ **This section exists because an acceptance criterion cited a witness that
 * did not exist** (independent adversarial review, 2026-09-12). The wave that
 * rewrote both download operations claimed "production behaviour unchanged"
 * against `sessions.test.tsx` **and** `test-plans.test.tsx` — and the second
 * file contains no draft-export case at all. `exportTestPlanDraft`'s only
 * behavioural witness was a Playwright spec in a lane the wave never ran.
 *
 * Both download operations now share one seam (`toDownload`), so the three
 * decisions below are shared too — which makes the gap worse, not better: a
 * regression in the seam shows up in whichever operation is actually exercised,
 * and only one was.
 */
describe('exportTestPlanDraft — the download seam, on the operation nobody was watching', () => {
  const DRAFT_EXPORT_PATH = '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/export';

  beforeEach(() => {
    transport.GET.mockReset();
  });

  it('names the file from `content-disposition` when the response sent one', async () => {
    transport.GET.mockResolvedValue(
      headlessDownload('get', DRAFT_EXPORT_PATH, new Blob(['xlsx']), {
        'content-disposition': 'attachment; filename="draft-7.xlsx"',
      }),
    );

    const download = await exportTestPlanDraft('p1', 'd1', 'fallback.xlsx');

    expect(download.filename).toBe('draft-7.xlsx');
    expect(download.blob).toBeInstanceOf(Blob);
  });

  it('falls back to the caller’s name when the header is absent', async () => {
    // Not the same assertion as above with a different expectation: the header
    // path and the fallback path are different branches of the shared seam, and
    // a seam that always returned the fallback would pass a header-only test.
    transport.GET.mockResolvedValue(headlessDownload('get', DRAFT_EXPORT_PATH, new Blob(['xlsx'])));

    const download = await exportTestPlanDraft('p1', 'd1', 'fallback.xlsx');

    expect(download.filename).toBe('fallback.xlsx');
  });

  it('sends the project and draft ids, and asks for a blob', async () => {
    transport.GET.mockResolvedValue(headlessDownload('get', DRAFT_EXPORT_PATH, new Blob(['xlsx'])));

    await exportTestPlanDraft('p1', 'd1', 'fallback.xlsx');

    const options = headlessRequest(transport.GET, 'get', DRAFT_EXPORT_PATH);
    expect(options.params.path).toEqual({ project_id: 'p1', draft_id: 'd1' });
    // ⚠️ The `parseAs` assertion is the *runtime* half of the consumption axis.
    // The declaration and its type constraint are checked by the compiler and by
    // the structural census; neither observes what actually reaches the client.
    expect(headlessClient.GET).toHaveBeenCalledWith(DRAFT_EXPORT_PATH, {
      params: { path: { project_id: 'p1', draft_id: 'd1' } },
      parseAs: 'blob',
    });
  });

  it('throws through the shared factory when the server refuses', async () => {
    transport.GET.mockResolvedValue(
      headlessProblem('get', DRAFT_EXPORT_PATH, 404, problemDetails(404, 'NOT_FOUND')),
    );

    // The seam's error branch: `error || data === undefined`. A seam that only
    // checked `data === undefined` would resolve with a `Blob`-less download.
    // `code` (the RFC 9457 extension), not the HTTP status: the screen branches
    // on it, and the seam drops it if the whole problem body is not forwarded.
    await expect(exportTestPlanDraft('p1', 'd1', 'fallback.xlsx')).rejects.toMatchObject({
      code: 'NOT_FOUND',
      status: 404,
    });
  });

  it('throws when the response carried no body at all', async () => {
    // The other half of the same predicate — a 2xx with no body is a legitimate
    // transport state (see `headless-empty-body-fact.test.ts`) and it must not
    // become `{ blob: undefined }`.
    transport.GET.mockResolvedValue(headlessEmptyOk());

    await expect(exportTestPlanDraft('p1', 'd1', 'fallback.xlsx')).rejects.toBeTruthy();
  });
});
