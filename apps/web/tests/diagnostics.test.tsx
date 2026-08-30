import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { sessionClient } from '@/api/session-client';
import { __resetRuntimeConfigCacheForTests } from '@/config/runtime';
import { DiagnosticsRoute } from '@/routes/diagnostics';

import type { ReactElement } from 'react';

/**
 * Diagnostics ([설정]>진단) panel tests (tester-ux Phase S-diag — migrated from
 * the former overview.test.tsx; the panel moved out of the operator landing).
 *
 * Built from the shared `@/ui` kit over the typed session client (mocked).
 * Covers: runtime-environment metrics (E2E-stable testids preserved), the
 * success path (structured api_version + operations, NOT a raw payload dump),
 * the error path mapped through `describeApiError`, and the central-hub gating
 * (no /session/info call when sessionApiEnabled is false).
 */

/**
 * The REAL session client is used and only its transport is spied
 * (session-workbook-upload-ui M3, 2026-09-01). It used to be replaced wholesale
 * by `vi.mock('@/api/session-client')`, which was fine while the route built its
 * own failures inline — and stopped being fine when the request plumbing moved
 * into the client, because a whole-module mock would have made the route test
 * pass against a client that never runs. `vi.spyOn` on the exported object is
 * the pattern `platform-client.test.ts` already uses for the same reason.
 */
const sessionGet = vi.spyOn(sessionClient, 'GET');

function renderRoute(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/diagnostics']}>
        <DiagnosticsRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(tree);
}

afterEach(() => {
  sessionGet.mockReset();
  vi.clearAllMocks();
  // Restore the default (session-enabled) runtime config + clear the cache so
  // the sessionApiEnabled override below does not leak into other cases.
  __resetRuntimeConfigCacheForTests();
  window.__FCC_RUNTIME_CONFIG__ = globalThis.__FCC_TEST_DEFAULT_RUNTIME_CONFIG__;
});

describe('DiagnosticsRoute', () => {
  it('renders runtime environment metrics with E2E-stable testids', () => {
    sessionGet.mockResolvedValue({
      data: { api_version: '2', operations: [] },
      error: undefined,
      response: { status: 200, headers: new Headers() },
    } as never);
    renderRoute();

    // These three testids are asserted by tests/e2e/smoke.spec.ts — keep them.
    expect(screen.getByTestId('env-name')).toBeInTheDocument();
    expect(screen.getByTestId('build-version')).toBeInTheDocument();
    expect(screen.getByTestId('api-base-url')).toBeInTheDocument();
    expect(screen.getByTestId('diagnostics-workbench-overview')).toBeInTheDocument();
    expect(screen.getByTestId('diagnostics-workbench')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '진단 작업 흐름' })).toBeInTheDocument();
    expect(screen.getByTestId('diagnostics-next-jobs')).toHaveAttribute('href', '/jobs');
    expect(screen.getByTestId('diagnostics-next-chambers')).toHaveAttribute('href', '/chambers');
    expect(screen.getByTestId('diagnostics-next-reports')).toHaveAttribute('href', '/reports');
  });

  it('renders session info as structured metrics, not a raw payload dump', async () => {
    sessionGet.mockResolvedValue({
      data: { api_version: '2', operations: ['start_session', 'stop_session'] },
      error: undefined,
      response: { status: 200, headers: new Headers() },
    } as never);
    renderRoute();

    await waitFor(() => expect(screen.getByTestId('session-api-version')).toHaveTextContent('2'));
    expect(screen.getByTestId('session-operations-count')).toHaveTextContent('2');
    const operations = screen.getByTestId('session-operations');
    expect(operations).toHaveTextContent('start_session');
    expect(operations).toHaveTextContent('stop_session');
    // No raw JSON payload dump remains on the panel.
    expect(screen.queryByTestId('session-info-payload')).not.toBeInTheDocument();
  });

  it('maps a failed session info request through describeApiError', async () => {
    sessionGet.mockResolvedValue({
      data: undefined,
      // A real problem body now that the route goes through the client: the
      // `code` is extracted and would refine the copy if the taxonomy mapped it.
      error: { type: 'about:blank', title: 'Forbidden', status: 403, code: 'FORBIDDEN' },
      response: { status: 403, headers: new Headers() },
    } as never);
    renderRoute();

    const errorState = await screen.findByTestId('session-error');
    expect(errorState).toHaveTextContent('권한이');
  });

  // Browser-integration closure (B1/P13): in a topology that does not serve the
  // Session API (central hub — sessionApiEnabled:false) the diagnostics panel
  // MUST NOT call /session/info (the rejected P0). It shows an unavailable note.
  it('does not call the session API when sessionApiEnabled is false', () => {
    __resetRuntimeConfigCacheForTests();
    const base = globalThis.__FCC_TEST_DEFAULT_RUNTIME_CONFIG__ as Record<string, unknown>;
    window.__FCC_RUNTIME_CONFIG__ = { ...base, sessionApiEnabled: false };
    renderRoute();

    // The panel still renders the environment metrics …
    expect(screen.getByTestId('env-name')).toBeInTheDocument();
    // … but shows the session-unavailable note and issues NO session call.
    expect(screen.getByTestId('session-unavailable')).toBeInTheDocument();
    expect(screen.getByTestId('diagnostics-next-state')).toHaveTextContent('중앙 허브');
    expect(screen.queryByTestId('session-loading')).not.toBeInTheDocument();
    expect(sessionGet).not.toHaveBeenCalled();
  });
});
