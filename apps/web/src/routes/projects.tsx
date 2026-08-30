import { useQuery, type InfiniteData } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { PERMISSION_PLATFORM_CLAIM, PERMISSION_PLATFORM_READ } from '@/api/permissions';
import {
  acquireClaim,
  fetchClaimsPage,
  fetchCoveragePage,
  fetchProviderList,
  fetchSyncStatus,
  releaseClaim,
  type ActiveClaimEnvelope,
  type ClaimEventEnvelope,
  type CoverageEnvelope,
  type PlatformPage,
  type ProviderSummary,
  type SyncStatusEnvelope,
} from '@/api/platform-client';
import { queryKeys } from '@/api/query-config';
import { RequirePermission, useAuthSession } from '@/auth/route-guard';
import { t, useT } from '@/i18n';
import { type ApiError } from '@/shared/api-error';
import { isValidProjectId } from '@/shared/project-id';
import { projectWorkflowActions } from '@/shared/project-workflow';
import { ProjectSelectField } from '@/shared/ProjectSelectField';
import { ROUTE_PATHS } from '@/shared/route-links';
import { SEARCH_DEBOUNCE_MS } from '@/shared/search-debounce';
import { useKeysetPagination } from '@/shared/use-keyset-pagination';
import { useOnlineStatus } from '@/shared/use-online-status';
import { useOptimisticMutation } from '@/shared/use-optimistic-mutation';
import { selectLatestWriteMessage } from '@/shared/write-feedback';
import {
  DataTable,
  DataTableSkeleton,
  Button,
  Card,
  describeApiError,
  EmptyState,
  ErrorState,
  FieldGroup,
  LoadMoreButton,
  liveRegionProps,
  PageHeader,
  SectionBand,
  StatusBadge,
  StatusMessage,
  Toolbar,
  verdictToStatusKind,
  type DataTableColumn,
  type DataTableExpansion,
  type DataTableSurface,
  type Translate,
  WorkbenchLayout,
} from '@/ui';

import { ProjectResultSelection } from './projects/ProjectResultSelection';

/**
 * Project coverage dashboard — central read model (FE-P2, 2026-05-27). ★1순위.
 *
 * 중복 방지의 핵심 화면. FE-P0d 의 central read API
 * (`GET /platform/projects/{id}/coverage` → `coverage_by_condition_hash`
 * materialized view) 를 **typed generated client** 로 소비해, 한 프로젝트의
 * 기술별(BT/BLE/DTS/UNII) 완료/진행 현황 + 담당 + 중복 측정을 계층형 매트릭스로
 * 보인다. operator 가 측정 착수 전 "이미 측정됨/진행중" 을 확인 → cross-engineer
 * 재측정을 막는다.
 *
 * SSOT: coverage source = central `coverage_by_condition_hash` (로컬 per-target
 * SQLite stopgap 아님). 기술 토큰 = coverage 데이터에서 파생(UI enum/리터럴 박기
 * 금지). RBAC = `platform:read` (backend HEADLESS/PLATFORM permission SSOT 미러).
 */

// Circuit-breaker bound for claim auto-advance (mirror of the backend keyset
// walk's range(50) guard). The active-claim set is bounded, so this is far above
// any realistic page count — it only trips if a backend cursor bug never
// terminates, halting the loop instead of fetching forever.
export const CLAIMS_MAX_AUTO_PAGES = 50;

// Coverage/claim envelope types come from the generated OpenAPI types
// (re-exported by platform-client). The keyset page fetch helpers there own the
// wire shape (typed limit/cursor query + the X-Next-Cursor response header), so
// view code stays declarative and never hand-builds query params.
type ClaimEnvelope = ActiveClaimEnvelope;

// Re-export the project-id SSOT (now in `@/shared/project-id`) so existing
// importers — `projects.test.tsx` + the FE-P2 seal — keep a stable surface.
export { isValidProjectId };

// Sentinel prefix for an OPTIMISTIC (not-yet-reconciled) claim_id. When an
// acquire is applied optimistically the cache gets a placeholder claim whose id
// is `optimistic:<conditionHash>` — a purely client-side identity that the
// central server has never seen. It exists only until the settle invalidation
// refetches the claims read and replaces it with the server's real claim row.
// Such a placeholder MUST NEVER be sent back to the server as a release identity
// (`releaseClaim(projectId, claim_id, …)`): the server would 404/400 on an
// unknown id, and worse, the operator's intent (release the lock they just took)
// would silently fail. The release path is guarded with `isOptimisticClaimId`
// and the release control is disabled until reconciliation. SSOT — the literal
// lives only here (sealed by `tests/test_frontend_optimistic_mutation.py`).
export const OPTIMISTIC_CLAIM_ID_PREFIX = 'optimistic:';

/** True for a placeholder claim_id minted by the optimistic acquire transform
 *  (not yet replaced by the server's real claim on settle). Such an id is not a
 *  server release identity — the release path must skip it until reconciliation. */
export function isOptimisticClaimId(claimId: string): boolean {
  return claimId.startsWith(OPTIMISTIC_CLAIM_ID_PREFIX);
}

/** Optimistic cache transform (Increment 3): add a just-acquired claim to the
 *  first loaded page so the lock overlay reflects it before the central write
 *  confirms. Pure — returns a new InfiniteData, never mutates. `undefined`
 *  cache (query not loaded yet) is returned unchanged; the settle invalidation
 *  reconciles either way. */
export function insertOptimisticClaim(
  current: InfiniteData<PlatformPage<ClaimEnvelope>> | undefined,
  claim: ClaimEnvelope,
): InfiniteData<PlatformPage<ClaimEnvelope>> | undefined {
  if (!current || current.pages.length === 0) return current;
  return {
    ...current,
    pages: current.pages.map((p, i) => (i === 0 ? { ...p, items: [...p.items, claim] } : p)),
  };
}

/** Optimistic cache transform (Increment 3): drop a just-released claim from
 *  every loaded page by claim_id. Pure — returns a new InfiniteData. */
export function removeOptimisticClaim(
  current: InfiniteData<PlatformPage<ClaimEnvelope>> | undefined,
  claimId: string,
): InfiniteData<PlatformPage<ClaimEnvelope>> | undefined {
  if (!current) return current;
  return {
    ...current,
    pages: current.pages.map((p) => ({
      ...p,
      items: p.items.filter((c) => c.claim_id !== claimId),
    })),
  };
}

