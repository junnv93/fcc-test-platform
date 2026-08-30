import { currentAccessToken } from '@/auth/session';
import { getRuntimeConfig } from '@/config/runtime';

import { resolvePlatformBaseUrl } from './platform-client';
import {
  bearerSubprotocols,
  wsReconnectDecision,
  DEFAULT_MAX_WS_RECONNECT_ATTEMPTS,
  type WsReconnectDecision,
  type WsStreamCloseReason,
} from './ws-bearer';

import type { ChamberMeasurementSnapshot } from './platform-client';

/**
 * Chamber progress event stream — WebSocket `/platform/chambers/events`
 * (멀티챔버 P7/B4, 2026-06-18, ADR-0015 Option B).
 *
 * The central Platform API fans out node heartbeat-carried progress (C1) in
 * real time over a WebSocket — a low-latency complement to the
 * `GET /platform/chambers` polling read (preserved as the fallback). Browser
 * `WebSocket` cannot set a custom `Authorization` header on the upgrade
 * request, so the bearer credential rides the `Sec-WebSocket-Protocol` offer
 * instead (W3-4, 2026-08-01) — see `ws-bearer.ts` for the encoding SSOT
 * (mirrors backend `application/common/ws_subprotocol_auth.py`). The token is
 * re-read from `currentAccessToken()` on every `connect()` call (including
 * reconnects) so a refreshed token is used automatically instead of the one
 * captured when the stream was first opened.
 *
 * Structurally mirrors `session-events.ts` (reconnect backoff + injectable
 * `socketFactory`/scheduler) so the two real-time clients cannot drift; the
 * payload is the chamber-specific `{kind, chamber_id, progress, ...}` envelope
 * (the backend `ChamberProgressEvent.as_wire()` SSOT) instead of the session
 * `{kind, payload}` shape.
 */

/** Progress snapshot reused from the generated platform schema (C1). */
export type ChamberProgressSnapshot = ChamberMeasurementSnapshot['progress'];

/** Wire-shape of a single chamber progress event (mirror of backend `ChamberProgressEvent`). */
export interface ChamberProgressEvent {
  readonly kind: 'chamber_progress';
  readonly chamber_id: string;
  readonly progress: ChamberProgressSnapshot;
  readonly session_id?: string | null;
  readonly occurred_at?: string | null;
}

/** Connection lifecycle the UI renders (drives the polling-fallback decision). */
export type ChamberStreamStatus = 'connecting' | 'open' | 'reconnecting' | 'closed';

/**
 * Why a `closed` status was emitted (fe-data-layer-robustness M3, 2026-07-19).
 *
 * The union above is deliberately NOT widened — every consumer (`chambers.tsx`
 * and friends, out of this wave's write scope) branches on those four tokens,
 * and adding a fifth would break their exhaustive switches. The reason rides
 * alongside as an optional second `onStatus` argument, so today's consumers
 * compile unchanged while the information needed to render "live vs polling
 * only" (W2) is already produced.
 *
 * Alias of the technology-neutral `WsStreamCloseReason` (ws-bearer.ts, W3-4
 * M8) — kept under this chamber-scoped name so existing importers compile
 * unchanged.
 */
export type ChamberStreamCloseReason = WsStreamCloseReason;

export interface ChamberEventStreamOptions {
  readonly onEvent: (event: ChamberProgressEvent) => void;
  readonly onStatus?: (status: ChamberStreamStatus, reason?: ChamberStreamCloseReason) => void;
  /** Cap for the exponential reconnect backoff. Default 10s. */
  readonly maxReconnectDelayMs?: number;
  /** Base reconnect delay (doubled each attempt, capped). Default 500ms. */
  readonly baseReconnectDelayMs?: number;
  /** Cap on reconnect attempts before permanent degradation to polling.
   *  Default {@link DEFAULT_MAX_WS_RECONNECT_ATTEMPTS}. */
  readonly maxReconnectAttempts?: number;
  /** Injected for tests so no real socket/network is opened. The optional
   *  second argument carries the bearer-credential subprotocol offer (W3-4)
   *  — existing single-argument test doubles keep compiling and simply
   *  ignore it (JS callables never require callers to match arity). */
  readonly socketFactory?: (url: string, protocols?: string[]) => WebSocket;
  /** Injected scheduler for deterministic backoff tests. */
  readonly setTimeoutFn?: (handler: () => void, timeoutMs: number) => number;
  readonly clearTimeoutFn?: (handle: number) => void;
}

