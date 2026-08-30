import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { REFETCH_STRATEGIES } from '@/api/query-config';
import { sessionClient } from '@/api/session-client';
import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import { __resetRuntimeConfigCacheForTests } from '@/config/runtime';
import { DEFAULT_LOCALE, setLocale, t } from '@/i18n';
import { ControlRoute, classifyControlOutcome, deriveRunState } from '@/routes/control';

import type { ReactElement } from 'react';
import type { MockInstance } from 'vitest';

/**
 * FE-P5 (2026-05-26) — remote control route tests.
 *
 * Auth state is driven through the real `auth/session.ts` SSOT (same pattern as
 * route-guard.test.tsx) so `RequirePermission` gating is exercised end-to-end.
 * The Session API client and the WS stream are mocked — the stream itself is
 * unit-tested in session-events.test.ts; here we assert the control surface
 * (RBAC gating, stop dispatch, 5-state failure taxonomy, progress).
 */

/**
 * The REAL session client is used and only its transport is spied
 * (session-workbook-upload-ui M3, 2026-09-01). Replacing the whole module —
 * what this file used to do — was harmless while the route hand-rolled its own
 * requests, and became misleading once the plumbing moved into the client:
 * a module mock would let this file pass against a client that never runs, and
 * the machine-readable `code` the screen now branches on is extracted inside
 * that client. `vi.spyOn` on the exported object is the pattern
 * `platform-client.test.ts` already uses.
 */
/**
 * ⚠️ Re-established per test, not at module scope: this file's `afterEach` calls
 * `vi.restoreAllMocks()`, which would put the real transport back after the
 * first case and leave every later one issuing a real request.
 */
let sessionGet: MockInstance<typeof sessionClient.GET>;
let sessionPost: MockInstance<typeof sessionClient.POST>;
vi.mock('@/api/session-events', () => ({
  createSessionEventStream: vi.fn(() => ({ close: vi.fn() })),
}));

/**
 * Mock response builders. The cast lives here once instead of at every call
 * site: now that the real client is used, `openapi-fetch`'s result type is
 * enforced on these fixtures, and it wants a whole `Response`.
 *
 * `fail` builds a real RFC 9457 problem body, because that is what the node
 * sends and what the client extracts `code` from — a bare `{ detail }` would
 * exercise a shape this surface stopped producing in 2026-08.
 */
const ok = (data: unknown) =>
  ({ data, error: undefined, response: { status: 200, headers: new Headers() } }) as never;
const fail = (status: number, code: string) =>
  ({
    data: undefined,
    error: { type: 'about:blank', title: 'x', status, detail: 'prose', code },
    response: { status, headers: new Headers() },
  }) as never;

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).replace(/=+$/u, '');
  const body = btoa(JSON.stringify(payload))
    .replace(/=+$/u, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  return `${header}.${body}.sig`;
}

function authenticateAs(permissions: readonly string[]): void {
  applyTokenSet({
    accessToken: makeJwt({ sub: 'op-1', [CLAIM_PERMISSIONS]: permissions }),
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
}

function renderControl(): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/control']}>
        <ControlRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
}

function progressOk(isRunning: boolean): void {
  sessionGet.mockResolvedValue(
    ok({
      completed: isRunning ? 2 : 0,
      is_running: isRunning,
      ratio: isRunning ? 0.5 : 0,
      total: 4,
    }),
  );
}

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
  sessionGet = vi.spyOn(sessionClient, 'GET');
  sessionPost = vi.spyOn(sessionClient, 'POST');
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  // The locale is module-global; restore the default so cases stay isolated.
  setLocale(DEFAULT_LOCALE);
  // Restore the default (session-enabled) runtime config + clear the cache.
  __resetRuntimeConfigCacheForTests();
  window.__FCC_RUNTIME_CONFIG__ = globalThis.__FCC_TEST_DEFAULT_RUNTIME_CONFIG__;
});

