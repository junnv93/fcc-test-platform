/**
 * Local login — the second auth strategy (identity axis, 2026-08-21).
 *
 * ⚠️ The rules asserted here are security rules, not conveniences: an open-redirect
 * guard, a forced-password-change destination that cannot be skipped, and a
 * refresh path that must not send a locally-issued token to the IdP.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  LOCAL_PASSWORD_CHANGE_ROUTE,
  LocalLoginError,
  localLogin,
  localLogout,
  localRefresh,
  resolveLoginDestination,
  toTokenSet,
} from '@/auth/local-login';
import { __resetAuthStateForTests, applyTokenSet, currentAuthStrategy } from '@/auth/session';
import { STORAGE_KEY_AUTH_STRATEGY } from '@/auth/storage-keys';

const ENVELOPE = {
  access_token: 'access-token-value',
  refresh_token: 'refresh-token-value',
  token_type: 'Bearer',
  expires_in: 900,
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('resolveLoginDestination', () => {
  it('sends a pending password change to its dedicated route', () => {
    expect(resolveLoginDestination(true, '/sessions')).toBe(LOCAL_PASSWORD_CHANGE_ROUTE);
  });

  it('lets a pending password change override even a valid returnTo', () => {
    // ⚠️ If returnTo could win, a bootstrap administrator would skip the one screen
    // that retires the password sitting in the deploy log.
    expect(resolveLoginDestination(true, '/projects?x=1')).toBe(LOCAL_PASSWORD_CHANGE_ROUTE);
  });

  it('honours a same-document returnTo', () => {
    expect(resolveLoginDestination(false, '/sessions?a=b#c')).toBe('/sessions?a=b#c');
  });

  it.each([
    ['absolute http', 'http://evil.example/steal'],
    ['absolute https', 'https://evil.example/steal'],
    ['protocol-relative', '//evil.example/steal'],
    ['javascript scheme', 'javascript:alert(1)'],
    ['relative without slash', 'sessions'],
    ['not a string', 42],
    ['null', null],
    ['undefined', undefined],
  ])('refuses an off-origin returnTo (%s)', (_label, candidate) => {
    // ⚠️ Trusting returnTo would make /login an open redirect: a crafted link could
    // bounce a just-authenticated operator, bearer token in hand, off-origin.
    expect(resolveLoginDestination(false, candidate)).toBe('/');
  });
});

describe('localLogin', () => {
  it('returns a token set and the server force-change verdict', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(200, { ...ENVELOPE, force_password_change: true })),
    );
    const result = await localLogin('a@x.com', 'correcthorse', () => 1000);
    expect(result.tokens.accessToken).toBe('access-token-value');
    expect(result.tokens.idToken).toBeNull();
    expect(result.tokens.issuedAt).toBe(1000);
    expect(result.forcePasswordChange).toBe(true);
  });

  it('maps a 401 to invalid_credentials', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(401, { code: 'AUTH_INVALID_CREDENTIALS' })),
    );
    await expect(localLogin('a@x.com', 'nope')).rejects.toMatchObject({
      kind: 'invalid_credentials',
    });
  });

  it('maps a network failure to unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('failed to fetch')));
    await expect(localLogin('a@x.com', 'pw')).rejects.toMatchObject({
      kind: 'unreachable',
    });
  });

  it('refuses a 200 that carries no token pair', async () => {
    // ⚠️ Without this the session store would install an "authenticated" state whose
    // bearer token is `undefined`, and every later request would 401 with no hint
    // that the LOGIN response was the problem.
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true })));
    await expect(localLogin('a@x.com', 'pw')).rejects.toBeInstanceOf(LocalLoginError);
  });

  it('never puts the password in the request URL', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse(200, ENVELOPE));
    vi.stubGlobal('fetch', fetchSpy);
    await localLogin('a@x.com', 'correcthorse');
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).not.toContain('correcthorse');
    expect(url).not.toContain('a@x.com');
    expect(init.method).toBe('POST');
    expect(typeof init.body === 'string' ? init.body : JSON.stringify(init.body)).toContain(
      'correcthorse',
    );
  });
});

describe('localChangePassword failures', () => {
  it('distinguishes a new-password policy rejection from invalid credentials', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse(400, { code: 'PASSWORD_REJECTED' }));
    vi.stubGlobal('fetch', fetchSpy);
    const { localChangePassword } = await import('@/auth/local-login');
    await expect(localChangePassword('access-token', 'current', 'short')).rejects.toMatchObject({
      kind: 'password_policy',
    });
  });
});

describe('localLogout', () => {
  it('never rejects, even when the server is unreachable', async () => {
    // ⚠️ A logout that can fail leaves the user believing they signed out while
    // they did not — the worst outcome this operation has.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('down')));
    await expect(localLogout('token')).resolves.toBeUndefined();
  });

  it('presents the token as a bearer', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse(200, { logged_out: true }));
    vi.stubGlobal('fetch', fetchSpy);
    await localLogout('token-abc');
    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer token-abc');
  });

  it('sends the refresh token so logout actually ends the session', async () => {
    // ⚠️ Adversarial review 2026-08-21 measured this: revoking only the access
    // token ends nothing, because a refresh token captured off the plaintext LAN
    // keeps minting fresh sessions for its whole 7-day life — straight through a
    // sign-out. The client is the only party holding it.
    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse(200, { logged_out: true }));
    vi.stubGlobal('fetch', fetchSpy);
    await localLogout('token-abc', 'refresh-abc');
    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(typeof init.body === 'string' ? init.body : JSON.stringify(init.body)).toContain(
      'refresh-abc',
    );
  });
});

describe('resolveLoginDestination — backslash disguise', () => {
  it.each(['/\\evil.example', '/\\/evil.example', '\\\\evil.example'])(
    'refuses %s (browsers normalise backslashes to slashes)',
    (candidate) => {
      // ⚠️ Adversarial review found this gap. A slash-only check lets
      // '/\\evil.example' past, and the browser then reads it as the
      // protocol-relative '//evil.example'. Not reachable today — returnTo comes
      // from router state, not the URL — but a filter must not be relied upon
      // while it carries a hole.
      expect(resolveLoginDestination(false, candidate)).toBe('/');
    },
  );
});

describe('the persisted strategy marker', () => {
  it('defaults to oidc so pre-existing sessions are unchanged', () => {
    expect(currentAuthStrategy()).toBe('oidc');
  });

  it('records local when a local token set is applied', () => {
    applyTokenSet(toTokenSet(ENVELOPE, Date.now()), { strategy: 'local' });
    expect(currentAuthStrategy()).toBe('local');
    expect(sessionStorage.getItem(STORAGE_KEY_AUTH_STRATEGY)).toBe('local');
  });

  it('survives a reload, which is the whole reason it is persisted', () => {
    // ⚠️ In-memory only, a reload would send the silent refresh down the OIDC path
    // with a locally-issued refresh_token — failing ~15 minutes later, which reads
    // as a network fault rather than a bug.
    applyTokenSet(toTokenSet(ENVELOPE, Date.now()), { strategy: 'local' });
    __resetAuthStateForTests(); // simulates a fresh document
    expect(currentAuthStrategy()).toBe('local');
  });

  it('is cleared by signOut along with every other auth key', async () => {
    const { signOut } = await import('@/auth/session');
    applyTokenSet(toTokenSet(ENVELOPE, Date.now()), { strategy: 'local' });
    signOut('user_initiated');
    expect(sessionStorage.getItem(STORAGE_KEY_AUTH_STRATEGY)).toBeNull();
    expect(currentAuthStrategy()).toBe('oidc');
  });
});

describe('localRefresh', () => {
  it('exchanges a refresh token for a new pair', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, ENVELOPE)));
    const tokens = await localRefresh('refresh-token-value', () => 2000);
    expect(tokens.accessToken).toBe('access-token-value');
    expect(tokens.issuedAt).toBe(2000);
  });

  it('posts to the platform refresh route, not the IdP token endpoint', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse(200, ENVELOPE));
    vi.stubGlobal('fetch', fetchSpy);
    await localRefresh('refresh-token-value');
    const [url] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/platform/auth/refresh');
  });
});

describe('the local strategy is actually WIRED, not merely recorded', () => {
  // ⚠️ Adversarial review deleted each of these two branches and the whole
  // 1366-test frontend suite stayed green. The existing tests assert that the
  // strategy marker is *persisted*; nothing asserted that anything READS it.
  // Deleting either branch reintroduces the exact failure its own comment
  // describes — which is the definition of an untested integration point.

  it('refreshes a local session through the platform route, never the IdP', async () => {
    // ⚠️ Without the branch in `attemptRefresh`, a locally-issued refresh_token
    // is presented to Keycloak, rejected, and the operator is signed out about
    // fifteen minutes after a page reload — which reads as a network fault.
    const {
      __resetAuthStateForTests: reset,
      applyTokenSet: apply,
      refreshAuthSession,
    } = await import('@/auth/session');
    reset();
    sessionStorage.clear();
    apply(toTokenSet(ENVELOPE, Date.now()), { strategy: 'local' });

    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse(200, ENVELOPE));
    vi.stubGlobal('fetch', fetchSpy);
    const ok = await refreshAuthSession();

    expect(ok).toBe(true);
    expect(fetchSpy).toHaveBeenCalled();
    const urls = fetchSpy.mock.calls.map((call) => String(call[0]));
    expect(urls.some((url) => url.includes('/platform/auth/refresh'))).toBe(true);
    expect(
      urls.some((url) => url.includes('.well-known') || url.includes('/realms/')),
      `a local refresh reached the IdP: ${urls.join(', ')}`,
    ).toBe(false);
  });

  it('leaves the OIDC refresh path alone when the strategy is oidc', async () => {
    // ⚠️ 음성 단언만 있으면 *항상* 로컬로 가는 구현이 만점을 받는다.
    const {
      __resetAuthStateForTests: reset,
      applyTokenSet: apply,
      refreshAuthSession,
    } = await import('@/auth/session');
    reset();
    sessionStorage.clear();
    apply(toTokenSet(ENVELOPE, Date.now())); // no strategy → 'oidc'

    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse(200, ENVELOPE));
    vi.stubGlobal('fetch', fetchSpy);
    await refreshAuthSession();

    const urls = fetchSpy.mock.calls.map((call) => String(call[0]));
    expect(
      urls.some((url) => url.includes('/platform/auth/refresh')),
      'an OIDC session refreshed through the local route',
    ).toBe(false);
  });
});
