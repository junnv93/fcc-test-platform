import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { createTestPlanDraft, fetchTestPlanDrafts } from '@/api/headless-client';
import { PERMISSION_TEST_PLAN_AUTHOR } from '@/api/permissions';
import { queryKeys } from '@/api/query-config';
import { useAuthSession } from '@/auth/route-guard';
import { useT } from '@/i18n';
import { type ApiError } from '@/shared/api-error';
import { isValidProjectId } from '@/shared/project-id';
import { ProjectSelectField } from '@/shared/ProjectSelectField';
import { projectScopedHref, ROUTE_PATHS } from '@/shared/route-links';
import {
  BlockSkeleton,
  Button,
  Card,
  DataTable,
  describeApiError,
  EmptyState,
  ErrorState,
  SectionBand,
  StatusMessage,
  Toolbar,
  WorkbenchLayout,
} from '@/ui';

import { DraftDetail } from './DraftDetail';
import { DraftReadinessPanel } from './DraftReadinessPanel';
import { DraftRow } from './DraftRow';
import { GenerateTestPlanForm } from './GenerateTestPlanForm';
import { ImportExcelForm } from './ImportExcelForm';
import {
  DRAFT_STATUS_VALUES,
  draftStatusLabel,
  type DraftStatusValue,
  normalizeDraftStatusFilter,
} from './status';

