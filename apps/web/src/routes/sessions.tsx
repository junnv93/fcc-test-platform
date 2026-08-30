import { useMutation } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { exportSessionResults, fetchSessionAttempts } from '@/api/headless-client';
import { PERMISSION_HEADLESS_READ } from '@/api/permissions';
import { queryKeys } from '@/api/query-config';
import { categorizeListQuery } from '@/api/query-status';
import { RequirePermission } from '@/auth/route-guard';
import { useT } from '@/i18n';
import { type ApiError } from '@/shared/api-error';
import { parsePositiveId } from '@/shared/numeric-id';
import { ROUTE_PATHS, SESSIONS_SESSION_PARAM } from '@/shared/route-links';
import { useKeysetPagination } from '@/shared/use-keyset-pagination';
import {
  DataTable,
  DataTableSkeleton,
  Button,
  describeApiError,
  EmptyState,
  ErrorState,
  FieldGroup,
  LoadMoreButton,
  MeasurementValueCell,
  PageHeader,
  SectionBand,
  StatusBadge,
  StatusMessage,
  Toolbar,
  verdictToStatusKind,
  VirtualizedTable,
} from '@/ui';

import type { DataTableColumn, Translate, VirtualizedTableRow } from '@/ui';

/**
 * Session / result browser — append-only attempt history (FE-P4 frontend,
 * 2026-05-26). Consumes the FE-P4 backend read API
 * `GET /headless/sessions/{session_id}/attempts` (per-target local read model —
 * a `(model, sample)` target's repeated measurements are complete in its local
 * DB). operator 가 세션의 측정 항목을 조회하고, 조건(condition_hash)별 시도
 * 이력(시도별 결과값/margin/판정/시각 + 추이)과 기술 필터를 볼 수 있다.
 *
 * URL query state: `?session=<id>&tech=<technology>` (공유/북마크 가능, 새로고침
 * 보존). RBAC: 읽기 모델이므로 `headless:read`.
 */

type AttemptPage = NonNullable<Awaited<ReturnType<typeof fetchSessionAttempts>>>;
type AttemptEnvelope = AttemptPage['items'][number];
// Above this loaded-row count the grouped per-condition tables are replaced by
// a single windowed list so the DOM stays bounded regardless of how many
// attempts are loaded. Real virtualization (not a fixed slice) keeps every
// loaded row reachable by scrolling — only the off-screen rows are unmounted.
const VIRTUAL_ATTEMPT_ROW_THRESHOLD = 500;
// Estimated row height (px) — matches --row-height (comfortable). measureElement
// corrects per-row after mount, so this only seeds the initial window size.
const VIRTUAL_ATTEMPT_ROW_ESTIMATE = 36;

/** Parse the `?session=` URL param (name = {@link SESSIONS_SESSION_PARAM} SSOT)
 *  into a positive session id, or null. The numeric validation is the shared
 *  `@/shared/numeric-id` SSOT; this thin wrapper only adds the `null`
 *  (param-absent) handling URLSearchParams returns. */
export function parseSessionParam(raw: string | null): number | null {
  return raw === null ? null : parsePositiveId(raw);
}

interface ConditionGroup {
  /** Stable, unique React key + Map key (hash alone can repeat across rows). */
  groupKey: string;
  conditionHash: string;
  rowOrder: number | null;
  sheetName: string;
  attempts: AttemptEnvelope[];
}

/** Group attempts by (condition_hash, row_order), preserving first-seen order,
 *  with each group's attempts sorted by attempt_number (the trend axis). Keying
 *  on the hash alone would merge distinct test-plan rows that share a hash and
 *  collide their React keys; the compound key prevents that data loss. */
