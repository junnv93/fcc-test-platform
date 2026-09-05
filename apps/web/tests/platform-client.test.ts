import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  PLATFORM_NEXT_CURSOR_HEADER,
  PLATFORM_PAGE_SIZE,
  assignMembership,
  fetchClaimsPage,
  fetchCoveragePage,
  fetchMembershipsPage,
  fetchProjects,
  fetchProjectsPage,
  platformClient,
  resolvePlatformBaseUrl,
  revokeMembership,
  updateProject,
  type AssignMembershipRequest,
  type MembershipEnvelope,
  type PlatformApiPaths,
  type RevokeMembershipRequest,
  type UpdateProjectRequest,
} from '@/api/platform-client';
import { problemParams, toApiError } from '@/api/to-api-error';

/**
 * FE-P0d (2026-05-27) — apps/web Platform read API client + codegen drift gate.
 *
 * Validates the full SSOT chain:
 *   `packages/api-artifacts/artifacts/platform-api.openapi.json`
 *   (mirror of backend build_platform_openapi_schema SSOT) → `npm run codegen` → `src/api/generated/platform-api.types.ts` →
 *   `@/api/platform-client` typed openapi-fetch surface.
 *
 * The platform surface is the typed foundation FE-P2 (coverage dashboard) +
 * FE-P3 (claim/lock UX) build on — no mock client. This sprint ships the typed
 * client only; the UI routes are out of scope (mock UI forbidden).
 */

const APPS_WEB_ROOT = resolve(__dirname, '..');
const PLATFORM_TYPES_PATH = resolve(
  APPS_WEB_ROOT,
  'src',
  'api',
  'generated',
  'platform-api.types.ts',
);

describe('platform client surface', () => {
  it('exposes a typed `openapi-fetch` client', () => {
    expect(platformClient).toBeDefined();
    expect(typeof platformClient.GET).toBe('function');
  });

  it('re-exports the generated paths type alias', () => {
    // Compile-time check: fails `npm run typecheck` if generator drift leaves
    // `PlatformApiPaths` undefined. Runtime is a noop.
    const _surface: PlatformApiPaths | undefined = undefined;
    expect(_surface).toBeUndefined();
  });
});

describe('platform generated types — drift gate', () => {
  it('generated types file exists (run `npm run codegen` if missing)', () => {
    expect(existsSync(PLATFORM_TYPES_PATH)).toBe(true);
  });

  it('cites the platform-api OpenAPI artifact in the codegen banner', () => {
    const source = readFileSync(PLATFORM_TYPES_PATH, 'utf8');
    expect(source).toContain('packages/api-artifacts/artifacts/platform-api.openapi.json');
    expect(source).toMatch(/AUTO-GENERATED/);
  });

  it('declares the project-scoped coverage + claims routes (not headless)', () => {
    const source = readFileSync(PLATFORM_TYPES_PATH, 'utf8');
    expect(source).toContain('/platform/projects/{project_id}/coverage');
    expect(source).toContain('/platform/projects/{project_id}/claims');
    expect(source).not.toContain('/headless/');
  });

  it('declares every backend operationId', () => {
    const source = readFileSync(PLATFORM_TYPES_PATH, 'utf8');
    for (const operationId of ['get_project_coverage', 'list_project_claims']) {
      expect(source).toContain(operationId);
    }
  });

  it('declares the coverage + claim envelope + array component schemas', () => {
    const source = readFileSync(PLATFORM_TYPES_PATH, 'utf8');
    for (const schema of [
      'CoverageEnvelope',
      'ProjectCoverageList',
      'ActiveClaimEnvelope',
      'ActiveClaimList',
    ]) {
      expect(source).toContain(schema);
    }
  });

  it('exposes opt-in keyset pagination (limit/cursor query + X-Next-Cursor header)', () => {
    const source = readFileSync(PLATFORM_TYPES_PATH, 'utf8');
    // Body stays a plain array (backward compatible) — pagination is opt-in via
    // typed query params + the next-page cursor response header.
    expect(source).toContain('limit');
    expect(source).toContain('cursor');
    expect(source).toContain('X-Next-Cursor');
  });

  it('exposes the optional technology facet query (Phase B)', () => {
    const source = readFileSync(PLATFORM_TYPES_PATH, 'utf8');
    // Cross-language seal: the contract SSOT (PLATFORM_API_QUERY_PARAMS) emits an
    // optional `technology?` query on both ops through OpenAPI → codegen. Backward
    // compatible (absent ⇒ full project); the client helpers forward it verbatim.
    expect(source).toMatch(/technology\?:\s*string/u);
  });

  it('exports a typed `paths` interface for openapi-fetch consumers', () => {
    const source = readFileSync(PLATFORM_TYPES_PATH, 'utf8');
    expect(source).toMatch(/export interface paths\b/);
  });
});

