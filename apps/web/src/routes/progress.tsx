import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';

import { fetchProjectProgress } from '@/api/platform-client';
import { queryKeys, REFETCH_STRATEGIES } from '@/api/query-config';
import { useT } from '@/i18n';
import { formatPercent } from '@/shared/percent-display';
import { isValidProjectId } from '@/shared/project-id';
import {
  formatBucketLabel,
  formatHours,
  groupByArea,
  overallTotals,
  percentTone,
} from '@/shared/project-progress';
import { projectScopedHref, ROUTE_PATHS } from '@/shared/route-links';
import {
  BlockSkeleton,
  describeApiError,
  EmptyState,
  ErrorState,
  MetricStrip,
  PageHeader,
  ProgressBar,
  SectionBand,
  StatusBadge,
} from '@/ui';

/**
 * 진행률 — 시간가중 시험 진행률 대시보드 (Phase 6, 2026-06-23).
 *
 * 비전 진입 워크플로우의 "진척 대시보드": 프로젝트 선택 → 분야 → **진행률**. 한 프로젝트의
 * 시간가중 진행률을 분야(workbench area) × 진행 버킷(BT/BLE/DTS/UNII…)으로 보여준다.
 *
 * SSOT: 데이터 = 중앙 `GET /platform/projects/{id}/progress`(platform:read, typed
 * generated client). 시간가중 정직 규칙은 백엔드 `ProgressBucketRollup` 도메인 DTO 가
 * 결정 — percent 가 null 이면 "시간 미설정"(가짜 0% 금지), unpriced/unbucketable 는
 * 분(分)이 아니라 condition 수로 노출. 프론트는 그 계약을 그대로 렌더한다(재계산 0).
 *
 * 인가: platform:read. RequireAuth 하위 + 백엔드 403 → ErrorState 가 처리.
 * 화면 문구는 전부 i18n, 분야 라벨은 `routes.fields.areas.*` SSOT 재사용.
 */

