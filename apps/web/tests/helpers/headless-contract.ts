/**
 * Contract-derived types and response builders for the headless transport stubs
 * (`typed-headless-transport-stubs`, 2026-09-10).
 *
 * ## What this closes
 *
 * `spyHeadlessTransport()` used to hand back bare `Mock`s — that is `Mock<any>`,
 * so **a stub payload that does not match the wire contract was invisible to
 * `tsc`**. The suites therefore proved something about *a* response shape, not
 * about *today's* response shape: a backend schema change could land and leave
 * every one of these files green against a fixture that no server would ever
 * send. The debt entry that named this is `[2026-08-19 headless-helper] P3`.
 *
 * ## Why derivation and not hand-written fixture types
 *
 * Writing the response shapes out by hand would create a **second definition of
 * the contract**, and the copy that drifts is always the fixture. Everything
 * here is derived from the generated `paths` (`npm run codegen`, ADR-0003) —
 * there is no shape spelled out in this file.
 *
 * The type helpers come from `openapi-typescript-helpers`, the package
 * `openapi-fetch` itself builds its types with. `openapi-ts.dev/examples` shows
 * this exact pattern with the helpers copied inline and the note *"type helpers
 * — ignore these; these just make TS lookups better"*; today they ship as a
 * package, so we import instead of re-declaring them. Re-declaring would be a
 * third definition of the same lookup.
 *
 * ## Why not MSW / a `fetch` mock
 *
 * `openapi-ts.dev/openapi-fetch/testing` recommends intercepting at `fetch`, and
 * that is genuinely higher fidelity — real serialisation, real middleware. It is
 * also a different wave: it moves ~145 stubs and ~118 assertions onto a URL axis
 * and starts running `authRetryMiddleware` for real. Recorded as follow-up in
 * `tech-debt-tracker.md` rather than smuggled in here.
 */
import type { HeadlessApiPaths, HeadlessBlobParsedPath } from '@/api/headless-client';
import type { MaybeOptionalInit } from 'openapi-fetch';
import type {
  ErrorResponse,
  HttpMethod,
  PathsWithMethod,
  ResponseObjectMap,
  SuccessResponse,
  SuccessResponseJSON,
} from 'openapi-typescript-helpers';

type Paths = HeadlessApiPaths;

/**
 * The Operation Object for `<method> <path>` in the generated schema.
 *
 * The conditional is what lets `Paths[P][M]` type-check: `PathsWithMethod`
 * proves the method exists at runtime, but TypeScript still needs the
 * constraint restated to index into it.
 */
type Operation<M extends HttpMethod, P extends PathsWithMethod<Paths, M>> =
  Paths[P] extends Record<
    M,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- schema-shaped lookup, mirrors openapi-typescript-helpers' own `OperationObject`
    any
  >
    ? Paths[P][M]
    : never;

/**
 * Success body for `<method> <path>`.
 *
 * JSON first, because all but three operations on this surface answer JSON.
 * The fallback is not defensive padding — those three would otherwise resolve to
 * `never` and be **unstubbable**, a hole paid for with an `as never` cast at
 * exactly the site this module exists to remove:
 *
 * | operation | media type |
 * |---|---|
 * | `GET …/test-plan/drafts/{draft_id}/export` | `…spreadsheetml.sheet` |
 * | `GET /headless/sessions/{session_id}/results/export` | `…spreadsheetml.sheet` |
 * | `GET /headless/reports/outputs/download` | `application/octet-stream` |
 *
 * ⚠️ An earlier version of this comment said *"every operation but one"* and
 * named the draft export as `application/octet-stream`. Both were wrong (an
 * independent review measured it), and the table is here rather than a count so
 * the next reader can re-derive it:
 * `[k for p,o in spec['paths'].items() for m,op in o.items() for c,r in
 * op['responses'].items() for k in (r.get('content') or {}) if 'json' not in k]`
 */
