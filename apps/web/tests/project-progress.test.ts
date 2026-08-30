import { describe, expect, it } from 'vitest';

import { progressBadgeStatus, rollupForArea } from '@/shared/project-progress';

import type { ProgressBucketEnvelope } from '@/api/platform-client';

/**
 * Phase 6 wiring (2026-07-03) — shared progress-rollup SSOT.
 *
 * The pure helpers moved out of `routes/progress.tsx` (byte-identical logic, now
 * sealed by `progress.test.tsx` which imports them here). This file seals the
 * NEW fields-badge additions — `rollupForArea` + `progressBadgeStatus` — and,
 * critically, that they reuse the "가짜 0% 금지" honesty rule: no priced time →
 * percent null → the badge renders "시간 미설정" (status 'stale'), never a
 * fabricated 0%.
 */

function bucket(over: Partial<ProgressBucketEnvelope>): ProgressBucketEnvelope {
  return {
    progress_area: 'unlicensed_conducted',
    progress_bucket_id: 'unii_1',
    planned_minutes: 0,
    completed_minutes: 0,
    percent: null,
    total_conditions: 0,
    priced_conditions: 0,
    unpriced_conditions: 0,
    unbucketable_conditions: 0,
    ...over,
  };
}

describe('rollupForArea', () => {
  it('rolls up only the requested area, computing its percent', () => {
    const rows = [
      bucket({ progress_area: 'unlicensed_conducted', planned_minutes: 12, completed_minutes: 3 }),
      bucket({ progress_area: 'unlicensed_conducted', planned_minutes: 8, completed_minutes: 5 }),
      bucket({
        progress_area: 'unlicensed_radiated',
        planned_minutes: 100,
        completed_minutes: 100,
      }),
    ];
    const totals = rollupForArea(rows, 'unlicensed_conducted');
    expect(totals).not.toBeNull();
    expect(totals?.plannedMinutes).toBe(20);
    expect(totals?.completedMinutes).toBe(8);
    expect(totals?.percent).toBe(40);
  });

  it('returns null when the area has no rows (no fabricated 0%)', () => {
    const rows = [bucket({ progress_area: 'unlicensed_conducted', planned_minutes: 5 })];
    expect(rollupForArea(rows, 'mmwave')).toBeNull();
    expect(rollupForArea([], 'unlicensed_conducted')).toBeNull();
  });

  it('keeps percent null for an area with no priced time (가짜 0% 금지)', () => {
    const rows = [
      bucket({ progress_area: 'unlicensed_conducted', planned_minutes: 0, unpriced_conditions: 4 }),
    ];
    const totals = rollupForArea(rows, 'unlicensed_conducted');
    expect(totals?.percent).toBeNull();
    expect(totals?.unpricedConditions).toBe(4);
  });
});

describe('progressBadgeStatus', () => {
  it('maps null (no priced time) to stale — 시간 미설정, not a 0% badge', () => {
    expect(progressBadgeStatus(null)).toBe('stale');
  });

  it('maps a completed percent to pass', () => {
    expect(progressBadgeStatus(100)).toBe('pass');
    expect(progressBadgeStatus(150)).toBe('pass');
  });

  it('maps an in-progress percent to running', () => {
    expect(progressBadgeStatus(1)).toBe('running');
    expect(progressBadgeStatus(50)).toBe('running');
  });

  it('maps a priced-but-untouched 0% to draft (distinct from null)', () => {
    expect(progressBadgeStatus(0)).toBe('draft');
  });
});
