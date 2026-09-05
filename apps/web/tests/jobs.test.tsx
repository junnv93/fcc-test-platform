import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { queryKeys, REFETCH_STRATEGIES } from '@/api/query-config';
import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import { fileName, isStoppableStatus, JobsRoute } from '@/routes/jobs';

import { headlessOk, headlessProblem, problemDetails } from './helpers/headless-contract';
import { spyHeadlessTransport } from './helpers/headless-transport';
import { cardView, tableView } from './helpers/responsive-table';

import type { HeadlessOkBody } from './helpers/headless-contract';
import type { ReactElement } from 'react';

const headlessClient = spyHeadlessTransport();

const STATUS_PATH = '/headless/status';
const JOBS_PATH = '/headless/jobs';
const STOP_PATH = '/headless/jobs/{job_uuid}/stop';

type StatusSnapshot = HeadlessOkBody<'get', typeof STATUS_PATH>;
type JobList = HeadlessOkBody<'get', typeof JOBS_PATH>;

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

function mockHeadless(status: StatusSnapshot, jobs: JobList): void {
  headlessClient.routes({
    [STATUS_PATH]: { get: () => headlessOk('get', STATUS_PATH, status) },
    [JOBS_PATH]: { get: () => headlessOk('get', JOBS_PATH, jobs) },
  });
}

function renderJobs(): QueryClient {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/jobs']}>
        <JobsRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
  return queryClient;
}

function statusSnapshot(overrides: Record<string, number> = {}): StatusSnapshot {
  return {
    measurement_jobs: {
      counts: {
        queued: 2,
        running: 1,
        completed: 3,
        failed: 1,
        cancelled: 0,
        ...overrides,
      },
      recent: [],
    },
    workers: [],
    report_automation: { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 },
  };
}

function job(overrides: Partial<JobList[number]> = {}): JobList[number] {
  return {
    // ⚠️ contract v0.1.22 — the storage primary key left the wire; the opaque
    // handle is what the list carries and what the stop route takes.
    job_uuid: 'job-1',
    status: 'queued',
    excel_path: 'C:\\plans\\alpha.xlsx',
    requested_by: 'operator',
    status_message: '',
    stop_requested: false,
    payload: {},
    options: {},
    created_at: '2026-06-14T00:00:00Z',
    updated_at: '2026-06-14T00:00:00Z',
    ...overrides,
  };
}

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
  headlessClient.GET.mockReset();
  headlessClient.POST.mockReset();
});

describe('jobs helpers', () => {
  it('extracts cross-platform file names and identifies stoppable statuses', () => {
    expect(fileName('C:\\plans\\alpha.xlsx')).toBe('alpha.xlsx');
    expect(fileName('/tmp/beta.xlsx')).toBe('beta.xlsx');
    expect(isStoppableStatus('queued')).toBe(true);
    expect(isStoppableStatus(' running ')).toBe(true);
    expect(isStoppableStatus('completed')).toBe(false);
  });
});

