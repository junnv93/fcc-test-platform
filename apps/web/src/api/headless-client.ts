import createClient from 'openapi-fetch';

import { getRuntimeConfig } from '@/config/runtime';
import { filenameFromContentDisposition } from '@/shared/content-disposition';

import { authRetryMiddleware } from './auth-middleware';
import { apiErrorFromResponse } from './to-api-error';

import type { components, paths as HeadlessPaths } from './generated/headless-api.types';
import type { PathsWithMethod } from 'openapi-typescript-helpers';

/**
 * Headless / Platform API client — backend `headless-api.openapi.json`
 * (FE-P1 SSOT, 2026-05-25).
 *
 * Types come from `npm run codegen` (ADR-0003 — openapi-typescript). The
 * generated file is gitignored; CI runs `npm run codegen:check` so a backend
 * OpenAPI change without a frontend type bump fails.
 *
 * AuthZ: the shared `authRetryMiddleware` (`./auth-middleware`, identical to
 * `session-client.ts`) attaches the `Authorization` bearer token the backend's
 * trusted-header resolver consumes and performs a single silent-refresh + replay
 * on a 401 (Increment 6). The header is omitted when unauthenticated — the
 * backend responds 401/403 in that case and view code handles the failure.
 *
 * Path distinction (preserved across the contract → OpenAPI → TS chain):
 * - POST `/headless/sessions/{session_id}/reports` — create a report-automation
 *   request (operationId `submit_report_request`).
 * - GET  `/headless/reports/{request_id}/outputs` — list generated outputs for
 *   an existing request (operationId `list_report_outputs`).
 */

const baseUrl = (): string => getRuntimeConfig().apiBaseUrl;

export const headlessClient = createClient<HeadlessPaths>({
  baseUrl: baseUrl(),
  headers: {
    Accept: 'application/json',
  },
});
headlessClient.use(authRetryMiddleware);

export function createHeadlessClientForBaseUrl(nodeBaseUrl: string) {
  const client = createClient<HeadlessPaths>({
    baseUrl: nodeBaseUrl,
    headers: {
      Accept: 'application/json',
    },
  });
  client.use(authRetryMiddleware);
  return client;
}

/**
 * Re-export of the generated types so callers do not import the autogen
 * path directly (decouples view code from the generator filename).
 */
export type HeadlessApiPaths = HeadlessPaths;

/** The shape both `headlessClient` and a per-node client (`createHeadlessClientForBaseUrl`)
 *  share — the type node-scoped operations accept so they work with either. */
export type HeadlessClient = typeof headlessClient;

/** A downloaded file the caller turns into a browser save prompt. `parseAs: 'blob'` and the
 *  filename derivation are transport concerns, so operations that return one own both. */
export interface HeadlessDownload {
  readonly blob: Blob;
  readonly filename: string;
}

/**
 * ── The blob-parsing consumption axis (2026-09-12) ───────────────────────────
 *
 * **Which operations this client consumes as a file rather than as JSON.**
 *
 * ## Why this is a declaration and not a schema lookup
 *
 * `parseAs: 'blob'` is a **call-site** decision. The schema says what the server
 * sends (`application/vnd…spreadsheetml.sheet`); it does not say that *we* ask
 * `openapi-fetch` to hand us a `Blob` instead of the `string` the generated type
 * declares. The two facts point the same way here but they are different facts,
 * and the reverse is measurable: `/headless/reports/outputs/download` also
 * declares a non-JSON body and is **not** consumed through this client at all —
 * its grant is spent by the browser. A schema-derived answer would be wrong
 * about it.
 *
 * ## Why the declaration is *used* rather than merely written
 *
 * A list nothing reads is a second definition of the contract, and the copy that
 * drifts is always the one without a consumer. So {@link downloadRequest} —
 * the **only** place `parseAs` appears in `src/` — constrains its path parameter
 * to this map's values, and {@link toDownload} refuses anything but a `Blob`.
 *
 * ⚠️ **Those two locks are not the whole answer, and an earlier version of this
 * paragraph claimed they were.** It said a download *"cannot be assembled"* any
 * other way. An independent adversarial review falsified that by compiling
 * three of them (2026-09-12), and the most plausible one writes no `parseAs` at
 * all — `await response.blob()`, which is exactly what
 * `shared/signed-download.ts` already does for its signed-URL download. `parseAs`
 * is *one* way to ask for a file; the type locks only see that one.
 *
 * What closes it is asking a different question in a different place:
 * `TestBlobParsingIsADeclaredConsumptionAxis` requires every operation **declared
 * to return {@link HeadlessDownload}** to pass through both seams, and separately
 * censuses the calls that materialise a response body anywhere in `src/`. Those
 * are derived, so a new download joins by existing rather than by remembering.
 *
 * The stub envelopes derive their blob member from {@link HeadlessBlobParsedPath}
 * rather than admitting a `Blob` for every operation — `tests/helpers/
 * headless-contract.ts`, which restates none of these strings.
 *
 * ⚠️ **`satisfies`, not a type annotation.** An annotation would widen the values
 * to `PathsWithMethod<…>` and the derived union below would become *every* GET
 * path — the widening this axis exists to remove, reintroduced silently.
 */
