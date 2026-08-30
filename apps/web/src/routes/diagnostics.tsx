import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { queryKeys } from '@/api/query-config';
import { fetchSessionInfo } from '@/api/session-client';
import { getRuntimeConfig } from '@/config/runtime';
import { useT } from '@/i18n';
import { ROUTE_PATHS } from '@/shared/route-links';
import {
  BlockSkeleton,
  describeApiError,
  ErrorState,
  MetricStrip,
  PageHeader,
  SectionBand,
} from '@/ui';

import type { MetricStripItem } from '@/ui';

/**
 * Diagnostics route ([설정]>진단) — runtime + session-runner health panel.
 *
 * Tester-ux redesign Phase S-diag: the runtime/session diagnostic panel moved
 * OUT of the operator landing (overview '/') so Phase H can replace the landing
 * with the "오늘 할 일" home. The content is unchanged (SSOT chain:
 * runtime-config -> openapi-fetch typed client -> TanStack Query -> ui-kit
 * primitives); only its home moved. The operational tables live in their own
 * routes (sessions / control / reports); this is a single-glance health +
 * identity panel from the shared `@/ui` kit (no raw payload dumps).
 */
export function DiagnosticsRoute(): JSX.Element {
  const { t } = useT();
  const config = getRuntimeConfig();
  // The Session API is a single-chamber-node surface. In a topology that does
  // not serve it (the central hub — runtime-config `sessionApiEnabled: false`),
  // a `/session/info` call would hit the gateway and 404, so this panel MUST
  // NOT issue it. `enabled` gates the fetch (the hook is still called
  // unconditionally — Rules of Hooks) and the render branches below. Sealed
  // statically by test_central_docker_compose.py and behaviourally by
  // tests/diagnostics.test.tsx ("does not call the session API when disabled").
  const sessionApiEnabled = config.sessionApiEnabled;
  const info = useQuery({
    queryKey: queryKeys.session.info(),
    enabled: sessionApiEnabled,
    queryFn: fetchSessionInfo,
  });

  const errorDetails = info.isError ? describeStatus(info.error) : undefined;

  const environmentItems: readonly MetricStripItem[] = [
    {
      key: 'env',
      label: t('routes.diagnostics.metricEnv'),
      value: config.environmentName,
      valueTestId: 'env-name',
    },
    {
      key: 'build',
      label: t('routes.diagnostics.metricBuild'),
      value: `${config.buildVersion} (${config.buildSha256.slice(0, 12)})`,
      valueTestId: 'build-version',
    },
    {
      key: 'backend',
      label: t('routes.diagnostics.metricBackend'),
      value: config.apiBaseUrl,
      valueTestId: 'api-base-url',
    },
  ];

  return (
    <section className="diagnostics" aria-labelledby="diagnostics-heading">
      <PageHeader
        title={t('routes.diagnostics.title')}
        titleId="diagnostics-heading"
        description={t('routes.diagnostics.description')}
      />

      <DiagnosticsWorkbenchOverview />

      <div className="diagnostics-workbench" data-testid="diagnostics-workbench">
        <div className="diagnostics-workbench__main">
          <section
            className="diagnostics-workbench-panel"
            aria-labelledby="diagnostics-runtime-heading"
          >
            <SectionBand
              title={t('routes.diagnostics.runtimeSection')}
              titleId="diagnostics-runtime-heading"
            />
            <MetricStrip
              ariaLabel={t('routes.diagnostics.runtimeEnvLabel')}
              items={environmentItems}
            />
          </section>

          <section className="diagnostics-workbench-panel" aria-labelledby="session-status-heading">
            <SectionBand
              title={t('routes.diagnostics.sessionStatusHeading')}
              titleId="session-status-heading"
            />

            {!sessionApiEnabled && (
              <p data-testid="session-unavailable">{t('routes.diagnostics.sessionUnavailable')}</p>
            )}

            {sessionApiEnabled && info.isLoading && (
              <BlockSkeleton lines={4} testId="session-loading" />
            )}

            {info.isError && (
              <ErrorState
                testId="session-error"
                message={describeApiError(info.error)}
                {...(errorDetails ? { details: errorDetails } : {})}
              />
            )}

            {info.isSuccess && info.data && (
              <>
                <MetricStrip
                  ariaLabel={t('routes.diagnostics.sessionApiLabel')}
                  items={[
                    {
                      key: 'api-version',
                      label: t('routes.diagnostics.apiVersion'),
                      value: info.data.api_version,
                      valueTestId: 'session-api-version',
                    },
                    {
                      key: 'operations',
                      label: t('routes.diagnostics.operations'),
                      value: String(info.data.operations.length),
                      valueTestId: 'session-operations-count',
                    },
                  ]}
                />
                <ul
                  className="overview-operations"
                  aria-label={t('routes.diagnostics.operationsList')}
                  data-testid="session-operations"
                >
                  {info.data.operations.map((operation) => (
                    <li key={operation}>{operation}</li>
                  ))}
                </ul>
              </>
            )}
          </section>
        </div>

        <aside
          className="diagnostics-workbench__rail"
          aria-labelledby="diagnostics-next-heading"
          data-testid="diagnostics-next-actions"
        >
          <DiagnosticsNextActions sessionApiEnabled={sessionApiEnabled} />
        </aside>
      </div>
    </section>
  );
}