export interface ChamberEventStreamHandle {
  /** Permanently stop the stream + cancel any pending reconnect. Idempotent. */
  readonly close: () => void;
}

const DEFAULT_BASE_DELAY_MS = 500;
const DEFAULT_MAX_DELAY_MS = 10_000;

/**
 * Reconnect decision for one close event — pure, so both branches are
 * sealable. Alias of the technology-neutral `WsReconnectDecision`
 * (ws-bearer.ts, W3-4 M8).
 */
export type ChamberReconnectDecision = WsReconnectDecision;

/**
 * Decide whether a closed socket should be retried — thin alias of the
 * shared `wsReconnectDecision` (ws-bearer.ts, W3-4 M8) kept under this
 * chamber-scoped name so existing importers compile unchanged. The old code
 * reconnected on ANY close with unbounded exponential backoff, so an
 * unauthorized operator's browser hammered the relay forever while the UI
 * showed a perpetual `reconnecting`; the shared policy fixed that once for
 * both real-time streams.
 */
export const chamberReconnectDecision = wsReconnectDecision;

/**
 * Derive the platform WebSocket base from the platform HTTP base by swapping the
 * scheme (`https`→`wss`, `http`→`ws`). The central progress relay is served by
 * the same Platform API origin as the HTTP read surface (same-origin gateway in
 * the containerized deployment), so no separate runtime-config field is needed —
 * we reuse `resolvePlatformBaseUrl` (platformApiBaseUrl ?? apiBaseUrl). Pure +
 * exported so the derivation is unit-tested without module-load timing.
 */
export function platformWsBaseUrl(config: Parameters<typeof resolvePlatformBaseUrl>[0]): string {
  const httpBase = resolvePlatformBaseUrl(config).replace(/\/+$/u, '');
  if (httpBase.startsWith('https://')) return `wss://${httpBase.slice('https://'.length)}`;
  if (httpBase.startsWith('http://')) return `ws://${httpBase.slice('http://'.length)}`;
  return httpBase; // already a ws(s):// or scheme-relative base — pass through.
}

/** Build the canonical `/platform/chambers/events` WebSocket URL from runtime config. */
export function chamberEventsUrl(): string {
  return `${platformWsBaseUrl(getRuntimeConfig())}/platform/chambers/events`;
}

/**
 * Runtime-validate a progress snapshot (fe-data-layer-robustness M2, 2026-07-19).
 *
 * The previous parser accepted *any* non-null object as the `progress` member
 * and reached the cache through `progress as ChamberProgressSnapshot` — a lie to
 * the compiler about untrusted network data. A relay/schema drift that dropped
 * `total` therefore wrote `{completed: 3}` into the same cache key the polling
 * read populates, and the progress bar rendered `NaN%` from a source the type
 * system swore was complete. Every field of the generated
 * `ChamberSessionProgress` is required, so all four are checked here and the
 * return type is derived structurally — **no unchecked cast remains**.
 *
 * `null` means "not a usable snapshot"; callers drop the event rather than
 * writing a partial object over good data.
 */
export function parseChamberProgressSnapshot(raw: unknown): ChamberProgressSnapshot | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const record = raw as Record<string, unknown>;
  const { completed, total, ratio, is_running: isRunning } = record;
  if (typeof isRunning !== 'boolean') return null;
  if (!Number.isFinite(completed) || !Number.isFinite(total) || !Number.isFinite(ratio)) {
    return null;
  }
  return {
    completed: completed as number,
    total: total as number,
    ratio: ratio as number,
    is_running: isRunning,
  };
}

/** Parse a raw WS message into a `ChamberProgressEvent`, or `null` if it is not a
 *  recognizable `chamber_progress` envelope (defensive — never throws). The
 *  server also sends `connection_accepted`/`ping` control frames which parse to
 *  `null` (ignored by consumers). M2: a `chamber_progress` frame whose snapshot
 *  fails {@link parseChamberProgressSnapshot} is likewise `null` — a malformed
 *  payload never reaches the cache. */
export function parseChamberProgressEvent(raw: unknown): ChamberProgressEvent | null {
  if (typeof raw !== 'string') return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== 'object' || parsed === null) return null;
  const record = parsed as Record<string, unknown>;
  if (record.kind !== 'chamber_progress') return null;
  if (typeof record.chamber_id !== 'string') return null;
  const progress = parseChamberProgressSnapshot(record.progress);
  if (progress === null) return null;
  return {
    kind: 'chamber_progress',
    chamber_id: record.chamber_id,
    progress,
    session_id: typeof record.session_id === 'string' ? record.session_id : null,
    occurred_at: typeof record.occurred_at === 'string' ? record.occurred_at : null,
  };
}