/**
 * FE-P2 keyset pagination adoption (2026-05-27) — the page fetch helpers issue
 * the typed limit/cursor query and read the next-page cursor from the
 * X-Next-Cursor response header. Spying on the real `platformClient.GET` keeps
 * the typed-call assertion honest (no hand-rolled mock client).
 */
describe('platform page helpers — keyset pagination', () => {
  const PROJECT_ID = '11111111-1111-4111-8111-111111111111';

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function stubResponse(items: unknown[], nextCursor: string | null) {
    const headers = new Headers();
    if (nextCursor !== null) headers.set(PLATFORM_NEXT_CURSOR_HEADER, nextCursor);
    return { data: items, error: undefined, response: { status: 200, headers } };
  }

  it('coverage: requests the typed limit/cursor query and extracts the next cursor', async () => {
    const get = vi
      .spyOn(platformClient, 'GET')
      .mockResolvedValue(stubResponse([{ project_id: PROJECT_ID }], 'cursor-2') as never);

    const result = await fetchCoveragePage(PROJECT_ID, 'cursor-1');

    expect(get).toHaveBeenCalledWith('/platform/projects/{project_id}/coverage', {
      params: {
        path: { project_id: PROJECT_ID },
        query: { limit: PLATFORM_PAGE_SIZE, cursor: 'cursor-1' },
      },
    });
    expect(result.items).toHaveLength(1);
    expect(result.nextCursor).toBe('cursor-2');
  });

  it('coverage: first page passes cursor undefined; last page yields a null cursor', async () => {
    const get = vi.spyOn(platformClient, 'GET').mockResolvedValue(stubResponse([], null) as never);

    const result = await fetchCoveragePage(PROJECT_ID);

    // First page omits the cursor key entirely (exactOptionalPropertyTypes).
    expect(get).toHaveBeenCalledWith('/platform/projects/{project_id}/coverage', {
      params: { path: { project_id: PROJECT_ID }, query: { limit: PLATFORM_PAGE_SIZE } },
    });
    expect(result.nextCursor).toBeNull();
  });

  it('coverage: throws a status-carrying error on a non-2xx response', async () => {
    vi.spyOn(platformClient, 'GET').mockResolvedValue({
      data: undefined,
      error: { detail: 'denied' },
      response: { status: 403, headers: new Headers() },
    } as never);

    await expect(fetchCoveragePage(PROJECT_ID)).rejects.toMatchObject({ status: 403 });
  });

  it('claims: same keyset contract over the claims route', async () => {
    const get = vi
      .spyOn(platformClient, 'GET')
      .mockResolvedValue(stubResponse([{ claim_id: 'c1' }], null) as never);

    const result = await fetchClaimsPage(PROJECT_ID, 'cursor-1');

    expect(get).toHaveBeenCalledWith('/platform/projects/{project_id}/claims', {
      params: {
        path: { project_id: PROJECT_ID },
        query: { limit: PLATFORM_PAGE_SIZE, cursor: 'cursor-1' },
      },
    });
    expect(result.items).toHaveLength(1);
    expect(result.nextCursor).toBeNull();
  });
});

/**
 * Phase B technology facet adoption (2026-05-27) — the page helpers forward the
 * typed `technology` query param so the dashboard narrows server-side. The
 * value is forwarded verbatim (backend `_facet()` normalizes); a blank/absent
 * facet omits the key entirely (full project, exactOptionalPropertyTypes).
 */