function ProgressRoute(): JSX.Element {
  const { t } = useT();
  const [searchParams] = useSearchParams();
  const projectId = (searchParams.get('project') ?? '').trim();
  const hasProject = isValidProjectId(projectId);

  const progress = useQuery({
    queryKey: queryKeys.project.progress(projectId),
    queryFn: () => fetchProjectProgress(projectId),
    enabled: hasProject,
    ...REFETCH_STRATEGIES.NORMAL,
  });

  if (!hasProject) {
    return (
      <div className="route route-progress">
        <PageHeader
          title={t('routes.progress.title')}
          description={t('routes.progress.description')}
        />
        <EmptyState
          testId="progress-no-project"
          title={t('routes.progress.noProjectTitle')}
          description={t('routes.progress.noProjectBody')}
          action={
            <Link to="/my-projects" data-testid="progress-pick-project">
              {t('routes.progress.pickProject')}
            </Link>
          }
        />
      </div>
    );
  }

  // `?area=` deep-link (from the fields entry cards): scope the metric strip AND
  // the area list to the selected workbench area so the badge that linked here
  // and this view agree. Blank area = whole project (byte-identical to the
  // pre-wiring behaviour). The token matches `ProgressBucketEnvelope.progress_area`
  // (== `WorkbenchAreaId`); an unknown area yields no rows → the empty state.
  const areaFilter = (searchParams.get('area') ?? '').trim();
  const allRollups = progress.data ?? [];
  const rollups =
    areaFilter === '' ? allRollups : allRollups.filter((r) => r.progress_area === areaFilter);
  const totals = overallTotals(rollups);
  const groups = groupByArea(rollups);
  const unbucketedLabel = t('routes.progress.unbucketed');
  const areaFilterLabel = areaFilter === '' ? '' : t(`routes.fields.areas.${areaFilter}.label`);

  const metricItems = [
    {
      key: 'percent',
      label: t('routes.progress.metricOverall'),
      value: totals.percent === null ? t('routes.progress.noTime') : formatPercent(totals.percent),
      valueTestId: 'progress-metric-percent',
    },
    {
      key: 'completed',
      label: t('routes.progress.metricCompleted'),
      value: `${formatHours(totals.completedMinutes)}h`,
    },
    {
      key: 'planned',
      label: t('routes.progress.metricPlanned'),
      value: `${formatHours(totals.plannedMinutes)}h`,
    },
    {
      key: 'unpriced',
      label: t('routes.progress.metricUnpriced'),
      value: String(totals.unpricedConditions),
      valueTestId: 'progress-metric-unpriced',
    },
  ];

  return (
    <div className="route route-progress" data-testid="progress-route">
      <PageHeader
        title={t('routes.progress.title')}
        description={t('routes.progress.description')}
      />

      <ProgressWorkbenchOverview />

      {areaFilter !== '' && (
        <p
          className="progress-area-filter section-hint"
          role="note"
          data-testid="progress-area-filter"
          data-area={areaFilter}
        >
          {t('routes.progress.areaFilterActive', { area: areaFilterLabel })}
          {' · '}
          <Link
            to={projectScopedHref(ROUTE_PATHS.progress, projectId)}
            data-testid="progress-area-filter-clear"
          >
            {t('routes.progress.areaFilterClear')}
          </Link>
        </p>
      )}

      {progress.isError ? (
        <ErrorState
          testId="progress-error"
          message={describeApiError(progress.error, 'platform')}
        />
      ) : progress.isPending ? (
        // Was an EmptyState — which announces "there is nothing here" for a
        // request that has not answered yet. Loading and empty are different
        // facts and must not share a surface (§M8.1).
        <BlockSkeleton lines={4} label={t('routes.progress.loading')} testId="progress-loading" />
      ) : rollups.length === 0 ? (
        <EmptyState
          testId="progress-empty"
          title={t('routes.progress.emptyTitle')}
          description={t('routes.progress.emptyBody')}
        />
      ) : (
        <div className="progress-workbench" data-testid="progress-workbench">
          <div className="progress-workbench__main">
            <section
              className="progress-workbench-panel"
              aria-labelledby="progress-summary-heading"
            >
              <SectionBand
                title={t('routes.progress.summarySection')}
                titleId="progress-summary-heading"
              />
              <MetricStrip items={metricItems} ariaLabel={t('routes.progress.metricStripAria')} />
            </section>

            {totals.unpricedConditions > 0 || totals.unbucketableConditions > 0 ? (
              <p className="progress-quality-warning" data-testid="progress-quality-warning">
                {t('routes.progress.qualityWarning')}
              </p>
            ) : null}

            <div
              className="progress-area-list"
              id="progress-area-list-heading"
              aria-label={t('routes.progress.stepAreas')}
            >
              {groups.map((group) => (
                <section
                  key={group.area}
                  className="progress-area"
                  data-testid="progress-area"
                  data-area={group.area}
                  aria-labelledby={`progress-area-${group.area}`}
                >
                  <SectionBand
                    title={t(`routes.fields.areas.${group.area}.label`)}
                    meta={t('routes.progress.bucketCount', { count: group.buckets.length })}
                    titleId={`progress-area-${group.area}`}
                  />
                  <ul className="progress-bucket-list">
                    {group.buckets.map((b) => {
                      const label = formatBucketLabel(
                        b.progress_bucket_id ?? null,
                        unbucketedLabel,
                      );
                      const hours = `${formatHours(b.completed_minutes)}h / ${formatHours(b.planned_minutes)}h`;
                      const pct: number | null = b.percent ?? null;
                      return (
                        <li
                          key={`${group.area}:${b.progress_bucket_id ?? '_none'}`}
                          className="progress-bucket"
                          data-testid="progress-bucket"
                          data-bucket={b.progress_bucket_id ?? ''}
                        >
                          <div className="progress-bucket__head">
                            <span className="progress-bucket__label">{label}</span>
                            {pct === null ? (
                              <StatusBadge status="stale" label={t('routes.progress.noTime')} />
                            ) : (
                              <span className="progress-bucket__hours">{hours}</span>
                            )}
                          </div>
                          {pct === null ? null : (
                            <ProgressBar
                              label={t('routes.progress.bucketProgressAria', { bucket: label })}
                              valueNow={pct}
                              valueText={formatPercent(pct)}
                              tone={percentTone(pct)}
                            />
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </section>
              ))}
            </div>
          </div>

          <aside
            className="progress-workbench__rail"
            aria-labelledby="progress-next-heading"
            data-testid="progress-next-actions"
          >
            <ProgressNextActions projectId={projectId} />
          </aside>
        </div>
      )}
    </div>
  );
}

function ProgressWorkbenchOverview(): JSX.Element {
  const { t } = useT();
  return (
    <nav
      className="progress-workbench-overview"
      aria-label={t('routes.progress.workbenchNavAria')}
      data-testid="progress-workbench-overview"
    >
      <a className="progress-workbench-overview__item" href="#progress-summary-heading">
        <span className="progress-workbench-overview__label">
          {t('routes.progress.stepSummary')}
        </span>
        <span className="progress-workbench-overview__detail">
          {t('routes.progress.stepSummaryDetail')}
        </span>
      </a>
      <a className="progress-workbench-overview__item" href="#progress-area-list-heading">
        <span className="progress-workbench-overview__label">{t('routes.progress.stepAreas')}</span>
        <span className="progress-workbench-overview__detail">
          {t('routes.progress.stepAreasDetail')}
        </span>
      </a>
      <a className="progress-workbench-overview__item" href="#progress-next-heading">
        <span className="progress-workbench-overview__label">{t('routes.progress.stepNext')}</span>
        <span className="progress-workbench-overview__detail">
          {t('routes.progress.stepNextDetail')}
        </span>
      </a>
    </nav>
  );
}

function ProgressNextActions({ projectId }: { readonly projectId: string }): JSX.Element {
  const { t } = useT();
  return (
    <section className="progress-next">
      <SectionBand title={t('routes.progress.nextSection')} titleId="progress-next-heading" />
      <dl className="progress-next__context">
        <dt>{t('routes.progress.projectLabel')}</dt>
        <dd data-testid="progress-project-context">{projectId}</dd>
      </dl>
      <div className="progress-next__actions">
        <Link
          to={projectScopedHref(ROUTE_PATHS.projects, projectId)}
          className="progress-next__action"
          data-testid="progress-next-coverage"
        >
          {t('routes.progress.nextCoverage')}
        </Link>
        <Link
          to={projectScopedHref(ROUTE_PATHS.testPlans, projectId)}
          className="progress-next__action"
          data-testid="progress-next-test-plans"
        >
          {t('routes.progress.nextTestPlans')}
        </Link>
        <Link
          to={projectScopedHref(ROUTE_PATHS.chambers, projectId)}
          className="progress-next__action"
          data-testid="progress-next-chambers"
        >
          {t('routes.progress.nextChambers')}
        </Link>
      </div>
      <p className="section-hint">{t('routes.progress.nextHint')}</p>
    </section>
  );
}

export default ProgressRoute;
export { ProgressRoute };