export function groupByCondition(attempts: readonly AttemptEnvelope[]): ConditionGroup[] {
  const groups = new Map<string, ConditionGroup>();
  for (const attempt of attempts) {
    const key = `${attempt.condition_hash}|${attempt.row_order ?? ''}`;
    const existing = groups.get(key);
    if (existing) {
      existing.attempts.push(attempt);
    } else {
      groups.set(key, {
        groupKey: key,
        conditionHash: attempt.condition_hash,
        rowOrder: attempt.row_order ?? null,
        sheetName: attempt.sheet_name ?? '',
        attempts: [attempt],
      });
    }
  }
  for (const group of groups.values()) {
    group.attempts.sort((a, b) => (a.attempt_number ?? 0) - (b.attempt_number ?? 0));
  }
  return [...groups.values()];
}

export function SessionsRoute(): JSX.Element {
  const { t } = useT();
  const [searchParams] = useSearchParams();
  // Drill-down breadcrumb (card C2): once a specific session is selected, the
  // header shows "Session history / Session {id}" so the current location and
  // its parent list are explicit.
  const selectedSessionId = parseSessionParam(searchParams.get(SESSIONS_SESSION_PARAM));
  return (
    <section className="sessions" aria-labelledby="sessions-heading">
      <PageHeader
        title={t('routes.sessions.title')}
        titleId="sessions-heading"
        description={t('routes.sessions.description')}
        breadcrumbLabel={t('routes.sessions.breadcrumbLabel')}
        breadcrumb={
          selectedSessionId !== null ? (
            <>
              <span>{t('routes.sessions.title')}</span>
              <span aria-hidden="true"> / </span>
              <span aria-current="page">
                {t('routes.sessions.breadcrumbCurrent', { id: selectedSessionId })}
              </span>
            </>
          ) : undefined
        }
      />
      <RequirePermission permission={PERMISSION_HEADLESS_READ}>
        <SessionsWorkbenchOverview />
        <SessionBrowser />
      </RequirePermission>
    </section>
  );
}

function SessionsWorkbenchOverview(): JSX.Element {
  const { t } = useT();
  return (
    <nav
      className="sessions-workbench-overview"
      aria-label={t('routes.sessions.workbenchNavAria')}
      data-testid="sessions-workbench-overview"
    >
      <a className="sessions-workbench-overview__item" href="#sessions-filter-heading">
        <span className="sessions-workbench-overview__label">{t('routes.sessions.stepFind')}</span>
        <span className="sessions-workbench-overview__detail">
          {t('routes.sessions.stepFindDetail')}
        </span>
      </a>
      <a className="sessions-workbench-overview__item" href="#sessions-history-heading">
        <span className="sessions-workbench-overview__label">
          {t('routes.sessions.stepHistory')}
        </span>
        <span className="sessions-workbench-overview__detail">
          {t('routes.sessions.stepHistoryDetail')}
        </span>
      </a>
      <a className="sessions-workbench-overview__item" href="#sessions-next-heading">
        <span className="sessions-workbench-overview__label">{t('routes.sessions.stepNext')}</span>
        <span className="sessions-workbench-overview__detail">
          {t('routes.sessions.stepNextDetail')}
        </span>
      </a>
    </nav>
  );
}

