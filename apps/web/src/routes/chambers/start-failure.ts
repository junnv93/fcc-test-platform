import { type ErrorCode } from '@/shared/api-error';

import { type NextActionKey } from './next-actions';
import { isChamberUnavailable, type ChamberUnavailabilityFacts } from './status';

/**
 * Chamber measurement *start/progress* failure recovery policy — chambers-local
 * SSOT.
 *
 * This is deliberately separate from `describeApiError` (the operator-facing
 * *message* taxonomy in `@/ui`, which this route cannot and should not fork).
 * `describeApiError` answers "what happened"; this module answers the
 * orthogonal "what do I do next" — a recovery sentence plus which hub-backed
 * follow-up screens help.
 *
 * ## Why classifying on `code` cannot contradict the message taxonomy
 *
 * The two taxonomies must never disagree, and the module used to guarantee that
 * by keying off the same input (HTTP status). It now keys off the RFC 9457
 * `code` first — and that is still safe *by construction*, not by coincidence:
 * the backend derives the status FROM the code
 * (`application/common/api_error_codes.py::ERROR_CODE_STATUS`, a total function
 * code → status). A partition induced by `code` is therefore a REFINEMENT of the
 * partition induced by `status`: it can split a status arm in two, never place
 * two different statuses in one arm nor cross an arm boundary. So a `code`-keyed
 * recovery hint is always at least as specific as, and never in conflict with,
 * the status-keyed message.
 *
 * The refinement is only worth having if the message side is specialised too —
 * `describeApiError`'s 404/503 arms fall back to generic copy unless the caller
 * passes `notFound` / `serviceUnavailable` overrides. `MeasurementStarter` does.
 * Widening the arms here without widening those overrides would produce exactly
 * the "message and recovery hint disagree" state this note exists to prevent.
 *
 * ## Why `status` remains
 *
 * `code` is absent on a network failure (no response body at all) and on any
 * legacy non-problem response, so the status ladder stays as the fallback — not
 * as a parallel authority.
 */
export type StartFailureKind =
  | 'forbidden'
  | 'notFound'
  | 'conflict'
  | 'conflictOffline'
  | 'unavailable'
  | 'network'
  | 'unknown';

/**
 * The `ErrorCode` values the chamber start path actually emits, mapped to their
 * recovery arm (backend `_PLATFORM_ERROR_CODE_TABLE`: `ChamberNotFoundError` →
 * `NOT_FOUND`, `ChamberNotAvailableError` → `CONFLICT`, `ChamberProxyError` →
 * `UPSTREAM_UNAVAILABLE`, authorization → `FORBIDDEN`).
 *
 * Codes this endpoint cannot produce get no arm — an invented arm would be dead
 * copy to translate and maintain. `satisfies Partial<Record<ErrorCode, …>>`
 * keeps every key checked against the generated union, so a renamed backend code
 * is a compile error rather than a branch that silently never matches.
 */
const KIND_BY_ERROR_CODE = {
  FORBIDDEN: 'forbidden',
  NOT_FOUND: 'notFound',
  CONFLICT: 'conflict',
  UPSTREAM_UNAVAILABLE: 'unavailable',
} as const satisfies Partial<Record<ErrorCode, StartFailureKind>>;

/** The RFC 9457 `code` extension member, when the thrown error carries one. */
function errorCodeOf(error: unknown): string | undefined {
  if (typeof error !== 'object' || error === null) return undefined;
  const code = (error as { code?: unknown }).code;
  return typeof code === 'string' ? code : undefined;
}

/** HTTP status of the failed response; `undefined` when the request never
 *  reached the hub (network/offline) — the fallback axis only. */
function statusOf(error: unknown): number | undefined {
  if (typeof error !== 'object' || error === null) return undefined;
  const status = (error as { status?: unknown }).status;
  return typeof status === 'number' ? status : undefined;
}

/**
 * The transport-level recovery class: `code` when the response carried one,
 * otherwise the status ladder (see the module note for why the two agree).
 */
