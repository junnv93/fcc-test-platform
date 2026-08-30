/**
 * ApiError — shared error shape SSOT (web-keyset-pagination-ssot, 2026-06-13).
 *
 * The API client helpers throw an `Error` decorated with the HTTP `status` so
 * a route's React Query error branch can map 400/403/404/409/503 distinctly
 * (`status === undefined` ⇒ network/offline). This shape was re-declared inline
 * in three places (platform-client, projects, membership); it lives here once so
 * every consumer narrows against the same type.
 */
import type { components as HeadlessComponents } from '@/api/generated/headless-api.types';
import type { components } from '@/api/generated/platform-api.types';
import type { components as SessionComponents } from '@/api/generated/session-api.types';

/**
 * Machine-readable RFC 9457 error identifier — the backend `ErrorCode` enum,
 * taken from the generated OpenAPI types so the union cannot be hand-typed and
 * drift (fe-data-layer-robustness M5, 2026-07-19).
 *
 * The backend's `api_error_codes.py` is the SSOT; it flows
 * `ErrorCode` enum → OpenAPI artifact → `npm run codegen` → here.
 *
 * **The union of both surfaces, not an alias of one (2026-08-13).** This used to
 * alias the platform artifact on the stated premise that "both artifacts publish
 * the identical enum". That premise stopped holding the moment a code was scoped
 * to a single surface (`ERROR_CODE_SURFACE_SCOPE`), and the asymmetry was silent
 * in one direction only: platform-scoped codes were covered *because* platform
 * was the one aliased, so nothing broke until a **headless**-scoped code arrived
 * (`SESSION_RESULTS_EMPTY`) and could not be named here at all. Taking the union
 * makes the type independent of which surface happens to own a code — a client
 * narrows on whatever the server can actually send it.
 *
 * **Session joined on 2026-08-23.** Until then the measurement-node surface
 * answered legacy `{"detail": string}` and published no `ErrorCode` at all, so
 * there was nothing to union. It now publishes six session-scoped codes
 * (`SESSION_ALREADY_RUNNING`, `WORKBOOK_UPLOAD_TOO_LARGE`,
 * `WORKBOOK_UPLOAD_UNSUPPORTED_TYPE`, `WORKBOOK_HANDLE_NOT_FOUND`,
 * `SESSION_UPLOAD_UNSUPPORTED`, `SESSION_NODE_NOT_PROVISIONED`) — omitting the
 * third arm here would reproduce exactly the defect described above, one surface
 * later.
 */
export type ErrorCode =
  | components['schemas']['ErrorCode']
  | HeadlessComponents['schemas']['ErrorCode']
  | SessionComponents['schemas']['ErrorCode'];

/**
 * Structured, non-PII context from the RFC 9457 `params` extension member
 * (W3-B M1, 2026-07-30).
 *
 * Derived from the generated `ProblemDetails` schema rather than hand-typed, for
 * the same reason as {@link ErrorCode}: the backend is the SSOT and the shape
 * must not be re-declared here. The backend guards the key domain with
 * `PROBLEM_PARAM_ALLOWLIST` (`field`/`limit`/`max`/`min`/`expected`/`allowed`/
 * `resource`/`retry_after`) and rejects anything else at `ProblemDetails`
 * construction — a PII guard, so `params` can never carry request body, header,
 * or query values. That allow-list is deliberately NOT mirrored as a TS union:
 * the OpenAPI artifact publishes an open record, so a hand-written union would
 * be a second, drift-prone source of truth. Consumers narrow the one key they
 * need at the point of use instead.
 *
 * Values are `unknown` because the backend types them `Any` — a reader must
 * check (`typeof params.field === 'string'`) before use.
 */
export type ProblemParams = NonNullable<components['schemas']['ProblemDetails']['params']>;

export interface ApiError extends Error {
  /** HTTP status of the failed response; `undefined` when the request never
   *  reached the server (network/offline). */
  status?: number;
  /**
   * Machine-readable error identifier from the RFC 9457 `application/problem+json`
   * body (`code` extension member) — ADR-0011 / backend Increment B1. Stable
   * across occurrences, so a route's error branch (and Increment 4 i18n
   * `describeApiError(code)`) routes on `code` instead of brittle HTTP status.
   * `undefined` when the response carried no problem body (network error or a
   * legacy non-problem response).
   *
   * M5 (2026-07-19) — narrowed from `string` to the generated {@link ErrorCode}
   * union now that `npm run codegen` emits it. A typo'd or invented code is now
   * a compile error rather than a branch that silently never matches.
   */
  code?: ErrorCode;
  /**
   * Machine-readable {@link ProblemParams} from the RFC 9457 problem body
   * (`params` extension member) — W3-B M1 (2026-07-30).
   *
   * `code` says *what kind* of failure it was; `params` says *which input*. The
   * motivating case is `PROJECT_IDENTIFIER_CONFLICT` (409), where the backend
   * sends `params.field` naming the offending identifier
   * (`management_number` / `project_code`) precisely "so the client can
   * highlight exactly that field" (OpenAPI 409 description). Without this the FE
   * could only show a generic conflict message on a form with two identifier
   * inputs.
   *
   * `undefined` when the response carried no problem body (network error /
   * legacy non-problem response) **or** when the body's `params` was absent or
   * empty — the backend's `as_dict` omits the member for an empty mapping, and
   * `problemParams` mirrors that, so "no context" is always the same absent key.
   */
  params?: ProblemParams;
}
