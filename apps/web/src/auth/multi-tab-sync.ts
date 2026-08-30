/**
 * Cross-tab auth coordination — Increment 6 (fe-multitab-auth-robustness).
 *
 * `sessionStorage` is per-tab: signing out in one tab clears only *that* tab's
 * token copy, leaving sibling tabs authenticated against a session the operator
 * believes is closed. This module bridges that gap with a `BroadcastChannel`:
 * a user-initiated sign-out posts a message that every other tab applies as a
 * local sign-out, so "log out" means "log out everywhere".
 *
 * Layering: `session.ts` stays free of any `BroadcastChannel` dependency (pure
 * token state machine). The broadcast lives here and is invoked at the sign-out
 * call site (`route-guard.tsx::SignOutButton`); the receive side is wired once
 * at app boot (`main.tsx`).
 *
 * Echo safety: the receive handler calls the plain {@link signOut} (NOT
 * {@link signOutEverywhere}) so applying a remote sign-out does not re-broadcast
 * and ping-pong between tabs. `BroadcastChannel` also never delivers a message
 * to its own sender, so the originating tab is not notified of its own post.
 */

import { signOut, type SignOutReason } from './session';

/** SSOT `BroadcastChannel` name for cross-tab auth coordination. Colon-namespaced
 *  to match the sessionStorage key convention in `storage-keys.ts`. Single
 *  source — no other module may declare this literal. */
export const AUTH_BROADCAST_CHANNEL = 'fcc-oidc:auth-sync';

/** Discriminant for the sign-out message carried on the channel. */
export const AUTH_SYNC_SIGN_OUT = 'sign-out';

/** The ONLY sign-out reason permitted to cross tabs (SSOT). A user-initiated
 *  logout means "log out everywhere", so it broadcasts. `token_expired` /
 *  `refresh_failed` are per-tab lifecycle events — `sessionStorage` is per-tab,
 *  so each tab runs its own silent-refresh / 401 lifecycle, and a transient
 *  401 in a backgrounded tab must NOT forcibly terminate an active tab
 *  mid-operation (data-loss risk for long-running FCC sessions). See the
 *  iter-2 decision in `.claude/contracts/fe-multitab-auth-robustness.md` and
 *  the per-tab `signOut('token_expired')` in `api/auth-middleware.ts`. */
export const CROSS_TAB_SIGN_OUT_REASON = 'user_initiated' satisfies SignOutReason;

/** Type-level narrowing of {@link CROSS_TAB_SIGN_OUT_REASON} — the public
 *  broadcast API accepts only this reason, so a caller cannot widen the
 *  cross-tab sign-out to `token_expired` / `refresh_failed`. Derived from the
 *  const so the value and the type stay a single source. */
export type CrossTabSignOutReason = typeof CROSS_TAB_SIGN_OUT_REASON;

export interface AuthSyncSignOutMessage {
  readonly type: typeof AUTH_SYNC_SIGN_OUT;
  readonly reason: CrossTabSignOutReason;
}

// Lazily-created singleton channel. `null` when the runtime lacks
// `BroadcastChannel` (very old browsers / non-DOM test envs) — the feature then
// degrades to per-tab sign-out without throwing.
let channel: BroadcastChannel | null = null;

function getChannel(): BroadcastChannel | null {
  if (typeof BroadcastChannel === 'undefined') {
    return null;
  }
  channel ??= new BroadcastChannel(AUTH_BROADCAST_CHANNEL);
  return channel;
}

function isSignOutMessage(value: unknown): value is AuthSyncSignOutMessage {
  // Validate BOTH the discriminant AND the reason: only `user_initiated` may
  // cross tabs. A remote `token_expired` / `refresh_failed` (from a buggy or
  // older tab, or a hand-crafted post) is rejected here so the receive side
  // can never widen the per-tab token lifecycle into a cross-tab sign-out.
  return (
    typeof value === 'object' &&
    value !== null &&
    (value as { type?: unknown }).type === AUTH_SYNC_SIGN_OUT &&
    (value as { reason?: unknown }).reason === CROSS_TAB_SIGN_OUT_REASON
  );
}

/** Broadcast a sign-out to sibling tabs, then sign out locally. Call from the
 *  user-initiated sign-out path so every tab clears its (per-tab) session. The
 *  reason is fixed to {@link CROSS_TAB_SIGN_OUT_REASON} at the type level —
 *  `token_expired` / `refresh_failed` are per-tab and use the plain
 *  {@link signOut} instead (see `api/auth-middleware.ts`). */
export function signOutEverywhere(reason: CrossTabSignOutReason = CROSS_TAB_SIGN_OUT_REASON): void {
  const ch = getChannel();
  if (ch !== null) {
    const message: AuthSyncSignOutMessage = { type: AUTH_SYNC_SIGN_OUT, reason };
    ch.postMessage(message);
  }
  signOut(reason);
}

/** Subscribe to cross-tab sign-out broadcasts. When a sibling tab signs out,
 *  clear THIS tab's session locally (without re-broadcasting — the originating
 *  tab already notified everyone). Returns a teardown function. Call once at
 *  app boot (`main.tsx`). No-op when `BroadcastChannel` is unavailable. */
export function initMultiTabAuthSync(): () => void {
  const ch = getChannel();
  if (ch === null) {
    return () => {
      /* no channel — nothing to tear down */
    };
  }
  const handler = (event: MessageEvent): void => {
    const data: unknown = event.data;
    if (isSignOutMessage(data)) {
      signOut(data.reason);
    }
  };
  ch.addEventListener('message', handler);
  return () => {
    ch.removeEventListener('message', handler);
  };
}

/** Test-only — drop the cached channel so a fresh `BroadcastChannel` stub is
 *  picked up between cases. Production code must not call this. */
export function __resetMultiTabSyncForTests(): void {
  if (channel !== null) {
    channel.close();
    channel = null;
  }
}
