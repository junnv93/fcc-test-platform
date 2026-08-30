/**
 * The behaviour axis for the contract-derived transport stubs.
 *
 * ## Why this file is compiled rather than executed
 *
 * The claim `./headless-contract` makes is *"a stub payload that does not match
 * the wire contract is a compile error"*. No test that **runs** can observe
 * that — by the time it runs, the compiler has already had its say. So the
 * assertions here are `@ts-expect-error` directives: each one fails the build if
 * the expression it guards **stops** being an error, which is exactly what would
 * happen if the derivation collapsed to `any`.
 *
 * ⚠️ **Asking whether the types are spelled correctly is not the same question.**
 * A grep for `openapi-typescript-helpers` is satisfied by code that imports the
 * package and then throws the result away. This file asks what the types *do*.
 * The repository has paid three times for seals that checked spelling
 * (`dev-seed` P0, the sqlite census BLOCKING-1); this is the counterexample tree
 * those findings prescribe, kept in the tree rather than in a scratch script.
 *
 * ## Every negative has a positive control
 *
 * A type that collapsed to `never` would reject the bogus payloads too — and
 * "it rejects things" would then certify a type nothing can satisfy. Each
 * negative below is therefore paired with a legitimate payload that must
 * compile, and with an explicit non-`never` / non-`any` probe.
 *
 * The file is deliberately **not** named `*.test.ts`: vitest must not collect it
 * (it declares no test cases), while `npm run typecheck` must (tsconfig includes
 * `tests`). If it were skipped by both, it would be a file that proves nothing
 * while looking like a seal.
 */
import {
  getBodyRoutes,
  headlessDownload,
  headlessEmptyOk,
  headlessOk,
  headlessProblem,
  problemDetails,
} from './headless-contract';
import { headlessRequest, headlessRequests } from './headless-transport';

import type {
  AnyHeadlessEnvelope,
  HeadlessBlobEnvelope,
  HeadlessEnvelope,
  HeadlessErrorBody,
  HeadlessOkBody,
  HeadlessOkEnvelope,
  HeadlessProblemDetails,
  HeadlessRequestInit,
  HeadlessRouteTable,
} from './headless-contract';
import type { HeadlessTransportMocks, HeadlessTransportSpies } from './headless-transport';

/** The spies as `spyHeadlessTransport()` hands them back. Declared rather than
 *  called: installing them needs a test context, and only the *type* is under
 *  examination here. */
declare const spies: HeadlessTransportMocks;
declare const transport: HeadlessTransportSpies;

const JOBS = '/headless/jobs';
/** Reached only through `typeof`, so declared rather than assigned — an unused
 *  runtime binding is a lint error and this one is a pure type anchor. */
type Status = '/headless/status';

/** The two operations `@/api/headless-client` declares as blob-parsed. Assigned
 *  rather than declared because the blob assertions below need them as *values*:
 *  `headlessDownload` names its operation now, and a type alias cannot be passed.
 *  `typeof` recovers the type, so there is still one spelling of each path. */
const EXPORT = '/headless/sessions/{session_id}/results/export';
const DRAFT_EXPORT = '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/export';
type Export = typeof EXPORT;

/* ------------------------------------------------------------------ probes */

/** `true` only when `T` is exactly `never`. */
type IsNever<T> = [T] extends [never] ? true : false;
/** `true` only when `T` is exactly `any` — `any` is the one type that is both a
 *  subtype and a supertype of everything, which is what this distinguishes. */
type IsAny<T> = 0 extends 1 & T ? true : false;

// The derived success body is a real type: neither the "nothing satisfies me"
// bottom nor the "everything satisfies me" escape hatch.
const jobsIsNever: IsNever<HeadlessOkBody<'get', typeof JOBS>> = false;
const jobsIsAny: IsAny<HeadlessOkBody<'get', typeof JOBS>> = false;
const statusIsNever: IsNever<HeadlessOkBody<'get', Status>> = false;
const statusIsAny: IsAny<HeadlessOkBody<'get', Status>> = false;

// The error body likewise. ⚠️ This probe is the one that would have caught the
// `ErrorResponseJSON` defect if it had asked the right question: that helper's
// fallback is not `never` and not `any`, it is the *media map*. So the shape is
// probed too, below.
const errorIsNever: IsNever<HeadlessErrorBody<'get', typeof JOBS>> = false;
const errorIsAny: IsAny<HeadlessErrorBody<'get', typeof JOBS>> = false;

