import { beforeEach, vi } from 'vitest';

import { headlessClient } from '@/api/headless-client';

import { headlessProblem, problemDetails } from './headless-contract';

import type {
  AnyHeadlessEnvelope,
  HeadlessRequestInit,
  HeadlessRouteTable,
} from './headless-contract';
import type { HeadlessApiPaths } from '@/api/headless-client';
import type { HttpMethod, PathsWithMethod } from 'openapi-typescript-helpers';
import type { Mock } from 'vitest';

/**
 * Spies on the **real** headless client's transport methods and returns an
 * object shaped like the ad-hoc fake these tests used to hoist.
 *
 * ## Why a spy and not `vi.mock`
 *
 * The request plumbing now lives inside `@/api/headless-client` (the operation
 * helpers). Mocking that module — whole or partial — **cannot** substitute the
 * transport those helpers use: an ES module mock replaces *exports*, and the
 * helpers close over the module-internal `headlessClient` binding, which stays
 * the real one. Measured on the first attempt: a partial mock left **9 of 11**
 * `jobs.test.tsx` tests failing, because the route test was exercising helpers
 * that talked to the real transport while the test believed it had replaced it.
 *
 * That is the exact failure `tests/diagnostics.test.tsx` names for the session
 * surface: *"a whole-module mock would have made the route test pass against a
 * client that never runs"*. Spying the exported object mutates the one instance
 * every helper holds, so the helpers really do run and only the wire is fake.
 *
 * ## Why the spies are typed against the contract, not `Mock`
 *
 * They used to be bare `Mock` — that is `Mock<any>`, so a stub payload that did
 * not match the wire contract was invisible to `tsc`. The reason recorded at the
 * time was real: `vi.spyOn` hands back a spy typed against the *concrete*
 * openapi-fetch signature, and satisfying that at every stub site costs ~167
 * `as never` casts for no added safety.
 *
 * The resolution is not to widen or to cast, but to declare a **stub-side**
 * signature ({@link HeadlessStubMethod}): the path is `keyof paths`, and the
 * result is the union of the envelopes the operations on this surface can
 * actually resolve with. A payload matching no operation is a compile error; a
 * payload built by `headlessOk`/`headlessProblem` is assignable without a cast.
 * `openapi-typescript-helpers` derives all of it from the generated schema, so
 * nothing here restates a response shape (`./headless-contract`).
 *
 * ⚠️ **What this still does not check, stated rather than hidden.** Two gaps,
 * both measured:
 *
 * 1. A `mockResolvedValue` on a bare spy only has to satisfy the union over
 *    *all* of that verb's operations, so operation A's body is accepted where
 *    operation B's was expected. `routes()` is the narrower check — it pins the
 *    entry to one path — so prefer it.
 * 2. **`routes()` does not narrow the empty member** — and, since 2026-09-12,
 *    that is the whole of what it does not narrow. The blob member used to ride
 *    on every operation for the reason stated here: whether a call site passes
 *    `parseAs: 'blob'` is a *call-site* fact, not a schema one. It is still a
 *    call-site fact — the change is that the call site is **ours**, so it is now
 *    declared once (`BLOB_PARSED_GET` in `@/api/headless-client`, the only
 *    `parseAs` in `src/`) and the envelope derives from that declaration. A JSON
 *    operation no longer accepts a `Blob`, inside a route table or out.
 *
 *    Emptiness is a different fact and stays universal: `openapi-fetch` answers
 *    `{ data: undefined }` for any 204 or `Content-Length: 0` whatever the schema
 *    declares, and no operation on this surface declares a content-less 2xx — so
 *    a schema-derived narrowing would admit *nothing*.
 *    `headless-contract.type-test.ts` asserts both halves, and
 *    `tests/headless-empty-body-fact.test.ts` holds the reason for the second.
 *
 * The wire contract proper is decided where it always was: the generated `paths`
 * constrain the operation helpers in `headless-client.ts`.
 *
 * ## Lifetime
 *
 * Call at module scope, once. `vi.spyOn` on the same method twice returns the
 * same spy, so a suite that also spies directly stays consistent.
 */