export function TestPlansWorkbench(): JSX.Element {
  const { t } = useT();
  const [searchParams, setSearchParams] = useSearchParams();
  // Normalize once at the source: the query key, the fetch URL, the `enabled`
  // gate, and the per-row invalidation all derive from this single trimmed
  // value, so a crafted `?project=<uuid>%20` can't drift the cache key (raw)
  // away from the fetched/invalidated id (trimmed) and silently miss a refetch.
  const projectId = (searchParams.get('project') ?? '').trim();
  // Which draft's detail is open. Stored in the URL so direct links such as
  // `/test-plans?project=<id>&draft=<draft-id>` restore the workbench state.
  // The detail panel (`DraftDetail`) reads it through the same query-key factory
  // the publish invalidation targets, so a publish refetches the open detail.
  const selectedDraftId = (searchParams.get('draft') ?? '').trim() || null;
  // UX Slice 2 — the drafts-list status filter, synced through the URL so a
  // `/test-plans?project=<id>&status=published` link restores the filtered view.
  // `null` means "all"; an unknown/stale `?status=` degrades to "all" rather
  // than hiding every row. Orthogonal to project/draft (a view preference), so
  // it is intentionally preserved across a project change — unlike `draft`,
  // which is project-scoped data and is cleared in `setProject`.
  const statusFilter = normalizeDraftStatusFilter(searchParams.get('status') ?? '');

  const setProject = useCallback(
    (value: string): void => {
      const normalized = value.trim();
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (normalized === '') next.delete('project');
          else next.set('project', normalized);
          // A different project's drafts make the prior selection meaningless —
          // clear the detail query param so it can't fetch a cross-project draft.
          next.delete('draft');
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const setSelectedDraftId = useCallback(
    (value: string | null): void => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value === null || value.trim() === '') next.delete('draft');
          else next.set('draft', value.trim());
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const setStatusFilter = useCallback(
    (value: DraftStatusValue | null): void => {
      setSearchParams(
        (prev) => {
          // Preserve project/draft — this writer owns only the `status` key, so
          // filtering never clobbers Slice 1 state.
          const next = new URLSearchParams(prev);
          if (value === null) next.delete('status');
          else next.set('status', value);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const auth = useAuthSession();
  const canAuthor =
    auth.kind === 'authenticated' &&
    auth.principal.permissions.includes(PERMISSION_TEST_PLAN_AUTHOR);
  // `created_by` provenance for a new draft is the authenticated principal's
  // subject (server re-derives identity from the trusted header — this is the
  // display/audit value, never an authz decision input).
  const createdBy = auth.kind === 'authenticated' ? auth.principal.subject : '';

  const queryClient = useQueryClient();
  // Create a fresh empty DRAFT for this project. Generated rows use the current
  // preview/async-generation flow below; manual authoring starts with zero
  // rows, then the server-owned draft snapshot is returned. On success the
  // drafts list refetches and the new draft auto-opens.
  const createMutation = useMutation<{ draft_id: string }, ApiError, void>({
    mutationFn: async () => {
      return createTestPlanDraft(projectId, createdBy);
    },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.testPlans.drafts(projectId),
      });
      setSelectedDraftId(data.draft_id);
    },
  });

  const drafts = useQuery({
    queryKey: queryKeys.testPlans.drafts(projectId),
    enabled: isValidProjectId(projectId),
    queryFn: async () => {
      return fetchTestPlanDrafts(projectId);
    },
  });
  const rows = drafts.data?.drafts ?? [];
  // Apply the Slice 2 status filter. Matching mirrors the row status labels
  // (trim + lowercase). `null` = show all.
  const visibleRows =
    statusFilter === null
      ? rows
      : rows.filter((row) => row.status.trim().toLowerCase() === statusFilter);
  // Policy — a draft opened via `?draft=` (its detail panel stays rendered) can
  // be excluded from the list by an active `?status=` filter. Silently leaving
  // the detail open with no matching row reads as a bug ("why is this panel
  // showing a draft that isn't in the list?"). Rather than auto-mutating URL
  // state (auto-clearing the filter would drop the operator's view preference;
  // auto-deselecting would drop the deep-linked draft), surface the conflict
  // explicitly and let the operator reveal it — a user-initiated `status` clear
  // that preserves project + draft. `true` only when the selected draft exists
  // in the fetched set but the filter hides it (a stale/cross-project `?draft=`
  // that isn't in `rows` is DraftDetail's own 404 concern, not a filter clash).
  const selectedDraftHiddenByFilter =
    selectedDraftId !== null &&
    statusFilter !== null &&
    rows.some((row) => row.draft_id === selectedDraftId) &&
    !visibleRows.some((row) => row.draft_id === selectedDraftId);

  return (
    <>
      <ProjectContextBar projectId={projectId} selectedDraftId={selectedDraftId} />
      <section aria-labelledby="test-plans-project-heading">
        <SectionBand
          title={t('routes.testPlans.projectSection')}
          titleId="test-plans-project-heading"
        />
        <Toolbar ariaLabel={t('routes.testPlans.projectLookupAria')}>
          <ProjectSelectField
            value={projectId}
            onChange={setProject}
            selectId="test-plans-project-select"
            selectTestId="test-plans-project-select"
          />
        </Toolbar>
      </section>
      <TestPlansNextActions projectId={projectId} />

      {/* §5① 조회 vs 생성 분리 — "새 항목표 만들기"(생성, 작성 권한자만)를
          "작성 중 항목표"(조회) 목록과 별도 섹션으로 나눠 두 작업의 의도를
          명확히 한다. 생성 갈래는 셋이고 만들어 주는 것이 서로 다르다 —
          수기(행 0) / Excel(워크북의 행) / 조건 생성(조건에서 파생된 후보행).
          각 갈래가 자기 설명을 달고 있어야 선택 **전에** 구분된다. */}
      {isValidProjectId(projectId) && canAuthor && (
        <section
          aria-labelledby="test-plans-create-heading"
          data-testid="test-plans-create-bar"
          className="test-plans-create-bar"
        >
          <SectionBand
            title={t('routes.testPlans.sectionCreate')}
            titleId="test-plans-create-heading"
          />
          <p className="section-hint">{t('routes.testPlans.createSectionDescription')}</p>
          <div className="test-plans-create-grid">
            <Card
              as="article"
              variant="action"
              className="test-plans-create-card test-plans-create-card--primary"
              testId="test-plans-create-card-manual"
            >
              <h3 className="test-plans-create-card__title">
                {t('routes.testPlans.createButton')}
              </h3>
              <p className="test-plans-create-card__description">
                {t('routes.testPlans.createSectionDescription')}
              </p>
              <Button
                type="button"
                variant="primary"
                data-testid="test-plans-create"
                disabled={createMutation.isPending}
                onClick={() => createMutation.mutate()}
              >
                {createMutation.isPending
                  ? t('routes.testPlans.createBusy')
                  : t('routes.testPlans.createButton')}
              </Button>
              {createMutation.isError && (
                <ErrorState
                  testId="test-plans-create-error"
                  message={describeApiError(createMutation.error, 'headless', {
                    forbidden: t('routes.testPlans.createForbidden'),
                    network: t('routes.testPlans.createNetwork'),
                    default: t('routes.testPlans.createFailed'),
                  })}
                />
              )}
            </Card>
            <div className="test-plans-create-card" data-testid="test-plans-create-card-import">
              <ImportExcelForm projectId={projectId} />
            </div>
            <div className="test-plans-create-card" data-testid="test-plans-create-card-generator">
              <GenerateTestPlanForm projectId={projectId} onGenerated={setSelectedDraftId} />
            </div>
          </div>
        </section>
      )}

      {/* §2 Workbench — a token-governed grid (main editor column + readiness
          rail). With no draft selected the rail is absent and the list spans the
          full width; selecting a draft opens the editor (main) and the readiness
          rail (right) side-by-side on desktop, stacked below `--bp-lg`. */}
      {isValidProjectId(projectId) && (
        <WorkbenchLayout
          className="test-plans-workbench"
          mainLabel={t('routes.testPlans.sectionDrafts')}
          railLabel={t('routes.testPlans.readinessAria')}
          testId="test-plans-workbench"
          hasRail={selectedDraftId !== null}
          hasSelection={selectedDraftId !== null}
          main={
            <div className="test-plans-workbench__main">
              <section aria-labelledby="test-plans-drafts-heading">
                <SectionBand
                  title={t('routes.testPlans.sectionDrafts')}
                  titleId="test-plans-drafts-heading"
                  meta={
                    drafts.isSuccess
                      ? t('routes.testPlans.draftCount', { count: visibleRows.length })
                      : undefined
                  }
                />
                {drafts.isPending && <BlockSkeleton lines={4} testId="test-plans-loading" />}
                {drafts.isError && (
                  <ErrorState
                    testId="test-plans-error"
                    message={describeApiError(drafts.error, 'headless', {
                      forbidden: t('routes.testPlans.readForbidden'),
                      notFound: t('routes.testPlans.readNotFound'),
                      network: t('routes.testPlans.readNetwork'),
                      default: t('routes.testPlans.readFailed'),
                    })}
                  />
                )}
                {drafts.isSuccess && rows.length === 0 && (
                  <EmptyState
                    testId="test-plans-empty"
                    title={t('routes.testPlans.emptyTitle')}
                    description={t('routes.testPlans.emptyDescription')}
                    action={
                      canAuthor ? (
                        <Button
                          type="button"
                          variant="primary"
                          data-testid="test-plans-empty-create"
                          disabled={createMutation.isPending}
                          onClick={() => createMutation.mutate()}
                        >
                          {createMutation.isPending
                            ? t('routes.testPlans.createBusy')
                            : t('routes.testPlans.createButton')}
                        </Button>
                      ) : undefined
                    }
                  />
                )}
                {drafts.isSuccess && rows.length > 0 && (
                  <>
                    {/* §Slice 2 — status filter. Options + validation both derive
                      from DRAFT_STATUS_VALUES so the control can never offer a
                      value the domain doesn't model. */}
                    <Toolbar ariaLabel={t('routes.testPlans.filterAria')}>
                      <label
                        className="test-plans-status-filter"
                        htmlFor="test-plans-status-filter"
                      >
                        <span>{t('routes.testPlans.filterLabel')}</span>
                        <select
                          id="test-plans-status-filter"
                          data-testid="test-plans-status-filter"
                          value={statusFilter ?? ''}
                          onChange={(event) =>
                            setStatusFilter(normalizeDraftStatusFilter(event.target.value))
                          }
                        >
                          <option value="">{t('routes.testPlans.filterAll')}</option>
                          {DRAFT_STATUS_VALUES.map((value) => (
                            <option key={value} value={value}>
                              {draftStatusLabel(t, value)}
                            </option>
                          ))}
                        </select>
                      </label>
                    </Toolbar>
                    {selectedDraftHiddenByFilter && (
                      <div
                        className="test-plans-hidden-selection"
                        data-testid="test-plans-hidden-selection"
                      >
                        <StatusMessage
                          tone="info"
                          testId="test-plans-hidden-selection-note"
                          message={t('routes.testPlans.hiddenSelectionNotice')}
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          data-testid="test-plans-hidden-selection-clear"
                          onClick={() => setStatusFilter(null)}
                        >
                          {t('routes.testPlans.hiddenSelectionClear')}
                        </Button>
                      </div>
                    )}
                    {visibleRows.length === 0 ? (
                      <EmptyState
                        testId="test-plans-filter-empty"
                        title={t('routes.testPlans.filterEmptyTitle')}
                        description={t('routes.testPlans.filterEmptyDescription')}
                        action={
                          <Button
                            type="button"
                            variant="ghost"
                            data-testid="test-plans-filter-empty-clear"
                            onClick={() => setStatusFilter(null)}
                          >
                            {t('routes.testPlans.hiddenSelectionClear')}
                          </Button>
                        }
                      />
                    ) : (
                      <DataTable
                        testId="test-plans-table"
                        caption={t('routes.testPlans.tableCaption')}
                        head={
                          <thead>
                            <tr>
                              <th scope="col">{t('routes.testPlans.colDraftId')}</th>
                              <th scope="col">{t('routes.testPlans.colStatus')}</th>
                              <th scope="col">{t('routes.testPlans.colRowCount')}</th>
                              <th scope="col">{t('routes.testPlans.colUpdatedAt')}</th>
                              <th scope="col">{t('routes.testPlans.colManage')}</th>
                            </tr>
                          </thead>
                        }
                        body={
                          <tbody>
                            {visibleRows.map((row) => (
                              <DraftRow
                                key={row.draft_id}
                                draftId={row.draft_id}
                                status={row.status}
                                rowCount={row.row_count}
                                updatedAt={row.updated_at}
                                selected={selectedDraftId === row.draft_id}
                                onSelect={() => setSelectedDraftId(row.draft_id)}
                              />
                            ))}
                          </tbody>
                        }
                      />
                    )}
                  </>
                )}
              </section>
              {selectedDraftId !== null && (
                // W2-C M2/M3 — `key` makes the draft id part of the panel's
                // IDENTITY, not just a prop. Without it, switching drafts reuses
                // the same component instance, so the previous draft's unsaved
                // bulk-CSV override and its validate result survive underneath the
                // new draft: the operator could import draft A's rows over draft B
                // (replace-all), or read A's "no issues" as B's judgement. Remount
                // is the only way to state "this is different data" for state that
                // is legitimately local (unsaved edits must not be shared).
                <DraftDetail
                  key={selectedDraftId}
                  projectId={projectId}
                  draftId={selectedDraftId}
                  canAuthor={canAuthor}
                />
              )}
            </div>
          }
          rail={
            selectedDraftId !== null ? (
              <div className="test-plans-workbench__rail">
                {/* Same identity rule as the editor panel — the validate result
                    is per-draft local state and must not outlive its draft. */}
                <DraftReadinessPanel
                  key={selectedDraftId}
                  projectId={projectId}
                  draftId={selectedDraftId}
                  canAuthor={canAuthor}
                />
              </div>
            ) : undefined
          }
        />
      )}
    </>
  );
}

function TestPlansNextActions({ projectId }: { readonly projectId: string }): JSX.Element {
  const { t } = useT();
  return (
    <section
      className="test-plans-next-actions"
      aria-labelledby="test-plans-next-heading"
      data-testid="test-plans-next-actions"
    >
      <SectionBand title={t('routes.testPlans.nextSection')} titleId="test-plans-next-heading" />
      <p className="test-plans-next-actions__state" data-testid="test-plans-next-state">
        {isValidProjectId(projectId)
          ? t('routes.testPlans.selectedProject', { project: projectId })
          : t('routes.testPlans.noProjectSelected')}
      </p>
      <div className="test-plans-next-actions__links">
        <Link
          to={projectScopedHref(ROUTE_PATHS.inventory, projectId)}
          className="test-plans-next-actions__link"
          data-testid="test-plans-next-inventory"
        >
          {t('routes.testPlans.nextInventory')}
        </Link>
        <Link
          to={projectScopedHref(ROUTE_PATHS.projects, projectId)}
          className="test-plans-next-actions__link"
          data-testid="test-plans-next-projects"
        >
          {t('routes.testPlans.nextProjects')}
        </Link>
        <Link
          to={projectScopedHref(ROUTE_PATHS.chambers, projectId)}
          className="test-plans-next-actions__link"
          data-testid="test-plans-next-chambers"
        >
          {t('routes.testPlans.nextChambers')}
        </Link>
        <Link
          to={projectScopedHref(ROUTE_PATHS.reports, projectId)}
          className="test-plans-next-actions__link"
          data-testid="test-plans-next-reports"
        >
          {t('routes.testPlans.nextReports')}
        </Link>
      </div>
      <p className="section-hint">{t('routes.testPlans.nextHint')}</p>
    </section>
  );
}

function ProjectContextBar({
  projectId,
  selectedDraftId,
}: {
  readonly projectId: string;
  readonly selectedDraftId: string | null;
}): JSX.Element {
  const { t } = useT();
  const hasProject = isValidProjectId(projectId);
  return (
    <section
      className="project-context-bar"
      aria-label={t('routes.testPlans.projectContextAria')}
      data-testid="test-plans-project-context"
      data-state={hasProject ? 'ready' : 'missing'}
    >
      <div className="project-context-bar__item">
        <span className="project-context-bar__label">
          {t('routes.testPlans.projectContextProject')}
        </span>
        {hasProject ? (
          <code className="project-context-bar__value" data-testid="test-plans-context-project">
            {projectId}
          </code>
        ) : (
          <span
            className="project-context-bar__empty"
            data-testid="test-plans-context-project-empty"
          >
            {t('routes.testPlans.projectContextMissing')}
          </span>
        )}
      </div>
      <div className="project-context-bar__item">
        <span className="project-context-bar__label">
          {t('routes.testPlans.projectContextDraft')}
        </span>
        {selectedDraftId !== null ? (
          <code className="project-context-bar__value" data-testid="test-plans-context-draft">
            {selectedDraftId}
          </code>
        ) : (
          <span className="project-context-bar__empty" data-testid="test-plans-context-draft-empty">
            {t('routes.testPlans.projectContextNoDraft')}
          </span>
        )}
      </div>
    </section>
  );
}
