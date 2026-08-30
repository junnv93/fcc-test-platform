/**
 * describeApiError — operator-facing error message taxonomy SSOT (FD-D).
 *
 * Phase 1 (fe-phase1-ui-foundation, 2026-05-30). Existing route-level
 * helpers (reports.tsx::describeError / sessions.tsx inline / providers.tsx
 * local) duplicate the 4xx-status → copy mapping. This module centralises
 * the 6-arm taxonomy (403 / 404 / 409 / 410 / network / default); routes
 * migrate to it in Phase 2 (the contract requires only that the SSOT
 * exists with the six branches — switching consumers is the next phase).
 *
 * Design: `ErrorState` primitive renders the *output* of this function —
 * the primitive itself contains no status branching (display-only).
 * Context-specific copy (e.g. "platform:read 필요" for a 403 on the platform
 * read API) is honoured via the explicit `overrides` argument, NOT by
 * silently mutating the SSOT for one route.
 *
 * i18n (fe-i18n-en-ko-parity, 2026-06-13): the default copy is no longer an
 * inline literal table — it resolves through `t('errors.*')` at call time so
 * the operator-facing taxonomy follows the active locale. Routes still pass
 * context-specific overrides (already-localised strings via `t()` themselves).
 */
import { t } from '@/i18n';
import { type ErrorCode } from '@/shared/api-error';
import { formatByteSize } from '@/shared/byte-size';

/** Stable backend surface name — used to specialise the 403 copy. The
 *  surface set mirrors the three driving adapters we ship today (platform
 *  read / headless / session). Adding a fourth surface is intentional
 *  enough to require a code change here. */
export type ApiErrorContext = 'platform' | 'headless' | 'session';

/** Per-status override map. A route can supply a non-default copy for one
 *  status code without having to fork the whole taxonomy. */
export interface ApiErrorOverrides {
  /** 400 — request rejected by a contract/validation check (e.g. an unknown
   *  role_key against the rbac_role_grants SSOT). Falls back to the generic
   *  `default` copy when a route does not specialise it. */
  readonly badRequest?: string;
  readonly forbidden?: string;
  readonly notFound?: string;
  readonly conflict?: string;
  readonly gone?: string;
  /** 422 — the request was well-formed but the server could not act on its
   *  content (an undecodable scope snapshot, a draft with no rows). Falls back
   *  to the `code`-refined default copy when a route does not specialise it. */
  readonly unprocessable?: string;
  /** 503 — the backend surface is temporarily unavailable (a dependency down, a
   *  registry not wired, maintenance). Falls back to the generic `default` copy
   *  when a route does not specialise it, so callers that never pass it stay
   *  byte-identical to before this arm existed. */
  readonly serviceUnavailable?: string;
  readonly network?: string;
  readonly default?: string;
}

/** Extract an HTTP status from the heterogeneous error shapes routes throw:
 *  TanStack Query `Error` with `.status` (route code wraps fetch errors that
 *  way), and a plain `{ status: number }` from older sites. Returns
 *  `undefined` when no numeric status is present — that branch maps to the
 *  taxonomy's "network unreachable" arm. */
function extractStatus(error: unknown): number | undefined {
  if (typeof error !== 'object' || error === null) return undefined;
  const candidate = (error as { status?: unknown }).status;
  return typeof candidate === 'number' ? candidate : undefined;
}

const FORBIDDEN_KEY_BY_CONTEXT: Readonly<Record<ApiErrorContext, string>> = {
  platform: 'errors.forbiddenPlatform',
  headless: 'errors.forbiddenHeadless',
  session: 'errors.forbiddenSession',
};