function SessionBrowser(): JSX.Element {
  const { t } = useT();
  const [searchParams, setSearchParams] = useSearchParams();
  const sessionId = parseSessionParam(searchParams.get(SESSIONS_SESSION_PARAM));
  const techFilter = searchParams.get('tech') ?? '';
  // Draft input is local; only an explicit submit commits it to the URL so a
  // partial number while typing ("1" → "12" → "123") does NOT fire 3 backend
  // queries + 3 history entries (P1-3 fix). The committed URL param drives the
  // query (shareable / refresh-safe).
  const [draft, setDraft] = useState(searchParams.get(SESSIONS_SESSION_PARAM) ?? '');
  const invalid = draft.trim() !== '' && parseSessionParam(draft) === null;

  // Keyset pagination (shared SSOT) — a session can accumulate tens of thousands
  // of attempts, so we page via the opaque next_cursor instead of materializing
  // the whole table. '더 보기' calls fetchNextPage; loaded pages are flattened.
  const attempts = useKeysetPagination<AttemptEnvelope, AttemptPage>({
    queryKey: queryKeys.sessionAttempts.list(sessionId),
    enabled: sessionId !== null,
    fetchPage: (cursor) => {
      if (sessionId === null) throw new Error('session id required'); // narrows; enabled-gated
      return fetchSessionAttempts(sessionId, cursor);
    },
    getNextCursor: (page) => page.next_cursor ?? undefined,
  });

  const rows = attempts.rows;
  const technologies = useMemo(
    () => [...new Set(rows.map((r) => r.technology).filter((t) => t !== ''))].sort(),
    [rows],
  );
  const filtered = useMemo(
    () => (techFilter === '' ? rows : rows.filter((r) => r.technology === techFilter)),
    [rows, techFilter],
  );
  const groups = useMemo(() => groupByCondition(filtered), [filtered]);

  // Single query-phase contract (E2/E4 SSOT) — branch on `view.kind` instead of
  // re-deriving isLoading/isError/isSuccess here. `filtered` is the loaded set
  // (post tech-filter), so `isEmpty` covers both no-data and filtered-to-empty;
  // a refetch error keeps the rows visible as `transientError`.
  const view = categorizeListQuery<AttemptEnvelope>({
    items: filtered,
    hasLoaded: attempts.isSuccess,
    isError: attempts.isError,
    error: (attempts.error as ApiError | null) ?? null,
    isFetching: attempts.isFetching,
  });
  // A background refetch keeps the table; a next-page fetch is signalled by the
  // LoadMore spinner instead, so don't double-announce it as "refreshing".
  const showRefreshing =
    view.kind === 'ready' &&
    view.isRefetching &&
    view.transientError === null &&
    !attempts.isFetchingNextPage &&
    !view.isEmpty;

  // Functional update avoids a stale-closure overwrite when two updates race;
  // replace:true keeps lookups out of the browser history stack (P2-3/P1-3).
  const updateParam = (key: string, value: string): void => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value === '') next.delete(key);
        else next.set(key, value);
        return next;
      },
      { replace: true },
    );
  };

  return (
    <div className="sessions-workbench" data-testid="sessions-workbench">
      <div className="sessions-workbench__main">
        <section className="sessions-workbench-panel" aria-labelledby="sessions-filter-heading">
          <SectionBand
            title={t('routes.sessions.filterSection')}
            titleId="sessions-filter-heading"
          />
          <form
            onSubmit={(e) => {
              e.preventDefault();
              updateParam(SESSIONS_SESSION_PARAM, draft.trim());
            }}
            aria-label={t('routes.sessions.filterFormLabel')}
          >
            <Toolbar ariaLabel={t('routes.sessions.filterFormLabel')}>
              {/* The rejection travels with the field, not after the button:
                  FieldGroup derives its id from `htmlFor` and links it through
                  `aria-describedby`, so focusing the input says why the lookup
                  is disabled (W4-A M3). */}
              <FieldGroup
                label={t('routes.sessions.sessionIdLabel')}
                htmlFor="session-id-input"
                {...(invalid ? { error: t('routes.sessions.invalidSessionId') } : {})}
                errorTestId="session-invalid"
              >
                <input
                  id="session-id-input"
                  data-testid="session-input"
                  inputMode="numeric"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  aria-invalid={invalid}
                />
              </FieldGroup>
              <Button
                type="submit"
                variant="primary"
                data-testid="session-lookup"
                disabled={parseSessionParam(draft) === null}
              >
                {t('routes.sessions.lookupButton')}
              </Button>
              {technologies.length > 0 && (
                <FieldGroup label={t('routes.sessions.techLabel')} htmlFor="tech-filter">
                  <select
                    id="tech-filter"
                    data-testid="tech-filter"
                    value={techFilter}
                    onChange={(e) => updateParam('tech', e.target.value)}
                  >
                    <option value="">{t('routes.sessions.techAll')}</option>
                    {technologies.map((tech) => (
                      <option key={tech} value={tech}>
                        {tech}
                      </option>
                    ))}
                  </select>
                </FieldGroup>
              )}
              {/* Right-aligned loaded count + truncation status — plan §6.2
                  "compact Toolbar: Session ID / Tech / loaded count / more·
                  truncated status" 충실 통합. */}
              {attempts.isSuccess && (
                <div className="toolbar-status-group" data-testid="sessions-toolbar-status">
                  <StatusBadge
                    status="pass"
                    label={t('routes.sessions.loadedPill', {
                      conditions: groups.length,
                      attempts: filtered.length,
                    })}
                    testId="toolbar-loaded-pill"
                    title={t('routes.sessions.loadedPillTitle')}
                  />
                  {attempts.hasNextPage && (
                    <StatusBadge
                      status="stale"
                      label={t('routes.sessions.truncatedPill')}
                      testId="toolbar-truncated-pill"
                      title={t('routes.sessions.truncatedPillTitle')}
                    />
                  )}
                </div>
              )}
            </Toolbar>
          </form>
        </section>

        <section className="sessions-workbench-panel" aria-labelledby="sessions-history-heading">
          <SectionBand
            title={t('routes.sessions.historySection')}
            titleId="sessions-history-heading"
          />
          {view.kind === 'loading' && (
            <DataTableSkeleton columns={attemptColumns(t).length} rows={8} />
          )}
          {view.kind === 'hardError' && (
            <ErrorState
              testId="attempts-error"
              message={describeApiError(view.error, 'headless', {
                default: t('routes.sessions.lookupError'),
              })}
            />
          )}
          {view.kind === 'ready' && (
            <>
              {/* Refetch failed but the loaded attempts are retained (E2 transient
                  error — non-destructive). */}
              {view.transientError !== null && (
                <StatusMessage
                  tone="info"
                  testId="attempts-stale"
                  message={t('common.staleData')}
                />
              )}
              {showRefreshing && (
                <StatusMessage
                  tone="info"
                  testId="attempts-refreshing"
                  message={t('common.refreshing')}
                />
              )}
              <p data-testid="attempts-summary">
                {t('routes.sessions.summary', {
                  conditions: groups.length,
                  attempts: filtered.length,
                })}
                {attempts.hasNextPage && t('routes.sessions.summaryTruncatedSuffix')}
              </p>
              {/* Deliberately outside the isEmpty branch: `isEmpty` is measured
                  after the tech filter, so gating on it would withhold the
                  download from a session that has results but none matching the
                  current filter. Whether the session truly measured nothing is
                  the server's answer (422), not this component's guess. */}
              {sessionId !== null && <ExportSessionResultsButton sessionId={sessionId} />}
              {view.isEmpty ? (
                <EmptyState
                  title={t('routes.sessions.emptyTitle')}
                  description={t('routes.sessions.emptyDescription')}
                />
              ) : filtered.length >= VIRTUAL_ATTEMPT_ROW_THRESHOLD ? (
                <VirtualizedAttemptHistory groups={groups} hasNextPage={attempts.hasNextPage} />
              ) : (
                groups.map((group) => <ConditionGroupTable key={group.groupKey} group={group} />)
              )}
            </>
          )}

          {attempts.hasNextPage && (
            <LoadMoreButton
              testId="attempts-load-more"
              label={t('routes.sessions.loadMore')}
              onClick={attempts.fetchNextPage}
              isFetching={attempts.isFetchingNextPage}
            />
          )}
        </section>
      </div>
      <aside
        className="sessions-workbench__rail"
        aria-labelledby="sessions-next-heading"
        data-testid="sessions-next-actions"
      >
        <SessionNextActions sessionId={sessionId} hasRows={filtered.length > 0} />
      </aside>
    </div>
  );
}