function DiagnosticsWorkbenchOverview(): JSX.Element {
  const { t } = useT();
  return (
    <nav
      className="diagnostics-workbench-overview"
      aria-label={t('routes.diagnostics.workbenchNavAria')}
      data-testid="diagnostics-workbench-overview"
    >
      <a className="diagnostics-workbench-overview__item" href="#diagnostics-runtime-heading">
        <span className="diagnostics-workbench-overview__label">
          {t('routes.diagnostics.stepRuntime')}
        </span>
        <span className="diagnostics-workbench-overview__detail">
          {t('routes.diagnostics.stepRuntimeDetail')}
        </span>
      </a>
      <a className="diagnostics-workbench-overview__item" href="#session-status-heading">
        <span className="diagnostics-workbench-overview__label">
          {t('routes.diagnostics.stepSession')}
        </span>
        <span className="diagnostics-workbench-overview__detail">
          {t('routes.diagnostics.stepSessionDetail')}
        </span>
      </a>
      <a className="diagnostics-workbench-overview__item" href="#diagnostics-next-heading">
        <span className="diagnostics-workbench-overview__label">
          {t('routes.diagnostics.stepNext')}
        </span>
        <span className="diagnostics-workbench-overview__detail">
          {t('routes.diagnostics.stepNextDetail')}
        </span>
      </a>
    </nav>
  );
}

function DiagnosticsNextActions({
  sessionApiEnabled,
}: {
  readonly sessionApiEnabled: boolean;
}): JSX.Element {
  const { t } = useT();
  return (
    <section className="diagnostics-next">
      <SectionBand title={t('routes.diagnostics.nextSection')} titleId="diagnostics-next-heading" />
      <p className="diagnostics-next__state" data-testid="diagnostics-next-state">
        {sessionApiEnabled
          ? t('routes.diagnostics.nextSessionEnabled')
          : t('routes.diagnostics.nextSessionDisabled')}
      </p>
      <div className="diagnostics-next__actions">
        <Link
          to={ROUTE_PATHS.jobs}
          className="diagnostics-next__action"
          data-testid="diagnostics-next-jobs"
        >
          {t('routes.diagnostics.nextJobs')}
        </Link>
        <Link
          to={ROUTE_PATHS.chambers}
          className="diagnostics-next__action"
          data-testid="diagnostics-next-chambers"
        >
          {t('routes.diagnostics.nextChambers')}
        </Link>
        <Link
          to={ROUTE_PATHS.reports}
          className="diagnostics-next__action"
          data-testid="diagnostics-next-reports"
        >
          {t('routes.diagnostics.nextReports')}
        </Link>
      </div>
      <p className="section-hint">{t('routes.diagnostics.nextHint')}</p>
    </section>
  );
}

/** Short, operator-facing detail line for the error state (no payload dump). */
function describeStatus(error: unknown): string | undefined {
  const status = (error as { status?: number } | null)?.status;
  return typeof status === 'number' ? `HTTP ${status}` : undefined;
}

export default DiagnosticsRoute;
