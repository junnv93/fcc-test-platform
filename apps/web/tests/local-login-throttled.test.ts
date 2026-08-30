/**
 * The 429 branch of the local-login client (account-axis throttle, 2026-08-22).
 *
 * ⚠️ Adversarial review found **zero** tests touching this path: the kind existed,
 * the `Retry-After` reader existed, and nothing exercised either. A 429 rendered
 * as "contact an administrator" — misdirecting the tester about a self-healing
 * 60-second condition, and, because the message offered no wait, inviting the
 * immediate retry that keeps the window pinned.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { LocalLoginError, localLogin } from '@/auth/local-login';

function respondWith(status: number, headers: Record<string, string> = {}): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Response(JSON.stringify({}), { status, headers })),
  );
}

async function failureOf(): Promise<LocalLoginError> {
  try {
    await localLogin('tester@x.com', 'nope');
  } catch (error) {
    return error as LocalLoginError;
  }
  throw new Error('expected localLogin to reject');
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('a throttled login', () => {
  it('is its own failure kind, not "unexpected"', async () => {
    respondWith(429, { 'Retry-After': '42' });
    const error = await failureOf();
    expect(error).toBeInstanceOf(LocalLoginError);
    expect(error.kind).toBe('throttled');
  });

  it('carries the wait the server sent', async () => {
    respondWith(429, { 'Retry-After': '42' });
    expect((await failureOf()).retryAfterSeconds).toBe(42);
  });

  it('leaves the wait undefined rather than NaN when the header is absent', async () => {
    respondWith(429);
    const error = await failureOf();
    expect(error.kind).toBe('throttled');
    expect(error.retryAfterSeconds).toBeUndefined();
  });

  it.each([
    ['an HTTP-date', 'Wed, 21 Oct 2026 07:28:00 GMT'],
    ['a negative value', '-5'],
    ['zero', '0'],
    ['nonsense', 'soon'],
  ])('ignores %s rather than rendering it', async (_label, raw) => {
    respondWith(429, { 'Retry-After': raw });
    expect((await failureOf()).retryAfterSeconds).toBeUndefined();
  });

  it('never carries the password', async () => {
    respondWith(429, { 'Retry-After': '42' });
    expect((await failureOf()).message).not.toContain('nope');
  });
});

describe('the other failure kinds are unchanged', () => {
  it('maps 401 to invalid_credentials', async () => {
    respondWith(401);
    expect((await failureOf()).kind).toBe('invalid_credentials');
  });

  it.each([500, 503, 418])('maps %i to unexpected', async (status) => {
    respondWith(status);
    expect((await failureOf()).kind).toBe('unexpected');
  });
});