describe('classifyControlOutcome', () => {
  it('maps HTTP status to the 5-state failure taxonomy', () => {
    expect(classifyControlOutcome(403)).toBe('forbidden');
    expect(classifyControlOutcome(503)).toBe('unreachable');
    expect(classifyControlOutcome(undefined)).toBe('unreachable');
    expect(classifyControlOutcome(500)).toBe('error');
  });
});

describe('deriveRunState (M6 three-state run status)', () => {
  it('separates a confirmed idle session from one the server never described', () => {
    expect(deriveRunState(true, true)).toBe('running');
    expect(deriveRunState(false, true)).toBe('idle');
    // The `?? false` collapse this replaces turned BOTH of the next two into
    // "idle", i.e. into a confident claim the screen had no basis for.
    expect(deriveRunState(undefined, false)).toBe('unknown');
    expect(deriveRunState(undefined, true)).toBe('unknown');
  });
});

describe('ControlRoute session-surface gating (B1/P13)', () => {
  it('shows an unavailable note and issues no session call when sessionApiEnabled is false', () => {
    __resetRuntimeConfigCacheForTests();
    const base = globalThis.__FCC_TEST_DEFAULT_RUNTIME_CONFIG__ as Record<string, unknown>;
    window.__FCC_RUNTIME_CONFIG__ = { ...base, sessionApiEnabled: false };
    authenticateAs(['session:control']);
    renderControl();
    expect(screen.getByTestId('control-unavailable')).toBeInTheDocument();
    // The panel (and its /session/progress query) must not mount.
    expect(screen.queryByTestId('control-stop')).not.toBeInTheDocument();
    expect(sessionGet).not.toHaveBeenCalled();
  });
});

describe('ControlRoute RBAC', () => {
  it('denies the control panel without session:control', async () => {
    authenticateAs(['session:read']);
    progressOk(false);
    renderControl();
    expect(await screen.findByTestId('auth-failure-permission_denied')).toBeInTheDocument();
    expect(screen.queryByTestId('control-stop')).not.toBeInTheDocument();
  });

  it('renders control panel + progress for an operator with session:control', async () => {
    authenticateAs(['session:control']);
    progressOk(false);
    renderControl();
    expect(await screen.findByTestId('control-workbench-overview')).toBeInTheDocument();
    expect(screen.getByTestId('control-workbench')).toBeInTheDocument();
    expect(await screen.findByTestId('control-stop')).toBeDisabled();
    expect(screen.getByTestId('control-stop')).toBeDisabled();
    await waitFor(() => expect(screen.getByTestId('progress-running')).toHaveTextContent('아니오'));
  });

  it('gates the live event log behind session:events', async () => {
    authenticateAs(['session:control']); // control but NOT events
    progressOk(false);
    renderControl();
    await screen.findByTestId('control-stop');
    expect(screen.getByTestId('control-events-panel')).toBeInTheDocument();
    // events panel renders a nested permission_denied, no events-log
    expect(screen.queryByTestId('events-log')).not.toBeInTheDocument();
  });

  it('opens the WS event stream only while a run is in progress (enabled=isRunning)', async () => {
    const { createSessionEventStream } = await import('@/api/session-events');
    const streamFn = vi.mocked(createSessionEventStream);
    streamFn.mockClear();
    authenticateAs(['session:control', 'session:events']);
    progressOk(false); // not running → stream must NOT open
    renderControl();
    await screen.findByTestId('control-stop');
    await waitFor(() => expect(screen.getByTestId('events-status')).toBeInTheDocument());
    expect(streamFn).not.toHaveBeenCalled();
  });

  it('opens the WS event stream once a run is active', async () => {
    const { createSessionEventStream } = await import('@/api/session-events');
    const streamFn = vi.mocked(createSessionEventStream);
    streamFn.mockClear();
    authenticateAs(['session:control', 'session:events']);
    progressOk(true); // running → stream opens
    renderControl();
    await waitFor(() => expect(streamFn).toHaveBeenCalled());
  });
});