/** Index active claims by condition_hash for O(1) per-condition lookup on the
 *  coverage matrix (FE-P3 claim/lock visualization over FE-P2 coverage). */
export function indexClaimsByCondition(
  claims: readonly ClaimEnvelope[],
): Map<string, ClaimEnvelope> {
  const byCondition = new Map<string, ClaimEnvelope>();
  for (const claim of claims) {
    if (claim.condition_hash) byCondition.set(claim.condition_hash, claim);
  }
  return byCondition;
}

/** A verdict of Pass/Fail is a completed condition; anything else (in-flight,
 *  empty, error) counts as in-progress. */
export type CoverageStatus = 'done' | 'in_progress';
export function classifyCoverage(verdict: string): CoverageStatus {
  const v = verdict.trim().toLowerCase();
  return v === 'pass' || v === 'fail' ? 'done' : 'in_progress';
}

// Phase 2 follow-up (2026-05-30) — `verdictToStatusKind` is now the
// `@/ui::status-mapping` SSOT (imported above). This file no longer
// owns the verdict → status mapping.

/** Display fallback — nullish OR empty/whitespace renders an em-dash. (`??`
 *  alone would keep '' visible; bare `||` trips prefer-nullish-coalescing.) */
function orDash(value: string | undefined): string {
  return value !== undefined && value.trim() !== '' ? value : '—';
}

/** FE-P3 duplicate-quality: a condition is a *cross-engineer* duplicate only when
 *  more than one distinct operator measured it. attempt_count > 1 alone is just a
 *  repeated measure (which the same engineer legitimately does on a retry). */
export function isCrossEngineerDuplicate(row: CoverageEnvelope): boolean {
  return (row.distinct_operator_count ?? 0) > 1;
}

/** A repeated measure (attempt_count > 1) that is NOT a cross-engineer duplicate
 *  — i.e. one engineer re-ran the same condition. Surfaced separately so a retry
 *  is not mislabeled as a duplicate. */
export function isSingleEngineerRemeasure(row: CoverageEnvelope): boolean {
  return (row.attempt_count ?? 0) > 1 && !isCrossEngineerDuplicate(row);
}

export interface TechSummary {
  technology: string;
  total: number;
  done: number;
  inProgress: number;
  /** conditions measured more than once (attempt_count > 1) — repeated-measure
   *  signal (NOT necessarily a duplicate; see crossEngineerDuplicate). */
  repeated: number;
  /** conditions measured by more than one distinct operator
   *  (distinct_operator_count > 1) — the TRUE cross-engineer duplicate signal. */
  crossEngineerDuplicate: number;
  operators: string[];
  rows: CoverageEnvelope[];
}

export function summarizeByTechnology(coverage: readonly CoverageEnvelope[]): TechSummary[] {
  const map = new Map<string, TechSummary>();
  for (const row of coverage) {
    const tech =
      row.technology !== undefined && row.technology.trim() !== '' ? row.technology : '(unknown)';
    let summary = map.get(tech);
    if (!summary) {
      summary = {
        technology: tech,
        total: 0,
        done: 0,
        inProgress: 0,
        repeated: 0,
        crossEngineerDuplicate: 0,
        operators: [],
        rows: [],
      };
      map.set(tech, summary);
    }
    summary.total += 1;
    if (classifyCoverage(row.latest_verdict ?? '') === 'done') summary.done += 1;
    else summary.inProgress += 1;
    if ((row.attempt_count ?? 0) > 1) summary.repeated += 1;
    if (isCrossEngineerDuplicate(row)) summary.crossEngineerDuplicate += 1;
    if (row.latest_operator && !summary.operators.includes(row.latest_operator)) {
      summary.operators.push(row.latest_operator);
    }
    summary.rows.push(row);
  }
  return [...map.values()].sort((a, b) => a.technology.localeCompare(b.technology));
}

/** Map a claim write failure to an operator-facing message. Delegates to the
 *  `describeApiError` 7-arm taxonomy SSOT (no inline status switch) and only
 *  supplies the claim-specific copy per arm: 409 = contended / no-open-claim;
 *  403 = missing platform:claim; 400 = invalid project/condition; network (no
 *  status) = offline/unreachable central server. Behaviour is byte-identical to
 *  the prior inline switch — only the dispatch moves to the SSOT (the same
 *  consolidation membership.tsx adopted in fe-error-id-ssot). */
export function claimErrorMessage(error: ApiError): string {
  return describeApiError(error, 'platform', {
    conflict: t('routes.projects.claim.conflict'),
    forbidden: t('routes.projects.claim.forbidden'),
    badRequest: t('routes.projects.claim.invalid'),
    network: t('routes.projects.claim.offline'),
    default: t('routes.projects.claim.failed'),
  });
}

/** Human-readable age (seconds → "N초/분/시간/일") for the sync-status banner. */
export function formatAge(seconds: number): string {
  if (seconds < 60) return t('routes.projects.age.seconds', { n: seconds });
  if (seconds < 3600) return t('routes.projects.age.minutes', { n: Math.floor(seconds / 60) });
  if (seconds < 86400) return t('routes.projects.age.hours', { n: Math.floor(seconds / 3600) });
  return t('routes.projects.age.days', { n: Math.floor(seconds / 86400) });
}

/** FE-SYNC: central-data freshness banner. When the central coverage is stale
 *  (no recent ingest), the duplicate-prevention guarantee is explicitly softened
 *  — an honest "this screen may be N old" rather than a silent stale read. */
function SyncStatusBanner({ status }: { status: SyncStatusEnvelope }): JSX.Element {
  const { t } = useT();
  if (status.last_ingested_at === null || status.last_ingested_at === undefined) {
    return (
      <p className="sync-status" {...liveRegionProps('inlineNotice')} data-testid="sync-status">
        ⓘ {t('routes.projects.sync.none')}
      </p>
    );
  }
  const age = status.age_seconds ?? null;
  return (
    <p
      className={status.is_stale ? 'sync-status sync-stale' : 'sync-status sync-fresh'}
      // One banner, one role — `inlineNotice` for both freshness states. The
      // role used to flip `alert`↔`note` with `is_stale`, which is wrong twice
      // over: staleness is a condition the screen was ALREADY in when it
      // loaded (nothing the operator just did), and a live region whose role
      // changes underneath assistive tech re-announces on every refetch. The
      // ⚠ glyph + the softened-guarantee copy carry the warning visually.
      {...liveRegionProps('inlineNotice')}
      data-testid="sync-status"
      data-stale={status.is_stale ? 'true' : 'false'}
    >
      {status.is_stale ? '⚠ ' : 'ⓘ '}
      {t('routes.projects.sync.lastIngested')}: {status.last_ingested_at}
      {age !== null && ` (${t('routes.projects.sync.ago', { age: formatAge(age) })})`}
      {status.is_stale ? t('routes.projects.sync.staleWarning') : ''}
    </p>
  );
}

