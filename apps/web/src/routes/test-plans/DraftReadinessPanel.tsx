import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import {
  archiveTestPlanDraft,
  fetchTestPlanDraft,
  publishTestPlanDraft,
  validateTestPlanDraft,
} from '@/api/headless-client';
import { queryKeys } from '@/api/query-config';
import { useT } from '@/i18n';
import { type ApiError } from '@/shared/api-error';
import { Button, describeApiError, ErrorState, SectionBand, StatusMessage, Toolbar } from '@/ui';

import { draftRowsFingerprint, draftValidationState } from './draftFingerprint';
import { DraftPublishedDiff } from './DraftPublishedDiff';
import { isPublishableDraft } from './status';
import { type ValidateResponse } from './types';
import { ValidateResult } from './ValidateResult';

/**
 * Readiness rail for the selected draft — the single owner of publish-readiness
 * (Phase 3). It answers "is this draft safe to publish, and what is the next
 * step?" before the operator scrolls through row data. It owns:
 *   - a next-action hint derived ONLY from real draft state (row count / role —
 *     no fabricated "recommended action" data),
 *   - the validate action + its result (StatusMessage announces via aria-live),
 *   - the draft↔published diff,
 *   - the publish action (moved here from the per-row list affordance so there
 *     is exactly one publish definition, co-located with the readiness context),
 *   - the archive action (W3-6 M2, 2026-07-31) — the OTHER terminal transition
 *     out of DRAFT. It belongs beside publish for the same reason publish was
 *     moved here: a draft's lifecycle has exactly one owner on this screen.
 *
 * Archive is offered only while `isPublishableDraft(status)` holds — the SAME
 * predicate that gates publish. That is not the frontend re-deriving a server
 * rule: DRAFT-only is already this module's published notion of "still
 * mutable", and the backend independently enforces it (409 on a non-draft),
 * which the conflict arm surfaces if a concurrent transition wins the race.
 *
 * Archive is IRREVERSIBLE — the headless API exposes no unarchive/restore
 * operation, so the UI must neither imply one nor let a stray click reach it.
 * Hence the two-step acknowledgement, and hence the warning copy states the
 * absence of a way back rather than a vague "are you sure".
 *
 * It reads the SAME `queryKeys.testPlans.draft(projectId, draftId)` key the
 * editor panel reads, so React Query dedupes the fetch and the publish/validate
 * invalidation refetches both panels in lockstep (key drift is impossible — both
 * call the same factory).
 */