describe('platform page helpers — technology facet', () => {
  const PROJECT_ID = '11111111-1111-4111-8111-111111111111';

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function ok() {
    return { data: [], error: undefined, response: { status: 200, headers: new Headers() } };
  }

  it('coverage: forwards the typed technology facet alongside limit/cursor', async () => {
    const get = vi.spyOn(platformClient, 'GET').mockResolvedValue(ok() as never);

    await fetchCoveragePage(PROJECT_ID, 'cursor-1', 'BLE');

    expect(get).toHaveBeenCalledWith('/platform/projects/{project_id}/coverage', {
      params: {
        path: { project_id: PROJECT_ID },
        query: { limit: PLATFORM_PAGE_SIZE, cursor: 'cursor-1', technology: 'BLE' },
      },
    });
  });

  it('coverage: forwards technology on the first page (cursor omitted)', async () => {
    const get = vi.spyOn(platformClient, 'GET').mockResolvedValue(ok() as never);

    await fetchCoveragePage(PROJECT_ID, undefined, 'UNII');

    expect(get).toHaveBeenCalledWith('/platform/projects/{project_id}/coverage', {
      params: {
        path: { project_id: PROJECT_ID },
        query: { limit: PLATFORM_PAGE_SIZE, technology: 'UNII' },
      },
    });
  });

  it('coverage: omits the technology key when blank or absent (full project)', async () => {
    const get = vi.spyOn(platformClient, 'GET').mockResolvedValue(ok() as never);

    await fetchCoveragePage(PROJECT_ID, undefined, '');
    await fetchCoveragePage(PROJECT_ID);

    // Both calls carry only `limit` — no `technology` key (exactOptionalPropertyTypes).
    expect(get).toHaveBeenNthCalledWith(1, '/platform/projects/{project_id}/coverage', {
      params: { path: { project_id: PROJECT_ID }, query: { limit: PLATFORM_PAGE_SIZE } },
    });
    expect(get).toHaveBeenNthCalledWith(2, '/platform/projects/{project_id}/coverage', {
      params: { path: { project_id: PROJECT_ID }, query: { limit: PLATFORM_PAGE_SIZE } },
    });
  });

  it('claims: forwards the same technology facet (overlay scoped to the filter)', async () => {
    const get = vi.spyOn(platformClient, 'GET').mockResolvedValue(ok() as never);

    await fetchClaimsPage(PROJECT_ID, undefined, 'BT');

    expect(get).toHaveBeenCalledWith('/platform/projects/{project_id}/claims', {
      params: {
        path: { project_id: PROJECT_ID },
        query: { limit: PLATFORM_PAGE_SIZE, technology: 'BT' },
      },
    });
  });
});

/**
 * SHOULD S2 (2026-05-27) — platform read surface base URL separation. The
 * platform API is a separately deployable ASGI app; `platformApiBaseUrl`
 * overrides the gateway, `null` reuses `apiBaseUrl` (backward compatible).
 */
describe('resolvePlatformBaseUrl — platform base URL separation (S2)', () => {
  it('reuses apiBaseUrl when platformApiBaseUrl is null (single-gateway)', () => {
    expect(
      resolvePlatformBaseUrl({ apiBaseUrl: 'https://api.example.com', platformApiBaseUrl: null }),
    ).toBe('https://api.example.com');
  });

  it('uses the dedicated platformApiBaseUrl when the deployment splits it off', () => {
    expect(
      resolvePlatformBaseUrl({
        apiBaseUrl: 'https://api.example.com',
        platformApiBaseUrl: 'https://platform.example.com',
      }),
    ).toBe('https://platform.example.com');
  });
});

/**
 * FE-P8 (2026-05-28) — membership client surface invariants.
 *
 * Validates that the generated types + typed client helpers cover the
 * project_membership read + assign/revoke write surface and that the wire
 * shape matches the backend `platform:read` / `platform:admin` contract.
 */
describe('FE-P8 — generated membership types drift gate', () => {
  it('declares list/assign/revoke membership routes', () => {
    const source = readFileSync(PLATFORM_TYPES_PATH, 'utf8');
    expect(source).toContain('/platform/projects/{project_id}/memberships');
    expect(source).toContain('/platform/projects/{project_id}/memberships/revoke');
  });

  it('declares the membership operationIds + envelope schemas', () => {
    const source = readFileSync(PLATFORM_TYPES_PATH, 'utf8');
    for (const id of [
      'list_project_memberships',
      'assign_project_membership',
      'revoke_project_membership',
      'MembershipEnvelope',
      'MembershipList',
      'AssignMembershipRequest',
      'RevokeMembershipRequest',
    ]) {
      expect(source).toContain(id);
    }
  });
});

