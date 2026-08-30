import { describe, expect, it } from 'vitest';

import {
  CACHE_TIMES,
  CHAMBER_HEARTBEAT_TTL_SECONDS_DEFAULT,
  queryKeys,
  REFETCH_STRATEGIES,
} from '@/api/query-config';

/**
 * query-config SSOT tests — proves the key factory emits keys that are
 * BYTE-IDENTICAL to the arrays the routes used inline before the SSOT
 * (behaviour preservation oracle), that the key hierarchy keeps stable
 * prefixes (so prefix-scoped invalidation matches), and that the cache /
 * refetch strategy tiers carry the expected named durations.
 *
 * If a leaf factory's shape drifts, the byte-identical block fails here before
 * the route vitest regresses — the routes import these exact functions.
 */

describe('queryKeys — byte-identical to the prior inline arrays', () => {
  it('session domain', () => {
    expect(queryKeys.session.info()).toEqual(['session', 'info']);
    expect(queryKeys.session.progress()).toEqual(['session', 'progress']);
  });

  it('project domain (with and without the technology facet)', () => {
    expect(queryKeys.project.coverage('proj-1', 'BT')).toEqual([
      'project-coverage',
      'proj-1',
      'BT',
    ]);
    // techQuery omitted ⇒ trailing `undefined` slot preserved (the routes pass
    // `string | undefined`, so the key carries the undefined facet verbatim).
    expect(queryKeys.project.coverage('proj-1')).toEqual(['project-coverage', 'proj-1', undefined]);
    expect(queryKeys.project.claims('proj-1', 'BLE')).toEqual(['project-claims', 'proj-1', 'BLE']);
    expect(queryKeys.project.claims('proj-1')).toEqual(['project-claims', 'proj-1', undefined]);
    expect(queryKeys.project.syncStatus('proj-1')).toEqual(['project-sync-status', 'proj-1']);
    expect(queryKeys.project.memberships('proj-1')).toEqual(['project-memberships', 'proj-1']);
  });

  it('directory + pickerOptions are distinct leaves under the lists() prefix', () => {
    // project-status-visibility — the directory read keys per status; 'active'
    // by default. The picker reads one page and keys only by search term.
    expect(queryKeys.project.directory()).toEqual([
      'project-list',
      'directory',
      'active',
      undefined,
    ]);
    expect(queryKeys.project.directory('completed', 'SM')).toEqual([
      'project-list',
      'directory',
      'completed',
      'SM',
    ]);
    expect(queryKeys.project.pickerOptions()).toEqual(['project-list', 'options', undefined]);
    expect(queryKeys.project.pickerOptions('SM')).toEqual(['project-list', 'options', 'SM']);

    // W3-B M-C: the two leaves hold DIFFERENT cache shapes ({pages:[…]} vs one
    // flat PlatformPage), so they must never collide on a key…
    expect(queryKeys.project.directory('active', 'SM')).not.toEqual(
      queryKeys.project.pickerOptions('SM'),
    );
    // …yet both must stay under lists(), which is the array-prefix a single
    // invalidateQueries() uses to refetch every status + search variant.
    const prefix = queryKeys.project.lists();
    const leaves = [
      queryKeys.project.directory('active'),
      queryKeys.project.directory('completed', 'SM'),
      queryKeys.project.directory('all'),
      queryKeys.project.pickerOptions(),
      queryKeys.project.pickerOptions('SM'),
    ] as const;
    for (const key of leaves) {
      expect(key.slice(0, prefix.length)).toEqual(prefix);
    }
  });

  it('report domain', () => {
    expect(queryKeys.report.stats()).toEqual(['report-automation', 'stats']);
    expect(queryKeys.report.request(42)).toEqual(['report-request', 42]);
    expect(queryKeys.report.request(null)).toEqual(['report-request', null]);
    expect(queryKeys.report.outputs(42)).toEqual(['report-outputs', 42]);
    expect(queryKeys.report.sessionArtifacts(7)).toEqual(['session-artifacts', 7]);
    expect(queryKeys.report.sessionArtifacts(null)).toEqual(['session-artifacts', null]);
  });

  it('session attempts + provider', () => {
    expect(queryKeys.sessionAttempts.list(9)).toEqual(['session-attempts', 9]);
    expect(queryKeys.sessionAttempts.list(null)).toEqual(['session-attempts', null]);
    expect(queryKeys.jobs.status()).toEqual(['headless-jobs', 'status']);
    expect(queryKeys.jobs.list()).toEqual(['headless-jobs', 'list']);
    expect(queryKeys.provider.uiDescriptor('unlicensed')).toEqual([
      'provider-ui-descriptor',
      'unlicensed',
    ]);
  });
});

