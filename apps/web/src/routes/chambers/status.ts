import { type ChamberAvailabilityEnvelope } from '@/api/platform-client';
import { isKnownChamberStatus, type Translate } from '@/ui';

import { orDash } from './util';

/**
 * Canonical availability tokens this module branches on.
 *
 * The vocabulary itself is the backend `ChamberNodeStatus` SSOT, mirrored for
 * badge/label purposes in `@/ui::chamberStatusKind`; these two names exist so a
 * branch below reads as a decision rather than as a bare string, and so the two
 * predicates that key off the same token cannot drift apart by a typo.
 */
const STATUS_IDLE = 'idle';
const STATUS_OFFLINE = 'offline';

/** A chamber can start a new measurement only when it is idle and enabled. A
 *  single predicate (not a scattered literal) so the start affordance and the
 *  selectable-chamber list share one definition. Exported for unit testing. */
export function isStartableChamber(chamber: ChamberAvailabilityEnvelope): boolean {
  return chamber.enabled && chamber.status.trim().toLowerCase() === STATUS_IDLE;
}

/**
 * The availability facts that explain *why* a chamber cannot take work.
 *
 * Deliberately a `Pick` of the generated envelope rather than a hand-written
 * shape: the caller passes the row it already polled, and the token vocabulary
 * stays derived from the OpenAPI artifact.
 */
export type ChamberUnavailabilityFacts = Pick<
  ChamberAvailabilityEnvelope,
  'status' | 'unavailable_reason'
>;

/**
 * True when the availability row itself accounts for the chamber being unable
 * to accept a run — it is `offline`, or the read service attached an
 * `unavailable_reason` (disabled / never seen / heartbeat timeout).
 *
 * This is the read-side evidence that lets a 409 be told apart from "somebody
 * else is measuring on it": the backend collapses both causes into one
 * `ChamberNotAvailableError`, so the cause cannot be recovered from the error
 * body — only from the availability view the screen already holds. `false` means
 * "no evidence", never "definitely in use", and the caller degrades accordingly.
 */
export function isChamberUnavailable(chamber: ChamberUnavailabilityFacts): boolean {
  const reason = chamber.unavailable_reason;
  if (reason !== null && reason !== undefined) return true;
  return chamber.status.trim().toLowerCase() === STATUS_OFFLINE;
}

/** Resolve the chamber status label. A canonical status (idle/in_use/offline)
 *  uses its localized label; an unknown/forward-compat status the backend may
 *  add later falls back to a generic "unknown status" label echoing the raw
 *  value — never a missing i18n key string (P10 status fallback). The known set
 *  is the `isKnownChamberStatus` SSOT, not a local literal. */
export function chamberStatusLabel(
  t: (key: string, params?: Record<string, string | number>) => string,
  status: string,
): string {
  if (isKnownChamberStatus(status)) {
    return t(`routes.chambers.status.${status.trim().toLowerCase()}`);
  }
  return t('routes.chambers.statusUnknown', { status: orDash(status) });
}

/**
 * M2 — `unavailable_reason` token → i18n key.
 *
 * The token vocabulary comes from the backend OpenAPI artifact (derived from the
 * domain `UnavailableReason` SSOT), so it is never re-declared here — only
 * labelled. The key set is derived from the generated type and pinned with
 * `satisfies`, so a domain enum gaining a member breaks `typecheck` at this map
 * rather than silently rendering a missing key (runtime behaviour unchanged).
 *
 * Promoted out of `ChamberAdminPanel` (fe-w2-b-execution-freshness M2,
 * 2026-07-28): the reason is a READ-side fact, and living inside the
 * `platform:admin` panel bound it to a write-permission axis — the operator
 * choosing a chamber is exactly the one without admin. Two surfaces now render
 * it, so the map has to be owned by neither.
 */
export type UnavailableReasonToken = NonNullable<ChamberAvailabilityEnvelope['unavailable_reason']>;

const REASON_LABEL_KEY = {
  heartbeat_timeout: 'routes.chambers.reasonHeartbeatTimeout',
  disabled: 'routes.chambers.reasonDisabled',
  never_seen: 'routes.chambers.reasonNeverSeen',
  unknown: 'routes.chambers.reasonUnknown',
} satisfies Record<UnavailableReasonToken, string>;

/** Localized label for an `unavailable_reason`. A forward-compat token the
 *  backend may add before the FE regenerates types degrades to the map's own
 *  `unknown` entry (not a second copy of that key). */
export function chamberUnavailableReasonLabel(
  t: Translate,
  reason: UnavailableReasonToken,
): string {
  return t(REASON_LABEL_KEY[reason] ?? REASON_LABEL_KEY.unknown);
}

/**
 * Server-derived chamber-mode verdict → i18n label.
 *
 * ⚠️ **The verdict itself is NOT computed here.** Approval (a central ruling)
 * and realisation (a heartbeat-derived observation) are compared by one pure
 * function on the server, and this screen reads the token it produced. Deriving
 * it a second time in TypeScript is the drift the repository's
 * `Derived-Value No-Client-Recompute` rule exists to prevent — and here the two
 * copies would disagree about whether a chamber is in breach of company policy.
 *
 * Unknown tokens degrade to the raw string rather than throwing: a future
 * backend token must not blank the operator's table.
 */
const CHAMBER_MODE_VERDICT_KEYS: Readonly<Record<string, string>> = {
  UNDECLARED: 'routes.chambers.modeUndeclared',
  POLICY_CONFLICT: 'routes.chambers.modePolicyConflict',
  NOT_OBSERVED: 'routes.chambers.modeNotObserved',
  CONSISTENT: 'routes.chambers.modeConsistent',
};

export function chamberModeVerdictLabel(t: Translate, verdict: string | undefined): string {
  if (verdict === undefined || verdict === null) return orDash(undefined);
  const key = CHAMBER_MODE_VERDICT_KEYS[verdict];
  return key === undefined ? verdict : t(key);
}

/**
 * True when the verdict is the one unambiguous signal on this axis: the chamber
 * is serving web sessions without an approval on record.
 *
 * A predicate rather than an inline comparison so the badge and any future
 * consumer share one definition of "breach", and so the token literal lives in
 * exactly one place on this side of the wire.
 */
export function isChamberModeBreach(verdict: string | undefined): boolean {
  return verdict === 'POLICY_CONFLICT';
}