/** Per-condition claim controls threaded down to the condition rows. */
export interface ClaimActions {
  /** Whether the claim/release affordance is offered at all. This is gated on
   *  authentication only (NOT the platform:claim token) because the backend
   *  authorizes the UNION of token ∪ project-membership effective permissions —
   *  hiding the control on a token-only miss would falsely block a
   *  membership-granted operator. The backend stays the authority (403 surfaces
   *  via claimErrorMessage's forbidden arm). */
  readonly canAttempt: boolean;
  readonly isOnline: boolean;
  readonly currentOperator: string;
  readonly pendingCondition: string | null;
  readonly onAcquire: (technology: string, conditionHash: string) => void;
  readonly onRelease: (claimId: string, conditionHash: string) => void;
}

export function ProjectsRoute(): JSX.Element {
  const { t } = useT();
  const [searchParams] = useSearchParams();
  const projectId = searchParams.get('project') ?? '';
  const providers = useQuery<readonly ProviderSummary[]>({
    queryKey: queryKeys.provider.list(),
    queryFn: fetchProviderList,
    enabled: isValidProjectId(projectId),
  });
  return (
    <section className="projects" aria-labelledby="projects-heading">
      <PageHeader
        title={t('routes.projects.title')}
        titleId="projects-heading"
        description={t('routes.projects.description')}
      />
      <RequirePermission permission={PERMISSION_PLATFORM_READ}>
        <ProjectsWorkbenchOverview />
        <CoverageDashboard />
        <ProjectResultSelection
          projectId={projectId}
          providers={providers.data ?? []}
          providersLoading={providers.isLoading}
          providersError={providers.isError}
        />
      </RequirePermission>
    </section>
  );
}

function ProjectsWorkbenchOverview(): JSX.Element {
  const { t } = useT();
  return (
    <nav
      className="projects-workbench-overview"
      aria-label={t('routes.projects.workbenchNavAria')}
      data-testid="projects-workbench-overview"
    >
      <a className="projects-workbench-overview__item" href="#projects-select-heading">
        <span className="projects-workbench-overview__label">
          {t('routes.projects.stepSelect')}
        </span>
        <span className="projects-workbench-overview__detail">
          {t('routes.projects.stepSelectDetail')}
        </span>
      </a>
      <a className="projects-workbench-overview__item" href="#projects-coverage-heading">
        <span className="projects-workbench-overview__label">
          {t('routes.projects.stepCoverage')}
        </span>
        <span className="projects-workbench-overview__detail">
          {t('routes.projects.stepCoverageDetail')}
        </span>
      </a>
      <a className="projects-workbench-overview__item" href="#projects-next-heading">
        <span className="projects-workbench-overview__label">{t('routes.projects.stepNext')}</span>
        <span className="projects-workbench-overview__detail">
          {t('routes.projects.stepNextDetail')}
        </span>
      </a>
    </nav>
  );
}