function SessionNextActions({
  sessionId,
  hasRows,
}: {
  readonly sessionId: number | null;
  readonly hasRows: boolean;
}): JSX.Element {
  const { t } = useT();
  return (
    <section className="sessions-next">
      <SectionBand title={t('routes.sessions.nextSection')} titleId="sessions-next-heading" />
      <p className="sessions-next__state" data-testid="sessions-next-state">
        {sessionId === null
          ? t('routes.sessions.nextNoSession')
          : t('routes.sessions.nextSession', { id: sessionId })}
      </p>
      <div className="sessions-next__actions">
        <Link
          to={ROUTE_PATHS.reports}
          className="sessions-next__action"
          data-testid="sessions-next-reports"
        >
          {t('routes.sessions.nextReports')}
        </Link>
        <Link
          to={ROUTE_PATHS.chambers}
          className="sessions-next__action"
          data-testid="sessions-next-chambers"
        >
          {hasRows ? t('routes.sessions.nextRemeasure') : t('routes.sessions.nextMeasure')}
        </Link>
      </div>
      <p className="section-hint">{t('routes.sessions.nextHint')}</p>
    </section>
  );
}

/**
 * Download this session's measurement results as an .xlsx workbook
 * (`GET /headless/sessions/{session_id}/results/export`, `headless:read` — the
 * same token this whole route already requires).
 *
 * This is the button the Measurement Result Export SSOT (2026-08-11) was built
 * for. That wave established the request-shaped export that replaces
 * `ExcelExporter.save_dirty_rows`, whose target — "the workbook the tester left
 * open" — stops existing once the web replaces the GUI; it landed the operation
 * and the versioned template, and no screen consumed it. The capability existed
 * with nowhere to press it.
 *
 * **The export is the whole session, not the current view.** The tech filter
 * narrows the attempt history on screen; the server renders every stored row.
 * That is why the button is not hidden when the filtered list is empty:
 * `view.isEmpty` is measured *after* the tech filter (see its call site), so
 * hiding on it would withhold the download from a session that has results but
 * no rows matching the current filter. The copy says which it is, and the one
 * genuinely empty case is the server's to declare — 422 `SESSION_RESULTS_EMPTY`,
 * surfaced verbatim below rather than guessed at here.
 *
 * Mechanics mirror {@link ExportDraftButton}: the route is RBAC-gated so a bare
 * URL navigation cannot carry the auth header, hence `parseAs: 'blob'` plus a
 * client-side object-URL anchor click. The RFC 9457 `code` is carried onto the
 * thrown error so the 422 can say *why* rather than "처리할 수 없습니다".
 */
