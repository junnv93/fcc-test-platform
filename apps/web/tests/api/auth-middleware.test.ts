import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { authRetryMiddleware } from '@/api/auth-middleware';
import { authorizationHeader } from '@/auth/route-guard';
import { lastRefreshWasThrottled, refreshAuthSession, signOut } from '@/auth/session';

/**
 * Increment 6 — 401 retry-once interceptor (fe-multitab-auth-robustness).
 *
 * The shared openapi-fetch middleware attaches the bearer token on every
 * request and, on a 401, performs a single silent refresh + replay with the
 * fresh token. A failed refresh signs out with `token_expired`. The replay is
 * capped at one attempt (loop guard).
 *
 * `vi.mock` calls are hoisted above the imports by vitest, so the imported
 * `refreshAuthSession` / `signOut` / `authorizationHeader` resolve to the mocks.
 */

vi.mock('@/auth/session', () => ({
  refreshAuthSession: vi.fn(),
  // ⚠️ A throttled refresh is not an expired session — the middleware now asks.
  // Defaulting to `false` keeps every pre-existing case meaning what it meant.
  lastRefreshWasThrottled: vi.fn(() => false),
  signOut: vi.fn(),
}));
vi.mock('@/auth/route-guard', () => ({
  authorizationHeader: vi.fn((): string | null => 'Bearer NEW'),
}));

// openapi-fetch's middleware callbacks take a rich options object; the
// interceptor only reads `request` / `response`, so build a minimal arg and
// cast through `unknown` (no `any`).
type OnRequestArg = Parameters<NonNullable<typeof authRetryMiddleware.onRequest>>[0];
type OnResponseArg = Parameters<NonNullable<typeof authRetryMiddleware.onResponse>>[0];

function reqArg(request: Request): OnRequestArg {
  return { request } as unknown as OnRequestArg;
}
function resArg(request: Request, response: Response): OnResponseArg {
  return { request, response } as unknown as OnResponseArg;
}

function runOnRequest(request: Request): Request {
  const onRequest = authRetryMiddleware.onRequest;
  if (!onRequest) throw new Error('onRequest middleware missing');
  const result = onRequest(reqArg(request));
  // The interceptor always returns the (mutated) request synchronously.
  return result as Request;
}

async function runOnResponse(request: Request, response: Response): Promise<Response> {
  const onResponse = authRetryMiddleware.onResponse;
  if (!onResponse) throw new Error('onResponse middleware missing');
  const result = await onResponse(resArg(request, response));
  return result ?? response;
}

const fetchMock = vi.fn();

beforeEach(() => {
  vi.mocked(refreshAuthSession).mockReset();
  vi.mocked(signOut).mockReset();
  vi.mocked(authorizationHeader).mockReset();
  vi.mocked(authorizationHeader).mockReturnValue('Bearer NEW');
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response(null, { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('authRetryMiddleware.onRequest', () => {
  it('attaches the current bearer token', () => {
    const request = new Request('http://api.test/x', { method: 'GET' });
    runOnRequest(request);
    expect(request.headers.get('Authorization')).toBe('Bearer NEW');
  });

  it('omits the header when unauthenticated', () => {
    vi.mocked(authorizationHeader).mockReturnValue(null);
    const request = new Request('http://api.test/x', { method: 'GET' });
    runOnRequest(request);
    expect(request.headers.get('Authorization')).toBeNull();
  });
});

describe('authRetryMiddleware.onResponse — 401 retry-once', () => {
  it('passes through a non-401 response without refreshing', async () => {
    const request = new Request('http://api.test/x', { method: 'GET' });
    runOnRequest(request);
    const ok = new Response(null, { status: 200 });
    const out = await runOnResponse(request, ok);
    expect(out.status).toBe(200);
    expect(refreshAuthSession).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('on 401 refreshes once and replays with the fresh token', async () => {
    vi.mocked(refreshAuthSession).mockResolvedValue(true);
    const request = new Request('http://api.test/x', { method: 'GET' });
    runOnRequest(request);

    const out = await runOnResponse(request, new Response(null, { status: 401 }));

    expect(refreshAuthSession).toHaveBeenCalledTimes(1);
    expect(signOut).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const replayed = fetchMock.mock.calls[0]?.[0] as Request;
    expect(replayed.headers.get('Authorization')).toBe('Bearer NEW');
    // Replay carries the loop-guard flag so it can never itself be retried.
    expect(replayed.headers.get('X-Auth-Retry')).toBe('1');
    expect(out.status).toBe(200); // the replay's response
  });

  it('signs out with token_expired when the refresh fails', async () => {
    vi.mocked(refreshAuthSession).mockResolvedValue(false);
    const request = new Request('http://api.test/x', { method: 'GET' });
    runOnRequest(request);

    const out = await runOnResponse(request, new Response(null, { status: 401 }));

    expect(refreshAuthSession).toHaveBeenCalledTimes(1);
    expect(signOut).toHaveBeenCalledWith('token_expired');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(out.status).toBe(401); // original 401 surfaced
  });

  it('keeps the session when the refresh was THROTTLED, not expired', async () => {
    // ⚠️ 이 문이 3R 에서 살아 있었다. 예약된 리프레시 경로는 429 에 재예약하도록
    // 고쳐졌는데, 평범한 API 호출이 401 을 받는 **이 경로**는 여전히 로그아웃을
    // 불렀다 — 그리고 이쪽은 남의 리프레시 토큰을 쥔 공격자가 원할 때 열 수 있다.
    vi.mocked(refreshAuthSession).mockResolvedValue(false);
    vi.mocked(lastRefreshWasThrottled).mockReturnValue(true);
    const request = new Request('http://api.test/x', { method: 'GET' });
    runOnRequest(request);

    const out = await runOnResponse(request, new Response(null, { status: 401 }));

    expect(refreshAuthSession).toHaveBeenCalledTimes(1);
    expect(signOut).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(out.status).toBe(401); // the 401 surfaces; the session survives
  });

  it('loop guard — a request already flagged as a replay is not retried', async () => {
    const request = new Request('http://api.test/x', { method: 'GET' });
    request.headers.set('X-Auth-Retry', '1'); // simulate an in-flight replay
    runOnRequest(request);

    const out = await runOnResponse(request, new Response(null, { status: 401 }));

    // No clone was stashed for a replay → no second refresh / fetch.
    expect(refreshAuthSession).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(out.status).toBe(401);
  });

  it('preserves a POST body on the replay', async () => {
    vi.mocked(refreshAuthSession).mockResolvedValue(true);
    const request = new Request('http://api.test/x', {
      method: 'POST',
      body: JSON.stringify({ hello: 'world' }),
      headers: { 'Content-Type': 'application/json' },
    });
    runOnRequest(request);

    await runOnResponse(request, new Response(null, { status: 401 }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const replayed = fetchMock.mock.calls[0]?.[0] as Request;
    const body = await replayed.text();
    expect(JSON.parse(body)).toEqual({ hello: 'world' });
  });
});
