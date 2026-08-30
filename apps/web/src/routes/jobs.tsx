import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { fetchHeadlessJobs, fetchHeadlessStatus, stopHeadlessJob } from '@/api/headless-client';
import { PERMISSION_HEADLESS_CONTROL, PERMISSION_HEADLESS_READ } from '@/api/permissions';
import { queryKeys, REFETCH_STRATEGIES } from '@/api/query-config';
import { categorizeListQuery } from '@/api/query-status';
import { RequirePermission, useAuthSession } from '@/auth/route-guard';
import { useT } from '@/i18n';
import { type ApiError } from '@/shared/api-error';
import { ROUTE_PATHS } from '@/shared/route-links';
import {
  BlockSkeleton,
  Button,
  DataTable,
  DataTableSkeleton,
  describeApiError,
  EmptyState,
  ErrorState,
  jobStatusToStatusKind,
  MetricStrip,
  PageHeader,
  queueStatusLabelToken,
  RefetchRegion,
  SectionBand,
  StatusBadge,
  StatusMessage,
  Toolbar,
  type DataTableColumn,
  type MetricStripItem,
  type Translate,
} from '@/ui';

/**
 * Measurement jobs console — Headless API (FE-P7, 2026-06-14).
 *
 * operator 가 백엔드 측정 job 큐를 조회하고(상태 카운트 + 전체 job 목록) 진행
 * 중인 job 을 원격 중지하는 화면이다. 백엔드는 (1) `GET /headless/status` 의
 * `measurement_jobs.counts` 집계와 (2) `GET /headless/jobs` 전체 snapshot 목록을
 * 제공한다 — list 엔드포인트에는 status/limit query param 이 없으므로(생성 타입상
 * `query?: never`) 상태 필터는 클라이언트측에서 수행한다(존재하지 않는 API
 * 파라미터를 만들지 않는다). 중지는 `POST /headless/jobs/{job_id}/stop`.
 *
 * RBAC: 조회는 `headless:read`, 중지(write)는 `headless:control` 로 각각
 * 게이트한다(백엔드 `HEADLESS_API_PERMISSIONS` SSOT 미러 — 프론트 enum 박기
 * 금지). 서버는 배포 계층 trusted-header 로 재차 강제한다.
 */

/** Central headless-context error copy (6-arm taxonomy SSOT in `@/ui/errors`). */
function describeError(error: unknown, t: (key: string) => string): string {
  return describeApiError(error, 'headless', {
    forbidden: t('routes.jobs.error.forbidden'),
    notFound: t('routes.jobs.error.notFound'),
    network: t('routes.jobs.error.network'),
    default: t('routes.jobs.error.default'),
  });
}

/** Last path segment of an excel_path for compact display (full path kept as the
 *  cell `title`). Handles both `\\` (Windows) and `/` separators. Exported for
 *  direct unit testing. */
export function fileName(path: string): string {
  const segments = path.split(/[\\/]/u);
  const last = segments[segments.length - 1];
  return last !== undefined && last !== '' ? last : path;
}

/** Render a string field, falling back to an em-dash when absent OR empty
 *  (`||` would read cleaner but the lint config bans it for the empty-string
 *  case `??` cannot express). */
function orDash(value: string | undefined): string {
  return value !== undefined && value !== '' ? value : '—';
}

/** Operator-meaningful "active" set — only queued/running jobs can be stopped.
 *  A single predicate (not a scattered literal) so the stop affordance and any
 *  future gating share one definition. Exported for direct unit testing. */
export function isStoppableStatus(status: string): boolean {
  const v = status.trim().toLowerCase();
  return v === 'queued' || v === 'running';
}

type JobSnapshot = Awaited<ReturnType<typeof fetchHeadlessJobs>>[number];

export function JobsRoute(): JSX.Element {
  const { t } = useT();
  return (
    <section className="jobs" aria-labelledby="jobs-heading">
      <PageHeader
        title={t('routes.jobs.page.title')}
        titleId="jobs-heading"
        description={t('routes.jobs.page.description')}
      />
      <RequirePermission permission={PERMISSION_HEADLESS_READ}>
        <JobsWorkbenchOverview />
        <JobsFreshnessToolbar />
        <div className="jobs-workbench" data-testid="jobs-workbench">
          <div className="jobs-workbench__main">
            <JobQueuePanel />
            <JobListPanel />
          </div>
          <aside
            className="jobs-workbench__rail"
            aria-labelledby="jobs-next-heading"
            data-testid="jobs-next-actions"
          >
            <JobsNextActions />
          </aside>
        </div>
      </RequirePermission>
    </section>
  );
}