function CoverageDashboard(): JSX.Element {
  const { t } = useT();
  const [searchParams, setSearchParams] = useSearchParams();
  const projectId = searchParams.get('project') ?? '';
  const expandedTech = searchParams.get('tech') ?? '';
  // Technology facet filter (Phase B adoption) — distinct URL param from the
  // `?tech=` drilldown. The value narrows the read server-side so the dedup
  // workflow reaches a technology's conditions without paging the full 16k+ set.
  const techFilter = searchParams.get('techFilter') ?? '';
  // Normalize once: blank → no facet (full project, backward compatible). The
  // backend `_facet()` normalizes the token, so it is forwarded verbatim (no
  // client-side normalization). Drives the queryKey + the fetch helpers.
  const techQuery = techFilter.trim() === '' ? undefined : techFilter.trim();
  // Local draft for the type-ahead facet input (responsive on every keystroke);
  // it is committed to `?techFilter=` (which drives the query) only after a
  // debounce, so typing does not issue one central read per character.
  const [techDraft, setTechDraft] = useState(techFilter);

  // Keyset pagination (FE-P2 adoption): the dashboard loads coverage one page
  // at a time (user-driven "더보기") instead of one unbounded fetch over a
  // project's 16k+ conditions. The cursor flows through getNextPageParam.
  // `techQuery` is part of the queryKey so changing the facet starts a fresh
  // query (pagination resets to the first page).
  const coverage = useKeysetPagination<CoverageEnvelope, PlatformPage<CoverageEnvelope>>({
    queryKey: queryKeys.project.coverage(projectId, techQuery),
    enabled: isValidProjectId(projectId),
    fetchPage: (cursor) => fetchCoveragePage(projectId.trim(), cursor, techQuery),
    getNextCursor: (page) => page.nextCursor ?? undefined,
  });

  // FE-P3: active claims over the same central read path (active_claims view).
  // Read-only visualization (lock/warning); claim acquire/release write API is
  // out of FE-P0d (read-only) scope — see tech-debt. The same `techQuery` facet
  // scopes claims to the filtered technology (overlay completeness + perf).
  const claims = useKeysetPagination<ClaimEnvelope, PlatformPage<ClaimEnvelope>>({
    queryKey: queryKeys.project.claims(projectId, techQuery),
    enabled: isValidProjectId(projectId),
    fetchPage: (cursor) => fetchClaimsPage(projectId.trim(), cursor, techQuery),
    getNextCursor: (page) => page.nextCursor ?? undefined,
  });

  // FE-SYNC: central-data freshness for the project. Not facet-scoped (it is a
  // project-wide freshness probe), so it keys on projectId only.
  const syncStatus = useQuery({
    queryKey: queryKeys.project.syncStatus(projectId),
    enabled: isValidProjectId(projectId),
    queryFn: () => fetchSyncStatus(projectId.trim()),
  });

  // Auto-advance claim pages so the per-condition lock overlay stays complete.
  // The active-claim set is bounded (only currently-held claims), unlike the
  // 16k+ coverage conditions which load on user-driven "더보기". Bounded by
  // CLAIMS_MAX_AUTO_PAGES so a non-terminating backend cursor cannot loop.
  const claimsPageCount = claims.pageCount;
  const claimsHasNext = claims.hasNextPage;
  const claimsFetchingNext = claims.isFetchingNextPage;
  const fetchNextClaims = claims.fetchNextPage;
  useEffect(() => {
    if (!claimsHasNext) return;
    if (claimsPageCount >= CLAIMS_MAX_AUTO_PAGES) {
      console.warn(
        `claims auto-advance halted at ${CLAIMS_MAX_AUTO_PAGES} pages — lock overlay ` +
          'may be incomplete (possible backend cursor issue).',
      );
      return;
    }
    if (!claimsFetchingNext) fetchNextClaims();
  }, [claimsHasNext, claimsFetchingNext, fetchNextClaims, claimsPageCount]);

  const rows = coverage.rows;
  const summaries = useMemo(() => summarizeByTechnology(rows), [rows]);
  // Datalist suggestions are data-derived from the loaded coverage (the
  // technologies actually present) — no hardcoded BT/BLE/UNII/DTS enum in the
  // UI. The unfiltered first page surfaces the technology mix for discovery;
  // `(unknown)` (rows with a blank technology) is not a selectable facet.
  const techOptions = useMemo(
    () => summaries.map((s) => s.technology).filter((tech) => tech !== '(unknown)'),
    [summaries],
  );
  const claimsByCondition = useMemo(() => indexClaimsByCondition(claims.rows), [claims.rows]);

  // FE-P3-write: claim acquire/release. The dashboard is already inside a
  // RequirePermission(platform:read) gate; writing additionally needs the
  // platform:claim token (a viewer can see locks but not change them). The
  // operator identity for the ledger row is the authenticated subject.
  const auth = useAuthSession();
  const isAuthenticated = auth.kind === 'authenticated';
  const currentOperator = isAuthenticated ? auth.principal.subject : '';
  // The token may carry platform:claim directly, OR the operator may hold it
  // through their project membership (project_pm / project_engineer / admin).
  // The backend authorizes the UNION (platform_routes authorize: token ∪
  // membership) — so the UI must NOT hard-hide the claim/release controls on a
  // token-only miss (that would wrongly block a legitimate membership-granted
  // operator). The affordance is offered to any authenticated operator; a
  // token-only miss only surfaces a non-blocking advisory, and the backend stays
  // the authority (403 → claimErrorMessage forbidden arm). Mirrors the
  // sample-inventory edit affordance pattern.
  const hasClaimToken =
    isAuthenticated && auth.principal.permissions.includes(PERMISSION_PLATFORM_CLAIM);
  const canAttemptClaim = isAuthenticated;
  const isOnline = useOnlineStatus();

  // Cache key for the claims read AND its optimistic write/invalidation — one
  // factory call (Increment 1 SSOT) so the optimistic setQueryData, the rollback
  // snapshot, and the settle invalidation can never drift from the read above.
  const claimsKey = queryKeys.project.claims(projectId, techQuery);

  // Optimistic claim writes (Increment 3): the lock overlay reflects the change
  // immediately, rolls back if the central write fails, and reconciles with the
  // committed central state on settle (invalidate → refetch). The transform runs
  // over the keyset InfiniteData cache and is derived from the mutation
  // *variables* (never the response — TData `ClaimEventEnvelope` ≠ the cached
  // `ActiveClaimEnvelope` row shape).
  const acquireMutation = useOptimisticMutation<
    ClaimEventEnvelope,
    { technology: string; conditionHash: string },
    InfiniteData<PlatformPage<ClaimEnvelope>>
  >({
    mutationFn: (v) =>
      acquireClaim(projectId.trim(), {
        technology: v.technology,
        condition_hash: v.conditionHash,
        operator: currentOperator,
      }),
    queryKey: claimsKey,
    optimisticUpdate: (current, v) =>
      insertOptimisticClaim(current, {
        claim_id: `${OPTIMISTIC_CLAIM_ID_PREFIX}${v.conditionHash}`,
        project_id: projectId.trim(),
        condition_hash: v.conditionHash,
        technology: v.technology,
        operator: currentOperator,
      }),
  });
  const releaseMutation = useOptimisticMutation<
    ClaimEventEnvelope,
    { claimId: string; conditionHash: string },
    InfiniteData<PlatformPage<ClaimEnvelope>>
  >({
    mutationFn: (v) => releaseClaim(projectId.trim(), v.claimId, { operator: currentOperator }),
    queryKey: claimsKey,
    optimisticUpdate: (current, v) => removeOptimisticClaim(current, v.claimId),
  });

  // Which condition_hash is currently mutating (per-row spinner/disable).
  const pendingCondition =
    (acquireMutation.isPending ? acquireMutation.variables?.conditionHash : undefined) ??
    (releaseMutation.isPending ? releaseMutation.variables?.conditionHash : undefined) ??
    null;
  const claimError = acquireMutation.error ?? releaseMutation.error;
  // WCAG 4.1.3: announce the most recent successful claim write (acquire/release)
  // via the shared selector + StatusMessage (the accessible success counterpart
  // to ErrorState — a screen-reader user otherwise got silence after a write).
  const claimSuccess = selectLatestWriteMessage([
    {
      isSuccess: acquireMutation.isSuccess,
      submittedAt: acquireMutation.submittedAt,
      message: acquireMutation.data
        ? t('routes.projects.claim.acquiredCondition', {
            technology: acquireMutation.data.technology ?? '',
          })
        : '',
    },
    {
      isSuccess: releaseMutation.isSuccess,
      submittedAt: releaseMutation.submittedAt,
      message: releaseMutation.data
        ? t('routes.projects.claim.releasedCondition', {
            technology: releaseMutation.data.technology ?? '',
          })
        : '',
    },
  ]);

  const claimActions: ClaimActions = {
    canAttempt: canAttemptClaim,
    isOnline,
    currentOperator,
    pendingCondition,
    onAcquire: (technology, conditionHash) => acquireMutation.mutate({ technology, conditionHash }),
    onRelease: (claimId, conditionHash) => {
      // Hard guard: never release with an optimistic placeholder id. Between an
      // optimistic acquire settling and the claims refetch landing, the cache
      // still holds the `optimistic:<hash>` placeholder; releasing it would send
      // a fabricated id the central server never issued. Skip — the settle
      // invalidation replaces it with the real claim, which is releasable.
      if (isOptimisticClaimId(claimId)) return;
      releaseMutation.mutate({ claimId, conditionHash });
    },
  };

  // Functional + replace:true so a lookup does not stack browser history (and
  // typing into the draft never queries until submit). Stable identity
  // (useCallback) so the debounce effect can depend on it without re-firing.
  const setParam = useCallback(
    (key: string, value: string): void => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value === '') next.delete(key);
          else next.set(key, value);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // Keep the draft in sync when `?techFilter=` changes from elsewhere (the clear
  // button, back/forward navigation, a shared URL) so the input never drifts
  // from the active facet.
  useEffect(() => {
    setTechDraft(techFilter);
  }, [techFilter]);

  // Debounce: commit the draft to the URL param (→ queryKey → a single fresh
  // read) only after the user pauses. Rapid typing reschedules, so a burst
  // collapses into one server-side narrowing read.
  useEffect(() => {
    if (techDraft === techFilter) return undefined;
    const handle = window.setTimeout(() => setParam('techFilter', techDraft), SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(handle);
    };
  }, [techDraft, techFilter, setParam]);

  const toggleTech = useCallback(
    (tech: string): void => setParam('tech', expandedTech === tech ? '' : tech),
    [setParam, expandedTech],
  );

  // One descriptor drives the header row, the desktop row grid, the phone card
  // projection AND the loading skeleton's column count (§M7.2 + §M8.2), so the
  // reserved shape cannot drift from the arriving shape.
  const matrixColumns = useMemo(
    () => coverageColumns(t, expandedTech, toggleTech),
    [t, expandedTech, toggleTech],
  );

  return (
    <WorkbenchLayout
      className="projects-workbench"
      mainLabel={t('routes.projects.coverageSection')}
      railLabel={t('routes.projects.nextSection')}
      testId="projects-workbench"
      main={
        <div className="projects-workbench__main">
          <Card
            as="section"
            className="projects-workbench-panel"
            aria-labelledby="projects-select-heading"
          >
            <SectionBand
              title={t('routes.projects.selectSection')}
              titleId="projects-select-heading"
            />
            {/* §6.1 Toolbar — 프로젝트 UUID lookup + (조회 후) Tech filter facet +
              우측 정렬 status pill 그룹 (sync freshness / online / claim 권한).
              plan §6.1 "상단 Toolbar: Project UUID / Technology filter / Sync
              freshness / Online·offline / Claim permission" 충실 통합.
              기존 form className "coverage-lookup"/"coverage-tech-filter" 는 폐기
              (Phase 1 토큰 흡수). */}
            <div aria-label={t('routes.projects.lookup.ariaLabel')}>
              <Toolbar ariaLabel={t('routes.projects.lookup.ariaLabel')}>
                <ProjectSelectField
                  value={projectId}
                  onChange={(value) => setParam('project', value)}
                  selectId="project-select"
                  selectTestId="project-select"
                />
                {/* Right-aligned status pill group — plan §6.1 toolbar acceptance. */}
                {isValidProjectId(projectId) && (
                  <div className="toolbar-status-group" data-testid="projects-toolbar-status">
                    {syncStatus.isSuccess && (
                      <StatusBadge
                        status={syncStatus.data.is_stale ? 'stale' : 'pass'}
                        label={
                          syncStatus.data.last_ingested_at === null ||
                          syncStatus.data.last_ingested_at === undefined
                            ? t('routes.projects.toolbar.noSync')
                            : syncStatus.data.is_stale
                              ? t('routes.projects.toolbar.stalePill', {
                                  age: formatAge(syncStatus.data.age_seconds ?? 0),
                                })
                              : t('routes.projects.toolbar.freshPill', {
                                  age: formatAge(syncStatus.data.age_seconds ?? 0),
                                })
                        }
                        testId="toolbar-sync-pill"
                        title={t('routes.projects.toolbar.syncTitle')}
                      />
                    )}
                    <StatusBadge
                      status={isOnline ? 'pass' : 'missing'}
                      label={isOnline ? t('common.online') : t('common.offline')}
                      testId="toolbar-online-pill"
                      title={t('routes.projects.toolbar.onlineTitle')}
                    />
                    {auth.kind === 'authenticated' && (
                      <StatusBadge
                        status={hasClaimToken ? 'pass' : 'missing'}
                        label={
                          hasClaimToken
                            ? t('routes.projects.toolbar.claimPermission')
                            : t('routes.projects.toolbar.claimNoPermission')
                        }
                        testId="toolbar-claim-pill"
                        title={t('routes.projects.toolbar.claimTitle')}
                      />
                    )}
                  </div>
                )}
              </Toolbar>
            </div>

            {/* Technology facet filter (Phase B adoption): narrows the read server-side
              so the dedup workflow reaches a technology without paging all 16k+
              conditions. Free-text + a data-derived <datalist> (suggestions come
              from the loaded coverage, never a hardcoded enum). Shown whenever a
              valid project is selected so the operator can always clear/change it —
              even when the current facet yields no rows. */}
            {isValidProjectId(projectId) && (
              <form
                aria-label={t('routes.projects.techFilter.ariaLabel')}
                onSubmit={(e) => e.preventDefault()}
              >
                <Toolbar ariaLabel={t('routes.projects.techFilter.ariaLabel')} inline>
                  <FieldGroup
                    label={t('routes.projects.techFilter.label')}
                    htmlFor="tech-filter-input"
                  >
                    <input
                      id="tech-filter-input"
                      data-testid="tech-filter-input"
                      list="tech-filter-options"
                      value={techDraft}
                      placeholder={t('routes.projects.techFilter.placeholder')}
                      onChange={(e) => setTechDraft(e.target.value)}
                    />
                    <datalist id="tech-filter-options" data-testid="tech-filter-options">
                      {techOptions.map((tech) => (
                        <option key={tech} value={tech} />
                      ))}
                    </datalist>
                  </FieldGroup>
                  {techDraft !== '' && (
                    <Button
                      type="button"
                      variant="ghost"
                      data-testid="tech-filter-clear"
                      onClick={() => {
                        setTechDraft('');
                        setParam('techFilter', '');
                      }}
                    >
                      {t('routes.projects.techFilter.clear')}
                    </Button>
                  )}
                </Toolbar>
              </form>
            )}

            {/* FE-SYNC: honest online/offline duplicate-prevention boundary (ADR-0005).
              Cross-engineer 중복 방지는 중앙 read model 이 최신(online)일 때만 보장 —
              offline 측정분은 재동기화 전까지 미반영. (live last-sync timestamp 는
              backend sync-status API 필요 → tech-debt.) */}
            <p className="coverage-sync-note" role="note" data-testid="dedup-boundary">
              ⓘ {t('routes.projects.dedupBoundary.prefix')}
              <strong>{t('routes.projects.dedupBoundary.emphasis')}</strong>
              {t('routes.projects.dedupBoundary.suffix')}
            </p>

            {/* FE-SYNC: live central freshness — last ingest + stale warning. */}
            {syncStatus.isSuccess && <SyncStatusBanner status={syncStatus.data} />}
          </Card>

          <section className="projects-workbench-panel" aria-labelledby="projects-coverage-heading">
            <SectionBand
              title={t('routes.projects.coverageSection')}
              titleId="projects-coverage-heading"
            />
            {!isValidProjectId(projectId) && (
              <EmptyState
                testId="projects-project-empty"
                title={t('routes.projects.selectProjectTitle')}
                description={t('routes.projects.selectProjectDescription')}
              />
            )}
            {coverage.isLoading && <DataTableSkeleton columns={matrixColumns.length} rows={6} />}
            {coverage.isError && (
              <ErrorState
                testId="coverage-error"
                message={describeApiError(coverage.error, 'platform', {
                  forbidden: t('routes.projects.coverage.forbidden'),
                  default:
                    (coverage.error as ApiError).status === 400
                      ? t('routes.projects.coverage.invalidProjectId')
                      : t('routes.projects.coverage.fetchFailed'),
                  network: t('routes.projects.coverage.network'),
                })}
              />
            )}
            {coverage.isSuccess &&
              rows.length === 0 &&
              (techQuery !== undefined ? (
                <EmptyState
                  testId="coverage-empty"
                  title={t('routes.projects.coverage.emptyFilteredTitle', { tech: techQuery })}
                  description={t('routes.projects.coverage.emptyFilteredDescription')}
                />
              ) : (
                <EmptyState
                  testId="coverage-empty"
                  title={t('routes.projects.coverage.emptyTitle')}
                  description={t('routes.projects.coverage.emptyDescription')}
                />
              ))}

            {coverage.isSuccess && summaries.length > 0 && (
              <>
                {/* M5 (D6): `summarizeByTechnology` folds only the pages loaded so
                  far, but the matrix presented those sums as if they were the
                  project's. A project carries 16k+ conditions and the first page
                  is a small slice, so "완료 3 / 조건 5" could be read as a
                  finished technology while hundreds of conditions sat unloaded.
                  Reuses the `sessions.tsx` truncated-pill idiom verbatim, and —
                  keyed on `hasNextPage` — disappears once everything is loaded,
                  so it never cries wolf on a fully-loaded project. */}
                {coverage.hasNextPage && (
                  <p className="section-hint" data-testid="coverage-truncated-note">
                    <StatusBadge
                      status="stale"
                      label={t('routes.projects.matrix.truncatedPill')}
                      testId="coverage-truncated-pill"
                      title={t('routes.projects.matrix.truncatedPillTitle')}
                    />{' '}
                    {t('routes.projects.matrix.truncatedNote')}
                  </p>
                )}
                <DataTable
                  testId="coverage-matrix"
                  caption={t('routes.projects.matrix.caption')}
                  columns={matrixColumns}
                  rows={summaries}
                  rowKey={(summary) => summary.technology}
                  rowTestId="tech-row"
                  expansion={coverageExpansion(t, expandedTech, claimsByCondition, claimActions)}
                />
                {/* Keyset pagination: more pages exist → user-driven incremental load.
                  The matrix reflects pages loaded so far (a project can have 16k+
                  conditions). */}
                {coverage.hasNextPage && (
                  <LoadMoreButton
                    testId="coverage-load-more"
                    onClick={coverage.fetchNextPage}
                    isFetching={coverage.isFetchingNextPage}
                  />
                )}
              </>
            )}
          </section>
        </div>
      }
      rail={
        <div
          className="projects-workbench__rail"
          aria-labelledby="projects-next-heading"
          data-testid="projects-next-actions"
        >
          <ProjectsNextActions projectId={projectId} />
          {/* Membership-effective permission affordance: the token may lack
              platform:claim while the operator holds it via project membership. The
              backend authorizes the union, so the controls stay offered and only a
              non-blocking advisory shows on a token-only miss (no false block). */}
          {isValidProjectId(projectId) && canAttemptClaim && !hasClaimToken && (
            <p className="coverage-token-note" role="note" data-testid="claim-token-hint">
              ⓘ {t('routes.projects.claimTokenHint')}
            </p>
          )}
          {/* FE-P3-write: offline disables claim writes (central ledger unreachable). */}
          {isValidProjectId(projectId) && canAttemptClaim && !isOnline && (
            <p className="coverage-offline-note" role="status" data-testid="claim-offline">
              ⚠ {t('routes.projects.offlineNote')}
            </p>
          )}
          {claimError && (
            <ErrorState testId="claim-error" message={claimErrorMessage(claimError)} />
          )}
          {claimSuccess !== null && !claimError && (
            <StatusMessage tone="success" testId="claim-success" message={claimSuccess} />
          )}
        </div>
      }
    />
  );
}