describe('queryKeys — hierarchy (prefix helpers for scoped invalidation)', () => {
  it('session.all is a genuine array-prefix of every session leaf', () => {
    // TanStack matches keys element-wise, so a prefix invalidation of
    // `session.all` (`['session']`) reaches both nested session leaves.
    const all = queryKeys.session.all;
    expect(all).toEqual(['session']);
    for (const leaf of [queryKeys.session.info(), queryKeys.session.progress()]) {
      expect(leaf.slice(0, all.length)).toEqual([...all]);
    }
  });

  it('sessionAttempts exposes a `list` leaf under a resource namespace', () => {
    // The paginated attempts resource is namespaced so future variants
    // (e.g. a detail leaf) attach without breaking the existing call site.
    expect(typeof queryKeys.sessionAttempts.list).toBe('function');
    expect(queryKeys.sessionAttempts.list(1)).toEqual(['session-attempts', 1]);
  });
});

describe('queryKeys — invalidation reuses the same call (no hand re-assembly)', () => {
  it('a query key equals its invalidation key for the same inputs', () => {
    // The contract: query definition and invalidateQueries call the SAME
    // factory, so identical inputs yield identical keys (deep-equal).
    expect(queryKeys.project.claims('p', 'BT')).toEqual(queryKeys.project.claims('p', 'BT'));
    expect(queryKeys.project.memberships('p')).toEqual(queryKeys.project.memberships('p'));
    expect(queryKeys.session.progress()).toEqual(queryKeys.session.progress());
  });

  it('keeps a stable prefix so prefix-scoped invalidation matches', () => {
    expect(queryKeys.project.coverage('p', 'BT')[0]).toBe('project-coverage');
    expect(queryKeys.project.coverage('p', 'BT')[1]).toBe('p');
  });
});

describe('CACHE_TIMES', () => {
  it('exposes named durations (no raw millisecond literals at call sites)', () => {
    expect(CACHE_TIMES.SHORT).toBe(60_000);
    expect(CACHE_TIMES.MEDIUM).toBe(5 * 60_000);
    expect(CACHE_TIMES.LONG).toBe(30 * 60_000);
    expect(CACHE_TIMES.SHORT).toBeLessThan(CACHE_TIMES.MEDIUM);
    expect(CACHE_TIMES.MEDIUM).toBeLessThan(CACHE_TIMES.LONG);
  });
});

