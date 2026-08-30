import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { OIDC_STORAGE_PREFIX } from '@/auth/oidc-pkce';
import * as pkceModule from '@/auth/oidc-pkce';
import { __resetAuthStateForTests, refreshAuthSession, signOut } from '@/auth/session';

/**
 * S5 — refresh single-flight seal (fe-data-layer-robustness M4/D4, 2026-07-19).
 *
 * **The defect this file reproduces.** `refreshAuthSession` is called from
 * `authRetryMiddleware.onResponse`, which runs *per request*. A dashboard mounts
 * several queries at once, so an access_token that expires while the tab is
 * backgrounded produces N simultaneous 401s and — before M4 — N concurrent
 * `POST /token` exchanges presenting the SAME refresh_token. Under refresh-token
 * rotation (Keycloak's default; mandatory for public SPA clients per OAuth 2.1)
 * the IdP invalidates a refresh_token on first use, so exactly one exchange
 * succeeds and the rest return `invalid_grant` → the middleware calls
 * `signOut('token_expired')` and the operator is thrown out mid-run. The failure
 * scales with how live the UI is, which is exactly backwards.
 *
 * The seal counts calls to the token endpoint primitive (`refreshTokens`), the
 * only observable that distinguishes coalesced from duplicated refreshes.
 */

const STORAGE_KEY_TOKENS = `${OIDC_STORAGE_PREFIX}:tokens`;

function seedStoredTokens(refreshToken = 'rt-1'): void {
  globalThis.sessionStorage.setItem(
    STORAGE_KEY_TOKENS,
    JSON.stringify({
      accessToken: 'at-expired',
      refreshToken,
      idToken: null,
      tokenType: 'Bearer',
      scope: 'openid',
      expiresIn: 300,
      issuedAt: Date.now() - 400_000,
    }),
  );
}

/** A token set with `idToken: null` so `attemptRefresh` skips discovery +
 *  id_token re-verification — this seal is about call COUNT, not verification. */
function freshTokenSet(): pkceModule.OidcTokenSet {
  return Object.freeze({
    accessToken: 'at-fresh',
    refreshToken: 'rt-2',
    idToken: null,
    tokenType: 'Bearer',
    scope: 'openid',
    expiresIn: 300,
    issuedAt: Date.now(),
  });
}

/** A promise plus its resolver, so several callers can pile up while the token
 *  exchange is deliberately still in flight (the real-world race). */
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

beforeEach(() => {
  globalThis.sessionStorage.clear();
  __resetAuthStateForTests();
});

afterEach(() => {
  vi.restoreAllMocks();
  globalThis.sessionStorage.clear();
  __resetAuthStateForTests();
});

describe('S5 — concurrent 401s coalesce onto ONE token exchange', () => {
  it('performs exactly one refresh for three simultaneous callers', async () => {
    seedStoredTokens();
    const gate = deferred<pkceModule.OidcTokenSet>();
    const spy = vi.spyOn(pkceModule, 'refreshTokens').mockImplementation(() => gate.promise);

    // Three 401s land before the token endpoint has answered — the exact
    // interleaving the rotation failure needs.
    const calls = [refreshAuthSession(), refreshAuthSession(), refreshAuthSession()];
    expect(spy).toHaveBeenCalledTimes(1);

    gate.resolve(freshTokenSet());
    await expect(Promise.all(calls)).resolves.toEqual([true, true, true]);
    expect(spy).toHaveBeenCalledTimes(1);
    // All three callers presented the SAME refresh_token exactly once.
    expect(spy).toHaveBeenCalledWith('rt-1');
  });

  it('releases the slot so a LATER refresh is not blocked by the finished one', async () => {
    seedStoredTokens();
    const spy = vi.spyOn(pkceModule, 'refreshTokens').mockResolvedValue(freshTokenSet());

    await refreshAuthSession();
    // `applyTokenSet` persisted the rotated token; a later expiry must be able
    // to refresh again rather than replay a settled promise forever.
    await refreshAuthSession();
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it('releases the slot after a FAILED refresh (no permanent wedge)', async () => {
    seedStoredTokens();
    const spy = vi
      .spyOn(pkceModule, 'refreshTokens')
      .mockRejectedValueOnce(new Error('invalid_grant'))
      .mockResolvedValueOnce(freshTokenSet());

    await expect(refreshAuthSession()).resolves.toBe(false);
    // The stored token is untouched by a failed refresh, so a retry is possible.
    await expect(refreshAuthSession()).resolves.toBe(true);
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it('a failing exchange fails ALL coalesced callers identically', async () => {
    seedStoredTokens();
    vi.spyOn(pkceModule, 'refreshTokens').mockRejectedValue(new Error('invalid_grant'));
    const results = await Promise.all([refreshAuthSession(), refreshAuthSession()]);
    expect(results).toEqual([false, false]);
  });

  it('sign-out abandons the in-flight slot (no session resurrection)', async () => {
    seedStoredTokens();
    const gate = deferred<pkceModule.OidcTokenSet>();
    const spy = vi
      .spyOn(pkceModule, 'refreshTokens')
      .mockImplementationOnce(() => gate.promise)
      .mockResolvedValue(freshTokenSet());

    const inFlight = refreshAuthSession();
    signOut('user_initiated');
    gate.resolve(freshTokenSet());
    // The in-flight refresh must NOT report success for an ended session — its
    // result is discarded rather than re-applied (session-epoch guard).
    await expect(inFlight).resolves.toBe(false);

    // Post-sign-out the stored tokens are gone, so a new caller short-circuits
    // at the storage read — it must NOT be handed the pre-sign-out promise.
    await expect(refreshAuthSession()).resolves.toBe(false);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('returns false without touching the token endpoint when no refresh_token exists', async () => {
    const spy = vi.spyOn(pkceModule, 'refreshTokens');
    await expect(refreshAuthSession()).resolves.toBe(false);
    expect(spy).not.toHaveBeenCalled();
  });
});
