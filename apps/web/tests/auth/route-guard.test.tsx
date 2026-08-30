import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as localLoginModule from '@/auth/local-login';
import * as multiTabModule from '@/auth/multi-tab-sync';
import * as pkceModule from '@/auth/oidc-pkce';
import { RequireAuth, RequirePermission, SignOutButton, useAuthSession } from '@/auth/route-guard';
import {
  __resetAuthStateForTests,
  CLAIM_PERMISSIONS,
  applyTokenSet,
  getAuthState,
  signOut,
} from '@/auth/session';
import { __resetRuntimeConfigCacheForTests, getRuntimeConfig } from '@/config/runtime';

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).replace(/=+$/u, '');
  const body = btoa(JSON.stringify(payload))
    .replace(/=+$/u, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  return `${header}.${body}.sig`;
}

function authenticateAs(permissions: readonly string[]): void {
  applyTokenSet({
    accessToken: makeJwt({ sub: 'u-1', [CLAIM_PERMISSIONS]: permissions }),
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
}

function authenticateAsWithPasswordChange(permissions: readonly string[]): void {
  applyTokenSet(
    {
      accessToken: makeJwt({
        sub: 'u-1',
        [CLAIM_PERMISSIONS]: permissions,
        force_password_change: true,
      }),
      refreshToken: null,
      idToken: null,
      tokenType: 'Bearer',
      expiresIn: 600,
      scope: null,
      issuedAt: Date.now(),
    },
    { strategy: 'local' },
  );
}

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useAuthSession', () => {
  it('renders the current state and re-renders on transitions', async () => {
    function Probe(): JSX.Element {
      const state = useAuthSession();
      return <span data-testid="state">{state.kind}</span>;
    }
    render(<Probe />);
    expect(screen.getByTestId('state')).toHaveTextContent('unauthenticated');
    authenticateAs(['session:read']);
    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('authenticated'));
    signOut('user_initiated');
    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('unauthenticated'));
  });
});

describe('<RequireAuth>', () => {
  it('renders children when authenticated', () => {
    authenticateAs(['session:read']);
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route
            path="/"
            element={
              <RequireAuth>
                <div data-testid="protected">secret</div>
              </RequireAuth>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId('protected')).toBeInTheDocument();
  });

  it('redirects a forced local-password-change session away from the app shell', async () => {
    authenticateAsWithPasswordChange(['platform:read']);
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route
            path="/"
            element={
              <RequireAuth>
                <div data-testid="protected">dashboard</div>
              </RequireAuth>
            }
          />
          <Route path="/change-password" element={<div data-testid="change">change</div>} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId('change')).toBeInTheDocument());
    expect(screen.queryByTestId('protected')).not.toBeInTheDocument();
  });

  it('kicks off startLogin and shows the redirecting view when unauthenticated', async () => {
    const startSpy = vi.spyOn(pkceModule, 'startLogin').mockResolvedValue();
    render(
      <MemoryRouter initialEntries={['/sessions?foo=bar']}>
        <Routes>
          <Route
            path="/sessions"
            element={
              <RequireAuth>
                <div>secret</div>
              </RequireAuth>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText(/로그인 페이지로 이동합니다/u)).toBeInTheDocument();
    await waitFor(() => expect(startSpy).toHaveBeenCalled());
    expect(startSpy).toHaveBeenCalledWith({ returnTo: '/sessions?foo=bar' });
  });

  it('does not start a competing login after an explicit user-initiated sign-out', async () => {
    const startSpy = vi.spyOn(pkceModule, 'startLogin').mockResolvedValue();
    authenticateAs(['session:read']);
    render(
      <MemoryRouter initialEntries={['/sessions']}>
        <Routes>
          <Route
            path="/sessions"
            element={
              <RequireAuth>
                <div>secret</div>
              </RequireAuth>
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    signOut('user_initiated');

    await waitFor(() =>
      expect(screen.getByText(/로그인 페이지로 이동합니다/u)).toBeInTheDocument(),
    );
    expect(startSpy).not.toHaveBeenCalled();
  });

  it('renders the idp_unreachable failure UI when startLogin throws OidcFlowError', async () => {
    vi.spyOn(pkceModule, 'startLogin').mockRejectedValue(
      new pkceModule.OidcFlowError('discovery_unreachable', 'net'),
    );
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route
            path="/"
            element={
              <RequireAuth>
                <div>secret</div>
              </RequireAuth>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByTestId('auth-failure-idp_unreachable')).toBeInTheDocument(),
    );
  });
});