describe('FE-P8 — membership client helpers', () => {
  const PROJECT_ID = '11111111-1111-1111-1111-111111111111';

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const okMembership: MembershipEnvelope = {
    project_id: PROJECT_ID,
    user_subject: 'alice@corp',
    role_key: 'project_engineer',
    assigned_at: '2026-05-28T00:00:00+00:00',
    expires_at: null,
  };

  it('fetchMembershipsPage: first page sends limit only (no cursor key)', async () => {
    const get = vi.spyOn(platformClient, 'GET').mockResolvedValue({
      data: [okMembership],
      response: {
        headers: { get: () => null } as unknown as Headers,
      },
    } as never);

    const page = await fetchMembershipsPage(PROJECT_ID);

    expect(page.items).toHaveLength(1);
    expect(page.nextCursor).toBeNull();
    expect(get).toHaveBeenCalledWith('/platform/projects/{project_id}/memberships', {
      params: { path: { project_id: PROJECT_ID }, query: { limit: PLATFORM_PAGE_SIZE } },
    });
  });

  it('fetchMembershipsPage: subsequent page forwards the cursor verbatim', async () => {
    const get = vi.spyOn(platformClient, 'GET').mockResolvedValue({
      data: [],
      response: {
        headers: { get: (name: string) => (name === PLATFORM_NEXT_CURSOR_HEADER ? null : null) },
      },
    } as never);

    await fetchMembershipsPage(PROJECT_ID, 'opaque-cursor');

    expect(get).toHaveBeenCalledWith('/platform/projects/{project_id}/memberships', {
      params: {
        path: { project_id: PROJECT_ID },
        query: { limit: PLATFORM_PAGE_SIZE, cursor: 'opaque-cursor' },
      },
    });
  });

  it('assignMembership: POSTs the body verbatim (operationId assign_project_membership)', async () => {
    const post = vi.spyOn(platformClient, 'POST').mockResolvedValue({
      data: okMembership,
      response: { status: 200 },
    } as never);
    const body: AssignMembershipRequest = {
      user_subject: 'alice@corp',
      role_key: 'project_engineer',
    };

    const result = await assignMembership(PROJECT_ID, body);

    expect(result.user_subject).toBe('alice@corp');
    expect(post).toHaveBeenCalledWith('/platform/projects/{project_id}/memberships', {
      params: { path: { project_id: PROJECT_ID } },
      body,
    });
  });

  it('assignMembership: throws PlatformError carrying the HTTP status on 4xx/5xx', async () => {
    vi.spyOn(platformClient, 'POST').mockResolvedValue({
      error: { detail: 'unknown role' },
      response: { status: 400 },
    } as never);

    await expect(
      assignMembership(PROJECT_ID, { user_subject: 'a', role_key: 'x' }),
    ).rejects.toMatchObject({ status: 400 });
  });

  it('revokeMembership: POSTs to /revoke (URL-safe of arbitrary subjects)', async () => {
    const post = vi.spyOn(platformClient, 'POST').mockResolvedValue({
      data: okMembership,
      response: { status: 200 },
    } as never);
    const body: RevokeMembershipRequest = {
      user_subject: 'alice@corp',
      role_key: 'project_engineer',
    };

    await revokeMembership(PROJECT_ID, body);

    expect(post).toHaveBeenCalledWith('/platform/projects/{project_id}/memberships/revoke', {
      params: { path: { project_id: PROJECT_ID } },
      body,
    });
  });

  it('revokeMembership: surfaces 404 via PlatformError status', async () => {
    vi.spyOn(platformClient, 'POST').mockResolvedValue({
      error: { detail: 'no current assignment' },
      response: { status: 404 },
    } as never);

    await expect(
      revokeMembership(PROJECT_ID, { user_subject: 'a', role_key: 'project_viewer' }),
    ).rejects.toMatchObject({ status: 404 });
  });
});

