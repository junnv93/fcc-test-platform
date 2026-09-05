import { useQuery } from '@tanstack/react-query';

import { fetchSampleIntakes } from '@/api/platform-client';
import { queryKeys } from '@/api/query-config';
import { useT } from '@/i18n';
import { BlockSkeleton, describeApiError, EmptyState, ErrorState, SectionBand } from '@/ui';

/**
 * 시험 실무자 축의 1:N 입고 이력 (ADR-0002 완료조건 4).
 *
 * ⚠️ 이 축은 스키마가 이미 옳았다 — `sample_intakes` 가 처음부터 append-only 1:N
 * 이었다. 없던 것은 **그것을 읽을 창**이다: 편집기는 `latest_intake` 한 건만
 * 채웠고, 「반출됐다 반입되면 다시 기록」한 과거 행들은 DB 에 쌓이기만 하고 화면
 * 어디에서도 보이지 않았다. 이름이 비슷한 `SampleHistory` 는 입고가 아니라
 * `sample_inventory_revisions`(감사 리비전)를 보여주는 다른 축이다.
 */
export interface SampleIntakeHistoryProps {
  readonly projectId: string;
  readonly sampleId: string;
}

const COLUMNS = [
  { key: 'intake_date', labelKey: 'intakeDate' },
  { key: 'tech_group', labelKey: 'techGroup' },
  { key: 'bl', labelKey: 'bl' },
  { key: 'ap', labelKey: 'ap' },
  { key: 'cp', labelKey: 'cp' },
  { key: 'csc', labelKey: 'csc' },
  { key: 'rf_cal', labelKey: 'rfCal' },
  { key: 'hw_rev', labelKey: 'hwRev' },
  { key: 'note', labelKey: 'intakeNote' },
] as const;

export function SampleIntakeHistory({
  projectId,
  sampleId,
}: SampleIntakeHistoryProps): JSX.Element {
  const { t } = useT();
  const intakes = useQuery({
    queryKey: queryKeys.sampleInventory.intakes(projectId, sampleId),
    queryFn: () => fetchSampleIntakes(projectId, sampleId),
    enabled: projectId !== '' && sampleId !== '',
  });
  const items = intakes.data?.items ?? [];

  return (
    <section className="sample-intake-history" aria-labelledby="sample-intake-history-heading">
      <SectionBand
        title={t('routes.sampleInventory.intakeHistoryTitle')}
        titleId="sample-intake-history-heading"
      />
      <p className="sample-intake-history__description">
        {t('routes.sampleInventory.intakeHistoryDescription')}
      </p>
      {intakes.isPending && <BlockSkeleton lines={3} testId="sample-intake-history-loading" />}
      {intakes.isError && (
        <ErrorState
          testId="sample-intake-history-error"
          message={describeApiError(intakes.error, 'platform', {
            default: t('routes.sampleInventory.intakeHistoryLoadFailed'),
          })}
        />
      )}
      {intakes.isSuccess && items.length === 0 && (
        <EmptyState
          testId="sample-intake-history-empty"
          title={t('routes.sampleInventory.intakeHistoryEmptyTitle')}
          description={t('routes.sampleInventory.intakeHistoryEmptyDescription')}
        />
      )}
      {items.length > 0 && (
        // 가로 스크롤은 표 자신이 갖는다 — 페이지 본문이 옆으로 밀리지 않게.
        <div className="sample-intake-history__scroll">
          <table className="sample-intake-history__table" data-testid="sample-intake-history-table">
            <thead>
              <tr>
                {COLUMNS.map(({ key, labelKey }) => (
                  <th key={key} scope="col">
                    {t(`routes.sampleInventory.editor.fields.${labelKey}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.intake_id} data-testid={`sample-intake-row-${item.intake_id}`}>
                  {COLUMNS.map(({ key }) => (
                    <td key={key}>{item[key] ?? ''}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default SampleIntakeHistory;
