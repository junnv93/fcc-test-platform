import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { NavLink } from 'react-router-dom';

import { fetchChambers } from '@/api/platform-client';
import { queryKeys } from '@/api/query-config';
import { getRuntimeConfig } from '@/config/runtime';
import { useT } from '@/i18n';
import { ROUTE_PATHS } from '@/shared/route-links';
import {
  BlockSkeleton,
  Card,
  chamberStatusKind,
  describeApiError,
  EmptyState,
  ErrorState,
  PageHeader,
  SectionBand,
  StatusBadge,
  WorkbenchLayout,
} from '@/ui';

/**
 * Operator home ('/') — "오늘 할 일" (tester-ux redesign Phase H, §3).
 *
 * Replaces the former diagnostics landing (moved to [설정]>진단 in Phase S-diag)
 * with a task-oriented entry point. Two sections, both G1/G2 only — no proxy
 * completion:
 *   - 진행 중인 측정 (G2): the in_use chambers from the EXISTING
 *     `chambers.list()` query (no new contract, no localStorage). Labelled
 *     "진행 중인 측정" — NOT "내 측정", because per-user session attribution is a
 *     G3 read contract we do not have (§3 데이터 등급표).
 *   - 처음이신가요? (G1): the six-step task guide — plain nav links to the
 *     existing routes in the tester's work order.
 *
 * Deliberately NOT shown (G3 — require backend read contracts, §3/§B): 최근 작업/
 * 이어가기, 내 실패 N건, 보고서 준비됨. They appear only after a contract phase,
 * never faked from partial aggregates.
 */
export function OverviewRoute(): JSX.Element {
  const { t } = useT();
  // /control is hidden on the central hub (sessionApiEnabled:false); the
  // measurement-start step then points at the chambers surface instead.
  const sessionApiEnabled = getRuntimeConfig().sessionApiEnabled;

  const chambers = useQuery({
    queryKey: queryKeys.chambers.list(),
    queryFn: fetchChambers,
  });
  const inUse = useMemo(
    () =>
      (chambers.data?.items ?? []).filter(
        (chamber) => chamber.status.trim().toLowerCase() === 'in_use',
      ),
    [chambers.data],
  );

  const steps = [
    { key: 'testPlans', to: ROUTE_PATHS.testPlans, labelKey: 'routes.home.stepTestPlans' },
    { key: 'chambers', to: ROUTE_PATHS.chambers, labelKey: 'routes.home.stepChambers' },
    {
      key: 'measure',
      to: sessionApiEnabled ? '/control' : ROUTE_PATHS.chambers,
      labelKey: 'routes.home.stepMeasure',
    },
    { key: 'progress', to: ROUTE_PATHS.progress, labelKey: 'routes.home.stepProgress' },
    { key: 'results', to: ROUTE_PATHS.sessions, labelKey: 'routes.home.stepResults' },
    { key: 'reports', to: ROUTE_PATHS.reports, labelKey: 'routes.home.stepReports' },
  ] as const;

  return (
    <section className="home" aria-labelledby="home-heading">
      <PageHeader
        title={t('routes.home.title')}
        titleId="home-heading"
        description={t('routes.home.description')}
      />

      <WorkbenchLayout
        className="home-workbench"
        mainLabel={t('routes.home.ctaHeading')}
        railLabel={t('routes.home.onboardingHeading')}
        testId="home-workbench"
        main={
          <div className="home-workbench__main">
            <Card as="section" className="home-workbench-panel" aria-labelledby="home-cta-heading">
              <SectionBand title={t('routes.home.ctaHeading')} titleId="home-cta-heading" />
              {/* First-action CTA (R3): G1/G2 only — plain nav links, no localStorage
                recent-list and no per-user aggregate. Mirrors the desktop GUI's
                "Open/start" entry without inventing a G3 read contract. */}
              <ul
                className="home-steps home-steps--primary"
                aria-label={t('routes.home.ctaAria')}
                data-testid="home-cta"
              >
                <li>
                  <NavLink
                    to={ROUTE_PATHS.testPlans}
                    className="home-step"
                    data-testid="home-cta-test-plans"
                  >
                    {t('routes.home.ctaTestPlans')}
                  </NavLink>
                </li>
                <li>
                  <NavLink
                    to={ROUTE_PATHS.chambers}
                    className="home-step"
                    data-testid="home-cta-chambers"
                  >
                    {t('routes.home.ctaChambers')}
                  </NavLink>
                </li>
                <li>
                  <NavLink
                    to={ROUTE_PATHS.progress}
                    className="home-step"
                    data-testid="home-cta-progress"
                  >
                    {t('routes.home.ctaProgress')}
                  </NavLink>
                </li>
              </ul>
            </Card>

            <section className="home-workbench-panel" aria-labelledby="home-today-heading">
              <SectionBand title={t('routes.home.todayHeading')} titleId="home-today-heading" />
              <h3 className="home-subheading">{t('routes.home.inProgressHeading')}</h3>

              {chambers.isPending && <BlockSkeleton lines={3} testId="home-progress-loading" />}

              {chambers.isError && (
                <ErrorState
                  testId="home-progress-error"
                  message={describeApiError(chambers.error, 'platform')}
                />
              )}

              {chambers.isSuccess && inUse.length === 0 && (
                <EmptyState
                  testId="home-inprogress-empty"
                  title={t('routes.home.inProgressEmpty')}
                  description={t('routes.home.inProgressEmptyHint')}
                />
              )}

              {chambers.isSuccess && inUse.length > 0 && (
                <ul
                  className="home-inprogress"
                  aria-label={t('routes.home.inProgressAria')}
                  data-testid="home-inprogress"
                >
                  {inUse.map((chamber) => (
                    <li key={chamber.chamber_id} className="home-inprogress__item">
                      <span className="home-inprogress__name">
                        {chamber.name || chamber.chamber_id}
                      </span>
                      <StatusBadge
                        status={chamberStatusKind(chamber.status)}
                        label={t('routes.chambers.status.in_use')}
                        testId="home-inprogress-status"
                      />
                      <NavLink
                        to={ROUTE_PATHS.chambers}
                        className="home-inprogress__link touch-target"
                      >
                        {t('routes.home.continueLink')}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        }
        rail={
          <div
            className="home-workbench__rail"
            aria-labelledby="home-onboarding-heading"
            data-testid="home-onboarding-panel"
          >
            <section className="home-workbench-panel">
              <SectionBand
                title={t('routes.home.onboardingHeading')}
                titleId="home-onboarding-heading"
              />
              <ul
                className="home-steps home-steps--guided"
                aria-label={t('routes.home.onboardingAria')}
                data-testid="home-steps"
              >
                {steps.map((step) => (
                  <li key={step.key}>
                    <NavLink
                      to={step.to}
                      className="home-step"
                      data-testid={`home-step-${step.key}`}
                    >
                      {t(step.labelKey)}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        }
      />
    </section>
  );
}

export default OverviewRoute;
