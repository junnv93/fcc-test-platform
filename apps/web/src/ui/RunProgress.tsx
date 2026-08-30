import { useT } from '@/i18n';
import { formatPercent } from '@/shared/percent-display';

import { ProgressBar } from './ProgressBar';

/**
 * RunProgress — a running measurement as a first-class state (§M8.7).
 *
 * An FCC measurement takes tens of seconds to minutes. A spinner communicates
 * "something is happening" and nothing else, which is precisely useless over
 * that duration: the operator cannot tell a healthy sweep from a hung GPIB
 * session. This surface answers the three questions they actually have —
 * how far along, which step, and how much longer.
 *
 * Presentational only: it owns no polling. The caller feeds it whatever the
 * progress boundary already fetched, exactly like `ProgressBar`.
 */
export interface RunProgressProps {
  /** Accessible name for the progress bar (e.g. "측정 진행률"). */
  readonly label: string;
  /** Percent complete, or `undefined` when the run has not reported one yet
   *  (renders the indeterminate bar rather than a fake 0%). */
  readonly percent?: number | undefined;
  /** Current step copy ("BLE 1M · CH 0 · Duty 측정"). */
  readonly step: string;
  /** Estimated seconds remaining, when the caller knows it. */
  readonly etaSeconds?: number | undefined;
  readonly testId?: string;
}

export function RunProgress({
  label,
  percent,
  step,
  etaSeconds,
  testId,
}: RunProgressProps): JSX.Element {
  const { t } = useT();
  return (
    <section className="run-progress" data-testid={testId ?? 'run-progress'}>
      {/* `valueNow` stays the RAW number (aria-valuenow keeps its numeric
          meaning); only the human-readable `valueText` routes through the
          honesty SSOT — 99.6 must not announce itself as 100% (M1/P1). */}
      <ProgressBar
        label={label}
        valueNow={percent}
        tone="running"
        {...(percent === undefined ? {} : { valueText: formatPercent(percent) })}
      />
      <p className="run-progress__step" data-testid="run-progress-step">
        {step}
      </p>
      <p className="run-progress__meta">
        <span data-testid="run-progress-percent">
          {percent === undefined
            ? t('ui.runProgress.percentUnknown')
            : t('ui.runProgress.percent', { percent: formatPercent(percent) })}
        </span>
        {etaSeconds !== undefined && (
          <span data-testid="run-progress-eta">
            {t('ui.runProgress.eta', { minutes: Math.ceil(etaSeconds / 60) })}
          </span>
        )}
      </p>
    </section>
  );
}

export default RunProgress;