const BLOB_PARSED_GET = {
  sessionResultsExport: '/headless/sessions/{session_id}/results/export',
  testPlanDraftExport: '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/export',
} as const satisfies Record<string, PathsWithMethod<HeadlessPaths, 'get'>>;

/** A GET operation this client consumes as a `Blob` — see {@link BLOB_PARSED_GET}. */
export type HeadlessBlobParsedPath = (typeof BLOB_PARSED_GET)[keyof typeof BLOB_PARSED_GET];

/**
 * Assemble a download request: the declared path plus the one `parseAs` in `src/`.
 *
 * ⚠️ **The request is assembled here and *sent* at the call site**, which looks
 * like half a helper until you try the other shape. A generic
 * `getAsDownload<P>(path, init)` that also sends does not compile: `openapi-fetch`
 * types its second argument `...init: InitParam<Init>`, and `InitParam` is
 * `RequiredKeysOf<Init> extends never ? [init?: Init] : [init: Init]` — with `P`
 * still generic the tuple never resolves and the call is `TS2345` (measured, not
 * assumed). Sending from a site where the path is a literal keeps every response
 * type exact and costs zero casts, which is the trade this module is here to make.
 *
 * ⚠️ **Exported for the counterexample tree, not for callers.** The constraint
 * on `P` is the lock; a seal that only reads the *type* `HeadlessBlobParsedPath`
 * stays green when the constraint is loosened to `string`, so the counterexample
 * has to call this function. `headless-client.type-test.ts` is its only importer
 * outside this module, and the structural gate asserts that stays true.
 */
export function downloadRequest<P extends HeadlessBlobParsedPath, I extends object>(
  path: P,
  init: I,
) {
  return { path, init: { ...init, parseAs: 'blob' as const } };
}

/**
 * Turn a resolved blob response into a {@link HeadlessDownload}.
 *
 * ⚠️ **`data?: Blob` is load-bearing, not descriptive.** A JSON operation's
 * envelope carries an object in `data`, which is not assignable here — so
 * routing a non-blob-parsed response through this function is a compile error.
 * That is the second lock referenced in {@link BLOB_PARSED_GET}; widening it to
 * `unknown` removes it, which is what `headless-client.type-test.ts` asserts.
 *
 * `fallbackFilename` is the service's own default, so a header-less response
 * still lands on the file the backend would have named.
 *
 * ⚠️ Exported on the same terms as {@link downloadRequest} — for the
 * counterexample tree, with the structural gate holding the importer set to it.
 */
export function toDownload(
  failureMessage: string,
  fallbackFilename: string,
  result: {
    readonly data?: Blob | undefined;
    readonly error?: unknown;
    readonly response: Response;
  },
): HeadlessDownload {
  const { data, error, response } = result;
  if (error || data === undefined) {
    throw apiErrorFromResponse(failureMessage, { error, response });
  }
  return {
    blob: data,
    filename: filenameFromContentDisposition(
      response.headers.get('content-disposition') ?? null,
      fallbackFilename,
    ),
  };
}

// Response types, derived from the generated schema components — never hand-typed
// (session-client.ts / platform-client.ts precedent). Operations land in M1–M3;
// these aliases let call sites be typed without reaching into `components['schemas']`.
export type ListPublishedTestPlansResponse =
  components['schemas']['ListPublishedTestPlansResponse'];
