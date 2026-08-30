import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  createSessionEventStream,
  parseSessionEvent,
  sessionEventsUrl,
  type SessionStreamCloseReason,
  type SessionStreamStatus,
} from '@/api/session-events';
import { WS_BEARER_SUBPROTOCOL } from '@/api/ws-bearer';
import { currentAccessToken } from '@/auth/session';

/**
 * FE-P5 (2026-05-26) — session event stream unit tests.
 *
 * `createSessionEventStream` is exercised with an injected `socketFactory` +
 * deterministic scheduler so no real WebSocket/network is opened (jsdom has no
 * usable WS server). Covers: status lifecycle, event delivery, exponential
 * backoff reconnection, and idempotent close that cancels pending reconnects.
 *
 * W3-4 (2026-08-01) additions: the stream reading `currentAccessToken()`
 * fresh on every `connect()` call (including reconnects) — mirror of the
 * equivalent `chamber-events.test.ts` coverage.
 *
 * W3-4 M8 (2026-08-01) additions: close-code reconnect policy (mirror of
 * `tests/api/chamber-stream-robustness.test.ts` S4) — before this wave
 * `onclose` ignored the close code entirely and reconnected forever, so a
 * server policy rejection (1008/1003 — now reachable for the first time
 * since a browser can offer a bearer credential at all) became an infinite
 * failing retry loop.
 */

vi.mock('@/auth/session', () => ({
  currentAccessToken: vi.fn((): string | null => null),
}));

class FakeSocket {
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onclose: ((event: unknown) => void) | null = null;
  closeCount = 0;

  close(): void {
    this.closeCount += 1;
  }

  // -- test drivers --
  emitOpen(): void {
    this.onopen?.({});
  }
  emitMessage(data: string): void {
    this.onmessage?.({ data } as MessageEvent);
  }
  /** Close WITH an optional code — the information the pre-M8 `onclose` threw away. */
  emitClose(code?: number): void {
    this.onclose?.(code === undefined ? {} : { code });
  }
}

interface ScheduledTask {
  readonly handler: () => void;
  readonly delay: number;
}

function makeScheduler(): {
  setTimeoutFn: (handler: () => void, delay: number) => number;
  clearTimeoutFn: (handle: number) => void;
  tasks: Map<number, ScheduledTask>;
  runNext: () => void;
} {
  const tasks = new Map<number, ScheduledTask>();
  let id = 0;
  return {
    tasks,
    setTimeoutFn: (handler, delay) => {
      id += 1;
      tasks.set(id, { handler, delay });
      return id;
    },
    clearTimeoutFn: (handle) => {
      tasks.delete(handle);
    },
    runNext: () => {
      const [key, task] = [...tasks.entries()][0] ?? [];
      if (key === undefined || task === undefined) throw new Error('no scheduled task');
      tasks.delete(key);
      task.handler();
    },
  };
}

const noop = (): void => undefined;

function expectDefined<T>(value: T | undefined, message: string): T {
  if (value === undefined) throw new Error(message);
  return value;
}

function firstTaskDelay(tasks: Map<number, ScheduledTask>): number {
  return expectDefined([...tasks.values()][0], 'expected a scheduled task').delay;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.mocked(currentAccessToken).mockReset();
  vi.mocked(currentAccessToken).mockReturnValue(null);
});

describe('parseSessionEvent', () => {
  it('parses a well-formed {kind, payload} envelope', () => {
    expect(parseSessionEvent(JSON.stringify({ kind: 'progress', payload: [1, 2] }))).toEqual({
      kind: 'progress',
      payload: [1, 2],
    });
  });

  it('defaults payload to [] when absent or non-array', () => {
    expect(parseSessionEvent(JSON.stringify({ kind: 'log' }))).toEqual({
      kind: 'log',
      payload: [],
    });
    expect(parseSessionEvent(JSON.stringify({ kind: 'log', payload: 'x' }))).toEqual({
      kind: 'log',
      payload: [],
    });
  });

  it('returns null for non-string, invalid JSON, non-object, or missing kind', () => {
    expect(parseSessionEvent(42)).toBeNull();
    expect(parseSessionEvent('not json{')).toBeNull();
    expect(parseSessionEvent(JSON.stringify([1, 2]))).toBeNull();
    expect(parseSessionEvent(JSON.stringify({ payload: [] }))).toBeNull();
    expect(parseSessionEvent('null')).toBeNull();
  });
});

describe('sessionEventsUrl', () => {
  it('builds the canonical /session/events URL from runtime config wsBaseUrl', () => {
    // tests/setup.ts seeds wsBaseUrl = ws://127.0.0.1:8000
    expect(sessionEventsUrl()).toBe('ws://127.0.0.1:8000/session/events');
  });
});

