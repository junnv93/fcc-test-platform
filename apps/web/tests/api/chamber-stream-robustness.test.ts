import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import {
  chamberReconnectDecision,
  createChamberProgressStream,
  parseChamberProgressEvent,
  parseChamberProgressSnapshot,
  type ChamberProgressEvent,
  type ChamberStreamCloseReason,
  type ChamberStreamStatus,
} from '@/api/chamber-events';
import { applyChamberProgressEvent, isChamberEventFresh } from '@/api/chamber-progress-stream';
import { queryKeys } from '@/api/query-config';

import type { ChamberMeasurementSnapshot } from '@/api/platform-client';

/**
 * S2 / S3 / S4 — chamber WS stream robustness seals
 * (fe-data-layer-robustness M2·M3 / D2·D3, 2026-07-19).
 *
 * Three independent defects lived in this stream, all with the same shape: the
 * client trusted the network more than the network deserves.
 *
 *  - **D2 ordering (S2)** — every event was written to the cache
 *    unconditionally. WS delivery is ordered per *connection*, but this stream
 *    reconnects and the relay replays from its own buffer, so a stale frame
 *    arriving after a newer one rolled displayed progress backwards
 *    (42% → 17% → 42%). `occurred_at` was parsed and then never read.
 *  - **D2 shape (S3)** — the parser accepted any non-null object as `progress`
 *    and reached the cache via `progress as ChamberProgressSnapshot`. A relay
 *    drift that dropped `total` therefore rendered `NaN%` from a value the type
 *    system claimed was complete.
 *  - **D3 reconnect (S4)** — `onclose` ignored the close code and always
 *    reconnected with unbounded backoff, so a policy rejection (1008 — the
 *    backend's AuthZ refusal) became an infinite failing retry loop.
 */

// ─── test doubles ──────────────────────────────────────────────────────────

class FakeSocket {
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onclose: ((event: unknown) => void) | null = null;

  close(): void {
    /* no-op */
  }
  emitOpen(): void {
    this.onopen?.({});
  }
  /** Close WITH a code — the information the old `onclose` threw away. */
  emitClose(code?: number): void {
    this.onclose?.(code === undefined ? {} : { code });
  }
}

function makeScheduler(): {
  setTimeoutFn: (handler: () => void, delay: number) => number;
  clearTimeoutFn: (handle: number) => void;
  scheduledCount: () => number;
  runNext: () => void;
} {
  const tasks = new Map<number, () => void>();
  let id = 0;
  let scheduled = 0;
  return {
    setTimeoutFn: (handler) => {
      id += 1;
      scheduled += 1;
      tasks.set(id, handler);
      return id;
    },
    clearTimeoutFn: (handle) => {
      tasks.delete(handle);
    },
    scheduledCount: () => scheduled,
    runNext: () => {
      const [key, handler] = [...tasks.entries()][0] ?? [];
      if (key === undefined || handler === undefined) throw new Error('no scheduled task');
      tasks.delete(key);
      handler();
    },
  };
}

function progressEvent(completed: number, occurredAt: string | null): ChamberProgressEvent {
  return {
    kind: 'chamber_progress',
    chamber_id: 'chA',
    progress: { is_running: true, completed, total: 10, ratio: completed / 10 },
    session_id: 's1',
    occurred_at: occurredAt,
  };
}

function cachedCompleted(qc: QueryClient): number | undefined {
  return qc.getQueryData<ChamberMeasurementSnapshot>(queryKeys.chambers.progress('chA'))?.progress
    .completed;
}

// ─── S2 — monotonic ordering ───────────────────────────────────────────────