/**
 * M5 (D5) — the operator's control over how fresh this screen is.
 *
 * The freshness policy for a job queue is deliberately "no polling + explicit
 * re-read + refetch on return", not a timer:
 *
 *   - A queue changes because a *worker* changed it, on human timescales
 *     (minutes), and nobody is asked to watch this screen continuously — unlike
 *     chamber availability, which is a supervision view on a wall monitor. So a
 *     background poll would spend requests to observe nothing, for every open
 *     tab, all shift.
 *   - The MONITORED cadence is DERIVED from the chamber heartbeat TTL. Borrowing
 *     that number here would be a coincidence dressed as a policy: the queue has
 *     no TTL, so nothing about it would make that interval right or wrong.
 *
 * That leaves the two moments freshness actually matters — when the operator
 * comes back to the tab (`IMPORTANT`'s focus refetch) and when they ask
 * (this button) — which is what the tier provides.
 */
function JobsFreshnessToolbar(): JSX.Element {
  const { t } = useT();
  const queryClient = useQueryClient();
  return (
    <Toolbar ariaLabel={t('routes.jobs.freshness.toolbarAria')} inline>
      <Button
        type="button"
        variant="secondary"
        data-testid="jobs-refresh"
        onClick={() => {
          // The counts strip and the table are two views of ONE queue, re-read
          // together through the same key factories the stop mutation uses — a
          // refresh that moved only one would leave the screen contradicting
          // itself.
          void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.status() });
          void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() });
        }}
      >
        {t('common.refresh')}
      </Button>
    </Toolbar>
  );
}

function JobsWorkbenchOverview(): JSX.Element {
  const { t } = useT();
  return (
    <nav
      className="jobs-workbench-overview"
      aria-label={t('routes.jobs.workbench.navAria')}
      data-testid="jobs-workbench-overview"
    >
      <a className="jobs-workbench-overview__item" href="#jobs-counts-heading">
        <span className="jobs-workbench-overview__label">
          {t('routes.jobs.workbench.stepQueue')}
        </span>
        <span className="jobs-workbench-overview__detail">
          {t('routes.jobs.workbench.stepQueueDetail')}
        </span>
      </a>
      <a className="jobs-workbench-overview__item" href="#jobs-list-heading">
        <span className="jobs-workbench-overview__label">
          {t('routes.jobs.workbench.stepList')}
        </span>
        <span className="jobs-workbench-overview__detail">
          {t('routes.jobs.workbench.stepListDetail')}
        </span>
      </a>
      <a className="jobs-workbench-overview__item" href="#jobs-next-heading">
        <span className="jobs-workbench-overview__label">
          {t('routes.jobs.workbench.stepNext')}
        </span>
        <span className="jobs-workbench-overview__detail">
          {t('routes.jobs.workbench.stepNextDetail')}
        </span>
      </a>
    </nav>
  );
}

function JobQueuePanel(): JSX.Element {
  const { t } = useT();
  const status = useQuery({
    queryKey: queryKeys.jobs.status(),
    queryFn: fetchHeadlessStatus,
    // M5 — named tier, not an inherited default. Both jobs queries used to
    // declare nothing and silently take the global NORMAL bundle, whose
    // `refetchOnWindowFocus: false` is what froze this screen at mount.
    ...REFETCH_STRATEGIES.IMPORTANT,
  });

  const counts = status.data?.measurement_jobs.counts;
  const items: readonly MetricStripItem[] = counts
    ? JOB_COUNT_KEYS.map((key) => ({
        key,
        label: t(`routes.jobs.counts.${key}`),
        value: counts[key],
        valueTestId: `job-count-${key}`,
      }))
    : [];

  return (
    <section className="jobs-workbench-panel" aria-labelledby="jobs-counts-heading">
      <SectionBand title={t('routes.jobs.counts.bandTitle')} titleId="jobs-counts-heading" />
      {status.isLoading && <BlockSkeleton variant="metric" lines={JOB_COUNT_KEYS.length} />}
      {status.isError && (
        <ErrorState testId="job-status-error" message={describeError(status.error, t)} />
      )}
      {status.isSuccess && counts && (
        <div data-testid="job-counts">
          <MetricStrip ariaLabel={t('routes.jobs.counts.metricStripAria')} items={items} />
        </div>
      )}
    </section>
  );
}

