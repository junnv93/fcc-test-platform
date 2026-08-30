import { describe, expect, it } from 'vitest';

import { isRunningChamber, summarizeChamberFleet } from '@/shared/chamber-fleet';
import { sessionHistoryHref } from '@/shared/route-links';

import type { ChamberAvailabilityEnvelope } from '@/api/platform-client';

/**
 * 멀티챔버 P12 — fleet summary + 세션 deep-link SSOT 단위 테스트.
 *
 * `summarizeChamberFleet`/`isRunningChamber`는 가용성 목록을 순수 파생하고,
 * `sessionHistoryHref`는 숫자 세션만 deep-link로 만든다(parsePositiveId SSOT 위임).
 */

// `over` is a loose record so forward-compat / whitespace status variants
// (`'degraded'`, `'  IN_USE '`) — runtime-valid strings the backend may emit —
// can be exercised without fighting the generated strict status union.
function chamber(over: Record<string, unknown>): ChamberAvailabilityEnvelope {
  return {
    chamber_id: 'c',
    name: 'C',
    base_url: 'http://node:8000',
    enabled: true,
    heartbeat_ttl_seconds: 30,
    reported_status: 'idle',
    last_heartbeat_at: '2026-06-16T00:00:00+00:00',
    session_id: null,
    status: 'idle',
    ...over,
  } as unknown as ChamberAvailabilityEnvelope;
}

describe('isRunningChamber', () => {
  it('is true only for an in_use chamber (status-mapping SSOT)', () => {
    expect(isRunningChamber(chamber({ status: 'in_use' }))).toBe(true);
    expect(isRunningChamber(chamber({ status: 'idle' }))).toBe(false);
    expect(isRunningChamber(chamber({ status: 'offline' }))).toBe(false);
    // Forward-compat / unknown status is NOT treated as running.
    expect(isRunningChamber(chamber({ status: 'degraded' }))).toBe(false);
    // Case / whitespace variants normalize through the SSOT.
    expect(isRunningChamber(chamber({ status: '  IN_USE ' }))).toBe(true);
  });
});

describe('summarizeChamberFleet', () => {
  it('counts per canonical status, buckets unknown, and collects the running subset', () => {
    const list = [
      chamber({ chamber_id: 'a', status: 'idle' }),
      chamber({ chamber_id: 'b', status: 'in_use' }),
      chamber({ chamber_id: 'c', status: 'in_use' }),
      chamber({ chamber_id: 'd', status: 'offline' }),
      chamber({ chamber_id: 'e', status: 'degraded' }),
    ];
    const summary = summarizeChamberFleet(list);
    expect(summary.total).toBe(5);
    expect(summary.counts.idle).toBe(1);
    expect(summary.counts.in_use).toBe(2);
    expect(summary.counts.offline).toBe(1);
    expect(summary.unknown).toBe(1);
    // running preserves source order and contains exactly the in_use chambers.
    expect(summary.running.map((c) => c.chamber_id)).toEqual(['b', 'c']);
  });

  it('returns zeroed canonical counts for an empty list', () => {
    const summary = summarizeChamberFleet([]);
    expect(summary.total).toBe(0);
    expect(summary.counts).toEqual({ idle: 0, in_use: 0, offline: 0 });
    expect(summary.unknown).toBe(0);
    expect(summary.running).toEqual([]);
  });
});

describe('sessionHistoryHref', () => {
  it('builds a /sessions deep-link for a positive numeric session id', () => {
    expect(sessionHistoryHref('42')).toBe('/sessions?session=42');
  });

  it('returns null for a non-numeric, empty, or absent session id', () => {
    expect(sessionHistoryHref('node-abc')).toBeNull();
    expect(sessionHistoryHref('0')).toBeNull();
    expect(sessionHistoryHref('')).toBeNull();
    expect(sessionHistoryHref(null)).toBeNull();
    expect(sessionHistoryHref(undefined)).toBeNull();
  });
});