export type MeasurementAttemptPage = components['schemas']['MeasurementAttemptPage'];
export type MeasurementJobList = components['schemas']['MeasurementJobList'];
export type StopMeasurementJobResponse = components['schemas']['StopMeasurementJobResponse'];
export type TestPlanGenerationCatalogueResponse =
  components['schemas']['TestPlanGenerationCatalogueResponse'];
export type TestPlanGenerationPreviewResponse =
  components['schemas']['TestPlanGenerationPreviewResponse'];
export type TestPlanGenerationSubmittedResponse =
  components['schemas']['TestPlanGenerationSubmittedResponse'];
export type TestPlanGenerationJobResponse = components['schemas']['TestPlanGenerationJobResponse'];
export type TestPlanGenerationMetadataResponse =
  components['schemas']['TestPlanGenerationMetadataResponse'];
export type TestPlanGenerationRowPageResponse =
  components['schemas']['TestPlanGenerationRowPageResponse'];
export type TestPlanDraftRowView = components['schemas']['TestPlanDraftRowView'];
export type TestPlanDraftView = components['schemas']['TestPlanDraftView'];
export type RemoveTestPlanDraftRowResponse =
  components['schemas']['RemoveTestPlanDraftRowResponse'];
export type ReplaceTestPlanDraftRowsResponse =
  components['schemas']['ReplaceTestPlanDraftRowsResponse'];
export type ListTestPlanDraftsResponse = components['schemas']['ListTestPlanDraftsResponse'];
export type ValidateTestPlanDraftResponse = components['schemas']['ValidateTestPlanDraftResponse'];
export type PublishedTestPlanView = components['schemas']['PublishedTestPlanView'];
export type ReportPreflightSummary = components['schemas']['ReportPreflightSummary'];
export type ReportRequestSubmitted = components['schemas']['ReportRequestSubmitted'];
export type ReportRequestSnapshot = components['schemas']['ReportRequestSnapshot'];
export type CancelReportAutomationResponse =
  components['schemas']['CancelReportAutomationResponse'];
export type ReportAutomationQueueStats = components['schemas']['ReportAutomationQueueStats'];
export type ReportOutputMetadataList = components['schemas']['ReportOutputMetadataList'];
export type ReportOutputDownloadGrant = components['schemas']['ReportOutputDownloadGrant'];
export type ArtifactMetadataList = components['schemas']['ArtifactMetadataList'];
export type TestPlanImportResponse = components['schemas']['TestPlanImportResponse'];
export type HeadlessBackendStatusSnapshot = components['schemas']['HeadlessBackendStatusSnapshot'];

/**
 * ── Why the headless calls live here and not in the routes (2026-08-19) ──────
 *
 * `platform-client.ts` (48 helpers) and `session-client.ts` (5) already settled
 * this shape; this surface was the one that had **none**, so 13 route files
 * owned HTTP verbs, path templates and parameter shapes at **30 call sites**.
 * A backend path rename was 30 edits, and "which route calls which operation"
 * was answerable only by grep.
 *
 * The proposition here is **transport hiding**, not information loss — every one
 * of those 13 routes already threw through `apiErrorFromResponse`, so no failure
 * was losing its `problemCode`. What was missing is that a route could still
 * *choose* to talk HTTP. Now it cannot: the verb/path literals exist only in
 * this file.
 *
 * ⚠️ Post-processing that is genuinely the view's stays in the view. A download
 * operation returns {@link HeadlessDownload} — the blob plus the filename the
 * response named — because `parseAs: 'blob'` and `content-disposition` are
 * transport facts the caller can no longer see; but creating the object URL and
 * clicking the anchor remain in the route, where they belong.
 */

/** GET `/headless/status` — aggregate queue/worker snapshot. */
export async function fetchHeadlessStatus(): Promise<HeadlessBackendStatusSnapshot | undefined> {
  const { data, error, response } = await headlessClient.GET('/headless/status', {});
  if (error) {
    throw apiErrorFromResponse('job status request failed', { error, response });
  }
  return data;
}

/**
 * GET `/headless/jobs` — the measurement job list.
 *
 * The `?? []` is not defensive padding: openapi-fetch types a 2xx body as
 * possibly `undefined`, and every caller flat-maps the result. Keeping the
 * fallback with the operation is what stops each route from re-deciding it.
 */
export async function fetchHeadlessJobs(): Promise<MeasurementJobList> {
  const { data, error, response } = await headlessClient.GET('/headless/jobs', {});
  if (error) {
    throw apiErrorFromResponse('job list request failed', { error, response });
  }
  return data ?? [];
}

