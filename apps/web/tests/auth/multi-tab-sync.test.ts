import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  AUTH_BROADCAST_CHANNEL,
  AUTH_SYNC_SIGN_OUT,
  __resetMultiTabSyncForTests,
  initMultiTabAuthSync,
  signOutEverywhere,
} from '@/auth/multi-tab-sync';
import { __resetAuthStateForTests, applyTokenSet, getAuthState } from '@/auth/session';

import type { OidcTokenSet } from '@/auth/oidc-pkce';

/**
 * Increment 6 — cross-tab logout sync (fe-multitab-auth-robustness).
 *
 * `sessionStorage` is per-tab, so each tab carries its own token copy. These
 * tests pin that a user-initiated sign-out broadcasts to sibling tabs, that a
 * received broadcast clears THIS tab's session, and that applying a remote
 * sign-out does NOT re-broadcast (echo guard).
 */

// ─── Synchronous in-memory BroadcastChannel fake ─────────────────────────────
// Delivers a post to every OTHER instance on the same name (never the sender —
// matching the real BroadcastChannel contract) and records every post so the
// echo guard is observable.
class FakeBroadcastChannel {
  static instances: FakeBroadcastChannel[] = [];
  static posts: { name: string; data: unknown }[] = [];

  readonly name: string;
  private readonly listeners = new Set<(e: MessageEvent) => void>();
  private closed = false;

  constructor(name: string) {
    this.name = name;
    FakeBroadcastChannel.instances.push(this);
  }

  postMessage(data: unknown): void {
    FakeBroadcastChannel.posts.push({ name: this.name, data });
    for (const inst of FakeBroadcastChannel.instances) {
      if (inst === this || inst.closed || inst.name !== this.name) continue;
      const event = new MessageEvent('message', { data });
      for (const listener of inst.listeners) listener(event);
    }
  }

  addEventListener(type: string, listener: (e: MessageEvent) => void): void {
    if (type === 'message') this.listeners.add(listener);
  }

  removeEventListener(type: string, listener: (e: MessageEvent) => void): void {
    if (type === 'message') this.listeners.delete(listener);
  }

  close(): void {
    this.closed = true;
    this.listeners.clear();
  }

  static reset(): void {
    FakeBroadcastChannel.instances = [];
    FakeBroadcastChannel.posts = [];
  }
}

function makeTokenSet(): OidcTokenSet {
  return Object.freeze({
    accessToken: 'opaque-access-token',
    refreshToken: null, // no refresh_token → no scheduled timer, keeps the test simple
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
}

beforeEach(() => {
  vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel);
  __resetMultiTabSyncForTests();
  FakeBroadcastChannel.reset();
  __resetAuthStateForTests();
  sessionStorage.clear();
});

afterEach(() => {
  __resetMultiTabSyncForTests();
  vi.unstubAllGlobals();
});

describe('multi-tab sign-out sync', () => {
  it('exposes the channel name as an SSOT constant', () => {
    expect(AUTH_BROADCAST_CHANNEL).toBe('fcc-oidc:auth-sync');
    expect(AUTH_SYNC_SIGN_OUT).toBe('sign-out');
  });

  it('signOutEverywhere broadcasts to sibling tabs AND clears the local session', () => {
    const sibling = new BroadcastChannel(AUTH_BROADCAST_CHANNEL);
    const received: unknown[] = [];
    sibling.addEventListener('message', (e) => received.push(e.data));

    applyTokenSet(makeTokenSet());
    expect(getAuthState().kind).toBe('authenticated');

    signOutEverywhere('user_initiated');

    // Local session cleared.
    expect(getAuthState().kind).toBe('unauthenticated');
    // Sibling tab received the sign-out message.
    expect(received).toEqual([{ type: 'sign-out', reason: 'user_initiated' }]);
  });

  it('a received broadcast clears this tab session and preserves the reason', () => {
    initMultiTabAuthSync();
    applyTokenSet(makeTokenSet());
    expect(getAuthState().kind).toBe('authenticated');

    // Another tab signs out (user-initiated — the only reason that crosses tabs).
    const otherTab = new BroadcastChannel(AUTH_BROADCAST_CHANNEL);
    otherTab.postMessage({ type: AUTH_SYNC_SIGN_OUT, reason: 'user_initiated' });

    const state = getAuthState();
    expect(state.kind).toBe('unauthenticated');
    if (state.kind === 'unauthenticated') {
      expect(state.reason).toBe('user_initiated');
    }
  });

  it('ignores a remote token_expired broadcast (only user_initiated crosses tabs)', () => {
    // The iter-2 decision: token_expired is a per-tab lifecycle event. Even if a
    // buggy/older tab or a hand-crafted post puts `token_expired` on the channel,
    // the receive side must reject it — a transient 401 in a backgrounded tab
    // must never forcibly sign out an active tab mid-operation.
    initMultiTabAuthSync();
    applyTokenSet(makeTokenSet());
    expect(getAuthState().kind).toBe('authenticated');

    const otherTab = new BroadcastChannel(AUTH_BROADCAST_CHANNEL);
    otherTab.postMessage({ type: AUTH_SYNC_SIGN_OUT, reason: 'token_expired' });
    otherTab.postMessage({ type: AUTH_SYNC_SIGN_OUT, reason: 'refresh_failed' });

    // Still authenticated — neither non-user reason is honored cross-tab.
    expect(getAuthState().kind).toBe('authenticated');
  });

  it('applying a remote sign-out does NOT re-broadcast (echo guard)', () => {
    initMultiTabAuthSync();
    applyTokenSet(makeTokenSet());
    FakeBroadcastChannel.posts = []; // forget setup posts; count only what follows (keep instances)

    const otherTab = new BroadcastChannel(AUTH_BROADCAST_CHANNEL);
    otherTab.postMessage({ type: AUTH_SYNC_SIGN_OUT, reason: 'user_initiated' });

    // Exactly one post total — the originating tab's. The receiving tab applied
    // a plain (non-broadcasting) signOut, so there is no echo.
    expect(FakeBroadcastChannel.posts).toHaveLength(1);
    expect(getAuthState().kind).toBe('unauthenticated');
  });

  it('ignores unrelated messages on the channel', () => {
    initMultiTabAuthSync();
    applyTokenSet(makeTokenSet());

    const otherTab = new BroadcastChannel(AUTH_BROADCAST_CHANNEL);
    otherTab.postMessage({ type: 'something-else' });
    otherTab.postMessage(null);

    // Still authenticated — only AUTH_SYNC_SIGN_OUT messages sign out.
    expect(getAuthState().kind).toBe('authenticated');
  });

  it('teardown unsubscribes so later broadcasts are ignored', () => {
    const teardown = initMultiTabAuthSync();
    applyTokenSet(makeTokenSet());
    teardown();

    const otherTab = new BroadcastChannel(AUTH_BROADCAST_CHANNEL);
    otherTab.postMessage({ type: AUTH_SYNC_SIGN_OUT, reason: 'user_initiated' });

    expect(getAuthState().kind).toBe('authenticated');
  });
});
