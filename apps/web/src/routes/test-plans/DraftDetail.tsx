import { useQuery } from '@tanstack/react-query';

import { type components } from '@/api/generated/headless-api.types';
import { fetchTestPlanDraft, removeTestPlanDraftRow } from '@/api/headless-client';
import { queryKeys } from '@/api/query-config';
import { useT } from '@/i18n';
import { useOptimisticMutation } from '@/shared/use-optimistic-mutation';
import {
  BlockSkeleton,
  Button,
  DataTable,
  describeApiError,
  draftStatusKind,
  EmptyState,
  ErrorState,
  MetricStrip,
  SectionBand,
  StatusBadge,
  VirtualizedTable,
  type MetricStripItem,
} from '@/ui';

import { AddRowForm } from './AddRowForm';
import { BulkRowsEditor } from './BulkRowsEditor';
import {
  draftRowCellValues,
  DraftRowItem,
  removeRowLabel,
  type DraftRowActions,
} from './DraftRowItem';
import { ExportDraftButton } from './ExportDraftButton';
import { draftStatusLabel, isPublishableDraft } from './status';
import { formatCapabilityPath, orDash } from './util';

type DraftRowView = components['schemas']['TestPlanDraftRowView'];
type DraftListView = components['schemas']['ListTestPlanDraftsResponse'];
type DraftView = components['schemas']['TestPlanDraftView'];
type RemoveRowResponse = components['schemas']['RemoveTestPlanDraftRowResponse'];

/**
 * Column i18n keys in render order — the ninth is the manage cell. Both render
 * paths (the `<table>` and the windowed div-table) build their header from this
 * one list, so a column added to one can never be missing from the other.
 */
const DRAFT_ROW_COLUMN_KEYS = [
  'routes.testPlans.colRowId',
  'routes.testPlans.colCapabilityPath',
  'routes.testPlans.colTestType',
  'routes.testPlans.colModeFamily',
  'routes.testPlans.colAntenna',
  'routes.testPlans.colTone',
  'routes.testPlans.colLocation',
  'routes.testPlans.colOrigin',
  'routes.testPlans.colRowManage',
] as const;

// Above this loaded-row count the plain table is replaced by a windowed list so
// the DOM stays bounded — a published FCC test plan runs to 16,000+ items, which
// is 144,000 table cells the browser would otherwise lay out at once. Mirrors
// `sessions.tsx`'s VIRTUAL_ATTEMPT_ROW_THRESHOLD; below the threshold the
// `<table>` keeps its responsive fold and semantics.
const VIRTUAL_DRAFT_ROW_THRESHOLD = 500;
// Seed row height (px) — matches --row-height (comfortable). `measureElement`
// corrects it per row after mount, so this only sizes the first window.
const VIRTUAL_DRAFT_ROW_ESTIMATE = 36;

/**
 * Editor panel for the selected draft — the main workbench surface (Phase 2/4).
 * It owns the draft metadata strip, the export action, and the row-editing
 * surface: the rows table (primary), the add-row form (adjacent to the table),
 * and the bulk CSV editor (secondary — disclosed inside `BulkRowsEditor`).
 *
 * Publish-readiness (validate / published diff / publish) lives in the sibling
 * `DraftReadinessPanel` rail, NOT here, so the editor stays focused on row data.
 *
 * The read key is `queryKeys.testPlans.draft(projectId, draftId)` — the exact
 * key the publish/validate/add-row mutations invalidate (and the readiness rail
 * also subscribes to), so any mutation refetches this panel in lockstep.
 */