/**
 * Copy refined by the RFC 9457 `code`, keyed by HTTP status — ONE table
 * (session-workbook-upload-ui M2, 2026-09-01).
 *
 * This started as three module-scope maps (`UNPROCESSABLE_KEY_BY_CODE` /
 * `NOT_FOUND_KEY_BY_CODE` / `SERVICE_UNAVAILABLE_KEY_BY_CODE`) whose arms each
 * re-spelled the same three lines — extract the code, look it up, fall back.
 * The workbook-upload screen needed a fourth and a fifth (413/415), and this
 * repository's own rule for a third copy is to fold it into a derived SSOT
 * rather than grow it.
 *
 * WHAT IS FOLDED IS THE LOOKUP, NOT THE FALLBACK. The arms genuinely differ in
 * what they say when no code matches — 404 falls back to `overrides.notFound`,
 * 422 to `overrides.unprocessable`, 503 to `overrides.serviceUnavailable`, and
 * each to a different generic key. Collapsing those too would change copy that
 * a dozen routes already depend on. So each arm keeps its own fallback and only
 * asks this table the one question they all asked identically.
 *
 * PRECEDENCE IS UNCHANGED AND IS THE POINT (2026-08-25): a mapped code wins over
 * a route's override. An override is what a route wants said when it does NOT
 * know why the refusal happened; a mapped code means the server told us exactly
 * why, and a generic override that still won would name the wrong object and the
 * wrong remedy. Codes without an entry fall back, so an unmapped backend code
 * degrades to correct-but-generic rather than a missing-key blank.
 *
 * The refusal *sentence* is never parsed — only the code. `detail` is
 * server-internal prose (it names Python-side fields) and must not reach a screen.
 */
const CODE_REFINED_KEY_BY_STATUS: Readonly<
  Record<number, Readonly<Partial<Record<ErrorCode, string>>>>
> = {
  // 404 — a bare "not found" and "that provider was never registered centrally"
  // call for opposite next actions; only the second is fixed by an operator.
  // `WORKBOOK_HANDLE_NOT_FOUND` is the upload axis: the node reclaimed (or never
  // had) the workbook behind the handle this screen is holding, and the remedy
  // is to upload again — which is also why the panel drops the handle on it.
  404: {
    REFERENCE_PROVIDER_NOT_REGISTERED: 'errors.referenceProviderNotRegistered',
    WORKBOOK_HANDLE_NOT_FOUND: 'errors.workbookHandleNotFound',
  },
  // 413 — the ceiling rides in `params.max`, so this arm is resolved in
  // `describeApiError` rather than here (a table of static keys cannot carry a
  // server value). The entry below is the copy used when the server declined to
  // name a bound.
  413: {
    WORKBOOK_UPLOAD_TOO_LARGE: 'errors.workbookUploadTooLarge',
  },
  // 415 — the node stores .xlsx workbooks and nothing else. Saying so is the
  // whole remedy, which is why this needs no server value.
  415: {
    WORKBOOK_UPLOAD_UNSUPPORTED_TYPE: 'errors.workbookUploadUnsupportedType',
  },
  // 422 — an undecodable scope snapshot and a draft with no rows call for
  // opposite next actions. "이 세션은 아무것도 측정하지 않았다" is a DIFFERENT
  // fact from "초안에 행이 없다" and the tester's next step differs, which is why
  // the backend minted a separate code instead of reusing `DRAFT_EMPTY`.
  422: {
    DRAFT_UNPROCESSABLE: 'errors.draftUnprocessable',
    DRAFT_EMPTY: 'errors.draftEmpty',
    SESSION_RESULTS_EMPTY: 'errors.sessionResultsEmpty',
  },
  // 503 — a dependency being down and a reference family having no rows are both
  // 503, and only the second is fixed by someone seeding data. Retrying the first
  // may work; retrying the second never will. `SESSION_UPLOAD_UNSUPPORTED` is a
  // third kind again: this node was composed without an upload store, so no
  // amount of retrying or seeding helps and the operator must use the node's own
  // workbook (or have the node recomposed).
  503: {
    REFERENCE_DATA_NOT_PROVISIONED: 'errors.referenceDataNotProvisioned',
    SESSION_UPLOAD_UNSUPPORTED: 'errors.sessionUploadUnsupported',
  },
};

/** Extract the RFC 9457 `code` from the heterogeneous error shapes routes throw.
 *  Sibling of {@link extractStatus}: never throws, `undefined` when absent. */
function extractCode(error: unknown): ErrorCode | undefined {
  if (typeof error !== 'object' || error === null) return undefined;
  const candidate = (error as { code?: unknown }).code;
  return typeof candidate === 'string' ? (candidate as ErrorCode) : undefined;
}