/**
 * Open a resilient chamber-progress stream with exponential-backoff reconnection
 * and graceful degradation (the central relay may be offline — the polling
 * fallback covers data). Returns a handle whose `close()` is idempotent and
 * cancels pending reconnects.
 */
export function createChamberProgressStream(
  options: ChamberEventStreamOptions,
): ChamberEventStreamHandle {
  const baseDelay = options.baseReconnectDelayMs ?? DEFAULT_BASE_DELAY_MS;
  const maxDelay = options.maxReconnectDelayMs ?? DEFAULT_MAX_DELAY_MS;
  const maxAttempts = options.maxReconnectAttempts ?? DEFAULT_MAX_WS_RECONNECT_ATTEMPTS;
  const makeSocket =
    options.socketFactory ??
    ((url: string, protocols?: string[]) =>
      protocols !== undefined && protocols.length > 0
        ? new WebSocket(url, protocols)
        : new WebSocket(url));
  const schedule =
    options.setTimeoutFn ?? ((h, t) => globalThis.setTimeout(h, t) as unknown as number);
  const cancel = options.clearTimeoutFn ?? ((h) => globalThis.clearTimeout(h));

  let socket: WebSocket | null = null;
  let reconnectHandle: number | null = null;
  let attempt = 0;
  let disposed = false;

  const emitStatus = (status: ChamberStreamStatus, reason?: ChamberStreamCloseReason): void => {
    if (!disposed) options.onStatus?.(status, reason);
  };

  /**
   * Permanently stop reconnecting and degrade to the polling fallback (M3). The
   * handle stays valid and `close()` remains idempotent; only the retry loop
   * ends. `disposed` is set LAST so the terminal status still reaches the
   * caller through `emitStatus`.
   */
  const degrade = (reason: ChamberStreamCloseReason): void => {
    emitStatus('closed', reason);
    disposed = true;
  };

  const connect = (): void => {
    if (disposed) return;
    if (attempt === 0) emitStatus('connecting');
    let nextSocket: WebSocket;
    try {
      // Read the token fresh on every connect() call (including reconnects)
      // — capturing it once at stream-creation time would keep reconnecting
      // with a token that has since expired (W3-4).
      // A constructor throw is a transport failure, not a server verdict — no
      // close code exists, so it takes the retryable branch.
      nextSocket = makeSocket(chamberEventsUrl(), bearerSubprotocols(currentAccessToken()));
    } catch {
      handleDisconnect(undefined);
      return;
    }
    socket = nextSocket;

    nextSocket.onopen = (): void => {
      if (disposed) return;
      attempt = 0;
      emitStatus('open');
    };
    nextSocket.onmessage = (event: MessageEvent): void => {
      if (disposed) return;
      const parsed = parseChamberProgressEvent(event.data);
      if (parsed !== null) options.onEvent(parsed);
    };
    nextSocket.onerror = (): void => {
      // Defer to onclose for the reconnect decision (browsers always fire close
      // after error). Avoids double-scheduling a reconnect.
    };
    nextSocket.onclose = (event?: CloseEvent): void => {
      if (disposed) return;
      socket = null;
      handleDisconnect(event?.code);
    };
  };

  /**
   * Single decision point for every disconnect (M3): a policy rejection or an
   * exhausted retry budget degrades permanently; anything else backs off and
   * retries. Previously `onclose` unconditionally called `scheduleReconnect`,
   * ignoring the close code entirely.
   */
  const handleDisconnect = (closeCode: number | undefined): void => {
    const decision = chamberReconnectDecision(closeCode, attempt, maxAttempts);
    if (decision.action === 'stop') {
      degrade(decision.reason);
      return;
    }
    scheduleReconnect();
  };

  const scheduleReconnect = (): void => {
    if (disposed || reconnectHandle !== null) return;
    const delay = Math.min(baseDelay * 2 ** attempt, maxDelay);
    attempt += 1;
    emitStatus('reconnecting');
    reconnectHandle = schedule(() => {
      reconnectHandle = null;
      connect();
    }, delay);
  };

  connect();

  return {
    close(): void {
      if (disposed) return;
      if (reconnectHandle !== null) {
        cancel(reconnectHandle);
        reconnectHandle = null;
      }
      if (socket !== null) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onclose = null;
        try {
          socket.close();
        } catch {
          /* already closing — ignore */
        }
        socket = null;
      }
      emitStatus('closed', 'disposed');
      disposed = true;
    },
  };
}
