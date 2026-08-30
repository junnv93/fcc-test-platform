import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  chamberEventsUrl,
  createChamberProgressStream,
  parseChamberProgressEvent,
  platformWsBaseUrl,
  type ChamberStreamStatus,
} from '@/api/chamber-events';
import { WS_BEARER_SUBPROTOCOL, bearerSubprotocols } from '@/api/ws-bearer';
import { currentAccessToken } from '@/auth/session';

/**
 * 멀티챔버 P7/B4 (2026-06-18) — chamber progress event stream unit tests.
 *
 * Mirror of `session-events.test.ts`: an injected `socketFactory` + deterministic
 * scheduler so no real WebSocket/network is opened. Covers: URL derivation,
 * envelope parsing (ignores control frames), status lifecycle, exponential
 * backoff reconnection, and idempotent close.
 *
 * W3-4 (2026-08-01) additions: bearer-subprotocol encoding grammar + the
 * stream reading `currentAccessToken()` fresh on every `connect()` call
 * (including reconnects).
 */

vi.mock('@/auth/session', () => ({
  currentAccessToken: vi.fn((): string | null => null),
}));

afterEach(() => {
  vi.mocked(currentAccessToken).mockReset();
  vi.mocked(currentAccessToken).mockReturnValue(null);
});

class FakeSocket {
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onclose: ((event: unknown) => void) | null = null;
  closeCount = 0;