describe('createSessionEventStream', () => {
  it('reports connecting → open and delivers parsed events', () => {
    const socket = new FakeSocket();
    const statuses: SessionStreamStatus[] = [];
    const events: unknown[] = [];
    const stream = createSessionEventStream({
      onEvent: (e) => events.push(e),
      onStatus: (s) => statuses.push(s),
      socketFactory: () => socket as unknown as WebSocket,
    });

    expect(statuses).toContain('connecting');
    socket.emitOpen();
    expect(statuses).toContain('open');

    socket.emitMessage(JSON.stringify({ kind: 'progress', payload: [3] }));
    socket.emitMessage('garbage{'); // ignored — never throws
    expect(events).toEqual([{ kind: 'progress', payload: [3] }]);

    stream.close();
  });

  it('reconnects with exponential backoff after an unexpected close', () => {
    let created = 0;
    const sockets: FakeSocket[] = [];
    const scheduler = makeScheduler();
    const stream = createSessionEventStream({
      onEvent: noop,
      socketFactory: () => {
        created += 1;
        const s = new FakeSocket();
        sockets.push(s);
        return s as unknown as WebSocket;
      },
      baseReconnectDelayMs: 500,
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
    });

    expect(created).toBe(1);
    const first = expectDefined(sockets[0], 'first socket');
    first.emitOpen();
    first.emitClose();
    // first reconnect scheduled at base delay
    expect(firstTaskDelay(scheduler.tasks)).toBe(500);
    scheduler.runNext();
    expect(created).toBe(2);

    // second close → delay doubles (500 * 2^1)
    expectDefined(sockets[1], 'second socket').emitClose();
    expect(firstTaskDelay(scheduler.tasks)).toBe(1000);

    stream.close();
  });

  it('emits "reconnecting" exactly once per reconnect cycle (no double-emit)', () => {
    const sockets: FakeSocket[] = [];
    const scheduler = makeScheduler();
    const statuses: SessionStreamStatus[] = [];
    const stream = createSessionEventStream({
      onEvent: noop,
      onStatus: (s) => statuses.push(s),
      socketFactory: () => {
        const s = new FakeSocket();
        sockets.push(s);
        return s as unknown as WebSocket;
      },
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
    });
    expectDefined(sockets[0], 'first socket').emitOpen();
    expectDefined(sockets[0], 'first socket').emitClose(); // schedules reconnect
    scheduler.runNext(); // connect attempt 1 — must NOT re-emit 'reconnecting'
    expect(statuses.filter((s) => s === 'reconnecting')).toHaveLength(1);
    expect(statuses).toEqual(['connecting', 'open', 'reconnecting']);
    stream.close();
  });

  it('close() cancels a pending reconnect and is idempotent', () => {
    const socket = new FakeSocket();
    const scheduler = makeScheduler();
    const statuses: SessionStreamStatus[] = [];
    const stream = createSessionEventStream({
      onEvent: noop,
      onStatus: (s) => statuses.push(s),
      socketFactory: () => socket as unknown as WebSocket,
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
    });

    socket.emitOpen();
    socket.emitClose(); // schedules a reconnect
    expect(scheduler.tasks.size).toBe(1);

    stream.close();
    expect(scheduler.tasks.size).toBe(0); // pending reconnect cancelled
    expect(statuses.at(-1)).toBe('closed');

    const before = statuses.length;
    stream.close(); // idempotent — no further status emissions
    expect(statuses.length).toBe(before);
  });

  it('does not reconnect after dispose even if a late close fires', () => {
    const sockets: FakeSocket[] = [];
    const scheduler = makeScheduler();
    const stream = createSessionEventStream({
      onEvent: noop,
      socketFactory: () => {
        const s = new FakeSocket();
        sockets.push(s);
        return s as unknown as WebSocket;
      },
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
    });
    expect(sockets.length).toBe(1);

    stream.close();
    // close() detaches the socket handlers, so a stray close on the (now
    // orphaned) socket cannot schedule a reconnect or open a new socket.
    expectDefined(sockets[0], 'first socket').emitClose();
    expect(scheduler.tasks.size).toBe(0);
    expect(sockets.length).toBe(1);
  });

  it('offers no subprotocol when unauthenticated (byte-identical to pre-W3-4)', () => {
    vi.mocked(currentAccessToken).mockReturnValue(null);
    const socket = new FakeSocket();
    const receivedProtocols: (string[] | undefined)[] = [];
    createSessionEventStream({
      onEvent: noop,
      socketFactory: (_url, protocols) => {
        receivedProtocols.push(protocols);
        return socket as unknown as WebSocket;
      },
    });
    expect(receivedProtocols).toEqual([[]]);
  });

  it('re-reads the token on every reconnect (not captured once at creation)', () => {
    const sockets: FakeSocket[] = [];
    const scheduler = makeScheduler();
    const receivedProtocols: (string[] | undefined)[] = [];
    vi.mocked(currentAccessToken).mockReturnValue('first-token');
    createSessionEventStream({
      onEvent: noop,
      socketFactory: (_url, protocols) => {
        receivedProtocols.push(protocols);
        const s = new FakeSocket();
        sockets.push(s);
        return s as unknown as WebSocket;
      },
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
    });
    expect(receivedProtocols).toEqual([[WS_BEARER_SUBPROTOCOL, 'first-token']]);

    // Token refreshed while disconnected — the reconnect must pick it up.
    vi.mocked(currentAccessToken).mockReturnValue('refreshed-token');
    expectDefined(sockets[0], 'first socket').emitClose();
    scheduler.runNext();
    expect(receivedProtocols).toEqual([
      [WS_BEARER_SUBPROTOCOL, 'first-token'],
      [WS_BEARER_SUBPROTOCOL, 'refreshed-token'],
    ]);
  });
});

