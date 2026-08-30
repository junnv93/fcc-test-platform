/**
 * The behaviour axis for the **blob-parsing consumption declaration**
 * (`headless-envelope-consumption-axis`, 2026-09-12).
 *
 * ## What is being asserted
 *
 * `headless-client.ts` declares which operations it consumes as a file
 * (`BLOB_PARSED_GET`) and routes every such call through `downloadRequest`,
 * whose path parameter is constrained to that declaration. The claim is that the
 * constraint is *enforced by the compiler* — not that the words are spelled
 * somewhere.
 *
 * ⚠️ **A seal that only inspected the exported type would stay green through the
 * defect it exists to catch.** Loosening `downloadRequest`'s constraint from
 * `HeadlessBlobParsedPath` to `string` leaves `HeadlessBlobParsedPath` itself
 * unchanged and narrow — every probe on the type still passes while any path in
 * the surface can be blob-parsed again. So the negatives below **call the
 * function**. Same lesson the response axis paid for: `any` satisfies a call, and
 * a narrow *type* satisfies an inspection of the type.
 *
 * The file is deliberately not `*.test.ts`: vitest must not collect it (it
 * declares no test cases) while `npm run typecheck` must (tsconfig includes
 * `src`). A file skipped by both would prove nothing while looking like a seal.
 */
import { downloadRequest, toDownload } from './headless-client';

import type { HeadlessApiPaths, HeadlessBlobParsedPath } from './headless-client';
import type { PathsWithMethod } from 'openapi-typescript-helpers';

/* ------------------------------------------------------------------ probes */

/** `true` only when `T` is exactly `never`. */
type IsNever<T> = [T] extends [never] ? true : false;
/** `true` only when `T` is exactly `any`. */
type IsAny<T> = 0 extends 1 & T ? true : false;

// The declaration resolves to a real union: neither the bottom type (which would
// make every download unassemblable) nor `any` (which would admit everything).
const declarationIsNever: IsNever<HeadlessBlobParsedPath> = false;
const declarationIsAny: IsAny<HeadlessBlobParsedPath> = false;

// ⚠️ **A proper subset, and this is the probe that would catch a `satisfies`
// turned into an annotation.** `const BLOB_PARSED_GET: Record<string,
// PathsWithMethod<…>>` widens the values to the whole GET path union, which is
// exactly the widening this axis removes — and every other probe here survives
// it, because the resulting type is still neither `never` nor `any`.
const declarationIsNotEveryGetPath: PathsWithMethod<
  HeadlessApiPaths,
  'get'
> extends HeadlessBlobParsedPath
  ? true
  : false = false;

export const probes = [declarationIsNever, declarationIsAny, declarationIsNotEveryGetPath];

/* --------------------------------------------------- positive controls */

// Both declared download operations assemble, and the assembled request carries
// the literal `parseAs` — read as a member rather than merely produced, because
// a return type widened to `any` also satisfies a bare call.
const sessionExport = downloadRequest('/headless/sessions/{session_id}/results/export', {
  params: { path: { session_id: 1 } },
});
const draftExport = downloadRequest(
  '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/export',
  { params: { path: { project_id: 'p', draft_id: 'd' } } },
);
const sessionParseAs: 'blob' = sessionExport.init.parseAs;
const draftParseAs: 'blob' = draftExport.init.parseAs;
const sessionPath: HeadlessBlobParsedPath = sessionExport.path;

// A blob response converts, and the result is a download rather than `any`.
const converted: string = toDownload('failed', 'fallback.xlsx', {
  data: new Blob(['x']),
  response: new Response(null, { status: 200 }),
}).filename;

export const accepted = [
  sessionExport,
  draftExport,
  sessionParseAs,
  draftParseAs,
  sessionPath,
  converted,
];

/* --------------------------------------------------------- counterexamples */

// A path this client does not consume as a file. This is the assertion that
// fails when the constraint is loosened — and it fails as an *unused directive*,
// which the mutation battery counts as a kill rather than a survivor.
// @ts-expect-error '/headless/jobs' is not a declared blob-parsed operation
const undeclaredPath = downloadRequest('/headless/jobs', {});

// …and a path that is not on the surface at all, so the negative above cannot be
// passing merely because *every* string is refused.
// @ts-expect-error '/nope' is not a path on this surface
const unknownPath = downloadRequest('/nope', {});

// ⚠️ **The other non-JSON operation, named by the compiler rather than by a
// list.** `/headless/reports/outputs/download` declares `application/octet-stream`
// and is *not* consumed through this client — its grant is spent by the browser.
// A schema-derived answer would have admitted it; the consumption axis does not.
// @ts-expect-error the download grant is spent by the browser, not blob-parsed here
const grantPath = downloadRequest('/headless/reports/outputs/download', {});

// The second lock: a JSON operation's body is not a `Blob`, so a response that
// never asked for blob parsing cannot be converted into a download.
//
// ⚠️ The envelopes are bound first because `@ts-expect-error` suppresses the
// error on the line that follows it, and an inline object literal reports on an
// inner line — a directive that lands one line early is an unused directive, not
// an assertion. Measured here: both of these were exactly that on the first pass.
const jsonEnvelope = {
  data: [{ id: 1, status: 'queued', excel_path: 'a.xlsx' }],
  response: new Response(null, { status: 200 }),
};
// @ts-expect-error a job list is not a Blob
const jsonBodyIsNotADownload = toDownload('failed', 'f.xlsx', jsonEnvelope);

// …and the same lock stated the other way: the schema's own `string` body for an
// octet-stream operation is refused too, which is what would arrive if a caller
// dropped `parseAs`.
const unparsedEnvelope = {
  data: 'raw-bytes-as-string',
  response: new Response(null, { status: 200 }),
};
// @ts-expect-error the unparsed body is a string, not a Blob
const stringBodyIsNotADownload = toDownload('failed', 'f.xlsx', unparsedEnvelope);

export const rejected = [
  undeclaredPath,
  unknownPath,
  grantPath,
  jsonBodyIsNotADownload,
  stringBodyIsNotADownload,
];