/** POST `/headless/jobs/{job_id}/stop` — request that a running job halt. */
export async function stopHeadlessJob(jobId: number): Promise<StopMeasurementJobResponse> {
  const { data, error, response } = await headlessClient.POST('/headless/jobs/{job_id}/stop', {
    params: { path: { job_id: jobId } },
    body: { message: '' },
  });
  if (error || !data) {
    throw apiErrorFromResponse('job stop failed', { error, response });
  }
  return data;
}

/**
 * GET `/headless/sessions/{session_id}/attempts` — one keyset page (FE-P4-PAGE).
 *
 * `cursor` is the opaque `next_cursor` echoed from the prior page; omitted for
 * the first page so the request shape stays `{ params: { path } }` with no empty
 * query object. The `?? { items: [], next_cursor: null }` fallback keeps callers
 * (`pages.flatMap` / `getNextPageParam`) from ever seeing `undefined`.
 */
export async function fetchSessionAttempts(
  sessionId: number,
  cursor?: string,
): Promise<MeasurementAttemptPage> {
  const query = cursor === undefined ? undefined : { cursor };
  const { data, error, response } = await headlessClient.GET(
    '/headless/sessions/{session_id}/attempts',
    { params: { path: { session_id: sessionId }, ...(query ? { query } : {}) } },
  );
  if (error) {
    throw apiErrorFromResponse('attempt history lookup failed', { error, response });
  }
  return data ?? { items: [], next_cursor: null };
}

/**
 * GET `/headless/sessions/{session_id}/results/export` — the measurement-result
 * workbook.
 *
 * Returns the blob **and the filename the response named**, because
 * `parseAs: 'blob'` and the `content-disposition` header are transport facts the
 * caller can no longer observe. `fallbackFilename` is the service's own default,
 * so a header-less response still lands on the file the backend would have named.
 * Turning the blob into a download stays in the view.
 */
export async function exportSessionResults(
  sessionId: number,
  fallbackFilename: string,
): Promise<HeadlessDownload> {
  const request = downloadRequest(BLOB_PARSED_GET.sessionResultsExport, {
    params: { path: { session_id: sessionId } },
  });
  return toDownload(
    'session result export failed',
    fallbackFilename,
    await headlessClient.GET(request.path, request.init),
  );
}