function JobListPanel(): JSX.Element {
  const { t } = useT();
  const [statusFilter, setStatusFilter] = useState('');
  // Write affordance gate (reactive): only render the stop control for a
  // principal carrying `headless:control`. Mirrors membership/projects' pattern
  // (`useAuthSession` + `permissions.includes`) — a quiet capability check, not
  // a per-row permission-denied view. The server re-enforces via trusted-header.
  const auth = useAuthSession();
  const canControl =
    auth.kind === 'authenticated' &&
    auth.principal.permissions.includes(PERMISSION_HEADLESS_CONTROL);

  const jobs = useQuery({
    queryKey: queryKeys.jobs.list(),
    queryFn: fetchHeadlessJobs,
    ...REFETCH_STRATEGIES.IMPORTANT,
  });

  const rows = useMemo(() => jobs.data ?? [], [jobs.data]);
  // Distinct statuses present in the loaded list drive the filter options — the
  // list endpoint has no status query param, so filtering is client-side.
  const statuses = useMemo(() => [...new Set(rows.map((r) => r.status))].sort(), [rows]);
  const filtered = useMemo(
    () => (statusFilter === '' ? rows : rows.filter((r) => r.status === statusFilter)),
    [rows, statusFilter],
  );

  // Single query-phase contract (E2/E4 SSOT). Branching on `view.kind` rather
  // than isLoading/isError/isSuccess keeps the loaded rows visible when a
  // background refetch errors (transientError) instead of replacing the table
  // with a hard error — the regression `categorizeListQuery` prevents.
  const view = categorizeListQuery<JobSnapshot>({
    items: rows,
    hasLoaded: jobs.data !== undefined,
    isError: jobs.isError,
    error: jobs.error,
    isFetching: jobs.isFetching,
  });
  const showRefreshing =
    view.kind === 'ready' && view.isRefetching && view.transientError === null && !view.isEmpty;

  return (
    <section className="jobs-workbench-panel" aria-labelledby="jobs-list-heading">
      <SectionBand title={t('routes.jobs.list.bandTitle')} titleId="jobs-list-heading" />

      {/* Initial load → table skeleton whose column count is DERIVED from the
          same descriptor the table renders, so the reserved grid can never
          drift from the arriving one (§M8.2). */}
      {view.kind === 'loading' && (
        <DataTableSkeleton columns={jobColumns(t, canControl).length} rows={6} />
      )}
      {view.kind === 'hardError' && (
        <ErrorState testId="job-list-error" message={describeError(view.error, t)} />
      )}

      {view.kind === 'ready' && (
        <>
          {/* Refetch failed but loaded rows are retained (E2 transient error). */}
          {view.transientError !== null && (
            <StatusMessage tone="info" testId="job-list-stale" message={t('common.staleData')} />
          )}
          {showRefreshing && (
            <StatusMessage
              tone="info"
              testId="job-list-refreshing"
              message={t('common.refreshing')}
            />
          )}

          {statuses.length > 0 && (
            <label className="jobs-filter" htmlFor="job-status-filter">
              {t('routes.jobs.list.filterLabel')}{' '}
              <select
                id="job-status-filter"
                data-testid="job-status-filter"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                <option value="">{t('routes.jobs.list.filterAll')}</option>
                {statuses.map((s) => (
                  <option key={s} value={s}>
                    {t(`routes.jobs.counts.${queueStatusLabelToken(s)}`)}
                  </option>
                ))}
              </select>
            </label>
          )}

          {filtered.length === 0 ? (
            <EmptyState
              title={t('routes.jobs.list.emptyStateTitle')}
              description={t('routes.jobs.list.emptyStateDescription')}
            />
          ) : (
            // §M8.7 middle state: a background refresh dims the rows in place
            // instead of swapping them for the skeleton. Blanking a queue the
            // operator is reading to re-show the same rows a moment later is
            // the orientation loss this separation exists to prevent.
            <RefetchRegion refetching={view.isRefetching} testId="jobs-refetch-region">
              <DataTable<JobSnapshot>
                testId="jobs-table"
                caption={t('routes.jobs.list.caption')}
                columns={jobColumns(t, canControl)}
                rows={filtered}
                rowKey={(job) => String(job.id)}
                rowTestId="job-row"
              />
            </RefetchRegion>
          )}
        </>
      )}
    </section>
  );
}

