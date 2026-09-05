import createClient from 'openapi-fetch';

import { getRuntimeConfig, type RuntimeConfig } from '@/config/runtime';
import { filenameFromContentDisposition } from '@/shared/content-disposition';

import { authRetryMiddleware } from './auth-middleware';
import { apiErrorFromResponse } from './to-api-error';

import type { components, paths as PlatformPaths } from './generated/platform-api.types';
import type { PathsWithMethod } from 'openapi-typescript-helpers';

/**
 * Platform read API client — backend `platform-api.openapi.json`
 * (FE-P0d SSOT, 2026-05-27).
 *
 * The platform surface reads the **central** PostgreSQL read model — project
 * coverage (`coverage_by_condition_hash`) + active claims (`active_claims`) —
 * the cross-engineer source of truth that unblocks FE-P2 (coverage dashboard)
 * and FE-P3 (claim/lock UX). It is distinct from `headless-client.ts`, which
 * serves a single engineer's local-SQLite session data.
 *
 * Types come from `npm run codegen` (ADR-0003 — openapi-typescript). The
 * generated file is gitignored; CI runs `npm run codegen:check` so a backend
 * OpenAPI change without a frontend type bump fails.
 *
 * AuthZ: the `platform:read` permission gates both routes. The shared
 * `authRetryMiddleware` (`./auth-middleware`, identical to `headless-client.ts`)
 * forwards the `Authorization` header and performs a single silent-refresh +
 * replay on a 401 (Increment 6); the backend responds 403 when the permission is
 * missing and view code handles the failure.
 *
 * Base URL: `platformApiBaseUrl` when the deployment splits the platform read
 * surface (a separate ASGI app, `platform_api_app:create_app`) off the gateway;
 * otherwise it falls back to `apiBaseUrl` (current single-gateway, path-routed
 * `/platform/*` topology). The runtime config field is nullable — `null` means
 * reuse `apiBaseUrl`.
 *
 * Routes (contract → OpenAPI → TS chain):
 * - GET `/platform/projects/{project_id}/coverage` — project coverage
 *   (operationId `get_project_coverage`).
 * - GET `/platform/projects/{project_id}/claims` — active claims
 *   (operationId `list_project_claims`).
 */

/**
 * Resolve the platform API base URL: the dedicated `platformApiBaseUrl` when the
 * deployment splits the platform read surface off the gateway, else the shared
 * `apiBaseUrl` (`null` → reuse). Pure + exported so the fallback is unit-tested
 * without depending on module-load timing of the client.
 */
export function resolvePlatformBaseUrl(
  config: Pick<RuntimeConfig, 'apiBaseUrl' | 'platformApiBaseUrl'>,
): string {
  return config.platformApiBaseUrl ?? config.apiBaseUrl;
}

const baseUrl = (): string => resolvePlatformBaseUrl(getRuntimeConfig());

export const platformClient = createClient<PlatformPaths>({
  baseUrl: baseUrl(),
  headers: {
    Accept: 'application/json',
  },
});
platformClient.use(authRetryMiddleware);

/**
 * Re-export of the generated types so callers do not import the autogen
 * path directly (decouples view code from the generator filename).
 */
export type PlatformApiPaths = PlatformPaths;

// ── Keyset pagination (FE-P2 adoption, 2026-05-27) ──────────────────────────
//
// FE-P0d shipped opt-in keyset pagination on the backend + typed client; this
// section adopts it so the coverage dashboard no longer issues a single
// unbounded fetch over a project's 16k+ conditions. The page helpers below are
// the only place that knows the wire shape (typed `limit`/`cursor` query + the
// next-page cursor response header), so view code stays declarative.

/**
 * Response header carrying the opaque keyset continuation token. Mirror of the
 * backend SSOT `PLATFORM_NEXT_CURSOR_HEADER` in
 * `src/application/central_contract/api_contracts.py` (cross-language constant — it
 * cannot be imported from Python). The body stays a plain array; the next-page
 * cursor rides in this header (GitHub-style).
 */
export const PLATFORM_NEXT_CURSOR_HEADER = 'X-Next-Cursor';

/**
 * Page size requested via the typed `limit` query param. Stays at/under the
 * backend `MAX_PAGE_SIZE` (1000); 200 balances round trips against payload size
 * for the coverage matrix. A named constant, not an inline magic number.
 */
export const PLATFORM_PAGE_SIZE = 200;

export type CoverageEnvelope = components['schemas']['CoverageEnvelope'];
export type ActiveClaimEnvelope = components['schemas']['ActiveClaimEnvelope'];
export type ClaimEventEnvelope = components['schemas']['ClaimEventEnvelope'];
export type AcquireClaimRequest = components['schemas']['AcquireClaimRequest'];
export type ReleaseClaimRequest = components['schemas']['ReleaseClaimRequest'];
export type SyncStatusEnvelope = components['schemas']['SyncStatusEnvelope'];
// FE-P8 membership types (generated from rbac_role_grants + audit_events SSOT).
export type MembershipEnvelope = components['schemas']['MembershipEnvelope'];
export type AssignMembershipRequest = components['schemas']['AssignMembershipRequest'];
export type RevokeMembershipRequest = components['schemas']['RevokeMembershipRequest'];

/** One keyset page: the loaded items plus the cursor for the next page
 *  (`null` on the last page or an unbounded read). */
export interface PlatformPage<T> {
  readonly items: readonly T[];
  readonly nextCursor: string | null;
}

/** Read the next-page cursor from a response, tolerating a header-less mock. */
function nextCursorFromResponse(response: Response | undefined): string | null {
  const raw = response?.headers?.get(PLATFORM_NEXT_CURSOR_HEADER);
  return raw !== null && raw !== undefined && raw !== '' ? raw : null;
}

/**
 * Build the keyset query. Optional keys are left *out* (not set to `undefined`)
 * so the typed query satisfies the generated optional-property contract under
 * `exactOptionalPropertyTypes`:
 *  - `cursor` is omitted on the first page.
 *  - `technology` is omitted when absent or blank — i.e. "no facet" reads the
 *    full project (backward compatible with the unfiltered dashboard). The
 *    backend `_facet()` normalizes the value server-side, so it is forwarded
 *    verbatim (no client-side technology normalization / hardcoded tokens).
 */
function pageQuery(
  cursor?: string,
  technology?: string,
): { limit: number; cursor?: string; technology?: string } {
  const query: { limit: number; cursor?: string; technology?: string } = {
    limit: PLATFORM_PAGE_SIZE,
  };
  if (cursor !== undefined) query.cursor = cursor;
  if (technology !== undefined && technology !== '') query.technology = technology;
  return query;
}

/**
 * Fetch one keyset page of project coverage. `cursor === undefined` requests the
 * first page; pass the previous page's `nextCursor` to continue. `technology`
 * narrows the read to one technology facet server-side (Phase B) so the dedup
 * dashboard reaches a technology's conditions without paging the full 16k+ set;
 * omit it (or pass `''`) to read the whole project. Throws a `ApiError`
 * (carrying the HTTP status) on a non-2xx response so the caller's React Query
 * error branch can map 400/403/503.
 */
export async function fetchCoveragePage(
  projectId: string,
  cursor?: string,
  technology?: string,
): Promise<PlatformPage<CoverageEnvelope>> {
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/coverage',
    { params: { path: { project_id: projectId }, query: pageQuery(cursor, technology) } },
  );
  if (error) {
    throw apiErrorFromResponse('coverage lookup failed', { error, response });
  }
  return { items: data ?? [], nextCursor: nextCursorFromResponse(response) };
}

/**
 * Fetch one keyset page of active claims (FE-P3 lock/warning overlay). Same
 * keyset + `technology` facet contract as {@link fetchCoveragePage}; the
 * dashboard applies the same facet to claims so the lock overlay stays scoped to
 * (and complete for) the filtered technology. It auto-advances claim pages
 * because the active-claim set is bounded, unlike the 16k+ coverage conditions.
 */
export async function fetchClaimsPage(
  projectId: string,
  cursor?: string,
  technology?: string,
): Promise<PlatformPage<ActiveClaimEnvelope>> {
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/claims',
    { params: { path: { project_id: projectId }, query: pageQuery(cursor, technology) } },
  );
  if (error) {
    throw apiErrorFromResponse('claims lookup failed', { error, response });
  }
  return { items: data ?? [], nextCursor: nextCursorFromResponse(response) };
}

/**
 * Fetch central-data freshness for a project (FE-SYNC). Reports the newest
 * central measurement timestamp + age + is_stale + condition/active-claim counts
 * — how fresh the CENTRAL coverage is (a station's local outbox is not visible
 * from the central surface). Throws a `ApiError` on a non-2xx response.
 */
