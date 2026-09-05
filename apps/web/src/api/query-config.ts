/**
 * Query key + cache strategy SSOT (fe-query-key-cache-ssot, Increment 1).
 *
 * Before this module the `apps/web` routes hard-coded TanStack Query keys as
 * inline arrays in 15 places, and each route re-assembled the *same* key by
 * hand for both the `useQuery`/`useInfiniteQuery` read and the matching
 * `invalidateQueries` write (e.g. `['project-claims', projectId, techQuery]`
 * lived in both `projects.tsx:286` and `:352`). If the key shape changed on
 * only one side (say a facet was added), cache invalidation broke *silently* —
 * a latent bug class. Refetch tuning (`refetchInterval: 2000`) was likewise
 * scattered as magic numbers with no criticality-based policy.
 *
 * This file is the single source of truth, mirroring the equipment-management
 * platform's proven pattern (hierarchical `queryKeys` + `CACHE_TIMES` +
 * `REFETCH_STRATEGIES`):
 *
 *   - `queryKeys` — domain-grouped factory functions. A read and its
 *     invalidation now call the *same* function, so the key cannot drift.
 *   - `CACHE_TIMES` — SHORT / MEDIUM / LONG duration tiers.
 *   - `REFETCH_STRATEGIES` — CRITICAL / IMPORTANT / NORMAL / STATIC bundles
 *     (staleTime / gcTime / refetchInterval / refetchOnWindowFocus) keyed to a
 *     view's criticality instead of ad-hoc per-route literals.
 *
 * Behavior is preserved byte-for-byte: every factory returns the *exact* array
 * the route previously inlined. The keys are not "cleaned up" — only their
 * origin moves to this SSOT. The `tests/test_frontend_query_key_ssot.py` seal
 * (backend-only CI lane) and the route vitest suites are the regression oracle.
 *
 * All exports are `as const` so the literal prefixes survive for TanStack
 * Query's prefix-matched invalidation (a coverage/claims invalidation can key
 * on the resource prefix array).
 */

// ---------------------------------------------------------------------------
// Cache duration tiers (gcTime / staleTime budgets). This module is the only
// place these literals live — routes reference the named tiers, never numbers.
// ---------------------------------------------------------------------------

export const CACHE_TIMES = {
  /** 1 minute — short-lived, frequently changing operator data. */
  SHORT: 60_000,
  /** 5 minutes — default operator view tolerance. */
  MEDIUM: 5 * 60_000,
  /** 30 minutes — near-static reference data. */
  LONG: 30 * 60_000,
} as const;

/**
 * `staleTime` for NORMAL/IMPORTANT views. Operator dashboards tolerate slightly
 * stale data (WS events invalidate explicitly when real-time matters). This is
 * the value the pre-SSOT `QueryClient` default carried inline; keeping it here
 * preserves that behavior byte-identically.
 */
const STANDARD_STALE_TIME_MS = 30_000;

/**
 * Active-run progress poll cadence. Previously the magic `2000` inside
 * `control.tsx`'s `refetchInterval`; now owned here as the CRITICAL strategy's
 * polling interval so the route carries no literal.
 */
const CRITICAL_REFETCH_INTERVAL_MS = 2_000;

/**
 * Server-side chamber heartbeat TTL default, in seconds — a MIRROR of the
 * backend domain constant `src/domain/models/chamber_node.py::
 * DEFAULT_HEARTBEAT_TTL_SECONDS` (fe-w2-b-execution-freshness M4, 2026-07-28).
 *
 * The value cannot be imported across the language boundary, so it is declared
 * here and the drift is sealed rather than trusted: `tests/
 * test_frontend_query_key_ssot.py::TestChamberHeartbeatTtlMirrorParity` fails if
 * the backend default moves and this mirror does not. Mirroring it explicitly is
 * the point — the alternative (a bare `45_000` with a comment claiming it is
 * "half the TTL") is a claim nothing checks, and it was exactly such an unchecked
 * claim that put `30` into the planning input for this milestone.
 *
 * It exists here only to DERIVE the supervision cadence below; no consumer reads
 * it as a per-chamber TTL (each row carries its own `heartbeat_ttl_seconds`).
 */