function transportKind(error: unknown): StartFailureKind {
  const code = errorCodeOf(error);
  if (code !== undefined && code in KIND_BY_ERROR_CODE) {
    return KIND_BY_ERROR_CODE[code as keyof typeof KIND_BY_ERROR_CODE];
  }
  switch (statusOf(error)) {
    case 403:
      return 'forbidden';
    case 404:
      return 'notFound';
    case 409:
      return 'conflict';
    case 503:
      return 'unavailable';
    case undefined:
      return 'network';
    default:
      return 'unknown';
  }
}

/**
 * Classify a thrown start/progress error into a recovery class. This is the
 * single place the recovery guidance is selected from.
 *
 * `chamber` is the availability row for the chamber the operator tried to start,
 * when the screen holds one. It exists to split the 409 arm: the backend raises
 * one `ChamberNotAvailableError` for "another operator is running on it" and for
 * "the node is offline/disabled", so the cause is NOT recoverable from the error
 * — only from the availability view. Telling an operator to wait and retry an
 * offline chamber is advice that never comes true.
 *
 * When no row is available, or the row shows nothing wrong (the supervision poll
 * has not caught up with the transition that caused the 409), the arm degrades to
 * the generic `conflict`. The screen does not invent a cause it cannot see.
 */
export function classifyStartFailure(
  error: unknown,
  chamber?: ChamberUnavailabilityFacts,
): StartFailureKind {
  const kind = transportKind(error);
  if (kind !== 'conflict') return kind;
  return chamber !== undefined && isChamberUnavailable(chamber) ? 'conflictOffline' : 'conflict';
}

export interface StartFailureGuidance {
  /** i18n key for the actionable recovery sentence(s) shown under the error. */
  readonly guidanceKey: string;
  /** Hub-backed follow-up screens that help the operator recover, rendered as
   *  `ChamberNextActionLinks` deep-links (empty when the fix is on this page —
   *  e.g. a permission request has no self-service screen). */
  readonly actions: readonly NextActionKey[];
}

/**
 * Recovery-guidance map: failure class → what to do + which screens help.
 *
 * Central-proxy invariant: guidance never points at a chamber node, only at the
 * availability list on this page (retry when idle), diagnostics, or history.
 *
 * Every arm owns its own sentence. That is the milestone, not a style rule: 404
 * (not registered) and 503 (node did not answer) previously shared the
 * `unknown` copy — "retry, and check diagnostics if it persists" — which is
 * wrong advice for the first and vague advice for the second.
 */
export const START_FAILURE_GUIDANCE: Readonly<Record<StartFailureKind, StartFailureGuidance>> = {
  // 403 — the operator lacks `platform:claim`; the only fix is an admin grant,
  // so there is no self-service deep-link.
  forbidden: { guidanceKey: 'routes.chambers.recoveryForbidden', actions: [] },
  // 404 — the hub has no such chamber: the availability row the operator picked
  // from is stale (the chamber was de-registered). Re-reading the list is the
  // fix and the list is on this page; registration is admin-only, so there is no
  // self-service screen and — unlike 503 — nothing about connectivity to check.
  notFound: { guidanceKey: 'routes.chambers.recoveryNotFound', actions: [] },
  // 409 with no read-side evidence of unavailability — most likely another
  // operator holds it. Re-check the availability list above and retry when idle;
  // history shows the active run.
  conflict: { guidanceKey: 'routes.chambers.recoveryConflict', actions: ['sessions'] },
  // 409 where the availability row says the chamber is offline/disabled. Waiting
  // will not help, so the copy must NOT say "retry shortly"; the useful move is
  // another chamber, and diagnostics carries the node's connectivity health.
  conflictOffline: {
    guidanceKey: 'routes.chambers.recoveryConflictOffline',
    actions: ['diagnostics'],
  },
  // 503 — the hub answered but could not reach the node (proxy forward failed).
  // Distinct from `network`: central is up, this one chamber's node is not.
  unavailable: { guidanceKey: 'routes.chambers.recoveryUnavailable', actions: ['diagnostics'] },
  // No status — the hub itself was unreachable. Retry shortly; diagnostics
  // surfaces central connectivity health.
  network: { guidanceKey: 'routes.chambers.recoveryNetwork', actions: ['diagnostics'] },
  // Any other server-side failure — retry, and inspect diagnostics if it
  // persists.
  unknown: { guidanceKey: 'routes.chambers.recoveryUnknown', actions: ['diagnostics'] },
};