export function ExportSessionResultsButton({
  sessionId,
}: {
  readonly sessionId: number;
}): JSX.Element {
  const { t } = useT();

  const exportMutation = useMutation<void, ApiError, void>({
    mutationFn: async () => {
      // The service's own fallback name, so a header-less response still lands
      // on the file the backend would have named.
      const { blob, filename } = await exportSessionResults(
        sessionId,
        `measurement-results-${sessionId}.xlsx`,
      );
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
      } finally {
        URL.revokeObjectURL(url);
      }
    },
  });

  return (
    <Toolbar ariaLabel={t('routes.sessions.exportButton')}>
      <Button
        type="button"
        variant="secondary"
        data-testid="sessions-export"
        disabled={exportMutation.isPending}
        onClick={() => exportMutation.mutate()}
      >
        {exportMutation.isPending
          ? t('routes.sessions.exportBusy')
          : t('routes.sessions.exportButton')}
      </Button>
      <span className="section-hint" data-testid="sessions-export-hint">
        {t('routes.sessions.exportHint')}
      </span>
      {exportMutation.isError && (
        <ErrorState
          testId="sessions-export-error"
          message={describeApiError(exportMutation.error, 'headless', {
            // No `unprocessable` override: the only 422 this route can raise is
            // SESSION_RESULTS_EMPTY, and the shared taxonomy now names it (the
            // code reaches the client because the headless artifact finally
            // publishes it — see the note in `headless/api_schema.py`). A route
            // override here would be a second phrasing of one fact.
            forbidden: t('routes.sessions.exportForbidden'),
            notFound: t('routes.sessions.exportNotFound'),
            network: t('routes.sessions.exportNetwork'),
            default: t('routes.sessions.exportFailed'),
          })}
        />
      )}
    </Toolbar>
  );
}

