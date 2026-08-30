import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RouterProvider } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { __resetAuthStateForTests } from '@/auth/session';
import { DEFAULT_LOCALE, LOCALE_STORAGE_KEY, setLocale, SUPPORTED_LOCALES, useT } from '@/i18n';
import enMessages from '@/locales/en.json';
import koMessages from '@/locales/ko.json';
import { BlockSkeleton, Button, EmptyState, ErrorState } from '@/ui';

import {
  authenticateForTests,
  buildTestRouter,
  flattenRoutes,
  routeKey,
  stubRequestForDataRouterNavigation,
} from './helpers/app-routes';

/**
 * S10 / S12 — the CONSUMPTION half of `handle.titleKey`.
 *
 * M-2 made every route declare a title key (`tsc`-enforced). Declaring it and
 * never reading it would look identical to a reviewer and identical to the
 * declaration seal, so this file drives the real router and asserts what a
 * screen-reader user actually receives on a client-side navigation: a document
 * title naming the screen, a polite announcement of the same, and focus at the
 * start of the new content.
 *
 * Expected copy is resolved from the LOCALE BUNDLES, not from `t()` — asserting
 * through the same resolver the component uses would pass even if both agreed
 * on the wrong key.
 */

interface MessageTree {
  readonly [key: string]: string | MessageTree;
}

/** Resolve a dotted key against a raw bundle — an independent path to the copy
 *  (deliberately not `t()`, which is what we are testing the wiring of). */
function fromBundle(bundle: MessageTree, dotted: string): string {
  let node: string | MessageTree = bundle;
  for (const part of dotted.split('.')) {
    if (typeof node === 'string') throw new Error(`key ${dotted} runs past a leaf`);
    const next: string | MessageTree | undefined = node[part];
    if (next === undefined) throw new Error(`key ${dotted} missing from the bundle`);
    node = next;
  }
  if (typeof node !== 'string') throw new Error(`key ${dotted} is not a leaf`);
  return node;
}

function titleKeyOf(key: string): string {
  const route = flattenRoutes().find((candidate) => routeKey(candidate) === key);
  if (route === undefined) {
    throw new Error(`no route registered under key ${key}`);
  }
  return route.handle.titleKey;
}

/** The document title the app OUGHT to show for a route, assembled from the
 *  bundle template rather than from the component's own code path. */
function expectedTitle(bundle: MessageTree, key: string): string {
  return fromBundle(bundle, 'routes.layout.documentTitle')
    .replace('{screen}', fromBundle(bundle, titleKeyOf(key)))
    .replace('{app}', fromBundle(bundle, 'routes.layout.appTitle'));
}

function MountedWave1LocaleSurface(): JSX.Element {
  const { t } = useT();
  return (
    <section>
      <h1 data-testid="live-wave1-heading">{t('routes.home.title')}</h1>
      <Button data-testid="live-wave1-action" type="button">
        {t('routes.home.ctaTestPlans')}
      </Button>
      <ErrorState testId="live-wave1-error" message={t('errors.network')} />
      <EmptyState testId="live-wave1-empty" title={t('routes.home.inProgressEmpty')} />
      <BlockSkeleton testId="live-wave1-loading" lines={1} label={t('common.loadingPage')} />
    </section>
  );
}

const STUB_ROUTES = {
  jobs: <div data-testid="jobs-stub">jobs</div>,
  sessions: <div data-testid="sessions-stub">sessions</div>,
} as const;

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
  localStorage.removeItem(LOCALE_STORAGE_KEY);
  setLocale(DEFAULT_LOCALE);
  document.title = 'unset-by-test';
  authenticateForTests(['platform:read']);
  stubRequestForDataRouterNavigation(vi.stubGlobal);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setLocale(DEFAULT_LOCALE);
  localStorage.removeItem(LOCALE_STORAGE_KEY);
  __resetAuthStateForTests();
});

