import { createMemoryRouter } from 'react-router-dom';

import { appRoutes, toRouteObjects, type AppRoute } from '@/app';
import { applyTokenSet, CLAIM_PERMISSIONS } from '@/auth/session';

import type { ReactElement } from 'react';

/**
 * Test harness over the PRODUCTION route registration.
 *
 * The point is to exercise the real nesting — real `<RequireAuth>`, real
 * `AppLayout`, real route/shell `errorElement`s — while swapping individual
 * screens for stubs. A hand-rebuilt route tree in a test would pass against a
 * shape production never renders.
 *
 * Routes are addressed by ROUTE KEY: `'index'` for the index route, otherwise
 * the route's `path` verbatim (`'/'`, `'/auth/callback'`, `'jobs'`, `'*'`).
 * An override naming a key the router does not register THROWS — so renaming a
 * route turns its tests red instead of leaving them silently exercising the
 * real (unstubbed) screen forever.
 */
export function routeKey(route: AppRoute): string {
  return 'index' in route ? 'index' : route.path;
}

export type RouteOverrides = Readonly<Record<string, ReactElement>>;

function replaceElements(
  routes: readonly AppRoute[],
  overrides: RouteOverrides,
  used: Set<string>,
): AppRoute[] {
  return routes.map((route) => {
    const key = routeKey(route);
    const override = Object.prototype.hasOwnProperty.call(overrides, key)
      ? overrides[key]
      : undefined;
    if (override !== undefined) used.add(key);
    const element = override ?? route.element;
    return 'index' in route
      ? { ...route, element }
      : {
          ...route,
          element,
          ...(route.children ? { children: replaceElements(route.children, overrides, used) } : {}),
        };
  });
}

/** Every registered route object, shell and children alike, in declaration
 *  order — the surface an "all of them, not a sample" assertion needs. */
export function flattenRoutes(routes: readonly AppRoute[] = appRoutes): AppRoute[] {
  return routes.flatMap((route) => [
    route,
    ...('index' in route || !route.children ? [] : flattenRoutes(route.children)),
  ]);
}

/** All route keys the router registers, in declaration order. */
export function registeredRouteKeys(routes: readonly AppRoute[] = appRoutes): string[] {
  return routes.flatMap((route) => [
    routeKey(route),
    ...('index' in route || !route.children ? [] : registeredRouteKeys(route.children)),
  ]);
}

export function buildTestRoutes(overrides: RouteOverrides = {}): AppRoute[] {
  const used = new Set<string>();
  const routes = replaceElements(appRoutes, overrides, used);
  const unknown = Object.keys(overrides).filter((key) => !used.has(key));
  if (unknown.length > 0) {
    throw new Error(
      `app-routes helper: unknown route key(s) ${unknown.join(', ')}. ` +
        `Registered keys: ${registeredRouteKeys().join(', ')}`,
    );
  }
  return routes;
}

export function buildTestRouter(
  overrides: RouteOverrides = {},
  initialEntries: readonly string[] = ['/'],
): ReturnType<typeof createMemoryRouter> {
  return createMemoryRouter(toRouteObjects(buildTestRoutes(overrides)), {
    initialEntries: [...initialEntries],
  });
}

/** Put the real auth store into an authenticated state so `<RequireAuth>`
 *  renders the shell (same mechanism as tests/auth/route-guard.test.tsx —
 *  no test-only bypass path is introduced). */
export function authenticateForTests(permissions: readonly string[] = []): void {
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).replace(/=+$/u, '');
  const body = btoa(JSON.stringify({ sub: 'u-test', [CLAIM_PERMISSIONS]: permissions }))
    .replace(/=+$/u, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  applyTokenSet({
    accessToken: `${header}.${body}.sig`,
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
}

/** A route element that throws on render — the injected defect the boundary
 *  tests are actually about. */
export function ThrowingRoute({ message }: { readonly message: string }): JSX.Element {
  throw new Error(message);
}

/**
 * Make imperative data-router navigation possible under vitest's jsdom.
 *
 * Measured: vitest's jsdom environment installs jsdom's `AbortController`
 * while `Request` stays Node's (undici), and undici rejects EVERY signal this
 * realm can produce — including one taken off a `Request` it built itself
 * ("RequestInit: Expected signal to be an instance of AbortSignal").
 * `@remix-run/router` builds a `Request` on every navigation
 * (`startNavigation` → `createClientSideRequest`, unconditional), so a data
 * router cannot be navigated here at all without a stand-in. That is why every
 * pre-existing spec uses the non-data `MemoryRouter`.
 *
 * The app registers NO route loaders or actions, so this object is bookkeeping
 * react-router hands back to itself and nothing ever reads a body from it.
 *
 * Two deliberate limits: it is FEATURE-DETECTED (the day the environment is
 * fixed, the real global is used and this dies quietly), and it must be
 * installed per test file with `vi.unstubAllGlobals()` in `afterEach`, so the
 * typed API-client suites keep the genuine `Request`.
 */
export function stubRequestForDataRouterNavigation(
  stubGlobal: (name: string, value: unknown) => void,
): boolean {
  try {
    new Request('http://localhost/', { signal: new AbortController().signal });
    return false;
  } catch {
    // Falls through to the stand-in below.
  }
  class NavigationRequest {
    readonly url: string;
    readonly method: string;
    readonly signal: AbortSignal | undefined;
    readonly headers: Headers;
    constructor(input: string | { url: string }, init?: RequestInit) {
      this.url = typeof input === 'string' ? input : input.url;
      this.method = init?.method ?? 'GET';
      this.signal = init?.signal ?? undefined;
      this.headers = new Headers(init?.headers);
    }
  }
  stubGlobal('Request', NavigationRequest);
  return true;
}