type VirtualItem =
  | { readonly kind: 'groupHeader'; readonly group: ConditionGroup }
  | {
      readonly kind: 'attemptRow';
      readonly group: ConditionGroup;
      readonly attempt: AttemptEnvelope;
    };

function flattenGroups(groups: readonly ConditionGroup[]): VirtualItem[] {
  return groups.flatMap((group) => [
    { kind: 'groupHeader' as const, group },
    ...group.attempts.map((attempt) => ({ kind: 'attemptRow' as const, group, attempt })),
  ]);
}

/** Build one windowed row for the {@link VirtualizedTable} primitive — a group
 *  band or an attempt row. The primitive owns the row chrome (role/aria/offset/
 *  measureElement); this only supplies cells + the sessions-specific layout
 *  className + testId. */
function renderAttemptHistoryRow(
  item: VirtualItem,
  t: ReturnType<typeof useT>['t'],
): VirtualizedTableRow {
  if (item.kind === 'groupHeader') {
    // Verdict + 재측정 affordance parity with the non-virtualized
    // ConditionGroupTable (§5⑤) — the windowed path must not silently drop the
    // standing verdict or the failing-condition remeasure entry point (card F1).
    const latest = item.group.attempts[item.group.attempts.length - 1];
    const latestVerdictKind = verdictToStatusKind(latest?.verdict);
    return {
      testId: 'condition-group',
      className: 'sessions-virtual-table__group',
      cells: (
        <span role="cell" className="condition-group__verdict">
          {item.group.sheetName} · {t('routes.sessions.rowLabel')} {item.group.rowOrder ?? '—'} ·{' '}
          <code data-testid="condition-hash">{item.group.conditionHash.slice(0, 12)}</code>
          {latestVerdictKind !== null && latest?.verdict !== undefined && (
            <>
              {' '}
              <StatusBadge
                status={latestVerdictKind}
                label={latest.verdict}
                testId="condition-verdict-summary"
              />
              {latestVerdictKind === 'fail' && (
                <Link
                  to={ROUTE_PATHS.chambers}
                  className="condition-group__remeasure"
                  data-testid="condition-remeasure"
                >
                  {t('routes.sessions.remeasure')}
                </Link>
              )}
            </>
          )}
        </span>
      ),
    };
  }
  return {
    testId: 'attempt-row',
    className: 'sessions-virtual-table__row',
    cells: <AttemptVirtualCells attempt={item.attempt} />,
  };
}

function VirtualizedAttemptHistory({
  groups,
  hasNextPage,
}: {
  readonly groups: ConditionGroup[];
  readonly hasNextPage: boolean;
}): JSX.Element {
  const { t } = useT();
  const items = useMemo(() => flattenGroups(groups), [groups]);
  // Windowing + ARIA-table scaffolding live in the shared VirtualizedTable
  // primitive (B1/B2 contract) — this route only supplies columns + per-item
  // cells, so the windowing behavior is owned and sealed in one place. When more
  // keyset pages remain, the loaded rows are a partial slice of an unknown total,
  // so announce -1 (WAI-ARIA "size unknown") instead of the partial count.
  return (
    <VirtualizedTable<VirtualItem>
      items={items}
      totalRowCount={hasNextPage ? -1 : items.length}
      testId="virtualized-attempt-history"
      className="sessions-virtual-table"
      headerClassName="sessions-virtual-table__row"
      ariaLabel={t('routes.sessions.virtualizedAttemptsLabel')}
      estimateRowHeight={VIRTUAL_ATTEMPT_ROW_ESTIMATE}
      overscan={12}
      // Keep the keyboard workflow on the LARGEST datasets — the non-virtualized
      // grouped tables expose DataTable keyboardNavigation, and the windowed path
      // must not silently drop it (card F1).
      keyboardNavigation
      getRowKey={(item) =>
        item.kind === 'groupHeader' ? `${item.group.groupKey}:header` : item.attempt.attempt_id
      }
      header={
        <>
          <span role="columnheader">{t('routes.sessions.colAttempt')}</span>
          <span role="columnheader">{t('routes.sessions.colResult1')}</span>
          <span role="columnheader">{t('routes.sessions.colResult2')}</span>
          <span role="columnheader">{t('routes.sessions.colMargin')}</span>
          <span role="columnheader">{t('routes.sessions.colVerdict')}</span>
          <span role="columnheader">{t('routes.sessions.colRecordedBy')}</span>
          <span role="columnheader">{t('routes.sessions.colMeasuredAt')}</span>
        </>
      }
      renderRow={(item) => renderAttemptHistoryRow(item, t)}
    />
  );
}