export const CHAMBER_HEARTBEAT_TTL_SECONDS_DEFAULT = 90;

/**
 * How many polls fit inside one TTL. Sampling a state change twice per period of
 * the phenomenon that produces it bounds the display's recognition lag *below*
 * the timescale of the thing being displayed; one poll per TTL would let the
 * screen be a full transition behind.
 */
const MONITORED_POLLS_PER_TTL = 2;

/**
 * Supervision poll cadence — chamber availability (M4).
 *
 * Availability flips to `offline` server-side once `heartbeat_ttl_seconds`
 * elapses with no heartbeat, and the read service derives that against its own
 * clock at read time. So the ONLY staleness the screen can suffer is "how long
 * since my last read", and this cadence is the bound on it: a transition is on
 * screen within one poll of happening, i.e. well inside one TTL.
 *
 * Why not CRITICAL (2s): the live axis is already served by the WS progress
 * relay, so this poll is the *fallback* channel for availability/heartbeat
 * transitions, not a live channel. A 2s poll would multiply request volume by
 * ~22x to observe a transition whose own timescale is 90 seconds.
 *
 * Why not IMPORTANT (`false`): that is what shipped, and it made the fleet table
 * a still photo — a chamber that went offline stayed "idle" on a wall monitor
 * until somebody refocused the tab.
 */
const MONITORED_REFETCH_INTERVAL_MS =
  (CHAMBER_HEARTBEAT_TTL_SECONDS_DEFAULT * 1_000) / MONITORED_POLLS_PER_TTL;

/**
 * Consecutive `is_running: false` snapshots tolerated before chamber-progress
 * polling parks (fe-data-layer-robustness M1, 2026-07-19).
 *
 * `ChamberSessionProgress` carries no explicit terminal flag — only
 * `{is_running, completed, total, ratio}`. A run that has genuinely finished is
 * recognizable (`completed >= total && total > 0`), but a *transient*
 * `is_running: false` is not distinguishable from a finished one by that field
 * alone: it is also what the central relay reports between a start command and
 * the node's first heartbeat, and after a node restart re-registers a chamber
 * whose run has not yet re-reported. The old policy parked polling forever on
 * the FIRST such snapshot, so the operator's only remote window onto a live
 * chamber froze on a stale value with no way to self-heal (the hook also never
 * inherited `refetchOnWindowFocus`, so a focus return did not resume it).
 *
 * Grace is expressed in POLL COUNTS rather than milliseconds so the decision
 * stays a pure function of query state (no wall clock inside the policy) and
 * the seal can drive it deterministically. 3 polls × the CRITICAL cadence is
 * ~6s of tolerance — long enough to cover a heartbeat gap, short enough that a
 * chamber left idle stops polling promptly.
 */
export const CHAMBER_PROGRESS_NOT_RUNNING_GRACE_POLLS = 3;

/**
 * Consecutive failed polls tolerated before a lifecycle poll parks
 * (fe-w2-a-result-report-honesty M2, 2026-07-28).
 *
 * A polled query whose fetch keeps failing used to re-fire at the fixed CRITICAL
 * cadence forever: the node is unreachable or answering 403, and the browser
 * asks again every 2s for the rest of the session while the surface shows
 * nothing about it. Parking after a bounded number of attempts makes the
 * rendered error the *final* statement rather than a flicker between silent
 * retries — and stops a dead node being hammered.
 */
export const ERROR_POLL_MAX_CONSECUTIVE_FAILURES = 3;

/**
 * Backoff growth per consecutive failure. Kept here (not at a call site) so the
 * retry cadence stays one policy, exactly like the poll cadence itself.
 */