// …and the error body is the problem document itself, not a `{ 'media/type':
// body }` map. Reading `code` off it is what proves that: a media map has no
// such member.
const problemCode: NonNullable<HeadlessProblemDetails>['code'] = 'NOT_FOUND';

// The octet-stream fallback resolves rather than leaving the export
// unstubbable.
const exportBodyIsNever: IsNever<HeadlessOkBody<'get', Export>> = false;

// ⚠️ **Per-verb envelope unions, all five.** The probes above ask about
// *bodies*; nothing asked whether a whole verb's union is inhabited. Measured
// (independent review, 2026-09-10): `AnyHeadlessEnvelope<'patch'>` **is**
// `never` — the surface declares 24 GET, 13 POST, 1 PUT and 1 DELETE operation
// and **no PATCH** — so `spies.PATCH.mockResolvedValue(x)` rejects every `x`.
//
// The spy is kept because it mirrors the openapi-fetch client's shape, and the
// emptiness is asserted rather than tolerated: the day a PATCH operation lands,
// this line goes red and someone reads this comment instead of rediscovering it.
const getIsInhabited: IsNever<AnyHeadlessEnvelope<'get'>> = false;
const postIsInhabited: IsNever<AnyHeadlessEnvelope<'post'>> = false;
const putIsInhabited: IsNever<AnyHeadlessEnvelope<'put'>> = false;
const deleteIsInhabited: IsNever<AnyHeadlessEnvelope<'delete'>> = false;
const patchIsUninhabitedToday: IsNever<AnyHeadlessEnvelope<'patch'>> = true;

export const verbProbes = [
  getIsInhabited,
  postIsInhabited,
  putIsInhabited,
  deleteIsInhabited,
  patchIsUninhabitedToday,
];

export const probes = [
  jobsIsNever,
  jobsIsAny,
  statusIsNever,
  statusIsAny,
  errorIsNever,
  errorIsAny,
  problemCode,
  exportBodyIsNever,
];

/* --------------------------------------------------- positive controls */

const okJobs = headlessOk('get', JOBS, [{ id: 1, status: 'queued', excel_path: 'a.xlsx' }]);
const okProblem = headlessProblem('get', JOBS, 503, problemDetails(503, 'UPSTREAM_UNAVAILABLE'));
const okEmpty = headlessEmptyOk();
const okBlob = headlessDownload('get', EXPORT, new Blob(['x']), {
  'content-disposition': 'attachment',
});
const okTable: HeadlessRouteTable = {
  [JOBS]: { get: () => headlessOk('get', JOBS, []) },
};
const okBodies = getBodyRoutes({ [JOBS]: [] });

// ⚠️ `response` is a real `Response`, and this is what holds it there. Both
// download operations read `response?.headers.get('content-disposition')` to
// name the saved file, so a `{ status: number }` literal satisfies the *other*
// consumer and fails this one silently. Measured: without this line, weakening
// the member survived every other assertion in the file.
// ⚠️ **Every envelope kind, not one of them.** The union has four members and
// weakening the member on *one* of them is invisible to a probe that happens to
// read a different one — measured: the first version of this probe read only the
// blob envelope and the mutation on `HeadlessOkEnvelope` survived it.
const okDataResponse: Response = okJobs.response;
const okErrorResponse: Response = okProblem.response;
const okEmptyResponse: Response = okEmpty.response;
const okBlobResponse: Response = okBlob.response;
const downloadFilename: string | null = okBlob.response.headers.get('content-disposition');
const okStatus: number = okProblem.response.status;

export const accepted = [
  okJobs,
  okProblem,
  okEmpty,
  okBlob,
  okTable,
  okBodies,
  okDataResponse,
  okErrorResponse,
  okEmptyResponse,
  okBlobResponse,
  downloadFilename,
  okStatus,
];

/* --------------------------------------------------------- counterexamples */

// A success payload that matches no member of the operation's declared body.
// @ts-expect-error a job list is not a { bogus } object
const wrongOkBody = headlessOk('get', JOBS, { bogus: true });

// A job missing members the contract declares required (`status`, `excel_path`).
// @ts-expect-error a partial job is not a job
const partialOkBody = headlessOk('get', JOBS, [{ id: 1 }]);

// A path that is not on this surface at all.
// @ts-expect-error '/nope' is not a key of the generated paths
const wrongPath = headlessOk('get', '/nope', []);

