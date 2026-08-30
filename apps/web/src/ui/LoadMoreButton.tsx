/**
 * LoadMoreButton — keyset "load more" control primitive.
 *
 * The platform read routes (coverage, membership) and the session route render
 * an identical user-driven "더보기" button at the foot of a keyset-paginated
 * table. This primitive unifies that markup (label swap while fetching +
 * disabled state) so the control stays consistent and a route does not
 * re-declare it. Display-only: the caller owns `onClick` (typically
 * `fetchNextPage` from {@link useKeysetPagination}).
 */
import { useT } from '@/i18n';

import { Button } from './Button';

export interface LoadMoreButtonProps {
  readonly onClick: () => void;
  /** True while the next page is loading (disables + swaps the label). */
  readonly isFetching: boolean;
  /** Idle label (e.g. "더보기" / "더 보기"). */
  readonly label?: string;
  /** Override `data-testid` for a stable route selector. */
  readonly testId?: string;
}

export function LoadMoreButton({
  onClick,
  isFetching,
  label,
  testId,
}: LoadMoreButtonProps): JSX.Element {
  const { t } = useT();
  return (
    <Button
      type="button"
      variant="secondary"
      size="md"
      className="load-more touch-target"
      data-testid={testId ?? 'load-more'}
      onClick={onClick}
      disabled={isFetching}
    >
      {isFetching ? t('common.loading') : (label ?? t('ui.loadMore.label'))}
    </Button>
  );
}

export default LoadMoreButton;