export function spyHeadlessTransport(): HeadlessTransportSpies {
  // Stable mock functions the suite keeps a reference to. The spy installed on
  // the client *delegates* to them, so a suite may `mockReset()` / restub them
  // between tests exactly as it did with the old hoisted fake.
  const fns: HeadlessTransportMocks = {
    GET: vi.fn(),
    POST: vi.fn(),
    PUT: vi.fn(),
    PATCH: vi.fn(),
    DELETE: vi.fn(),
  };

  // ⚠️ Five explicit statements, not a loop over the verb names. A loop widens
  // `headlessClient[verb]` into a union of five `ClientMethod<…>`s, and `spyOn`
  // then demands an implementation assignable to *all* of them — which nothing
  // is. Written out, each `spyOn` keeps its concrete method type.
  const install = (): void => {
    vi.spyOn(headlessClient, 'GET').mockImplementation(fns.GET as never);
    vi.spyOn(headlessClient, 'POST').mockImplementation(fns.POST as never);
    vi.spyOn(headlessClient, 'PUT').mockImplementation(fns.PUT as never);
    vi.spyOn(headlessClient, 'PATCH').mockImplementation(fns.PATCH as never);
    vi.spyOn(headlessClient, 'DELETE').mockImplementation(fns.DELETE as never);
  };

  install();

  /** The table `routes()` accumulates into. Cleared per test — see below. */
  const table: MutableRouteTable = {};
  /** Mutable so a later `routes()` call in the same test keeps the chosen mode. */
  const unrouted: { mode: NonNullable<HeadlessRouteOptions['unrouted']> } = { mode: 'reject' };

  // ⚠️ **Re-install before every test, and this is not belt-and-braces.**
  // Several of these suites end with `afterEach(() => vi.restoreAllMocks())`,
  // which puts the *real* openapi-fetch method back on the client. Without this
  // hook the first test in a file passes and every later one silently talks to
  // the real transport — measured: 27 of 27 `reports.test.tsx` cases failed that
  // way, with the first one green.
  //
  // Registered at module scope, so it runs before the suite's own `beforeEach`
  // (hooks fire in registration order) and a `mockReset()` there still wins.
  beforeEach(() => {
    install();
    // ⚠️ **Cleared per test, not accumulated across the file.** A surviving
    // table would let a route one test declared answer another that never asked
    // for it — the quiet cross-test coupling that makes a suite pass in file
    // order and fail alone.
    for (const key of Object.keys(table)) delete table[key];
    unrouted.mode = 'reject';
  });

  return {
    ...fns,
    routes: (added: HeadlessRouteTable, options?: HeadlessRouteOptions): void => {
      mergeRouteTable(table, added);
      unrouted.mode = options?.unrouted ?? unrouted.mode;
      installRouteTable(fns, table, unrouted);
    },
  };
}

/**
 * The five transport verbs, and the schema method each one speaks.
 *
 * The intersection is the check: `Record<Verb, HttpMethod>` rejects a mapping to
 * anything that is not a real OpenAPI method, so a typo here is a compile error
 * rather than a verb whose stubs silently type against `never`.
 */
type Verb = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
type VerbMethod = Record<Verb, HttpMethod> & {
  GET: 'get';
  POST: 'post';
  PUT: 'put';
  PATCH: 'patch';
  DELETE: 'delete';
};

/**
 * The stub-side signature of one transport verb.
 *
 * `path` is `keyof paths` for this method, so a retired or mistyped route is a
 * compile error at any site that calls a stub directly. (This is what the
 * module docstring above always claimed; the type said `string` until
 * `stub-request-axis-typing`. A comment that describes a check the code does not
 * perform is worse than no comment — the next reader trusts it.)
 *
 * ⚠️ **`init` stays `unknown`, and that is the design, not the gap.** Narrowing
 * it here would type one mock against the union of *every* operation this verb
 * has, so a call site would still have to say which one it meant — the per-site
 * cast, moved rather than removed. That is the trap the debt entry recorded.
 * The request axis is checked where the operation is *named* instead:
 * {@link headlessRequest} / {@link headlessRequests}, mirroring how
 * `headlessOk(method, path, body)` pins the response axis.
 */