function ProjectsNextActions({ projectId }: { readonly projectId: string }): JSX.Element {
  const { t } = useT();
  const actions = projectWorkflowActions(projectId, [
    'fields',
    'progress',
    'inventory',
    'testPlans',
    'chambers',
    'membership',
    'reports',
    'testReports',
  ]);
  const actionById = new Map(actions.map((action) => [action.id, action.href]));
  return (
    <section className="projects-next">
      <SectionBand title={t('routes.projects.nextSection')} titleId="projects-next-heading" />
      <p className="projects-next__state" data-testid="projects-next-state">
        {isValidProjectId(projectId)
          ? t('routes.projects.selectedProject', { project: projectId })
          : t('routes.projects.noProjectSelected')}
      </p>
      <div className="projects-next__actions">
        <Link
          to={actionById.get('fields') ?? '/fields'}
          className="projects-next__action"
          data-testid="projects-next-fields"
        >
          {t('routes.fields.title')}
        </Link>
        <Link
          to={actionById.get('progress') ?? '/progress'}
          className="projects-next__action"
          data-testid="projects-next-progress"
        >
          {t('routes.projects.nextProgress')}
        </Link>
        <Link
          to={actionById.get('inventory') ?? '/inventory'}
          className="projects-next__action"
          data-testid="projects-next-inventory"
        >
          {t('routes.projects.nextInventory')}
        </Link>
        <Link
          to={actionById.get('testPlans') ?? '/test-plans'}
          className="projects-next__action"
          data-testid="projects-next-test-plans"
        >
          {t('routes.projects.nextTestPlans')}
        </Link>
        <Link
          to={actionById.get('chambers') ?? '/chambers'}
          className="projects-next__action"
          data-testid="projects-next-chambers"
        >
          {t('routes.progress.nextChambers')}
        </Link>
        <Link
          to={actionById.get('membership') ?? '/membership'}
          className="projects-next__action"
          data-testid="projects-next-membership"
        >
          {t('routes.projects.nextMembership')}
        </Link>
        <Link
          to={actionById.get('reports') ?? '/reports'}
          className="projects-next__action"
          data-testid="projects-next-reports"
        >
          {t('routes.projects.nextReports')}
        </Link>
        <Link
          to={actionById.get('testReports') ?? ROUTE_PATHS.testReports}
          className="projects-next__action"
          data-testid="projects-next-test-reports"
        >
          {t('routes.testReports.title')}
        </Link>
      </div>
      <p className="section-hint">{t('routes.projects.nextHint')}</p>
    </section>
  );
}

