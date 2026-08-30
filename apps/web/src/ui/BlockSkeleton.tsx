import { useT } from '@/i18n';

/**
 * BlockSkeleton — non-table loading placeholder (§M8.1/§M8.2).
 *
 * `DataTableSkeleton` covers the table branch, but most `isLoading` branches in
 * this app are panels, metric strips and detail readouts that were rendering a
 * bare `<p aria-busy="true">불러오는 중…</p>`. A text line reserves neither the
 * height nor the shape of what arrives, so every load ended in a layout jump —
 * exactly the regression §M8.2 forbids.
 *
 * `lines` is meant to be derived from the shape the caller is about to render
 * (field count, metric count), not guessed.
 */
export interface BlockSkeletonProps {
  /** How many placeholder lines to reserve — mirror the arriving shape. */
  readonly lines: number;
  /** `metric` reserves the taller rung used by MetricStrip values. */
  readonly variant?: 'text' | 'metric';
  /** Accessible label override; defaults to the shared loading announcement. */
  readonly label?: string;
  readonly testId?: string;
}

export function BlockSkeleton({
  lines,
  variant = 'text',
  label,
  testId,
}: BlockSkeletonProps): JSX.Element {
  const { t } = useT();
  const className =
    variant === 'metric' ? 'block-skeleton block-skeleton--metric' : 'block-skeleton';
  return (
    <div
      className={className}
      role="status"
      aria-live="polite"
      aria-busy="true"
      aria-label={label ?? t('ui.blockSkeleton.ariaLabel')}
      data-testid={testId ?? 'block-skeleton'}
    >
      {Array.from({ length: Math.max(1, lines) }).map((_, index) => (
        <span key={index} className="block-skeleton__line" data-testid="block-skeleton-line" />
      ))}
    </div>
  );
}

export default BlockSkeleton;