export type HeadlessStubMethod<M extends HttpMethod> = (
  path: PathsWithMethod<HeadlessApiPaths, M>,
  init?: unknown,
) => Promise<AnyHeadlessEnvelope<M>> | AnyHeadlessEnvelope<M>;

/**
 * Every request the suite sent to `<method> <path>`, typed against the contract.
 *
 * ## What this replaces
 *
 * ```ts
 * const [, options] = transport.GET.mock.calls[0] as [
 *   string,
 *   { params: { path: { session_id: number } } },   // a hand copy of the contract
 * ];
 * ```
 *
 * That cast did three things at once: restated the request shape, asserted it
 * without checking it, and kept compiling after the contract renamed the member.
 * It is the request-side twin of the response fixtures the previous wave
 * removed.
 *
 * ## Why it filters by path
 *
 * `mock.calls[0]` assumes the first call to that verb was the one you meant, and
 * `.find(c => c[0] === PATH)` re-implements the filter at every site. Naming the
 * operation makes the filter one implementation and makes the *type* follow from
 * it.
 *
 * ## The one erasure, and why it is here
 *
 * The recorded calls are `[path, init?]` with `init: unknown`, so handing back a
 * typed value needs a cast. It happens **once, behind the runtime filter that
 * justifies it**: production calls the transport with the path template and that
 * operation's own init, so a call whose `path` matches carries that operation's
 * request. `getBodyRoutes` in `./headless-contract` documents the identical
 * trade — *"the one erasure, and it is here rather than at the call sites"*. The
 * alternative is one cast per site, which is what this exists to remove.
 */
export function headlessRequests<
  M extends HttpMethod,
  P extends PathsWithMethod<HeadlessApiPaths, M>,
>(mock: Mock<HeadlessStubMethod<M>>, method: M, path: P): HeadlessRequestInit<M, P>[] {
  void method;
  return mock.mock.calls
    .filter((call) => call[0] === path)
    .map((call) => call[1] as HeadlessRequestInit<M, P>);
}

/**
 * The single request the suite sent to `<method> <path>` — and an error naming
 * the count if there was not exactly one.
 *
 * ⚠️ **`mock.calls[0]` quietly takes the first of many.** Several sites this
 * replaces asserted on "the" call while the component had polled the same
 * endpoint more than once; the assertion then described whichever request
 * happened to be first, and a second call with different parameters could never
 * fail it. Refusing to guess is the point — a suite that genuinely expects
 * several calls says so with {@link headlessRequests}.
 */
export function headlessRequest<
  M extends HttpMethod,
  P extends PathsWithMethod<HeadlessApiPaths, M>,
>(mock: Mock<HeadlessStubMethod<M>>, method: M, path: P): HeadlessRequestInit<M, P> {
  const matches = headlessRequests(mock, method, path);
  if (matches.length !== 1) {
    throw new Error(
      `expected exactly one ${method.toUpperCase()} ${String(path)} request, saw ${String(matches.length)}. ` +
        'Use headlessRequests() if several are expected, or assert on the count first.',
    );
  }
  return matches[0] as HeadlessRequestInit<M, P>;
}

export type HeadlessTransportMocks = {
  [V in Verb]: Mock<HeadlessStubMethod<VerbMethod[V]>>;
};

/**
 * What an unrouted path answers.
 *
 * The default is a **loud rejection**, so a route the suite forgot to declare
 * names itself. `'not-found'` reproduces a server that has no such resource —
 * legitimate when the *absence* is what a test is about, and explicit so it
 * cannot become the silent default again.
 */
export interface HeadlessRouteOptions {
  readonly unrouted?: 'reject' | 'not-found';
}

/** The accumulated table, addressed structurally because the merge is per (path, method). */
type MutableRouteTable = Record<string, Record<string, unknown>>;

function mergeRouteTable(target: MutableRouteTable, added: HeadlessRouteTable): void {
  for (const [path, methods] of Object.entries(added as MutableRouteTable)) {
    // Merge per method rather than replacing the path entry: a suite that
    // declares GET and POST for one path in two calls must keep both.
    target[path] = { ...target[path], ...methods };
  }
}