describe('ControlRoute stop dispatch + outcomes', () => {
  it('dispatches POST /session/stop on click', async () => {
    authenticateAs(['session:control']);
    progressOk(true);
    sessionPost.mockResolvedValue({
      data: {},
      error: undefined,
      response: { status: 200, headers: new Headers() },
    } as never);
    renderControl();
    await userEvent.click(await screen.findByTestId('control-stop'));
    await waitFor(() => expect(sessionPost).toHaveBeenCalledWith('/session/stop', {}));
  });

  it('surfaces forbidden outcome on a 403 stop', async () => {
    authenticateAs(['session:control']);
    progressOk(true);
    sessionPost.mockResolvedValue({
      data: undefined,
      error: { type: 'about:blank', title: 'Forbidden', status: 403, code: 'FORBIDDEN' },
      response: { status: 403, headers: new Headers() },
    } as never);
    renderControl();
    await userEvent.click(await screen.findByTestId('control-stop'));
    expect(await screen.findByTestId('control-outcome-forbidden')).toBeInTheDocument();
  });

  it('degrades to unreachable when the agent Session API is offline during stop', async () => {
    authenticateAs(['session:control']);
    progressOk(true);
    sessionPost.mockResolvedValue(fail(503, 'UPSTREAM_UNAVAILABLE'));
    renderControl();
    await userEvent.click(await screen.findByTestId('control-stop'));
    expect(await screen.findByTestId('control-outcome-unreachable')).toBeInTheDocument();
  });

  it('enables the emergency stop while a run is in progress', async () => {
    authenticateAs(['session:control']);
    progressOk(true);
    renderControl();
    await waitFor(() => expect(screen.getByTestId('control-stop')).toBeEnabled());
  });

  it('observes a run that was started outside this screen (S9)', async () => {
    // D6 — the progress poll used to park permanently the first time it saw
    // `is_running: false`, and never re-armed (no focus refetch either). A run
    // started from the node PC, another browser, or the chambers surface was
    // therefore invisible here FOREVER, and the stop button — the one control
    // that matters in an emergency — stayed locked because this screen had
    // decided nothing was running.
    vi.useFakeTimers();
    try {
      const { createSessionEventStream } = await import('@/api/session-events');
      const streamFn = vi.mocked(createSessionEventStream);
      streamFn.mockClear();
      authenticateAs(['session:control', 'session:events']);
      let poll = 0;
      sessionGet.mockImplementation(() => {
        poll += 1;
        const running = poll > 1;
        return Promise.resolve(
          ok({
            completed: running ? 1 : 0,
            is_running: running,
            ratio: running ? 0.25 : 0,
            total: 4,
          }),
        );
      });

      renderControl();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(screen.getByTestId('control-stop')).toBeDisabled();

      // Somebody else starts a run. This screen issued no start command...
      await act(async () => {
        await vi.advanceTimersByTimeAsync(REFETCH_STRATEGIES.MONITORED.refetchInterval);
      });
      // A short extra tick lets the re-read's response land and re-render. It is
      // sized at the CRITICAL cadence rather than another MONITORED window so the
      // now-running query does not fire ~20 more polls inside the assertion.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(REFETCH_STRATEGIES.CRITICAL.refetchInterval);
      });
      expect(sessionGet.mock.calls.length).toBeGreaterThan(1);
      expect(sessionPost).not.toHaveBeenCalled();
      // ...and yet it now knows, and can stop it.
      expect(screen.getByTestId('control-stop')).toBeEnabled();
      expect(streamFn).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps stop shut, and says why, while the run state is unknown (S10)', async () => {
    // `is_running ?? false` collapsed "the server has not told us" into
    // "definitely idle". Opening stop on a guess invites an operator to fire a
    // stop command at a session nobody has confirmed exists; keeping it shut
    // with NO explanation is the silent-disabled state the surface had before.
    authenticateAs(['session:control']);
    sessionGet.mockResolvedValue(fail(503, 'UPSTREAM_UNAVAILABLE'));

    renderControl();
    await waitFor(() => expect(screen.getByTestId('progress-error')).toBeInTheDocument());

    expect(screen.getByTestId('control-stop')).toBeDisabled();
    expect(screen.getByTestId('control-stop-reason')).toHaveTextContent(
      t('routes.control.stopUnavailableUnknown'),
    );
  });

  it('distinguishes a confirmed-idle session from an unknown one in the copy', async () => {
    authenticateAs(['session:control']);
    progressOk(false);
    renderControl();
    // Wait for the CONTENT, not just the node: the reason line is present from
    // the first paint (a pending read is genuinely "unknown"), so waiting on
    // presence alone would assert against the loading state.
    await waitFor(() =>
      expect(screen.getByTestId('control-stop-reason')).toHaveTextContent(
        t('routes.control.stopUnavailableIdle'),
      ),
    );
    // The two states must not share a sentence — that is the whole distinction.
    expect(t('routes.control.stopUnavailableIdle')).not.toBe(
      t('routes.control.stopUnavailableUnknown'),
    );
  });

  it('does not re-lock the emergency stop because one mid-run poll failed', async () => {
    // The safety asymmetry inside M6: a run is confirmed live, then the next
    // poll fails. Treating query HEALTH as the "has reported" predicate would
    // flip the surface to `unknown` and lock stop again — reinstating the exact
    // lock this milestone removes, at the moment it is most dangerous. A
    // retained snapshot is stale, not absent; the failed read is surfaced
    // separately as an error rather than by withdrawing the control.
    vi.useFakeTimers();
    try {
      authenticateAs(['session:control']);
      let poll = 0;
      sessionGet.mockImplementation(() => {
        poll += 1;
        if (poll === 1) {
          return Promise.resolve(ok({ completed: 2, is_running: true, ratio: 0.5, total: 4 }));
        }
        return Promise.resolve(fail(503, 'UPSTREAM_UNAVAILABLE'));
      });

      renderControl();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(screen.getByTestId('control-stop')).toBeEnabled();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(REFETCH_STRATEGIES.CRITICAL.refetchInterval * 2);
      });
      expect(sessionGet.mock.calls.length).toBeGreaterThan(1);
      expect(screen.getByTestId('progress-error')).toBeInTheDocument();
      expect(screen.getByTestId('control-stop')).toBeEnabled();
      expect(screen.queryByTestId('control-stop-reason')).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('says nothing about stop being unavailable while a run is live', async () => {
    authenticateAs(['session:control']);
    progressOk(true);
    renderControl();
    await waitFor(() => expect(screen.getByTestId('control-stop')).toBeEnabled());
    expect(screen.queryByTestId('control-stop-reason')).not.toBeInTheDocument();
  });

  it('re-renders the failure copy in the active locale after setLocale (live, not module-load snapshot)', async () => {
    // Regression: OUTCOME_COPY used to snapshot t() at module load, so the
    // error copy stayed frozen to the import-time locale after a switch
    // (iter-02 P0). The copy now resolves through the render-time t() (useT()).
    authenticateAs(['session:control']);
    progressOk(true);
    sessionPost.mockResolvedValue({
      data: undefined,
      error: { type: 'about:blank', title: 'Forbidden', status: 403, code: 'FORBIDDEN' },
      response: { status: 403, headers: new Headers() },
    } as never);
    renderControl();
    await userEvent.click(await screen.findByTestId('control-stop'));
    // Default locale (ko) copy first. Phase L (§4): the forbidden copy is the
    // generic tester-language message — it must NOT leak the permission token.
    const panel = await screen.findByTestId('control-outcome-forbidden');
    expect(panel).toHaveTextContent('이 작업을 할 권한이 없어요. 관리자에게 문의하세요.');
    expect(panel).not.toHaveTextContent('session:control');
    // Switching the locale must re-render the already-mounted panel into English.
    act(() => setLocale('en'));
    await waitFor(() =>
      expect(screen.getByTestId('control-outcome-forbidden')).toHaveTextContent(
        "You don't have permission for this action. Please contact an administrator.",
      ),
    );
  });
});
