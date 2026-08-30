import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { fetchPublishedTestPlans } from '@/api/headless-client';
import { PERMISSION_TEST_PLAN_READ } from '@/api/permissions';
import {
  fetchSampleInventory,
  startChamberMeasurement,
  type ChamberAvailabilityEnvelope,
  type ChamberMeasurementSnapshot,
} from '@/api/platform-client';
import { queryKeys } from '@/api/query-config';
import { useAuthSession } from '@/auth/route-guard';
import { useT } from '@/i18n';
import { type ApiError } from '@/shared/api-error';
import { isValidProjectId } from '@/shared/project-id';
import { ProjectSelectField } from '@/shared/ProjectSelectField';
import {
  describeApiError,
  Button,
  EmptyState,
  ErrorState,
  FieldGroup,
  SectionBand,
  StatusMessage,
  Toolbar,
} from '@/ui';
import { liveRegionProps } from '@/ui/live-region';

import { ChamberProgress } from './ChamberProgress';
import { ChamberNextActionLinks } from './next-actions';
import { classifyStartFailure, START_FAILURE_GUIDANCE } from './start-failure';
import { isChamberUnavailable, isStartableChamber } from './status';
import { orDash } from './util';

export function MeasurementStarter({
  chambers,
}: {
  /**
   * The WHOLE availability list, not just the startable subset (M1/M2).
   *
   * The selectable options are still the startable ones, derived here — but a
   * start failure has to be explained against the row for the chamber the
   * operator actually submitted, and that row is precisely the one that has
   * *stopped* being startable (a chamber that went offline while the form was
   * being filled is the realistic path to a 409). Handing this component only
   * the startable subset would delete the evidence exactly when it is needed.
   */
  readonly chambers: readonly ChamberAvailabilityEnvelope[];
}): JSX.Element {
  const { t } = useT();
  const [searchParams] = useSearchParams();
  const initialProjectId = (searchParams.get('project') ?? '').trim();
  const initialSampleId = (searchParams.get('sample') ?? '').trim();
  const auth = useAuthSession();
  // The published-plan datalist is sourced from the headless publications read,
  // which the backend gates with `test_plan:read`. The project-scoped roles a
  // chamber operator holds (`project_viewer`/`project_engineer`/`project_admin`)
  // grant only `platform:read|claim|admin` per the rbac_role_grants SSOT in
  // docs/platform/central_db_schema.v1.json — never `test_plan:read` (these are
  // distinct from the headless `platform_rbac.py` seed roles). So a
  // `platform:claim` operator may lack it; gate the lookup on the actual
  // permission rather than assuming claim implies it. Without it the query never
  // fires (no 403, no round trip) and the operator falls back to free-text
  // manual plan entry.
  const canReadPublications =
    auth.kind === 'authenticated' && auth.principal.permissions.includes(PERMISSION_TEST_PLAN_READ);
  const queryClient = useQueryClient();
  const [chamberId, setChamberId] = useState('');
  const [projectId, setProjectId] = useState(initialProjectId);
  const [planId, setPlanId] = useState('');
  // ADR-0017 Phase 3 — 측정 직전에 고른 샘플번호((분야 × 기술) 맥락). 비우면 노드의
  // Excel "Save Data" 샘플 그대로 사용(byte-identical). 웹의 Save Data 편집 대체.
  const [sampleId, setSampleId] = useState(initialSampleId);
  const [activeChamberId, setActiveChamberId] = useState<string | null>(null);

  const startMutation = useMutation<ChamberMeasurementSnapshot, ApiError, void>({
    mutationFn: () =>
      startChamberMeasurement(chamberId, {
        published_plan_id: planId.trim() === '' ? null : planId.trim(),
        project_id: projectId.trim(),
        sample_id: sampleId.trim(),
      }),
    onSuccess: (snapshot) => {
      setActiveChamberId(snapshot.chamber_id);
      void queryClient.invalidateQueries({ queryKey: queryKeys.chambers.list() });
    },
  });

  // Published plans for the entered project (G2 server SSOT — authoritative
  // across browsers/sessions, replacing the removed browser-local registry),
  // offered as datalist suggestions on the free-text plan input. The query is
  // gated by (1) the `test_plan:read` permission the backend endpoint requires
  // and (2) a client-side UUID check (shared `isValidProjectId` SSOT) so neither
  // a missing permission nor a non-uuid ever costs a round trip; manual plan
  // entry stays available regardless.
  const publications = useQuery({
    queryKey: queryKeys.testPlans.publications(projectId.trim()),
    enabled: canReadPublications && isValidProjectId(projectId),
    queryFn: async () => {
      return fetchPublishedTestPlans(projectId.trim());
    },
  });
  const recentPlans = publications.data?.publications ?? [];

  // The chamber selector must use the web-authoritative active inventory. A
  // free-text sample value can name a deleted sample, a sample from another
  // project, or a value the central API has never seen. Keep the query on the
  // platform gateway and make the start button depend on its successful result.
  const samples = useInfiniteQuery({
    queryKey: queryKeys.sampleInventory.list({
      projectId: projectId.trim(),
      status: 'active',
      limit: 100,
    }),
    queryFn: ({ pageParam }) =>
      fetchSampleInventory({
        projectId: projectId.trim(),
        status: 'active',
        limit: 100,
        ...(pageParam === null ? {} : { after: pageParam }),
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: isValidProjectId(projectId),
  });
  const activeSamples =
    samples.data?.pages
      .flatMap((page) => page.items)
      .filter((sample) => sample.status === 'active') ?? [];
  const selectedSampleIsActive = activeSamples.some(
    (sample) => sample.sample_id === sampleId.trim(),
  );
  const sampleSelectionReady =
    isValidProjectId(projectId) &&
    !samples.isPending &&
    !samples.isError &&
    activeSamples.length > 0 &&
    selectedSampleIsActive;

  // Changing the chamber/plan selection clears a prior attempt's success/error
  // banner so a stale outcome does not linger over a new selection (P10 mutation
  // reset). `mutation.reset()` is a no-op when idle.
  const resetOutcome = (): void => {
    if (startMutation.isSuccess || startMutation.isError) startMutation.reset();
  };

  const ready =
    chamberId.trim() !== '' &&
    projectId.trim() !== '' &&
    sampleSelectionReady &&
    !startMutation.isPending;

  const startable = useMemo(() => chambers.filter(isStartableChamber), [chambers]);
  // The row for the submitted chamber, as the availability view currently reads
  // it — the only place a 409's cause can come from (see `classifyStartFailure`).
  const selectedChamber = useMemo(
    () => chambers.find((c) => c.chamber_id === chamberId),
    [chambers, chamberId],
  );
  const selectedIsUnavailable =
    selectedChamber !== undefined && isChamberUnavailable(selectedChamber);

  return (
    <section aria-labelledby="chambers-start-heading">
      <SectionBand title={t('routes.chambers.sectionStart')} titleId="chambers-start-heading" />
      {startable.length === 0 ? (
        <EmptyState
          testId="chambers-none-startable"
          title={t('routes.chambers.noneStartableTitle')}
          description={t('routes.chambers.noneStartableDescription')}
          action={
            <div className="chambers-next__actions">
              <ChamberNextActionLinks keys={['diagnostics']} idPrefix="chambers-none-startable" />
            </div>
          }
        />
      ) : (
        <form
          aria-label={t('routes.chambers.startFormAria')}
          data-testid="chambers-start-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (ready) startMutation.mutate();
          }}
        >
          <Toolbar ariaLabel={t('routes.chambers.startFormAria')} inline>
            <FieldGroup
              label={t('routes.chambers.startChamberLabel')}
              htmlFor="chambers-start-select"
            >
              <select
                id="chambers-start-select"
                data-testid="chambers-start-select"
                value={chamberId}
                onChange={(e) => {
                  setChamberId(e.target.value);
                  resetOutcome();
                }}
              >
                <option value="">{t('routes.chambers.startChamberPlaceholder')}</option>
                {startable.map((chamber) => (
                  <option key={chamber.chamber_id} value={chamber.chamber_id}>
                    {chamber.name}
                  </option>
                ))}
              </select>
            </FieldGroup>
            <ProjectSelectField
              label={t('routes.chambers.startProjectLabel')}
              value={projectId}
              onChange={(value) => {
                setProjectId(value);
                setSampleId('');
                resetOutcome();
              }}
              selectId="chambers-start-project-select"
              selectTestId="chambers-start-project-select"
            />
            <FieldGroup label={t('routes.chambers.startPlanLabel')} htmlFor="chambers-start-plan">
              <input
                id="chambers-start-plan"
                data-testid="chambers-start-plan"
                value={planId}
                placeholder={t('routes.chambers.startPlanPlaceholder')}
                list={recentPlans.length > 0 ? 'chambers-start-plan-options' : undefined}
                onChange={(e) => {
                  setPlanId(e.target.value);
                  resetOutcome();
                }}
              />
              {recentPlans.length > 0 && (
                <datalist
                  id="chambers-start-plan-options"
                  data-testid="chambers-start-plan-options"
                >
                  {recentPlans.map((plan) => (
                    <option key={plan.plan_id} value={plan.plan_id}>
                      {t('routes.chambers.startPlanOptionLabel', {
                        draft: plan.draft_id,
                        publishedAt: orDash(plan.published_at ?? null),
                      })}
                    </option>
                  ))}
                </datalist>
              )}
            </FieldGroup>
            <FieldGroup
              label={t('routes.chambers.startSampleLabel')}
              htmlFor="chambers-start-sample"
            >
              <select
                id="chambers-start-sample"
                data-testid="chambers-start-sample"
                value={selectedSampleIsActive ? sampleId : ''}
                required
                disabled={
                  !isValidProjectId(projectId) ||
                  samples.isPending ||
                  samples.isError ||
                  activeSamples.length === 0
                }
                onChange={(e) => {
                  setSampleId(e.target.value);
                  resetOutcome();
                }}
              >
                <option value="">{t('routes.chambers.startSamplePlaceholder')}</option>
                {activeSamples.map((sample) => (
                  <option key={sample.sample_id} value={sample.sample_id}>
                    {sample.sample_number ??
                      sample.label_number ??
                      sample.sample_code ??
                      sample.sample_id}
                  </option>
                ))}
              </select>
              {samples.isPending && (
                <span role="status" data-testid="chambers-start-sample-loading">
                  {t('routes.sampleInventory.loading')}
                </span>
              )}
              {samples.isError && (
                <span
                  {...liveRegionProps('blockingFailure')}
                  data-testid="chambers-start-sample-error"
                >
                  {t('routes.sampleInventory.loadFailed')}
                </span>
              )}
              {!samples.isPending && !samples.isError && activeSamples.length === 0 && (
                <span role="status" data-testid="chambers-start-sample-empty">
                  {t('routes.sampleInventory.emptyTitle')}
                </span>
              )}
              {samples.hasNextPage && (
                <Button
                  type="button"
                  variant="secondary"
                  data-testid="chambers-start-sample-load-more"
                  disabled={samples.isFetchingNextPage}
                  onClick={() => void samples.fetchNextPage()}
                >
                  {samples.isFetchingNextPage
                    ? t('routes.sampleInventory.loadingMore')
                    : t('routes.sampleInventory.loadMore')}
                </Button>
              )}
            </FieldGroup>
            <Button
              type="submit"
              variant="primary"
              data-testid="chambers-start-submit"
              disabled={!ready}
            >
              {startMutation.isPending
                ? t('routes.chambers.startBusy')
                : t('routes.chambers.startButton')}
            </Button>
          </Toolbar>
        </form>
      )}

      {startMutation.isError && (
        <>
          <ErrorState
            testId="chambers-start-error"
            /* The message axis mirrors the recovery axis arm for arm (M1). The
               404 and 503 arms were previously unspecialised, so `describeApiError`
               answered a de-registered chamber and an unreachable node with copy
               *less* specific than a 500 — while the hint below named the cause.
               The conflict copy splits on the same read-side evidence the hint
               uses, so the two can never tell different stories. */
            message={describeApiError(startMutation.error, 'platform', {
              forbidden: t('routes.chambers.startForbidden'),
              notFound: t('routes.chambers.startNotFound'),
              conflict: selectedIsUnavailable
                ? t('routes.chambers.startConflictOffline')
                : t('routes.chambers.startConflict'),
              serviceUnavailable: t('routes.chambers.startUnavailable'),
              network: t('routes.chambers.startNetwork'),
              default: t('routes.chambers.startFailed'),
            })}
          />
          <StartFailureRecovery
            error={startMutation.error}
            projectId={projectId}
            chamber={selectedChamber}
          />
        </>
      )}
      {startMutation.isSuccess && (
        <StatusMessage
          tone="success"
          testId="chambers-start-success"
          message={t('routes.chambers.startSuccess')}
        />
      )}

      {activeChamberId !== null && (
        <ChamberProgress chamberId={activeChamberId} projectId={projectId} />
      )}
    </section>
  );
}

/**
 * Actionable recovery guidance shown under a start ErrorState. The message says
 * *what* failed (via `describeApiError`); this says *what to do* — a recovery
 * sentence keyed off the chambers-local `classifyStartFailure` SSOT, plus the
 * hub-backed follow-up screens that help (none for a permission failure, whose
 * only fix is an admin grant).
 */
function StartFailureRecovery({
  error,
  projectId,
  chamber,
}: {
  readonly error: unknown;
  readonly projectId: string;
  /** Availability row for the submitted chamber — the 409 cause evidence. */
  readonly chamber?: ChamberAvailabilityEnvelope | undefined;
}): JSX.Element {
  const { t } = useT();
  const guidance = START_FAILURE_GUIDANCE[classifyStartFailure(error, chamber)];
  return (
    <div className="chambers-next" data-testid="chambers-start-recovery">
      <p className="section-hint" data-testid="chambers-start-recovery-text">
        {t(guidance.guidanceKey)}
      </p>
      {guidance.actions.length > 0 && (
        <div className="chambers-next__actions">
          <ChamberNextActionLinks
            keys={guidance.actions}
            projectId={projectId}
            idPrefix="chambers-start-recovery"
          />
        </div>
      )}
    </div>
  );
}