describe('<SignOutButton> — S2-α #13 forceReauth', () => {
  it('passes forceReauth=false to signOutAtIdp by default', () => {
    authenticateAs(['session:read']);
    const spy = vi.spyOn(pkceModule, 'signOutAtIdp').mockResolvedValue(false);
    render(<SignOutButton />);
    screen.getByRole('button', { name: '로그아웃' }).click();
    expect(spy).toHaveBeenCalledWith({ forceReauth: false });
  });

  it('passes forceReauth=true when the prop is set', () => {
    authenticateAs(['session:read']);
    const spy = vi.spyOn(pkceModule, 'signOutAtIdp').mockResolvedValue(false);
    render(<SignOutButton forceReauth>강제 재로그인</SignOutButton>);
    screen.getByRole('button', { name: '강제 재로그인' }).click();
    expect(spy).toHaveBeenCalledWith({ forceReauth: true });
  });

  it('broadcasts + clears locally even when the IdP logout redirect succeeds (redirected=true)', async () => {
    // Supervisor iter-2 P0 regression guard. A successful RP-Initiated Logout
    // (`redirected=true`) navigates this tab to the IdP; the prior code only
    // broadcast on the `redirected=false` / catch branches, so sibling tabs
    // stayed authenticated. The broadcast must fire UNCONDITIONALLY (before the
    // IdP round-trip), so even a redirect success notifies sibling tabs.
    authenticateAs(['session:read']);
    const idpSpy = vi.spyOn(pkceModule, 'signOutAtIdp').mockResolvedValue(true);
    const everywhereSpy = vi.spyOn(multiTabModule, 'signOutEverywhere');
    render(<SignOutButton />);
    screen.getByRole('button', { name: '로그아웃' }).click();

    // Broadcast fired synchronously on click — BEFORE the (resolved-true) IdP
    // redirect promise settles — so the cross-tab message is sent regardless.
    expect(everywhereSpy).toHaveBeenCalledWith('user_initiated');
    // Local session cleared immediately too.
    expect(getAuthState().kind).toBe('unauthenticated');
    // forceReauth still forwarded to the IdP end-session request.
    await waitFor(() => expect(idpSpy).toHaveBeenCalledWith({ forceReauth: false }));
  });
});

describe('<RequirePermission>', () => {
  it('renders children when principal holds the permission', () => {
    authenticateAs(['session:control']);
    render(
      <RequirePermission permission="session:control">
        <div data-testid="ok">ok</div>
      </RequirePermission>,
    );
    expect(screen.getByTestId('ok')).toBeInTheDocument();
  });

  it('renders permission_denied failure UI when principal lacks the permission', () => {
    authenticateAs(['session:read']);
    render(
      <RequirePermission permission="platform:admin">
        <div>nope</div>
      </RequirePermission>,
    );
    expect(screen.getByTestId('auth-failure-permission_denied')).toBeInTheDocument();
  });

  it('renders permission_denied when unauthenticated (defensive — caller forgot RequireAuth)', () => {
    render(
      <RequirePermission permission="session:read">
        <div>nope</div>
      </RequirePermission>,
    );
    expect(screen.getByTestId('auth-failure-permission_denied')).toBeInTheDocument();
  });
});

describe('RequireAuth honours authMode (identity axis, 2026-08-21)', () => {
  // ⚠️ Adversarial review deleted this branch and the entire frontend suite
  // stayed green. With it gone, a `local` deployment calls `startLogin()` and
  // throws the very `crypto.subtle` TypeError this whole axis exists to remove —
  // on the one screen an operator cannot get past.
  function withAuthMode(mode: 'oidc' | 'local'): void {
    const base = getRuntimeConfig();
    __resetRuntimeConfigCacheForTests({ ...base, authMode: mode });
  }

  afterEach(() => {
    __resetRuntimeConfigCacheForTests();
  });

  it('navigates to the in-app sign-in screen in local mode', async () => {
    withAuthMode('local');
    const startSpy = vi.spyOn(pkceModule, 'startLogin').mockResolvedValue(undefined);
    __resetAuthStateForTests();

    render(
      <MemoryRouter initialEntries={['/sessions?a=b']}>
        <Routes>
          <Route
            path="/sessions"
            element={
              <RequireAuth>
                <p>guarded</p>
              </RequireAuth>
            }
          />
          <Route path="/login" element={<p>local sign-in screen</p>} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('local sign-in screen')).toBeInTheDocument();
    });
    expect(
      startSpy,
      'local mode reached the OIDC redirect — that call throws on a plaintext host',
    ).not.toHaveBeenCalled();
  });

  it('still redirects to the IdP in oidc mode', async () => {
    // ⚠️ 음성 단언만 있으면 *항상* /login 으로 보내는 구현이 만점을 받는다.
    withAuthMode('oidc');
    const startSpy = vi.spyOn(pkceModule, 'startLogin').mockResolvedValue(undefined);
    __resetAuthStateForTests();

    render(
      <MemoryRouter initialEntries={['/sessions?a=b']}>
        <Routes>
          <Route
            path="/sessions"
            element={
              <RequireAuth>
                <p>guarded</p>
              </RequireAuth>
            }
          />
          <Route path="/login" element={<p>local sign-in screen</p>} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(startSpy).toHaveBeenCalledWith({ returnTo: '/sessions?a=b' });
    });
    expect(screen.queryByText('local sign-in screen')).not.toBeInTheDocument();
  });
});