// A method the operation does not declare.
// @ts-expect-error /headless/jobs has no PUT
const wrongMethod = headlessOk('put', JOBS, []);

// The pre-migration error shape: `{ title, status }` is not a problem document —
// `type`, `detail` and `code` are required and `code` is what the FE branches on.
// @ts-expect-error an incomplete problem document is not a problem document
const wrongErrorBody = headlessProblem('get', JOBS, 404, { title: 'nope', status: 404 });

// A route table keyed by a path the surface does not have.
const wrongTableKey: HeadlessRouteTable = {
  // @ts-expect-error '/nope' is not a key of the generated paths
  '/nope': { get: () => headlessEmptyOk() },
};

// A body map whose payload belongs to a different operation.
// @ts-expect-error the status snapshot is not a job list
const wrongBodyMap = getBodyRoutes({ [JOBS]: { measurement_jobs: {}, workers: [] } });

// The stub-side signature itself. ⚠️ Without this the whole
// `HeadlessStubMethod` axis is unguarded: loosening its return type to `any`
// was measured to fail the build only *incidentally* (an unused generic
// parameter), which is a survivor wearing a kill's clothes.
//
// The payload is bound first because `@ts-expect-error` suppresses the error on
// the line that follows it, and an inline object literal reports on an inner
// line — a directive that lands one line early is an unused directive, not an
// assertion.
const bogusEnvelope = { data: { bogus: 1 }, error: undefined, response: new Response() };
// @ts-expect-error a bogus payload is not any operation's envelope
const wrongSpyPayload = spies.GET.mockResolvedValue(bogusEnvelope);

// An error code that is not in the published enum. This is what pins the
// `problemDetails` code parameter — typing it `any` was measured to survive
// every other assertion in this file.
// @ts-expect-error 'NOT_A_REAL_CODE' is not an ErrorCode
const wrongErrorCode = problemDetails(500, 'NOT_A_REAL_CODE');

// The error body reached through the derived type rather than the builder — the
// axis that catches `HeadlessErrorBody` collapsing to `any`.
// @ts-expect-error the problem document has no `bogus` member
const wrongErrorShape: HeadlessErrorBody<'get', typeof JOBS> = { bogus: 1 };

/* ------------------------------------------- the consumption axis (2026-09-12)
 *
 * The blob member used to ride on *every* operation, and the line that stood
 * here asserted it — `knownWidenings`, kept so a future narrowing would show up
 * as a failure to fix. This is that narrowing, so the assertion inverts.
 *
 * What changed is where the fact lives: `parseAs: 'blob'` is a decision this
 * codebase makes, so it is declared once (`BLOB_PARSED_GET` in
 * `@/api/headless-client`) and consumed by the only `parseAs` in `src/`. The
 * envelope derives from that declaration and restates none of it.
 */

// ⚠️ **The conditional itself, isolated — and the in-tree battery is why this
// line exists.** Every other negative here is refused for a *different* reason:
// `headlessDownload` will not build a blob for `/headless/jobs` (the declaration
// constrains it), and a blob branded to the export operation is refused by
// `OperationBrand` whatever the union admits. So with only those, restoring the
// widening (`| HeadlessBlobEnvelope<M, P>`, unconditional) produced **zero**
// diagnostics — measured, `HeadlessEnvelopeBlobUnconditional` SURVIVED.
//
// The blob envelope branded to *this same JSON operation* is the one payload
// that isolates the conditional: the brand agrees, the builder is bypassed, and
// only the union's blob member decides. Reached through `declare` because no
// builder will make one — which is the point.
declare const jobsBlob: HeadlessBlobEnvelope<'get', typeof JOBS>;
// @ts-expect-error '/headless/jobs' is not consumed as a file, so no blob envelope belongs to it
const jsonOperationHasNoBlobMember: HeadlessEnvelope<'get', typeof JOBS> = jobsBlob;

// …and the same through a route table, which is the realistic form.
const jsonOperationRejectsABlobRoute: HeadlessRouteTable = {
  // @ts-expect-error '/headless/jobs' is not consumed as a file
  [JOBS]: { get: () => jobsBlob },
};