/**
 * W3-B M1 (2026-07-30) — 표지 메타 편집 + 서버측 프로젝트 디렉토리 능력 계층.
 *
 * PR #63 shipped `PATCH /platform/projects/{id}` and `GET /platform/projects
 * ?q=&limit=&cursor=`; the frontend consumed neither (`.PATCH(` count: 0). These
 * seals cover the client-side half of that wiring — the wire shape only, since
 * the UI lands in a later milestone.
 *
 * **Why `toStrictEqual` / `Object.keys` instead of `toHaveBeenCalledWith`.**
 * Several criteria here are about a key being *absent* (`q` omitted for a blank
 * search, `cursor` omitted on page 1, untouched fields omitted from a PATCH
 * body). `toHaveBeenCalledWith` uses `toEqual` semantics, which treats
 * `{limit: 200}` and `{limit: 200, q: undefined}` as equal — so a regression that
 * started sending `?q=` on every keystroke would pass such an assertion while
 * changing the actual request. Asserting on `mock.calls` with `toStrictEqual`
 * (undefined-valued keys are significant) plus an explicit key-set check is what
 * makes these seals non-vacuous.
 */
describe('W3-B M1 — project directory keyset + server-side search', () => {
  const PROJECT_ID = '22222222-2222-4222-8222-222222222222';

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function stubProjects(items: unknown[], nextCursor: string | null) {
    const headers = new Headers();
    if (nextCursor !== null) headers.set(PLATFORM_NEXT_CURSOR_HEADER, nextCursor);
    return { data: items, error: undefined, response: { status: 200, headers } };
  }

  /** The query object actually handed to the typed client on call `n` (1-based). */
  function queryOfCall(calls: readonly unknown[][], n: number): Record<string, unknown> {
    const [, init] = calls[n - 1] as [string, { params: { query: Record<string, unknown> } }];
    return init.params.query;
  }

  it('S9: forwards the search term as the server-side `q` query param', async () => {
    const get = vi
      .spyOn(platformClient, 'GET')
      .mockResolvedValue(stubProjects([{ project_id: PROJECT_ID }], null) as never);

    const page = await fetchProjectsPage('active', 'SM-A165');

    // Verbatim — no client-side casing/trimming (backend normalize_search_term owns it).
    expect(queryOfCall(get.mock.calls, 1)).toStrictEqual({
      limit: PLATFORM_PAGE_SIZE,
      status: 'active',
      q: 'SM-A165',
    });
    expect(page.items).toHaveLength(1);
  });

  it('S11: a blank search term omits the `q` key entirely (no empty-string filter)', async () => {
    const get = vi.spyOn(platformClient, 'GET').mockResolvedValue(stubProjects([], null) as never);

    await fetchProjectsPage('active', '');
    await fetchProjectsPage('active');

    // toStrictEqual (not toEqual) so a `q: undefined` key would FAIL here.
    expect(queryOfCall(get.mock.calls, 1)).toStrictEqual({
      limit: PLATFORM_PAGE_SIZE,
      status: 'active',
    });
    expect(queryOfCall(get.mock.calls, 2)).toStrictEqual({
      limit: PLATFORM_PAGE_SIZE,
      status: 'active',
    });
    // Belt-and-braces: the key must not exist at all, not merely be undefined.
    expect(Object.keys(queryOfCall(get.mock.calls, 1))).not.toContain('q');
    expect(Object.keys(queryOfCall(get.mock.calls, 2))).not.toContain('q');
  });

  it('S12: consumes X-Next-Cursor and sends it back as `cursor` on the next page', async () => {
    const get = vi
      .spyOn(platformClient, 'GET')
      .mockResolvedValueOnce(stubProjects([{ project_id: PROJECT_ID }], 'opaque-cursor') as never)
      .mockResolvedValueOnce(stubProjects([], null) as never);

    const first = await fetchProjectsPage('active', 'SM');
    expect(first.nextCursor).toBe('opaque-cursor');
    // Page 1 must NOT carry a cursor key.
    expect(Object.keys(queryOfCall(get.mock.calls, 1))).not.toContain('cursor');

    const second = await fetchProjectsPage('active', 'SM', first.nextCursor ?? undefined);
    expect(queryOfCall(get.mock.calls, 2)).toStrictEqual({
      limit: PLATFORM_PAGE_SIZE,
      status: 'active',
      q: 'SM',
      cursor: 'opaque-cursor',
    });
    // Last page reports exhaustion so a view can distinguish "no more" from "unknown".
    expect(second.nextCursor).toBeNull();
  });

  it('S12: always sends `limit` — the switch that opts into the paged contract', async () => {
    const get = vi.spyOn(platformClient, 'GET').mockResolvedValue(stubProjects([], null) as never);

    await fetchProjectsPage();

    // No status/q/cursor, but `limit` present: a bounded read, unlike fetchProjects().
    expect(queryOfCall(get.mock.calls, 1)).toStrictEqual({ limit: PLATFORM_PAGE_SIZE });
  });

  it('M5 backward compatibility: fetchProjects() still sends no q/limit/cursor', async () => {
    const get = vi.spyOn(platformClient, 'GET').mockResolvedValue({
      data: [],
      error: undefined,
      response: { status: 200, headers: new Headers() },
    } as never);

    await fetchProjects('active');

    // The backend's unbounded read is byte-identical ONLY when all three are absent.
    const query = queryOfCall(get.mock.calls, 1);
    expect(query).toStrictEqual({ status: 'active' });
    for (const key of ['limit', 'q', 'cursor']) {
      expect(Object.keys(query)).not.toContain(key);
    }
  });

  it('surfaces a non-2xx directory read as a status-carrying ApiError', async () => {
    vi.spyOn(platformClient, 'GET').mockResolvedValue({
      data: undefined,
      error: { code: 'VALIDATION_ERROR', detail: 'malformed cursor' },
      response: { status: 400, headers: new Headers() },
    } as never);

    await expect(fetchProjectsPage('active', undefined, 'bogus')).rejects.toMatchObject({
      status: 400,
      code: 'VALIDATION_ERROR',
    });
  });
});