export type HeadlessOkBody<M extends HttpMethod, P extends PathsWithMethod<Paths, M>> = [
  SuccessResponseJSON<Operation<M, P>>,
] extends [never]
  ? SuccessResponse<ResponseObjectMap<Operation<M, P>>>
  : SuccessResponseJSON<Operation<M, P>>;

/**
 * Media types that carry a JSON error body.
 *
 * ⚠️ **Not `openapi-typescript-helpers`' `ErrorResponseJSON`.** That helper
 * filters on `` `${string}/json` ``, and this backend answers errors with
 * `application/problem+json` — which ends in `+json`, not `/json`, so the filter
 * matches **nothing** and the helper silently falls back to handing back the
 * whole *media map* (`{ 'application/problem+json'?: … }`) instead of the body.
 * Measured: the fallback still passes an `extends never` probe, so "the type is
 * not `never`" would have certified a type that is the wrong thing entirely.
 *
 * `${string}/${string}json` satisfies `MediaType` (it has the slash) and matches
 * both spellings, so a legacy `application/json` error body still resolves.
 */
type JsonErrorMedia = `${string}/${string}json`;

/** Error body for `<method> <path>` — the RFC 9457 problem document the backend declares. */
export type HeadlessErrorBody<
  M extends HttpMethod,
  P extends PathsWithMethod<Paths, M>,
> = ErrorResponse<ResponseObjectMap<Operation<M, P>>, JsonErrorMedia>;

/**
 * ⚠️ **The operation an envelope belongs to, made *measurable*.**
 *
 * Without this member the envelope types are **unsound in `P`**. `HeadlessOkBody`
 * reaches `P` only through a deferred conditional (`Operation<M, P>` →
 * `Paths[P] extends Record<M, any> ? … : never`), and TypeScript's variance
 * measurement treats a type parameter used only that way as **independent** — so
 * two instantiations relate regardless of `P`. `M` *is* measured, which is
 * exactly why the hole looks closed until the path axis is tested on its own.
 *
 * Measured consequence before this fix (independent adversarial review,
 * 2026-09-10): a status snapshot could be laundered into a jobs body with no
 * cast anywhere — `const l: HeadlessOkEnvelope<'get','/headless/jobs'> =
 * headlessOk('get','/headless/status', statusBody); l.data[0]!.id` compiled.
 * And `routes()` therefore checked the *key* but not the *body*, which made the
 * plan's central claim ("`routes()` pins the operation") false.
 *
 * The member is optional and never assigned — it exists only so the compiler has
 * a covariant position mentioning `P`. Converting the interfaces to type aliases
 * does **not** fix it (measured; do not reach for it).
 */
interface OperationBrand<M extends HttpMethod, P extends PathsWithMethod<Paths, M>> {
  readonly __operation?: readonly [M, P];
}

/**
 * The envelope `openapi-fetch` resolves with.
 *
 * `response` is a real `Response`, not a `{ status }` literal. Consumers reach
 * for `response?.status` **and** `response?.headers.get('content-disposition')`
 * (the two download paths in `headless-client.ts`), and a literal that satisfies
 * one silently fails the other — which is how a hand-rolled
 * `{ headers: { get: () => null } }` ended up in the suites.
 */
export interface HeadlessOkEnvelope<M extends HttpMethod, P extends PathsWithMethod<Paths, M>>
  extends OperationBrand<M, P> {
  readonly data: HeadlessOkBody<M, P>;
  readonly error?: undefined;
  readonly response: Response;
}

export interface HeadlessErrorEnvelope<M extends HttpMethod, P extends PathsWithMethod<Paths, M>>
  extends OperationBrand<M, P> {
  readonly data?: undefined;
  readonly error: HeadlessErrorBody<M, P>;
  readonly response: Response;
}

