import { currentAccessToken } from '@/auth/session';
import { getRuntimeConfig } from '@/config/runtime';

import {
  bearerSubprotocols,
  wsReconnectDecision,
  DEFAULT_MAX_WS_RECONNECT_ATTEMPTS,
  type WsStreamCloseReason,
} from './ws-bearer';

/**
 * Session event stream — WebSocket `/session/events` (FE-P5, 2026-05-26).
 *
 * The Session API exposes real-time progress/log/notification events over a
 * WebSocket (AsyncAPI `subscribe_session_events`). Browser `WebSocket` cannot
 * set a custom `Authorization` header on the upgrade request, so the bearer
 * credential rides the `Sec-WebSocket-Protocol` offer instead (W3-4,
 * 2026-08-01) — see `ws-bearer.ts` for the encoding SSOT (mirrors backend
 * `application/common/ws_subprotocol_auth.py`); auth-disabled/trusted-network
 * deployments simply offer no subprotocol. The token is re-read from
 * `currentAccessToken()` on every `connect()` call (including reconnects) so
 * a refreshed token is used automatically. The frontend connects to
 * `runtime-config.wsBaseUrl` and renders `{kind, payload}` events;
 * client-side RBAC (`RequirePermission`) gates whether the subscription UI is
 * shown.
 *
 * This module is framework-agnostic and fully unit-testable: the socket is
 * created through an injectable `socketFactory` and reconnection timing uses an
 * injectable scheduler, so tests never touch a real network.
 *
 * Reconnect policy (W3-4 M8, 2026-08-01): mirrors `chamber-events.ts` —
 * both streams consume the SAME `wsReconnectDecision` (ws-bearer.ts) so a
 * server policy rejection (1008/1003) stops reconnecting immediately instead
 * of retrying forever, and a spent reconnect budget permanently degrades.
 * Before this wave a browser WS had no way to offer a bearer credential at
 * all, so an `oidc_jwt`-mode deployment always resolved to an anonymous
 * principal and this stream never actually reached a policy rejection in
 * practice — this wave makes that path reachable for the first time, so
 * leaving it unbounded (as it was) would turn "token expired" into an
 * infinite reconnect loop against a server that will always say no.
 */

/** Wire-shape of a single session event (mirror of backend `SessionEventView`). */
export interface SessionEvent {
  readonly kind: string;
  readonly payload: readonly unknown[];
}

/** Connection lifecycle the UI renders (drives the failure taxonomy). */
export type SessionStreamStatus = 'connecting' | 'open' | 'reconnecting' | 'closed';

/** Why a `closed` status was emitted — alias of `WsStreamCloseReason` (ws-bearer.ts). */
export type SessionStreamCloseReason = WsStreamCloseReason;

export interface SessionEventStreamOptions {
  readonly onEvent: (event: SessionEvent) => void;
  readonly onStatus?: (status: SessionStreamStatus, reason?: SessionStreamCloseReason) => void;
  /** Cap for the exponential reconnect backoff. Default 10s. */
  readonly maxReconnectDelayMs?: number;
  /** Base reconnect delay (doubled each attempt, capped). Default 500ms. */
  readonly baseReconnectDelayMs?: number;
  /** Cap on reconnect attempts before permanent degradation.
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

export interface SessionEventStreamHandle {
  /** Permanently stop the stream + cancel any pending reconnect. Idempotent. */
  readonly close: () => void;
}

const DEFAULT_BASE_DELAY_MS = 500;
const DEFAULT_MAX_DELAY_MS = 10_000;

/** Build the canonical `/session/events` WebSocket URL from runtime config. */
export function sessionEventsUrl(): string {
  const base = getRuntimeConfig().wsBaseUrl.replace(/\/+$/u, '');
  return `${base}/session/events`;
}

/** Parse a raw WS message into a `SessionEvent`, or `null` if it is not a
 *  recognizable `{kind, payload}` envelope (defensive — never throws). */
export function parseSessionEvent(raw: unknown): SessionEvent | null {
  if (typeof raw !== 'string') return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== 'object' || parsed === null) return null;
  const record = parsed as Record<string, unknown>;
  if (typeof record.kind !== 'string') return null;
  const payload = Array.isArray(record.payload) ? (record.payload as readonly unknown[]) : [];
  return { kind: record.kind, payload };
}

/**
 * Open a resilient session-event stream with exponential-backoff reconnection
 * and graceful degradation (the agent-side Session API may be offline). Returns
 * a handle whose `close()` is idempotent and cancels pending reconnects.
 */
export function createSessionEventStream(
  options: SessionEventStreamOptions,
): SessionEventStreamHandle {
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

  const emitStatus = (status: SessionStreamStatus, reason?: SessionStreamCloseReason): void => {
    if (!disposed) options.onStatus?.(status, reason);
  };

  /**
   * Permanently stop reconnecting (M8, mirrors chamber-events.ts `degrade`).
   * `disposed` is set LAST so the terminal status still reaches the caller
   * through `emitStatus`.
   */
  const degrade = (reason: SessionStreamCloseReason): void => {
    emitStatus('closed', reason);
    disposed = true;
  };

  const connect = (): void => {
    if (disposed) return;
    // First attempt → 'connecting'. Reconnect attempts are already announced
    // by scheduleReconnect() (single source) — re-emitting here would double
    // the 'reconnecting' status per cycle.
    if (attempt === 0) emitStatus('connecting');
    let nextSocket: WebSocket;
    try {
      // Read the token fresh on every connect() call (including reconnects)
      // — capturing it once at stream-creation time would keep reconnecting
      // with a token that has since expired (W3-4).
      nextSocket = makeSocket(sessionEventsUrl(), bearerSubprotocols(currentAccessToken()));
    } catch {
      // A constructor throw is a transport failure, not a server verdict —
      // no close code exists, so it takes the retryable branch (mirrors
      // chamber-events.ts).
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
      const parsed = parseSessionEvent(event.data);
      if (parsed !== null) options.onEvent(parsed);
    };
    nextSocket.onerror = (): void => {
      // Defer to onclose for the reconnect decision (browsers always fire
      // close after error). Avoids double-scheduling a reconnect.
    };
    nextSocket.onclose = (event?: CloseEvent): void => {
      if (disposed) return;
      socket = null;
      handleDisconnect(event?.code);
    };
  };

  /**
   * Single decision point for every disconnect (M8): a policy rejection or
   * an exhausted retry budget degrades permanently; anything else backs off
   * and retries. Previously `onclose` unconditionally called
   * `scheduleReconnect`, ignoring the close code entirely.
   */
  const handleDisconnect = (closeCode: number | undefined): void => {
    const decision = wsReconnectDecision(closeCode, attempt, maxAttempts);
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
      // Emit the terminal status before flipping `disposed` so the guard in
      // `emitStatus` (which suppresses post-dispose async callbacks) does not
      // swallow the caller-initiated close.
      emitStatus('closed', 'disposed');
      disposed = true;
    },
  };
}
