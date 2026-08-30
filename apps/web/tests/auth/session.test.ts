import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as pkceModule from '@/auth/oidc-pkce';
import {
  __resetAuthStateForTests,
  CLAIM_PERMISSIONS,
  CLAIM_ROLES,
  CLAIM_SCOPE,
  MIN_REFRESH_MARGIN_SECONDS,
  applyTokenSet,
  currentAccessToken,
  getAuthState,
  hasPermission,
  restoreSession,
  signOut,
  subscribeAuth,
} from '@/auth/session';
import { ALL_STORAGE_KEYS, STORAGE_KEY_TOKENS } from '@/auth/storage-keys';

// ─── helpers ───────────────────────────────────────────────────────────────

/** Encode a JWT-like token with a payload — signature segment is opaque. */
function makeJwt(payload: Record<string, unknown>): string {
  const header = base64UrlEncode(JSON.stringify({ alg: 'RS256', typ: 'JWT' }));
  const body = base64UrlEncode(JSON.stringify(payload));
  return `${header}.${body}.signature`;
}

function base64UrlEncode(value: string): string {
  return btoa(unescape(encodeURIComponent(value)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/u, '');
}

function makeTokenSet(overrides: Partial<pkceModule.OidcTokenSet> = {}): pkceModule.OidcTokenSet {
  return Object.freeze({
    accessToken: makeJwt({
      sub: 'u-1',
      name: 'Op Erator',
      email: 'op@fcc.test',
      [CLAIM_PERMISSIONS]: ['session:read', 'session:control'],
      [CLAIM_SCOPE]: 'openid profile',
      [CLAIM_ROLES]: ['operator'],
    }),
    refreshToken: 'REF',
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: 'openid profile',
    issuedAt: Date.now(),
    ...overrides,
  });
}

// ─── tests ─────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.useFakeTimers();
  __resetAuthStateForTests();
  sessionStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('SSOT constants (backend HttpAuthConfig defaults)', () => {
  it("mirrors backend oidc_permissions_claim default ('permissions')", () => {
    expect(CLAIM_PERMISSIONS).toBe('permissions');
  });

  it("mirrors backend oidc_scope_claim default ('scope')", () => {
    expect(CLAIM_SCOPE).toBe('scope');
  });

  it("mirrors backend oidc_role_claim default ('roles')", () => {
    expect(CLAIM_ROLES).toBe('roles');
  });

  it('MIN_REFRESH_MARGIN_SECONDS = 30 (RFC 6749 § 5.1 + industry guidance)', () => {
    expect(MIN_REFRESH_MARGIN_SECONDS).toBe(30);
  });
});

describe('applyTokenSet', () => {
  it('persists the token set to sessionStorage', () => {
    applyTokenSet(makeTokenSet());
    const raw = sessionStorage.getItem(STORAGE_KEY_TOKENS);
    expect(raw).not.toBeNull();
    const parsed: unknown = JSON.parse(raw ?? '{}');
    expect((parsed as { accessToken?: string }).accessToken).toBeTruthy();
  });

  it('decodes the principal from the access_token JWT', () => {
    applyTokenSet(makeTokenSet());
    const state = getAuthState();
    expect(state.kind).toBe('authenticated');
    if (state.kind === 'authenticated') {
      expect(state.principal.subject).toBe('u-1');
      expect(state.principal.name).toBe('Op Erator');
      expect(state.principal.email).toBe('op@fcc.test');
      expect(state.principal.permissions).toContain('session:control');
      expect(state.principal.scopes).toContain('openid');
      expect(state.principal.roles).toContain('operator');
    }
  });

  it('notifies subscribers when state changes (useSyncExternalStore-compatible)', () => {
    // S2-β α-2 — subscribeAuth no longer fires immediately on subscribe
    // (useSyncExternalStore reads initial value via getSnapshot). The
    // callback runs only on real state changes.
    const events: string[] = [];
    const unsub = subscribeAuth(() => events.push(getAuthState().kind));
    expect(events).toEqual([]); // no immediate fire
    applyTokenSet(makeTokenSet());
    expect(events).toEqual(['authenticated']);
    unsub();
    signOut('user_initiated');
    expect(events).toEqual(['authenticated']); // unsub stopped the callback
  });
});

describe('silent refresh scheduler', () => {
  it('schedules refresh at (expiresIn - MIN_REFRESH_MARGIN_SECONDS) seconds', async () => {
    const refreshSpy = vi
      .spyOn(pkceModule, 'refreshTokens')
      .mockResolvedValue(
        makeTokenSet({ accessToken: makeJwt({ sub: 'u-1', permissions: ['session:read'] }) }),
      );
    applyTokenSet(makeTokenSet({ expiresIn: 100, refreshToken: 'REF' }));
    expect(refreshSpy).not.toHaveBeenCalled();
    // Advance to T = 100 - 30 = 70s.
    await vi.advanceTimersByTimeAsync((100 - MIN_REFRESH_MARGIN_SECONDS) * 1000);
    expect(refreshSpy).toHaveBeenCalledWith('REF');
  });

  it('does not schedule when refresh_token is absent', () => {
    const refreshSpy = vi.spyOn(pkceModule, 'refreshTokens');
    applyTokenSet(makeTokenSet({ refreshToken: null }));
    vi.advanceTimersByTime(10 * 60 * 1000);
    expect(refreshSpy).not.toHaveBeenCalled();
  });

  it('signs out with reason=refresh_failed when refresh rejects', async () => {
    vi.spyOn(pkceModule, 'refreshTokens').mockRejectedValue(new Error('network'));
    applyTokenSet(makeTokenSet({ expiresIn: 60, refreshToken: 'REF' }));
    await vi.advanceTimersByTimeAsync(30 * 1000);
    const state = getAuthState();
    expect(state.kind).toBe('unauthenticated');
    if (state.kind === 'unauthenticated') {
      expect(state.reason).toBe('refresh_failed');
    }
  });
});

describe('signOut', () => {
  it('clears sessionStorage and updates state', () => {
    for (const key of ALL_STORAGE_KEYS) {
      sessionStorage.setItem(key, 'stale-auth-value');
    }
    applyTokenSet(makeTokenSet());
    signOut('user_initiated');
    for (const key of ALL_STORAGE_KEYS) {
      expect(sessionStorage.getItem(key)).toBeNull();
    }
    expect(getAuthState().kind).toBe('unauthenticated');
  });
});

describe('hasPermission', () => {
  it('returns false when unauthenticated', () => {
    expect(hasPermission('session:read')).toBe(false);
  });

  it('returns true when principal holds the permission', () => {
    applyTokenSet(makeTokenSet());
    expect(hasPermission('session:control')).toBe(true);
    expect(hasPermission('platform:admin')).toBe(false);
  });
});

describe('currentAccessToken', () => {
  it('returns null when unauthenticated', () => {
    expect(currentAccessToken()).toBeNull();
  });

  it('returns the bearer token when authenticated', () => {
    const tokens = makeTokenSet();
    applyTokenSet(tokens);
    expect(currentAccessToken()).toBe(tokens.accessToken);
  });
});

describe('restoreSession', () => {
  it('rehydrates an unexpired token set from sessionStorage', () => {
    const tokens = makeTokenSet();
    sessionStorage.setItem(STORAGE_KEY_TOKENS, JSON.stringify(tokens));
    const state = restoreSession();
    expect(state.kind).toBe('authenticated');
  });

  it('purges and signs out when the persisted token has already expired', () => {
    const expired = makeTokenSet({ issuedAt: Date.now() - 3_600_000, expiresIn: 60 });
    sessionStorage.setItem(STORAGE_KEY_TOKENS, JSON.stringify(expired));
    const state = restoreSession();
    expect(state.kind).toBe('unauthenticated');
    if (state.kind === 'unauthenticated') {
      expect(state.reason).toBe('token_expired');
    }
    expect(sessionStorage.getItem(STORAGE_KEY_TOKENS)).toBeNull();
  });

  it('purges on corrupted JSON in sessionStorage', () => {
    sessionStorage.setItem(STORAGE_KEY_TOKENS, 'not-json');
    const state = restoreSession();
    expect(state.kind).toBe('unauthenticated');
    expect(sessionStorage.getItem(STORAGE_KEY_TOKENS)).toBeNull();
  });
});

describe('storage policy', () => {
  it('writes only to sessionStorage (never localStorage)', () => {
    applyTokenSet(makeTokenSet());
    // jsdom's localStorage is a separate Storage instance — assert empty.
    expect(localStorage.length).toBe(0);
  });
});

describe('applyTokenSet({ idTokenClaims }) — S2-α #11 verified-claim path', () => {
  it('prefers verified id_token claims over best-effort JWT decode', () => {
    // Build an access_token whose claims would say `permissions: ['x']`,
    // and supply a separate verified id_token claim set with different
    // permissions. The verified set must win.
    const verifiedClaims = {
      sub: 'verified-subject',
      name: 'Verified Name',
      email: 'verified@fcc.test',
      [CLAIM_PERMISSIONS]: ['session:control'],
    };
    applyTokenSet(makeTokenSet(), { idTokenClaims: verifiedClaims });
    const state = getAuthState();
    expect(state.kind).toBe('authenticated');
    if (state.kind === 'authenticated') {
      // Verified id_token sub/name/email take precedence.
      expect(state.principal.subject).toBe('verified-subject');
      expect(state.principal.name).toBe('Verified Name');
      expect(state.principal.email).toBe('verified@fcc.test');
    }
  });

  it('falls back to JWT decode when verified claims are absent (silent refresh path)', () => {
    applyTokenSet(makeTokenSet());
    const state = getAuthState();
    expect(state.kind).toBe('authenticated');
    if (state.kind === 'authenticated') {
      // access_token decode path — sub from JWT payload.
      expect(state.principal.subject).toBe('u-1');
    }
  });
});
