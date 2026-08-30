import { useQuery } from '@tanstack/react-query';

import { fetchPublishedTestPlans } from '@/api/headless-client';
import { queryKeys } from '@/api/query-config';
import { useT } from '@/i18n';
import { DataTable, describeApiError, ErrorState, SectionBand } from '@/ui';

export function DraftPublishedDiff({
  projectId,
  draftId,
  draftRowCount,
}: {
  readonly projectId: string;
  readonly draftId: string;
  readonly draftRowCount: number;
}): JSX.Element {
  const { t } = useT();
  const publications = useQuery({
    queryKey: queryKeys.testPlans.publications(projectId),
    queryFn: async () => {
      return fetchPublishedTestPlans(projectId);
    },
  });
  const published = publications.data?.publications.find((plan) => plan.draft_id === draftId);
  const publishedRows = published?.row_count ?? 0;
  const delta = draftRowCount - publishedRows;

  return (
    <section aria-labelledby="test-plans-diff-heading" data-testid="test-plans-diff">
      <SectionBand title={t('routes.testPlans.sectionDiff')} titleId="test-plans-diff-heading" />
      {publications.isError && (
        <ErrorState
          testId="test-plans-diff-error"
          message={describeApiError(publications.error, 'headless', {
            forbidden: t('routes.testPlans.readForbidden'),
            network: t('routes.testPlans.readNetwork'),
            default: t('routes.testPlans.readFailed'),
          })}
        />
      )}
      <DataTable
        testId="test-plans-diff-table"
        caption={t('routes.testPlans.diffCaption')}
        head={
          <thead>
            <tr>
              <th scope="col">{t('routes.testPlans.diffField')}</th>
              <th scope="col">{t('routes.testPlans.diffDraft')}</th>
              <th scope="col">{t('routes.testPlans.diffPublished')}</th>
              <th scope="col">{t('routes.testPlans.diffDelta')}</th>
            </tr>
          </thead>
        }
        body={
          <tbody>
            <tr>
              <th scope="row">{t('routes.testPlans.metaRowCount')}</th>
              <td data-testid="test-plans-diff-draft-count">{draftRowCount}</td>
              <td data-testid="test-plans-diff-published-count">
                {published === undefined ? t('routes.testPlans.diffNoPublication') : publishedRows}
              </td>
              <td data-testid="test-plans-diff-delta">
                {published === undefined ? t('routes.testPlans.diffUnavailable') : delta}
              </td>
            </tr>
            <tr>
              <th scope="row">{t('routes.testPlans.diffPlan')}</th>
              <td>{draftId}</td>
              <td data-testid="test-plans-diff-plan">
                {published?.plan_id ?? t('routes.testPlans.diffNoPublication')}
              </td>
              <td>{published?.published_at ?? t('routes.testPlans.diffUnavailable')}</td>
            </tr>
          </tbody>
        }
      />
      <p data-testid="test-plans-diff-gap">{t('routes.testPlans.diffRowLevelGap')}</p>
    </section>
  );
}
