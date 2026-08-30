import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';

import { type ChamberProgressEvent } from '@/api/chamber-events';
import { applyChamberProgressEvent } from '@/api/chamber-progress-stream';
import { queryKeys } from '@/api/query-config';

import type { ChamberMeasurementSnapshot } from '@/api/platform-client';

/**
 * 멀티챔버 P7/B4 (2026-06-18) — WS → query cache bridge unit tests.
 *
 * `applyChamberProgressEvent` is a pure cache mutation: it writes each progress
 * event into the SAME per-chamber key the polling fallback uses (no second
 * source of truth). The availability list query is intentionally untouched (its
 * progress field is unused by the UI), so a progress event never fabricates a
 * list entry.
 */

function event(chamberId: string, completed: number): ChamberProgressEvent {
  return {
    kind: 'chamber_progress',
    chamber_id: chamberId,
    progress: { is_running: true, completed, total: 10, ratio: completed / 10 },
    session_id: 's1',
    occurred_at: '2026-06-18T00:00:00+00:00',
  };
}

describe('applyChamberProgressEvent', () => {
  it('refreshes the per-chamber progress snapshot key (UI read path)', () => {
    const qc = new QueryClient();
    applyChamberProgressEvent(qc, event('chA', 6));
    const snap = qc.getQueryData<ChamberMeasurementSnapshot>(queryKeys.chambers.progress('chA'));
    expect(snap?.chamber_id).toBe('chA');
    expect(snap?.progress.completed).toBe(6);
  });

  it('writes the same key shape the polling fallback uses (no parallel cache)', () => {
    const qc = new QueryClient();
    // Simulate a prior poll result, then a fresher WS event overwrites it.
    qc.setQueryData(queryKeys.chambers.progress('chA'), {
      chamber_id: 'chA',
      progress: { is_running: true, completed: 1, total: 10, ratio: 0.1 },
    });
    applyChamberProgressEvent(qc, event('chA', 9));
    const snap = qc.getQueryData<ChamberMeasurementSnapshot>(queryKeys.chambers.progress('chA'));
    expect(snap?.progress.completed).toBe(9); // WS event won (fresher)
  });

  it('does not touch / fabricate the availability list query', () => {
    const qc = new QueryClient();
    applyChamberProgressEvent(qc, event('chA', 3));
    // The list query is owned by the polling fallback — a progress event must
    // never create or mutate it.
    expect(qc.getQueryData(queryKeys.chambers.list())).toBeUndefined();
    expect(qc.getQueryData(queryKeys.chambers.progress('chA'))).toBeDefined();
  });
});
