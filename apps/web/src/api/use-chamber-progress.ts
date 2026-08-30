/**
 * useChamberProgressPolling — chamber measurement-progress polling SSOT
 * (C2 query-hooks-ssot, 2026-06-17; re-arm policy fe-data-layer-robustness M1,
 * 2026-07-19).
 *
 * Two chambers views poll the same central-proxy progress endpoint (P5) with the
 * same self-healing cadence: the per-running-chamber overview row
 * (`ChamberRunRow`) and the post-start progress panel (`ChamberProgress`). Both
 * inlined a byte-identical `useQuery` + `refetchInterval` block and then
 * re-derived the hard/transient error split by hand. The two `refetchInterval`
 * closures drifted out of sync trivially; this hook owns the polling policy and
 * returns the {@link categorizeQueryStatus} phase so both views branch on one
 * definition.
 *
 * ## Polling policy
 *
 * Poll on the CRITICAL cadence while the run is active; on a transient fetch
 * error React Query retains the last successful data so `is_running` stays true
 * and polling self-heals; if the FIRST fetch errors (no data yet) keep retrying
 * at the critical cadence rather than parking forever.
 *
 * **M1 (D1) — stopping is now an explicit decision, not a single observation.**
 * The prior policy returned `false` on the first `is_running: false` success,
 * treating it as terminal. It is not: the central relay reports not-running
 * between a start command and the node's first heartbeat, and again after a node
 * restart re-registers a chamber whose run has not yet re-reported. Once parked,
 * nothing re-armed the poll — the hook read only the *interval value* out of
 * `REFETCH_STRATEGIES.CRITICAL` and never spread the strategy, so it inherited
 * the QueryClient default `refetchOnWindowFocus: false` and a focus return could
 * not resume it either. The operator's only remote window onto a live chamber
 * froze on a stale snapshot.
 *
 * Now polling stops only on an *explicit* completion signal
 * ({@link isChamberRunComplete}), and any other not-running snapshot is held in
 * a grace window of `CHAMBER_PROGRESS_NOT_RUNNING_GRACE_POLLS` consecutive
 * observations before parking. The hook also spreads the full CRITICAL strategy
 * so its cadence is declared rather than inherited.
 *
 * **M1 rework — the streak is scoped to ONE chamber.** The grace window counts
 * consecutive observations *of the same poll target*. A hook instance survives
 * a `chamberId` change (overview rows recycle, route params swap), so without
 * scoping, a previous chamber's exhausted grace would park the next chamber's
 * poll before it made a single observation. {@link streakForChamber} resets the
 * streak whenever the stored chamber differs from the one being polled.
 */
import { useQuery } from '@tanstack/react-query';
import { useRef } from 'react';

import { type ApiError } from '@/shared/api-error';

import { fetchChamberProgress, type ChamberMeasurementSnapshot } from './platform-client';
import {
  CHAMBER_PROGRESS_NOT_RUNNING_GRACE_POLLS,
  queryKeys,
  REFETCH_STRATEGIES,
} from './query-config';
import { categorizeQueryStatus, type QueryPhase } from './query-status';

export interface ChamberProgressPolling {
  /** Self-healing phase (loading / hardError / transientError / ready). */
  readonly phase: QueryPhase<ChamberMeasurementSnapshot>;
  /** Last-known snapshot — present in `ready` and `transientError`. */
  readonly snapshot: ChamberMeasurementSnapshot | undefined;
  /** The raw query error (for `describeApiError`), `null` when not errored. */
  readonly error: ApiError | null;
}

/** The minimal query-state surface the polling policy reads. */
export interface ChamberProgressRefetchState {
  readonly data: ChamberMeasurementSnapshot | undefined;
  readonly status: 'pending' | 'error' | 'success';
  /**
   * Timestamp of the most recent successful data write. Used ONLY to count each
   * distinct snapshot once — React Query may evaluate `refetchInterval` several
   * times for the same data, and a per-evaluation counter would burn the grace
   * window without a single new server observation.
   */
  readonly dataUpdatedAt: number;
}

/**
 * Consecutive not-running observations, tagged with the update they were
 * counted at so re-evaluation of the same snapshot is idempotent.
 */
export interface NotRunningStreak {
  readonly lastCountedUpdateAt: number;
  readonly count: number;
}

/** Neutral starting streak (nothing observed yet). */
export const INITIAL_NOT_RUNNING_STREAK: NotRunningStreak = {
  lastCountedUpdateAt: 0,
  count: 0,
};