export async function fetchSyncStatus(projectId: string): Promise<SyncStatusEnvelope> {
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/sync-status',
    { params: { path: { project_id: projectId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('sync-status lookup failed', { error, response });
  }
  return data;
}

// ── Phase 6: 시간가중 진행률 (per workbench area × progress bucket) ───────────
//
// `GET /platform/projects/{id}/progress` (platform:read). 한 프로젝트의 시간가중
// 진행률 rollup — bucket 별 planned/completed 분 + percent(priced 시간 없으면 null
// = 가짜 0% 금지) + unpriced/unbucketable condition 수. 작은 bounded 배열(분야×버킷
// 단위)이라 keyset pagination 없음. wire shape 는 codegen 타입에서 파생(하드코딩 0).

export type ProgressBucketEnvelope = components['schemas']['ProgressBucketEnvelope'];
export type ProjectProgressList = components['schemas']['ProjectProgressList'];
export type ProjectReportSessionEnvelope = components['schemas']['ProjectReportSessionEnvelope'];
export type ProjectReportSessionList = components['schemas']['ProjectReportSessionList'];

export type ProjectArtifactCustody = components['schemas']['ProjectArtifactCustody'];
export type ArtifactCustodySessionSummary = components['schemas']['ArtifactCustodySessionSummary'];
export type ArtifactCustodySnapshotDetail = components['schemas']['ArtifactCustodySnapshotDetail'];

/**
 * 플롯 원본 보관 현황 — 프로젝트 축 요약 + 세션 행 (plot-custody ①).
 *
 * 판정은 **노드**가 내리고 중앙은 받아서 보관한다(중앙은 회사 파일서버도 챔버 PC
 * 로컬도 열 수 없다). 따라서 이 응답의 상태 토큰·집계·차단 여부·신선도는 전부 서버가
 * 정한 값이고 프론트는 **읽기만** 한다 — 재계산하면 규칙이 두 언어로 쪼개져 화면과
 * 발행 게이트가 다른 답을 낸다.
 */
export async function fetchProjectArtifactCustody(
  projectId: string,
): Promise<ProjectArtifactCustody> {
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/artifact-custody',
    { params: { path: { project_id: projectId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('artifact custody lookup failed', { error, response });
  }
  return data;
}

/** 한 세션에서 **무엇을** 옮겨야 하는지 (비-verified 항목만 — verified 는 개수로만 온다). */
export async function fetchArtifactCustodySnapshot(
  projectId: string,
  snapshotId: string,
): Promise<ArtifactCustodySnapshotDetail> {
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/artifact-custody/{snapshot_id}',
    { params: { path: { project_id: projectId, snapshot_id: snapshotId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('custody snapshot lookup failed', { error, response });
  }
  return data;
}

export async function fetchProjectProgress(projectId: string): Promise<ProjectProgressList> {
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/progress',
    { params: { path: { project_id: projectId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('progress lookup failed', { error, response });
  }
  return data;
}

export async function fetchProjectReportSessions(
  projectId: string,
): Promise<ProjectReportSessionList> {
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/report-sessions',
    { params: { path: { project_id: projectId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('project report sessions lookup failed', { error, response });
  }
  return data;
}

// ── Cross-session result selection (M1-M5) ─────────────────────────────────

export type ResultSelectionEnvelope = components['schemas']['ResultSelectionEnvelope'];
export type MeasurementAttemptEnvelope = components['schemas']['MeasurementAttemptEnvelope'];
export type SelectionEventRequest = components['schemas']['SelectionEventRequest'];
export type SelectionEventEnvelope = components['schemas']['SelectionEventEnvelope'];
export type ProjectResultReferenceEnvelope =
  components['schemas']['ProjectResultReferenceEnvelope'];
export type CreateProjectResultReferenceRequest =
  components['schemas']['CreateProjectResultReferenceRequest'];
export type RetireProjectResultReferenceRequest =
  components['schemas']['RetireProjectResultReferenceRequest'];

export async function fetchResultSelections(
  projectId: string,
  providerId: string,
  cursor?: string,
): Promise<PlatformPage<ResultSelectionEnvelope>> {
  const query: { limit: number; cursor?: string } = { limit: PLATFORM_PAGE_SIZE };
  if (cursor !== undefined) query.cursor = cursor;
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/providers/{provider_id}/result-selections',
    { params: { path: { project_id: projectId, provider_id: providerId }, query } },
  );
  if (error) throw apiErrorFromResponse('result selections lookup failed', { error, response });
  return { items: data ?? [], nextCursor: nextCursorFromResponse(response) };
}

export async function fetchResultAttempts(
  projectId: string,
  providerId: string,
  conditionHash: string,
  cursor?: string,
): Promise<PlatformPage<MeasurementAttemptEnvelope>> {
  const query: { limit: number; cursor?: string } = { limit: PLATFORM_PAGE_SIZE };
  if (cursor !== undefined) query.cursor = cursor;
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/providers/{provider_id}/conditions/{condition_hash}/attempts',
    {
      params: {
        path: {
          project_id: projectId,
          provider_id: providerId,
          condition_hash: conditionHash,
        },
        query,
      },
    },
  );
  if (error) throw apiErrorFromResponse('result attempts lookup failed', { error, response });
  return { items: data ?? [], nextCursor: nextCursorFromResponse(response) };
}

export async function selectResult(
  projectId: string,
  providerId: string,
  conditionHash: string,
  body: SelectionEventRequest,
): Promise<SelectionEventEnvelope> {
  const { data, error, response } = await platformClient.POST(
    '/platform/projects/{project_id}/providers/{provider_id}/conditions/{condition_hash}/selection-events',
    {
      params: {
        path: { project_id: projectId, provider_id: providerId, condition_hash: conditionHash },
      },
      body,
    },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('result selection failed', { error, response });
  }
  return data;
}

export async function clearResultSelection(
  projectId: string,
  providerId: string,
  conditionHash: string,
  body: SelectionEventRequest,
): Promise<SelectionEventEnvelope> {
  const { data, error, response } = await platformClient.DELETE(
    '/platform/projects/{project_id}/providers/{provider_id}/conditions/{condition_hash}/selection-events',
    {
      params: {
        path: { project_id: projectId, provider_id: providerId, condition_hash: conditionHash },
      },
      body,
    },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('result selection clear failed', { error, response });
  }
  return data;
}

export async function fetchProjectResultReferences(
  projectId: string,
  providerId?: string,
  state?: 'published' | 'retired',
  cursor?: string,
): Promise<PlatformPage<ProjectResultReferenceEnvelope>> {
  const query: {
    provider_id?: string;
    state?: 'published' | 'retired';
    limit: number;
    cursor?: string;
  } = { limit: PLATFORM_PAGE_SIZE };
  if (providerId) query.provider_id = providerId;
  if (state) query.state = state;
  if (cursor !== undefined) query.cursor = cursor;
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/project-result-references',
    { params: { path: { project_id: projectId }, query } },
  );
  if (error)
    throw apiErrorFromResponse('project result references lookup failed', { error, response });
  return { items: data ?? [], nextCursor: nextCursorFromResponse(response) };
}

export async function createProjectResultReference(
  projectId: string,
  body: CreateProjectResultReferenceRequest,
): Promise<ProjectResultReferenceEnvelope> {
  const { data, error, response } = await platformClient.POST(
    '/platform/projects/{project_id}/project-result-references',
    { params: { path: { project_id: projectId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('project result reference create failed', { error, response });
  }
  return data;
}

export async function retireProjectResultReference(
  projectId: string,
  revisionId: string,
  body: RetireProjectResultReferenceRequest,
): Promise<ProjectResultReferenceEnvelope> {
  const { data, error, response } = await platformClient.POST(
    '/platform/projects/{project_id}/project-result-references/{revision_id}/retire',
    { params: { path: { project_id: projectId, revision_id: revisionId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('project result reference retirement failed', { error, response });
  }
  return data;
}

// ── Phase 1: 프로젝트 진입층 (list / detail / create) ───────────────────────
//
// "내 프로젝트" 진입 화면의 데이터 접근. list/detail 은 platform:read(멤버십 scope),
// create 는 ADR-0017 D3 의 authenticated 인가-클래스로 게이트(생성자 자동 admin).
// 동명 모델은 멱등 재사용(409 아님). 와이어 shape 는 생성 타입에서 파생(하드코딩 0).

export type ProjectEnvelope = components['schemas']['ProjectEnvelope'];
export type ProjectList = components['schemas']['ProjectList'];
export type ProjectDetailEnvelope = components['schemas']['ProjectDetailEnvelope'];
export type CreateProjectRequest = components['schemas']['CreateProjectRequest'];
/** Partial 표지 메타 update body (W3-B M1) — every field optional and nullable:
 *  an omitted key leaves the column unchanged, an explicit `null` clears it. */
export type UpdateProjectRequest = components['schemas']['UpdateProjectRequest'];
/** 신청자 제안 한 건 — 생성 폼 자동 채움의 단위. 프로젝트 행에서 파생된 값이라
 *  식별자가 없다(신청자 마스터 테이블은 존재하지 않는다). */
export type ApplicantSuggestionEnvelope = components['schemas']['ApplicantSuggestionEnvelope'];
export type ApplicantSuggestionList = components['schemas']['ApplicantSuggestionList'];

/** Project directory status filter (project-status-visibility) — derived from the
 *  generated OpenAPI query enum (SSOT, no hand-maintained union drift). Omitted ⇒
 *  the backend defaults to 'active' (in-progress projects). */
export type ProjectStatusFilter = NonNullable<
  NonNullable<PlatformPaths['/platform/projects']['get']['parameters']['query']>['status']
>;

/**
 * Fetch the project directory (read-open — any authenticated principal, NOT
 * membership-scoped) filtered by `status` (defaults to active in-progress
 * projects when omitted). Throws a `ApiError` on a non-2xx response. A plain
 * array — an **unbounded** read of every project with that status.
 *
 * W3-B: prefer {@link fetchProjectsPage} for anything a user browses or searches.
 * This helper sends no `q`/`limit`/`cursor`, which is exactly the condition under
 * which the backend keeps the pre-W3 unbounded contract — so it stays as the
 * "I really do want all of them" read (and as the byte-identical baseline the
 * backward-compatibility guarantee is stated against).
 */
export async function fetchProjects(status?: ProjectStatusFilter): Promise<ProjectList> {
  const query: { status?: ProjectStatusFilter } = {};
  if (status !== undefined) query.status = status;
  const { data, error, response } = await platformClient.GET('/platform/projects', {
    params: { query },
  });
  if (error || data === undefined) {
    throw apiErrorFromResponse('projects lookup failed', { error, response });
  }
  return data;
}

/**
 * Build the project-directory keyset query (W3-B M1). Same "omit optional keys"
 * regime as {@link pageQuery} — a key set to `undefined` would violate the
 * generated optional-property contract under `exactOptionalPropertyTypes`, and
 * more importantly a literal `?q=` is a *different request* from no `q` at all:
 *
 *  - `limit` is always sent. This is the switch that opts the request into the
 *    paged contract; without it (and without a cursor) the backend performs the
 *    legacy unbounded read.
 *  - `status` is omitted when absent (backend defaults to `active`).
 *  - `q` is omitted when absent or empty — a cleared search box means "no
 *    filter", never a match against the empty string. The term is forwarded
 *    verbatim (the backend `normalize_search_term` owns normalization, so no
 *    client-side casing/trimming rules are duplicated here).
 *  - `cursor` is omitted on the first page.
 */
function projectDirectoryQuery(
  status?: ProjectStatusFilter,
  q?: string,
  cursor?: string,
): { limit: number; status?: ProjectStatusFilter; q?: string; cursor?: string } {
  const query: {
    limit: number;
    status?: ProjectStatusFilter;
    q?: string;
    cursor?: string;
  } = { limit: PLATFORM_PAGE_SIZE };
  if (status !== undefined) query.status = status;
  if (q !== undefined && q !== '') query.q = q;
  if (cursor !== undefined) query.cursor = cursor;
  return query;
}

/**
 * Fetch one keyset page of the project directory, optionally narrowed by a
 * server-side search term (W3-B M1 — backend PR #63).
 *
 * Same keyset contract as {@link fetchCoveragePage} / {@link fetchMembershipsPage}:
 * `cursor === undefined` requests the first page, pass the previous page's
 * `nextCursor` to continue, and the cursor rides in the
 * `PLATFORM_NEXT_CURSOR_HEADER` response header while the body stays a plain
 * array. Arguments are positional (not an options object) to match those
 * siblings and because `exactOptionalPropertyTypes` makes an omitted *positional*
 * argument the ergonomic way to say "absent" — notably for the
 * `useKeysetPagination` closure `(cursor) => fetchProjectsPage(status, term, cursor)`.
 *
 * `q` matches server-side across the directory's searchable identity columns, so
 * "no results" means no such project exists **within the requested `status`
 * scope**, not "none among the rows this client happened to load" — that
 * distinction is the point of the helper. The scope qualifier is not padding:
 * `status` narrows the very same read, so a verdict stated over "the whole
 * directory" would overclaim by exactly the width of that filter.
 *
 * Throws a `ApiError` on a non-2xx response (400 on a malformed cursor).
 */
export async function fetchProjectsPage(
  status?: ProjectStatusFilter,
  q?: string,
  cursor?: string,
): Promise<PlatformPage<ProjectEnvelope>> {
  const { data, error, response } = await platformClient.GET('/platform/projects', {
    params: { query: projectDirectoryQuery(status, q, cursor) },
  });
  if (error) {
    throw apiErrorFromResponse('projects lookup failed', { error, response });
  }
  return { items: data ?? [], nextCursor: nextCursorFromResponse(response) };
}

/**
 * Mark a project completed (active → completed) — idempotent. `platform:admin`
 * gated (membership-admin allowed). Throws a `ApiError` on a non-2xx response —
 * 404 when the project is unknown, 403 when not an admin.
 */
export async function completeProject(projectId: string): Promise<ProjectDetailEnvelope> {
  const { data, error, response } = await platformClient.POST(
    '/platform/projects/{project_id}/complete',
    { params: { path: { project_id: projectId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('project complete failed', { error, response });
  }
  return data;
}

/**
 * Reopen a project (→ active) — the idempotent reverse of {@link completeProject},
 * same `platform:admin` gate. Throws a `ApiError` on a non-2xx response.
 */
export async function reopenProject(projectId: string): Promise<ProjectDetailEnvelope> {
  const { data, error, response } = await platformClient.POST(
    '/platform/projects/{project_id}/reopen',
    { params: { path: { project_id: projectId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('project reopen failed', { error, response });
  }
  return data;
}

/**
 * Fetch one project's detail (model + samples). Throws a `ApiError` on a
 * non-2xx response — notably 404 when the project is unknown.
 */
export async function fetchProjectDetail(projectId: string): Promise<ProjectDetailEnvelope> {
  const { data, error, response } = await platformClient.GET('/platform/projects/{project_id}', {
    params: { path: { project_id: projectId } },
  });
  if (error || data === undefined) {
    throw apiErrorFromResponse('project detail lookup failed', { error, response });
  }
  return data;
}

/**
 * Fetch applicant suggestions for the create form's auto-fill (2026-09-04).
 *
 * **Not a master record.** The backend derives one entry per distinct applicant
 * from the project rows, carrying that applicant's MOST RECENT address /
 * manufacturer. Selecting one pre-fills those fields, where they stay editable —
 * a suggestion is a starting point, not a constraint.
 *
 * `management_number` is deliberately absent from a suggestion: it is UNIQUE per
 * project, so inheriting it would be an immediate 409. That exclusion is derived
 * on the backend from the uniqueness constraint, not from a hand-picked list.
 *
 * There is no cursor — an autocomplete's top N is the whole answer. `limit`
 * bounds the read; the backend clamps it to the pagination SSOT.
 */
export async function fetchApplicantSuggestions(
  term: string | undefined,
  limit: number,
): Promise<ApplicantSuggestionList> {
  // An empty box means "no filter", not "match the empty string" — omit the key
  // entirely (the same regime as the project directory's `q`).
  const query: { q?: string; limit: number } = { limit };
  const trimmed = term?.trim();
  if (trimmed !== undefined && trimmed !== '') query.q = trimmed;
  const { data, error, response } = await platformClient.GET('/platform/applicants', {
    params: { query },
  });
  if (error || data === undefined) {
    throw apiErrorFromResponse('applicant directory lookup failed', { error, response });
  }
  return data;
}

/**
 * Create a project = model (ADR-0017 D1). The backend inserts one projects row +
 * one device_models row (`project_code == model name`); a request whose model
 * name matches an existing project reuses it (idempotent — never a duplicate, so
 * no 409 on a same-name retry). The creator is auto-granted project_admin (D3).
 * Throws a `ApiError` on a non-2xx response — 400 when model_name is blank, 403
 * when not authenticated.
 */
export async function createProject(body: CreateProjectRequest): Promise<ProjectDetailEnvelope> {
  const { data, error, response } = await platformClient.POST('/platform/projects', { body });
  if (error || data === undefined) {
    throw apiErrorFromResponse('project creation failed', { error, response });
  }
  return data;
}

/**
 * Partially update a project's 성적서 표지 메타 (W3-B M1 — backend PR #63).
 *
 * **The caller owns the partial-update semantics.** The backend applies exactly
 * the keys present in `body`: an omitted key leaves that column untouched, and an
 * explicit `null` clears it. It does NOT protect against lost updates
 * (`central_project_service.update_project_metadata` says so outright), so a form
 * that helpfully re-sends every current value would silently overwrite whatever a
 * concurrent editor changed in the fields the user never touched. Send a diff
 * against the loaded snapshot — nothing else.
 *
 * A body with **no** editable key is a loud 400, not a no-op: the backend
 * requires at least one field. Callers must therefore skip the request entirely
 * when nothing is dirty rather than "saving" an empty diff.
 *
 * `model_name` / `project_code` (project identity — changing them is a re-key,
 * ADR-0005) and `status` (the complete/reopen sub-resources) are rejected with
 * 400; the generated `UpdateProjectRequest` does not offer them.
 *
 * AuthZ is `platform:admin` **∪ project-membership admin**, so a caller without
 * the token permission may still succeed — never pre-disable on the token alone.
 *
 * Throws a `ApiError` on a non-2xx response — 400 (empty/invalid body), 404
 * (unknown project), 503 (central unavailable), and 409
 * `PROJECT_IDENTIFIER_CONFLICT` whose `params.field` names the colliding
 * identifier so the form can attribute the error to that input.
 */
export async function updateProject(
  projectId: string,
  body: UpdateProjectRequest,
): Promise<ProjectDetailEnvelope> {
  const { data, error, response } = await platformClient.PATCH('/platform/projects/{project_id}', {
    params: { path: { project_id: projectId } },
    body,
  });
  if (error || data === undefined) {
    throw apiErrorFromResponse('project metadata update failed', { error, response });
  }
  return data;
}

// ── Phase G: 성적서(test_reports) 인스턴스 대장 + 자동 인용 ──────────────────
//
// 중앙 `test_reports` 는 프로젝트(=모델) 1:N 의 성적서 인스턴스다. `/headless/
// reports/*`(리포트 생성 요청 큐, `headless-client.ts`)와는 다른 도메인 — 이쪽은
// platform 권한(read/admin)으로 게이트되는 중앙 대장이다.
//
// **`report_number` 는 서버 파생값이다.** 백엔드 도메인 SSOT
// (`src/domain/services/report_number_policy.py`)가 관리번호 + edition 에서
// 유도하며, `fcc_id` 선례대로 DB 에 저장조차 하지 않는다. 프론트는 응답 필드를
// 읽기만 한다 — 여기서 재조립하면 같은 규칙이 Python/TS 두 곳으로 쪼개져 조용히
// 드리프트한다(계약 M4, `tests/test_frontend_architecture_conformance.py::
// TestReportNumberIsServerDerived` 가 봉인).
//
// 목록은 keyset 페이지네이션이 없다: 백엔드가 `ReportList = ReportEnvelope[]` 를
// 그대로 반환하고 limit/cursor 쿼리를 노출하지 않는다(edition 단위의 작은 집합).

export type ReportEnvelope = components['schemas']['ReportEnvelope'];
export type ReportList = components['schemas']['ReportList'];
export type CreateReportRequest = components['schemas']['CreateReportRequest'];
export type ReportCitationEnvelope = components['schemas']['ReportCitationEnvelope'];
export type ReportSampleCitation = components['schemas']['ReportSampleCitation'];
export type ReportFirmwareCitation = components['schemas']['FirmwareCitationEnvelope'];

/**
 * List a project's test reports (성적서 인스턴스), newest first. `platform:read`
 * gated. Throws a `ApiError` on a non-2xx response — 404 when the project is
 * unknown, 503 when the central backend is unreachable.
 */
export async function fetchProjectReports(projectId: string): Promise<ReportList> {
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/reports',
    { params: { path: { project_id: projectId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('project reports lookup failed', { error, response });
  }
  return data;
}

/**
 * Create a test report at an edition. `platform:admin` gated — but the backend
 * authorizes the UNION of the token permission and the caller's project
 * membership, so view code must NOT hard-disable the affordance on a token-only
 * miss (the server still applies its project-membership union at the boundary).
 *
 * A duplicate `(project_id, edition)` answers **409**, which the backend maps to
 * the generic `ErrorCode.CONFLICT` (`_PLATFORM_ERROR_CODE_TABLE`) — there is no
 * report-specific code to branch on; callers specialise the copy through
 * `describeApiError(..., { conflict })`.
 */
export async function createProjectReport(
  projectId: string,
  body: CreateReportRequest,
): Promise<ReportEnvelope> {
  const { data, error, response } = await platformClient.POST(
    '/platform/projects/{project_id}/reports',
    { params: { path: { project_id: projectId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('report creation failed', { error, response });
  }
  return data;
}

/**
 * Assemble the report header citation for a project — SN + latest firmware per
 * sample plus 관리번호 / FCC ID / applicant / EUT / standard, all derived
 * server-side. `platform:read` gated.
 *
 * The route is PROJECT-scoped (there is no `report_id` parameter): the optional
 * `edition` only feeds the derived `report_number`. It is omitted from the query
 * when blank rather than sent as `''` — an empty edition is not an edition, and
 * `exactOptionalPropertyTypes` makes the distinction a type-level one.
 */
export async function fetchReportCitation(
  projectId: string,
  edition?: string,
  sessionId?: string,
): Promise<ReportCitationEnvelope> {
  const query: { edition?: string; session_id?: string } = {};
  if (edition !== undefined && edition !== '') query.edition = edition;
  if (sessionId !== undefined && sessionId !== '') query.session_id = sessionId;
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/report-citation',
    { params: { path: { project_id: projectId }, query } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('report citation lookup failed', { error, response });
  }
  return data;
}

// ── 성적서 §6 장비목록 (2026-08-07) ──────────────────────────────────────────
//
// 성적서의 `6. TEST AND MEASUREMENT EQUIPMENT` 절에 들어가는, 프로젝트가 **실제로
// 사용한** 장비/시험용 소프트웨어 목록이다. EMS(사내 장비관리시스템)가 팀 × 시험항목
// × 챔버별 **표준** 리스트의 SSOT 이고, 이 표면은 시험원이 성적서 작성 시점에 고른
// 실사용본을 기록·확정한다.
//
// **두 표의 열 순서는 서버가 내려준다.** 응답의 `tables[]` 가
// `[{item_type, columns}]` 형태로 열 순서를 싣는다(백엔드 도메인 SSOT
// `test_equipment_list_policy.py`). 여기서 열 이름 배열을 다시 선언하면 같은 순서가
// TS/Python 두 곳으로 쪼개져 조용히 드리프트하고, 그 드리프트는 제출된 성적서에서만
// 드러난다 — 화면은 `tables` 를 **읽기만** 한다.
//
// **`sort_order` 는 보내지 않는다.** 배열 위치가 곧 순서이고 서버가 부여한다.
// 요청 스키마에 그 필드가 아예 없다(`TestEquipmentListItemInput`).
//
// 쓰기는 `platform:claim`(engineer 티어) 게이트지만, 백엔드 `authorize` 는 토큰 ∪
// 프로젝트 멤버십 UNION 이므로 토큰 미보유를 근거로 화면에서 잠그면 안 된다
// (`createProjectReport` 의 주의와 같다).

export type TestEquipmentListSummary = components['schemas']['TestEquipmentListSummary'];
export type TestEquipmentListCollection = components['schemas']['TestEquipmentListCollection'];
export type TestEquipmentListEnvelope = components['schemas']['TestEquipmentListEnvelope'];
export type TestEquipmentListItem = components['schemas']['TestEquipmentListItem'];
export type TestEquipmentListItemInput = components['schemas']['TestEquipmentListItemInput'];
export type TestEquipmentTableSpec = components['schemas']['TestEquipmentTableSpec'];
export type CreateTestEquipmentListRequest =
  components['schemas']['CreateTestEquipmentListRequest'];
/**
 * The test-item axis — one FCC report each (DTS/BLE/BT/UNII). **Derived from the
 * generated request schema**, never re-listed here: the vocabulary is owned by
 * the backend domain policy and reaches TS only through the OpenAPI artifact.
 */
export type TestItemKey = CreateTestEquipmentListRequest['test_item_key'];
export type ReplaceTestEquipmentListItemsRequest =
  components['schemas']['ReplaceTestEquipmentListItemsRequest'];
export type ReplaceTestEquipmentListItemsResult =
  components['schemas']['ReplaceTestEquipmentListItemsResult'];
export type ConfirmTestEquipmentListResult =
  components['schemas']['ConfirmTestEquipmentListResult'];

/**
 * List a project's §6 equipment lists, newest first, each with its item count,
 * **together with the test-item vocabulary** the create form may choose from
 * (`test_items` — DTS/BLE/BT/UNII, one FCC report each). The vocabulary rides on
 * the response for the same reason the detail envelope carries `tables`: the
 * generated TS types are type-level unions and give no runtime array, so a
 * client-side list would split the vocabulary across TS and Python.
 *
 * `platform:read` gated. 404 when the project is unknown, 503 when the central
 * backend is unreachable.
 */
export async function fetchProjectEquipmentLists(
  projectId: string,
): Promise<TestEquipmentListCollection> {
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/equipment-lists',
    { params: { path: { project_id: projectId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('equipment lists lookup failed', { error, response });
  }
  return data;
}

/**
 * Read one equipment list with its items **and the two tables' column order**.
 * A list belonging to another project answers 404 (not 403) — the backend does
 * not leak the fact that the id exists.
 */
export async function fetchEquipmentList(
  projectId: string,
  equipmentListId: string,
): Promise<TestEquipmentListEnvelope> {
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/equipment-lists/{equipment_list_id}',
    {
      params: { path: { project_id: projectId, equipment_list_id: equipmentListId } },
    },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('equipment list lookup failed', { error, response });
  }
  return data;
}

/**
 * Create an equipment list for a test item. The server owns `status` (draft), so
 * the body has no status field. A duplicate natural key answers 409.
 *
 * `platform:claim` gated — see the UNION note above; do not hard-disable on a
 * token-only miss.
 */
export async function createProjectEquipmentList(
  projectId: string,
  body: CreateTestEquipmentListRequest,
): Promise<TestEquipmentListSummary> {
  const { data, error, response } = await platformClient.POST(
    '/platform/projects/{project_id}/equipment-lists',
    { params: { path: { project_id: projectId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('equipment list creation failed', { error, response });
  }
  return data;
}

/**
 * Replace every item of a draft list in one PUT. There is no row-level endpoint:
 * `sort_order` is the array position, so per-row writes would need a second
 * reorder contract. A confirmed list answers 409.
 */
export async function replaceEquipmentListItems(
  projectId: string,
  equipmentListId: string,
  body: ReplaceTestEquipmentListItemsRequest,
): Promise<ReplaceTestEquipmentListItemsResult> {
  const { data, error, response } = await platformClient.PUT(
    '/platform/projects/{project_id}/equipment-lists/{equipment_list_id}/items',
    {
      params: { path: { project_id: projectId, equipment_list_id: equipmentListId } },
      body,
    },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('equipment list item write failed', { error, response });
  }
  return data;
}

/**
 * Confirm (freeze) a list — the snapshot the report is rendered from. Already
 * confirmed or empty answers 409; an empty §6 table is refused at report
 * generation anyway.
 */
export async function confirmEquipmentList(
  projectId: string,
  equipmentListId: string,
): Promise<ConfirmTestEquipmentListResult> {
  const { data, error, response } = await platformClient.POST(
    '/platform/projects/{project_id}/equipment-lists/{equipment_list_id}/confirm',
    {
      params: { path: { project_id: projectId, equipment_list_id: equipmentListId } },
    },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('equipment list confirm failed', { error, response });
  }
  return data;
}

export type SampleEnvelope = components['schemas']['SampleEnvelope'];

/** Current web-authoritative inventory item. */
export type SampleInventoryItem = components['schemas']['SampleInventoryItem'];
export type SampleInventoryPage = components['schemas']['SampleInventoryPage'];
export type SampleCreateRequest = components['schemas']['SampleCreateRequest'];
export type SamplePatchRequest = components['schemas']['SamplePatchRequest'];
export type SampleStatusRequest = components['schemas']['SampleStatusRequest'];
export type SampleVersionRequest = components['schemas']['SampleVersionRequest'];
export type SampleHistoryPage = components['schemas']['SampleHistoryPage'];
export type SampleIntakeHistoryEnvelope =
  components['schemas']['SampleIntakeHistoryEnvelope'];
export type SampleIntakeHistoryList = components['schemas']['SampleIntakeHistoryList'];
export type SampleCustodyEventEnvelope =
  components['schemas']['SampleCustodyEventEnvelope'];
export type SampleCustodyEventList = components['schemas']['SampleCustodyEventList'];
export type SampleCustodyEventRequest =
  components['schemas']['SampleCustodyEventRequest'];
export type SampleCustodyEventType = SampleCustodyEventRequest['event_type'];
export type SampleRevisionEnvelope = components['schemas']['SampleRevisionEnvelope'];
export type HardDeleteReceipt = components['schemas']['HardDeleteReceipt'];

export type SampleInventoryStatusFilter = SampleInventoryItem['status'] | 'all';

export interface SampleInventoryFilters {
  readonly projectId?: string;
  readonly team?: string;
  readonly status?: SampleInventoryStatusFilter;
  readonly asOf?: string;
  readonly after?: string;
  readonly limit?: number;
  readonly includeDeleted?: boolean;
}

/** A workbook response consumed as a browser download. */
export interface PlatformDownload {
  readonly blob: Blob;
  readonly filename: string;
}

const PLATFORM_BLOB_PARSED_GET = {
  sampleInventoryExport: '/platform/projects/{project_id}/sample-inventory/exports/{template}',
} as const satisfies Record<string, PathsWithMethod<PlatformPaths, 'get'>>;

export type PlatformBlobParsedPath =
  (typeof PLATFORM_BLOB_PARSED_GET)[keyof typeof PLATFORM_BLOB_PARSED_GET];

function platformDownloadRequest<P extends PlatformBlobParsedPath, I extends object>(
  path: P,
  init: I,
) {
  return { path, init: { ...init, parseAs: 'blob' as const } };
}

function toPlatformDownload(
  failureMessage: string,
  fallbackFilename: string,
  result: {
    readonly data?: Blob | undefined;
    readonly error?: unknown;
    readonly response: Response;
  },
): PlatformDownload {
  const { data, error, response } = result;
  if (error || data === undefined) {
    throw apiErrorFromResponse(failureMessage, { error, response });
  }
  return {
    blob: data,
    filename: filenameFromContentDisposition(
      response.headers.get('content-disposition') ?? null,
      fallbackFilename,
    ),
  };
}

function sampleInventoryQuery(filters: SampleInventoryFilters) {
  const query: {
    project_id?: string;
    team?: string;
    status?: SampleInventoryStatusFilter;
    as_of?: string;
    after?: string;
    limit?: number;
    include_deleted?: boolean;
  } = {};
  if (filters.projectId !== undefined) query.project_id = filters.projectId;
  if (filters.team !== undefined) query.team = filters.team;
  if (filters.status !== undefined) query.status = filters.status;
  if (filters.asOf !== undefined) query.as_of = filters.asOf;
  if (filters.after !== undefined) query.after = filters.after;
  if (filters.limit !== undefined) query.limit = filters.limit;
  if (filters.includeDeleted !== undefined) query.include_deleted = filters.includeDeleted;
  return query;
}

export async function fetchSampleInventory(
  filters: SampleInventoryFilters = {},
): Promise<SampleInventoryPage> {
  const { data, error, response } = await platformClient.GET('/platform/sample-inventory', {
    params: { query: sampleInventoryQuery(filters) },
  });
  if (error || data === undefined) {
    throw apiErrorFromResponse('sample inventory lookup failed', { error, response });
  }
  return data;
}

export async function fetchSample(
  projectId: string,
  sampleId: string,
  asOf?: string,
): Promise<SampleInventoryItem> {
  const query = asOf === undefined ? undefined : { as_of: asOf };
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/samples/{sample_id}',
    {
      params: {
        path: { project_id: projectId, sample_id: sampleId },
        ...(query === undefined ? {} : { query }),
      },
    },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('sample lookup failed', { error, response });
  }
  return data;
}

export async function createSample(
  projectId: string,
  body: SampleCreateRequest,
): Promise<SampleInventoryItem> {
  const { data, error, response } = await platformClient.POST(
    '/platform/projects/{project_id}/samples',
    { params: { path: { project_id: projectId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('sample creation failed', { error, response });
  }
  return data;
}

export async function patchSample(
  projectId: string,
  sampleId: string,
  body: SamplePatchRequest,
): Promise<SampleInventoryItem> {
  const { data, error, response } = await platformClient.PATCH(
    '/platform/projects/{project_id}/samples/{sample_id}',
    { params: { path: { project_id: projectId, sample_id: sampleId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('sample update failed', { error, response });
  }
  return data;
}

export async function changeSampleStatus(
  projectId: string,
  sampleId: string,
  body: SampleStatusRequest,
): Promise<SampleInventoryItem> {
  const { data, error, response } = await platformClient.POST(
    '/platform/projects/{project_id}/samples/{sample_id}/status',
    { params: { path: { project_id: projectId, sample_id: sampleId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('sample status update failed', { error, response });
  }
  return data;
}

export async function softDeleteSample(
  projectId: string,
  sampleId: string,
  body: SampleVersionRequest,
): Promise<SampleInventoryItem> {
  const { data, error, response } = await platformClient.DELETE(
    '/platform/projects/{project_id}/samples/{sample_id}',
    { params: { path: { project_id: projectId, sample_id: sampleId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('sample deletion failed', { error, response });
  }
  return data;
}

export async function hardDeleteSample(sampleId: string): Promise<HardDeleteReceipt> {
  const { data, error, response } = await platformClient.DELETE(
    '/platform/system/sample-inventory/{sample_id}',
    { params: { path: { sample_id: sampleId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('sample hard delete failed', { error, response });
  }
  return data;
}

export async function fetchSampleHistory(
  projectId: string,
  sampleId: string,
  after?: string,
  limit?: number,
): Promise<SampleHistoryPage> {
  const query: { after?: string; limit?: number } = {};
  if (after !== undefined) query.after = after;
  if (limit !== undefined) query.limit = limit;
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/samples/{sample_id}/history',
    { params: { path: { project_id: projectId, sample_id: sampleId }, query } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('sample history lookup failed', { error, response });
  }
  return data;
}

export async function fetchSampleIntakes(
  projectId: string,
  sampleId: string,
): Promise<SampleIntakeHistoryList> {
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/samples/{sample_id}/intakes',
    { params: { path: { project_id: projectId, sample_id: sampleId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('sample intake history lookup failed', { error, response });
  }
  return data;
}

export async function fetchSampleCustodyEvents(
  projectId: string,
  sampleId: string,
): Promise<SampleCustodyEventList> {
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/samples/{sample_id}/custody-events',
    { params: { path: { project_id: projectId, sample_id: sampleId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('sample custody lookup failed', { error, response });
  }
  return data;
}

export async function appendSampleCustodyEvent(
  projectId: string,
  sampleId: string,
  body: SampleCustodyEventRequest,
): Promise<SampleCustodyEventEnvelope> {
  const { data, error, response } = await platformClient.POST(
    '/platform/projects/{project_id}/samples/{sample_id}/custody-events',
    { params: { path: { project_id: projectId, sample_id: sampleId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('custody event append failed', { error, response });
  }
  return data;
}

export async function deleteSampleCustodyEvent(
  projectId: string,
  sampleId: string,
  eventId: string,
): Promise<void> {
  const { error, response } = await platformClient.DELETE(
    '/platform/projects/{project_id}/samples/{sample_id}/custody-events/{event_id}',
    {
      params: {
        path: { project_id: projectId, sample_id: sampleId, event_id: eventId },
      },
    },
  );
  if (error) {
    throw apiErrorFromResponse('custody event delete failed', { error, response });
  }
}

export async function exportSampleInventory(
  projectId: string,
  template: 'pm-status' | 'rf-data',
  filters: Omit<SampleInventoryFilters, 'projectId' | 'after' | 'limit'> = {},
): Promise<PlatformDownload> {
  const query = sampleInventoryQuery(filters);
  delete query.project_id;
  delete query.after;
  delete query.limit;
  const request = platformDownloadRequest(PLATFORM_BLOB_PARSED_GET.sampleInventoryExport, {
    params: {
      path: { project_id: projectId, template },
      query,
    },
  });
  return toPlatformDownload(
    'sample inventory export failed',
    `sample-inventory-${template}.xlsx`,
    await platformClient.GET(request.path, request.init),
  );
}

// ── WEB-PROVIDER-UI-0: provider UI descriptor (read-only proxy) ─────────────
//
// The platform proxies a provider-owned UI descriptor (served by the provider's
// GET /headless/ui-descriptor + the platform registry). apps/web consumes ONLY
// this platform client — never headless-client — so a provider's private
// headless surface is never called from the browser. The descriptor is
// schema-first: every label/column/sheet is descriptor runtime data, so the
// viewer hardcodes no provider technology / equipment / Excel-column literal.

export type ProviderUiDescriptor = components['schemas']['ProviderUiDescriptor'];
export type ProviderFieldDescriptor = components['schemas']['ProviderFieldDescriptor'];
export type ProviderSummary = components['schemas']['ProviderSummary'];
export type ProviderSummaryList = components['schemas']['ProviderSummaryList'];

/**
 * List the registered providers as selectable summaries (WEB-PROVIDER-UI-0).
 * Backs the ProviderPicker so the browser never hardcodes a provider list — the
 * options are the backend registry's `summaries()` projection. Throws a
 * `ApiError` on a non-2xx response (403 when `platform:read` is missing).
 */
export async function fetchProviderList(): Promise<ProviderSummaryList> {
  const { data, error, response } = await platformClient.GET('/platform/providers', {});
  if (error || data === undefined) {
    throw apiErrorFromResponse('provider list lookup failed', { error, response });
  }
  return data;
}

/**
 * Fetch a provider's UI descriptor (read-only foundation, WEB-PROVIDER-UI-0).
 * Throws a `ApiError` on a non-2xx response — 404 when the provider is not
 * registered, 403 when the `platform:read` permission is missing.
 */
export async function fetchProviderUiDescriptor(providerId: string): Promise<ProviderUiDescriptor> {
  const { data, error, response } = await platformClient.GET(
    '/platform/providers/{provider_id}/ui-descriptor',
    { params: { path: { provider_id: providerId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('provider UI descriptor lookup failed', { error, response });
  }
  return data;
}

// ── 멀티챔버 P6: chamber availability + central measurement proxy ────────────
//
// The chamber surface is the multi-chamber workbench (roadmap Phase 6). The web
// reads the central `chamber_availability` view (GET /platform/chambers,
// platform:read) and drives a distributed measurement through the central proxy
// (P5): POST /platform/chambers/{id}/measurements (platform:claim) starts a run
// on a chamber node, GET .../measurements/progress (platform:read) polls it. The
// browser never calls a chamber node directly — the hub forwards. The helpers
// here own the wire shape so view code stays declarative (mirror of the coverage
// helpers above).

export type ChamberAvailabilityEnvelope = components['schemas']['ChamberAvailabilityEnvelope'];
export type ChamberAvailabilityList = components['schemas']['ChamberAvailabilityList'];
export type ChamberNodeEnvelope = components['schemas']['ChamberNodeEnvelope'];
export type RegisterChamberRequest = components['schemas']['RegisterChamberRequest'];
export type ChamberMeasurementSnapshot = components['schemas']['ChamberMeasurementSnapshot'];
export type StartChamberMeasurementRequest =
  components['schemas']['StartChamberMeasurementRequest'];

/**
 * Fetch chamber availability from the central `chamber_availability` view. Each
 * row carries the chamber's derived status (idle/in_use/offline), last heartbeat,
 * and current session. Throws a `ApiError` on a non-2xx response (403 when the
 * `platform:read` permission is missing).
 */
export async function fetchChambers(): Promise<ChamberAvailabilityList> {
  const { data, error, response } = await platformClient.GET('/platform/chambers', {});
  if (error || data === undefined) {
    throw apiErrorFromResponse('chamber availability lookup failed', { error, response });
  }
  return data;
}

/**
 * Register or update a chamber node. The backend endpoint is an UPSERT keyed by
 * `chamber_id`, so the admin UI uses it for name/base_url/ttl/enabled edits and
 * enable/disable toggles. `platform:admin` gated. Machine-token secrets are not
 * part of this request/response contract and must never be shown by the browser.
 */
export async function registerChamber(body: RegisterChamberRequest): Promise<ChamberNodeEnvelope> {
  const { data, error, response } = await platformClient.POST('/platform/chambers', { body });
  if (error || data === undefined) {
    throw apiErrorFromResponse('chamber registration failed', { error, response });
  }
  return data;
}

export type ChamberEquipmentConfig = components['schemas']['ChamberEquipmentConfig'];
export type UpdateChamberWebSessionApprovalRequest =
  components['schemas']['UpdateChamberWebSessionApprovalRequest'];
export type UpdateChamberEquipmentConfigRequest =
  components['schemas']['UpdateChamberEquipmentConfigRequest'];

/**
 * Read a chamber's instrument connection settings (SPLIT-6 ②). `platform:read`
 * gated — this is the operator-facing door, distinct from the node-scoped
 * `/settings` read a chamber PC pulls at boot.
 *
 * The map is opaque here on purpose: which keys exist is declared by that
 * provider's UI descriptor, and what they mean is resolved by the provider's
 * node. The browser renders whatever it is handed and never names a key.
 */
export async function fetchChamberEquipmentConfig(
  chamberId: string,
): Promise<ChamberEquipmentConfig> {
  const { data, error, response } = await platformClient.GET(
    '/platform/chambers/{chamber_id}/equipment-config',
    { params: { path: { chamber_id: chamberId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('equipment config lookup failed', { error, response });
  }
  return data;
}

/**
 * Change a chamber's instrument connection settings. `platform:chamber-config-write`
 * gated.
 *
 * ⚠️ The body is a patch per KEY: omit a key to leave it alone, send `null` to
 * delete it. Send ONLY the fields the operator actually edited — the server
 * merges per key inside one locked transaction, and that is what lets two
 * testers edit two different fields of the same chamber at once. Echoing every
 * rendered field turns the request back into whole-document replacement and
 * silently overwrites the other person's change with the value this browser
 * happened to load.
 */
/**
 * Record the operator ruling on whether a chamber accepts web sessions.
 *
 * ⚠️ **Three-valued, and the three values are not interchangeable.** `true`/`false`
 * are rulings; `null` withdraws the ruling back to *"nobody has decided"*, which is
 * a different operator state from an explicit `false` and must stay reachable.
 * Omitting the field entirely means "leave it unchanged" — so this helper always
 * sends the key, and the caller decides which of the three it is.
 *
 * This records approval only. It starts nothing and blocks nothing; whether the
 * node actually opened a listener is observed from its heartbeat, and the server
 * compares the two.
 */
export async function updateChamberWebSessionApproval(
  chamberId: string,
  acceptsWebSessions: boolean | null,
): Promise<ChamberNodeEnvelope> {
  const { data, error, response } = await platformClient.PATCH(
    '/platform/chambers/{chamber_id}/web-session-approval',
    {
      params: { path: { chamber_id: chamberId } },
      body: { accepts_web_sessions: acceptsWebSessions },
    },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('web session approval update failed', { error, response });
  }
  return data;
}

export async function updateChamberEquipmentConfig(
  chamberId: string,
  body: UpdateChamberEquipmentConfigRequest,
): Promise<ChamberEquipmentConfig> {
  const { data, error, response } = await platformClient.PATCH(
    '/platform/chambers/{chamber_id}/equipment-config',
    { params: { path: { chamber_id: chamberId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('equipment config update failed', { error, response });
  }
  return data;
}

/**
 * Start a measurement on a chamber via the central proxy (P5). The hub gates on
 * the chamber being idle, looks up its `base_url`, and forwards the start to the
 * node's Session API. `platform:claim` gated. Throws a `ApiError` on a non-2xx
 * response — notably 409 when the chamber is not idle (already in use / offline).
 */
export async function startChamberMeasurement(
  chamberId: string,
  body: StartChamberMeasurementRequest,
): Promise<ChamberMeasurementSnapshot> {
  const { data, error, response } = await platformClient.POST(
    '/platform/chambers/{chamber_id}/measurements',
    { params: { path: { chamber_id: chamberId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('chamber measurement start failed', { error, response });
  }
  return data;
}

/**
 * Poll a chamber measurement's progress via the central proxy (P5). `platform:read`
 * gated. Throws a `ApiError` on a non-2xx response.
 */
export async function fetchChamberProgress(chamberId: string): Promise<ChamberMeasurementSnapshot> {
  const { data, error, response } = await platformClient.GET(
    '/platform/chambers/{chamber_id}/measurements/progress',
    { params: { path: { chamber_id: chamberId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('chamber progress lookup failed', { error, response });
  }
  return data;
}

// ── FE-P3-write: claim acquire / release (central claim_events ledger) ──────
//
// These MUTATE the central ledger. View code calls them through a react-query
// mutation and refetches claims on success (central-write-then-refresh, NOT
// optimistic) so the lock overlay reflects the committed central state. A 409
// status means the condition is already held by another operator (acquire) or
// no open claim matched (release); status `undefined` means the central server
// was unreachable (offline) — the caller surfaces both distinctly.

/**
 * Acquire a measurement claim before measuring a condition. Throws a
 * `ApiError` (carrying the HTTP status) on a non-2xx response — notably 409
 * when another operator already holds the condition (the enforcement that turns
 * the FE-P3 lock overlay from a warning into a guarantee).
 */
export async function acquireClaim(
  projectId: string,
  body: AcquireClaimRequest,
): Promise<ClaimEventEnvelope> {
  const { data, error, response } = await platformClient.POST(
    '/platform/projects/{project_id}/claims',
    { params: { path: { project_id: projectId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('claim acquire failed', { error, response });
  }
  return data;
}

/**
 * Release (or expire) an open claim after measuring. Throws a `ApiError`
 * on a non-2xx response — 409 when no still-open claim matches `claimId`.
 */
export async function releaseClaim(
  projectId: string,
  claimId: string,
  body: ReleaseClaimRequest = {},
): Promise<ClaimEventEnvelope> {
  const { data, error, response } = await platformClient.POST(
    '/platform/projects/{project_id}/claims/{claim_id}/release',
    { params: { path: { project_id: projectId, claim_id: claimId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('claim release failed', { error, response });
  }
  return data;
}

// ── FE-P8: project_membership read + admin write ────────────────────────────
//
// The membership roster (read) is `platform:read` so a viewer can see the
// project's RBAC table. assign/revoke MUTATE central state and are gated by a
// dedicated `platform:admin` permission — viewer/engineer tokens cannot
// escalate, and every change is audited via `audit_events`. View code calls
// these through a react-query mutation and refetches the roster on success
// (central-write-then-refresh, mirror of FE-P3-write).

/**
 * Fetch one keyset page of project memberships (FE-P2 RBAC roster). Same
 * keyset contract as {@link fetchCoveragePage} / {@link fetchClaimsPage}
 * — `cursor === undefined` requests the first page; pass the previous
 * page's `nextCursor` to continue. There is no technology facet (membership
 * is project-scoped, not tech-scoped). Throws on non-2xx.
 */
export async function fetchMembershipsPage(
  projectId: string,
  cursor?: string,
): Promise<PlatformPage<MembershipEnvelope>> {
  const query: { limit: number; cursor?: string } = { limit: PLATFORM_PAGE_SIZE };
  if (cursor !== undefined) query.cursor = cursor;
  const { data, error, response } = await platformClient.GET(
    '/platform/projects/{project_id}/memberships',
    { params: { path: { project_id: projectId }, query } },
  );
  if (error) {
    throw apiErrorFromResponse('memberships lookup failed', { error, response });
  }
  return { items: data ?? [], nextCursor: nextCursorFromResponse(response) };
}

/**
 * Assign or update a project membership role. UPSERT on
 * (project_id, user_subject, role_key) — re-assigning the same triple is
 * idempotent at the data layer but each call is audited. Throws a
 * `ApiError` on a non-2xx response — 400 when role_key is unknown
 * to the rbac_role_grants SSOT, 404 when user_subject is not yet onboarded
 * by the IdP.
 */
export async function assignMembership(
  projectId: string,
  body: AssignMembershipRequest,
): Promise<MembershipEnvelope> {
  const { data, error, response } = await platformClient.POST(
    '/platform/projects/{project_id}/memberships',
    { params: { path: { project_id: projectId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('membership assign failed', { error, response });
  }
  return data;
}

/**
 * Revoke a project membership role. Throws a `ApiError` on a non-2xx
 * response — 404 when the (project, user, role) triple has no current
 * assignment.
 */
export async function revokeMembership(
  projectId: string,
  body: RevokeMembershipRequest,
): Promise<MembershipEnvelope> {
  const { data, error, response } = await platformClient.POST(
    '/platform/projects/{project_id}/memberships/revoke',
    { params: { path: { project_id: projectId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('membership revoke failed', { error, response });
  }
  return data;
}

// ── 참조 카탈로그 (2026-08-08) ─────────────────────────────────────────────
/**
 * 중앙 플랫폼이 측정 참조 데이터의 **권위 있는 원본**이고 챔버 PC 는 복제본이다.
 * 이 화면이 하는 일은 셋뿐이다 — 지금 무엇이 게시돼 있는지 보고, 후보의 내용을
 * 검토하고, 게시한다.
 *
 * 2026-08-11 부터 저작도 여기서 한다 — 복사해서 새 판(fork), 칸 수정, **처음부터
 * 만들기**, **행 추가·삭제**, 그리고 게시. (이 주석은 2026-08-09 에 셀 편집이
 * 착지한 뒤에도 "저작은 없다"고 적고 있었다. 화면이 하는 일을 잘못 적은 주석은
 * 다음 사람이 없는 제약을 우회하려 들게 만든다.)
 *
 * 처음부터 만들기는 워크북 임포터와 **다른 operation** 이다. 어느 operation 이
 * 돌았는지가 곧 provenance 이므로(`WEB_AUTHORED`), 같은 경로에 플래그를 더하면
 * 감사 사실이 요청 모양이 정하는 값이 된다. 워크북 임포트는 여전히 운영자 CLI
 * (`scripts/import_workbook_references.py`)가 한다.
 *
 * 권한: 읽기는 `platform:read`, 저작/게시는 `platform:reference-write`
 * (project_engineer = 시험원 + project_admin). 백엔드 `authorize` 는 토큰 ∪ 프로젝트
 * 멤버십 UNION 이므로, 토큰 미보유를 근거로 화면이 버튼을 잠그면 멤버십으로 권한을
 * 받은 시험원이 부당하게 차단된다 — 백엔드를 최종 권위로 두고 403 을 표면화한다.
 */
export type ReferenceRevisionSummary = components['schemas']['ReferenceRevisionSummary'];
export type ReferenceRevisionList = components['schemas']['ReferenceRevisionList'];
export type ReferenceRevisionDetail = components['schemas']['ReferenceRevisionDetail'];
export type ReferenceEntryRecord = components['schemas']['ReferenceEntryRecord'];
export type ReferenceRevisionEnvelope = components['schemas']['ReferenceRevisionEnvelope'];
export type PublishReferenceRevisionRequest =
  components['schemas']['PublishReferenceRevisionRequest'];
export type UpdateReferenceRevisionEntriesRequest =
  components['schemas']['UpdateReferenceRevisionEntriesRequest'];
export type ReferenceEntryEdit = components['schemas']['ReferenceEntryEdit'];
export type CreateAuthoredReferenceRevisionRequest =
  components['schemas']['CreateAuthoredReferenceRevisionRequest'];
export type UpdateReferenceRevisionRowsRequest =
  components['schemas']['UpdateReferenceRevisionRowsRequest'];
export type AuthoredReferenceEntry = components['schemas']['AuthoredReferenceEntry'];
export type ReferenceFamilyDescriptor = components['schemas']['ReferenceFamilyDescriptor'];

/** 리비전 identity 축의 facet — 생성 타입에서 파생하므로 여기에 목록이 없다. */
export type ReferenceFamily = ReferenceRevisionSummary['family'];
export type ReferenceRevisionState = ReferenceRevisionSummary['state'];
export type ReferenceScopeKind = ReferenceRevisionSummary['scope_kind'];

export interface ReferenceRevisionsPage {
  readonly items: ReferenceRevisionList;
  readonly nextCursor: string | null;
}

export interface ReferenceRevisionQuery {
  readonly family?: ReferenceFamily;
  readonly scopeKind?: ReferenceScopeKind;
  readonly scopeId?: string;
  readonly state?: ReferenceRevisionState;
  readonly limit?: number;
  readonly cursor?: string;
}

/**
 * 한 provider 의 리비전 한 페이지. 커서는 응답 헤더로 오고 본문은 평평한 배열이라
 * coverage/claims/memberships 와 같은 모양이다.
 */
export async function fetchReferenceRevisions(
  providerId: string,
  query: ReferenceRevisionQuery = {},
): Promise<ReferenceRevisionsPage> {
  // `exactOptionalPropertyTypes` 아래에서는 키를 `undefined` 로 두는 것과 키가
  // 없는 것이 다르다. 미지정 facet 은 쿼리에서 **빠져야** 하며, 그래야 서버가
  // "전체"와 "빈 값으로 필터"를 구별한다.
  const search: Record<string, string | number> = {};
  if (query.family !== undefined) search.family = query.family;
  if (query.scopeKind !== undefined) search.scope_kind = query.scopeKind;
  if (query.scopeId) search.scope_id = query.scopeId;
  if (query.state !== undefined) search.state = query.state;
  if (query.limit !== undefined) search.limit = query.limit;
  if (query.cursor) search.cursor = query.cursor;

  const { data, error, response } = await platformClient.GET(
    '/platform/providers/{provider_id}/reference-revisions',
    { params: { path: { provider_id: providerId }, query: search } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('reference revisions lookup failed', { error, response });
  }
  return {
    items: data,
    nextCursor: response?.headers.get('X-Next-Cursor') ?? null,
  };
}

/**
 * 한 리비전의 엔트리 + **열 순서** + 결합 그룹의 형제 패밀리.
 *
 * 열 이름도 짝 어휘도 여기서 다시 선언하지 않는다. payload 는 열린 매핑이라 null
 * 필드가 생략될 수 있어 엔트리마다 열 집합이 달라지고, 짝을 적으면 결합 어휘가
 * 백엔드 도메인과 TS 두 곳으로 쪼개진다.
 */
export async function fetchReferenceRevision(
  providerId: string,
  revisionId: string,
): Promise<ReferenceRevisionDetail> {
  const { data, error, response } = await platformClient.GET(
    '/platform/providers/{provider_id}/reference-revisions/{revision_id}',
    { params: { path: { provider_id: providerId, revision_id: revisionId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('reference revision lookup failed', { error, response });
  }
  return data;
}

/**
 * 게시본을 복사해 **고칠 수 있는 후보**를 만든다.
 *
 * 이것이 시험원을 저자로 만드는 지점이다. 이전에는 후보를 만드는 방법이 운영자
 * CLI 하나뿐이라, 재배선 후 케이블 손실을 다시 잰 사람이 자기 숫자를 넣지 못하고
 * 기다려야 했다 — 그 기다림 동안 워크북이 계속 권위다.
 *
 * 본문이 없다. fork 에 필요한 것은 전부 부모에 있고, 본문을 두면 클라이언트가
 * 부모가 이미 답하는 것을 주장할 자리만 생긴다.
 */
export async function forkReferenceRevision(
  providerId: string,
  revisionId: string,
): Promise<ReferenceRevisionDetail> {
  const { data, error, response } = await platformClient.POST(
    '/platform/providers/{provider_id}/reference-revisions/{revision_id}/fork',
    { params: { path: { provider_id: providerId, revision_id: revisionId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('reference fork failed', { error, response });
  }
  return data;
}

/**
 * 후보의 지목된 행들만 값을 바꾼다.
 *
 * `expected_etag` 는 **서버가 준 값을 그대로 되돌려 보내는** 동시성 토큰이다
 * (조립하는 값이 아니다). 서버가 그것을 UPDATE 의 WHERE 절에서 검사하므로 두
 * 편집이 동시에 이겼다고 믿는 창이 없고, 낡은 것은 409 다.
 *
 * 파생값은 하나도 싣지 않는다 — `content_sha256`/`etag`/`version`/`provenance_kind`
 * 는 서버가 다시 센다. 여기서 해싱하면 같은 규칙이 두 언어로 쪼개지고, 그 드리프트는
 * 시험원이 게시한 뒤에야 드러난다.
 */
export async function updateReferenceRevisionEntries(
  providerId: string,
  revisionId: string,
  body: UpdateReferenceRevisionEntriesRequest,
): Promise<ReferenceRevisionDetail> {
  const { data, error, response } = await platformClient.PUT(
    '/platform/providers/{provider_id}/reference-revisions/{revision_id}/entries',
    {
      params: { path: { provider_id: providerId, revision_id: revisionId } },
      body,
    },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('reference entry edit failed', { error, response });
  }
  return data;
}

/**
 * 후보를 게시한다. 결합 그룹(예: 케이블 손실 ↔ 포트맵)이면 `coupled_revision_id` 로
 * 형제 후보를 **같은 요청에** 실어야 하고, 서버는 한 트랜잭션으로 둘 다 옮긴다.
 * 반쪽만 보내면 409 이고 그 메시지가 형제 패밀리를 댄다.
 */
/**
 * 저작 가능한 패밀리와 각각이 요구하는 칸 목록.
 *
 * 처음부터 만들기 화면이 이것 없이는 **어떤 칸을 그려야 하는지 모른다**. 상세 응답의
 * `payload_columns` 는 리비전이 하나라도 있어야 답하고, 새 패밀리는 정확히 그것이
 * 없는 경우다. 프론트에 6 패밀리 × N 컬럼을 적는 것은 같은 순서를 두 언어로 쪼개는
 * 일이고 이 화면이 명시적으로 금지하는 것이다.
 */
export async function fetchReferenceFamilies(
  providerId: string,
): Promise<ReferenceFamilyDescriptor[]> {
  const { data, error, response } = await platformClient.GET(
    '/platform/providers/{provider_id}/reference-families',
    { params: { path: { provider_id: providerId } } },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('reference families load failed', { error, response });
  }
  return data;
}

/**
 * 워크북 없이 **처음부터** 리비전을 만든다.
 *
 * 요청은 `payload` 만 나른다. `reference_id`/`identity_key`/`content_sha256` 은 전부
 * payload 의 파생값이라 **서버가 민팅**한다 — 여기서 조립하면 저장된 identity_key 가
 * 그 행을 설명하지 않는 상태를 만들 수 있고, 그 어긋남은 투영이 측정 경로가 읽는
 * 테이블을 채울 때에야 드러난다.
 *
 * `provenance_kind` 도 싣지 않는다. 이 함수가 호출됐다는 사실 자체가 그 값이다.
 */
export async function createAuthoredReferenceRevision(
  providerId: string,
  body: CreateAuthoredReferenceRevisionRequest,
): Promise<ReferenceRevisionEnvelope> {
  const { data, error, response } = await platformClient.POST(
    '/platform/providers/{provider_id}/reference-revisions/authored',
    { params: { path: { provider_id: providerId } }, body },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('authored reference create failed', { error, response });
  }
  return data;
}

/**
 * 후보의 **행을** 더하거나 뺀다.
 *
 * 값 편집(`updateReferenceRevisionEntries`)과 다른 요청인 이유는 서버 정책이 식별
 * 필드 이동을 거부하는 사유 그 자체다 — 그것은 추가+삭제이지 편집이 아니다. 삭제는
 * `reference_id` 로 지목하고(유일 인덱스를 가진 유일한 키) 추가는 payload 만 나른다.
 *
 * `expected_etag` 는 값 편집과 같은 동시성 토큰이고, 낡은 것은 409 다.
 */
export async function updateReferenceRevisionRows(
  providerId: string,
  revisionId: string,
  body: UpdateReferenceRevisionRowsRequest,
): Promise<ReferenceRevisionDetail> {
  const { data, error, response } = await platformClient.POST(
    '/platform/providers/{provider_id}/reference-revisions/{revision_id}/rows',
    {
      params: { path: { provider_id: providerId, revision_id: revisionId } },
      body,
    },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('reference row edit failed', { error, response });
  }
  return data;
}

export async function publishReferenceRevision(
  providerId: string,
  revisionId: string,
  body: PublishReferenceRevisionRequest,
): Promise<ReferenceRevisionEnvelope> {
  const { data, error, response } = await platformClient.POST(
    '/platform/providers/{provider_id}/reference-revisions/{revision_id}/publish',
    {
      params: { path: { provider_id: providerId, revision_id: revisionId } },
      body,
    },
  );
  if (error || data === undefined) {
    throw apiErrorFromResponse('reference publish failed', { error, response });
  }
  return data;
}