/** GET `/headless/projects/{project_id}/test-plan/publications` — published plans for a project. */
export async function fetchPublishedTestPlans(
  projectId: string,
): Promise<ListPublishedTestPlansResponse> {
  const { data, error, response } = await headlessClient.GET(
    '/headless/projects/{project_id}/test-plan/publications',
    { params: { path: { project_id: projectId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('published-plan list lookup failed', { error, response });
  }
  return data;
}

/** Request bodies, derived from the generated schema (never hand-typed). */
export type AddTestPlanDraftRowRequest = components['schemas']['AddTestPlanDraftRowRequest'];

/** POST `…/drafts/{draft_id}/rows` — append one row to a draft. */
export async function addTestPlanDraftRow(
  projectId: string,
  draftId: string,
  body: AddTestPlanDraftRowRequest,
): Promise<TestPlanDraftRowView> {
  const { data, error, response } = await headlessClient.POST(
    '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/rows',
    { params: { path: { project_id: projectId, draft_id: draftId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('draft row add failed', { error, response });
  }
  return data;
}

/** PUT `…/drafts/{draft_id}/rows` — replace the draft's rows wholesale. */
export async function replaceTestPlanDraftRows(
  projectId: string,
  draftId: string,
  rows: readonly AddTestPlanDraftRowRequest[],
): Promise<ReplaceTestPlanDraftRowsResponse> {
  const { data, error, response } = await headlessClient.PUT(
    '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/rows',
    { params: { path: { project_id: projectId, draft_id: draftId } }, body: { rows: [...rows] } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('draft bulk replace failed', { error, response });
  }
  return data;
}

/**
 * GET `…/drafts/{draft_id}/export` — the draft workbook.
 *
 * Same shape as {@link exportSessionResults}: the blob plus the filename the
 * response named, because both are transport facts the caller cannot see.
 */
export async function exportTestPlanDraft(
  projectId: string,
  draftId: string,
  fallbackFilename: string,
): Promise<HeadlessDownload> {
  const request = downloadRequest(BLOB_PARSED_GET.testPlanDraftExport, {
    params: { path: { project_id: projectId, draft_id: draftId } },
  });
  return toDownload(
    'draft export failed',
    fallbackFilename,
    await headlessClient.GET(request.path, request.init),
  );
}

/**
 * POST `…/test-plan/imports` — upload a workbook and get the resulting draft.
 *
 * The `as unknown as` casts are unavoidable at this boundary and are **confined
 * here**: openapi-typescript models a `binary` part as `string`, while the value
 * that must be appended is a `File`. `session-client.ts::workbookFormData` keeps
 * the same casts for the same reason, in the same place.
 */
export async function importTestPlanWorkbook(
  projectId: string,
  file: File,
): Promise<TestPlanImportResponse> {
  const { data, error, response } = await headlessClient.POST(
    '/headless/projects/{project_id}/test-plan/imports',
    {
      params: { path: { project_id: projectId } },
      body: { file: file as unknown as string },
      bodySerializer: (body: { file: string }) => {
        const form = new FormData();
        form.append('file', body.file as unknown as File);
        return form;
      },
    },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('test plan import failed', { error, response });
  }
  return data;
}

/** GET `…/drafts/{draft_id}` — one draft with its rows. */
export async function fetchTestPlanDraft(
  projectId: string,
  draftId: string,
): Promise<TestPlanDraftView> {
  const { data, error, response } = await headlessClient.GET(
    '/headless/projects/{project_id}/test-plan/drafts/{draft_id}',
    { params: { path: { project_id: projectId, draft_id: draftId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('draft detail lookup failed', { error, response });
  }
  return data;
}

/** DELETE `…/drafts/{draft_id}/rows/{draft_row_id}` — drop one row. */
export async function removeTestPlanDraftRow(
  projectId: string,
  draftId: string,
  draftRowId: number,
): Promise<RemoveTestPlanDraftRowResponse> {
  const { data, error, response } = await headlessClient.DELETE(
    '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/rows/{draft_row_id}',
    { params: { path: { project_id: projectId, draft_id: draftId, draft_row_id: draftRowId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('draft row remove failed', { error, response });
  }
  return data;
}

/** POST `…/test-plan/drafts` — start a new draft. */
export async function createTestPlanDraft(
  projectId: string,
  createdBy: string,
): Promise<TestPlanDraftView> {
  const { data, error, response } = await headlessClient.POST(
    '/headless/projects/{project_id}/test-plan/drafts',
    { params: { path: { project_id: projectId } }, body: { created_by: createdBy } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('draft create failed', { error, response });
  }
  return data;
}

/** GET `…/test-plan/drafts` — the project's drafts. */
export async function fetchTestPlanDrafts(projectId: string): Promise<ListTestPlanDraftsResponse> {
  const { data, error, response } = await headlessClient.GET(
    '/headless/projects/{project_id}/test-plan/drafts',
    { params: { path: { project_id: projectId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('draft list lookup failed', { error, response });
  }
  return data;
}

/** POST `…/drafts/{draft_id}/validate` — readiness check over the draft's rows. */
export async function validateTestPlanDraft(
  projectId: string,
  draftId: string,
): Promise<ValidateTestPlanDraftResponse> {
  const { data, error, response } = await headlessClient.POST(
    '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/validate',
    { params: { path: { project_id: projectId, draft_id: draftId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('draft validate failed', { error, response });
  }
  return data;
}

/** POST `…/drafts/{draft_id}/publish` — promote a draft to the published slot. */
export async function publishTestPlanDraft(
  projectId: string,
  draftId: string,
): Promise<PublishedTestPlanView> {
  const { data, error, response } = await headlessClient.POST(
    '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/publish',
    { params: { path: { project_id: projectId, draft_id: draftId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('draft publish failed', { error, response });
  }
  return data;
}

/** POST `…/drafts/{draft_id}/archive` — retire a draft **without** publishing it. */
export async function archiveTestPlanDraft(
  projectId: string,
  draftId: string,
): Promise<TestPlanDraftView> {
  const { data, error, response } = await headlessClient.POST(
    '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/archive',
    { params: { path: { project_id: projectId, draft_id: draftId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('draft archive failed', { error, response });
  }
  return data;
}

export type TestPlanGenerationRequest = components['schemas']['TestPlanGenerationRequest'];

/** Query for one page of generated draft rows (keyset — `after_draft_row_id`). */
export interface GeneratedRowPageQuery {
  readonly limit: number;
  readonly after_draft_row_id?: number;
}

/** GET `/headless/test-plan/generation/catalogue` — the generation axes catalogue. */
export async function fetchTestPlanGenerationCatalogue(): Promise<TestPlanGenerationCatalogueResponse> {
  const { data, error, response } = await headlessClient.GET(
    '/headless/test-plan/generation/catalogue',
    {},
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('generation catalogue lookup failed', { error, response });
  }
  return data;
}

/**
 * POST `…/test-plan/generation/preview` — what a request would generate.
 *
 * ⚠️ `apiErrorFromResponse` carries `problemParams` alongside `problemCode`: a
 * 400 from this operation names the offending axis in the RFC 9457 `params`, and
 * dropping it would leave the server saying which box is wrong and the screen
 * unable to repeat it.
 */
export async function previewTestPlanGeneration(
  projectId: string,
  request: TestPlanGenerationRequest,
): Promise<TestPlanGenerationPreviewResponse> {
  const { data, error, response } = await headlessClient.POST(
    '/headless/projects/{project_id}/test-plan/generation/preview',
    { params: { path: { project_id: projectId } }, body: request },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('generation preview failed', { error, response });
  }
  return data;
}

/**
 * POST `…/test-plan/generations` — submit a generation job.
 *
 * `idempotencyKey` is a header the caller owns (it is stable across retries of
 * the *same* submission), so it stays an argument rather than being minted here.
 */
export async function submitTestPlanGeneration(
  projectId: string,
  idempotencyKey: string,
  request: TestPlanGenerationRequest,
  preview: TestPlanGenerationPreviewResponse,
): Promise<TestPlanGenerationSubmittedResponse> {
  const { data, error, response } = await headlessClient.POST(
    '/headless/projects/{project_id}/test-plan/generations',
    {
      params: {
        path: { project_id: projectId },
        header: { 'Idempotency-Key': idempotencyKey },
      },
      body: { request, preview },
    },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('generation submit failed', { error, response });
  }
  return data;
}

/** GET `…/test-plan/generations/{generation_job_id}` — job progress. */
export async function fetchTestPlanGenerationJob(
  projectId: string,
  jobId: string,
): Promise<TestPlanGenerationJobResponse> {
  const { data, error, response } = await headlessClient.GET(
    '/headless/projects/{project_id}/test-plan/generations/{generation_job_id}',
    { params: { path: { project_id: projectId, generation_job_id: jobId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('generation status lookup failed', { error, response });
  }
  return data;
}

/** GET `…/drafts/{draft_id}/generation-metadata` — how this draft was generated. */
export async function fetchTestPlanGenerationMetadata(
  projectId: string,
  draftId: string,
): Promise<TestPlanGenerationMetadataResponse> {
  const { data, error, response } = await headlessClient.GET(
    '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/generation-metadata',
    { params: { path: { project_id: projectId, draft_id: draftId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('generation metadata lookup failed', { error, response });
  }
  return data;
}

/** GET `…/drafts/{draft_id}/rows` — one keyset page of generated rows. */
export async function fetchTestPlanDraftRows(
  projectId: string,
  draftId: string,
  query: GeneratedRowPageQuery,
): Promise<TestPlanGenerationRowPageResponse> {
  const { data, error, response } = await headlessClient.GET(
    '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/rows',
    { params: { path: { project_id: projectId, draft_id: draftId }, query } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('generated rows lookup failed', { error, response });
  }
  return data;
}

export type SubmitReportRequest = components['schemas']['SubmitReportRequest'];

/**
 * ── Node-scoped operations ───────────────────────────────────────────────────
 *
 * These take the client as their first argument because *which node to talk to*
 * is a decision the report screen legitimately owns (it routes to the node that
 * ran the session, or to the shared client for legacy rows). *How* to talk to it
 * is not — that is what moved in here.
 *
 * ⚠️ The client is threaded rather than resolved here on purpose. Folding the
 * node choice into these functions would move policy, not transport, and the
 * screen's rule ("route to the node captured in the submit variable, never the
 * live state, so a concurrent preflight cannot redirect an in-flight POST")
 * would become invisible.
 */

/** GET `/headless/reports/preflight` — can this session produce a report. */
export async function fetchReportPreflight(
  client: HeadlessClient,
  sessionId: number,
): Promise<ReportPreflightSummary> {
  const { data, error, response } = await client.GET('/headless/reports/preflight', {
    params: { query: { session_id: sessionId } },
  });
  if (error || !data) {
    throw apiErrorFromResponse('report preflight failed', { error, response });
  }
  return data;
}

/** POST `/headless/sessions/{session_id}/reports` — create a report-automation request. */
export async function submitReportRequest(
  client: HeadlessClient,
  sessionId: number,
  body: SubmitReportRequest,
): Promise<ReportRequestSubmitted> {
  const { data, error, response } = await client.POST('/headless/sessions/{session_id}/reports', {
    params: { path: { session_id: sessionId } },
    body,
  });
  if (error || !data) {
    throw apiErrorFromResponse('report request submit failed', { error, response });
  }
  return data;
}

/** GET `/headless/reports/{request_id}` — the request's current status. */
export async function fetchReportRequestStatus(
  client: HeadlessClient,
  requestId: number,
): Promise<ReportRequestSnapshot | undefined> {
  const { data, error, response } = await client.GET('/headless/reports/{request_id}', {
    params: { path: { request_id: requestId } },
  });
  if (error) {
    throw apiErrorFromResponse('report request status failed', { error, response });
  }
  return data;
}

/**
 * POST `/report-automation/requests/{request_id}/cancel` — stop a queued request.
 *
 * The body is optional in the contract (`message` defaults to `''` server-side)
 * and the screen collects no reason, so it is **omitted rather than sent empty**
 * — no invented field.
 */
export async function cancelReportRequest(
  client: HeadlessClient,
  requestId: number,
): Promise<void> {
  const { error, response } = await client.POST('/report-automation/requests/{request_id}/cancel', {
    params: { path: { request_id: requestId } },
  });
  if (error) {
    throw apiErrorFromResponse('report request cancel failed', { error, response });
  }
}

/** GET `/report-automation/stats` — queue counters for the metric strip. */
export async function fetchReportAutomationStats(): Promise<
  ReportAutomationQueueStats | undefined
> {
  const { data, error, response } = await headlessClient.GET('/report-automation/stats', {});
  if (error) {
    throw apiErrorFromResponse('stats request failed', { error, response });
  }
  return data;
}

/** GET `/headless/reports/{request_id}` on the shared client — lookup panel. */
export async function lookupReportRequest(
  requestId: number,
): Promise<ReportRequestSnapshot | undefined> {
  const { data, error, response } = await headlessClient.GET('/headless/reports/{request_id}', {
    params: { path: { request_id: requestId } },
  });
  if (error) {
    throw apiErrorFromResponse('report request lookup failed', { error, response });
  }
  return data;
}

/** GET `/headless/reports/{request_id}/outputs` — generated outputs for a request. */
export async function fetchReportOutputs(
  requestId: number,
): Promise<ReportOutputMetadataList | undefined> {
  const { data, error, response } = await headlessClient.GET(
    '/headless/reports/{request_id}/outputs',
    { params: { path: { request_id: requestId } } },
  );
  if (error) {
    throw apiErrorFromResponse('report outputs lookup failed', { error, response });
  }
  return data;
}

/**
 * POST `/headless/reports/{request_id}/outputs/download` — a signed, short-TTL
 * download grant (FE-P6-DL). The grant stays self-authorizing and the raw
 * filesystem path is never exposed; spending it is the caller's step.
 */
export async function issueReportOutputDownloadGrant(
  requestId: number,
  relativePath: string,
): Promise<ReportOutputDownloadGrant> {
  const { data, error, response } = await headlessClient.POST(
    '/headless/reports/{request_id}/outputs/download',
    { params: { path: { request_id: requestId } }, body: { relative_path: relativePath } },
  );
  if (error || !data) {
    throw apiErrorFromResponse('download grant failed', { error, response });
  }
  return data;
}

/** GET `/headless/sessions/{session_id}/artifacts` — artifacts a session produced. */
export async function fetchSessionArtifacts(
  sessionId: number,
): Promise<ArtifactMetadataList | undefined> {
  const { data, error, response } = await headlessClient.GET(
    '/headless/sessions/{session_id}/artifacts',
    { params: { path: { session_id: sessionId } } },
  );
  if (error) {
    throw apiErrorFromResponse('session artifacts lookup failed', { error, response });
  }
  return data;
}