export function DraftDetail({
  projectId,
  draftId,
  canAuthor,
}: {
  readonly projectId: string;
  readonly draftId: string;
  readonly canAuthor: boolean;
}): JSX.Element {
  const { t } = useT();
  const detailKey = queryKeys.testPlans.draft(projectId, draftId);
  const draftsKey = queryKeys.testPlans.drafts(projectId);

  const detail = useQuery({
    queryKey: detailKey,
    queryFn: async () => {
      return fetchTestPlanDraft(projectId, draftId);
    },
  });

  const view = detail.data;
  const detailStatus = view?.status ?? '';
  const detailRows = view?.rows ?? [];
  // Only a loaded DRAFT-status draft is editable — published/archived are
  // terminal (the backend would 409 a mutation). The add-row form and per-row
  // remove affordance share this gate so the UI never offers an edit the server
  // would reject.
  const editable = canAuthor && view !== undefined && isPublishableDraft(detailStatus);
  const capabilityPathSuggestions = [
    ...new Set(
      detailRows
        .map((row) => formatCapabilityPath(row.capability_path))
        .filter((value) => value.trim() !== ''),
    ),
  ];

  // W2-C M4 — ONE remove mutation for the whole table, not one per row.
  //
  // `TVariables` is the `draft_row_id`, which is what preserves the per-row
  // isolation the old per-row hook provided: React Query keeps `variables`
  // alongside the pending/error state, so "which row is busy" and "which row
  // failed" are read off the mutation itself rather than inferred. Invalidation
  // targets the same two keys the per-row version did (detail + the project's
  // drafts list, whose `row_count` summary shifts).
  const removeMutation = useOptimisticMutation<RemoveRowResponse, number, DraftView>({
    mutationFn: async (draftRowId) => {
      return removeTestPlanDraftRow(projectId, draftId, draftRowId);
    },
    queryKey: detailKey,
    optimisticUpdate: (current, draftRowId) =>
      current === undefined
        ? current
        : {
            ...current,
            rows: current.rows.filter((row) => row.draft_row_id !== draftRowId),
          },
    extraOptimisticUpdates: [
      {
        queryKey: draftsKey,
        optimisticUpdate: (current) => {
          const page = current as DraftListView | undefined;
          if (page === undefined) return page;
          return {
            ...page,
            drafts: page.drafts.map((draft) =>
              draft.draft_id === draftId
                ? {
                    ...draft,
                    row_count: Math.max(0, draft.row_count - 1),
                  }
                : draft,
            ),
          };
        },
      },
    ],
    invalidateKeys: [detailKey, draftsKey],
  });

  const rowActions: DraftRowActions = {
    editable,
    pendingRowId: removeMutation.isPending ? (removeMutation.variables ?? null) : null,
    onRemove: (rowId) => removeMutation.mutate(rowId),
  };
  const failedRowId = removeMutation.isError ? (removeMutation.variables ?? null) : null;

  const virtualized = detailRows.length > VIRTUAL_DRAFT_ROW_THRESHOLD;

  const metrics: readonly MetricStripItem[] = view
    ? [
        {
          key: 'status',
          label: t('routes.testPlans.metaStatus'),
          value: (
            <StatusBadge
              status={draftStatusKind(detailStatus)}
              label={draftStatusLabel(t, detailStatus)}
              testId="test-plans-detail-status"
            />
          ),
        },
        {
          key: 'rowCount',
          label: t('routes.testPlans.metaRowCount'),
          value: detailRows.length,
          valueTestId: 'test-plans-detail-row-count',
        },
        {
          key: 'createdAt',
          label: t('routes.testPlans.metaCreatedAt'),
          value: orDash(view.created_at),
        },
        {
          key: 'createdBy',
          label: t('routes.testPlans.metaCreatedBy'),
          value: orDash(view.created_by),
        },
        {
          key: 'scopeRevision',
          label: t('routes.testPlans.metaScopeRevision'),
          value: view.scope_revision ?? '—',
        },
      ]
    : [];

  return (
    <section aria-labelledby="test-plans-detail-heading" data-testid="test-plans-detail">
      <SectionBand
        title={t('routes.testPlans.sectionDetail')}
        titleId="test-plans-detail-heading"
        meta={<code>{orDash(draftId)}</code>}
      />
      {detail.isPending && <BlockSkeleton lines={5} testId="test-plans-detail-loading" />}
      {detail.isError && (
        <ErrorState
          testId="test-plans-detail-error"
          message={describeApiError(detail.error, 'headless', {
            forbidden: t('routes.testPlans.detailForbidden'),
            notFound: t('routes.testPlans.detailNotFound'),
            network: t('routes.testPlans.detailNetwork'),
            default: t('routes.testPlans.detailFailed'),
          })}
        />
      )}
      {detail.isSuccess && view && (
        <>
          <MetricStrip ariaLabel={t('routes.testPlans.detailMetricStripLabel')} items={metrics} />
          {/* Export is a non-mutating read (test_plan:read suffices) — rendered
              for any loaded draft, NOT author-gated. */}
          <ExportDraftButton projectId={projectId} draftId={draftId} />
          {detailRows.length === 0 ? (
            <EmptyState
              testId="test-plans-detail-empty"
              title={t('routes.testPlans.detailEmptyTitle')}
              description={t('routes.testPlans.detailEmptyDescription')}
            />
          ) : (
            <>
              {/* Row-removal failures are reported HERE, outside the row list.
                  In the windowed path a failing row can scroll out of the
                  mounted window, which would take an inline error with it and
                  make the failure vanish without ever being read. The message
                  names the row so the attribution the per-row mutation used to
                  give by position is kept explicitly. */}
              {removeMutation.isError && (
                <ErrorState
                  testId="test-plans-remove-row-error"
                  message={t('routes.testPlans.removeRowErrorForRow', {
                    row: failedRowId ?? '—',
                    reason: describeApiError(removeMutation.error, 'headless', {
                      forbidden: t('routes.testPlans.removeRowForbidden'),
                      conflict: t('routes.testPlans.removeRowConflict'),
                      notFound: t('routes.testPlans.removeRowNotFound'),
                      network: t('routes.testPlans.removeRowNetwork'),
                      default: t('routes.testPlans.removeRowFailed'),
                    }),
                  })}
                />
              )}
              {virtualized ? (
                <VirtualizedTable<DraftRowView>
                  items={detailRows}
                  testId="test-plans-detail-virtual-table"
                  className="test-plans-virtual-table"
                  headerClassName="test-plans-virtual-table__row"
                  ariaLabel={t('routes.testPlans.rowsTableCaption')}
                  estimateRowHeight={VIRTUAL_DRAFT_ROW_ESTIMATE}
                  getRowKey={(row) => String(row.draft_row_id)}
                  header={
                    <>
                      {DRAFT_ROW_COLUMN_KEYS.map((key) => (
                        <span role="columnheader" key={key}>
                          {t(key)}
                        </span>
                      ))}
                    </>
                  }
                  renderRow={(row) => ({
                    testId: 'test-plans-detail-row',
                    className: 'test-plans-virtual-table__row',
                    cells: (
                      <>
                        {draftRowCellValues(row).map((value, index) => (
                          <span role="cell" key={index}>
                            {value}
                          </span>
                        ))}
                        <span role="cell">
                          {!editable ? (
                            <span>—</span>
                          ) : (
                            <Button
                              type="button"
                              variant="danger"
                              data-testid="test-plans-remove-row"
                              disabled={rowActions.pendingRowId !== null}
                              onClick={() => rowActions.onRemove(row.draft_row_id)}
                            >
                              {removeRowLabel(t, rowActions.pendingRowId === row.draft_row_id)}
                            </Button>
                          )}
                        </span>
                      </>
                    ),
                  })}
                />
              ) : (
                <DataTable
                  testId="test-plans-detail-table"
                  caption={t('routes.testPlans.rowsTableCaption')}
                  head={
                    <thead>
                      <tr>
                        {DRAFT_ROW_COLUMN_KEYS.map((key) => (
                          <th scope="col" key={key}>
                            {t(key)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                  }
                  body={
                    <tbody>
                      {detailRows.map((row) => (
                        <DraftRowItem key={row.draft_row_id} row={row} actions={rowActions} />
                      ))}
                    </tbody>
                  }
                />
              )}
            </>
          )}
          {editable && (
            <AddRowForm
              projectId={projectId}
              draftId={draftId}
              capabilityPathSuggestions={capabilityPathSuggestions}
            />
          )}
          <BulkRowsEditor
            projectId={projectId}
            draftId={draftId}
            rows={detailRows}
            editable={editable}
          />
        </>
      )}
    </section>
  );
}