export type HeadlessTransportSpies = HeadlessTransportMocks & {
  /**
   * Answer a path → method → handler table, and **reject anything else loudly**.
   *
   * **Merges** into what earlier calls in the same test declared, so a suite can
   * set a baseline in `beforeEach` and have one test override a single method.
   * The accumulated table is cleared before each test.
   *
   * Replaces the hand-written `if (path === …)` chains. Those could not use the
   * contract-derived builders (a runtime `===` does not narrow `path` for the
   * type system) and answered an unrouted path with a quiet 404 — so a route
   * the suite forgot to stub looked like a route the server does not have.
   *
   * The rejection mirrors what `openapi-ts.dev/openapi-fetch/testing` recommends
   * for MSW (`onUnhandledRequest: throw`). A test that *wants* a 404 declares it
   * with `headlessProblem`.
   *
   * Matching is exact. `openapi-fetch` is called with the path **template**
   * (`GET('/x/{id}', { params: { path: { id } } })`), so the string a spy sees is
   * already the table key — the URL↔template matcher the openapi-ts examples
   * carry is unnecessary at this layer, and unnecessary machinery is machinery
   * that can be wrong.
   */
  routes: (table: HeadlessRouteTable, options?: HeadlessRouteOptions) => void;
};

/**
 * Look the handler up and call it. Deliberately **not** generic over the method.
 *
 * ⚠️ A `<M extends HttpMethod>` signature makes TypeScript evaluate
 * `AnyHeadlessEnvelope<M>` at the *constraint* — the join of all eight methods'
 * per-verb unions — and it refuses outright (`TS2590: union type too complex to
 * represent`). The envelope type therefore appears only where the verb is a
 * literal: at the five install sites below.
 *
 * The type safety this module provides does not live here anyway. It lives at
 * the **table literal**, which `HeadlessRouteTable` has already checked
 * path-by-path before any of this runs.
 */
function lookupRoute(
  verb: Verb,
  method: HttpMethod,
  table: MutableRouteTable,
  unrouted: { mode: NonNullable<HeadlessRouteOptions['unrouted']> },
  path: string,
  init: unknown,
): Promise<unknown> {
  const entry = (table as Record<string, Record<string, unknown> | undefined>)[path];
  const handler = entry?.[method];
  if (typeof handler !== 'function') {
    if (unrouted.mode === 'not-found') {
      return Promise.resolve(
        headlessProblem(
          'get',
          '/headless/status',
          404,
          problemDetails(404, 'NOT_FOUND', { detail: `no route for ${verb} ${path}` }),
        ),
      );
    }
    return Promise.reject(
      new Error(
        `headless transport: no route for ${verb} ${path}. ` +
          `Add it to the routes({…}) table (declare an expected failure with headlessProblem), ` +
          `or pass { unrouted: 'not-found' } if the absence is the point.`,
      ),
    );
  }
  return Promise.resolve((handler as (p: string, i: unknown) => unknown)(path, init));
}

/** Five explicit installs, mirroring the spy install above — see {@link lookupRoute}. */
function installRouteTable(
  fns: HeadlessTransportMocks,
  table: MutableRouteTable,
  unrouted: { mode: NonNullable<HeadlessRouteOptions['unrouted']> },
): void {
  fns.GET.mockImplementation(
    (path, init) =>
      lookupRoute('GET', 'get', table, unrouted, path, init) as Promise<AnyHeadlessEnvelope<'get'>>,
  );
  fns.POST.mockImplementation(
    (path, init) =>
      lookupRoute('POST', 'post', table, unrouted, path, init) as Promise<
        AnyHeadlessEnvelope<'post'>
      >,
  );
  fns.PUT.mockImplementation(
    (path, init) =>
      lookupRoute('PUT', 'put', table, unrouted, path, init) as Promise<AnyHeadlessEnvelope<'put'>>,
  );
  fns.PATCH.mockImplementation(
    (path, init) =>
      lookupRoute('PATCH', 'patch', table, unrouted, path, init) as Promise<
        AnyHeadlessEnvelope<'patch'>
      >,
  );
  fns.DELETE.mockImplementation(
    (path, init) =>
      lookupRoute('DELETE', 'delete', table, unrouted, path, init) as Promise<
        AnyHeadlessEnvelope<'delete'>
      >,
  );
}
