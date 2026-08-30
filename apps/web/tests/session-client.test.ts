import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  fetchSessionInfo,
  fetchSessionProgress,
  sessionClient,
  stopSession,
  uploadSessionWorkbook,
} from '@/api/session-client';

/**
 * The session surface must not throw away the problem body
 * (session-workbook-upload-ui M3, 2026-09-01).
 *
 * ⚠️ WHAT THIS FILE EXISTS FOR. Since 2026-08-23 the node publishes six
 * session-scoped `ErrorCode`s and the frontend `ErrorCode` union has named them
 * ever since — yet **not one of them had ever reached a screen**. Every call
 * site built its failure as `Object.assign(new Error(msg), { status })`, so the
 * `code` the client is supposed to branch on was parsed by `openapi-fetch`,
 * handed back, and dropped one line later. The screen could name the codes and
 * could not observe one.
 *
 * The static half of the seal (no route hand-rolls a failure; every helper
 * passes `problemCode`/`problemParams`) lives in
 * `tests/test_frontend_architecture_conformance.py::TestSessionCallsKeepTheProblemBody`.
 * A source scan alone is silent about a NEW call site, so the behaviour is
 * asserted here: a real problem body in, a decorated error out.
 */

const problemBody = (code: string, params?: Record<string, unknown>) => ({
  type: 'about:blank',
  title: 'x',
  status: 0,
  detail: 'server-internal prose that must never reach a screen',
  code,
  ...(params === undefined ? {} : { params }),
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('session client — failures carry the machine-readable code', () => {
  it('carries `code` and `params` off a workbook upload refusal', async () => {
    vi.spyOn(sessionClient, 'POST').mockResolvedValue({
      data: undefined,
      error: problemBody('WORKBOOK_UPLOAD_TOO_LARGE', { max: 2048 }),
      response: { status: 413, headers: new Headers() },
    } as never);

    await expect(uploadSessionWorkbook(new File(['x'], 'plan.xlsx'))).rejects.toMatchObject({
      status: 413,
      code: 'WORKBOOK_UPLOAD_TOO_LARGE',
      params: { max: 2048 },
    });
  });

  it('carries `code` off stop, progress and info too', async () => {
    // Not incidental coverage: these are the three calls that were already
    // shipping when the codes were published, so they are exactly the ones a
    // reader would assume were fine.
    vi.spyOn(sessionClient, 'POST').mockResolvedValue({
      data: undefined,
      error: problemBody('SESSION_NODE_NOT_PROVISIONED'),
      response: { status: 503, headers: new Headers() },
    } as never);
    vi.spyOn(sessionClient, 'GET').mockResolvedValue({
      data: undefined,
      error: problemBody('FORBIDDEN'),
      response: { status: 403, headers: new Headers() },
    } as never);

    await expect(stopSession()).rejects.toMatchObject({
      code: 'SESSION_NODE_NOT_PROVISIONED',
    });
    await expect(fetchSessionProgress()).rejects.toMatchObject({ code: 'FORBIDDEN' });
    await expect(fetchSessionInfo()).rejects.toMatchObject({ code: 'FORBIDDEN' });
  });

  it('still produces a usable error for a body that carries no code', async () => {
    // A network failure or a legacy non-problem body must not become a crash;
    // `status` alone is what the taxonomy falls back to.
    vi.spyOn(sessionClient, 'POST').mockResolvedValue({
      data: undefined,
      error: 'not a problem document',
      response: { status: 500, headers: new Headers() },
    } as never);

    const failure = await stopSession().catch((e: unknown) => e);
    expect(failure).toMatchObject({ status: 500 });
    expect((failure as { code?: unknown }).code).toBeUndefined();
  });
});

describe('session client — the upload is multipart', () => {
  it('serializes the File into a FormData part named `file`', async () => {
    const post = vi.spyOn(sessionClient, 'POST').mockResolvedValue({
      data: { workbook_id: 'a'.repeat(64), filename: 'plan.xlsx', size_bytes: 3 },
      error: undefined,
      response: { status: 200, headers: new Headers() },
    } as never);
    const file = new File(['abc'], 'plan.xlsx');

    const stored = await uploadSessionWorkbook(file);

    expect(stored.workbook_id).toBe('a'.repeat(64));
    const options = post.mock.calls[0]?.[1] as unknown as {
      body: { file: string };
      bodySerializer: (body: { file: string }) => FormData;
    };
    const form = options.bodySerializer(options.body);
    expect(form).toBeInstanceOf(FormData);
    expect(form.get('file')).toBe(file);
    // Non-emptiness: a serializer that appended nothing would satisfy an
    // `instanceof` check alone.
    expect([...form.keys()]).toStrictEqual(['file']);
  });
});