/**
 * A 2xx that carried no body.
 *
 * ⚠️ **A third state, not a modelling convenience.** `openapi-fetch` resolves
 * `{ data: undefined, error: undefined }` when a success response has an empty
 * body, and this surface's operations lean on it — `fetchHeadlessJobs` exists
 * partly to turn that `undefined` into `[]` so callers can `flatMap`. A union of
 * only ok-with-data and error would make the very case those operations were
 * written for unstubbable.
 *
 * ## Why this member stays universal while the blob member was narrowed
 *
 * ⚠️ **Deliberate, and measured rather than assumed** (2026-09-12). The two
 * looked like one debt entry — *"the envelope admits blob and empty for every
 * operation"* — and they are not the same fact:
 *
 * - Whether a response is blob-parsed is a decision **this codebase makes**, so
 *   it can be declared (`BLOB_PARSED_GET` in `@/api/headless-client`).
 * - Whether a response arrives empty is decided by the **wire**. Measured on the
 *   installed `openapi-fetch@0.13.0` (`dist/index.js`): `status === 204 ||
 *   Content-Length === '0'` yields `{ data: undefined }` for **any** operation,
 *   regardless of what the schema declares — and `FetchResponse` does not model
 *   that state at all, which is precisely why the operation helpers carry
 *   `data ?? []` fallbacks.
 *
 * A schema-derived narrowing would be worse than useless here: **no** operation
 * on this surface declares a content-less 2xx (measured — zero), so the derived
 * set is empty and every legitimate empty stub becomes unwritable.
 *
 * The reason is held by a **running** test rather than by this paragraph:
 * `tests/headless-empty-body-fact.test.ts` drives the real client against a 204
 * and fails the day the library narrows this.
 *
 * ⚠️ **No {@link OperationBrand} either.** The brand exists to stop a *body*
 * being laundered between operations, and an empty envelope has no body to
 * launder. Branding it would force `headlessEmptyOk('get', '/headless/jobs')` at
 * sites that are exercising a different operation — a false statement bought for
 * nothing.
 */
export interface HeadlessEmptyEnvelope {
  readonly data?: undefined;
  readonly error?: undefined;
  readonly response: Response;
}

/**
 * A success whose body was parsed as a `Blob`.
 *
 * ⚠️ **`parseAs` outranks the schema.** A download operation is called with
 * `parseAs: 'blob'`, so what reaches the caller is a `Blob` — not the
 * `application/vnd…sheet: string` the schema declares. Deriving the stub type
 * from the schema alone would demand a `string` and make the download paths
 * unstubbable without a cast.
 *
 * ⚠️ **Which operations may resolve one is no longer "all of them"**
 * (2026-09-12). `parseAs` is a call-site fact, and the call site is ours — so it
 * is declared once in `@/api/headless-client` (`BLOB_PARSED_GET`, consumed by
 * the only `parseAs` in `src/`) and this envelope enters an operation's union
 * only through {@link HeadlessEnvelope}'s conditional. The declaration is
 * imported, never restated: a second copy of the path set here is the drift this
 * module exists to prevent.
 *
 * The brand is what makes it operation-scoped rather than merely
 * operation-*gated*: without it a session-export blob answers a draft-export
 * route, since both paths pass the same conditional.
 */
export interface HeadlessBlobEnvelope<M extends HttpMethod, P extends PathsWithMethod<Paths, M>>
  extends OperationBrand<M, P> {
  readonly data: Blob;
  readonly error?: undefined;
  readonly response: Response;
}

/**
 * Every envelope `<method> <path>` can resolve with.
 *
 * ⚠️ **The blob member is conditional and the condition does not mention a
 * method.** Writing `M extends 'get'` would add a literal the declaration
 * already implies, and that literal would then be a second place to update the
 * day a POST operation is consumed as a file.
 *
 * The premise this omission rests on is narrower than it looks, so state it
 * rather than the implication: **both declared paths are served only by GET**
 * (measured). `satisfies Record<…, PathsWithMethod<Paths, 'get'>>` guarantees
 * *serves GET*, not *serves only GET* — three paths on this surface answer GET
 * and POST — so if a declared path ever gained a second verb,
 * `HeadlessEnvelope<'post', thatPath>` would quietly admit a blob again. That
 * premise is asserted by `TestBlobParsingIsADeclaredConsumptionAxis::
 * test_the_declared_paths_are_served_only_by_get`, not left to this comment
 * (independent adversarial review, 2026-09-12).
 */
