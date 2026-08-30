import { render, screen, waitFor } from '@testing-library/react';
import { RouterProvider } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { appRoutes } from '@/app';
import { __resetAuthStateForTests } from '@/auth/session';
import { t } from '@/i18n';
import { captureException } from '@/observability/sentry';

import {
  authenticateForTests,
  buildTestRouter,
  flattenRoutes as flatten,
  registeredRouteKeys,
  routeKey,
  stubRequestForDataRouterNavigation,
  ThrowingRoute,
} from './helpers/app-routes';

import type * as SentryModule from '@/observability/sentry';

/**
 * S3–S6 — the three error-boundary layers, driven through the REAL route tree.
 *
 * A broken screen must not take the shell with it, and the operator must be
 * able to leave by navigating (react-router discards a route error when
 * `location` changes — that is why the boundary belongs on the route object and
 * not in `<main>`).
 *
 * `captureException` is mocked rather than silenced: react-router keeps route
 * errors inside its own state, so "the crash is observable at all" is part of
 * what these layers owe and is asserted below.
 */
vi.mock('@/observability/sentry', async (importOriginal) => ({
  ...(await importOriginal<typeof SentryModule>()),
  captureException: vi.fn(),
}));

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
  authenticateForTests(['platform:read']);
  stubRequestForDataRouterNavigation(vi.stubGlobal);
});

afterEach(() => {
  vi.unstubAllGlobals();
  __resetAuthStateForTests();
});

describe('route registration structure', () => {
  it('gives every registered route an errorElement — all of them, not a sample', () => {
    const all = flatten(appRoutes);
    // Non-vacuity: the shell + its children are the surface this claims to cover.
    expect(all.length).toBeGreaterThanOrEqual(18);
    expect(all.filter((route) => route.errorElement === undefined).map(routeKey)).toEqual([]);
  });

  it('gives every registered route a document-title key', () => {
    expect(
      flatten(appRoutes)
        .filter((route) => route.handle.titleKey.trim() === '')
        .map(routeKey),
    ).toEqual([]);
  });

  it('registers unique route keys so the test harness can address them', () => {
    const keys = registeredRouteKeys();
    expect(new Set(keys).size).toBe(keys.length);
  });
});

describe('route-layer boundary (S3)', () => {
  it('keeps the shell mounted when one screen throws', async () => {
    const router = buildTestRouter({ jobs: <ThrowingRoute message="jobs boom" /> }, ['/jobs']);
    render(<RouterProvider router={router} />);

    await screen.findByTestId('route-error-fallback');
    // The shell survives: nav, skip link and header are still there, so the
    // operator can leave without reloading.
    expect(
      screen.getByRole('navigation', { name: t('routes.layout.navMenuToggle') }),
    ).toBeInTheDocument();
    expect(screen.getByRole('banner')).toBeInTheDocument();
    expect(screen.queryByTestId('shell-error-fallback')).not.toBeInTheDocument();
  });

  it('forwards the route error to observability', async () => {
    const router = buildTestRouter({ jobs: <ThrowingRoute message="jobs boom" /> }, ['/jobs']);
    render(<RouterProvider router={router} />);
    await screen.findByTestId('route-error-fallback');

    expect(captureException).toHaveBeenCalledWith(
      expect.objectContaining({ message: 'jobs boom' }),
      { errorBoundaryLayer: 'route' },
    );
  });
});

describe('navigation clears the error (S4)', () => {
  it('drops the fallback on a location change — no retry click needed', async () => {
    const router = buildTestRouter(
      {
        jobs: <ThrowingRoute message="jobs boom" />,
        sessions: <div data-testid="sessions-stub">sessions</div>,
      },
      ['/jobs'],
    );
    render(<RouterProvider router={router} />);
    await screen.findByTestId('route-error-fallback');

    await router.navigate('/sessions');

    await waitFor(() => expect(screen.getByTestId('sessions-stub')).toBeInTheDocument());
    expect(screen.queryByTestId('route-error-fallback')).not.toBeInTheDocument();
  });
});

describe('shell-layer boundary (S5/S6)', () => {
  const shellError = new Error('shell boom');
  const stackFrame = (shellError.stack ?? '').split('\n')[1]?.trim() ?? '';

  function ThrowingShell(): JSX.Element {
    throw shellError;
  }

  it('replaces the shell with our fallback and never prints the stack', async () => {
    // Guard against a vacuous assertion: if the runtime gave us no stack there
    // is nothing to prove absent.
    expect(stackFrame).not.toBe('');

    const router = buildTestRouter({ '/': <ThrowingShell /> }, ['/']);
    render(<RouterProvider router={router} />);

    const fallback = await screen.findByTestId('shell-error-fallback');
    // Shell is gone (that is what this layer is for), and react-router's own
    // DefaultErrorComponent did not get the chance to render.
    expect(
      screen.queryByRole('navigation', { name: t('routes.layout.navMenuToggle') }),
    ).not.toBeInTheDocument();
    // Says what failed…
    expect(fallback).toHaveTextContent('shell boom');
    // …without leaking a stack frame to the screen (it goes to Sentry instead).
    expect(document.body.textContent ?? '').not.toContain(stackFrame);
  });
});

describe('test harness safety', () => {
  it('throws when an override names a route the router does not register', () => {
    expect(() => buildTestRouter({ 'jobs-renamed': <div /> })).toThrow(/unknown route key/u);
  });
});