function JobsNextActions(): JSX.Element {
  const { t } = useT();
  return (
    <section className="jobs-next">
      <SectionBand title={t('routes.jobs.next.bandTitle')} titleId="jobs-next-heading" />
      <p className="jobs-next__state" data-testid="jobs-next-state">
        {t('routes.jobs.next.description')}
      </p>
      <div className="jobs-next__actions">
        <Link
          className="jobs-next__action"
          to={ROUTE_PATHS.chambers}
          data-testid="jobs-next-chambers"
        >
          {t('routes.jobs.next.chambers')}
        </Link>
        <Link
          className="jobs-next__action"
          to={ROUTE_PATHS.sessions}
          data-testid="jobs-next-sessions"
        >
          {t('routes.jobs.next.sessions')}
        </Link>
        <Link
          className="jobs-next__action"
          to={ROUTE_PATHS.reports}
          data-testid="jobs-next-reports"
        >
          {t('routes.jobs.next.reports')}
        </Link>
      </div>
      <p className="section-hint">{t('routes.jobs.next.hint')}</p>
    </section>
  );
}

/**
 * Column descriptor for the jobs table (§M7.2).
 *
 * Priorities encode what an operator scanning a phone actually needs: the job
 * id and its status identify the row, the excel path and the stop control are
 * the working columns, and the requester/worker attribution is provenance —
 * it folds into the per-row overflow line on a compact viewport and into the
 * card body on a phone rather than disappearing.
 */
/** Queue-count tiles, declared once. The metric strip and the loading
 *  skeleton both read this list, so the skeleton reserves exactly as many rows
 *  as arrive (§M8.2 — no hardcoded placeholder count). */
const JOB_COUNT_KEYS = ['queued', 'running', 'completed', 'failed', 'cancelled'] as const;

function jobColumns(t: Translate, canControl: boolean): readonly DataTableColumn<JobSnapshot>[] {
  return [
    {
      key: 'id',
      header: t('routes.jobs.list.colId'),
      priority: 'primary',
      className: 'data-cell-numeric',
      testId: 'job-id',
      cell: (job) => job.id,
    },
    {
      key: 'status',
      header: t('routes.jobs.list.colStatus'),
      priority: 'primary',
      testId: 'job-status',
      cell: (job) => {
        const statusMessage = job.status_message ?? '';
        return (
          <StatusBadge
            status={jobStatusToStatusKind(job.status)}
            label={t(`routes.jobs.counts.${queueStatusLabelToken(job.status)}`)}
            {...(statusMessage !== '' ? { title: statusMessage } : {})}
          />
        );
      },
    },
    {
      key: 'excel',
      header: t('routes.jobs.list.colExcel'),
      priority: 'secondary',
      cell: (job) => <span title={job.excel_path}>{fileName(job.excel_path)}</span>,
    },
    {
      key: 'requestedBy',
      header: t('routes.jobs.list.colRequestedBy'),
      priority: 'detail',
      cell: (job) => orDash(job.requested_by),
    },
    {
      key: 'worker',
      header: t('routes.jobs.list.colWorker'),
      priority: 'detail',
      cell: (job) => orDash(job.assigned_worker_id),
    },
    {
      key: 'action',
      header: t('routes.jobs.list.colAction'),
      priority: 'secondary',
      cell: (job) => <JobStopCell job={job} canControl={canControl} />,
    },
  ];
}

function JobStopCell({
  job,
  canControl,
}: {
  readonly job: JobSnapshot;
  readonly canControl: boolean;
}): JSX.Element {
  const { t } = useT();
  if (!isStoppableStatus(job.status)) return <span>—</span>;
  if (job.stop_requested === true) {
    return (
      <StatusBadge
        status="stale"
        label={t('routes.jobs.list.stopRequested')}
        testId="job-stop-requested"
      />
    );
  }
  if (!canControl) return <span data-testid="job-stop-forbidden">—</span>;
  return <JobStopButton jobId={job.id} />;
}

function JobStopButton({ jobId }: { readonly jobId: number }): JSX.Element {
  const { t } = useT();
  const queryClient = useQueryClient();
  const stop = useMutation<unknown, ApiError, number>({
    mutationFn: async (id: number) => {
      return stopHeadlessJob(id);
    },
    onSuccess: () => {
      // A stopped job shifts both the aggregate counts and its own row state —
      // invalidate both via the same query-key factories (no key drift).
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.status() });
    },
  });

  return (
    <>
      <Button
        type="button"
        variant="danger"
        data-testid="job-stop"
        disabled={stop.isPending}
        onClick={() => stop.mutate(jobId)}
      >
        {stop.isPending ? t('routes.jobs.list.stopPending') : t('routes.jobs.list.stopButton')}
      </Button>
      {stop.isError && (
        <ErrorState testId="job-stop-error" message={describeError(stop.error, t)} />
      )}
    </>
  );
}

export default JobsRoute;
