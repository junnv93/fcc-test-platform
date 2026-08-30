import { Link } from 'react-router-dom';

import { type ChamberAvailabilityEnvelope } from '@/api/platform-client';
import { useChamberProgressPolling } from '@/api/use-chamber-progress';
import { useT } from '@/i18n';
import { sessionHistoryHref } from '@/shared/route-links';
import {
  DataTable,
  EmptyState,
  SectionBand,
  StatusBadge,
  StatusMessage,
  streamStatusKind,
  streamStatusLabelToken,
  type StreamStatus,
} from '@/ui';

import { ChamberProgressBar } from './ChamberProgressBar';
import { orDash } from './util';

/** Per-running-chamber progress overview (P12). Lets the operator see the
 *  progress of every in-use chamber *without* entering or starting one, and
 *  deep-links each to its session history. Renders an empty state when nothing
 *  is running. Progress is polled only for these (bounded) running chambers. */
export function ChamberRunOverview({
  running,
  streamStatus,
}: {
  readonly running: readonly ChamberAvailabilityEnvelope[];
  /**
   * M3 — connection state of the central progress relay that feeds the numbers
   * in this section. It belongs HERE rather than beside the availability table:
   * the relay pushes progress, and progress is what this section renders. When
   * the channel is not `open` these figures come from the fallback poll, and an
   * operator watching a wall monitor has to be able to tell which.
   */
  readonly streamStatus: StreamStatus;
}): JSX.Element {
  const { t } = useT();
  return (
    <section aria-labelledby="chambers-run-heading" data-testid="chambers-run">
      <SectionBand
        title={t('routes.chambers.sectionRunning')}
        titleId="chambers-run-heading"
        meta={
          <>
            {t('routes.chambers.streamLabel')}{' '}
            <StatusBadge
              status={streamStatusKind(streamStatus)}
              label={t(`ui.streamStatus.${streamStatusLabelToken(streamStatus)}`)}
              testId="chambers-stream-status"
            />
          </>
        }
      />
      {running.length === 0 ? (
        <EmptyState
          testId="chambers-run-empty"
          title={t('routes.chambers.runEmptyTitle')}
          description={t('routes.chambers.runEmptyDescription')}
        />
      ) : (
        <DataTable
          testId="chambers-run-table"
          caption={t('routes.chambers.runTableCaption')}
          head={
            <thead>
              <tr>
                <th scope="col">{t('routes.chambers.colName')}</th>
                <th scope="col">{t('routes.chambers.metricCompleted')}</th>
                <th scope="col">{t('routes.chambers.metricRatio')}</th>
                <th scope="col">{t('routes.chambers.colSession')}</th>
              </tr>
            </thead>
          }
          body={
            <tbody>
              {running.map((chamber) => (
                <ChamberRunRow key={chamber.chamber_id} chamber={chamber} />
              ))}
            </tbody>
          }
        />
      )}
    </section>
  );
}

/** One running chamber's live progress row. Polls the central proxy progress
 *  (P5) on the CRITICAL cadence while the run is active, retaining the
 *  last-known snapshot across a transient poll error (self-heal, mirror of the
 *  post-start `ChamberProgress`). The session cell deep-links to the history
 *  when the chamber's session_id is a resolvable numeric id. */
function ChamberRunRow({
  chamber,
}: {
  readonly chamber: ChamberAvailabilityEnvelope;
}): JSX.Element {
  const { t } = useT();
  const { phase, snapshot } = useChamberProgressPolling(chamber.chamber_id);
  const hardError = phase.kind === 'hardError';
  const transient = phase.kind === 'transientError';
  const href = sessionHistoryHref(chamber.session_id);

  return (
    <tr data-testid="chambers-run-row">
      <th scope="row">
        {orDash(chamber.name)}
        {transient && (
          <StatusMessage
            tone="info"
            testId="chambers-run-transient"
            message={t('routes.chambers.progressTransient')}
          />
        )}
      </th>
      <td className="data-cell-numeric" data-testid="chambers-run-completed">
        {hardError
          ? t('routes.chambers.runProgressFailed')
          : snapshot
            ? `${snapshot.progress.completed} / ${snapshot.progress.total}`
            : '—'}
      </td>
      <td data-testid="chambers-run-ratio">
        {snapshot ? (
          <ChamberProgressBar progress={snapshot.progress} testId="chambers-run-progress-bar" />
        ) : (
          '—'
        )}
      </td>
      <td>
        {href !== null ? (
          <Link to={href} data-testid="chambers-run-session-link">
            {orDash(chamber.session_id)}
          </Link>
        ) : (
          orDash(chamber.session_id)
        )}
      </td>
    </tr>
  );
}
