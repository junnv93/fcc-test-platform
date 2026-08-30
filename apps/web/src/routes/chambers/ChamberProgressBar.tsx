import { type ChamberMeasurementSnapshot } from '@/api/platform-client';
import { useT } from '@/i18n';
import { classifyPercent, formatPercent } from '@/shared/percent-display';
import { ProgressBar, type ProgressTone } from '@/ui';

/** Render the C1 progress snapshot as a token-driven ProgressBar. This is a
 *  pure presentation of the already-polled snapshot — it opens no fetch/query
 *  of its own (the C1/C2 boundary stays the single data source). A run that has
 *  started but not yet reported a test count (running with total 0) is shown
 *  indeterminate; otherwise the bar tracks the completed/total ratio. */
export function ChamberProgressBar({
  progress,
  testId,
}: {
  readonly progress: ChamberMeasurementSnapshot['progress'];
  readonly testId: string;
}): JSX.Element {
  const { t } = useT();
  const indeterminate = progress.is_running && progress.total === 0;
  // M7 — the RAW percentage. This component used to round first
  // (`Math.round(ratio * 100)`) and then decide completion from the rounded
  // value, so a 99.6% run was painted in the pass palette *and* labelled "100%":
  // the colour axis and the string axis lying in the same direction, which is
  // how a rounding artefact becomes an operator's belief that a run finished.
  // Both axes now derive from the same unrounded value through the same SSOT, so
  // they cannot disagree with each other either.
  const percent = progress.ratio * 100;
  const tone: ProgressTone = progress.is_running
    ? 'running'
    : classifyPercent(percent).kind === 'complete'
      ? 'pass'
      : 'accent';
  return (
    <ProgressBar
      label={t('routes.chambers.progressBarLabel')}
      // The aria VALUE axis stays the raw number (percent-display.ts note): the
      // honesty rule governs the human-readable string, not the numeric channel.
      valueNow={indeterminate ? undefined : percent}
      valueText={
        indeterminate ? t('routes.chambers.progressBarIndeterminate') : formatPercent(percent)
      }
      indeterminate={indeterminate}
      tone={tone}
      testId={testId}
    />
  );
}
