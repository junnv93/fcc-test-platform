/**
 * A throttled ROTATION must not sign the operator out (2026-08-23).
 *
 * ⚠️ The platform gained a per-subject refresh-rotation budget whose denial is a
 * 429 carrying `Retry-After`. Adversarial review measured what the shipped client
 * did with it: `attemptRefresh` caught every error, returned `false`, and
 * `performSilentRefresh` called `signOut('refresh_failed')` — so the FIRST 429
 * signed the tester out. That is precisely the failure the server-side budget was
 * sized to avoid ("signed out mid-measurement"), delivered by the client instead.
 *
 * The sibling file `local-login-throttled.test.ts` covers the LOGIN 429 only; it
 * never touches refresh. This file owns the rotation axis.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LocalLoginError } from '@/auth/local-login';
import { STORAGE_KEY_AUTH_STRATEGY } from '@/auth/storage-keys';

import type * as LocalLoginModule from '@/auth/local-login';

const localRefresh = vi.hoisted(() => vi.fn());

vi.mock('@/auth/local-login', async () => {
  const actual = await vi.importActual<typeof LocalLoginModule>('@/auth/local-login');
  return { ...actual, localRefresh };
});

function jwt(payload: Record<string, unknown>): string {
  const body = btoa(JSON.stringify(payload)).replace(/=+$/, '');
  return `e30.${body}.sig`;
}

function tokenSet(issuedAt: number, suffix = '') {
  return {
    accessToken: jwt({ sub: `tester@x.com${suffix}`, permissions: [] }),
    refreshToken: `r.e.f${suffix}`,
    idToken: null,
    expiresIn: 900,
    issuedAt,
    scope: null,
    tokenType: 'Bearer',
  } as never;
}

async function loadSession() {
  vi.resetModules();
  return import('@/auth/session');
}

beforeEach(() => {
  vi.useFakeTimers();
  localRefresh.mockReset();
  window.sessionStorage.clear();
  window.localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('a throttled rotation', () => {
  it('does not sign the operator out', async () => {
    const session = await loadSession();
    window.sessionStorage.setItem(STORAGE_KEY_AUTH_STRATEGY, 'local');
    session.applyTokenSet(tokenSet(Date.now()));

    localRefresh.mockRejectedValue(
      new LocalLoginError('throttled', 'too many refresh rotations', 60),
    );

    // Fire the scheduled silent refresh.
    await vi.advanceTimersByTimeAsync(880_000);

    expect(localRefresh).toHaveBeenCalledTimes(1);
    expect(session.getAuthState().kind).not.toBe('unauthenticated');
  });

  it('comes back after the window the server named', async () => {
    const session = await loadSession();
    window.sessionStorage.setItem(STORAGE_KEY_AUTH_STRATEGY, 'local');
    session.applyTokenSet(tokenSet(Date.now()));

    localRefresh.mockRejectedValueOnce(
      new LocalLoginError('throttled', 'too many refresh rotations', 60),
    );
    // ⚠️ 호출 시점에 발급해야 한다 — 값으로 미리 만들면 그 토큰은 타이머를
    // 앞으로 돌린 시점 기준으로 이미 만료돼 있어, 성공 직후 또 한 번의 회전이
    // 즉시 예약된다(그러면 이 단언이 세는 것은 재시도가 아니라 그 부작용이다).
    localRefresh.mockImplementationOnce(() => tokenSet(Date.now(), '2'));

    await vi.advanceTimersByTimeAsync(880_000);
    expect(localRefresh).toHaveBeenCalledTimes(1);
    expect(session.getAuthState().kind).not.toBe('unauthenticated');

    // ⚠️ The retry must be scheduled from the HINT (60 s), not from the token
    // lifetime — otherwise the session simply dies quietly a window later.
    await vi.advanceTimersByTimeAsync(61_000);
    expect(localRefresh).toHaveBeenCalledTimes(2);
    expect(session.getAuthState().kind).not.toBe('unauthenticated');
  });

  it('does not sign out on the 401-interceptor path either', async () => {
    // ⚠️ **두 문이 같은 서버 동작을 본다.** 첫 판은 예약된 리프레시만 고쳤고,
    // 평범한 API 호출이 401 을 받는 경로(`auth-middleware`)는 여전히 429 에
    // `signOut('token_expired')` 를 불렀다 — 즉 서버 예산이 막으려던 실패가
    // **다른 문으로** 그대로 살아 있었다(적대 평가 3R 실측). 게다가 그 문은
    // 남의 리프레시 토큰을 쥔 공격자가 **원할 때** 열 수 있다.
    const session = await loadSession();
    window.sessionStorage.setItem(STORAGE_KEY_AUTH_STRATEGY, 'local');
    session.applyTokenSet(tokenSet(Date.now()));

    localRefresh.mockRejectedValue(
      new LocalLoginError('throttled', 'too many refresh rotations', 60),
    );

    const refreshed = await session.refreshAuthSession();
    expect(refreshed).toBe(false);
    expect(session.lastRefreshWasThrottled()).toBe(true);
    expect(session.getAuthState().kind).not.toBe('unauthenticated');
  });

  it('waits the throttle window, not one second, when Retry-After is absent', async () => {
    // ⚠️ 게이트웨이의 nginx `limit_req` 는 `Retry-After` 없는 평문 HTML 429 를
    // 돌려준다 — 즉 «힌트 없는 429» 가 예외가 아니라 **정상 모양**이다. 옛
    // 폴백은 1초였고, 실측 결과 분당 60회 재시도 루프가 됐다(상류가 이미
    // 과부하로 흘리고 있는 바로 그 순간에).
    const session = await loadSession();
    window.sessionStorage.setItem(STORAGE_KEY_AUTH_STRATEGY, 'local');
    session.applyTokenSet(tokenSet(Date.now()));

    localRefresh.mockRejectedValue(new LocalLoginError('throttled', 'too many refresh rotations'));

    await vi.advanceTimersByTimeAsync(880_000);
    expect(localRefresh).toHaveBeenCalledTimes(1);

    // 1초 뒤에는 아직 재시도하지 않는다.
    await vi.advanceTimersByTimeAsync(2_000);
    expect(localRefresh).toHaveBeenCalledTimes(1);

    // 창(60초)이 지나면 한 번 더.
    await vi.advanceTimersByTimeAsync(60_000);
    expect(localRefresh).toHaveBeenCalledTimes(2);
  });

  it('gives up after a bounded number of consecutive throttles', async () => {
    // ⚠️ «절대 로그아웃하지 않는다» 는 «첫 429 에 로그아웃한다» 의 반대가 아니라
    // 또 다른 결함이다. 영원히 429 를 주는 상대에게 영원히 재시도하지 않는다.
    const session = await loadSession();
    window.sessionStorage.setItem(STORAGE_KEY_AUTH_STRATEGY, 'local');
    session.applyTokenSet(tokenSet(Date.now()));

    localRefresh.mockRejectedValue(
      new LocalLoginError('throttled', 'too many refresh rotations', 60),
    );

    await vi.advanceTimersByTimeAsync(880_000);
    for (let i = 0; i < 10; i += 1) {
      await vi.advanceTimersByTimeAsync(61_000);
    }
    expect(session.getAuthState().kind).toBe('unauthenticated');
  });

  it('does not knock again while the window is still open', async () => {
    // ⚠️ **재예약 상한은 «타이머» 만 묶는다.** 실측: 상한을 소진한 뒤에도 평범한 API
    // 호출 30건이 401 을 받자 리프레시 POST 가 **30건 더** 나갔다 — 자기가 만든
    // 폭주가 시계가 아니라 애플리케이션의 요청 속도에 맞춰 돌아왔고, 겨냥한 곳은
    // 하필 지금 우리를 제한하고 있는 그 엔드포인트다.
    const session = await loadSession();
    window.sessionStorage.setItem(STORAGE_KEY_AUTH_STRATEGY, 'local');
    session.applyTokenSet(tokenSet(Date.now()));

    localRefresh.mockRejectedValue(
      new LocalLoginError('throttled', 'too many refresh rotations', 60),
    );

    await vi.advanceTimersByTimeAsync(880_000);
    const afterFirst = localRefresh.mock.calls.length;

    for (let i = 0; i < 30; i += 1) {
      await session.refreshAuthSession();
    }
    expect(localRefresh).toHaveBeenCalledTimes(afterFirst);
    expect(session.lastRefreshWasThrottled()).toBe(true);
    expect(session.getAuthState().kind).not.toBe('unauthenticated');
  });

  it('does not carry the throttle count into the next session', async () => {
    // ⚠️ 실측: 카운터가 signOut 을 넘어 살아남아, 같은 탭에서 새로 로그인한
    // 시험원이 **자기 첫 429** 에 로그아웃됐다.
    const session = await loadSession();
    window.sessionStorage.setItem(STORAGE_KEY_AUTH_STRATEGY, 'local');
    session.applyTokenSet(tokenSet(Date.now()));

    localRefresh.mockRejectedValue(
      new LocalLoginError('throttled', 'too many refresh rotations', 60),
    );
    await vi.advanceTimersByTimeAsync(880_000);
    for (let i = 0; i < 10; i += 1) {
      await vi.advanceTimersByTimeAsync(61_000);
    }
    expect(session.getAuthState().kind).toBe('unauthenticated');

    // 새 시험원이 같은 탭에서 로그인한다.
    window.sessionStorage.setItem(STORAGE_KEY_AUTH_STRATEGY, 'local');
    session.applyTokenSet(tokenSet(Date.now(), '9'));
    localRefresh.mockClear();

    await vi.advanceTimersByTimeAsync(880_000);
    expect(localRefresh).toHaveBeenCalledTimes(1);
    // 첫 429 에 죽지 않는다 — 카운터가 초기화됐기 때문이다.
    expect(session.getAuthState().kind).not.toBe('unauthenticated');
  });

  it('still signs out when the failure is NOT a throttle', async () => {
    // ⚠️ Counterfactual — without it the first assertion passes on a client that
    // simply never signs out, which would be a different (worse) defect.
    const session = await loadSession();
    window.sessionStorage.setItem(STORAGE_KEY_AUTH_STRATEGY, 'local');
    session.applyTokenSet(tokenSet(Date.now()));

    localRefresh.mockRejectedValue(new LocalLoginError('invalid_credentials', 'nope'));

    await vi.advanceTimersByTimeAsync(880_000);

    expect(session.getAuthState().kind).toBe('unauthenticated');
  });
});