  close(): void {
    this.closeCount += 1;
  }
  emitOpen(): void {
    this.onopen?.({});
  }
  emitMessage(data: string): void {
    this.onmessage?.({ data } as MessageEvent);
  }
  emitClose(): void {
    this.onclose?.({});
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

const progressFrame = (completed: number): string =>
  JSON.stringify({
    kind: 'chamber_progress',
    chamber_id: 'chA',
    progress: { is_running: true, completed, total: 10, ratio: completed / 10 },
    session_id: 's1',
    occurred_at: '2026-06-18T00:00:00+00:00',
  });

describe('bearerSubprotocols', () => {
  it('encodes a blank/null token to an empty array', () => {
    expect(bearerSubprotocols(null)).toEqual([]);
    expect(bearerSubprotocols('')).toEqual([]);
    expect(bearerSubprotocols('   ')).toEqual([]);
  });
  it('encodes a non-blank token as [sentinel, token]', () => {
    expect(bearerSubprotocols('abc.def-123')).toEqual([WS_BEARER_SUBPROTOCOL, 'abc.def-123']);
  });
});

describe('platformWsBaseUrl', () => {
  it('swaps http→ws', () => {
    expect(
      platformWsBaseUrl({ apiBaseUrl: 'http://127.0.0.1:8000', platformApiBaseUrl: null }),
    ).toBe('ws://127.0.0.1:8000');
  });
  it('swaps https→wss and prefers platformApiBaseUrl', () => {
    expect(
      platformWsBaseUrl({
        apiBaseUrl: 'https://app.example.com',
        platformApiBaseUrl: 'https://platform.example.com',
      }),
    ).toBe('wss://platform.example.com');
  });
  it('strips trailing slashes', () => {
    expect(platformWsBaseUrl({ apiBaseUrl: 'http://h:8000/', platformApiBaseUrl: null })).toBe(
      'ws://h:8000',
    );
  });
});

describe('chamberEventsUrl', () => {
  it('builds the canonical /platform/chambers/events URL from runtime config', () => {
    // tests/setup.ts seeds apiBaseUrl = http://127.0.0.1:8000, platformApiBaseUrl = null
    expect(chamberEventsUrl()).toBe('ws://127.0.0.1:8000/platform/chambers/events');
  });
});

describe('parseChamberProgressEvent', () => {
  it('parses a chamber_progress envelope', () => {
    const ev = parseChamberProgressEvent(progressFrame(4));
    expect(ev).not.toBeNull();
    expect(ev?.chamber_id).toBe('chA');
    expect(ev?.progress.completed).toBe(4);
  });
  it('ignores connection_accepted / ping control frames', () => {
    expect(parseChamberProgressEvent(JSON.stringify({ kind: 'connection_accepted' }))).toBeNull();
    expect(parseChamberProgressEvent(JSON.stringify({ kind: 'ping', payload: [] }))).toBeNull();
  });
  it('returns null for malformed / non-string / missing fields', () => {
    expect(parseChamberProgressEvent('garbage{')).toBeNull();
    expect(parseChamberProgressEvent(42)).toBeNull();
    expect(parseChamberProgressEvent(JSON.stringify({ kind: 'chamber_progress' }))).toBeNull();
    expect(
      parseChamberProgressEvent(JSON.stringify({ kind: 'chamber_progress', chamber_id: 'x' })),
    ).toBeNull();
  });
});

describe('createChamberProgressStream', () => {
  it('reports connecting → open and delivers parsed events', () => {
    const socket = new FakeSocket();
    const statuses: ChamberStreamStatus[] = [];
    const events: unknown[] = [];
    const stream = createChamberProgressStream({
      onEvent: (e) => events.push(e),
      onStatus: (s) => statuses.push(s),
      socketFactory: () => socket as unknown as WebSocket,
    });

    expect(statuses).toContain('connecting');
    socket.emitOpen();
    expect(statuses).toContain('open');

    socket.emitMessage(progressFrame(7));
    socket.emitMessage('garbage{'); // ignored — never throws
    expect(events).toHaveLength(1);

    stream.close();
    expect(socket.closeCount).toBe(1);
    expect(statuses.at(-1)).toBe('closed');
  });

  it('reconnects with exponential backoff after close', () => {
    const sockets: FakeSocket[] = [];
    const scheduler = makeScheduler();
    const statuses: ChamberStreamStatus[] = [];
    createChamberProgressStream({
      onEvent: () => undefined,
      onStatus: (s) => statuses.push(s),
      socketFactory: () => {
        const s = new FakeSocket();
        sockets.push(s);
        return s as unknown as WebSocket;
      },
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
      baseReconnectDelayMs: 500,
    });

    const s0 = sockets[0];
    if (s0 === undefined) throw new Error('first socket not created');
    s0.emitOpen();
    s0.emitClose(); // → schedule reconnect
    expect(statuses).toContain('reconnecting');
    const first = [...scheduler.tasks.values()][0];
    if (first === undefined) throw new Error('no reconnect scheduled');
    expect(first.delay).toBe(500); // attempt 0 → base
    scheduler.runNext(); // reconnect → new socket
    expect(sockets.length).toBe(2);
  });

  it('idempotent close cancels a pending reconnect', () => {
    const socket = new FakeSocket();
    const scheduler = makeScheduler();
    const stream = createChamberProgressStream({
      onEvent: () => undefined,
      socketFactory: () => socket as unknown as WebSocket,
      setTimeoutFn: scheduler.setTimeoutFn,
      clearTimeoutFn: scheduler.clearTimeoutFn,
    });
    socket.emitClose(); // schedules a reconnect
    expect(scheduler.tasks.size).toBe(1);
    stream.close();
    expect(scheduler.tasks.size).toBe(0); // cancelled
    stream.close(); // idempotent — no throw
  });

  it('offers no subprotocol when unauthenticated (byte-identical to pre-W3-4)', () => {
    vi.mocked(currentAccessToken).mockReturnValue(null);
    const socket = new FakeSocket();
    const receivedProtocols: (string[] | undefined)[] = [];
    createChamberProgressStream({
      onEvent: () => undefined,
      socketFactory: (_url, protocols) => {
        receivedProtocols.push(protocols);
        return socket as unknown as WebSocket;
      },
    });
    expect(receivedProtocols).toEqual([[]]);
  });

  it('offers the bearer subprotocol pair when a token is present', () => {
    vi.mocked(currentAccessToken).mockReturnValue('tok-123');
    const socket = new FakeSocket();
    const receivedProtocols: (string[] | undefined)[] = [];
    createChamberProgressStream({
      onEvent: () => undefined,
      socketFactory: (_url, protocols) => {
        receivedProtocols.push(protocols);
        return socket as unknown as WebSocket;
      },
    });
    expect(receivedProtocols).toEqual([[WS_BEARER_SUBPROTOCOL, 'tok-123']]);
  });

  it('re-reads the token on every reconnect (not captured once at creation)', () => {
    const sockets: FakeSocket[] = [];
    const scheduler = makeScheduler();
    const receivedProtocols: (string[] | undefined)[] = [];
    vi.mocked(currentAccessToken).mockReturnValue('first-token');
    createChamberProgressStream({
      onEvent: () => undefined,
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
    const first = sockets[0];
    if (first === undefined) throw new Error('first socket not created');
    first.emitClose();
    scheduler.runNext();
    expect(receivedProtocols).toEqual([
      [WS_BEARER_SUBPROTOCOL, 'first-token'],
      [WS_BEARER_SUBPROTOCOL, 'refreshed-token'],
    ]);
  });
});