describe('W3-B M1 — updateProject partial-update wire contract', () => {
  const PROJECT_ID = '22222222-2222-4222-8222-222222222222';

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function patchBodyOfCall(calls: readonly unknown[][], n: number): Record<string, unknown> {
    const [, init] = calls[n - 1] as [string, { body: Record<string, unknown> }];
    return init.body;
  }

  it('S1: PATCHes the caller-supplied diff verbatim — untouched fields have no key', async () => {
    const patch = vi.spyOn(platformClient, 'PATCH').mockResolvedValue({
      data: { project_id: PROJECT_ID, model_name: 'SM-A165', project_code: 'SM-A165' },
      error: undefined,
      response: { status: 200, headers: new Headers() },
    } as never);
    // A one-field edit: the other seven editable fields must not appear at all,
    // or the backend's last-write-wins would clobber a concurrent editor.
    const body: UpdateProjectRequest = { fcc_grantee_code: 'A3L' };

    await updateProject(PROJECT_ID, body);

    expect(patch).toHaveBeenCalledWith('/platform/projects/{project_id}', {
      params: { path: { project_id: PROJECT_ID } },
      body,
    });
    expect(patchBodyOfCall(patch.mock.calls, 1)).toStrictEqual({ fcc_grantee_code: 'A3L' });
    expect(Object.keys(patchBodyOfCall(patch.mock.calls, 1))).toStrictEqual(['fcc_grantee_code']);
  });

  it('S2: a cleared field travels as an explicit null, distinct from an untouched one', async () => {
    const patch = vi.spyOn(platformClient, 'PATCH').mockResolvedValue({
      data: { project_id: PROJECT_ID },
      error: undefined,
      response: { status: 200, headers: new Headers() },
    } as never);

    await updateProject(PROJECT_ID, { applicant_name: null, applicant_address: 'ACME' });

    // `null` = delete, key-absent = unchanged. The client must not collapse the
    // two (e.g. by stripping nullish values before sending).
    expect(patchBodyOfCall(patch.mock.calls, 1)).toStrictEqual({
      applicant_name: null,
      applicant_address: 'ACME',
    });
  });

  it('S6: a 409 identifier conflict carries params.field for form attribution', async () => {
    vi.spyOn(platformClient, 'PATCH').mockResolvedValue({
      data: undefined,
      error: {
        code: 'PROJECT_IDENTIFIER_CONFLICT',
        detail: 'management_number already used',
        params: { field: 'management_number', resource: 'project' },
      },
      response: { status: 409, headers: new Headers() },
    } as never);

    await expect(updateProject(PROJECT_ID, { management_number: 'DUP-1' })).rejects.toMatchObject({
      status: 409,
      code: 'PROJECT_IDENTIFIER_CONFLICT',
      params: { field: 'management_number' },
    });
  });

  it('surfaces 400 (empty diff) and 404 (unknown project) via the ApiError status', async () => {
    const patch = vi.spyOn(platformClient, 'PATCH');
    patch.mockResolvedValueOnce({
      data: undefined,
      error: { code: 'VALIDATION_ERROR', detail: 'requires at least one field' },
      response: { status: 400, headers: new Headers() },
    } as never);
    await expect(updateProject(PROJECT_ID, {})).rejects.toMatchObject({ status: 400 });

    patch.mockResolvedValueOnce({
      data: undefined,
      error: { code: 'NOT_FOUND', detail: 'unknown project_id' },
      response: { status: 404, headers: new Headers() },
    } as never);
    await expect(updateProject(PROJECT_ID, { applicant_address: 'ACME' })).rejects.toMatchObject({
      status: 404,
    });
  });
});

