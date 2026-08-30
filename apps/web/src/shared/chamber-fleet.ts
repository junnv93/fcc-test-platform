/**
 * Chamber fleet summary SSOT (멀티챔버 P12, 2026-06-16).
 *
 * The chambers screen used to show only the raw availability table. P12 adds a
 * "data at a glance" layer: a fleet-wide count summary + a per-running-chamber
 * progress overview. This module owns the *pure derivation* of both from a
 * `fetchChambers()` availability list, so the route stays declarative and the
 * derivation is unit-tested without a render.
 *
 * The status buckets are keyed by the canonical {@link KNOWN_CHAMBER_STATUSES}
 * tokens (not local `'idle'`/`'in_use'`/`'offline'` literals), and the
 * "is this chamber running?" predicate delegates to the {@link chamberStatusKind}
 * SSOT (`in_use` ⇒ badge kind `'running'`). So adding/renaming a chamber status
 * happens in exactly one place (`@/ui::status-mapping`) and this summary follows.
 */
import { chamberStatusKind, KNOWN_CHAMBER_STATUSES } from '@/ui';

import type { ChamberAvailabilityEnvelope } from '@/api/platform-client';

/** A canonical chamber status token (`'idle' | 'in_use' | 'offline'`). */
export type KnownChamberStatus = (typeof KNOWN_CHAMBER_STATUSES)[number];

export interface ChamberFleetSummary {
  /** Total chambers in the availability list. */
  readonly total: number;
  /** Count per canonical status token — every known token is present (0 when
   *  none), so the strip can render a stable column set. */
  readonly counts: Readonly<Record<KnownChamberStatus, number>>;
  /** Chambers whose status is NOT a canonical token (backend forward-compat). */
  readonly unknown: number;
  /** The in-use chambers, for the per-chamber progress overview. Preserves the
   *  source order so the overview row order is stable across refetches. */
  readonly running: readonly ChamberAvailabilityEnvelope[];
}

/** True when a chamber is actively measuring. Delegates to the status-mapping
 *  SSOT: `in_use` is the one canonical status whose badge kind is `'running'`,
 *  so this never re-declares the `'in_use'` literal. An unknown status (kind
 *  `'stale'`) is NOT treated as running — we only poll progress for a chamber
 *  the central view positively reports as in-use. */
export function isRunningChamber(chamber: ChamberAvailabilityEnvelope): boolean {
  return chamberStatusKind(chamber.status) === 'running';
}

/** Initialize a zero count for every canonical status token. */
function zeroCounts(): Record<KnownChamberStatus, number> {
  const counts = {} as Record<KnownChamberStatus, number>;
  for (const status of KNOWN_CHAMBER_STATUSES) counts[status] = 0;
  return counts;
}

/**
 * Summarize a chamber availability list into fleet counts + the running subset.
 * Pure — no fetch, no clock. A status is bucketed by its trimmed/lowercased
 * canonical token; anything outside {@link KNOWN_CHAMBER_STATUSES} increments
 * `unknown` (so a forward-compat status is counted, never silently dropped).
 */
export function summarizeChamberFleet(
  chambers: readonly ChamberAvailabilityEnvelope[],
): ChamberFleetSummary {
  const counts = zeroCounts();
  let unknown = 0;
  const running: ChamberAvailabilityEnvelope[] = [];
  const known = new Set<string>(KNOWN_CHAMBER_STATUSES);
  for (const chamber of chambers) {
    const token = chamber.status.trim().toLowerCase();
    if (known.has(token)) {
      counts[token as KnownChamberStatus] += 1;
    } else {
      unknown += 1;
    }
    if (isRunningChamber(chamber)) running.push(chamber);
  }
  return { total: chambers.length, counts, unknown, running };
}
