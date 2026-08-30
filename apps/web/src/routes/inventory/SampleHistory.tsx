import { useInfiniteQuery } from '@tanstack/react-query';

import { fetchSampleHistory } from '@/api/platform-client';
import { queryKeys } from '@/api/query-config';
import { useT } from '@/i18n';
import { BlockSkeleton, describeApiError, EmptyState, ErrorState, SectionBand } from '@/ui';

export interface SampleHistoryProps {
  readonly projectId: string;
  readonly sampleId: string;
}

export function SampleHistory({ projectId, sampleId }: SampleHistoryProps): JSX.Element {
  const { t } = useT();
  const history = useInfiniteQuery({
    queryKey: queryKeys.sampleInventory.history(projectId, sampleId),
    queryFn: ({ pageParam }) =>
      fetchSampleHistory(projectId, sampleId, pageParam ?? undefined, 100),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: projectId !== '' && sampleId !== '',
  });
  const revisions = history.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <section className="sample-history" aria-labelledby="sample-history-heading">
      <SectionBand
        title={t('routes.sampleInventory.history.title')}
        titleId="sample-history-heading"
      />
      {history.isPending && <BlockSkeleton lines={3} testId="sample-history-loading" />}
      {history.isError && (
        <ErrorState
          testId="sample-history-error"
          message={describeApiError(history.error, 'platform', {
            default: t('routes.sampleInventory.history.loadFailed'),
          })}
        />
      )}
      {history.isSuccess && revisions.length === 0 && (
        <EmptyState
          testId="sample-history-empty"
          title={t('routes.sampleInventory.history.emptyTitle')}
          description={t('routes.sampleInventory.history.emptyDescription')}
        />
      )}
      {history.isSuccess && revisions.length > 0 && (
        <ol className="sample-history__list" data-testid="sample-history-list">
          {revisions.map((revision) => (
            <li key={revision.revision_id} className="sample-history__item">
              <div className="sample-history__meta">
                <strong>
                  {t('routes.sampleInventory.history.revision', {
                    revision: revision.revision_number,
                  })}
                </strong>
                <time dateTime={revision.occurred_at}>{revision.occurred_at}</time>
                <span>{revision.actor_subject}</span>
              </div>
              <div className="sample-history__event">{revision.event_type}</div>
              <div className="sample-history__fields">
                {revision.changed_fields.length > 0
                  ? revision.changed_fields.join(', ')
                  : t('routes.sampleInventory.history.noChangedFields')}
              </div>
            </li>
          ))}
        </ol>
      )}
      {history.hasNextPage && (
        <button
          type="button"
          disabled={history.isFetchingNextPage}
          onClick={() => void history.fetchNextPage()}
          data-testid="sample-history-load-more"
        >
          {history.isFetchingNextPage
            ? t('routes.sampleInventory.loadingMore')
            : t('routes.sampleInventory.loadMore')}
        </button>
      )}
    </section>
  );
}

export default SampleHistory;