/**
 * Extract the offending field name from the RFC 9457 `params` extension
 * (bad-request-names-the-field, 2026-08-31).
 *
 * The 400 arm needs a different kind of refinement from its three siblings. They
 * branch on `code`, because for them the *kind* of failure is what varies. Here
 * the kind is always the same — a value the server will not accept — and the
 * only thing worth saying is WHICH one, which no fixed set of codes can carry.
 * `VALIDATION_ERROR` is shared by every screen that submits anything, so minting
 * per-field codes would be inventing vocabulary for a distinction the RFC
 * already has a member for.
 *
 * The value is the catalogue axis name (`packets`, `bandwidths`,
 * `bands_per_subfamily`) — which is also the request field and the legend the
 * generator form already prints over that group of checkboxes. So this is read,
 * never re-derived: no client-side field vocabulary exists to drift.
 *
 * `params` values are typed `unknown` on purpose (the backend types them `Any`),
 * so a reader must narrow. A non-string, empty, or absent `field` yields
 * `undefined` and the arm degrades to the route's own copy — the server declining
 * to name an axis (two candidates, or a refusal about a combination) must not
 * render as an empty gap where a field name should be.
 */
function extractFieldParam(error: unknown): string | undefined {
  if (typeof error !== 'object' || error === null) return undefined;
  const params = (error as { params?: unknown }).params;
  if (typeof params !== 'object' || params === null) return undefined;
  const candidate = (params as { field?: unknown }).field;
  if (typeof candidate !== 'string') return undefined;
  const trimmed = candidate.trim();
  return trimmed === '' ? undefined : trimmed;
}

/**
 * Extract the RFC 9457 `params.max` upper bound (session-workbook-upload-ui, 2026-09-01).
 *
 * Sibling of {@link extractFieldParam} with the same contract: never throws,
 * `undefined` for anything that is not a usable bound. The value is read, never
 * re-derived — the node's upload ceiling is a deployment setting
 * (`FCC_SESSION_MAX_WORKBOOK_UPLOAD_BYTES`) and a frontend that spelled the
 * default would state a number this deployment may not be using.
 *
 * A non-numeric value yields `undefined` so the arm degrades to a bound-less
 * sentence rather than rendering `NaN bytes`.
 *
 * ⚠️ It does NOT re-check finite/non-negative, and that omission is deliberate.
 * The only consumer hands the result straight to `formatByteSize`, which
 * rejects exactly those two cases for its own reasons (it has a second caller —
 * an upload's `size_bytes`). Checking here as well could not change any answer:
 * both spellings of `NaN`, `Infinity` and `-1` end at the same degraded
 * sentence. A guard that cannot change the answer reads as protection while
 * protecting nothing, so it is gone rather than tested around — the same call
 * this repository made on `db is None` → row-count.
 */
function extractMaxParam(error: unknown): number | undefined {
  if (typeof error !== 'object' || error === null) return undefined;
  const params = (error as { params?: unknown }).params;
  if (typeof params !== 'object' || params === null) return undefined;
  const candidate = (params as { max?: unknown }).max;
  return typeof candidate === 'number' ? candidate : undefined;
}

/**
 * The `code`-refined copy key for a refusal, or `undefined` when there is none.
 *
 * The one place the four (soon more) code-refined arms ask their shared
 * question. Each arm still owns what it says when this returns `undefined` —
 * see {@link CODE_REFINED_KEY_BY_STATUS}.
 */
function refinedKeyForCode(status: number, error: unknown): string | undefined {
  const code = extractCode(error);
  if (code === undefined) return undefined;
  return CODE_REFINED_KEY_BY_STATUS[status]?.[code];
}

