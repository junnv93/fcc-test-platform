import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';

import {
  appendSampleCustodyEvent,
  deleteSampleCustodyEvent,
  fetchSampleCustodyEvents,
  type SampleCustodyEventRequest,
  type SampleCustodyEventType,
} from '@/api/platform-client';
import { queryKeys } from '@/api/query-config';
import { useT } from '@/i18n';
import {
  BlockSkeleton,
  Button,
  describeApiError,
  EmptyState,
  ErrorState,
  FieldGroup,
  SectionBand,
} from '@/ui';

/**
 * PM 축의 반입/반출 이력 (ADR-0002).
 *
 * ⚠️ 이 패널이 생기기 전까지 반입·반출은 `intake_cert`/`received_date`/
 * `released_date`/`note` 네 개의 단일 TEXT 칸에 줄바꿈으로 쌓여 있었다. 그래서
 * 「이 시료가 몇 번 나갔다 들어왔나」를 셀 수 없었고, 반출과 반입을 짝지을 수도
 * 없었다. 그 원문은 편집기의 「기존 기록」 칸에 그대로 남아 있다 — 한 줄도 잃지
 * 않는다.
 *
 * 수정(PATCH)이 없는 것은 누락이 아니라 결정이다: 수정은 흔적 없이 과거를 바꾸지만
 * 삭제는 보이고, 다시 적으면 새 행위자와 시각이 붙는다.
 */
export interface SampleCustodyPanelProps {
  readonly projectId: string;
  readonly sampleId: string;
  readonly canWrite: boolean;
}

const EMPTY_DRAFT = {
  event_type: 'received' as SampleCustodyEventType,
  occurred_on: '',
  counterparty: '',
  intake_cert_number: '',
  reason: '',
  note: '',
};

type Draft = typeof EMPTY_DRAFT;

function nullable(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
}