const ERROR_POLL_BACKOFF_MULTIPLIER = 2;

/**
 * Poll interval to use after `consecutiveFailures` failed attempts, or `false`
 * to stop polling entirely.
 *
 * Pure function of the query's failure count — no wall clock — so the seal can
 * drive it deterministically. `consecutiveFailures <= 0` (a successful poll)
 * returns the base cadence unchanged, which keeps the healthy path
 * byte-identical to the pre-M2 behaviour.
 */
export function errorBackoffPollInterval(
  baseIntervalMs: number,
  consecutiveFailures: number,
): number | false {
  if (consecutiveFailures <= 0) return baseIntervalMs;
  if (consecutiveFailures >= ERROR_POLL_MAX_CONSECUTIVE_FAILURES) return false;
  return baseIntervalMs * ERROR_POLL_BACKOFF_MULTIPLIER ** consecutiveFailures;
}

// ---------------------------------------------------------------------------
// Refetch strategies — criticality-based bundles. A route picks a strategy by
// how live the view must be, instead of hand-tuning staleTime/refetchInterval.
// ---------------------------------------------------------------------------

export const REFETCH_STRATEGIES = {
  /** Live-polling views (active run progress). Always-fresh, polls on cadence. */
  CRITICAL: {
    staleTime: 0,
    gcTime: CACHE_TIMES.SHORT,
    refetchInterval: CRITICAL_REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: true,
  },
  /**
   * Supervision views — polled, but on the timescale of the thing they watch
   * rather than the timescale of a live feed (chamber availability). The tier
   * that was missing between CRITICAL and IMPORTANT: "must not go stale" did
   * not previously imply "polls", so such views silently took IMPORTANT and
   * froze at page load.
   *
   * `refetchIntervalInBackground` is declared explicitly, not left to the
   * library default: a supervision screen is precisely the one parked on a
   * second monitor for a whole shift, and a hidden tab that keeps polling is
   * the request-volume growth this tier must not cause.
   */
  MONITORED: {
    staleTime: 0,
    gcTime: CACHE_TIMES.SHORT,
    refetchInterval: MONITORED_REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: true,
    refetchIntervalInBackground: false,
  },
  /** Important but not polled — refetch on focus, short stale window. */
  IMPORTANT: {
    staleTime: STANDARD_STALE_TIME_MS,
    gcTime: CACHE_TIMES.MEDIUM,
    refetchInterval: false,
    refetchOnWindowFocus: true,
  },
  /** Default operator views — slightly stale tolerated, no focus burst. */
  NORMAL: {
    staleTime: STANDARD_STALE_TIME_MS,
    gcTime: CACHE_TIMES.MEDIUM,
    refetchInterval: false,
    refetchOnWindowFocus: false,
  },
  /** Near-static reference data — long cache, no auto refetch. */
  STATIC: {
    staleTime: CACHE_TIMES.LONG,
    gcTime: CACHE_TIMES.LONG,
    refetchInterval: false,
    refetchOnWindowFocus: false,
  },
} as const;

// ---------------------------------------------------------------------------
// Query key factory — domain-grouped. A read and its invalidation MUST call the
// same function. Each leaf returns the byte-identical array the route inlined.
// ---------------------------------------------------------------------------

