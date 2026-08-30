/**
 * Signed download grant execution (fe-w2-a-result-report-honesty M3, 2026-07-28).
 *
 * FE-P6-DL issues a short-TTL, self-authorizing grant URL for one report output
 * and the browser fetches the bytes: the raw filesystem path is never exposed
 * and the URL carries no RBAC header (that is the whole point of signing it).
 *
 * The original consumer executed the grant with `window.location.assign(url)`.
 * That works only on the happy path. The stream endpoint has a documented
 * failure taxonomy (409 integrity mismatch / 410 expired grant / 404 file
 * missing), and a top-level navigation renders those as a raw problem+json
 * *page*: the SPA unmounts, the looked-up request id, the loaded output list and
 * every other piece of route state are gone, and the operator is left staring at
 * JSON with no way back except the browser's back button. The failure taxonomy
 * was, in practice, unreachable from the UI.
 *
 * This module executes the grant **inside the page** instead:
 *
 *   1. refuse to spend a grant whose `expires_at` has already elapsed (the field
 *      was previously received and ignored) — the caller re-issues one instead
 *      of walking into a guaranteed 410;
 *   2. read the response, so a non-2xx becomes an {@link ApiError} carrying both
 *      the HTTP status and the RFC 9457 `code` extension — the download-specific
 *      codes (`DOWNLOAD_INTEGRITY_CONFLICT` / `DOWNLOAD_EXPIRED`) finally reach
 *      the view instead of being guessed from the status;
 *   3. hand the bytes to the browser through an object-URL anchor, so a
 *      successful download does not navigate either.
 *
 * ## Why raw `fetch` here
 *
 * The typed openapi-fetch client attaches the `Authorization` bearer via
 * `authRetryMiddleware`. Routing the grant through it would put an RBAC header
 * on a URL that is signed *precisely so that it needs none* — inverting the
 * FE-P6-DL security model. Like the OIDC layer (`src/auth/`), this is access
 * that structurally cannot go through the typed client, so it is an explicit,
 * file-scoped exception in `tests/test_frontend_architecture_conformance.py::
 * TestFrontendBackendAccessViaTypedClient` rather than a widened directory rule.
 */
import { apiErrorFromResponse, clientOriginatedApiError } from '@/api/to-api-error';

import type { ApiError } from './api-error';

/** The grant payload returned by `POST …/outputs/download`
 *  (`ReportOutputDownloadGrant`). */
export interface SignedDownloadGrant {
  readonly download_url: string;
  readonly expires_at: string;
}

/**
 * Has the grant's access window already elapsed at `nowMs`?
 *
 * An unparseable `expires_at` returns `false`: the frontend must not *fabricate*
 * an expiry the server never asserted. A genuinely expired token is still caught
 * — the server answers 410 and the taxonomy surfaces it.
 */
export function isGrantExpired(expiresAt: string, nowMs: number): boolean {
  const expiry = Date.parse(expiresAt);
  if (Number.isNaN(expiry)) return false;
  return nowMs >= expiry;
}

/** Injection seam so the seal can drive the DOM/timer effects deterministically.
 *  Defaults are the real browser globals. */
export interface SignedDownloadDeps {
  readonly fetchImpl?: typeof fetch;
  readonly createObjectURL?: (blob: Blob) => string;
  readonly revokeObjectURL?: (url: string) => void;
}

/**
 * Fetch a granted output and hand it to the browser as a file download.
 *
 * Resolves on success; rejects with an {@link ApiError} (status + `code` when the
 * server sent a problem body, `status === undefined` for an unreachable network)
 * on failure. Never navigates — the caller's route state survives either way.
 */
export async function runSignedDownload(
  grant: SignedDownloadGrant,
  fileName: string,
  deps: SignedDownloadDeps = {},
): Promise<void> {
  // Spelled as a literal `fetch(` call rather than a bare `globalThis.fetch`
  // reference so the raw-access seal in
  // `tests/test_frontend_architecture_conformance.py` genuinely *sees* this
  // module. Aliasing the global would slip past that guard silently, which is
  // the opposite of an explicit, documented exception.
  const doFetch = deps.fetchImpl ?? ((input: string): Promise<Response> => fetch(input));
  const createUrl = deps.createObjectURL ?? URL.createObjectURL.bind(URL);
  const revokeUrl = deps.revokeObjectURL ?? URL.revokeObjectURL.bind(URL);

  let response: Response;
  try {
    // No credentials, no Authorization header: the token in the URL is the
    // authorization (FE-P6-DL invariant).
    response = await doFetch(grant.download_url);
  } catch {
    // Network/offline — no numeric status, which the taxonomy maps to the
    // "server unreachable" arm.
    throw clientOriginatedApiError('report output download unreachable');
  }

  if (!response.ok) {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      // A non-problem body (HTML error page, empty 404) simply yields no code —
      // the status arm of the taxonomy still applies.
      body = undefined;
    }
    // A raw `fetch` rather than an openapi-fetch operation, but structurally the
    // same failure: a response plus a parsed body. Routing it through the shared
    // factory also picks up `params`, which this site was dropping.
    throw apiErrorFromResponse('report output download failed', {
      error: body,
      response,
    }) satisfies ApiError;
  }

  const blob = await response.blob();
  const objectUrl = createUrl(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = fileName;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoking synchronously can race the browser's read of the object URL in
  // some engines; defer one task so the download is already committed.
  globalThis.setTimeout(() => revokeUrl(objectUrl), 0);
}