describe('REFETCH_STRATEGIES', () => {
  it('NORMAL is the QueryClient default tier (slightly stale, no poll/focus)', () => {
    // staleTime is 30s — the value the pre-SSOT QueryClient default carried
    // inline. Behaviour preservation is this increment's oracle, so NORMAL must
    // keep 30s and NOT drift up to CACHE_TIMES.SHORT (60s).
    expect(REFETCH_STRATEGIES.NORMAL.staleTime).toBe(30_000);
    expect(REFETCH_STRATEGIES.NORMAL.staleTime).not.toBe(CACHE_TIMES.SHORT);
    expect(REFETCH_STRATEGIES.NORMAL.gcTime).toBe(CACHE_TIMES.MEDIUM);
    expect(REFETCH_STRATEGIES.NORMAL.refetchInterval).toBe(false);
    expect(REFETCH_STRATEGIES.NORMAL.refetchOnWindowFocus).toBe(false);
  });

  it('IMPORTANT shares the 30s stale window but refetches on focus', () => {
    expect(REFETCH_STRATEGIES.IMPORTANT.staleTime).toBe(30_000);
    expect(REFETCH_STRATEGIES.IMPORTANT.refetchOnWindowFocus).toBe(true);
    expect(REFETCH_STRATEGIES.IMPORTANT.refetchInterval).toBe(false);
  });

  it('CRITICAL carries the live-poll cadence used by control progress', () => {
    expect(REFETCH_STRATEGIES.CRITICAL.refetchInterval).toBe(2_000);
    expect(REFETCH_STRATEGIES.CRITICAL.refetchOnWindowFocus).toBe(true);
    expect(REFETCH_STRATEGIES.CRITICAL.staleTime).toBe(0);
  });

  it('STATIC caches longest for rarely-changing reference data', () => {
    expect(REFETCH_STRATEGIES.STATIC.staleTime).toBe(CACHE_TIMES.LONG);
    expect(REFETCH_STRATEGIES.STATIC.gcTime).toBe(CACHE_TIMES.LONG);
    expect(REFETCH_STRATEGIES.STATIC.refetchInterval).toBe(false);
  });

  /**
   * MONITORED (fe-w2-b-execution-freshness M4, 2026-07-28) — the tier that was
   * missing. Chamber availability sat on IMPORTANT, whose `refetchInterval` is
   * `false`, so an operator watching the fleet on a wall monitor saw a snapshot
   * frozen at page load: a chamber that went offline stayed "idle" until someone
   * happened to refocus the tab. CRITICAL (2s) is the wrong repair — the live
   * axis is already served by the WS progress relay, so a 2s poll would be pure
   * request volume for a transition measured in tens of seconds.
   */
  describe('MONITORED — polled supervision views (chamber availability)', () => {
    it('actually polls (this is the tier IMPORTANT could not provide)', () => {
      expect(REFETCH_STRATEGIES.MONITORED.refetchInterval).not.toBe(false);
      expect(typeof REFETCH_STRATEGIES.MONITORED.refetchInterval).toBe('number');
    });

    it('polls fast enough to notice an offline transition within one TTL', () => {
      // Availability flips to offline once `heartbeat_ttl_seconds` elapses with
      // no heartbeat. Sampling twice per TTL keeps the screen's recognition lag
      // strictly inside the timescale of the transition it is reporting.
      //
      // The TTL here is the BACKEND default (90s), read from the domain SSOT —
      // not the 30 that appears in this suite's chamber fixture and in the
      // admin panel's stale draft fallback. Asserting the relation (rather than
      // just the literal) is what keeps the cadence honest if the mirror moves.
      const interval = REFETCH_STRATEGIES.MONITORED.refetchInterval;
      expect(CHAMBER_HEARTBEAT_TTL_SECONDS_DEFAULT).toBe(90);
      expect(interval).toBe(45_000);
      expect(interval * 2).toBeLessThanOrEqual(CHAMBER_HEARTBEAT_TTL_SECONDS_DEFAULT * 1_000);
    });

    it('sits between CRITICAL and IMPORTANT rather than duplicating either', () => {
      const interval = REFETCH_STRATEGIES.MONITORED.refetchInterval;
      expect(interval).toBeGreaterThan(REFETCH_STRATEGIES.CRITICAL.refetchInterval);
      expect(REFETCH_STRATEGIES.IMPORTANT.refetchInterval).toBe(false);
    });

    it('serves fresh reads and refetches on focus, but never in a background tab', () => {
      expect(REFETCH_STRATEGIES.MONITORED.staleTime).toBe(0);
      expect(REFETCH_STRATEGIES.MONITORED.gcTime).toBe(CACHE_TIMES.SHORT);
      expect(REFETCH_STRATEGIES.MONITORED.refetchOnWindowFocus).toBe(true);
      // Declared explicitly rather than left to the library default: a monitoring
      // screen is exactly the one left open on a second monitor for hours, and a
      // background tab polling every 15s is the "polling explosion" the contract
      // forbids.
      expect(REFETCH_STRATEGIES.MONITORED.refetchIntervalInBackground).toBe(false);
    });
  });
});