/** Per-condition claim/release control (FE-P3-write). Renders nothing for an
 *  unauthenticated visitor (`canAttempt` false), or for a condition held by
 *  *another* operator (the lock badge already conveys that — only the holder can
 *  release). NOTE: it is intentionally offered to authenticated operators who
 *  lack the platform:claim token, because the backend may grant claim via project
 *  membership (token ∪ membership) — the backend stays the authority and a 403
 *  surfaces through claimErrorMessage. A free condition shows "점유"; the holder's
 *  own condition shows "해제". Both are disabled offline (central ledger
 *  unreachable) or while a write is in flight. */
function ClaimControl({
  technology,
  conditionHash,
  claim,
  actions,
  surface,
}: {
  readonly technology: string;
  readonly conditionHash: string;
  readonly claim: ClaimEnvelope | undefined;
  readonly actions: ClaimActions;
  readonly surface: DataTableSurface;
}): JSX.Element | null {
  const { t } = useT();
  if (!actions.canAttempt || conditionHash === '') return null;
  const busy = actions.pendingCondition === conditionHash;
  const offline = !actions.isOnline;

  if (claim) {
    // Only the holder can release their own claim; another operator's lock is
    // read-only here (the badge already shows who holds it).
    if (claim.operator !== actions.currentOperator) return null;
    // An optimistic (not-yet-reconciled) claim has a placeholder id the server
    // never issued — release is disabled until the settle invalidation refetches
    // the real claim. (Defense-in-depth alongside the onRelease guard.)
    const optimisticPending = isOptimisticClaimId(claim.claim_id);
    return (
      <Button
        type="button"
        variant="ghost"
        className="condition-release"
        data-testid={surfaceTestId(surface, 'condition-claim-release')}
        disabled={busy || offline || optimisticPending}
        title={
          optimisticPending
            ? t('routes.projects.control.releasePendingTitle')
            : offline
              ? t('routes.projects.control.releaseOfflineTitle')
              : undefined
        }
        onClick={() => actions.onRelease(claim.claim_id, conditionHash)}
      >
        {busy || optimisticPending
          ? t('routes.projects.control.releasing')
          : t('routes.projects.control.release')}
      </Button>
    );
  }

  return (
    <Button
      type="button"
      variant="primary"
      className="condition-claim"
      data-testid={surfaceTestId(surface, 'condition-claim-acquire')}
      disabled={busy || offline}
      title={offline ? t('routes.projects.control.acquireOfflineTitle') : undefined}
      onClick={() => actions.onAcquire(technology, conditionHash)}
    >
      {busy ? t('routes.projects.control.acquiring') : t('routes.projects.control.acquire')}
    </Button>
  );
}