/**
 * Explicit run-completion signal.
 *
 * `ChamberSessionProgress` has no terminal field (`{is_running, completed,
 * total, ratio}` — see the generated platform schema), so completion is derived:
 * the run is not running AND every planned item is accounted for. `total === 0`
 * is deliberately NOT complete — a plan with no items is an unstarted/unknown
 * run, exactly the state the grace window exists to tolerate.
 */
export function isChamberRunComplete(progress: ChamberMeasurementSnapshot['progress']): boolean {
  return !progress.is_running && progress.total > 0 && progress.completed >= progress.total;
}

/**
 * A not-running streak tagged with the chamber it was observed on. The streak
 * is meaningless across chambers — grace is a per-target budget, not a
 * per-hook-instance one.
 */
export interface ChamberScopedStreak {
  readonly chamberId: string;
  readonly streak: NotRunningStreak;
}

/**
 * Return `previous` if it already belongs to `chamberId`, otherwise a fresh
 * neutral streak for the new chamber. Pure and idempotent — safe to apply on
 * every `refetchInterval` evaluation, including from a stale closure still
 * pointing at the previous chamber (it then only ever touches its own scope).
 */
export function streakForChamber(
  previous: ChamberScopedStreak,
  chamberId: string,
): ChamberScopedStreak {
  if (previous.chamberId === chamberId) {
    return previous;
  }
  return { chamberId, streak: INITIAL_NOT_RUNNING_STREAK };
}

/**
 * Fold one query-state observation into the not-running streak. Pure — the
 * caller owns the storage. Any running (or absent) snapshot resets the streak,
 * so the grace window measures *consecutive* not-running observations.
 */
export function advanceNotRunningStreak(
  previous: NotRunningStreak,
  state: ChamberProgressRefetchState,
): NotRunningStreak {
  if (state.data === undefined || state.data.progress.is_running) {
    return INITIAL_NOT_RUNNING_STREAK;
  }
  if (state.dataUpdatedAt === previous.lastCountedUpdateAt) {
    return previous; // same snapshot re-evaluated — do not double-count.
  }
  return {
    lastCountedUpdateAt: state.dataUpdatedAt,
    count: previous.count + 1,
  };
}

/**
 * Polling cadence policy for chamber progress — a pure function of the query
 * state plus the caller-owned not-running streak, so every branch is
 * unit-sealable without a React render.
 */
export function chamberProgressRefetchInterval(
  state: ChamberProgressRefetchState,
  streak: NotRunningStreak = INITIAL_NOT_RUNNING_STREAK,
): number | false {
  if (state.data?.progress.is_running) {
    return REFETCH_STRATEGIES.CRITICAL.refetchInterval;
  }
  if (state.status === 'error' && state.data === undefined) {
    return REFETCH_STRATEGIES.CRITICAL.refetchInterval;
  }
  if (state.data === undefined) {
    return false; // pending, nothing observed yet — unchanged from C2.
  }
  if (isChamberRunComplete(state.data.progress)) {
    return false; // explicit terminal signal — the only permanent stop.
  }
  if (streak.count >= CHAMBER_PROGRESS_NOT_RUNNING_GRACE_POLLS) {
    return false; // grace exhausted — chamber is idle, not merely quiet.
  }
  return REFETCH_STRATEGIES.CRITICAL.refetchInterval;
}

export function useChamberProgressPolling(chamberId: string): ChamberProgressPolling {
  // Streak storage lives in a ref (not state): it must not trigger a render,
  // and React Query reads it from inside the `refetchInterval` callback. The
  // ref outlives chamberId changes, so the streak carries its chamber and is
  // re-scoped inside the callback (not at render) — a stale closure for the
  // previous chamber then cannot corrupt the new chamber's fresh grace window.
  const streakRef = useRef<ChamberScopedStreak>({
    chamberId,
    streak: INITIAL_NOT_RUNNING_STREAK,
  });

  const query = useQuery<ChamberMeasurementSnapshot, ApiError>({
    // Declare the full CRITICAL strategy rather than inheriting the client
    // defaults — M1: `refetchOnWindowFocus` must be on for this view so a
    // backgrounded tab re-arms on focus even if the grace window expired.
    ...REFETCH_STRATEGIES.CRITICAL,
    queryKey: queryKeys.chambers.progress(chamberId),
    queryFn: () => fetchChamberProgress(chamberId),
    refetchInterval: (q) => {
      const scoped = streakForChamber(streakRef.current, chamberId);
      const streak = advanceNotRunningStreak(scoped.streak, q.state);
      streakRef.current = { chamberId, streak };
      return chamberProgressRefetchInterval(q.state, streak);
    },
  });

  return {
    phase: categorizeQueryStatus({
      data: query.data,
      isError: query.isError,
      error: query.error,
    }),
    snapshot: query.data,
    error: query.error,
  };
}