describe('S2 — out-of-order events do not roll the cache backwards', () => {
  it('drops a replayed older event after a newer one has been applied', () => {
    const qc = new QueryClient();
    applyChamberProgressEvent(qc, progressEvent(7, '2026-07-19T00:00:07Z'));
    expect(cachedCompleted(qc)).toBe(7);

    // Post-reconnect relay replay of a frame from earlier in the run.
    applyChamberProgressEvent(qc, progressEvent(2, '2026-07-19T00:00:02Z'));
    expect(cachedCompleted(qc)).toBe(7);
  });

  it('accepts a newer event and advances the watermark', () => {
    const qc = new QueryClient();
    applyChamberProgressEvent(qc, progressEvent(7, '2026-07-19T00:00:07Z'));
    applyChamberProgressEvent(qc, progressEvent(9, '2026-07-19T00:00:09Z'));
    expect(cachedCompleted(qc)).toBe(9);
    expect(qc.getQueryData<string>(queryKeys.chambers.progressWatermark('chA'))).toBe(
      '2026-07-19T00:00:09Z',
    );
  });

  it('accepts a re-delivered event with an identical timestamp (at-least-once relay)', () => {
    const qc = new QueryClient();
    applyChamberProgressEvent(qc, progressEvent(7, '2026-07-19T00:00:07Z'));
    applyChamberProgressEvent(qc, progressEvent(8, '2026-07-19T00:00:07Z'));
    expect(cachedCompleted(qc)).toBe(8);
  });

  it('fails OPEN when the relay stamps no timestamp (no ordering info ⇒ no drops)', () => {
    const qc = new QueryClient();
    applyChamberProgressEvent(qc, progressEvent(7, null));
    applyChamberProgressEvent(qc, progressEvent(2, null));
    expect(cachedCompleted(qc)).toBe(2);
  });

  it('keeps the ordering rule pure and inspectable', () => {
    expect(isChamberEventFresh(undefined, '2026-07-19T00:00:00Z')).toBe(true);
    expect(isChamberEventFresh('2026-07-19T00:00:05Z', '2026-07-19T00:00:04Z')).toBe(false);
    expect(isChamberEventFresh('2026-07-19T00:00:05Z', '2026-07-19T00:00:05Z')).toBe(true);
    expect(isChamberEventFresh('2026-07-19T00:00:05Z', null)).toBe(true);
  });

  it('scopes the watermark per chamber (one chamber cannot gate another)', () => {
    const qc = new QueryClient();
    applyChamberProgressEvent(qc, progressEvent(9, '2026-07-19T00:00:09Z'));
    applyChamberProgressEvent(qc, {
      ...progressEvent(1, '2026-07-19T00:00:01Z'),
      chamber_id: 'chB',
    });
    expect(
      qc.getQueryData<ChamberMeasurementSnapshot>(queryKeys.chambers.progress('chB'))?.progress
        .completed,
    ).toBe(1);
  });
});

// ─── S3 — shape validation ─────────────────────────────────────────────────

describe('S3 — malformed payloads never reach the cache', () => {
  const INCOMPLETE = [
    ['missing total', { is_running: true, completed: 3, ratio: 0.3 }],
    ['missing completed', { is_running: true, total: 10, ratio: 0.3 }],
    ['missing ratio', { is_running: true, completed: 3, total: 10 }],
    ['missing is_running', { completed: 3, total: 10, ratio: 0.3 }],
    ['string counter', { is_running: true, completed: '3', total: 10, ratio: 0.3 }],
    ['null progress', null],
  ] as const;

  it.each(INCOMPLETE)('rejects a snapshot with %s', (_label, progress) => {
    expect(parseChamberProgressSnapshot(progress)).toBeNull();
  });

  it('leaves an existing good snapshot untouched when a bad event arrives', () => {
    const qc = new QueryClient();
    applyChamberProgressEvent(qc, progressEvent(7, '2026-07-19T00:00:07Z'));
    applyChamberProgressEvent(qc, {
      ...progressEvent(0, '2026-07-19T00:00:08Z'),
      // A caller holding a widened/hand-built event — the exported function must
      // still refuse to write a partial object behind a complete-looking type.
      progress: { is_running: true, completed: 8 } as ChamberProgressEvent['progress'],
    });
    expect(cachedCompleted(qc)).toBe(7);
  });

  it('does not advance the watermark on a rejected event', () => {
    const qc = new QueryClient();
    applyChamberProgressEvent(qc, {
      ...progressEvent(0, '2026-07-19T00:00:08Z'),
      progress: { is_running: true } as ChamberProgressEvent['progress'],
    });
    expect(qc.getQueryData<string>(queryKeys.chambers.progressWatermark('chA'))).toBeUndefined();
  });

  it('the wire parser rejects a chamber_progress frame with a partial snapshot', () => {
    const raw = JSON.stringify({
      kind: 'chamber_progress',
      chamber_id: 'chA',
      progress: { is_running: true, completed: 3 },
    });
    expect(parseChamberProgressEvent(raw)).toBeNull();
  });

  it('still parses a well-formed frame (no false rejection)', () => {
    const raw = JSON.stringify({
      kind: 'chamber_progress',
      chamber_id: 'chA',
      progress: { is_running: true, completed: 3, total: 10, ratio: 0.3 },
      occurred_at: '2026-07-19T00:00:03Z',
    });
    expect(parseChamberProgressEvent(raw)?.progress.total).toBe(10);
  });
});