function ConditionGroupTable({ group }: { readonly group: ConditionGroup }): JSX.Element {
  const { t } = useT();
  // The latest attempt (groups are sorted ascending by attempt_number) carries
  // the group's standing verdict — shown prominently so 합/부 reads at a glance
  // (§5⑤). A failing latest verdict offers a direct "재측정" entry into ③ 측정
  // (the chamber fleet — /control is hidden). The condition hash rides along as
  // supplementary debug metadata (design-system layer) without displacing the
  // verdict affordance.
  const latest = group.attempts[group.attempts.length - 1];
  const latestVerdictKind = verdictToStatusKind(latest?.verdict);
  return (
    <section
      className="condition-group"
      data-testid="condition-group"
      aria-label={t('routes.sessions.conditionGroupLabel', {
        sheet: group.sheetName,
        row: group.rowOrder ?? '—',
      })}
    >
      <SectionBand
        title={`${group.sheetName} · ${t('routes.sessions.rowLabel')} ${group.rowOrder ?? '—'}`}
        meta={
          <span className="condition-group__verdict">
            {latestVerdictKind !== null && latest?.verdict !== undefined && (
              <>
                <StatusBadge
                  status={latestVerdictKind}
                  label={latest.verdict}
                  size="lg"
                  testId="condition-verdict-summary"
                />
                {latestVerdictKind === 'fail' && (
                  <Link
                    to={ROUTE_PATHS.chambers}
                    className="condition-group__remeasure"
                    data-testid="condition-remeasure"
                  >
                    {t('routes.sessions.remeasure')}
                  </Link>
                )}
              </>
            )}
            <code data-testid="condition-hash">{group.conditionHash.slice(0, 12)}</code>
          </span>
        }
      />
      <DataTable<AttemptEnvelope>
        testId="attempt-history"
        stickyHeader
        keyboardNavigation
        caption={t('routes.sessions.attemptHistoryCaption', {
          sheet: group.sheetName,
          row: group.rowOrder ?? '—',
        })}
        columns={attemptColumns(t)}
        rows={group.attempts}
        rowKey={(attempt) => attempt.attempt_id}
        rowTestId="attempt-row"
      />
    </section>
  );
}

/**
 * Column descriptor for the attempt-history table (§M7.2).
 *
 * The attempt number and the verdict are what an operator scans for — is this
 * condition passing, and on which try. Result1/2 and margin are the working
 * numbers. Attribution (who recorded it, when) is provenance: it folds into
 * the per-row overflow line on a compact viewport and into the card body on a
 * phone, so it is never lost — only demoted.
 */
/**
 * Optional `unit` prop for {@link MeasurementValueCell} (M4, 2026-07-28).
 *
 * The backend has always emitted `result1_unit` / `result2_unit` /
 * `result_sum_unit`; the OpenAPI schema simply did not describe them, so the
 * generated type had no such fields and the cells rendered a bare `"22.0"` —
 * dBm, kHz and msec all look identical at that point.
 *
 * The unit MUST be omitted rather than passed as `''`: the primitive treats any
 * *defined* `unit` as authoritative and skips its suffix-parsing fallback, so
 * `unit=""` would blank the unit on a legacy combined-string payload
 * (`"22.0 dBm"`). Omitting it is what makes an attempt without unit metadata
 * render byte-identically to before this change.
 *
 * `margin` deliberately gets no unit: the payload carries no margin unit axis,
 * and inventing one would be exactly the fabrication this wave exists to remove.
 */