describe('SignOutButton actually ends the SERVER session in local mode', () => {
  // ⚠️ Adversarial review measured the shipped flow: clicking sign-out in local
  // mode made exactly one network request — to the IdP's discovery endpoint —
  // and called `localLogout` ZERO times. Rotation, dual revocation and the
  // shared revocation list were all unreachable from the product; sign-out was
  // a sessionStorage wipe. This is the seal that failure needed.
  function withAuthMode(mode: 'oidc' | 'local'): void {
    const base = getRuntimeConfig();
    __resetRuntimeConfigCacheForTests({ ...base, authMode: mode });
  }

  afterEach(() => {
    __resetRuntimeConfigCacheForTests();
  });

  function authenticate(): void {
    applyTokenSet(
      {
        accessToken: makeJwt({ sub: 'u-1' }),
        refreshToken: 'refresh-token-value',
        idToken: null,
        tokenType: 'Bearer',
        expiresIn: 600,
        scope: null,
        issuedAt: Date.now(),
      },
      { strategy: 'local' },
    );
  }

  it('tells the server, sending BOTH tokens', async () => {
    withAuthMode('local');
    __resetAuthStateForTests();
    sessionStorage.clear();
    authenticate();
    const logoutSpy = vi.spyOn(localLoginModule, 'localLogout').mockResolvedValue(undefined);
    const idpSpy = vi.spyOn(pkceModule, 'signOutAtIdp').mockResolvedValue({
      redirected: false,
    } as never);

    render(
      <MemoryRouter>
        <SignOutButton>나가기</SignOutButton>
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole('button'));

    await waitFor(() => {
      expect(
        logoutSpy,
        'local sign-out never reached the server — the refresh token stays alive',
      ).toHaveBeenCalledTimes(1);
    });
    // ⚠️ Both arguments matter. Retiring only the access token ends nothing.
    const [accessArg, refreshArg] = logoutSpy.mock.calls[0] as [string, string | null];
    expect(accessArg).toBeTruthy();
    expect(refreshArg).toBe('refresh-token-value');
    expect(
      idpSpy,
      'local mode reached the IdP — that call throws on a plaintext host',
    ).not.toHaveBeenCalled();
  });

  it('captures the tokens BEFORE the local session is cleared', async () => {
    // ⚠️ `signOut` wipes sessionStorage. Reading the tokens after it would send
    // empty strings and the server would answer 200 having revoked nothing —
    // the exact "believing you signed out while you did not" outcome.
    withAuthMode('local');
    __resetAuthStateForTests();
    sessionStorage.clear();
    authenticate();
    const seen: (string | null)[] = [];
    vi.spyOn(localLoginModule, 'localLogout').mockImplementation(
      (_access: string, refresh: string | null = null) => {
        seen.push(refresh);
        return Promise.resolve();
      },
    );

    render(
      <MemoryRouter>
        <SignOutButton>나가기</SignOutButton>
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole('button'));

    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toBe('refresh-token-value');
  });

  it('still uses the IdP path in oidc mode', async () => {
    // ⚠️ 음성 단언만 있으면 *항상* 로컬로 가는 구현이 만점을 받는다.
    withAuthMode('oidc');
    __resetAuthStateForTests();
    sessionStorage.clear();
    authenticate();
    const logoutSpy = vi.spyOn(localLoginModule, 'localLogout').mockResolvedValue(undefined);
    const idpSpy = vi.spyOn(pkceModule, 'signOutAtIdp').mockResolvedValue({
      redirected: false,
    } as never);

    render(
      <MemoryRouter>
        <SignOutButton>나가기</SignOutButton>
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole('button'));

    await waitFor(() => expect(idpSpy).toHaveBeenCalledTimes(1));
    expect(logoutSpy).not.toHaveBeenCalled();
  });

  it('returns a local-auth operator to the in-app login route after sign-out', async () => {
    withAuthMode('local');
    __resetAuthStateForTests();
    sessionStorage.clear();
    authenticate();
    vi.spyOn(localLoginModule, 'localLogout').mockResolvedValue(undefined);

    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route
            path="/"
            element={
              <>
                <RequireAuth>
                  <p>dashboard</p>
                </RequireAuth>
                <SignOutButton>나가기</SignOutButton>
              </>
            }
          />
          <Route path="/login" element={<p>local sign-in screen</p>} />
        </Routes>
      </MemoryRouter>,
    );

    await userEvent.click(screen.getByRole('button', { name: '나가기' }));
    await waitFor(() => expect(screen.getByText('local sign-in screen')).toBeInTheDocument());
  });
});