/**
 * S8 — `ApiError.params` is an ADDITIVE extension of the `toApiError` SSOT.
 *
 * ~23 call sites pass three arguments and must keep the exact own-property set
 * they had before this milestone. `'params' in error` (not `error.params ===
 * undefined`) is the honest check: the failure mode being sealed is a spurious
 * `params: undefined` key, which reads as `undefined` either way.
 */
describe('W3-B M1 — toApiError params extension (S8)', () => {
  it('attaches no `params` key when the caller passes none', () => {
    const error = toApiError('boom', 503, 'UPSTREAM_UNAVAILABLE');

    expect('params' in error).toBe(false);
    expect(Object.keys(error)).toStrictEqual(['status', 'code']);
  });

  it('keeps the status-only shape untouched (network error)', () => {
    const error = toApiError('offline', undefined);

    expect(Object.keys(error)).toStrictEqual(['status']);
    expect(error.status).toBeUndefined();
  });

  it('attaches `params` only when supplied, without disturbing status/code', () => {
    const error = toApiError('conflict', 409, 'PROJECT_IDENTIFIER_CONFLICT', {
      field: 'management_number',
    });

    expect(Object.keys(error)).toStrictEqual(['status', 'code', 'params']);
    expect(error.params?.field).toBe('management_number');
    expect(error.status).toBe(409);
  });

  it('problemParams: extracts an object body, rejects everything else', () => {
    expect(problemParams({ params: { field: 'project_code' } })).toStrictEqual({
      field: 'project_code',
    });
    // Legacy non-problem body, null params, non-objects, and arrays → undefined.
    expect(problemParams({ detail: 'legacy' })).toBeUndefined();
    expect(problemParams({ params: null })).toBeUndefined();
    expect(problemParams('nope')).toBeUndefined();
    expect(problemParams(undefined)).toBeUndefined();
    expect(problemParams({ params: ['field'] })).toBeUndefined();
  });

  it('problemParams: folds an empty mapping to undefined (mirrors backend as_dict)', () => {
    // The backend writes the member only `if self.params:`, so `{}` never reaches
    // the wire — folding it keeps "no context" as a single observable state.
    expect(problemParams({ params: {} })).toBeUndefined();
    expect('params' in toApiError('x', 409, undefined, problemParams({ params: {} }))).toBe(false);
  });
});

describe('W3-B M1 — generated types drift gate (PATCH + directory search)', () => {
  it('declares the update_project operation and its request schema', () => {
    const source = readFileSync(PLATFORM_TYPES_PATH, 'utf8');
    expect(source).toContain('update_project');
    expect(source).toContain('UpdateProjectRequest');
  });

  it('declares the directory `q` query param alongside limit/cursor', () => {
    const source = readFileSync(PLATFORM_TYPES_PATH, 'utf8');
    expect(source).toContain('list_projects');
    expect(source).toMatch(/q\?:\s*string/u);
  });

  it('declares the RFC 9457 `params` extension member on ProblemDetails', () => {
    const source = readFileSync(PLATFORM_TYPES_PATH, 'utf8');
    // ApiError.params derives from this schema — if codegen drops it, the FE type
    // silently becomes `never`/`any` instead of failing loudly here.
    expect(source).toContain('ProblemDetails');
    expect(source).toMatch(/params\?:/u);
  });
});