// The builder-side refusal, kept because it is a *different* lock (the
// declaration constrains what can be built, not only what can be answered).
//
// ⚠️ The directive sits on the *property* line, not on the `const`: an object
// literal reports its mismatch on the inner member, and a directive one line
// early is an unused directive rather than an assertion (measured here, twice).
const jsonOperationRejectsAForeignBlob: HeadlessRouteTable = {
  // @ts-expect-error a session-export blob is not the jobs operation's envelope
  [JOBS]: { get: () => headlessDownload('get', EXPORT, new Blob(['x'])) },
};

// …and `headlessDownload` refuses to build one for it in the first place, so the
// negative above is not passing merely because the route table is strict.
// @ts-expect-error '/headless/jobs' is not a declared blob-parsed operation
const undeclaredDownload = headlessDownload('get', JOBS, new Blob(['x']));

// ⚠️ **Positive control, and it is what keeps the narrowing from being a
// collapse.** A conditional that resolved to `never` for every path would reject
// the two negatives above just as well — and would certify a type no download
// stub can satisfy. Both declared operations must still route.
const declaredDownloadsRoute: HeadlessRouteTable = {
  [EXPORT]: { get: () => headlessDownload('get', EXPORT, new Blob(['x'])) },
  [DRAFT_EXPORT]: { get: () => headlessDownload('get', DRAFT_EXPORT, new Blob(['x'])) },
};

// ⚠️ **The path axis for the blob member.** Gating is not scoping: both export
// paths pass the same conditional, so without `OperationBrand` a session-export
// blob answers a draft-export route and the table checks the key while the
// handler answers the other operation. This is the assertion that keeps the
// brand on this envelope.
const launderedDownload: HeadlessRouteTable = {
  // @ts-expect-error the handler must answer *this* path's operation
  [DRAFT_EXPORT]: { get: () => headlessDownload('get', EXPORT, new Blob(['x'])) },
};

export const consumptionAxis = [
  jsonOperationHasNoBlobMember,
  jsonOperationRejectsABlobRoute,
  jsonOperationRejectsAForeignBlob,
  undeclaredDownload,
  declaredDownloadsRoute,
  launderedDownload,
];

// ⚠️ **The breadth that remains, and it is a judgement rather than an oversight.**
// The empty envelope still rides on every operation. That is not the same fact as
// the blob member: emptiness is decided by the wire (`openapi-fetch` answers
// `{ data: undefined }` for any 204 / `Content-Length: 0`, whatever the schema
// declares), so there is nothing here to derive it from — and the schema-derived
// set would be *empty*, since no operation on this surface declares a
// content-less 2xx. Asserted rather than commented, so a future narrowing shows
// up here; the *reason* is held by a running test
// (`tests/headless-empty-body-fact.test.ts`), because a paragraph cannot go red.
const jsonOperationAcceptsAnEmptyBody: HeadlessRouteTable = {
  [JOBS]: { get: () => headlessEmptyOk() },
};
export const knownBreadth = [jsonOperationAcceptsAnEmptyBody];

// ⚠️ **The path axis, envelope-to-envelope.** Every other negative in this file
// fails at the *builder call*, which is why the unsoundness below survived until
// an independent review looked for it: `HeadlessOkEnvelope<M,P>` reached `P`
// only through a deferred conditional, so TypeScript measured `P` as
// independent and related any two instantiations. `OperationBrand` fixes that;
// these two assertions are what keep it fixed.
declare const statusEnvelope: HeadlessOkEnvelope<'get', Status>;
// @ts-expect-error a status envelope is not a jobs envelope
const launderedEnvelope: HeadlessOkEnvelope<'get', typeof JOBS> = statusEnvelope;

// …and the same laundering through a route table, which is the realistic form:
// the key is `/headless/jobs` and the handler answers the status body.
const launderingTable: HeadlessRouteTable = {
  // @ts-expect-error the handler must answer *this* path's operation
  [JOBS]: { get: () => statusEnvelope },
};

// ⚠️ **The two builders whose *return types* nothing pinned.** The in-tree
// battery (`scripts/verify-stub-typing-mutations.mjs`) found that widening
// either of these to `any` survived every other assertion in this file: the
// positive controls only ever *called* them, and `any` satisfies a call.
// A negative is needed, and it has to read a member the real type refuses.
// @ts-expect-error the empty envelope carries no data
const emptyHasNoData: number = headlessEmptyOk().data;
// @ts-expect-error the download envelope answers a Blob, not a string
const downloadIsNotAString: string = headlessDownload('get', EXPORT, new Blob(['x'])).data;