export type HeadlessEnvelope<M extends HttpMethod, P extends PathsWithMethod<Paths, M>> =
  | HeadlessOkEnvelope<M, P>
  | HeadlessErrorEnvelope<M, P>
  | HeadlessEmptyEnvelope
  | (P extends HeadlessBlobParsedPath ? HeadlessBlobEnvelope<M, P> : never);

/**
 * Every envelope any operation of `<method>` can resolve with.
 *
 * This is the return type the stubs are declared against. It is a **union, not
 * `any`**: a payload that matches no operation on this surface is a compile
 * error, which is precisely the check that was missing.
 */
export type AnyHeadlessEnvelope<M extends HttpMethod> = {
  [P in PathsWithMethod<Paths, M>]: HeadlessEnvelope<M, P>;
}[PathsWithMethod<Paths, M>];

/** Response metadata a stub may override. Absent members take transport defaults. */
export interface HeadlessResponseInit {
  readonly status?: number;
  readonly headers?: Record<string, string>;
}

const DEFAULT_OK_STATUS = 200;
const DEFAULT_ERROR_STATUS = 400;

function stubResponse(status: number, headers: Record<string, string> | undefined): Response {
  // `null` body throughout: the payload travels in `data`/`error` because
  // `openapi-fetch` has already parsed it by the time a caller sees the
  // envelope. Passing a body here would also make 204/205 throw.
  //
  // `headers` is spread rather than assigned: under `exactOptionalPropertyTypes`
  // an explicit `headers: undefined` is not the same as an absent member, and
  // `ResponseInit` declares the optional one.
  return new Response(null, { status, ...(headers === undefined ? {} : { headers }) });
}

/**
 * A success envelope for `<method> <path>`, with `data` constrained to that
 * operation's declared success body.
 *
 * `method` and `path` exist to pin the generics — TypeScript cannot infer them
 * from `data` alone. The side effect is that every call site names the
 * operation it is standing in for, which the old `if (path === …)` chains only
 * implied.
 */
export function headlessOk<M extends HttpMethod, P extends PathsWithMethod<Paths, M>>(
  method: M,
  path: P,
  data: HeadlessOkBody<M, P>,
  init?: HeadlessResponseInit,
): HeadlessOkEnvelope<M, P> {
  void method;
  void path;
  return {
    data,
    error: undefined,
    response: stubResponse(init?.status ?? DEFAULT_OK_STATUS, init?.headers),
  };
}

/**
 * A downloaded file — the `parseAs: 'blob'` case, see {@link HeadlessBlobEnvelope}.
 *
 * `headers` matters here and nowhere else: both download operations derive the
 * save-as name from `content-disposition`, so a stub that omits it exercises the
 * fallback path rather than the header path.
 *
 * ⚠️ **`method` and `path` are not decoration** (2026-09-12). They pin the
 * generics — TypeScript cannot infer an operation from a `Blob` — and the
 * constraint on `P` is where an undeclared operation is refused: a blob stub for
 * an operation this codebase does not blob-parse is now a compile error rather
 * than a payload no server would send. The side effect is the one the rest of
 * this module already buys: every call site names the operation it stands in for.
 */
export function headlessDownload<
  M extends HttpMethod,
  P extends PathsWithMethod<Paths, M> & HeadlessBlobParsedPath,
>(
  method: M,
  path: P,
  blob: Blob,
  headers: Record<string, string> = {},
): HeadlessBlobEnvelope<M, P> {
  void method;
  void path;
  return { data: blob, error: undefined, response: stubResponse(DEFAULT_OK_STATUS, headers) };
}