/**
 * Coverage-matrix columns (§M7.2 + §M8.2).
 *
 * The header row, the loading skeleton and the phone card projection all read
 * this one descriptor, so the number of placeholder columns can never drift
 * from the number that arrives — the layout jump the contract bans is
 * structurally impossible rather than merely "currently correct".
 *
 * The matrix is 1:N (a technology row expands into its condition rows), which a
 * flat column list cannot express. That half is carried by the additive
 * `expansion` descriptor (`coverageExpansion` below) instead of by an escape
 * back to the slot API, so this call site gets the responsive fold as well.
 */
function coverageColumns(
  t: Translate,
  expandedTech: string,
  onToggle: (technology: string) => void,
): readonly DataTableColumn<TechSummary>[] {
  return [
    {
      key: 'technology',
      header: t('routes.projects.matrix.colTechnology'),
      priority: 'primary',
      rowHeader: true,
      cell: (summary, surface) => (
        <Button
          type="button"
          variant="ghost"
          className="touch-target"
          data-testid={surfaceTestId(surface, `tech-toggle-${summary.technology}`)}
          aria-expanded={expandedTech === summary.technology}
          onClick={() => {
            onToggle(summary.technology);
          }}
        >
          {summary.technology}
        </Button>
      ),
    },
    {
      key: 'total',
      header: t('routes.projects.matrix.colConditions'),
      priority: 'secondary',
      className: 'data-cell-numeric',
      testId: 'tech-total',
      cell: (summary) => summary.total,
    },
    {
      key: 'done',
      header: t('routes.projects.matrix.colDone'),
      priority: 'secondary',
      className: 'data-cell-numeric',
      cell: (summary) => summary.done,
    },
    {
      key: 'inProgress',
      header: t('routes.projects.matrix.colInProgress'),
      priority: 'secondary',
      className: 'data-cell-numeric',
      cell: (summary) => summary.inProgress,
    },
    {
      // Deliberately NOT `detail`: a folded column is re-rendered in the
      // overflow line, which would put two copies of the same badge (and its
      // testid) on the row surface. Duplicate-quality is also the reason this
      // dashboard exists, so it earns a place in the row grid.
      key: 'duplicate',
      header: t('routes.projects.matrix.colDuplicate'),
      priority: 'secondary',
      cell: (summary, surface) => <TechDuplicateCell summary={summary} surface={surface} />,
    },
    {
      // Provenance — useful, but the first thing that can leave the row grid.
      key: 'operator',
      header: t('routes.projects.matrix.colOperator'),
      priority: 'detail',
      cell: (summary) => orDash(summary.operators.join(', ')),
    },
  ];
}

