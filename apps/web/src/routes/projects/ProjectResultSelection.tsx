import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';

import {
  clearResultSelection,
  fetchResultAttempts,
  fetchResultSelections,
  selectResult,
  type MeasurementAttemptEnvelope,
  type PlatformPage,
  type ProviderSummary,
  type ResultSelectionEnvelope,
  type SelectionEventEnvelope,
  type SelectionEventRequest,
} from '@/api/platform-client';
import { queryKeys } from '@/api/query-config';
import { useT } from '@/i18n';
import { type ApiError } from '@/shared/api-error';
import { isValidProjectId } from '@/shared/project-id';
import { useKeysetPagination } from '@/shared/use-keyset-pagination';
import {
  BlockSkeleton,
  Button,
  Card,
  EmptyState,
  ErrorState,
  FieldGroup,
  SectionBand,
  StatusBadge,
  liveRegionProps,
} from '@/ui';

/** The platform selection service rejects reasons longer than this bound. */
export const RESULT_SELECTION_REASON_MAX_LENGTH = 500;

type SelectionAction =
  | { readonly kind: 'select'; readonly attemptId: string; readonly conditionHash: string }
  | { readonly kind: 'clear'; readonly conditionHash: string };

interface PendingConfirmation {
  readonly action: SelectionAction;
  readonly reason: string | null;
}

interface ConflictState {
  readonly action: SelectionAction;
  readonly error: ApiError;
  readonly ready: boolean;
  readonly refreshFailed: boolean;
}

export interface ProjectResultSelectionProps {
  readonly projectId: string;
  readonly providers: readonly ProviderSummary[];
  readonly providersLoading?: boolean;
  readonly providersError?: boolean;
}

function displayValue(value: string | number | null | undefined, unavailable: string): string {
  if (value === null || value === undefined) return unavailable;
  const text = String(value).trim();
  return text === '' ? unavailable : text;
}