describe('JobsRoute', () => {
  it('renders a table skeleton during the initial job list load (E4 loading contract)', async () => {
    authenticateAs(['headless:read']);
    // Never resolve so the list query stays in its first-load (isLoading) phase.
    headlessClient.GET.mockImplementation(() => new Promise(() => undefined));
    renderJobs();
    expect(await screen.findByTestId('data-table-skeleton')).toBeInTheDocument();
  });

  it('renders queue counts and the job list', async () => {
    authenticateAs(['headless:read']);
    mockHeadless(statusSnapshot(), [
      job({ job_uuid: 'job-1', status: 'queued', excel_path: 'C:\\plans\\alpha.xlsx' }),
      job({
        job_uuid: 'job-2',
        status: 'completed',
        excel_path: '/tmp/beta.xlsx',
        requested_by: 'qa',
      }),
    ]);

    renderJobs();

    expect(screen.getByTestId('jobs-workbench-overview')).toBeInTheDocument();
    expect(screen.getByTestId('jobs-workbench')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '측정 작업 흐름' })).toBeInTheDocument();
    expect(screen.getByTestId('jobs-next-chambers')).toHaveAttribute('href', '/chambers');
    expect(screen.getByTestId('jobs-next-sessions')).toHaveAttribute('href', '/sessions');
    expect(screen.getByTestId('jobs-next-reports')).toHaveAttribute('href', '/reports');
    await waitFor(() => expect(screen.getByTestId('job-count-queued')).toHaveTextContent('2'));
    expect(screen.getByTestId('job-count-running')).toHaveTextContent('1');
    expect(screen.getAllByTestId('job-row')).toHaveLength(2);
    expect(tableView('jobs-table').getByText('alpha.xlsx')).toBeInTheDocument();
    expect(tableView('jobs-table').getByText('beta.xlsx')).toBeInTheDocument();
    // §M7.2 — the phone projection is rendered from the same descriptor and
    // carries the columns the compact band folds away (requester/worker), so
    // narrowing the viewport never costs the operator a value.
    expect(cardView().getAllByTestId('data-table-card')).toHaveLength(2);
    expect(cardView().getByText('qa')).toBeInTheDocument();
  });

  it('filters loaded rows client-side by status', async () => {
    authenticateAs(['headless:read']);
    mockHeadless(statusSnapshot(), [
      job({ job_uuid: 'job-1', status: 'queued', excel_path: 'queued.xlsx' }),
      job({ job_uuid: 'job-2', status: 'failed', excel_path: 'failed.xlsx' }),
    ]);

    renderJobs();

    await waitFor(() => expect(screen.getAllByTestId('job-row')).toHaveLength(2));
    await userEvent.selectOptions(screen.getByTestId('job-status-filter'), 'failed');
    expect(screen.getAllByTestId('job-row')).toHaveLength(1);
    expect(tableView('jobs-table').getByText('failed.xlsx')).toBeInTheDocument();
    expect(screen.queryByText('queued.xlsx')).not.toBeInTheDocument();
  });

  it('keeps the loaded rows visible when a refetch errors (E2 transient error contract)', async () => {
    authenticateAs(['headless:read']);
    let jobsCall = 0;
    headlessClient.routes({
      [STATUS_PATH]: { get: () => headlessOk('get', STATUS_PATH, statusSnapshot()) },
      [JOBS_PATH]: {
        get: () => {
          jobsCall += 1;
          if (jobsCall === 1) {
            return headlessOk('get', JOBS_PATH, [
              job({ job_uuid: 'job-1', status: 'queued', excel_path: 'alpha.xlsx' }),
            ]);
          }
          // The refetch fails — React Query flips isError true but RETAINS the
          // first page's data. The route must keep the table, not swap to a hard
          // error.
          return headlessProblem(
            'get',
            JOBS_PATH,
            503,
            problemDetails(503, 'UPSTREAM_UNAVAILABLE', { detail: 'boom' }),
          );
        },
      },
    });

    const queryClient = renderJobs();
    await waitFor(() => expect(screen.getAllByTestId('job-row')).toHaveLength(1));

    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() });
    });

    await waitFor(() => expect(screen.getByTestId('job-list-stale')).toBeInTheDocument());
    // Rows retained — NOT replaced by the hard ErrorState.
    expect(screen.getAllByTestId('job-row')).toHaveLength(1);
    expect(screen.queryByTestId('job-list-error')).not.toBeInTheDocument();
  });

  it('keeps rows and announces refreshing during an in-flight background refetch (E2 stale-while-revalidate)', async () => {
    authenticateAs(['headless:read']);
    let jobsCall = 0;
    headlessClient.routes({
      [STATUS_PATH]: { get: () => headlessOk('get', STATUS_PATH, statusSnapshot()) },
      [JOBS_PATH]: {
        get: () => {
          jobsCall += 1;
          if (jobsCall === 1) {
            return headlessOk('get', JOBS_PATH, [
              job({ job_uuid: 'job-1', status: 'queued', excel_path: 'alpha.xlsx' }),
            ]);
          }
          // The refetch never resolves → isFetching stays true with data retained.
          return new Promise<never>(() => undefined);
        },
      },
    });

    const queryClient = renderJobs();
    await waitFor(() => expect(screen.getAllByTestId('job-row')).toHaveLength(1));

    act(() => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() });
    });

    await waitFor(() => expect(screen.getByTestId('job-list-refreshing')).toBeInTheDocument());
    // Loaded rows stay visible during the refetch (no flicker to skeleton).
    expect(screen.getAllByTestId('job-row')).toHaveLength(1);
    expect(screen.queryByTestId('data-table-skeleton')).not.toBeInTheDocument();
  });

  /**
   * M5 (D5) — neither jobs query declared a cadence tier, so both inherited the
   * global NORMAL bundle (`refetchInterval: false`, `refetchOnWindowFocus:
   * false`). The mount snapshot was therefore the LAST word: the queue could
   * drain completely and the screen kept showing the jobs it had at page load,
   * with no button, no poll, and no focus re-read to correct it. The existing
   * `common.refreshing` indicator was wired but unreachable — nothing could
   * trigger the refetch it announces.
   */
  it('gives the operator an explicit way to re-read the queue (S8)', async () => {
    authenticateAs(['headless:read']);
    mockHeadless(statusSnapshot(), [
      job({ job_uuid: 'job-1', status: 'queued', excel_path: 'a.xlsx' }),
    ]);

    renderJobs();
    await waitFor(() => expect(screen.getAllByTestId('job-row')).toHaveLength(1));
    const readsAtMount = headlessClient.GET.mock.calls.length;

    // The queue drains while the operator is looking at it.
    mockHeadless(statusSnapshot({ queued: 0 }), []);
    await userEvent.click(screen.getByTestId('jobs-refresh'));

    // BOTH reads re-run: the counts and the list are two views of one queue, so
    // refreshing only one would leave the screen internally inconsistent.
    await waitFor(() =>
      expect(headlessClient.GET.mock.calls.length).toBeGreaterThanOrEqual(readsAtMount + 2),
    );
    await waitFor(() => expect(screen.queryAllByTestId('job-row')).toHaveLength(0));
    expect(screen.getByTestId('job-count-queued')).toHaveTextContent('0');
  });

  it('announces the in-flight re-read through the existing refreshing idiom', async () => {
    authenticateAs(['headless:read']);
    let jobsCall = 0;
    headlessClient.routes({
      [STATUS_PATH]: { get: () => headlessOk('get', STATUS_PATH, statusSnapshot()) },
      [JOBS_PATH]: {
        get: () => {
          jobsCall += 1;
          if (jobsCall === 1) {
            return headlessOk('get', JOBS_PATH, [job({ job_uuid: 'job-1', status: 'queued' })]);
          }
          // The manual re-read never resolves → isFetching stays true.
          return new Promise<never>(() => undefined);
        },
      },
    });

    renderJobs();
    await waitFor(() => expect(screen.getAllByTestId('job-row')).toHaveLength(1));
    await userEvent.click(screen.getByTestId('jobs-refresh'));

    await waitFor(() => expect(screen.getByTestId('job-list-refreshing')).toBeInTheDocument());
    // Rows stay put — the manual refresh reuses the stale-while-revalidate
    // region, it does not blank the queue the operator is reading.
    expect(screen.getAllByTestId('job-row')).toHaveLength(1);
    expect(screen.queryByTestId('data-table-skeleton')).not.toBeInTheDocument();
  });

  it('re-reads the queue when the operator returns to the tab', async () => {
    // The other half of the freshness policy: no polling (a job queue does not
    // need watching between glances), but coming BACK to the screen must not
    // show the snapshot from an hour ago.
    //
    // The client below mirrors the APP's global defaults (`REFETCH_STRATEGIES.
    // NORMAL` — focus refetch OFF), not TanStack's library defaults (focus
    // refetch ON). With the library default this case would pass without the
    // route declaring anything, i.e. it would be a vacuous green: the very
    // situation that let `/jobs` inherit NORMAL unnoticed in the first place.
    authenticateAs(['headless:read']);
    mockHeadless(statusSnapshot(), [job({ job_uuid: 'job-1', status: 'queued' })]);

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          retry: false,
          staleTime: REFETCH_STRATEGIES.NORMAL.staleTime,
          refetchInterval: REFETCH_STRATEGIES.NORMAL.refetchInterval,
          refetchOnWindowFocus: REFETCH_STRATEGIES.NORMAL.refetchOnWindowFocus,
        },
      },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/jobs']}>
          <JobsRoute />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getAllByTestId('job-row')).toHaveLength(1));
    const readsAtMount = headlessClient.GET.mock.calls.length;

    // Focus refetch fires for STALE data only, so the clock has to move past the
    // tier's stale window — a return to the tab one second after leaving it is
    // correctly a no-op, and asserting otherwise would test the wrong thing.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.setSystemTime(Date.now() + REFETCH_STRATEGIES.IMPORTANT.staleTime + 1_000);
      act(() => {
        window.dispatchEvent(new Event('visibilitychange'));
      });
      await waitFor(() =>
        expect(headlessClient.GET.mock.calls.length).toBeGreaterThan(readsAtMount),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it('hides stop actions without headless:control and enables them with it', async () => {
    authenticateAs(['headless:read']);
    mockHeadless(statusSnapshot(), [job({ job_uuid: 'job-1', status: 'running' })]);

    renderJobs();

    await screen.findByTestId('jobs-table');
    expect(tableView('jobs-table').getByTestId('job-stop-forbidden')).toBeInTheDocument();
    expect(tableView('jobs-table').queryByTestId('job-stop')).not.toBeInTheDocument();
  });

  it('stops a running job through the typed client and invalidates both reads', async () => {
    authenticateAs(['headless:read', 'headless:control']);
    mockHeadless(statusSnapshot(), [job({ job_uuid: 'j-7', status: 'running' })]);
    headlessClient.routes({
      [STOP_PATH]: {
        post: () => headlessOk('post', STOP_PATH, { job_uuid: 'j-7', stop_requested: true }),
      },
    });

    const queryClient = renderJobs();
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');

    await screen.findByTestId('jobs-table');
    await userEvent.click(tableView('jobs-table').getByTestId('job-stop'));

    await waitFor(() => {
      expect(headlessClient.POST).toHaveBeenCalledWith('/headless/jobs/{job_uuid}/stop', {
        params: { path: { job_uuid: 'j-7' } },
        body: { message: '' },
      });
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['headless-jobs', 'list'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['headless-jobs', 'status'] });
  });
});