describe('document title follows the route (S10)', () => {
  it('names the screen on first render, not just the app', async () => {
    render(<RouterProvider router={buildTestRouter(STUB_ROUTES, ['/jobs'])} />);
    await screen.findByTestId('jobs-stub');

    await waitFor(() => expect(document.title).toBe(expectedTitle(enMessages, 'jobs')));
    // Non-vacuity: the app name alone would also "be a title".
    expect(document.title).not.toBe(fromBundle(enMessages, 'routes.layout.appTitle'));
    expect(document.title).toContain(fromBundle(enMessages, titleKeyOf('jobs')));
  });

  it('changes when the location changes', async () => {
    const router = buildTestRouter(STUB_ROUTES, ['/jobs']);
    render(<RouterProvider router={router} />);
    await waitFor(() => expect(document.title).toBe(expectedTitle(enMessages, 'jobs')));

    await router.navigate('/sessions');

    await screen.findByTestId('sessions-stub');
    await waitFor(() => expect(document.title).toBe(expectedTitle(enMessages, 'sessions')));
    // The two screens must actually differ, or "it changed" proves nothing.
    expect(expectedTitle(enMessages, 'sessions')).not.toBe(expectedTitle(enMessages, 'jobs'));
  });

  it('follows the locale toggle without navigating', async () => {
    const otherLocale = SUPPORTED_LOCALES.find((locale) => locale !== DEFAULT_LOCALE);
    if (otherLocale === undefined)
      throw new Error('single-locale build — this assertion is vacuous');
    // Guard: identical copy in both bundles would make the switch unobservable.
    expect(expectedTitle(koMessages, 'jobs')).not.toBe(expectedTitle(enMessages, 'jobs'));

    render(<RouterProvider router={buildTestRouter(STUB_ROUTES, ['/jobs'])} />);
    await waitFor(() => expect(document.title).toBe(expectedTitle(enMessages, 'jobs')));

    setLocale(otherLocale);

    await waitFor(() => expect(document.title).toBe(expectedTitle(koMessages, 'jobs')));
  });

  it('switches mounted Wave-1 state copy and language parts without reload', async () => {
    const user = userEvent.setup();
    const keys = [
      'routes.home.title',
      'routes.home.ctaTestPlans',
      'errors.network',
      'routes.home.inProgressEmpty',
      'common.loadingPage',
    ] as const;
    for (const key of keys) {
      expect(fromBundle(enMessages, key)).not.toBe(fromBundle(koMessages, key));
    }

    const router = buildTestRouter({ index: <MountedWave1LocaleSurface /> }, ['/']);
    render(<RouterProvider router={router} />);
    await screen.findByTestId('live-wave1-heading');
    await waitFor(() => expect(document.title).toBe(expectedTitle(enMessages, 'index')));
    const heading = screen.getByTestId('live-wave1-heading');
    const action = screen.getByTestId('live-wave1-action');
    const error = screen.getByTestId('live-wave1-error');
    const empty = screen.getByTestId('live-wave1-empty');
    const loading = screen.getByTestId('live-wave1-loading');
    const toggle = screen.getByTestId('locale-toggle');

    expect(heading).toHaveTextContent(fromBundle(enMessages, 'routes.home.title'));
    expect(action).toHaveTextContent(fromBundle(enMessages, 'routes.home.ctaTestPlans'));
    expect(error).toHaveTextContent(fromBundle(enMessages, 'errors.network'));
    expect(empty).toHaveTextContent(fromBundle(enMessages, 'routes.home.inProgressEmpty'));
    expect(loading).toHaveAttribute('aria-label', fromBundle(enMessages, 'common.loadingPage'));
    expect(document.documentElement).toHaveAttribute('lang', 'en');
    expect(router.state.location.pathname).toBe('/');

    await user.click(toggle);

    await waitFor(() => {
      expect(heading).toHaveTextContent(fromBundle(koMessages, 'routes.home.title'));
      expect(action).toHaveTextContent(fromBundle(koMessages, 'routes.home.ctaTestPlans'));
      expect(error).toHaveTextContent(fromBundle(koMessages, 'errors.network'));
      expect(empty).toHaveTextContent(fromBundle(koMessages, 'routes.home.inProgressEmpty'));
      expect(loading).toHaveAttribute('aria-label', fromBundle(koMessages, 'common.loadingPage'));
      expect(document.documentElement).toHaveAttribute('lang', 'ko');
      expect(document.title).toBe(expectedTitle(koMessages, 'index'));
    });

    expect(screen.getByTestId('live-wave1-heading')).toBe(heading);
    expect(screen.getByTestId('live-wave1-action')).toBe(action);
    expect(screen.getByTestId('live-wave1-error')).toBe(error);
    expect(screen.getByTestId('live-wave1-empty')).toBe(empty);
    expect(screen.getByTestId('live-wave1-loading')).toBe(loading);
    expect(toggle.querySelector('.locale-toggle__label')).toHaveAttribute('lang', 'en');
  });

  it('covers routes outside the shell too', async () => {
    // `/auth/callback` is a SIBLING of `/`, so a title hook living inside
    // `AppLayout` would silently skip it.
    render(<RouterProvider router={buildTestRouter({}, ['/auth/callback'])} />);

    await waitFor(() => expect(document.title).toBe(expectedTitle(enMessages, '/auth/callback')));
  });
});

