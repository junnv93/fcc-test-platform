import { useChamberProgressPolling } from '@/api/use-chamber-progress';
import { useT } from '@/i18n';
import { formatPercent } from '@/shared/percent-display';
import {
  describeApiError,
  ErrorState,
  MetricStrip,
  SectionBand,
  StatusMessage,
  type MetricStripItem,
} from '@/ui';

import { ChamberProgressBar } from './ChamberProgressBar';
import { ChamberNextActionLinks } from './next-actions';

export function ChamberProgress({
  chamberId,
  projectId = '',
}: {
  readonly chamberId: string;
  /** Carried through so the reports next-action lands scoped to the project the
   *  run belongs to; falls back to the unscoped reports screen when absent. */
  readonly projectId?: string;
}): JSX.Element {
  const { t } = useT();
  const { phase, snapshot, error } = useChamberProgressPolling(chamberId);

  const items: readonly MetricStripItem[] = snapshot
    ? [
        {
          key: 'running',
          label: t('routes.chambers.metricRunning'),
          value: snapshot.progress.is_running
            ? t('routes.chambers.metricRunningYes')
            : t('routes.chambers.metricRunningNo'),
          valueTestId: 'chambers-progress-running',
        },
        {
          key: 'completed',
          label: t('routes.chambers.metricCompleted'),
          value: `${snapshot.progress.completed} / ${snapshot.progress.total}`,
          valueTestId: 'chambers-progress-completed',
        },
        {
          key: 'remaining',
          label: t('routes.chambers.metricRemaining'),
          // Derived from the same snapshot (total − completed, floored at 0) —
          // §5③ "완료/남은". No new read contract; pure presentation.
          value: `${Math.max(0, snapshot.progress.total - snapshot.progress.completed)}`,
          valueTestId: 'chambers-progress-remaining',
        },
        {
          key: 'ratio',
          label: t('routes.chambers.metricRatio'),
          // M7 — delegated to the percent-display SSOT: a local round here
          // would re-introduce the 99.6%→"100%" boundary lie next to a bar
          // that (correctly) does not claim completion.
          value: formatPercent(snapshot.progress.ratio * 100),
          valueTestId: 'chambers-progress-ratio',
        },
      ]
    : [];

  return (
    <section aria-labelledby="chambers-progress-heading" data-testid="chambers-progress">
      <SectionBand
        title={t('routes.chambers.sectionProgress')}
        titleId="chambers-progress-heading"
      />
      {phase.kind === 'loading' && <p aria-busy="true">{t('routes.chambers.progressLoading')}</p>}
      {/* Hard error: errored with NO data yet (initial fetch failed) — surface
          the full ErrorState plus a diagnostics affordance so the operator can
          check central connectivity rather than dead-ending on the error. */}
      {phase.kind === 'hardError' && (
        <>
          <ErrorState
            testId="chambers-progress-error"
            message={describeApiError(error, 'platform', {
              forbidden: t('routes.chambers.readForbidden'),
              network: t('routes.chambers.readNetwork'),
              default: t('routes.chambers.progressFailed'),
            })}
          />
          <nav
            className="chambers-next__actions"
            aria-label={t('routes.chambers.progressErrorNextAria')}
            data-testid="chambers-progress-error-actions"
          >
            <ChamberNextActionLinks keys={['diagnostics']} idPrefix="chambers-progress-error" />
          </nav>
        </>
      )}
      {/* Transient error: a poll failed but we still hold the last-known
          progress — show a non-destructive note while polling self-heals, and
          keep the metrics below visible. */}
      {phase.kind === 'transientError' && (
        <StatusMessage
          tone="info"
          testId="chambers-progress-transient"
          message={t('routes.chambers.progressTransient')}
        />
      )}
      {/* Last-known metrics stay visible across a transient error. */}
      {snapshot !== undefined && (
        <>
          <ChamberProgressBar progress={snapshot.progress} testId="chambers-progress-bar" />
          <MetricStrip ariaLabel={t('routes.chambers.metricStripLabel')} items={items} />
          {/* Contextual next steps right where the operator watches the run —
              history/queue while it runs, reports once it completes — so they
              need not hunt the rail for the next move. */}
          <nav
            className="chambers-next__actions"
            aria-label={t('routes.chambers.progressNextAria')}
            data-testid="chambers-progress-next"
          >
            <ChamberNextActionLinks
              keys={['sessions', 'jobs', 'reports']}
              projectId={projectId}
              idPrefix="chambers-progress-next"
            />
          </nav>
        </>
      )}
    </section>
  );
}
