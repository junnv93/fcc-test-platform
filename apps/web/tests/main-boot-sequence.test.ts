import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type * as SessionModule from '@/auth/session';
import type * as StorageKeysModule from '@/auth/storage-keys';

/**
 * Wave `fe-w4-bundle-observability-cost` (2026-07-31) — S6.
 *
 * Making observability conditional turned part of `main()` asynchronous, and an
 * `await` in a boot sequence is exactly the kind of change that silently
 * reorders things. `main.tsx` states two ordering guarantees in prose; this file
 * turns them into assertions, together with the auth boot sequence and the
 * pre-React config-failure path that must not have moved at all.
 *
 * `main.tsx` runs `main()` at module scope, so each case drives it by importing
 * the module after registering its own mocks.
 */

interface MainProbe {
  order: string[];
  render: ReturnType<typeof vi.fn>;
  /** Resolves the pending `initObservability`, if the case deferred it. */
  releaseObservability: () => void;
}

async function runMain(options: { deferObservability?: boolean } = {}): Promise<MainProbe> {
  vi.resetModules();

  const order: string[] = [];
  const render = vi.fn(() => order.push('render'));
  let release: () => void = () => undefined;

  vi.doMock('react-dom/client', () => ({
    createRoot: vi.fn(() => {
      order.push('createRoot');
      return { render, unmount: vi.fn() };
    }),
  }));
  vi.doMock('@/app', () => ({ App: () => null }));
  vi.doMock('@/observability/bootstrap', () => ({
    initObservability: vi.fn(async () => {
      order.push('initObservability:start');
      if (options.deferObservability === true) {
        await new Promise<void>((resolve) => {
          release = () => {
            order.push('initObservability:end');
            resolve();
          };
        });
      } else {
        order.push('initObservability:end');
      }
    }),
  }));
  vi.doMock('@/auth/storage-keys', async () => ({
    ...(await vi.importActual<typeof StorageKeysModule>('@/auth/storage-keys')),
    purgeLegacyStorage: vi.fn(() => order.push('purgeLegacyStorage')),
  }));
  vi.doMock('@/auth/session', async () => ({
    ...(await vi.importActual<typeof SessionModule>('@/auth/session')),
    restoreSession: vi.fn(() => order.push('restoreSession')),
  }));
  vi.doMock('@/auth/multi-tab-sync', () => ({
    initMultiTabAuthSync: vi.fn(() => order.push('initMultiTabAuthSync')),
  }));

  await import('@/main');
  return { order, render, releaseObservability: () => release() };
}

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
});

afterEach(() => {
  vi.resetModules();
  vi.doUnmock('react-dom/client');
  vi.doUnmock('@/app');
  vi.doUnmock('@/observability/bootstrap');
  vi.doUnmock('@/auth/storage-keys');
  vi.doUnmock('@/auth/session');
  vi.doUnmock('@/auth/multi-tab-sync');
  vi.restoreAllMocks();
});

describe('S6 — the boot sequence survives becoming asynchronous', () => {
  it('keeps observability, then the auth sequence, then the React render', async () => {
    const probe = await runMain();
    // `main()` is fired at module scope; let its microtasks drain.
    await vi.waitFor(() => expect(probe.render).toHaveBeenCalledTimes(1));

    expect(probe.order).toEqual([
      'initObservability:start',
      'initObservability:end',
      'purgeLegacyStorage',
      'restoreSession',
      'initMultiTabAuthSync',
      'createRoot',
      'render',
    ]);
  });

  it('does not render until observability has settled', async () => {
    // This is the tracing guarantee in executable form: the app must not mount
    // — and therefore must not issue its first fetch — before the OTel fetch
    // instrumentation has had its chance to register.
    const probe = await runMain({ deferObservability: true });

    // Give any stray microtask a chance to render early.
    await Promise.resolve();
    await Promise.resolve();
    expect(probe.render).not.toHaveBeenCalled();
    expect(probe.order).toEqual(['initObservability:start']);

    probe.releaseObservability();
    await vi.waitFor(() => expect(probe.render).toHaveBeenCalledTimes(1));
  });
});

describe('M4 — the pre-React failure paths are untouched', () => {
  it('paints the boot error and never mounts React when runtime config is invalid', async () => {
    const w = window as Window & { __FCC_RUNTIME_CONFIG__?: unknown };
    const saved = w.__FCC_RUNTIME_CONFIG__;
    w.__FCC_RUNTIME_CONFIG__ = { apiBaseUrl: 'not-a-url' };
    try {
      const probe = await runMain();

      const banner = document.querySelector('[data-testid="boot-error"]');
      expect(banner).not.toBeNull();
      expect(banner?.getAttribute('role')).toBe('alert');
      expect(banner?.className).toBe('boot-error');
      expect(probe.render).not.toHaveBeenCalled();
      // Not even observability starts — a misconfigured deployment must fail
      // visibly and immediately, not after a chunk round-trip.
      expect(probe.order).toEqual([]);
    } finally {
      w.__FCC_RUNTIME_CONFIG__ = saved;
    }
  });

  it('throws synchronously when #root is missing rather than as an unhandled rejection', async () => {
    document.body.innerHTML = '';
    // An unhandled rejection is easy to miss in a browser console; the
    // synchronous throw predates this wave and must stay synchronous, which is
    // why `main()` did not become `async`.
    await expect(runMain()).rejects.toThrow(/#root/u);
  });
});