/**
 * Map a thrown API error to an operator-facing message.
 *
 * Taxonomy (11 arms):
 * - 400 → names the offending field when the RFC 9457 `params.field` says which
 *         one; otherwise the route's `badRequest` copy, else the generic default
 * - 403 → "권한이 없습니다" (context-specialised; e.g. platform → "platform:read 필요")
 * - 404 → "대상을 찾을 수 없습니다" — refined by the RFC 9457 `code`
 *         (`REFERENCE_PROVIDER_NOT_REGISTERED`); never renders `detail`
 * - 409 → "요청이 충돌했습니다" (route may override for download integrity, claim conflict, ...)
 * - 410 → "리소스가 만료되었습니다" (typically a signed download grant)
 * - 413 → names the node's upload ceiling when the RFC 9457 `params.max` carries
 *         it; otherwise the `code`-refined copy, else the route's `default`
 * - 415 → refined by the `code` (`WORKBOOK_UPLOAD_UNSUPPORTED_TYPE`)
 * - 422 → "요청 내용을 처리할 수 없습니다" — refined by the RFC 9457 `code`
 *         (`DRAFT_UNPROCESSABLE` / `DRAFT_EMPTY`); never renders `detail`
 * - 503 → shares the `default` copy unless a route supplies `serviceUnavailable`
 *         (a temporarily-unavailable backend surface, e.g. an unwired registry)
 * - network (no numeric status) → "서버에 연결할 수 없습니다"
 * - default (any other status) → "요청이 실패했습니다"
 *
 * The 400 arm falls back to the `default` copy unless a route supplies
 * `badRequest`, so a caller that neither specialises 400 nor receives a named
 * field keeps byte-identical output.
 */
export function describeApiError(
  error: unknown,
  context?: ApiErrorContext,
  overrides?: ApiErrorOverrides,
): string {
  const status = extractStatus(error);
  switch (status) {
    case 400: {
      // Same precedence as the 404/422/503 arms, for the same reason: when the
      // server named the offending field it told us exactly why, and a generic
      // override that still won would print a message that says less than what
      // we were handed. Overrides remain the fallback for every refusal the
      // server could not pin to one field, so no existing caller shifts.
      const field = extractFieldParam(error);
      return field !== undefined
        ? t('errors.badRequestField', { field })
        : (overrides?.badRequest ?? t('errors.default'));
    }
    case 403:
      return (
        overrides?.forbidden ?? t(context ? FORBIDDEN_KEY_BY_CONTEXT[context] : 'errors.forbidden')
      );
    case 404: {
      // The code-derived key wins over `overrides.notFound`, and that ordering
      // is the whole point (2026-08-25). A route's override is what it wants
      // said when it does NOT know why the 404 happened; a mapped code means
      // the server told us exactly why, and a generic override that still won
      // would print a *wrong object* — the reference screen's override says
      // "that revision could not be found" for an unregistered PROVIDER, which
      // names the wrong thing and the wrong remedy. Overrides remain the
      // fallback for every unmapped code, so no existing caller shifts.
      const key = refinedKeyForCode(404, error);
      return key !== undefined ? t(key) : (overrides?.notFound ?? t('errors.notFound'));
    }
    case 409:
      return overrides?.conflict ?? t('errors.conflict');
    case 410:
      return overrides?.gone ?? t('errors.gone');
    case 413: {
      // The ceiling is a deployment setting, so the number comes from the
      // server (`params.max`) and is never spelled here. Without it the arm
      // still says something better than "요청이 실패했습니다" — it says the
      // file was too large — which is why the bound is a refinement and not a
      // precondition.
      const bytes = extractMaxParam(error);
      const rendered = bytes === undefined ? undefined : formatByteSize(bytes);
      if (rendered !== undefined) {
        return t('errors.uploadTooLargeMax', { max: rendered });
      }
      const key = refinedKeyForCode(413, error);
      return key !== undefined ? t(key) : (overrides?.default ?? t('errors.default'));
    }
    case 415: {
      const key = refinedKeyForCode(415, error);
      return key !== undefined ? t(key) : (overrides?.default ?? t('errors.default'));
    }
    case 422: {
      // Same precedence as the 404 arm, for the same reason. This arm was
      // written override-first and has been harmless only because no route
      // supplies `unprocessable` — a latent copy of the defect fixed above,
      // waiting for the first route that does.
      const key = refinedKeyForCode(422, error);
      return key !== undefined ? t(key) : (overrides?.unprocessable ?? t('errors.unprocessable'));
    }
    case 503: {
      // Same precedence as the 404 and 422 arms, for the same reason: a mapped
      // code means the server said exactly why, and a generic override that
      // still won would name the wrong remedy.
      const key = refinedKeyForCode(503, error);
      return key !== undefined ? t(key) : (overrides?.serviceUnavailable ?? t('errors.default'));
    }
    case undefined:
      return overrides?.network ?? t('errors.network');
    default:
      return overrides?.default ?? t('errors.default');
  }
}