export function SampleCustodyPanel({
  projectId,
  sampleId,
  canWrite,
}: SampleCustodyPanelProps): JSX.Element {
  const { t } = useT();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);

  const events = useQuery({
    queryKey: queryKeys.sampleInventory.custody(projectId, sampleId),
    queryFn: () => fetchSampleCustodyEvents(projectId, sampleId),
    enabled: projectId !== '' && sampleId !== '',
  });

  // 사건 하나가 시료의 보유 상태를 바꾸므로 상세/목록 캐시도 함께 무효화한다.
  const invalidate = async (): Promise<void> => {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.sampleInventory.custody(projectId, sampleId),
    });
    await queryClient.invalidateQueries({ queryKey: queryKeys.sampleInventory.all });
  };

  const append = useMutation({
    mutationFn: (): Promise<unknown> => {
      const body: SampleCustodyEventRequest = {
        event_type: draft.event_type,
        occurred_on: nullable(draft.occurred_on),
        counterparty: nullable(draft.counterparty),
        intake_cert_number: nullable(draft.intake_cert_number),
        reason: nullable(draft.reason),
        note: nullable(draft.note),
      };
      return appendSampleCustodyEvent(projectId, sampleId, body);
    },
    onSuccess: () => {
      setDraft(EMPTY_DRAFT);
      void invalidate();
    },
  });

  const remove = useMutation({
    mutationFn: (eventId: string) =>
      deleteSampleCustodyEvent(projectId, sampleId, eventId),
    onSuccess: () => void invalidate(),
  });

  function update<K extends keyof Draft>(key: K, value: Draft[K]): void {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (canWrite && !append.isPending) append.mutate();
  }

  const items = events.data?.items ?? [];

  return (
    <section className="sample-custody" aria-labelledby="sample-custody-heading">
      <SectionBand
        title={t('routes.sampleInventory.custodyTitle')}
        titleId="sample-custody-heading"
      />
      <p className="sample-custody__description">
        {t('routes.sampleInventory.custodyDescription')}
      </p>

      {events.isPending && <BlockSkeleton lines={3} testId="sample-custody-loading" />}
      {events.isError && (
        <ErrorState
          testId="sample-custody-error"
          message={describeApiError(events.error, 'platform', {
            default: t('routes.sampleInventory.custodyLoadFailed'),
          })}
        />
      )}
      {events.isSuccess && items.length === 0 && (
        <EmptyState
          testId="sample-custody-empty"
          title={t('routes.sampleInventory.custodyEmptyTitle')}
          description={t('routes.sampleInventory.custodyEmptyDescription')}
        />
      )}
      {items.length > 0 && (
        <ol className="sample-custody__list" data-testid="sample-custody-list">
          {items.map((item) => (
            <li key={item.custody_event_id} className="sample-custody__item">
              <span
                className="sample-custody__type"
                data-event-type={item.event_type}
                data-testid={`sample-custody-type-${item.custody_event_id}`}
              >
                {item.event_type === 'received'
                  ? t('routes.sampleInventory.custodyReceived')
                  : t('routes.sampleInventory.custodyReleased')}
              </span>
              <span className="sample-custody__date">{item.occurred_on ?? '—'}</span>
              <span className="sample-custody__counterparty">
                {item.counterparty ?? ''}
              </span>
              <span className="sample-custody__cert">{item.intake_cert_number ?? ''}</span>
              <span className="sample-custody__reason">{item.reason ?? ''}</span>
              <span className="sample-custody__note">{item.note ?? ''}</span>
              <span className="sample-custody__actor">{item.actor_subject}</span>
              {canWrite && (
                <Button
                  type="button"
                  variant="danger"
                  disabled={remove.isPending}
                  onClick={() => {
                    if (window.confirm(t('routes.sampleInventory.custodyDeleteConfirm'))) {
                      remove.mutate(item.custody_event_id);
                    }
                  }}
                  data-testid={`sample-custody-delete-${item.custody_event_id}`}
                >
                  {t('routes.sampleInventory.custodyDelete')}
                </Button>
              )}
            </li>
          ))}
        </ol>
      )}
      {remove.isError && (
        <ErrorState
          testId="sample-custody-delete-error"
          message={describeApiError(remove.error, 'platform', {
            default: t('routes.sampleInventory.custodyDeleteFailed'),
          })}
        />
      )}

      {canWrite && (
        <form onSubmit={submit} data-testid="sample-custody-form">
          <fieldset disabled={append.isPending}>
            <legend className="sr-only">{t('routes.sampleInventory.custodyAdd')}</legend>
            <div className="sample-custody__grid">
              <FieldGroup
                label={t('routes.sampleInventory.custodyFields.eventType')}
                htmlFor="sample-custody-event-type"
              >
                <select
                  id="sample-custody-event-type"
                  data-testid="sample-custody-event-type"
                  value={draft.event_type}
                  onChange={(event) =>
                    update('event_type', event.target.value as SampleCustodyEventType)
                  }
                >
                  <option value="received">
                    {t('routes.sampleInventory.custodyReceived')}
                  </option>
                  <option value="released">
                    {t('routes.sampleInventory.custodyReleased')}
                  </option>
                </select>
              </FieldGroup>
              <FieldGroup
                label={t('routes.sampleInventory.custodyFields.occurredOn')}
                htmlFor="sample-custody-occurred-on"
              >
                <input
                  id="sample-custody-occurred-on"
                  data-testid="sample-custody-occurred-on"
                  value={draft.occurred_on}
                  onChange={(event) => update('occurred_on', event.target.value)}
                />
              </FieldGroup>
              <FieldGroup
                label={t('routes.sampleInventory.custodyFields.counterparty')}
                htmlFor="sample-custody-counterparty"
              >
                <input
                  id="sample-custody-counterparty"
                  data-testid="sample-custody-counterparty"
                  value={draft.counterparty}
                  onChange={(event) => update('counterparty', event.target.value)}
                />
              </FieldGroup>
              <FieldGroup
                label={t('routes.sampleInventory.custodyFields.intakeCertNumber')}
                htmlFor="sample-custody-intake-cert"
                help={t('routes.sampleInventory.custodyCertHint')}
                helpTestId="sample-custody-intake-cert-help"
              >
                <input
                  id="sample-custody-intake-cert"
                  data-testid="sample-custody-intake-cert"
                  value={draft.intake_cert_number}
                  onChange={(event) => update('intake_cert_number', event.target.value)}
                />
              </FieldGroup>
              <FieldGroup
                label={t('routes.sampleInventory.custodyFields.reason')}
                htmlFor="sample-custody-reason"
              >
                <input
                  id="sample-custody-reason"
                  data-testid="sample-custody-reason"
                  value={draft.reason}
                  onChange={(event) => update('reason', event.target.value)}
                />
              </FieldGroup>
              <FieldGroup
                label={t('routes.sampleInventory.custodyFields.note')}
                htmlFor="sample-custody-note"
              >
                <input
                  id="sample-custody-note"
                  data-testid="sample-custody-note"
                  value={draft.note}
                  onChange={(event) => update('note', event.target.value)}
                />
              </FieldGroup>
            </div>
          </fieldset>
          <Button
            type="submit"
            variant="primary"
            loading={append.isPending}
            loadingLabel={t('routes.sampleInventory.custodyAdding')}
            data-testid="sample-custody-add"
          >
            {t('routes.sampleInventory.custodyAdd')}
          </Button>
          {append.isError && (
            <ErrorState
              testId="sample-custody-add-error"
              message={describeApiError(append.error, 'platform', {
                default: t('routes.sampleInventory.custodyAddFailed'),
              })}
            />
          )}
        </form>
      )}
    </section>
  );
}

export default SampleCustodyPanel;
