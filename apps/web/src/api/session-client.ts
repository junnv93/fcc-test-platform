import createClient from 'openapi-fetch';

import { getRuntimeConfig } from '@/config/runtime';

import { authRetryMiddleware } from './auth-middleware';
import { apiErrorFromResponse } from './to-api-error';

import type { paths as SessionPaths } from './generated/session-api.types';

/**
 * Session API client — backend `session-api.openapi.json` (F-2-D3 SSOT).
 *
 * Types come from `npm run codegen` (ADR-0003 — openapi-typescript).
 * The generated file is gitignored; CI runs `npm run codegen:check` so a
 * backend OpenAPI change without a frontend type bump fails.
 *
 * Auth: the shared `authRetryMiddleware` (`./auth-middleware`) attaches the
 * current bearer token on every request (omitted when unauthenticated — the
 * backend returns 401/403 and view code handles it) and performs a single
 * silent-refresh + replay on a 401 (Increment 6 — 401 retry-once).
 */

const baseUrl = (): string => getRuntimeConfig().apiBaseUrl;

export const sessionClient = createClient<SessionPaths>({
  baseUrl: baseUrl(),
  headers: {
    Accept: 'application/json',
  },
});
sessionClient.use(authRetryMiddleware);

/**
 * Re-export of the generated types so callers do not import the autogen
 * path directly (decouples view code from the generator filename).
 */
export type SessionApiPaths = SessionPaths;

/** Wire shape of a stored workbook upload — derived from the generated paths,
 *  never hand-typed. The session surface declares its schemas inline under
 *  `paths` (it publishes no `components.schemas` beyond `ProblemDetails`), so
 *  this is the only place the shape can be named without re-declaring it. */
export type WorkbookUploadResponse =
  SessionPaths['/session/workbook']['post']['responses'][200]['content']['application/json'];

/** Wire shape of a session progress snapshot. Same derivation, same reason. */
export type SessionProgress =
  SessionPaths['/session/progress']['get']['responses'][200]['content']['application/json'];

/**
 * The multipart field name the workbook upload operation expects.
 *
 * Single literal, mirroring `platform-client.ts::SAMPLE_WORKBOOK_FORM_FIELD` —
 * a backend rename is one edit, not one per call site. It is `MULTIPART_FILE_FIELD`
 * on the backend, shared by all three surfaces through
 * `openapi_schema_builder.multipart_file_request_body`.
 */
const SESSION_WORKBOOK_FORM_FIELD = 'file';

/**
 * multipart/form-data serializer for the workbook upload.
 *
 * The `as unknown as` casts are unavoidable at this boundary and are confined
 * here: openapi-typescript models a `binary` part as `string`, while the value
 * that must be appended is a `File`.
 */
function workbookFormData(body: { file: string }): FormData {
  const form = new FormData();
  form.append(SESSION_WORKBOOK_FORM_FIELD, body.file as unknown as File);
  return form;
}

/**
 * ── Why the session calls live here and not in the route (2026-09-01) ────────
 *
 * `control.tsx` and `diagnostics.tsx` used to build their own failures with
 * `Object.assign(new Error(msg), { status })`. That predates the RFC 9457
 * migration and it silently threw away the one field the screen needs: since
 * 2026-08-23 this surface publishes six session-scoped `ErrorCode`s
 * (`WORKBOOK_UPLOAD_TOO_LARGE`, `WORKBOOK_HANDLE_NOT_FOUND`,
 * `SESSION_UPLOAD_UNSUPPORTED`, …) and **none of them had ever reached a
 * screen** — the FE could name them in its `ErrorCode` union and could not
 * observe one.
 *
 * Putting the helpers in the client rather than in each route is the shape
 * `platform-client.ts` already settled on for typed file operations, and it is
 * what makes the defect unrepeatable: a new session call cannot forget
 * `problemCode`, because there is no per-route place left to forget it in.
 */

/** POST `/session/workbook` — store a workbook on this node, get an opaque handle. */
export async function uploadSessionWorkbook(file: File): Promise<WorkbookUploadResponse> {
  const { data, error, response } = await sessionClient.POST('/session/workbook', {
    body: { file: file as unknown as string },
    bodySerializer: workbookFormData,
  });
  if (error || data === undefined) {
    throw apiErrorFromResponse('session workbook upload failed', { error, response });
  }
  return data;
}

/** POST `/session/stop` — halt the running measurement. */
export async function stopSession(): Promise<void> {
  const { error, response } = await sessionClient.POST('/session/stop', {});
  if (error) {
    throw apiErrorFromResponse('/session/stop failed', { error, response });
  }
}

/** GET `/session/progress` — the node's own account of the current session. */
export async function fetchSessionProgress(): Promise<SessionProgress> {
  const { data, error, response } = await sessionClient.GET('/session/progress', {});
  if (error || data === undefined) {
    throw apiErrorFromResponse('progress request failed', { error, response });
  }
  return data;
}

/** Wire shape of the node self-description. Same derivation as the two above. */
export type SessionInfo =
  SessionPaths['/session/info']['get']['responses'][200]['content']['application/json'];

/**
 * GET `/session/info` — what this node says about itself.
 *
 * The route this replaces also attached the raw response body as `.body`. That
 * property had no reader (`diagnostics.tsx::describeStatus` reads `.status`
 * only), and the structured half of it — `code`/`params` — now arrives through
 * the shared shape instead, so nothing observable is lost and the surface gains
 * a machine-readable failure identifier it did not have.
 */
export async function fetchSessionInfo(): Promise<SessionInfo> {
  const { data, error, response } = await sessionClient.GET('/session/info', {});
  if (error || data === undefined) {
    throw apiErrorFromResponse('session info request failed', { error, response });
  }
  return data;
}