export function DraftReadinessPanel({
  projectId,
  draftId,
  canAuthor,
}: {
  readonly projectId: string;
  readonly draftId: string;
  readonly canAuthor: boolean;
}): JSX.Element {
  const { t } = useT();
  const queryClient = useQueryClient();

  const detail = useQuery({
    queryKey: queryKeys.testPlans.draft(projectId, draftId),
    queryFn: async () => {
      return fetchTestPlanDraft(projectId, draftId);
    },
  });

  const view = detail.data;
  const status = view?.status ?? '';
  // Kept as the raw (possibly undefined) reference rather than `?? []`: a fresh
  // empty array every render would invalidate the memo below on every render.
  const rows = view?.rows;
  const rowCount = rows?.length ?? 0;
  // W2-C M3 — content digest of the row set currently on screen. Recomputed
  // whenever the detail query yields a new rows array (add-row, remove-row, bulk
  // replace, another operator's write), which is exactly when a prior validation
  // stops describing the draft.
  const fingerprint = useMemo(() => draftRowsFingerprint(rows ?? []), [rows]);

  // TVariables is the fingerprint: the request carries the exact draft content it
  // was issued for, so the result and "what it judged" travel together. React
  // Query keeps `variables` after the mutation settles, which is what makes the
  // freshness check a pure derivation instead of a second synchronized state.
  const validateMutation = useMutation<ValidateResponse, ApiError, string>({
    mutationFn: async () => {
      return validateTestPlanDraft(projectId, draftId);
    },
  });

  const validationState = draftValidationState(
    validateMutation.isSuccess,
    validateMutation.variables,
    fingerprint,
  );
  // ONLY a fresh result can block. A stale result was computed over rows that no
  // longer exist, and "never validated" is not a failure — treating either as a
  // block would be the fabricated judgement this route's product frame bans (and
  // the backend does not require validation before publish, so a client-side
  // block would be inventing a rule the system does not have).
  const blockingIssueCount =
    validationState === 'fresh' ? (validateMutation.data?.error_count ?? 0) : 0;
  const hasBlockingIssues = blockingIssueCount > 0;
  // Empty draft → backend 422; non-DRAFT → terminal (409). Gate publish on both
  // so the operator never hits a server error from the readiness rail — plus the
  // freshly-measured blocking issues above.
  const publishable = isPublishableDraft(status) && rowCount > 0 && !hasBlockingIssues;

  // Next safe action derived ONLY from real draft state (role + row count +
  // whatever the operator's own validate run actually returned). A fresh result
  // IS real draft state, so folding it in is reporting, not fabrication; an
  // absent or stale result contributes nothing and the hint falls back to
  // "validate first".
  const nextActionKey = !canAuthor
    ? 'routes.testPlans.readinessNextReadOnly'
    : rowCount === 0
      ? 'routes.testPlans.readinessNextAddRows'
      : hasBlockingIssues
        ? 'routes.testPlans.readinessNextFixIssues'
        : validationState === 'fresh'
          ? 'routes.testPlans.readinessNextPublish'
          : 'routes.testPlans.readinessNextValidatePublish';

  const publishMutation = useMutation<
    { plan_id: string; published_at?: string | null },
    ApiError,
    void
  >({
    mutationFn: async () => {
      return publishTestPlanDraft(projectId, draftId);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.testPlans.drafts(projectId),
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.testPlans.draft(projectId, draftId),
      });
      // Refresh the authoritative published-plan list (G2 server SSOT) so the
      // chamber measurement starter sees the freshly published plan without a
      // manual reload — server-sourced, cross-browser (no browser-local registry).
      void queryClient.invalidateQueries({
        queryKey: queryKeys.testPlans.publications(projectId),
      });
    },
  });

  // W3-6 M2 — archive (DRAFT→ARCHIVED, terminal). Acknowledgement is stored AS
  // the draft it was granted for, never as a bare boolean, following the
  // `BulkRowsEditor` convention: keying it by identity makes expiry a pure
  // derivation instead of a second state that needs an effect to reset.
  const [archiveAckFor, setArchiveAckFor] = useState<string | null>(null);
  const archiveArmed = archiveAckFor === draftId;

  const archiveMutation = useMutation<{ status: string }, ApiError, void>({
    mutationFn: async () => {
      return archiveTestPlanDraft(projectId, draftId);
    },
    onSuccess: () => {
      // The two reads that now describe a different draft: the project's drafts
      // list (this row's status moved to `archived`) and this draft's detail
      // (which both panels of the workbench render from).
      //
      // `publications` is deliberately NOT invalidated. Copying the publish
      // handler wholesale would refetch the published-plan list on an archive,
      // which asserts that archiving changed what is published — it did not.
      // Archiving is how a draft is retired *without* publishing it.
      void queryClient.invalidateQueries({
        queryKey: queryKeys.testPlans.drafts(projectId),
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.testPlans.draft(projectId, draftId),
      });
      setArchiveAckFor(null);
    },
  });

  return (
    <section aria-labelledby="test-plans-readiness-heading" data-testid="test-plans-readiness">
      <SectionBand
        title={t('routes.testPlans.sectionReadiness')}
        titleId="test-plans-readiness-heading"
      />
      {view !== undefined && (
        <p className="readiness-next" data-testid="test-plans-readiness-next">
          <span className="readiness-next__label">{t('routes.testPlans.readinessNextLabel')}</span>
          <span className="readiness-next__value">{t(nextActionKey)}</span>
        </p>
      )}

      {/* The three validation states are named explicitly. Collapsing them
          ("no issues shown" reading as "validated clean") is how an unvalidated
          or outdated draft gets published as if it had been checked. */}
      {view !== undefined && (
        <p
          className="section-hint"
          data-testid="test-plans-validation-state"
          data-state={validationState}
        >
          {t(`routes.testPlans.validationState.${validationState}`)}
        </p>
      )}

      <Toolbar ariaLabel={t('routes.testPlans.validateButton')}>
        <Button
          type="button"
          variant="primary"
          data-testid="test-plans-validate"
          disabled={validateMutation.isPending}
          onClick={() => validateMutation.mutate(fingerprint)}
        >
          {validateMutation.isPending
            ? t('routes.testPlans.validateBusy')
            : t('routes.testPlans.validateButton')}
        </Button>
      </Toolbar>
      {validateMutation.isError && (
        <ErrorState
          testId="test-plans-validate-error"
          message={describeApiError(validateMutation.error, 'headless', {
            forbidden: t('routes.testPlans.validateForbidden'),
            notFound: t('routes.testPlans.validateNotFound'),
            network: t('routes.testPlans.validateNetwork'),
            default: t('routes.testPlans.validateFailed'),
          })}
        />
      )}
      {validateMutation.isSuccess && (
        <ValidateResult result={validateMutation.data} stale={validationState === 'stale'} />
      )}

      <DraftPublishedDiff projectId={projectId} draftId={draftId} draftRowCount={rowCount} />

      {!canAuthor ? (
        <span data-testid="test-plans-publish-denied">—</span>
      ) : (
        <>
          <Toolbar ariaLabel={t('routes.testPlans.publishButton')}>
            <Button
              type="button"
              variant="primary"
              data-testid="test-plans-publish"
              disabled={!publishable || publishMutation.isPending}
              onClick={() => publishMutation.mutate()}
            >
              {publishMutation.isPending
                ? t('routes.testPlans.publishBusy')
                : t('routes.testPlans.publishButton')}
            </Button>
          </Toolbar>
          {isPublishableDraft(status) && rowCount === 0 && (
            <p data-testid="test-plans-publish-empty-hint" className="section-hint">
              {t('routes.testPlans.publishEmptyHint')}
            </p>
          )}
          {hasBlockingIssues && (
            <p data-testid="test-plans-publish-blocked-hint" className="section-hint">
              {t('routes.testPlans.publishBlockedHint', { errors: blockingIssueCount })}
            </p>
          )}
          {publishMutation.isError && (
            <ErrorState
              testId="test-plans-publish-error"
              message={describeApiError(publishMutation.error, 'headless', {
                forbidden: t('routes.testPlans.publishForbidden'),
                conflict: t('routes.testPlans.publishConflict'),
                notFound: t('routes.testPlans.publishNotFound'),
                network: t('routes.testPlans.publishNetwork'),
                default: t('routes.testPlans.publishFailed'),
              })}
            />
          )}
          {publishMutation.isSuccess && (
            <StatusMessage
              tone="success"
              testId="test-plans-publish-success"
              message={t('routes.testPlans.publishSuccess', {
                plan: publishMutation.data.plan_id,
              })}
            />
          )}

          {/* Archive — offered ONLY while the draft is still DRAFT. A published
              or already-archived draft would 409, and a control that only ever
              fails is a fabricated next action. When the transition completes
              the status leaves DRAFT and this whole block disappears, which is
              also what retires a pending acknowledgement. */}
          {isPublishableDraft(status) && (
            <div data-testid="test-plans-archive-section">
              <Toolbar ariaLabel={t('routes.testPlans.archiveButton')}>
                <Button
                  type="button"
                  variant="danger"
                  data-testid="test-plans-archive"
                  disabled={archiveMutation.isPending}
                  onClick={() => setArchiveAckFor(draftId)}
                >
                  {t('routes.testPlans.archiveButton')}
                </Button>
              </Toolbar>
              {archiveArmed && (
                <div className="draft-archive-confirm" data-testid="test-plans-archive-confirm">
                  {/* States the absence of a restore path as a fact, because
                      the headless API genuinely has no unarchive operation —
                      promising a way back the backend cannot deliver would be
                      the worse failure. */}
                  <p
                    className="draft-archive-confirm__irreversible"
                    data-testid="test-plans-archive-irreversible"
                  >
                    {t('routes.testPlans.archiveIrreversible')}
                  </p>
                  <Toolbar ariaLabel={t('routes.testPlans.archiveConfirm')}>
                    <Button
                      type="button"
                      variant="danger"
                      data-testid="test-plans-archive-confirm-button"
                      disabled={archiveMutation.isPending}
                      onClick={() => archiveMutation.mutate()}
                    >
                      {archiveMutation.isPending
                        ? t('routes.testPlans.archiveBusy')
                        : t('routes.testPlans.archiveConfirm')}
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      data-testid="test-plans-archive-cancel"
                      disabled={archiveMutation.isPending}
                      onClick={() => setArchiveAckFor(null)}
                    >
                      {t('routes.testPlans.archiveKeep')}
                    </Button>
                  </Toolbar>
                </div>
              )}
            </div>
          )}
          {archiveMutation.isError && (
            <ErrorState
              testId="test-plans-archive-error"
              message={describeApiError(archiveMutation.error, 'headless', {
                forbidden: t('routes.testPlans.archiveForbidden'),
                conflict: t('routes.testPlans.archiveConflict'),
                notFound: t('routes.testPlans.archiveNotFound'),
                network: t('routes.testPlans.archiveNetwork'),
                default: t('routes.testPlans.archiveFailed'),
              })}
            />
          )}
          {archiveMutation.isSuccess && (
            <StatusMessage
              tone="success"
              testId="test-plans-archive-success"
              message={t('routes.testPlans.archiveSuccess')}
            />
          )}
        </>
      )}
    </section>
  );
}