/** A 2xx that carried no body — see {@link HeadlessEmptyEnvelope}. */
export function headlessEmptyOk(init?: HeadlessResponseInit): HeadlessEmptyEnvelope {
  return {
    data: undefined,
    error: undefined,
    response: stubResponse(init?.status ?? DEFAULT_OK_STATUS, init?.headers),
  };
}

/**
 * A failure envelope for `<method> <path>`, with `error` constrained to that
 * operation's declared error body.
 *
 * The status is explicit rather than derived: an operation declares several
 * error statuses and only the test knows which one it is exercising.
 */
export function headlessProblem<M extends HttpMethod, P extends PathsWithMethod<Paths, M>>(
  method: M,
  path: P,
  status: number,
  error: HeadlessErrorBody<M, P>,
  init?: Omit<HeadlessResponseInit, 'status'>,
): HeadlessErrorEnvelope<M, P> {
  void method;
  void path;
  return {
    data: undefined,
    error,
    response: stubResponse(status || DEFAULT_ERROR_STATUS, init?.headers),
  };
}

/**
 * The RFC 9457 problem document every error response on this surface declares.
 *
 * Reached through one operation rather than through `components` so that nothing
 * outside `src/api/` has to import the generated module directly. Every error
 * response `$ref`s the same schema, so the choice of operation is arbitrary —
 * and if that ever stops being true, this alias stops compiling, which is the
 * loud failure you want.
 */
export type HeadlessProblemDetails = HeadlessErrorBody<'get', '/headless/status'>;

/**
 * A complete problem document.
 *
 * ⚠️ **The suites used to hand-roll `{ title: 'nope', status }`, and that is not
 * a problem document** — the backend declares `type`, `title`, `status`,
 * `detail` **and** `code` as required, and `to-api-error.ts` reads `code` to
 * tell `DRAFT_ROW_CONFLICT` from `PUBLISH_CONFLICT` from `CLAIM_CONFLICT` (all
 * HTTP 409). A stub missing it made every error branch look code-less, so the
 * route was being proven against a failure the server cannot produce.
 *
 * `code` is required here for the same reason it is required on the wire: a
 * default would let a caller keep omitting the one member that carries meaning.
 */
export function problemDetails(
  status: number,
  code: NonNullable<HeadlessProblemDetails>['code'],
  overrides: Partial<NonNullable<HeadlessProblemDetails>> = {},
): HeadlessProblemDetails {
  return {
    type: 'about:blank',
    title: String(code),
    status,
    detail: `stubbed ${String(status)} ${String(code)}`,
    code,
    ...overrides,
  };
}

/**
 * A path this surface serves with `<method>`.
 *
 * Exported so a suite helper that takes an endpoint can say
 * `HeadlessPath<'get'>` instead of `string`. A `string` parameter accepts a
 * retired or mistyped route and then counts zero calls to it forever — which
 * reads as "the screen correctly did not fetch this" and is the quietest way
 * for an assertion to stop asserting.
 *
 * ⚠️ Per-verb aliases (`HeadlessGetPath`, `HeadlessPostPath`) were removed
 * rather than added to the mutation battery: they were pure renames of one
 * instantiation, and the battery's coverage gate correctly refused to let two
 * new exported names sit outside it. Padding the table to satisfy a gate is
 * how a battery stops meaning anything.
 */
export type HeadlessPath<M extends HttpMethod> = PathsWithMethod<Paths, M>;