describe('M8 — close-code reconnect policy (mirror of chamber-events S4)', () => {
  it.each([1008, 1003])('schedules ZERO reconnects after close code %i', (code) => {
    const scheduler = makeScheduler();
    const socket = new FakeSocket();
    const statuses: [SessionStreamStatus, SessionStreamCloseReason | undefined][] = [];

    createSessionEventStream({
      onEvent: noop,
      onStatus: (status, reason) => statuses.push([status, reason]),
      socketFactory: () => socket as unknown as WebSocket,
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
    });
    socket.emitOpen();
    socket.emitClose(code);

    expect(scheduler.tasks.size).toBe(0);
    expect(statuses.at(-1)).toEqual(['closed', 'policy_rejected']);
  });

  it('still reconnects on an ordinary abnormal close (1006)', () => {
    const scheduler = makeScheduler();
    const socket = new FakeSocket();
    createSessionEventStream({
      onEvent: noop,
      socketFactory: () => socket as unknown as WebSocket,
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
    });
    socket.emitOpen();
    socket.emitClose(1006);
    expect(scheduler.tasks.size).toBe(1);
  });

  it('reconnects on a code-less close (older browser / test double — byte-identical to pre-M8)', () => {
    const scheduler = makeScheduler();
    const socket = new FakeSocket();
    createSessionEventStream({
      onEvent: noop,
      socketFactory: () => socket as unknown as WebSocket,
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
    });
    socket.emitOpen();
    socket.emitClose();
    expect(scheduler.tasks.size).toBe(1);
  });

  it('caps retries and reports the exhaustion (permanent degrade)', () => {
    const scheduler = makeScheduler();
    const sockets: FakeSocket[] = [];
    const statuses: [SessionStreamStatus, SessionStreamCloseReason | undefined][] = [];

    createSessionEventStream({
      onEvent: noop,
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

    const latest = (): FakeSocket => expectDefined(sockets.at(-1), 'no socket created');
    latest().emitClose(1006); // attempt 1 scheduled
    scheduler.runNext();
    latest().emitClose(1006); // attempt 2 scheduled
    scheduler.runNext();
    latest().emitClose(1006); // budget spent

    expect(statuses.at(-1)).toEqual(['closed', 'retry_exhausted']);
  });

  it('defaults the reconnect budget to DEFAULT_MAX_WS_RECONNECT_ATTEMPTS (8) when unset', () => {
    const scheduler = makeScheduler();
    const sockets: FakeSocket[] = [];
    const statuses: [SessionStreamStatus, SessionStreamCloseReason | undefined][] = [];

    createSessionEventStream({
      onEvent: noop,
      onStatus: (status, reason) => statuses.push([status, reason]),
      socketFactory: () => {
        const s = new FakeSocket();
        sockets.push(s);
        return s as unknown as WebSocket;
      },
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
    });

    const latest = (): FakeSocket => expectDefined(sockets.at(-1), 'no socket created');
    for (let i = 0; i < 8; i += 1) {
      latest().emitClose(1006);
      scheduler.runNext();
    }
    // 8 attempts spent — the 9th close must exhaust the (default) budget.
    latest().emitClose(1006);
    expect(statuses.at(-1)).toEqual(['closed', 'retry_exhausted']);
  });

  it('a constructor throw is a transport failure, not a policy verdict — still retryable', () => {
    const scheduler = makeScheduler();
    let throwOnce = true;
    createSessionEventStream({
      onEvent: noop,
      socketFactory: () => {
        if (throwOnce) {
          throwOnce = false;
          throw new Error('transport unavailable');
        }
        return new FakeSocket() as unknown as WebSocket;
      },
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
    });
    expect(scheduler.tasks.size).toBe(1);
  });
});