// …and the unrouted mode, for the same reason: widening it to `string` made a
// typo legal, and nothing objected.
// @ts-expect-error 'quiet' is not one of the two unrouted modes
const bogusUnroutedMode = transport.routes({}, { unrouted: 'quiet' });

export const rejected = [
  emptyHasNoData,
  downloadIsNotAString,
  bogusUnroutedMode,
  launderedEnvelope,
  launderingTable,
  wrongSpyPayload,
  wrongErrorCode,
  wrongErrorShape,
  wrongOkBody,
  partialOkBody,
  wrongPath,
  wrongMethod,
  wrongErrorBody,
  wrongTableKey,
  wrongBodyMap,
];

/* ---------------------------------------------- the request axis (2026-09-11)
 *
 * Everything above asks about the **response**. `stub-request-axis-typing` adds
 * the other direction: `HeadlessRequestInit` and the two accessors that expose
 * it. Same discipline — every negative gets a positive control, and the
 * positive controls **read a member** rather than merely calling the function,
 * because `any` satisfies a call.
 */

const ATTEMPTS = '/headless/sessions/{session_id}/attempts';
const GENERATIONS = '/headless/projects/{project_id}/test-plan/generations';

// The derived request is a real type in all three shapes it takes on this
// surface: path parameters, a required body, and no parameters at all.
const attemptsInitIsNever: IsNever<HeadlessRequestInit<'get', typeof ATTEMPTS>> = false;
const attemptsInitIsAny: IsAny<HeadlessRequestInit<'get', typeof ATTEMPTS>> = false;
const generationsInitIsNever: IsNever<HeadlessRequestInit<'post', typeof GENERATIONS>> = false;
const generationsInitIsAny: IsAny<HeadlessRequestInit<'post', typeof GENERATIONS>> = false;
// ⚠️ The no-parameter case admits `undefined` — that is the contract, not a
// hole, and it is why several production call sites pass nothing. Asserting it
// keeps a future narrowing from silently breaking them.
const jobsInitIsAny: IsAny<HeadlessRequestInit<'get', typeof JOBS>> = false;
const jobsInitAdmitsUndefined: undefined extends HeadlessRequestInit<'get', typeof JOBS>
  ? true
  : false = true;

export const requestProbes = [
  attemptsInitIsNever,
  attemptsInitIsAny,
  generationsInitIsNever,
  generationsInitIsAny,
  jobsInitIsAny,
  jobsInitAdmitsUndefined,
];

/* --------------------------------------- request positive controls */

// ⚠️ **Reads a member.** `headlessRequest(...).params.path.session_id` is the
// assertion: a return type widened to `any` also satisfies a bare call, so a
// positive control that only calls proves nothing. This is the shape the
// in-tree battery found surviving on the response axis.
const attemptsSessionId: number = headlessRequest(spies.GET, 'get', ATTEMPTS).params.path
  .session_id;
const attemptsCursor: string | undefined = headlessRequest(spies.GET, 'get', ATTEMPTS).params.query
  ?.cursor;
const everyAttemptsRequest: number[] = headlessRequests(spies.GET, 'get', ATTEMPTS).map(
  (init) => init.params.path.session_id,
);

export const requestControls = [attemptsSessionId, attemptsCursor, everyAttemptsRequest];

/* --------------------------------------- request counterexamples */

// A parameter the operation does not declare. This is the failure the hand
// casts could not have: `as { params: { path: { sessionId: number } } }` was
// always accepted, so a client that sent the wrong key stayed green.
//
// ⚠️ Stated as a **whole-object mismatch** rather than by reading `.sessionId`.
// Accessing a member that does not exist produces an *error type*, which
// `@typescript-eslint/no-unsafe-assignment` then reports — and silencing that
// with a disable comment would have hidden a real category of lint. The
// structural form asks the same question and is stronger: it fails if `path`
// gains the member as well as if it never had it.
// @ts-expect-error the path parameter is `session_id`, not `sessionId`
const wrongParamName: { sessionId: number } = headlessRequest(spies.GET, 'get', ATTEMPTS).params
  .path;

// …and the right name with the wrong type.
// @ts-expect-error `session_id` is a number
const wrongParamType: string = headlessRequest(spies.GET, 'get', ATTEMPTS).params.path.session_id;