export const queryKeys = {
  /** Session API — single-session info/progress surface. */
  session: {
    all: ['session'] as const,
    info: () => ['session', 'info'] as const,
    progress: () => ['session', 'progress'] as const,
  },
  /** Platform project reads — entry list / detail + coverage / claims / sync / membership. */
  project: {
    /**
     * 프로젝트 목록 읽기 전체를 덮는 무효화 프리픽스 (생성/편집/완료/재개 후 호출).
     *
     * 아래 두 리프(`directory` · `pickerOptions`)가 모두 이 프리픽스 밑에 있으므로,
     * mutation 은 `invalidateQueries({ queryKey: lists() })` 한 번으로 모든 status
     * 변형 · 모든 검색어 변형을 한꺼번에 갱신한다(mutation 쪽 배선 무접촉).
     */
    lists: () => ['project-list'] as const,
    /**
     * 서버측 검색 + keyset 으로 읽는 프로젝트 디렉토리 (W3-B M-B, 2026-07-30).
     * `GET /platform/projects?limit=&status=&q=&cursor=`.
     *
     * **형제 리프와 키를 공유할 수 없다.** 이 읽기의 캐시 값은 `useInfiniteQuery`
     * 의 `{ pages: [...] }` 이고 `pickerOptions` 의 캐시 값은 평평한 `PlatformPage`
     * 하나다. 같은 키를 쓰면 한 캐시 슬롯에 두 형상이 들어가는데, 그 결함은
     * **typecheck 를 통과하고 런타임에 깨진다** — 그래서 리프를 나눈다.
     *
     * 프리픽스는 그래도 `'project-list'` 를 유지한다: 생성/편집/완료/재개 mutation
     * 이 `lists()`(`['project-list']`) 로 무효화하므로, 프리픽스 아래에 있어야
     * 이 리프까지 계속 덮인다(mutation 배선 무접촉).
     *
     * `q` 가 키의 일부인 것은 페이징 리셋 메커니즘이기도 하다 — 검색어가 바뀌면
     * 다른 캐시 항목이 되어 `initialPageParam` 부터 다시 시작한다(`remove()` /
     * `reset()` 을 손으로 호출할 필요가 없다). `status` 도 같은 이유로 키에 있다.
     */
    directory: (status = 'active', q?: string) => ['project-list', 'directory', status, q] as const,
    /**
     * 프로젝트 선택기(`ProjectSelectField`)가 읽는 **검색 가능한 한 페이지**
     * (W3-B M-C, 2026-07-30). `GET /platform/projects?limit=&status=active&q=`.
     *
     * 선택기는 `<select>` 안에 [더보기]를 둘 수 없어(접근성·의미 모두 오류)
     * 페이지 2+ 를 읽지 않는다 → `useInfiniteQuery` 가 아니라 평평한
     * `PlatformPage` 하나를 담는 `useQuery` 다. 그 **형상 차이**가 `directory` 와
     * 리프를 나눈 이유이고, 위 `directory` 주석의 경고와 같은 사실을 반대편에서
     * 말한 것이다.
     *
     * `status` 는 키에 없다 — 선택기는 항상 'active' 만 읽는다(진행 중 프로젝트를
     * 골라 작업하지, 완료된 것을 고르지 않는다). 축이 없는 것을 키에 넣으면
     * 불변인 값이 키에 섞여 무엇이 캐시를 나누는지 흐려진다.
     */
    pickerOptions: (q?: string) => ['project-list', 'options', q] as const,
    /**
     * 생성 폼 신청자 자동완성 (2026-09-04). `GET /platform/applicants?q=&limit=`.
     *
     * **프리픽스가 `'project-list'` 가 아니다.** 프로젝트 생성/편집 mutation 이
     * `lists()` 로 무효화하는데, 신청자 디렉터리도 그 아래 두면 프로젝트를 하나
     * 만들 때마다 열려 있지도 않은 자동완성 목록까지 다시 읽는다. 신청자는 프로젝트
     * 행에서 **파생**되므로 새 프로젝트가 생기면 실제로 낡기는 하지만, 그 낡음은
     * 다음에 폼을 열 때 `staleTime` 이 자연스럽게 처리한다 — 즉시성이 필요한 읽기가
     * 아니다.
     *
     * `q` 가 키의 일부라 검색어마다 캐시가 나뉜다(타이핑을 되돌리면 이전 결과가
     * 즉시 보인다).
     */
    /**
     * 신청자 디렉터리 **전체**를 덮는 무효화 프리픽스 — 검색어 변형이 전부 이 아래
     * 있으므로, 새 프로젝트가 신청자를 등록했을 때 한 번의 무효화로 모든 변형이
     * 낡음 표시된다. 리프 팩토리를 무인자로 부르면 `[…, undefined]` 라는 **다른
     * 키 하나**가 되어 나머지 변형이 낡은 채 남는다(`lists()` ↔ `directory()` 와
     * 같은 관계다).
     */
    applicantDirectory: () => ['applicant-directory'] as const,
    applicants: (q?: string) => ['applicant-directory', q] as const,
    /** 한 프로젝트 상세 (모델 + 샘플 목록). `GET /platform/projects/{id}`. */
    detail: (projectId: string) => ['project-detail', projectId] as const,
    coverage: (projectId: string, techQuery?: string) =>
      ['project-coverage', projectId, techQuery] as const,
    claims: (projectId: string, techQuery?: string) =>
      ['project-claims', projectId, techQuery] as const,
    syncStatus: (projectId: string) => ['project-sync-status', projectId] as const,
    memberships: (projectId: string) => ['project-memberships', projectId] as const,
    /** Phase 6 시간가중 진행률 rollup. `GET /platform/projects/{id}/progress`. */
    progress: (projectId: string) => ['project-progress', projectId] as const,
    /** plot-custody ① 플롯 보관 현황 rollup. `GET /platform/projects/{id}/artifact-custody`. */
    artifactCustody: (projectId: string) => ['project-artifact-custody', projectId] as const,
    /** 한 세션의 보관 상세(조치 가능한 항목). */
    artifactCustodySnapshot: (projectId: string, snapshotId: string) =>
      ['project-artifact-custody-snapshot', projectId, snapshotId] as const,
    /** P5-C reportable sessions + node routing. */
    reportSessions: (projectId: string | null) => ['project-report-sessions', projectId] as const,
    /**
     * Phase G 성적서(test_reports) 인스턴스 대장.
     * `GET /platform/projects/{id}/reports`. 생성 mutation 이 같은 팩토리로
     * 무효화하므로 읽기 키와 무효화 키가 드리프트할 수 없다.
     *
     * `/reports`(headless 리포트 생성 요청 큐)의 `report.*` 네임스페이스와는
     * 별개다 — 도메인·권한·백엔드 surface 가 모두 다르므로 키도 분리한다.
     */
    reports: (projectId: string) => ['project-reports', projectId] as const,
    /**
     * 자동 인용(citation). 프로젝트 스코프 조회이며 `edition` 은 파생
     * `report_number` 만 좌우하는 선택적 정제 축이라 키의 일부다(edition 을 바꾸면
     * 다른 응답이므로 같은 캐시 항목을 재사용하면 안 된다).
     */
    reportCitation: (projectId: string, edition?: string, sessionId?: string) =>
      ['project-report-citation', projectId, edition, sessionId] as const,
    /**
     * 성적서 §6 장비목록 (2026-08-07). 두 리프의 캐시 형상이 다르다:
     * 목록은 프로젝트 스코프 요약 배열(항목 수 포함)이고, 상세는 목록 하나의
     * 항목 + 두 표의 열 명세다. 상세를 프로젝트 키 아래 두면 목록 하나를 저장할
     * 때마다 다른 목록의 캐시까지 무효화된다.
     */
    equipmentLists: (projectId: string) => ['project-equipment-lists', projectId] as const,
    equipmentList: (projectId: string, equipmentListId: string) =>
      ['project-equipment-list', projectId, equipmentListId] as const,
    resultSelections: (projectId: string, providerId: string) =>
      ['project-result-selections', projectId, providerId] as const,
    resultAttempts: (projectId: string, providerId: string, conditionHash: string) =>
      ['project-result-attempts', projectId, providerId, conditionHash] as const,
    resultReferences: (projectId: string, providerId?: string, state?: string) =>
      ['project-result-references', projectId, providerId ?? null, state ?? null] as const,
  },
  /** Web-authoritative sample inventory. Every filter axis is part of the list key. */
  sampleInventory: {
    all: ['sample-inventory'] as const,
    list: (
      filters: {
        projectId?: string;
        team?: string;
        status?: 'active' | 'deleted' | 'all';
        asOf?: string;
        after?: string;
        limit?: number;
        includeDeleted?: boolean;
      } = {},
    ) =>
      [
        'sample-inventory',
        'list',
        filters.projectId ?? null,
        filters.team ?? null,
        filters.status ?? null,
        filters.asOf ?? null,
        filters.after ?? null,
        filters.limit ?? null,
        filters.includeDeleted ?? null,
      ] as const,
    detail: (projectId: string, sampleId: string, asOf?: string) =>
      ['sample-inventory', 'detail', projectId, sampleId, asOf ?? null] as const,
    history: (projectId: string, sampleId: string, after?: string, limit?: number) =>
      ['sample-inventory', 'history', projectId, sampleId, after ?? null, limit ?? null] as const,
    /** 시험 실무자 축의 1:N 입고 이력 — `detail` 이 싣는 최신 1건과 다른 축이다. */
    intakes: (projectId: string, sampleId: string) =>
      ['sample-inventory', 'intakes', projectId, sampleId] as const,
    /** PM 축의 반입/반출 사건 (ADR-0002). */
    custody: (projectId: string, sampleId: string) =>
      ['sample-inventory', 'custody', projectId, sampleId] as const,
  },
  /**
   * 참조 카탈로그 (2026-08-08). 프로젝트가 아니라 **provider** 스코프다 —
   * 리비전의 버킷은 (provider, family, profile, scope) 이고 그 scope 는 케이블
   * 관련 패밀리에서 **방**이다. 프로젝트 키 아래 두면 한 프로젝트가 두 방에
   * 걸치는 실제 형상을 캐시가 표현하지 못한다.
   *
   * 목록 키에 facet 을 싣는 이유는 서버가 그것으로 **다른 집합**을 돌려주기
   * 때문이다. 싣지 않으면 방을 바꿔도 이전 방의 캐시가 그려진다.
   */
  reference: {
    all: ['reference-revisions'] as const,
    list: (
      providerId: string,
      facets: {
        family?: string;
        scopeKind?: string;
        scopeId?: string;
        state?: string;
      } = {},
    ) =>
      [
        'reference-revisions',
        providerId,
        facets.family ?? '',
        facets.scopeKind ?? '',
        facets.scopeId ?? '',
        facets.state ?? '',
      ] as const,
    detail: (providerId: string, revisionId: string) =>
      ['reference-revision', providerId, revisionId] as const,
    // 패밀리별 열 어휘. 순수 정책의 투영이라 리비전 무효화와 함께 움직일 필요가 없다.
    families: (providerId: string) => ['reference-families', providerId] as const,
  },
  /** Headless reporting — queue stats / request / outputs / session artifacts. */
  report: {
    stats: () => ['report-automation', 'stats'] as const,
    // Pre-generation preflight (report-preflight-precheck B3, 2026-06-24) — a
    // read-only dry-run of per-technology completeness + data-quality keyed by
    // the target measurement session.
    preflight: (sessionId: number | null, nodeBaseUrl?: string | null) =>
      nodeBaseUrl === undefined
        ? (['report-preflight', sessionId] as const)
        : (['report-preflight', sessionId, nodeBaseUrl] as const),
    request: (requestId: number | null, nodeBaseUrl?: string | null) =>
      nodeBaseUrl === undefined
        ? (['report-request', requestId] as const)
        : (['report-request', requestId, nodeBaseUrl] as const),
    outputs: (requestId: number | null) => ['report-outputs', requestId] as const,
    sessionArtifacts: (sessionId: number | null) => ['session-artifacts', sessionId] as const,
  },
  /** Headless session attempts (keyset-paginated). */
  sessionAttempts: {
    list: (sessionId: number | null) => ['session-attempts', sessionId] as const,
  },
  /**
   * Headless measurement jobs — backend queue. `status` is the aggregate count
   * snapshot (`GET /headless/status`); `list` is the full job table
   * (`GET /headless/jobs`). A stop mutation invalidates BOTH (a stopped job
   * shifts the counts and its row state) by calling these same factories.
   */
  jobs: {
    status: () => ['headless-jobs', 'status'] as const,
    list: () => ['headless-jobs', 'list'] as const,
  },
  /** Provider list + UI descriptor (read-only). */
  provider: {
    list: () => ['provider-list'] as const,
    uiDescriptor: (providerId: string) => ['provider-ui-descriptor', providerId] as const,
  },
  /**
   * 멀티챔버 P6 — central chamber availability + per-chamber measurement progress
   * (central proxy). `list` is the `chamber_availability` view; `progress` polls a
   * single chamber's run. A start mutation invalidates `list` (status flips to
   * in_use) by calling the same factory.
   */
  chambers: {
    all: ['chambers'] as const,
    list: () => ['chambers', 'list'] as const,
    progress: (chamberId: string | null) => ['chambers', 'progress', chamberId] as const,
    /**
     * A chamber's instrument connection settings (SPLIT-6 ②). Under the same
     * `chambers` prefix so one `invalidateQueries({ queryKey:
     * queryKeys.chambers.all })` clears it with the rest of the chamber state.
     */
    equipmentConfig: (chamberId: string | null) =>
      ['chambers', 'equipment-config', chamberId] as const,
    /**
     * Last applied WS event timestamp per chamber (fe-data-layer-robustness M2).
     * Not a server read — a monotonic watermark colocated with the snapshot it
     * guards, under the same `chambers` prefix so one `invalidateQueries({
     * queryKey: queryKeys.chambers.all })` clears snapshot AND watermark
     * together. Keeping it in the cache (rather than a module-level Map) means
     * it shares the QueryClient's lifetime: a fresh client in a test — or a
     * logout that drops the client — starts with no stale ordering state.
     */
    progressWatermark: (chamberId: string) =>
      ['chambers', 'progress-watermark', chamberId] as const,
  },
  /**
   * 시험 항목표 P6 — test-plan draft authoring (headless API). `drafts` is the
   * project-scoped (optionally status-filtered) summary list; `draft` is one
   * draft's detail. A publish mutation invalidates `drafts` (status → published)
   * and `draft` (the published view) by calling the same factories.
   */
  testPlans: {
    drafts: (projectId: string, status?: string) =>
      ['test-plan-drafts', projectId, status] as const,
    draft: (projectId: string, draftId: string | null) =>
      ['test-plan-draft', projectId, draftId] as const,
    /**
     * Authoritative published-plan list for a project (G2 server SSOT). This is
     * the source the chamber measurement starter reads to suggest plan ids —
     * it replaces the removed browser-local registry, so plans published on any
     * browser/session are visible. A publish mutation invalidates this factory
     * key so a freshly published plan appears without a manual reload.
     */
    publications: (projectId: string) => ['test-plan-publications', projectId] as const,
    /** Current provider-neutral catalogue, derived from policy/provider SSOT. */
    generationCatalogue: () => ['test-plan-generation-catalogue'] as const,
    generationJob: (projectId: string, jobId: string | null) =>
      ['test-plan-generation-job', projectId, jobId] as const,
    generationMetadata: (projectId: string, draftId: string | null) =>
      ['test-plan-generation-metadata', projectId, draftId] as const,
    generationRowsPrefix: (projectId: string, draftId: string) =>
      ['test-plan-generation-rows', projectId, draftId] as const,
    generationRows: (projectId: string, draftId: string, after: number | null) =>
      ['test-plan-generation-rows', projectId, draftId, after] as const,
  },
} as const;