function dedupeSelections(rows: readonly ResultSelectionEnvelope[]): ResultSelectionEnvelope[] {
  const seen = new Set<string>();
  return rows.filter((row) => {
    const key = `${row.provider_id}:${row.condition_hash}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function dedupeAttempts(rows: readonly MeasurementAttemptEnvelope[]): MeasurementAttemptEnvelope[] {
  const seen = new Set<string>();
  return rows.filter((row) => {
    if (seen.has(row.attempt_id)) return false;
    seen.add(row.attempt_id);
    return true;
  });
}

function selectionBody(
  action: SelectionAction,
  expectedRevision: number,
  reason: string | null,
): SelectionEventRequest {
  const body: SelectionEventRequest = { expected_revision: expectedRevision };
  if (reason !== null) body.reason = reason;
  if (action.kind === 'select') body.attempt_id = action.attemptId;
  return body;
}

/**
 * Central result browser. The route owns no provider-specific payload logic:
 * it only renders the opaque provenance fields returned by Platform.
 */
export function ProjectResultSelection({
  projectId,
  providers,
  providersLoading = false,
  providersError = false,
}: ProjectResultSelectionProps): JSX.Element {
  const { t } = useT();
  const queryClient = useQueryClient();
  const providerOptions = providers;
  const [providerId, setProviderId] = useState('');
  const [conditionHash, setConditionHash] = useState<string | null>(null);
  const [reasonByCondition, setReasonByCondition] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<PendingConfirmation | null>(null);
  const [conflict, setConflict] = useState<ConflictState | null>(null);
  const previousScope = useRef({ projectId, providerId: '' });

  const activeProvider =
    providerOptions.some((provider) => provider.provider_id === providerId) && providerId !== ''
      ? providerId
      : (providerOptions[0]?.provider_id ?? '');

  useEffect(() => {
    if (
      previousScope.current.projectId === projectId &&
      previousScope.current.providerId === activeProvider
    ) {
      return;
    }
    previousScope.current = { projectId, providerId: activeProvider };
    setConditionHash(null);
    setPending(null);
    setConflict(null);
  }, [activeProvider, projectId]);

  const selectionQueryKey = queryKeys.project.resultSelections(projectId, activeProvider);
  const selections = useKeysetPagination<
    ResultSelectionEnvelope,
    PlatformPage<ResultSelectionEnvelope>
  >({
    queryKey: selectionQueryKey,
    enabled: isValidProjectId(projectId) && activeProvider !== '',
    fetchPage: (cursor) => fetchResultSelections(projectId.trim(), activeProvider, cursor),
    getNextCursor: (page) => page.nextCursor ?? undefined,
  });
  const selectionRows = useMemo(() => dedupeSelections(selections.rows), [selections.rows]);

  const attemptsQueryKey = queryKeys.project.resultAttempts(
    projectId,
    activeProvider,
    conditionHash ?? '',
  );
  const attempts = useKeysetPagination<
    MeasurementAttemptEnvelope,
    PlatformPage<MeasurementAttemptEnvelope>
  >({
    queryKey: attemptsQueryKey,
    enabled: isValidProjectId(projectId) && activeProvider !== '' && conditionHash !== null,
    fetchPage: (cursor) =>
      fetchResultAttempts(projectId.trim(), activeProvider, conditionHash ?? '', cursor),
    getNextCursor: (page) => page.nextCursor ?? undefined,
  });
  const attemptRows = useMemo(() => dedupeAttempts(attempts.rows), [attempts.rows]);

  const currentRow = (condition: string): ResultSelectionEnvelope | undefined =>
    selectionRows.find((row) => row.condition_hash === condition);

  const reasonFor = (condition: string): string | null => {
    const value = reasonByCondition[condition]?.trim() ?? '';
    return value === '' ? null : value;
  };

  const reconcileConflict = async (action: SelectionAction, error: ApiError): Promise<void> => {
    setConflict({ action, error, ready: false, refreshFailed: false });
    try {
      await Promise.all([
        queryClient.refetchQueries({
          queryKey: queryKeys.project.resultSelections(projectId, activeProvider),
        }),
        queryClient.refetchQueries({
          queryKey: queryKeys.project.resultAttempts(
            projectId,
            activeProvider,
            action.conditionHash,
          ),
        }),
      ]);
      setConflict((current) =>
        current?.error === error ? { ...current, ready: true, refreshFailed: false } : current,
      );
    } catch {
      setConflict((current) =>
        current?.error === error ? { ...current, ready: false, refreshFailed: true } : current,
      );
    }
  };

  interface MutationVariables {
    readonly action: SelectionAction;
    readonly expectedRevision: number;
    readonly reason: string | null;
  }

  const mutationOptions = {
    onSuccess: () => {
      setPending(null);
      setConflict(null);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.project.resultSelections(projectId, activeProvider),
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.project.resultAttempts(projectId, activeProvider, conditionHash ?? ''),
      });
    },
    onError: (error: ApiError, variables: MutationVariables) => {
      if (error.status === 409) void reconcileConflict(variables.action, error);
    },
  };

  const selectMutation = useMutation<SelectionEventEnvelope, ApiError, MutationVariables>({
    mutationFn: ({ action, expectedRevision, reason }) => {
      if (action.kind !== 'select') {
        return Promise.reject(new Error('invalid result selection action'));
      }
      return selectResult(
        projectId.trim(),
        activeProvider,
        action.conditionHash,
        selectionBody(action, expectedRevision, reason),
      );
    },
    ...mutationOptions,
  });
  const clearMutation = useMutation<SelectionEventEnvelope, ApiError, MutationVariables>({
    mutationFn: ({ action, expectedRevision, reason }) => {
      if (action.kind !== 'clear') {
        return Promise.reject(new Error('invalid result clear action'));
      }
      return clearResultSelection(
        projectId.trim(),
        activeProvider,
        action.conditionHash,
        selectionBody(action, expectedRevision, reason),
      );
    },
    ...mutationOptions,
  });

  if (!isValidProjectId(projectId)) return <></>;
  if (providersError) {
    return (
      <ErrorState
        testId="project-results-provider-error"
        message={t('routes.projectResults.providerFailed')}
      />
    );
  }

  const requestAction = (action: SelectionAction): void => {
    setConflict(null);
    setPending({ action, reason: reasonFor(action.conditionHash) });
  };

  const confirmAction = (): void => {
    if (pending === null) return;
    const row = currentRow(pending.action.conditionHash);
    if (row === undefined) return;
    const variables: MutationVariables = {
      action: pending.action,
      expectedRevision: row.selection_revision,
      reason: pending.reason,
    };
    if (pending.action.kind === 'select') selectMutation.mutate(variables);
    else clearMutation.mutate(variables);
  };

  const retryConflict = (): void => {
    if (!conflict?.ready) return;
    const row = currentRow(conflict.action.conditionHash);
    if (row === undefined) return;
    const variables: MutationVariables = {
      action: conflict.action,
      expectedRevision: row.selection_revision,
      reason: reasonFor(conflict.action.conditionHash),
    };
    setConflict(null);
    if (conflict.action.kind === 'select') selectMutation.mutate(variables);
    else clearMutation.mutate(variables);
  };

  const mutationPending = selectMutation.isPending || clearMutation.isPending;
  const mutationFailed = selectMutation.isError || clearMutation.isError;

  return (
    <Card
      as="section"
      aria-labelledby="project-results-heading"
      // `Card` writes `data-testid` *after* spreading the rest props, so a raw
      // `data-testid` here is silently replaced by its `'card'` default and the
      // landmark becomes unaddressable. `testId` is the primitive's own slot.
      testId="project-result-selection"
    >
      <SectionBand title={t('routes.projectResults.title')} titleId="project-results-heading" />
      {providersLoading && (
        <BlockSkeleton
          lines={1}
          label={t('routes.projectResults.providerLoading')}
          testId="project-results-provider-loading"
        />
      )}
      {!providersLoading && providerOptions.length === 0 && (
        <EmptyState
          testId="project-results-no-providers"
          title={t('routes.projectResults.noProviders')}
          description={t('routes.projectResults.noProvidersDescription')}
        />
      )}
      {providerOptions.length > 0 && (
        <label htmlFor="project-results-provider">
          {t('routes.projectResults.provider')}
          <select
            id="project-results-provider"
            data-testid="project-results-provider"
            value={activeProvider}
            onChange={(event) => setProviderId(event.target.value)}
          >
            {providerOptions.map((provider) => (
              <option key={provider.provider_id} value={provider.provider_id}>
                {provider.display_name}
              </option>
            ))}
          </select>
        </label>
      )}
      {selections.isLoading && (
        <p role="status" data-testid="project-results-loading">
          {t('routes.projectResults.loading')}
        </p>
      )}
      {selections.isError && (
        <ErrorState
          testId="project-results-error"
          message={t('routes.projectResults.fetchFailed')}
        />
      )}
      {selections.isSuccess && selectionRows.length === 0 && (
        <EmptyState
          testId="project-results-empty"
          title={t('routes.projectResults.empty')}
          description={t('routes.projectResults.emptyDescription')}
        />
      )}
      {selections.isSuccess && selectionRows.length > 0 && (
        <ul aria-label={t('routes.projectResults.listLabel')}>
          {selectionRows.map((row) => (
            <ResultSelectionRow
              key={`${row.provider_id}:${row.condition_hash}`}
              row={row}
              isOpen={conditionHash === row.condition_hash}
              onOpen={() => setConditionHash(row.condition_hash)}
              attempts={conditionHash === row.condition_hash ? attemptRows : []}
              attemptsLoading={conditionHash === row.condition_hash && attempts.isLoading}
              attemptsError={conditionHash === row.condition_hash && attempts.isError}
              hasMoreAttempts={conditionHash === row.condition_hash && attempts.hasNextPage}
              onLoadMoreAttempts={attempts.fetchNextPage}
              reason={reasonByCondition[row.condition_hash] ?? ''}
              onReasonChange={(value) =>
                setReasonByCondition((current) => ({
                  ...current,
                  [row.condition_hash]: value,
                }))
              }
              onSelect={(attemptId) =>
                requestAction({ kind: 'select', attemptId, conditionHash: row.condition_hash })
              }
              onClear={() => {
                // Open the row before showing the confirmation.  A clear action
                // is available on a collapsed manual row; keeping the dialog
                // inside the row's details would otherwise leave the operator
                // with no confirmation or accessible context.
                setConditionHash(row.condition_hash);
                requestAction({ kind: 'clear', conditionHash: row.condition_hash });
              }}
              pendingAction={pending?.action.conditionHash === row.condition_hash ? pending : null}
              onConfirm={confirmAction}
              onCancel={() => setPending(null)}
              mutationPending={mutationPending}
            />
          ))}
        </ul>
      )}
      {selections.hasNextPage && (
        <Button
          type="button"
          variant="secondary"
          data-testid="project-results-next"
          disabled={selections.isFetchingNextPage}
          onClick={selections.fetchNextPage}
        >
          {selections.isFetchingNextPage
            ? t('routes.projectResults.loadingMore')
            : t('routes.projectResults.next')}
        </Button>
      )}
      {conflict !== null && (
        <div
          {...liveRegionProps('inlineNotice')}
          data-testid="project-results-conflict"
          data-conflict-code={conflict.error.code ?? ''}
        >
          <p>{t('routes.projectResults.conflict')}</p>
          {conflict.refreshFailed && <p>{t('routes.projectResults.conflictRefreshFailed')}</p>}
          <Button
            type="button"
            variant="primary"
            data-testid="project-results-conflict-retry"
            disabled={!conflict.ready || mutationPending}
            onClick={retryConflict}
          >
            {conflict.ready
              ? t('routes.projectResults.conflictRetry')
              : t('routes.projectResults.conflictReloading')}
          </Button>
        </div>
      )}
      {mutationFailed && conflict === null && (
        <p {...liveRegionProps('inlineNotice')} data-testid="project-results-write-error">
          {t('routes.projectResults.writeFailed')}
        </p>
      )}
    </Card>
  );
}

function ResultSelectionRow({
  row,
  isOpen,
  onOpen,
  attempts,
  attemptsLoading,
  attemptsError,
  hasMoreAttempts,
  onLoadMoreAttempts,
  reason,
  onReasonChange,
  onSelect,
  onClear,
  pendingAction,
  onConfirm,
  onCancel,
  mutationPending,
}: {
  readonly row: ResultSelectionEnvelope;
  readonly isOpen: boolean;
  readonly onOpen: () => void;
  readonly attempts: readonly MeasurementAttemptEnvelope[];
  readonly attemptsLoading: boolean;
  readonly attemptsError: boolean;
  readonly hasMoreAttempts: boolean;
  readonly onLoadMoreAttempts: () => void;
  readonly reason: string;
  readonly onReasonChange: (value: string) => void;
  readonly onSelect: (attemptId: string) => void;
  readonly onClear: () => void;
  readonly pendingAction: PendingConfirmation | null;
  readonly onConfirm: () => void;
  readonly onCancel: () => void;
  readonly mutationPending: boolean;
}): JSX.Element {
  const { t } = useT();
  const unavailable = t('routes.projectResults.notAvailable');
  const detailsId = `project-result-details-${row.condition_hash.replace(/[^a-zA-Z0-9_-]/gu, '-')}`;
  const reasonId = `${detailsId}-reason`;
  const confirmationId = `${detailsId}-confirmation`;
  const activeAction = pendingAction?.action ?? null;
  const selectionStatusKey =
    row.selection_source === 'manual'
      ? 'routes.projectResults.pinned'
      : 'routes.projectResults.latest';

  return (
    <li data-testid="project-result-row">
      <button
        type="button"
        aria-controls={detailsId}
        aria-expanded={isOpen}
        onClick={onOpen}
        data-testid="project-result-condition"
      >
        {row.condition_hash}
      </button>
      <StatusBadge
        status={row.selection_source === 'manual' ? 'pass' : 'running'}
        label={t(selectionStatusKey)}
      />
      <dl data-testid="project-result-provenance">
        <MetadataField label={t('routes.projectResults.sourceSession')} value={row.session_id} />
        <MetadataField
          label={t('routes.projectResults.providerSession')}
          value={row.provider_session_id}
          unavailable={unavailable}
        />
        <MetadataField
          label={t('routes.projectResults.chamber')}
          value={row.chamber_id}
          unavailable={unavailable}
        />
        <MetadataField
          label={t('routes.projectResults.sample')}
          value={row.sample_id}
          unavailable={unavailable}
        />
        <MetadataField
          label={t('routes.projectResults.operator')}
          value={row.operator}
          unavailable={unavailable}
        />
        <MetadataField
          label={t('routes.projectResults.time')}
          value={row.measured_at ?? row.created_at}
          unavailable={unavailable}
        />
        <MetadataField
          label={t('routes.projectResults.verdict')}
          value={row.verdict}
          unavailable={unavailable}
        />
        <MetadataField
          label={t('routes.projectResults.selectionRevision')}
          value={row.selection_revision}
          unavailable={unavailable}
        />
      </dl>
      {row.selection_source === 'manual' && (
        <Button
          type="button"
          variant="ghost"
          data-testid="project-result-clear"
          onClick={onClear}
          disabled={mutationPending}
        >
          {t('routes.projectResults.clear')}
        </Button>
      )}
      {isOpen && (
        <div id={detailsId} data-testid="project-result-details">
          <FieldGroup
            label={t('routes.projectResults.reasonLabel')}
            htmlFor={reasonId}
            help={t('routes.projectResults.reasonHint', {
              max: RESULT_SELECTION_REASON_MAX_LENGTH,
            })}
          >
            <textarea
              id={reasonId}
              value={reason}
              maxLength={RESULT_SELECTION_REASON_MAX_LENGTH}
              onChange={(event) => onReasonChange(event.target.value)}
            />
          </FieldGroup>
          <h3>{t('routes.projectResults.attempts')}</h3>
          {attemptsLoading && (
            <p {...liveRegionProps('backgroundLoad')}>
              {t('routes.projectResults.attemptLoading')}
            </p>
          )}
          {attemptsError && (
            <p {...liveRegionProps('inlineNotice')}>{t('routes.projectResults.attemptsError')}</p>
          )}
          {!attemptsLoading && !attemptsError && attempts.length === 0 && (
            <p>{t('routes.projectResults.noAttempts')}</p>
          )}
          {attempts.length > 0 && (
            <ul data-testid="project-result-attempts">
              {attempts.map((attempt) => (
                <li key={attempt.attempt_id}>
                  <MetadataField
                    label={t('routes.projectResults.attempt')}
                    value={attempt.attempt_number}
                    unavailable={unavailable}
                  />
                  <dl>
                    <MetadataField
                      label={t('routes.projectResults.sourceSession')}
                      value={attempt.session_id}
                      unavailable={unavailable}
                    />
                    <MetadataField
                      label={t('routes.projectResults.providerSession')}
                      value={attempt.provider_session_id}
                      unavailable={unavailable}
                    />
                    <MetadataField
                      label={t('routes.projectResults.chamber')}
                      value={attempt.chamber_id}
                      unavailable={unavailable}
                    />
                    <MetadataField
                      label={t('routes.projectResults.sample')}
                      value={attempt.sample_id}
                      unavailable={unavailable}
                    />
                    <MetadataField
                      label={t('routes.projectResults.operator')}
                      value={attempt.operator}
                      unavailable={unavailable}
                    />
                    <MetadataField
                      label={t('routes.projectResults.time')}
                      value={attempt.measured_at ?? attempt.created_at}
                      unavailable={unavailable}
                    />
                    <MetadataField
                      label={t('routes.projectResults.verdict')}
                      value={attempt.verdict}
                      unavailable={unavailable}
                    />
                  </dl>
                  <Button
                    type="button"
                    variant="secondary"
                    data-testid="project-result-select"
                    onClick={() => onSelect(attempt.attempt_id)}
                    disabled={mutationPending}
                  >
                    {t('routes.projectResults.select')}
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {hasMoreAttempts && (
            <Button type="button" variant="secondary" onClick={onLoadMoreAttempts}>
              {t('routes.projectResults.attemptNext')}
            </Button>
          )}
          {activeAction !== null && (
            <div
              role="group"
              aria-labelledby={confirmationId}
              data-testid="project-result-confirmation"
            >
              <p id={confirmationId}>
                {activeAction.kind === 'select'
                  ? t('routes.projectResults.selectConfirm')
                  : t('routes.projectResults.clearConfirm')}
              </p>
              <Button
                type="button"
                variant="primary"
                data-testid="project-result-confirm"
                disabled={mutationPending}
                onClick={onConfirm}
              >
                {mutationPending
                  ? t('routes.projectResults.confirmBusy')
                  : activeAction.kind === 'select'
                    ? t('routes.projectResults.confirmSelect')
                    : t('routes.projectResults.confirmClear')}
              </Button>
              <Button
                type="button"
                variant="ghost"
                data-testid="project-result-cancel"
                disabled={mutationPending}
                onClick={onCancel}
              >
                {t('routes.projectResults.cancel')}
              </Button>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function MetadataField({
  label,
  value,
  unavailable = '—',
}: {
  readonly label: string;
  readonly value: string | number | null | undefined;
  readonly unavailable?: string;
}): JSX.Element {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{displayValue(value, unavailable)}</dd>
    </div>
  );
}

export default ProjectResultSelection;