// ⚠️ **Where the "absent" members bite, measured rather than assumed.**
//
// Two counterexamples in a first draft came back as *unused* directives, and
// the type was right both times — the tests were wrong. `openapi-typescript`
// and `openapi-fetch` model what an operation does **not** declare with a
// `never` slot rather than by omitting the member: the generated schema carries
// `requestBody?: never` for a body-less operation and `header?: never` for one
// with no header parameters. ⚠️ The **resolved init member** is then
// `undefined`, not `never` — an optional `never` property reads as `undefined`,
// and conflating the two is what an independent review had to correct here.
// Either way, **reading** such a member always compiles.
//
// That is worth pinning rather than deleting, because it says exactly what this
// axis can and cannot catch: a client that *sends* a header to an operation
// that declares none is a compile error, while a test that merely *mentions*
// `params.header` is not.
//
// ⚠️ **The `@ts-expect-error` lines are the load-bearing half; do not delete
// them as redundant.** An earlier version of this comment claimed the plain
// assignments would also fail on an `any` slot "because an `any` is not
// assignable to `undefined`". That is **false** — `any` is assignable to every
// type except `never`, and an independent review proved it by guarding such an
// assignment with `@ts-expect-error` and getting `TS2578: Unused directive`.
// Only the rejections below distinguish a correctly-`undefined` slot from an
// `any` one. The plain assignments still earn their place: they pin the slot as
// `undefined` rather than, say, `never` or a real type.
const attemptsHeaderIsUnusable: undefined = headlessRequest(spies.GET, 'get', ATTEMPTS).params
  .header;
const attemptsBodyIsUnusable: undefined = headlessRequest(spies.GET, 'get', ATTEMPTS).body;
// @ts-expect-error a body-less operation admits no body value
const wrongInitMember: object = headlessRequest(spies.GET, 'get', ATTEMPTS).body;
// @ts-expect-error the attempts operation declares no header parameters
const wrongParamLocation: object = headlessRequest(spies.GET, 'get', ATTEMPTS).params.header;

// ⚠️ **The plural accessor needs its own negative, and the battery proved it.**
// Widening `headlessRequests` to `any[]` survived every line above: the
// positive control maps over the result and assigns to `number[]`, and `any[]`
// is assignable to `number[]`. `any` satisfies a *use*; only a rejection can
// distinguish it. This one fails the build the moment the element stops being
// this operation's request.
// @ts-expect-error the elements are requests, not strings
const wrongElementType: string[] = headlessRequests(spies.GET, 'get', ATTEMPTS);

// The path must be one this verb actually serves — the same check the route
// table gets, now on the accessor.
// @ts-expect-error `/headless/jobs` has no DELETE operation
const wrongVerbForPath = headlessRequests(spies.DELETE, 'delete', JOBS);

// …and the method argument must agree with the mock it was handed. Reading a
// GET-only path off the POST spy must not typecheck.
// @ts-expect-error the generations operation is POST, not GET
const wrongMethodForMock = headlessRequests(spies.GET, 'get', GENERATIONS);

// A stub called with a path that is not on the contract at all. The module
// docstring claimed this was checked long before the type said so.
// @ts-expect-error '/headless/nope' is not a path on this surface
const wrongStubPath = spies.GET('/headless/nope');

// A route handler's `init` is the operation's request, not `unknown`. Before
// this wave the handler could read anything off it.
//
// ⚠️ **A negative alone would be vacuous here, and the first draft was.** If
// `init` were still `unknown`, *every* member access on it would be an error —
// so a lone `@ts-expect-error` stays "used" whether the type is right or
// missing entirely, and certifies nothing. The positive line is what
// distinguishes them: it only compiles when `init` really is this operation's
// request. The negative then shows the type is not `any` either.
const handlerInitTable: HeadlessRouteTable = {
  [ATTEMPTS]: {
    get: (_path, init) => {
      const session: number = init.params.path.session_id;
      // @ts-expect-error `session_id` is a number, so this handler's `init` is
      // neither `unknown` (which would reject the line above) nor `any`.
      const notAString: string = init.params.path.session_id;
      void session;
      void notAString;
      return headlessOk('get', ATTEMPTS, { items: [], next_cursor: null });
    },
  },
};

export const requestRejected = [
  wrongElementType,
  wrongParamName,
  wrongParamLocation,
  attemptsBodyIsUnusable,
  attemptsHeaderIsUnusable,
  wrongParamType,
  wrongInitMember,
  wrongVerbForPath,
  wrongMethodForMock,
  wrongStubPath,
  handlerInitTable,
];