/**
 * Route `data-testid`s belong to the ROW surface only.
 *
 * The card list is a second rendering of the same rows and is always in the
 * DOM (the band swap is CSS-only), so mirroring an id there would make both
 * `getByTestId` and Playwright's strict locator ambiguous. The values are still
 * all present on the card — only the test hooks are single-surface, which is
 * the convention `DataTableColumn.testId` already documents.
 */
function surfaceTestId(surface: DataTableSurface, testId: string): string | undefined {
  return surface === 'row' ? testId : undefined;
}

/** Same rule for a PRIMITIVE's `testId` prop. `exactOptionalPropertyTypes`
 *  rejects an explicit `undefined`, so the prop is omitted rather than blanked. */
function surfaceTestIdProps(
  surface: DataTableSurface,
  testId: string,
): { readonly testId?: string } {
  return surface === 'row' ? { testId } : {};
}

function TechDuplicateCell({
  summary,
  surface,
}: {
  readonly summary: TechSummary;
  readonly surface: DataTableSurface;
}): JSX.Element {
  const { t } = useT();
  return (
    <>
      {/* FE-P3 duplicate-quality: ⚠ is reserved for TRUE cross-engineer
          duplicates (distinct_operator_count > 1). A repeated measure by the
          same engineer is shown as a softer "재측정" without the warning. */}
      {summary.crossEngineerDuplicate > 0 && (
        <StatusBadge
          status="duplicate"
          label={t('routes.projects.matrix.crossBadge', { n: summary.crossEngineerDuplicate })}
          {...surfaceTestIdProps(surface, 'tech-cross-duplicate')}
          title={t('routes.projects.matrix.crossBadgeTitle')}
        />
      )}
      {summary.repeated > 0 ? (
        <>
          {summary.crossEngineerDuplicate > 0 && ' '}
          <StatusBadge
            status="running"
            label={t('routes.projects.matrix.repeatedBadge', { n: summary.repeated })}
            {...surfaceTestIdProps(surface, 'tech-repeated')}
            title={t('routes.projects.matrix.repeatedBadgeTitle')}
          />
        </>
      ) : summary.crossEngineerDuplicate > 0 ? null : (
        // No repeated measure and no cross-engineer duplicate — render the
        // neutral em-dash (orDash convention) rather than a bare numeric 0
        // in a column that otherwise holds status badges.
        '—'
      )}
    </>
  );
}

/**
 * The 1:N half of the matrix: a technology's condition rows.
 *
 * Rendered as a list rather than as nested `<tr>`s so the identical markup can
 * ride inside the phone card's `<details>`, which is what keeps the card
 * projection lossless (the table — and with it the expansion row — is
 * `display: none` below `--bp-sm`).
 */
function coverageExpansion(
  t: Translate,
  expandedTech: string,
  claimsByCondition: ReadonlyMap<string, ClaimEnvelope>,
  claimActions: ClaimActions,
): DataTableExpansion<TechSummary> {
  return {
    isExpanded: (summary) => expandedTech === summary.technology,
    cardSummary: (summary) =>
      t('routes.projects.matrix.conditionsDisclosure', { n: summary.rows.length }),
    render: (summary, surface) => (
      <ul className="condition-list">
        {summary.rows.map((row, rowIndex) => (
          <ConditionLine
            key={`${summary.technology}-${row.condition_hash ?? String(rowIndex)}`}
            technology={summary.technology}
            row={row}
            index={rowIndex}
            surface={surface}
            claimsByCondition={claimsByCondition}
            claimActions={claimActions}
          />
        ))}
      </ul>
    ),
  };
}

function ConditionLine({
  technology,
  row,
  index,
  surface,
  claimsByCondition,
  claimActions,
}: {
  readonly technology: string;
  readonly row: CoverageEnvelope;
  readonly index: number;
  readonly surface: DataTableSurface;
  readonly claimsByCondition: ReadonlyMap<string, ClaimEnvelope>;
  readonly claimActions: ClaimActions;
}): JSX.Element {
  const { t } = useT();
  const conditionHash = row.condition_hash ?? '';
  const claim = conditionHash ? claimsByCondition.get(conditionHash) : undefined;
  const verdictKind = verdictToStatusKind(row.latest_verdict);
  return (
    /* §5④ 행 분해 — 좌: 시험 조건(사람말) / 중: 상태칩(합·부 우선 +
       중복·재측정·점유 잠금) / 우: 담당자·시각 + 점유 액션 1개. */
    <li className="condition-list__item" data-testid={surfaceTestId(surface, 'condition-row')}>
      <span data-testid={surfaceTestId(surface, 'condition-label')}>
        {t('routes.projects.condition.label', { n: index + 1 })}
      </span>
      <span
        className="condition-row__status"
        data-testid={surfaceTestId(surface, 'condition-verdict')}
      >
        {verdictKind !== null ? (
          <StatusBadge status={verdictKind} label={row.latest_verdict ?? ''} />
        ) : (
          orDash(row.latest_verdict)
        )}
        {isCrossEngineerDuplicate(row) ? (
          <StatusBadge
            status="duplicate"
            {...surfaceTestIdProps(surface, 'condition-cross-duplicate')}
            label={t('routes.projects.condition.crossDuplicate', {
              operators: row.distinct_operator_count ?? '?',
              attempts: row.attempt_count ?? 0,
            })}
          />
        ) : (row.attempt_count ?? 0) > 1 ? (
          <StatusBadge
            status="running"
            {...surfaceTestIdProps(surface, 'condition-repeated')}
            label={t('routes.projects.condition.repeated', { attempts: row.attempt_count })}
          />
        ) : null}
        {claim && (
          <StatusBadge
            status="claimed"
            {...surfaceTestIdProps(surface, 'condition-claimed')}
            label={
              t('routes.projects.condition.heldBy', { operator: orDash(claim.operator) }) +
              (claim.expires_at
                ? t('routes.projects.condition.expires', { at: claim.expires_at })
                : '')
            }
          />
        )}
      </span>
      <span className="condition-list__meta">
        <span>
          {orDash(row.latest_operator)} · {orDash(row.latest_measured_at)}
        </span>
        <ClaimControl
          technology={technology}
          conditionHash={conditionHash}
          claim={claim}
          actions={claimActions}
          surface={surface}
        />
      </span>
    </li>
  );
}

export default ProjectsRoute;
