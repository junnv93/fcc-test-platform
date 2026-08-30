/**
 * toApiError — single factory for the decorated-`Error` shape SSOT (C2 query-hooks-ssot, 2026-06-17).
 *
 * Every typed-client helper and route query/mutation that wraps a non-2xx
 * response throws an {@link ApiError}: a built-in `Error` decorated with the
 * HTTP `status` (and, when present, the RFC 9457 `code` extension) so a route's
 * React Query error branch can map 400/403/404/409/410/503 distinctly. That
 * decoration was hand-rolled as `Object.assign(new Error(msg), { status }) as
 * ApiError` in ~20 call sites across `api/platform-client.ts` and the
 * `chambers`/`test-plans` routes — one drifted spelling away from a silent
 * `status`-loss bug. This is the one place the shape is constructed.
 *
 * Behaviour is byte-identical to the inlined expression: `status` is always
 * assigned (mirroring `Object.assign`, so `error.status === undefined` holds for
 * a network error), and the optional `code`/`params` properties are only attached
 * when a caller supplies them (so `Object.keys(error)` stays `['status']` for the
 * common status-only case — no spurious `code: undefined` key). Every extension
 * member added here MUST preserve that rule: it is what keeps the ~23 existing
 * call sites byte-identical as the problem body grows richer.
 */
import { type ApiError, type ErrorCode, type ProblemParams } from '@/shared/api-error';

/**
 * Extract the RFC 9457 `code` extension member from an error response body
 * (fe-data-layer-robustness M5, 2026-07-19).
 *
 * The backend has emitted `application/problem+json` with a stable 16-member
 * `code` since Increment B1, and `openapi-fetch` already parses it (its
 * content-type sniff matches `…+json`) and hands it back as `error`. Every one
 * of the 23 `toApiError` call sites in `platform-client.ts` nevertheless
 * dropped it, passing only `response?.status` — so the FE threw away the one
 * field that distinguishes `DRAFT_ROW_CONFLICT` from `PUBLISH_CONFLICT` from
 * `CLAIM_CONFLICT` (all HTTP 409) and had to guess from the status alone.
 *
 * Deliberately untyped input: the `error` slot is `unknown`-ish across the
 * generated operations, and a legacy non-problem body (`{detail: "..."}`) or a
 * plain string must yield `undefined` rather than throw. The returned value is
 * NOT validated against the enum at runtime — a server that ships a new code
 * before the FE regenerates types should still surface it (the compile-time
 * union governs FE-authored literals, which is where drift actually originates).
 */
export function problemCode(body: unknown): ErrorCode | undefined {
  if (typeof body !== 'object' || body === null) return undefined;
  const code = (body as Record<string, unknown>).code;
  return typeof code === 'string' ? (code as ErrorCode) : undefined;
}

/**
 * Extract the RFC 9457 `params` extension member from an error response body
 * (W3-B M1, 2026-07-30).
 *
 * Sibling of {@link problemCode} with the same contract: deliberately untyped
 * input, never throws, and yields `undefined` for anything that is not a param
 * mapping (a legacy `{detail: "..."}` body, a plain string, `params: null`). An
 * array is rejected too — it is `typeof 'object'` but positional, so treating it
 * as a param map would invent index keys the backend never sends.
 *
 * An **empty** mapping also yields `undefined`. That is not a client-side
 * convention: the backend's `ProblemDetails.as_dict` writes the member only
 * `if self.params:`, so an empty `params` never reaches the wire in the first
 * place. Folding `{}` back to `undefined` keeps "no structured context" as one
 * observable state (absent key) instead of two.
 *
 * Values are NOT validated against the backend `PROBLEM_PARAM_ALLOWLIST` at
 * runtime — same reasoning as `problemCode`: a server that starts attaching a
 * newly allow-listed key should still surface it, and the allow-list is enforced
 * server-side at construction (it is a PII guard on the *producer*, not a
 * parsing rule for the consumer).
 */
export function problemParams(body: unknown): ProblemParams | undefined {
  if (typeof body !== 'object' || body === null) return undefined;
  const params = (body as Record<string, unknown>).params;
  if (typeof params !== 'object' || params === null || Array.isArray(params)) {
    return undefined;
  }
  const known = params as ProblemParams;
  return Object.keys(known).length > 0 ? known : undefined;
}