describe('route announcement is polite (S10)', () => {
  it('announces the screen through a status region, never an alert', async () => {
    render(<RouterProvider router={buildTestRouter(STUB_ROUTES, ['/jobs'])} />);
    await screen.findByTestId('jobs-stub');

    const announcer = screen.getByTestId('route-announcer');
    // `role="status"` IS the polite live region; `alert` would interrupt the
    // operator on every navigation.
    expect(announcer).toHaveAttribute('role', 'status');
    expect(announcer).not.toHaveAttribute('role', 'alert');
    // Visually hidden — this is for assistive tech, not a banner.
    expect(announcer).toHaveClass('sr-only');
    expect(announcer.textContent ?? '').toContain(fromBundle(enMessages, titleKeyOf('jobs')));
  });

  it('updates its text on navigation so the region actually fires', async () => {
    const router = buildTestRouter(STUB_ROUTES, ['/jobs']);
    render(<RouterProvider router={router} />);
    const announcer = await screen.findByTestId('route-announcer');
    const before = announcer.textContent ?? '';

    await router.navigate('/sessions');

    await screen.findByTestId('sessions-stub');
    await waitFor(() =>
      expect(screen.getByTestId('route-announcer').textContent ?? '').toContain(
        fromBundle(enMessages, titleKeyOf('sessions')),
      ),
    );
    // The live region node must SURVIVE the navigation — a region remounted
    // together with its text is not announced at all.
    expect(screen.getByTestId('route-announcer')).toBe(announcer);
    expect(announcer.textContent ?? '').not.toBe(before);
  });
});

describe('focus moves to the new screen (S12)', () => {
  function mainElement(): HTMLElement {
    return screen.getByRole('main');
  }

  it('makes the main column programmatically focusable', async () => {
    render(<RouterProvider router={buildTestRouter(STUB_ROUTES, ['/jobs'])} />);
    await screen.findByTestId('jobs-stub');

    // Load-bearing for the skip-link too: a browser only moves focus to a
    // fragment target that can hold focus.
    expect(mainElement()).toHaveAttribute('tabindex', '-1');
    const skipLink = screen.getByRole('link', {
      name: fromBundle(enMessages, 'routes.layout.skipToContent'),
    });
    expect(skipLink.getAttribute('href')).toBe(`#${mainElement().id}`);
  });

  it('does not steal focus on first mount', async () => {
    render(<RouterProvider router={buildTestRouter(STUB_ROUTES, ['/jobs'])} />);
    await screen.findByTestId('jobs-stub');

    // Arriving at a page is not a navigation — focus belongs at the document
    // start, where the skip-link is the first stop.
    expect(document.activeElement).toBe(document.body);
  });

  it('moves focus to the main column on a route change', async () => {
    const router = buildTestRouter(STUB_ROUTES, ['/jobs']);
    render(<RouterProvider router={router} />);
    await screen.findByTestId('jobs-stub');

    await router.navigate('/sessions');
    await screen.findByTestId('sessions-stub');

    await waitFor(() => expect(document.activeElement).toBe(mainElement()));
  });

  it('leaves focus alone when only the query string changes', async () => {
    const router = buildTestRouter(STUB_ROUTES, ['/jobs']);
    render(<RouterProvider router={router} />);
    await screen.findByTestId('jobs-stub');

    // Park focus on a control the way an operator mid-interaction would.
    const toggle = screen.getByTestId('nav-menu-toggle');
    toggle.focus();
    expect(document.activeElement).toBe(toggle);

    await router.navigate('/jobs?project=p-1');

    await waitFor(() => expect(router.state.location.search).toBe('?project=p-1'));
    // A filter/pagination change must not yank focus out from under them.
    expect(document.activeElement).toBe(toggle);
  });
});