function unitProps(unit: string | undefined): { unit?: string } {
  return unit === undefined || unit === '' ? {} : { unit };
}

function attemptColumns(t: Translate): readonly DataTableColumn<AttemptEnvelope>[] {
  return [
    {
      key: 'attempt',
      header: t('routes.sessions.colAttempt'),
      priority: 'primary',
      className: 'data-cell-numeric',
      testId: 'attempt-number',
      cell: (attempt) => attempt.attempt_number ?? '—',
    },
    {
      key: 'result1',
      header: t('routes.sessions.colResult1'),
      priority: 'primary',
      className: 'data-cell-numeric',
      cell: (attempt) => (
        <MeasurementValueCell
          value={attempt.result?.result1 ?? null}
          {...unitProps(attempt.result?.result1_unit)}
        />
      ),
    },
    {
      key: 'result2',
      header: t('routes.sessions.colResult2'),
      priority: 'secondary',
      className: 'data-cell-numeric',
      cell: (attempt) => (
        <MeasurementValueCell
          value={attempt.result?.result2 ?? null}
          {...unitProps(attempt.result?.result2_unit)}
        />
      ),
    },
    {
      key: 'margin',
      header: t('routes.sessions.colMargin'),
      priority: 'secondary',
      className: 'data-cell-numeric',
      cell: (attempt) => <MeasurementValueCell value={attempt.result?.margin ?? null} />,
    },
    {
      key: 'verdict',
      header: t('routes.sessions.colVerdict'),
      priority: 'primary',
      testId: 'attempt-verdict',
      cell: (attempt) => {
        const verdictKind = verdictToStatusKind(attempt.verdict);
        return verdictKind !== null && attempt.verdict !== undefined ? (
          <StatusBadge status={verdictKind} label={attempt.verdict} />
        ) : (
          attempt.verdict
        );
      },
    },
    {
      key: 'recordedBy',
      header: t('routes.sessions.colRecordedBy'),
      priority: 'detail',
      cell: (attempt) => attempt.recorded_by,
    },
    {
      key: 'measuredAt',
      header: t('routes.sessions.colMeasuredAt'),
      priority: 'detail',
      cell: (attempt) => attempt.measured_at,
    },
  ];
}

/** The 7 cells of one windowed attempt row. The {@link VirtualizedTable}
 *  primitive wraps these in the `role="row"` chrome + virtualizer offset, so
 *  this component is pure content (no ref / positioning concerns). */
function AttemptVirtualCells({ attempt }: { readonly attempt: AttemptEnvelope }): JSX.Element {
  const verdictKind = verdictToStatusKind(attempt.verdict);
  return (
    <>
      <span role="cell" className="data-cell-numeric" data-testid="attempt-number">
        {attempt.attempt_number ?? '—'}
      </span>
      <span role="cell" className="data-cell-numeric">
        <MeasurementValueCell
          value={attempt.result?.result1 ?? null}
          {...unitProps(attempt.result?.result1_unit)}
        />
      </span>
      <span role="cell" className="data-cell-numeric">
        <MeasurementValueCell
          value={attempt.result?.result2 ?? null}
          {...unitProps(attempt.result?.result2_unit)}
        />
      </span>
      <span role="cell" className="data-cell-numeric">
        <MeasurementValueCell value={attempt.result?.margin ?? null} />
      </span>
      <span role="cell" data-testid="attempt-verdict">
        {verdictKind !== null && attempt.verdict !== undefined ? (
          <StatusBadge status={verdictKind} label={attempt.verdict} />
        ) : (
          attempt.verdict
        )}
      </span>
      <span role="cell">{attempt.recorded_by}</span>
      <span role="cell">{attempt.measured_at}</span>
    </>
  );
}

export default SessionsRoute;
