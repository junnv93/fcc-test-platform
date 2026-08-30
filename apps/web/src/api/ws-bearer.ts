/**
 * WebSocket bearer-credential subprotocol grammar — frontend mirror of the
 * backend SSOT `application/common/ws_subprotocol_auth.py` (W3-4, 2026-08-01).
 *
 * Browser `WebSocket` cannot set a custom `Authorization` header on the
 * upgrade request (the Fetch/WHATWG spec limits the constructor to a URL and
 * an optional subprotocol list). RFC 6455 §4.1 lets the client offer one or
 * more `Sec-WebSocket-Protocol` values, each of which must satisfy the
 * `token` grammar of RFC 7230 §3.2.6 — a JWT's charset (base64url
 * `A-Za-z0-9-_` plus `.` segment separators) is a strict subset of that
 * grammar, so the token rides a subprotocol offer unmodified.
 *
 * Cross-language parity: this constant and the encoding rule below must stay
 * byte-identical to the Python SSOT — asserted directly by a pytest that
 * reads both files (see `.claude/contracts/ws-bearer-subprotocol-authz.md`).
 */

/** Sentinel subprotocol value — MUST match `ws_subprotocol_auth.WS_BEARER_SUBPROTOCOL`. */
export const WS_BEARER_SUBPROTOCOL = 'fcc.bearer.v1';

/**
 * Client-side encoding — mirrors `encode_bearer_subprotocols`. A `null` or
 * blank token encodes to an empty array (no subprotocol offer at all — the
 * auth-disabled deployment and today's non-token test doubles stay
 * byte-identical). A non-blank token encodes as the sentinel first,
 * immediately followed by the raw token.
 */
export function bearerSubprotocols(token: string | null): string[] {
  const normalized = (token ?? '').trim();
  if (!normalized) return [];
  return [WS_BEARER_SUBPROTOCOL, normalized];
}

/**
 * Reconnect policy — technology-neutral SSOT (W3-4 M8, 2026-08-01).
 *
 * Promoted from `chamber-events.ts`'s (chamber-only) `chamberReconnectDecision`
 * so both real-time WS clients (`chamber-events.ts` + `session-events.ts`)
 * consume the SAME decision function instead of each growing its own copy.
 * `chamber-events.ts` re-exports the old chamber-scoped names as thin aliases
 * so existing importers keep compiling unchanged.
 *
 * This wave is what makes the "server rejects by policy" path reachable for
 * the session stream for the first time: before a bearer credential could
 * ride the WS handshake, an `oidc_jwt`-mode deployment always resolved the
 * browser socket to an anonymous principal, so a 1008 (or an expired-token
 * rejection) was unreachable in practice. Without this policy, the session
 * stream keeps retrying a rejection the server will repeat forever.
 */

/**
 * WebSocket close codes that mean "the server refused this connection and
 * will refuse it again" — RFC 6455 §7.4.1 `1008 Policy Violation` (a
 * pre-`accept()` AuthZ rejection — missing permission, or an invalid/expired
 * bearer credential) and `1003 Unsupported Data`. Reconnecting on these is a
 * guaranteed-failing loop.
 */
export const WS_POLICY_REJECT_CLOSE_CODES: readonly number[] = [1008, 1003];

/**
 * Default reconnect budget for non-policy failures. The relay/agent being
 * down is a normal operating condition (a polling fallback or degraded UI
 * covers it), so retrying is right — but forever is not.
 */
export const DEFAULT_MAX_WS_RECONNECT_ATTEMPTS = 8;

/** Why a stream permanently stopped reconnecting. */
export type WsStreamCloseReason =
  /** Caller invoked `handle.close()` — normal teardown. */
  | 'disposed'
  /** Server rejected the connection by policy (1008/1003). No reconnect. */
  | 'policy_rejected'
  /** Reconnect budget spent. Permanently degraded. */
  | 'retry_exhausted';

/** Reconnect decision for one close event — pure, so both branches are sealable. */
export type WsReconnectDecision =
  | { readonly action: 'reconnect' }
  | { readonly action: 'stop'; readonly reason: WsStreamCloseReason };

/**
 * Decide whether a closed WS socket should be retried.
 *
 * @param closeCode the WS close code, or `undefined` when the environment did
 *   not supply one (a bare `onclose()` in a test double, a constructor throw,
 *   or an older browser) — treated as a retryable transport failure.
 * @param attemptsUsed reconnect attempts already spent since the last open.
 * @param maxAttempts the reconnect budget.
 */
export function wsReconnectDecision(
  closeCode: number | undefined,
  attemptsUsed: number,
  maxAttempts: number,
): WsReconnectDecision {
  if (closeCode !== undefined && WS_POLICY_REJECT_CLOSE_CODES.includes(closeCode)) {
    return { action: 'stop', reason: 'policy_rejected' };
  }
  if (attemptsUsed >= maxAttempts) {
    return { action: 'stop', reason: 'retry_exhausted' };
  }
  return { action: 'reconnect' };
}