/**
 * Build an {@link ApiError} from a failure message and the response status.
 *
 * @param message human-debuggable error text (never operator-facing — routes map
 *   the error to a localized string via `describeApiError`).
 * @param status HTTP status of the failed response, or `undefined` when the
 *   request never reached the server (network/offline).
 * @param code optional machine-readable RFC 9457 `code` extension member —
 *   normally `problemCode(error)` from the failing response body.
 * @param params optional machine-readable RFC 9457 `params` extension member —
 *   normally `problemParams(error)`. Attached under the same "omit, do not set
 *   `undefined`" rule as `code`, so every pre-existing call site keeps its exact
 *   own-property set (`Object.keys(error)` unchanged).
 */
export function toApiError(
  message: string,
  status: number | undefined,
  code?: ErrorCode,
  params?: ProblemParams,
): ApiError {
  const fields: {
    status: number | undefined;
    code?: ErrorCode;
    params?: ProblemParams;
  } = { status };
  if (code !== undefined) fields.code = code;
  if (params !== undefined) fields.params = params;
  return Object.assign(new Error(message), fields) as ApiError;
}

/**
 * The failing half of an `openapi-fetch` result.
 *
 * Deliberately structural and minimal: every generated operation returns
 * `{ data, error, response }`, and this is the part that describes a failure.
 * Typing it this way means a call site can hand over what it already destructured
 * without naming a single extension member.
 */
export interface ResponseFailure {
  readonly error?: unknown;
  readonly response?: { readonly status: number } | undefined;
}

/**
 * Build an {@link ApiError} from a failed response — **the only shape a route
 * should use** (boundary-plumbing-and-node-liveness, 2026-08-19).
 *
 * ## Why this exists when `toApiError` already did
 *
 * `toApiError(message, status, code?, params?)` lets a caller pass the status and
 * *stop there*. Measured on the tree this landed in: **26 call sites across 12
 * headless route files did exactly that** — 11 hand-rolling
 * `Object.assign(new Error(msg), { status })` and 15 calling
 * `toApiError(msg, response?.status)`. All 26 were on one surface;
 * `platform-client.ts` (48 calls) and `session-client.ts` (5) pass all four.
 *
 * The cost was not cosmetic. `ui/errors.ts::CODE_REFINED_KEY_BY_STATUS` already
 * carried refined operator copy for `DRAFT_UNPROCESSABLE`, `DRAFT_EMPTY`,
 * `SESSION_RESULTS_EMPTY` and `REFERENCE_DATA_NOT_PROVISIONED`, and the backend
 * already emitted every one of them — **but the code never survived the client
 * boundary, so those arms were unreachable.** The screen said "요청이
 * 실패했습니다" about a fact it had been told.
 *
 * ## The design
 *
 * This factory takes the failure **whole** and extracts `code`/`params` itself,
 * so there is no argument left to forget. That is the same property the session
 * surface reached for by moving plumbing into its client helper — achieved here
 * without moving the transport, because the defect is *information loss*, not
 * *transport exposure* (those are different properties and a wave that conflates
 * them cannot say which one it proved).
 *
 * `status` stays `undefined` when the request never produced a response, exactly
 * as `Object.assign` behaved, and the own-property rule is inherited from
 * {@link toApiError}: absent extensions are **omitted, never set to
 * `undefined`**. That is what keeps `Object.keys(error)` stable for existing
 * callers.
 */
export function apiErrorFromResponse(message: string, failure: ResponseFailure): ApiError {
  return toApiError(
    message,
    failure.response?.status,
    problemCode(failure.error),
    problemParams(failure.error),
  );
}

/**
 * Build an {@link ApiError} for a failure that **never met a response**.
 *
 * ⚠️ Not a convenience wrapper — the distinction is a fact about what happened.
 * "the server said this" and "we never reached the server" are different states,
 * and folding them together would make `status: undefined` mean two things. The
 * three call sites in this tree are a local validation refusal (a 400 the client
 * decided), an unreachable download, and a grant that expired before use — none
 * of them has a body to read a `code` out of, so a `code` here can only be one
 * the client itself minted.
 */
export function clientOriginatedApiError(
  message: string,
  options: { status?: number; code?: ErrorCode; params?: ProblemParams } = {},
): ApiError {
  return toApiError(message, options.status, options.code, options.params);
}