// ─── S4 — close-code policy ────────────────────────────────────────────────

describe('S4 — a policy rejection stops the reconnect loop', () => {
  it.each([1008, 1003])('schedules ZERO reconnects after close code %i', (code) => {
    const scheduler = makeScheduler();
    const socket = new FakeSocket();
    const statuses: [ChamberStreamStatus, ChamberStreamCloseReason | undefined][] = [];

    createChamberProgressStream({
      onEvent: vi.fn(),
      onStatus: (status, reason) => statuses.push([status, reason]),
      socketFactory: () => socket as unknown as WebSocket,
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
    });
    socket.emitOpen();
    socket.emitClose(code);

    expect(scheduler.scheduledCount()).toBe(0);
    expect(statuses.at(-1)).toEqual(['closed', 'policy_rejected']);
  });

  it('still reconnects on an ordinary abnormal close (1006)', () => {
    const scheduler = makeScheduler();
    const socket = new FakeSocket();
    createChamberProgressStream({
      onEvent: vi.fn(),
      socketFactory: () => socket as unknown as WebSocket,
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
    });
    socket.emitOpen();
    socket.emitClose(1006);
    expect(scheduler.scheduledCount()).toBe(1);
  });

  it('reconnects on a code-less close (older browser / test double)', () => {
    const scheduler = makeScheduler();
    const socket = new FakeSocket();
    createChamberProgressStream({
      onEvent: vi.fn(),
      socketFactory: () => socket as unknown as WebSocket,
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
    });
    socket.emitOpen();
    socket.emitClose();
    expect(scheduler.scheduledCount()).toBe(1);
  });

  it('caps retries and reports the exhaustion (permanent polling-only degrade)', () => {
    const scheduler = makeScheduler();
    const sockets: FakeSocket[] = [];
    const statuses: [ChamberStreamStatus, ChamberStreamCloseReason | undefined][] = [];

    createChamberProgressStream({
      onEvent: vi.fn(),
      onStatus: (status, reason) => statuses.push([status, reason]),
      socketFactory: () => {
        const s = new FakeSocket();
        sockets.push(s);
        return s as unknown as WebSocket;
      },
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
      maxReconnectAttempts: 2,
    });

    const latest = (): FakeSocket => {
      const socket = sockets[sockets.length - 1];
      if (socket === undefined) throw new Error('no socket created');
      return socket;
    };
    latest().emitClose(1006); // attempt 1 scheduled
    scheduler.runNext();
    latest().emitClose(1006); // attempt 2 scheduled
    scheduler.runNext();
    latest().emitClose(1006); // budget spent

    expect(scheduler.scheduledCount()).toBe(2);
    expect(statuses.at(-1)).toEqual(['closed', 'retry_exhausted']);
  });

  it('keeps the decision pure and exhaustive', () => {
    expect(chamberReconnectDecision(1008, 0, 8)).toEqual({
      action: 'stop',
      reason: 'policy_rejected',
    });
    expect(chamberReconnectDecision(1006, 8, 8)).toEqual({
      action: 'stop',
      reason: 'retry_exhausted',
    });
    expect(chamberReconnectDecision(1006, 0, 8)).toEqual({ action: 'reconnect' });
    expect(chamberReconnectDecision(undefined, 0, 8)).toEqual({ action: 'reconnect' });
  });

  it('labels an explicit teardown distinctly from a degrade', () => {
    const scheduler = makeScheduler();
    const socket = new FakeSocket();
    const statuses: [ChamberStreamStatus, ChamberStreamCloseReason | undefined][] = [];
    const handle = createChamberProgressStream({
      onEvent: vi.fn(),
      onStatus: (status, reason) => statuses.push([status, reason]),
      socketFactory: () => socket as unknown as WebSocket,
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
    });
    socket.emitOpen();
    handle.close();
    handle.close(); // idempotent
    expect(statuses.filter(([s]) => s === 'closed')).toEqual([['closed', 'disposed']]);
  });
});
