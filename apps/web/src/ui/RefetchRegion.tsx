import { useT } from '@/i18n';

import type { ReactNode } from 'react';

/**
 * RefetchRegion — the middle loading state (§M8.7).
 *
 * Three loading states, three shapes:
 *   1. first load      → skeleton (`DataTableSkeleton` / `BlockSkeleton`)
 *   2. refetch         → THIS: keep the previous result, dim it, float a badge
 *   3. measurement run → `RunProgress` (progress + step, never a spinner)
 *
 * Collapsing (2) into (1) is the common mistake: changing a filter blanks the
 * table the operator was reading and they lose their place. Keeping the stale
 * result on screen while it refreshes preserves orientation, and the badge is
 * a polite live region so assistive tech learns about the refresh without the
 * content being torn down and re-announced.
 */
export interface RefetchRegionProps {
  /** True while a background refresh is in flight (`isFetching && !isLoading`). */
  readonly refetching: boolean;
  /** The already-rendered content. Stays mounted throughout. */
  readonly children: ReactNode;
  /** Badge copy override; defaults to the shared "갱신 중" announcement. */
  readonly label?: string;
  readonly testId?: string;
}

export function RefetchRegion({
  refetching,
  children,
  label,
  testId,
}: RefetchRegionProps): JSX.Element {
  const { t } = useT();
  return (
    <div
      className="refetch-region"
      data-refetching={refetching ? 'true' : 'false'}
      data-testid={testId ?? 'refetch-region'}
    >
      <div className="refetch-region__content">{children}</div>
      {refetching && (
        <div className="refetch-region__overlay">
          <p
            className="refetch-region__badge"
            role="status"
            aria-live="polite"
            data-testid="refetch-region-badge"
          >
            <span className="refetch-region__spinner" aria-hidden="true" />
            {label ?? t('ui.refetchRegion.label')}
          </p>
        </div>
      )}
    </div>
  );
}

export default RefetchRegion;
