import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import { createElement, type ReactElement, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  CHAMBER_PROGRESS_NOT_RUNNING_GRACE_POLLS,
  queryKeys,
  REFETCH_STRATEGIES,
} from '@/api/query-config';
import {
  advanceNotRunningStreak,
  chamberProgressRefetchInterval,
  isChamberRunComplete,
  streakForChamber,
  useChamberProgressPolling,
  INITIAL_NOT_RUNNING_STREAK,
  type ChamberProgressRefetchState,
  type ChamberScopedStreak,
  type NotRunningStreak,
} from '@/api/use-chamber-progress';

const platformApi = vi.hoisted(() => ({
  fetchChamberProgress: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

/**
 * S1 — chamber progress polling re-arm seal (fe-data-layer-robustness M1/D1,
 * 2026-07-19).
 *
 * **The defect this file reproduces.** `chamberProgressRefetchInterval` used to
 * return `false` for ANY `is_running: false` success. A chamber reports exactly
 * that in two entirely non-terminal situations — between the start command and
 * the node's first heartbeat, and after a node restart re-registers a chamber
 * whose run has not re-reported yet — so a single such snapshot parked the poll
 * permanently. The hook could not self-heal either: it read only
 * `REFETCH_STRATEGIES.CRITICAL.refetchInterval` (a number) and never spread the
 * strategy, so it inherited the QueryClient's `refetchOnWindowFocus: false` and
 * even returning to the tab did not resume it. The operator's only remote view
 * of a live chamber froze on a stale number, indistinguishable from a live one.
 *
 * The policy is sealed as a PURE function of (query state, streak) rather than
 * through a rendered hook: React Query's own timer behaviour is not under test
 * here, the stop/continue decision is, and a pure seal cannot go green for
 * timing reasons.
 */

const RUNNING: ChamberProgressRefetchState['data'] = {
  chamber_id: 'cham-1',
  progress: { is_running: true, completed: 3, total: 10, ratio: 0.3 },
};

/** Not running, work outstanding — the transient state D1 mis-read as terminal. */
const QUIET: ChamberProgressRefetchState['data'] = {
  chamber_id: 'cham-1',
  progress: { is_running: false, completed: 3, total: 10, ratio: 0.3 },
};

/** Not running, plan unknown (`total: 0`) — likewise NOT a completion signal. */
const UNKNOWN: ChamberProgressRefetchState['data'] = {
  chamber_id: 'cham-1',
  progress: { is_running: false, completed: 0, total: 0, ratio: 0 },
};

const COMPLETE: ChamberProgressRefetchState['data'] = {
  chamber_id: 'cham-1',
  progress: { is_running: false, completed: 10, total: 10, ratio: 1 },
};

function state(
  data: ChamberProgressRefetchState['data'],
  dataUpdatedAt: number,
): ChamberProgressRefetchState {
  return { data, status: 'success', dataUpdatedAt };
}

/** Drive N successive server observations through the same fold the hook uses. */
function foldObservations(observations: readonly ChamberProgressRefetchState[]): NotRunningStreak {
  return observations.reduce(advanceNotRunningStreak, INITIAL_NOT_RUNNING_STREAK);
}

describe('S1 — polling does not park on a single not-running snapshot', () => {
  it('keeps polling after the FIRST not-running observation (the D1 regression)', () => {
    const streak = foldObservations([state(QUIET, 1)]);
    expect(chamberProgressRefetchInterval(state(QUIET, 1), streak)).toBe(
      REFETCH_STRATEGIES.CRITICAL.refetchInterval,
    );
  });

  it('keeps polling for the whole grace window, then parks', () => {
    const observations = Array.from({ length: CHAMBER_PROGRESS_NOT_RUNNING_GRACE_POLLS }, (_, i) =>
      state(QUIET, i + 1),
    );
    // Every observation strictly inside the window still polls.
    for (let n = 1; n < observations.length; n += 1) {
      const streak = foldObservations(observations.slice(0, n));
      expect(chamberProgressRefetchInterval(state(QUIET, n), streak)).toBe(
        REFETCH_STRATEGIES.CRITICAL.refetchInterval,
      );
    }
    // Exhausting the window parks — an idle chamber must not poll forever.
    const exhausted = foldObservations(observations);
    expect(exhausted.count).toBe(CHAMBER_PROGRESS_NOT_RUNNING_GRACE_POLLS);
    expect(
      chamberProgressRefetchInterval(
        state(QUIET, CHAMBER_PROGRESS_NOT_RUNNING_GRACE_POLLS),
        exhausted,
      ),
    ).toBe(false);
  });

  it('re-arms when the run resumes mid-grace (streak resets)', () => {
    const resumed = foldObservations([state(QUIET, 1), state(RUNNING, 2)]);
    expect(resumed).toEqual(INITIAL_NOT_RUNNING_STREAK);
    // …and a not-running snapshot after the resume gets a FULL fresh window.
    const afterResume = advanceNotRunningStreak(resumed, state(QUIET, 3));
    expect(afterResume.count).toBe(1);
    expect(chamberProgressRefetchInterval(state(QUIET, 3), afterResume)).toBe(
      REFETCH_STRATEGIES.CRITICAL.refetchInterval,
    );
  });

  it('does not burn the grace window on re-evaluations of the SAME snapshot', () => {
    // React Query may evaluate `refetchInterval` several times per data update;
    // only distinct server observations may advance the streak.
    const sameSnapshot = state(QUIET, 42);
    const streak = foldObservations([sameSnapshot, sameSnapshot, sameSnapshot, sameSnapshot]);
    expect(streak.count).toBe(1);
    expect(chamberProgressRefetchInterval(sameSnapshot, streak)).toBe(
      REFETCH_STRATEGIES.CRITICAL.refetchInterval,
    );
  });

  it('parks immediately on the explicit completion signal (no grace spent)', () => {
    const streak = foldObservations([state(COMPLETE, 1)]);
    expect(isChamberRunComplete(COMPLETE.progress)).toBe(true);
    expect(chamberProgressRefetchInterval(state(COMPLETE, 1), streak)).toBe(false);
  });

  it('treats `total: 0` as unknown, not complete', () => {
    expect(isChamberRunComplete(UNKNOWN.progress)).toBe(false);
    const streak = foldObservations([state(UNKNOWN, 1)]);
    expect(chamberProgressRefetchInterval(state(UNKNOWN, 1), streak)).toBe(
      REFETCH_STRATEGIES.CRITICAL.refetchInterval,
    );
  });
});

describe('S1 — pre-existing branches preserved (behaviour oracle)', () => {
  it('polls while the run is active', () => {
    expect(chamberProgressRefetchInterval(state(RUNNING, 1))).toBe(
      REFETCH_STRATEGIES.CRITICAL.refetchInterval,
    );
  });

  it('keeps retrying when the FIRST fetch errored with no data', () => {
    expect(
      chamberProgressRefetchInterval({
        data: undefined,
        status: 'error',
        dataUpdatedAt: 0,
      }),
    ).toBe(REFETCH_STRATEGIES.CRITICAL.refetchInterval);
  });
});

describe('S1 — a chamberId switch grants the new chamber a FULL grace window', () => {
  // The regression this seals (Codex review, supervisor iteration 2): the
  // streak ref outlives a chamberId change, so a hook instance whose previous
  // chamber exhausted its grace parked the NEXT chamber's poll before it made
  // a single observation.
  const exhausted: ChamberScopedStreak = {
    chamberId: 'cham-1',
    streak: foldObservations(
      Array.from({ length: CHAMBER_PROGRESS_NOT_RUNNING_GRACE_POLLS }, (_, i) =>
        state(QUIET, i + 1),
      ),
    ),
  };

  it('resets an exhausted streak when the chamber changes', () => {
    expect(exhausted.streak.count).toBe(CHAMBER_PROGRESS_NOT_RUNNING_GRACE_POLLS);
    const scoped = streakForChamber(exhausted, 'cham-2');
    expect(scoped.chamberId).toBe('cham-2');
    expect(scoped.streak).toEqual(INITIAL_NOT_RUNNING_STREAK);
  });

  it('preserves the streak (same reference) while the chamber is unchanged', () => {
    expect(streakForChamber(exhausted, 'cham-1')).toBe(exhausted);
  });

  it('the new chamber polls on its first not-running snapshot despite the old exhaustion', () => {
    // End-to-end at policy level: exhaust cham-1, switch to cham-2, fold
    // cham-2's first quiet observation — the interval must be the CRITICAL
    // cadence, not `false`.
    const scoped = streakForChamber(exhausted, 'cham-2');
    const firstObservation = state(
      { ...QUIET, chamber_id: 'cham-2' },
      CHAMBER_PROGRESS_NOT_RUNNING_GRACE_POLLS + 1,
    );
    const streak = advanceNotRunningStreak(scoped.streak, firstObservation);
    expect(streak.count).toBe(1);
    expect(chamberProgressRefetchInterval(firstObservation, streak)).toBe(
      REFETCH_STRATEGIES.CRITICAL.refetchInterval,
    );
  });
});

describe('S1 — rendered wiring: the SAME hook instance re-arms across chambers', () => {
  // The pure seals above cannot catch a refactor that stops calling
  // `streakForChamber` from the hook, so the wiring itself is sealed with a
  // rendered hook and fake timers driving real refetch cycles.
  beforeEach(() => {
    // Non-zero base time: `INITIAL_NOT_RUNNING_STREAK.lastCountedUpdateAt` is 0
    // and a `dataUpdatedAt` of exactly 0 would be skipped as a re-evaluation.
    vi.useFakeTimers({ now: 60_000 });
    platformApi.fetchChamberProgress.mockReset();
    platformApi.fetchChamberProgress.mockImplementation((id: string) =>
      Promise.resolve({
        chamber_id: id,
        progress: { is_running: false, completed: 3, total: 10, ratio: 0.3 },
      }),
    );
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  function callsFor(id: string): number {
    return platformApi.fetchChamberProgress.mock.calls.filter(([calledId]) => calledId === id)
      .length;
  }

  it('an exhausted previous chamber does not park the next chamber (the iteration-2 regression)', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }): ReactElement =>
      createElement(QueryClientProvider, { client }, children);
    const { rerender, unmount } = renderHook(
      ({ id }: { id: string }) => useChamberProgressPolling(id),
      { wrapper, initialProps: { id: 'cham-1' } },
    );

    // Settle the mount fetch, then drive polls until cham-1's grace exhausts.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(callsFor('cham-1')).toBe(1);
    for (let i = 1; i < CHAMBER_PROGRESS_NOT_RUNNING_GRACE_POLLS; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(REFETCH_STRATEGIES.CRITICAL.refetchInterval);
      });
    }
    expect(callsFor('cham-1')).toBe(CHAMBER_PROGRESS_NOT_RUNNING_GRACE_POLLS);

    // Grace exhausted — cham-1 is parked (no further fetches on the cadence).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REFETCH_STRATEGIES.CRITICAL.refetchInterval * 2);
    });
    expect(callsFor('cham-1')).toBe(CHAMBER_PROGRESS_NOT_RUNNING_GRACE_POLLS);

    // Seed cham-2's cache with a quiet snapshot BEFORE the switch. This is the
    // deterministic shape of the regression: with cached data there is no
    // `data === undefined` evaluation window whose branch would incidentally
    // reset the streak, so a carried-over exhausted streak parks cham-2 on its
    // very first interval decision.
    client.setQueryData(queryKeys.chambers.progress('cham-2'), {
      chamber_id: 'cham-2',
      progress: { is_running: false, completed: 1, total: 10, ratio: 0.1 },
    });

    // Re-point the SAME hook instance at cham-2: the stale-cache refetch fires
    // unconditionally, so the discriminator is the SECOND fetch — it only
    // happens when cham-2 got a fresh grace window instead of inheriting
    // cham-1's exhausted streak.
    rerender({ id: 'cham-2' });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(callsFor('cham-2')).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REFETCH_STRATEGIES.CRITICAL.refetchInterval);
    });
    expect(callsFor('cham-2')).toBe(2);

    unmount();
  });
});

describe('S1 — the hook declares its cadence instead of inheriting it', () => {
  it('CRITICAL enables refetchOnWindowFocus (the re-arm path of last resort)', () => {
    // The hook spreads this strategy; if the SSOT ever flips this to `false`
    // the focus-return re-arm silently disappears again.
    expect(REFETCH_STRATEGIES.CRITICAL.refetchOnWindowFocus).toBe(true);
  });

  it('the grace window is a named SSOT constant, not a magic number', () => {
    expect(CHAMBER_PROGRESS_NOT_RUNNING_GRACE_POLLS).toBeGreaterThan(0);
    expect(Number.isInteger(CHAMBER_PROGRESS_NOT_RUNNING_GRACE_POLLS)).toBe(true);
  });
});