/**
 * The `init` `openapi-fetch` is called with for `<method> <path>`.
 *
 * ## Why this is imported and not written
 *
 * `openapi-fetch` already derives this from the operation's `parameters` and
 * `requestBody`, and it exports the derivation. Restating it here would be a
 * **second definition of the request contract**, and the copy that drifts is
 * always the fixture — the same argument this module already makes for the
 * response axis, applied to the other direction. `MaybeOptionalInit` is the
 * exact type `ClientMethod` constrains its own `init` parameter with, so a stub
 * that satisfies it is satisfying what the production client is satisfying.
 *
 * It carries the optionality too: an operation with no required parameters and
 * no required body admits `undefined`, which is why several call sites can pass
 * nothing at all.
 *
 * ⚠️ **The typed value is only as true as the caller.** The accessors below
 * hand this type back for a *recorded* call, and `HeadlessStubMethod.init` is
 * `unknown` by design — so a suite that invokes a stub **directly**
 * (`transport.GET(path, anythingAtAll)`) can put a value of any shape, or no
 * value, behind a member the compiler will describe as `number`. Production
 * call sites are typechecked by `openapi-fetch` itself, which is why the
 * premise holds for every real path; a hand-invoked stub is the exception, and
 * it is stated here rather than discovered. `HeadlessRouteHandler` inherits the
 * same limit through `lookupRoute`.
 *
 * ⚠️ **This is the *declared* request, not the serialised one.** `params.query`
 * is the object the caller handed over, not a query string; `body` is the value
 * before `bodySerializer` runs. Checking the wire bytes is a different layer
 * (MSW — recorded as follow-up, deliberately not smuggled in here), and the
 * artifact-level body check is a third (`./request-body-contract`, which asks
 * whether the *server* would accept the body rather than whether the *compiler*
 * agrees with the client). Three questions, three answers; none replaces another.
 */
export type HeadlessRequestInit<
  M extends HttpMethod,
  P extends PathsWithMethod<Paths, M>,
> = M extends keyof Paths[P] ? MaybeOptionalInit<Paths[P], M> : never;

/**
 * A handler a route table entry supplies. It receives the arguments the
 * transport was called with, so a stub can branch on query parameters the way
 * the hand-written chains did.
 *
 * `init` is the operation's own request type: the handler already knows `M` and
 * `P`, so there was never a reason for it to be `unknown`. Measured: no handler
 * in the suite reads `init` today, so this costs nothing to adopt and means the
 * first one that does starts out contract-checked rather than casting.
 */
export type HeadlessRouteHandler<M extends HttpMethod, P extends PathsWithMethod<Paths, M>> = (
  path: P,
  init: HeadlessRequestInit<M, P>,
) => HeadlessEnvelope<M, P> | Promise<HeadlessEnvelope<M, P>>;

/**
 * A path → method → handler table.
 *
 * Keys are constrained to `keyof paths`, so a typo'd or retired route is a
 * compile error rather than a stub that silently never matches.
 */
export type HeadlessRouteTable = {
  [P in keyof Paths]?: {
    [M in HttpMethod & keyof Paths[P]]?: P extends PathsWithMethod<Paths, M>
      ? HeadlessRouteHandler<M, P>
      : never;
  };
};

/**
 * A path → GET success-body map — the shorthand for a suite that only varies
 * bodies and never branches on the request.
 *
 * Each body is checked against **that path's** declared success shape, so this
 * is not a loosening of the route table; it is the same check with the handler
 * boilerplate removed.
 */
export type HeadlessGetBodies = {
  [P in PathsWithMethod<Paths, 'get'>]?: HeadlessOkBody<'get', P>;
};

/**
 * Turn a body map into a route table.
 *
 * ⚠️ **The one erasure, and it is here rather than at the call sites.** Iterating
 * a mapped type loses the key↔value correlation — TypeScript cannot express
 * "for each key, this value is *that* key's body" once the pair is inside an
 * `Object.entries` loop. The check that matters already happened: the caller's
 * literal was validated against {@link HeadlessGetBodies} before it arrived. The
 * alternative is one cast per stub site, which is precisely what this module
 * exists to remove.
 */
export function getBodyRoutes(bodies: HeadlessGetBodies): HeadlessRouteTable {
  const table: Record<string, unknown> = {};
  for (const [path, data] of Object.entries(bodies)) {
    table[path] = {
      get: () => ({ data, error: undefined, response: stubResponse(DEFAULT_OK_STATUS, undefined) }),
    };
  }
  return table;
}
