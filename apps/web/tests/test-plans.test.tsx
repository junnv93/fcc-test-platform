import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { queryKeys } from '@/api/query-config';
import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import { setLocale, t } from '@/i18n';
import { isPublishableDraft, TestPlansRoute } from '@/routes/test-plans';

import { headlessOk, headlessProblem, problemDetails } from './helpers/headless-contract';
import {
  headlessRequest,
  headlessRequests,
  spyHeadlessTransport,
} from './helpers/headless-transport';
import { componentSchema, requestBodyViolations } from './helpers/request-body-contract';
import { TEST_PLAN_GENERATION_LIMITS } from './helpers/test-plan-generation-limits';

import type { HeadlessEnvelope, HeadlessPath } from './helpers/headless-contract';
import type { HeadlessOkBody } from './helpers/headless-contract';
import type { ReactElement } from 'react';

/**
 * 멀티챔버 P6 — 시험 항목표(test-plan draft/publish) 화면 테스트.
 *
 * 화면은 headless API(draft 목록/발행)를 typed client(`headlessClient`, 여기서 GET/POST
 * mock)로 소비한다. 테스트는 RBAC 게이트(test_plan:read 읽기 / test_plan:author 발행) +
 * 프로젝트 UUID 게이트 + draft 목록 렌더 + DRAFT 만 발행 가능 + 발행 성공 배선을 검증한다.
 */

const headlessClient = spyHeadlessTransport();

const platformApi = vi.hoisted(() => ({
  fetchProjectsPage: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

const PROJECT_ID = '22222222-2222-4222-8222-222222222222';
const NEXT_PROJECT_ID = '33333333-3333-4333-8333-333333333333';

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).replace(/=+$/u, '');
  const body = btoa(JSON.stringify(payload))
    .replace(/=+$/u, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  return `${header}.${body}.sig`;
}

function authenticateAs(permissions: readonly string[]): void {
  applyTokenSet({
    accessToken: makeJwt({ sub: 'author@corp', [CLAIM_PERMISSIONS]: permissions }),
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
}

function draftSummary(
  over: Partial<DraftList['drafts'][number]> = {},
): DraftList['drafts'][number] {
  return {
    draft_id: 'draft-1',
    project_id: PROJECT_ID,
    status: 'draft',
    row_count: 12,
    created_at: '2026-06-16T00:00:00+00:00',
    updated_at: '2026-06-16T01:00:00+00:00',
    ...over,
  };
}

/** A `TestPlanDraftView` (detail GET payload) — metadata + draft rows. */
function draftDetailView(over: Partial<DraftDetail> = {}): DraftDetail {
  return {
    draft_id: 'draft-1',
    project_id: PROJECT_ID,
    status: 'draft',
    created_at: '2026-06-16T00:00:00+00:00',
    created_by: 'author@corp',
    scope_revision: 3,
    generation_metadata_json: null,
    rows: [],
    ...over,
  };
}

/** A `TestPlanDraftRowView` (one draft test item). */
function draftRowView(
  over: Partial<DraftDetail['rows'][number]> = {},
): DraftDetail['rows'][number] {
  return {
    draft_row_id: 1,
    capability_path: ['BLE', 'DTM'],
    origin: 'generated',
    antenna: 'ANT1',
    location: 'CH0',
    mode_family: '1M',
    test_type: 'PSD',
    tone: null,
    derived_kind: null,
    generation_key: null,
    scope_revision: 1,
    ...over,
  };
}

const LIST_PATH = '/headless/projects/{project_id}/test-plan/drafts';
const DETAIL_PATH = '/headless/projects/{project_id}/test-plan/drafts/{draft_id}';
// Authoring write surfaces (P8). CREATE_PATH shares LIST_PATH's URL (dispatched
// by HTTP method); the rest are the per-draft authoring endpoints.
const CREATE_PATH = '/headless/projects/{project_id}/test-plan/drafts';
const ROWS_PATH = '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/rows';
const REMOVE_ROW_PATH =
  '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/rows/{draft_row_id}';
const VALIDATE_PATH = '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/validate';
const PUBLICATIONS_PATH = '/headless/projects/{project_id}/test-plan/publications';
const GENERATION_CATALOGUE_PATH = '/headless/test-plan/generation/catalogue';
const PUBLISH_PATH = '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/publish';

type DraftList = HeadlessOkBody<'get', typeof LIST_PATH>;
type DraftDetail = HeadlessOkBody<'get', typeof DETAIL_PATH>;
type Publications = HeadlessOkBody<'get', typeof PUBLICATIONS_PATH>['publications'];
type ImportAudit = HeadlessOkBody<'post', typeof IMPORT_PATH>['audit'];
type Publication = HeadlessOkBody<'post', typeof PUBLISH_PATH>;

/** A `PublishedTestPlanView` — the contract requires `project_id`, `status` and
 *  `rows` alongside the two ids the older fixtures carried. */
function publication(over: Partial<Publication> = {}): Publication {
  return {
    plan_id: 'plan-1',
    draft_id: 'draft-1',
    project_id: PROJECT_ID,
    status: 'published',
    rows: [],
    ...over,
  };
}

const GENERATION_CATALOGUE: HeadlessOkBody<'get', typeof GENERATION_CATALOGUE_PATH> = {
  catalogues: {
    BT: {
      technology: 'BT',
      stages: [],
      axes: [
        { name: 'packets', values: ['DH5'] },
        { name: 'sub_families', values: ['BR'] },
        { name: 'modes', values: ['SISO'] },
        { name: 'test_types', values: ['Pk power'] },
        { name: 'antennas', values: ['ANT1'] },
      ],
      bands_per_subfamily: { BR: ['2.4G'] },
      revision: 'catalogue:bt',
      sha256: 'b'.repeat(64),
      limits: TEST_PLAN_GENERATION_LIMITS,
    },
  },
};

/** Route the GET mock by endpoint: the list path vs the per-draft detail path. */
function routeGet(opts: {
  drafts?: DraftList['drafts'];
  detail?: DraftDetail;
  publications?: Publications;
}): void {
  headlessClient.routes({
    [PUBLICATIONS_PATH]: {
      get: () => headlessOk('get', PUBLICATIONS_PATH, { publications: opts.publications ?? [] }),
    },
    // The generator entry point reads the current provider-neutral catalogue.
    [GENERATION_CATALOGUE_PATH]: {
      get: () => headlessOk('get', GENERATION_CATALOGUE_PATH, GENERATION_CATALOGUE),
    },
    [LIST_PATH]: {
      get: () => headlessOk('get', LIST_PATH, { drafts: opts.drafts ?? [], next_cursor: null }),
    },
    [DETAIL_PATH]: {
      get: () =>
        opts.detail === undefined
          ? headlessProblem('get', DETAIL_PATH, 404, problemDetails(404, 'DRAFT_NOT_FOUND'))
          : headlessOk('get', DETAIL_PATH, opts.detail),
    },
  });
}

/**
 * Count GET calls dispatched to a given endpoint.
 *
 * `path` is `keyof paths` rather than `string`: a retired or mistyped endpoint
 * used to count zero calls forever and read as "the screen correctly did not
 * fetch this", which is the quietest way for a polling assertion to stop
 * asserting.
 */
function getCallCount(path: HeadlessPath<'get'>): number {
  return headlessRequests(headlessClient.GET, 'get', path).length;
}

/** Count POST calls dispatched to a given endpoint — see {@link getCallCount}. */
function postCallCount(path: HeadlessPath<'post'>): number {
  return headlessRequests(headlessClient.POST, 'post', path).length;
}

/**
 * Route the POST mock by endpoint so a single test can wire create / add-row /
 * validate / publish independently.
 */
function routePost(opts: {
  create?: HeadlessOkBody<'post', typeof CREATE_PATH>;
  addRow?: HeadlessOkBody<'post', typeof ROWS_PATH>;
  validate?: HeadlessOkBody<'post', typeof VALIDATE_PATH>;
  publish?: HeadlessOkBody<'post', typeof PUBLISH_PATH>;
}): void {
  headlessClient.routes({
    [ROWS_PATH]: {
      post: () => headlessOk('post', ROWS_PATH, opts.addRow ?? draftRowView({ draft_row_id: 99 })),
    },
    [VALIDATE_PATH]: {
      post: () =>
        headlessOk(
          'post',
          VALIDATE_PATH,
          opts.validate ?? { draft_id: 'draft-1', error_count: 0, warning_count: 0, issues: [] },
        ),
    },
    [PUBLISH_PATH]: {
      post: () => headlessOk('post', PUBLISH_PATH, opts.publish ?? publication()),
    },
    // CREATE_PATH shares the list URL — a POST here is the create surface.
    [CREATE_PATH]: {
      post: () =>
        headlessOk('post', CREATE_PATH, opts.create ?? draftDetailView({ draft_id: 'new-draft' })),
    },
  });
}

/**
 * Default GET mock: dispatch the current generation catalogue, answer
 * everything else with the drafts list.
 *
 * A URL-blind `mockResolvedValue` cannot do this, and the workbench legitimately
 * reads more than one endpoint. Handing the catalogue call a drafts-list payload
 * does not "not matter": it is a response satisfying none of the fields that
 * surface declares, so the screen faults on data no server would send. Routing
 * by URL keeps the suite testing the route rather than the shape of its own
 * fixture — and now the *payloads* are checked against the contract too.
 */
function mockGetDefault(fallback: DraftList = { drafts: [], next_cursor: null }): void {
  routeGet({ drafts: fallback.drafts });
}

/**
 * Test-only observer of the router's live `search` string. The workbench owns
 * the `?project=/?draft=/?status=` URL state as its SSOT, so asserting the raw
 * search (not just derived context testids) proves a status-clear actually
 * rewrites the URL — preserving project + draft while removing only status.
 */
function LocationProbe(): ReactElement {
  const location = useLocation();
  return <output data-testid="test-plans-location-search">{location.search}</output>;
}

function renderTestPlans(entry: string): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <TestPlansRoute />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
}

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
  localStorage.clear();
  platformApi.fetchProjectsPage.mockReset();
  platformApi.fetchProjectsPage.mockResolvedValue({
    items: [
      {
        project_id: PROJECT_ID,
        project_code: 'SM-TEST',
        model_name: 'SM-TEST',
        manufacturer: null,
        management_number: 'M-001',
        status: 'active',
        sample_count: 0,
      },
      {
        project_id: NEXT_PROJECT_ID,
        project_code: 'SM-NEXT',
        model_name: 'SM-NEXT',
        manufacturer: null,
        management_number: 'M-002',
        status: 'active',
        sample_count: 0,
      },
    ],
    nextCursor: null,
  });
  headlessClient.GET.mockReset();
  headlessClient.POST.mockReset();
  headlessClient.PUT.mockReset();
  headlessClient.DELETE.mockReset();
  mockGetDefault();
  headlessClient.DELETE.mockResolvedValue(
    headlessOk('delete', REMOVE_ROW_PATH, { draft_id: 'draft-1', draft_row_id: 1, removed: true }),
  );
});

afterEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

describe('isPublishableDraft', () => {
  it('is true only for a DRAFT-status draft', () => {
    expect(isPublishableDraft('draft')).toBe(true);
    expect(isPublishableDraft('DRAFT')).toBe(true);
    expect(isPublishableDraft('published')).toBe(false);
    expect(isPublishableDraft('archived')).toBe(false);
  });
});

describe('TestPlansRoute', () => {
  it('denies the view without test_plan:read', () => {
    authenticateAs([]);
    renderTestPlans('/test-plans');
    expect(screen.getByTestId('auth-failure-permission_denied')).toBeInTheDocument();
  });

  it('denies an author-only token at the read-gated workbench boundary (M1/M5)', () => {
    authenticateAs(['test_plan:author']);
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    expect(screen.getByTestId('auth-failure-permission_denied')).toBeInTheDocument();
    expect(headlessClient.GET).not.toHaveBeenCalled();
  });

  it('does not query until a project is selected', async () => {
    authenticateAs(['test_plan:read']);
    renderTestPlans('/test-plans');
    await screen.findByTestId('test-plans-project-select');
    expect(screen.getByTestId('test-plans-workbench-overview')).toBeInTheDocument();
    expect(screen.getByTestId('test-plans-next-actions')).toBeInTheDocument();
    expect(headlessClient.GET).not.toHaveBeenCalled();
  });

  it('lists drafts for a valid project', async () => {
    authenticateAs(['test_plan:read']);
    mockGetDefault({
      drafts: [draftSummary({ draft_id: 'd-1', status: 'draft' })],
      next_cursor: null,
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await waitFor(() => expect(screen.getByTestId('test-plans-table')).toBeInTheDocument());
    expect(screen.getByTestId('test-plans-next-inventory')).toHaveAttribute(
      'href',
      `/inventory?project=${PROJECT_ID}`,
    );
    expect(screen.getByText('d-1')).toBeInTheDocument();
  });

  it('hides the publish control for a reader without test_plan:author', async () => {
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'draft-1', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'draft-1', status: 'draft', rows: [draftRowView({})] }),
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=draft-1`);
    await waitFor(() => expect(screen.getByTestId('test-plans-readiness')).toBeInTheDocument());
    expect(screen.queryByTestId('test-plans-publish')).not.toBeInTheDocument();
    expect(screen.getByTestId('test-plans-publish-denied')).toBeInTheDocument();
  });

  it('disables publish for a non-DRAFT draft', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'draft-1', status: 'published' })],
      detail: draftDetailView({
        draft_id: 'draft-1',
        status: 'published',
        rows: [draftRowView({})],
      }),
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=draft-1`);
    await waitFor(() => expect(screen.getByTestId('test-plans-publish')).toBeInTheDocument());
    expect(screen.getByTestId('test-plans-publish')).toBeDisabled();
  });

  it('disables publish for an empty draft (no rows) to avoid a 422', async () => {
    // chamber-and-draft Phase 4 (F3): an empty draft has no measurement meaning;
    // the backend 422s it. Gate the button on row_count > 0 so the operator never
    // hits that error (the disabled button conveys "nothing to publish yet").
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'draft-1', status: 'draft', row_count: 0 })],
      detail: draftDetailView({ draft_id: 'draft-1', status: 'draft', rows: [] }),
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=draft-1`);
    await waitFor(() => expect(screen.getByTestId('test-plans-publish')).toBeInTheDocument());
    expect(screen.getByTestId('test-plans-publish')).toBeDisabled();
    await waitFor(() => expect(screen.getByTestId('test-plans-detail-empty')).toBeInTheDocument());
    // The disabled button is explained by a hint (why publish is unavailable).
    expect(screen.getByTestId('test-plans-publish-empty-hint')).toBeInTheDocument();
  });

  it('normalizes a whitespace-padded project param so publish invalidation refetches (P1-1)', async () => {
    // A crafted URL `?project=<uuid>%20` (trailing space) must not drift the
    // query key (raw) away from the fetched/invalidated id (trimmed) — else the
    // post-publish invalidation misses and the list never refreshes. projectId
    // is normalized once at the source, so the key/fetch/invalidation all align.
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-ws', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-ws', status: 'draft', rows: [draftRowView({})] }),
    });
    headlessClient.POST.mockResolvedValue(
      headlessOk('post', PUBLISH_PATH, publication({ plan_id: 'plan-ws', draft_id: 'd-ws' })),
    );
    renderTestPlans(`/test-plans?project=${PROJECT_ID}%20&draft=d-ws`);
    await waitFor(() => expect(screen.getByTestId('test-plans-publish')).toBeInTheDocument());
    // The fetch used the trimmed UUID (not the padded raw value).
    expect(headlessClient.GET).toHaveBeenCalledWith(
      '/headless/projects/{project_id}/test-plan/drafts',
      { params: { path: { project_id: PROJECT_ID } } },
    );
    const getCallsBeforePublish = headlessClient.GET.mock.calls.length;
    await userEvent.click(screen.getByTestId('test-plans-publish'));
    await waitFor(() =>
      expect(screen.getByTestId('test-plans-publish-success')).toBeInTheDocument(),
    );
    // Invalidation hits the same (trimmed) key → the list refetches.
    await waitFor(() =>
      expect(headlessClient.GET.mock.calls.length).toBeGreaterThan(getCallsBeforePublish),
    );
  });

  it('publishes a DRAFT and surfaces the published plan_id', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-9', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-9', status: 'draft', rows: [draftRowView({})] }),
    });
    headlessClient.POST.mockResolvedValue(
      headlessOk('post', PUBLISH_PATH, publication({ plan_id: 'plan-xyz', draft_id: 'd-9' })),
    );
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-9`);
    await waitFor(() => expect(screen.getByTestId('test-plans-publish')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('test-plans-publish'));
    await waitFor(() =>
      expect(headlessClient.POST).toHaveBeenCalledWith(
        '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/publish',
        { params: { path: { project_id: PROJECT_ID, draft_id: 'd-9' } } },
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId('test-plans-publish-success')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('test-plans-publish-success')).toHaveTextContent('plan-xyz');
  });

  it('invalidates the project publications list on publish success (A1 producer path)', async () => {
    // The chamber measurement starter sources its plan suggestions from the G2
    // server publications read (the SSOT — no browser-local registry). This
    // seals the PRODUCER end: publishing a draft must invalidate the project's
    // publications query so the freshly published plan is visible without a
    // manual reload, on this browser and any other.
    const invalidateSpy = vi.spyOn(QueryClient.prototype, 'invalidateQueries');
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-reg', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-reg', status: 'draft', rows: [draftRowView({})] }),
    });
    headlessClient.POST.mockResolvedValue(
      headlessOk(
        'post',
        PUBLISH_PATH,
        publication({
          plan_id: 'plan-reg-1',
          draft_id: 'd-reg',
          published_at: '2026-06-16T09:00:00+00:00',
        }),
      ),
    );
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-reg`);
    await waitFor(() => expect(screen.getByTestId('test-plans-publish')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('test-plans-publish'));
    await waitFor(() =>
      expect(screen.getByTestId('test-plans-publish-success')).toBeInTheDocument(),
    );
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: queryKeys.testPlans.publications(PROJECT_ID),
    });
    invalidateSpy.mockRestore();
  });

  it('consumes the typed detail GET when a draft is selected (MUST #4)', async () => {
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-1', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-1', rows: [draftRowView({})] }),
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await waitFor(() => expect(screen.getByTestId('test-plans-detail-button')).toBeInTheDocument());
    // No detail fetch until the user selects a draft.
    expect(getCallCount(DETAIL_PATH)).toBe(0);
    await userEvent.click(screen.getByTestId('test-plans-detail-button'));
    await waitFor(() => expect(screen.getByTestId('test-plans-detail-table')).toBeInTheDocument());
    // The detail used the typed generated client with the selected draft id.
    expect(headlessClient.GET).toHaveBeenCalledWith(DETAIL_PATH, {
      params: { path: { project_id: PROJECT_ID, draft_id: 'd-1' } },
    });
  });

  it('restores selected draft detail from the draft query param (UX Slice 1)', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-1', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-1', rows: [draftRowView({})] }),
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-1`);

    await waitFor(() => expect(screen.getByTestId('test-plans-detail-table')).toBeInTheDocument());
    expect(screen.getByTestId('test-plans-workbench')).toHaveAttribute(
      'data-has-selection',
      'true',
    );
    expect(screen.getByTestId('test-plans-readiness')).toBeInTheDocument();
    expect(screen.getByTestId('test-plans-readiness-next')).toHaveTextContent('다음 작업');
    expect(screen.getByTestId('test-plans-readiness-next')).toHaveTextContent(
      '초안을 검증한 뒤 차단 이슈가 없으면 발행하세요.',
    );
    expect(screen.getByTestId('test-plans-context-project')).toHaveTextContent(PROJECT_ID);
    expect(screen.getByTestId('test-plans-context-draft')).toHaveTextContent('d-1');
    expect(headlessClient.GET).toHaveBeenCalledWith(DETAIL_PATH, {
      params: { path: { project_id: PROJECT_ID, draft_id: 'd-1' } },
    });
  });

  it('clears the selected draft query state when the project changes (UX Slice 1)', async () => {
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-1', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-1', rows: [draftRowView({})] }),
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-1`);
    await waitFor(() =>
      expect(screen.getByTestId('test-plans-context-draft')).toHaveTextContent('d-1'),
    );

    await userEvent.selectOptions(screen.getByTestId('test-plans-project-select'), NEXT_PROJECT_ID);

    await waitFor(() =>
      expect(screen.getByTestId('test-plans-context-project')).toHaveTextContent(NEXT_PROJECT_ID),
    );
    expect(screen.getByTestId('test-plans-context-draft-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('test-plans-detail')).not.toBeInTheDocument();
    expect(headlessClient.GET).toHaveBeenCalledWith(LIST_PATH, {
      params: { path: { project_id: NEXT_PROJECT_ID } },
    });
  });

  it('restores the drafts-list status filter from the status query param (UX Slice 2)', async () => {
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [
        draftSummary({ draft_id: 'd-draft', status: 'draft' }),
        draftSummary({ draft_id: 'd-pub', status: 'published' }),
        draftSummary({ draft_id: 'd-arch', status: 'archived' }),
      ],
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&status=published`);

    // Only the published row survives the filter; the control reflects the URL.
    await waitFor(() => expect(screen.getAllByTestId('test-plans-row')).toHaveLength(1));
    expect(screen.getByTestId('test-plans-status-filter')).toHaveValue('published');
    expect(screen.getByTestId('test-plans-status')).toHaveTextContent('발행됨');
  });

  it('changing the status filter updates the view and preserves project/draft (Slice 1 intact)', async () => {
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [
        draftSummary({ draft_id: 'd-draft', status: 'draft' }),
        draftSummary({ draft_id: 'd-pub', status: 'published' }),
      ],
      detail: draftDetailView({ draft_id: 'd-draft', status: 'draft', rows: [draftRowView({})] }),
    });
    // Start with a project + selected draft (Slice 1 state) and no filter.
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-draft`);
    await waitFor(() => expect(screen.getAllByTestId('test-plans-row')).toHaveLength(2));

    await userEvent.selectOptions(screen.getByTestId('test-plans-status-filter'), 'published');

    // The filter now hides the draft-status row…
    await waitFor(() => expect(screen.getAllByTestId('test-plans-row')).toHaveLength(1));
    expect(screen.getByTestId('test-plans-status-filter')).toHaveValue('published');
    // …and Slice 1 state is untouched: project + selected draft both survive.
    expect(screen.getByTestId('test-plans-context-project')).toHaveTextContent(PROJECT_ID);
    expect(screen.getByTestId('test-plans-context-draft')).toHaveTextContent('d-draft');
  });

  it('degrades an unknown status param to "all" (no rows hidden) (UX Slice 2)', async () => {
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [
        draftSummary({ draft_id: 'd-draft', status: 'draft' }),
        draftSummary({ draft_id: 'd-pub', status: 'published' }),
      ],
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&status=bogus`);

    // A crafted/stale value must not hide every row — it falls back to "all".
    await waitFor(() => expect(screen.getAllByTestId('test-plans-row')).toHaveLength(2));
    expect(screen.getByTestId('test-plans-status-filter')).toHaveValue('');
  });

  it('shows a filter-empty note when no drafts match the selected status (UX Slice 2)', async () => {
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-draft', status: 'draft' })],
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&status=archived`);

    // Drafts exist, but none are archived → a filter-specific empty note (NOT
    // the "no drafts at all" empty state, and NOT the drafts table).
    expect(await screen.findByTestId('test-plans-filter-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('test-plans-table')).not.toBeInTheDocument();
    expect(screen.queryByTestId('test-plans-empty')).not.toBeInTheDocument();
    // The filter control is still present so the operator can clear it.
    expect(screen.getByTestId('test-plans-status-filter')).toHaveValue('archived');
  });

  it('surfaces an explicit notice when the open draft is hidden by the status filter (UX Slice 2)', async () => {
    // Policy: a draft deep-linked via `?draft=` keeps its detail panel open even
    // when a `?status=` filter excludes its row. Rather than silently leaving an
    // open panel with no matching list row (reads as a bug), the workbench shows
    // an explicit notice — the detail still renders below it.
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [
        draftSummary({ draft_id: 'd-draft', status: 'draft' }),
        draftSummary({ draft_id: 'd-pub', status: 'published' }),
      ],
      detail: draftDetailView({ draft_id: 'd-draft', status: 'draft', rows: [draftRowView({})] }),
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-draft&status=published`);

    // The selected draft (draft-status) is hidden by the published filter…
    await waitFor(() =>
      expect(screen.getByTestId('test-plans-hidden-selection')).toBeInTheDocument(),
    );
    // …but its detail panel stays open (the deep link is preserved).
    await waitFor(() => expect(screen.getByTestId('test-plans-detail-table')).toBeInTheDocument());
    // Only the published row survives the filter in the list.
    expect(screen.getAllByTestId('test-plans-row')).toHaveLength(1);
  });

  it('reveals the hidden draft by clearing the filter, preserving project + draft (UX Slice 2)', async () => {
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [
        draftSummary({ draft_id: 'd-draft', status: 'draft' }),
        draftSummary({ draft_id: 'd-pub', status: 'published' }),
      ],
      detail: draftDetailView({ draft_id: 'd-draft', status: 'draft', rows: [draftRowView({})] }),
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-draft&status=published`);
    await waitFor(() =>
      expect(screen.getByTestId('test-plans-hidden-selection')).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByTestId('test-plans-hidden-selection-clear'));

    // Filter cleared → the notice is gone and the draft row is visible again.
    await waitFor(() =>
      expect(screen.queryByTestId('test-plans-hidden-selection')).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId('test-plans-status-filter')).toHaveValue('');
    expect(screen.getAllByTestId('test-plans-row')).toHaveLength(2);
    // Project + selected draft URL state both survive the filter clear.
    expect(screen.getByTestId('test-plans-context-project')).toHaveTextContent(PROJECT_ID);
    expect(screen.getByTestId('test-plans-context-draft')).toHaveTextContent('d-draft');
    // …and the *actual* router search confirms only `status` was removed —
    // `project` + `draft` remain bookmarkable (URL state is the SSOT, not just
    // the derived context testids above).
    const search = new URLSearchParams(
      screen.getByTestId('test-plans-location-search').textContent ?? '',
    );
    expect(search.get('project')).toBe(PROJECT_ID);
    expect(search.get('draft')).toBe('d-draft');
    expect(search.has('status')).toBe(false);
  });

  it('shows no hidden-selection notice when the open draft still matches the filter (UX Slice 2)', async () => {
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [
        draftSummary({ draft_id: 'd-draft', status: 'draft' }),
        draftSummary({ draft_id: 'd-pub', status: 'published' }),
      ],
      detail: draftDetailView({ draft_id: 'd-pub', status: 'published', rows: [draftRowView({})] }),
    });
    // The selected draft (d-pub) matches the published filter → still listed.
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-pub&status=published`);
    await waitFor(() => expect(screen.getByTestId('test-plans-detail-table')).toBeInTheDocument());
    expect(screen.getAllByTestId('test-plans-row')).toHaveLength(1);
    expect(screen.queryByTestId('test-plans-hidden-selection')).not.toBeInTheDocument();
  });

  it('renders the selected draft detail metadata and rows', async () => {
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-1', status: 'draft' })],
      detail: draftDetailView({
        draft_id: 'd-1',
        created_by: 'alice@corp',
        rows: [
          draftRowView({ draft_row_id: 7, test_type: 'OBW', capability_path: ['BLE', 'DTM'] }),
        ],
      }),
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await waitFor(() => expect(screen.getByTestId('test-plans-detail-button')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('test-plans-detail-button'));
    await waitFor(() => expect(screen.getByTestId('test-plans-detail-table')).toBeInTheDocument());
    // Metadata (row count derived from the rows array) + provenance render.
    expect(screen.getByTestId('test-plans-detail-row-count')).toHaveTextContent('1');
    expect(screen.getByText('alice@corp')).toBeInTheDocument();
    // Row cells: capability path joined + test type.
    expect(screen.getByText('BLE / DTM')).toBeInTheDocument();
    expect(screen.getByText('OBW')).toBeInTheDocument();
  });

  it('shows the empty state when the selected draft has no rows', async () => {
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-1', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-1', rows: [] }),
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await waitFor(() => expect(screen.getByTestId('test-plans-detail-button')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('test-plans-detail-button'));
    await waitFor(() => expect(screen.getByTestId('test-plans-detail-empty')).toBeInTheDocument());
    expect(screen.queryByTestId('test-plans-detail-table')).not.toBeInTheDocument();
  });

  it('renders a localized fallback label for an unknown draft status instead of a raw i18n key', async () => {
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-uk', status: 'future-lifecycle' })],
      detail: draftDetailView({
        draft_id: 'd-uk',
        status: 'future-lifecycle',
        rows: [draftRowView({})],
      }),
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-uk`);
    await waitFor(() => expect(screen.getByTestId('test-plans-detail-table')).toBeInTheDocument());
    const listBadge = screen.getByTestId('test-plans-status');
    const detailBadge = screen.getByTestId('test-plans-detail-status');
    expect(listBadge).toHaveAttribute('data-status', 'stale');
    expect(detailBadge).toHaveAttribute('data-status', 'stale');
    expect(listBadge).toHaveTextContent('future-lifecycle');
    expect(detailBadge).toHaveTextContent('future-lifecycle');
    expect(listBadge).not.toHaveTextContent('routes.testPlans.status.future-lifecycle');
    expect(detailBadge).not.toHaveTextContent('routes.testPlans.status.future-lifecycle');
  });

  it('refetches BOTH the list and the open detail after publish (MUST #4 regression)', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-9', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-9', rows: [draftRowView({})] }),
    });
    headlessClient.POST.mockResolvedValue(
      headlessOk('post', PUBLISH_PATH, publication({ plan_id: 'plan-xyz', draft_id: 'd-9' })),
    );
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await waitFor(() => expect(screen.getByTestId('test-plans-detail-button')).toBeInTheDocument());
    // Open the detail so its query is subscribed (and thus refetchable).
    await userEvent.click(screen.getByTestId('test-plans-detail-button'));
    await waitFor(() => expect(screen.getByTestId('test-plans-detail-table')).toBeInTheDocument());

    const listBefore = getCallCount(LIST_PATH);
    const detailBefore = getCallCount(DETAIL_PATH);

    await userEvent.click(screen.getByTestId('test-plans-publish'));
    await waitFor(() =>
      expect(screen.getByTestId('test-plans-publish-success')).toBeInTheDocument(),
    );
    // Publish invalidates the list key AND the same detail key — both refetch.
    await waitFor(() => expect(getCallCount(LIST_PATH)).toBeGreaterThan(listBefore));
    await waitFor(() => expect(getCallCount(DETAIL_PATH)).toBeGreaterThan(detailBefore));
  });

  // ── P8 authoring: create / add-row / remove-row / validate ────────────────

  it('hides the create control for a reader without test_plan:author (M1/M5)', async () => {
    authenticateAs(['test_plan:read']);
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await waitFor(() => expect(screen.getByTestId('test-plans-empty')).toBeInTheDocument());
    expect(screen.queryByTestId('test-plans-create')).not.toBeInTheDocument();
  });

  it('creates a draft and auto-selects it (M1)', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [],
      detail: draftDetailView({ draft_id: 'new-draft', status: 'draft', rows: [] }),
    });
    routePost({ create: draftDetailView({ draft_id: 'new-draft' }) });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await waitFor(() => expect(screen.getByTestId('test-plans-create')).toBeInTheDocument());
    const listBefore = getCallCount(LIST_PATH);
    await userEvent.click(screen.getByTestId('test-plans-create'));
    // POST to the create surface with only the principal subject. The manual
    // authoring contract has no generation payload overload.
    await waitFor(() =>
      expect(headlessClient.POST).toHaveBeenCalledWith(CREATE_PATH, {
        params: { path: { project_id: PROJECT_ID } },
        body: { created_by: 'author@corp' },
      }),
    );
    // Contract touchpoint (D-2): the body the component actually produced is
    // checked against the *generated* artifact, not against a second hand-written
    // expectation. Without this, "what the FE sends" and "what the server
    // accepts" are asserted by nobody in the same place.
    //
    // ⚠️ **Two axes, and this line is where they meet.** `headlessRequests`
    // answers *"does the compiler agree the client built this request"*;
    // `requestBodyViolations` answers *"would the server accept this body"*.
    // The first cannot see a body that is schema-valid but semantically wrong,
    // and the second cannot see a member the client never sent. Neither
    // replaces the other, which is why the typed accessor feeds the artifact
    // check rather than displacing it.
    //
    // No non-null assertion: an absent call leaves `body` undefined, which the
    // checker reports as a violation — the test still fails, and it fails saying
    // what was wrong instead of throwing on a `!`.
    const createBody = headlessRequests(headlessClient.POST, 'post', CREATE_PATH)[0]?.body;
    expect(
      requestBodyViolations(
        createBody,
        componentSchema('headless-api.openapi.json', 'CreateTestPlanDraftRequest'),
      ),
    ).toEqual([]);
    // The drafts list invalidates so the new draft appears in the list.
    await waitFor(() => expect(getCallCount(LIST_PATH)).toBeGreaterThan(listBefore));
    // Auto-select: the new draft's detail panel opens (detail GET fires).
    await waitFor(() =>
      expect(headlessClient.GET).toHaveBeenCalledWith(DETAIL_PATH, {
        params: { path: { project_id: PROJECT_ID, draft_id: 'new-draft' } },
      }),
    );
  });

  it('adds a manual row to an editable draft and refetches detail + list (M2)', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-1', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-1', status: 'draft', rows: [] }),
    });
    routePost({ addRow: draftRowView({ draft_row_id: 42 }) });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await userEvent.click(await screen.findByTestId('test-plans-detail-button'));
    await waitFor(() => expect(screen.getByTestId('test-plans-add-row-form')).toBeInTheDocument());
    // Submit disabled until the capability path parses non-empty.
    expect(screen.getByTestId('test-plans-add-row-submit')).toBeDisabled();
    await userEvent.type(screen.getByTestId('test-plans-add-row-path'), 'BLE / DTM / 1M');
    await userEvent.type(screen.getByTestId('test-plans-add-row-test-type'), 'PSD');
    expect(screen.getByTestId('test-plans-add-row-submit')).toBeEnabled();
    const detailBefore = getCallCount(DETAIL_PATH);
    const listBefore = getCallCount(LIST_PATH);
    await userEvent.click(screen.getByTestId('test-plans-add-row-submit'));
    // POST the parsed path + the typed facet; blank facets are sent as null.
    await waitFor(() =>
      expect(headlessClient.POST).toHaveBeenCalledWith(ROWS_PATH, {
        params: { path: { project_id: PROJECT_ID, draft_id: 'd-1' } },
        body: {
          capability_path: ['BLE', 'DTM', '1M'],
          test_type: 'PSD',
          mode_family: null,
          antenna: null,
          tone: null,
          location: null,
        },
      }),
    );
    await waitFor(() => expect(getCallCount(DETAIL_PATH)).toBeGreaterThan(detailBefore));
    await waitFor(() => expect(getCallCount(LIST_PATH)).toBeGreaterThan(listBefore));
  });

  it('shows the added row immediately while the add request is still pending', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-opt-add', status: 'draft', row_count: 0 })],
      detail: draftDetailView({ draft_id: 'd-opt-add', status: 'draft', rows: [] }),
    });
    headlessClient.routes({ [ROWS_PATH]: { post: () => new Promise<never>(() => undefined) } });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-opt-add`);
    await waitFor(() => expect(screen.getByTestId('test-plans-add-row-form')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('test-plans-add-row-path'), 'BLE / DTM / 1M');
    await userEvent.type(screen.getByTestId('test-plans-add-row-test-type'), 'PSD');
    await userEvent.click(screen.getByTestId('test-plans-add-row-submit'));

    await waitFor(() => expect(screen.getByTestId('test-plans-detail-table')).toBeInTheDocument());
    expect(screen.getByText('BLE / DTM / 1M')).toBeInTheDocument();
    expect(screen.getByText('PSD')).toBeInTheDocument();
    expect(screen.getByTestId('test-plans-detail-row-count')).toHaveTextContent('1');
    const row = screen.getByTestId('test-plans-row');
    expect(within(row).getAllByRole('cell')[1]).toHaveTextContent('1');
  });

  it('offers capability_path autocomplete from the current draft rows only', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-suggest', status: 'draft' })],
      detail: draftDetailView({
        draft_id: 'd-suggest',
        status: 'draft',
        rows: [
          draftRowView({ draft_row_id: 7, capability_path: ['BLE', 'DTM'] }),
          draftRowView({ draft_row_id: 8, capability_path: ['BLE', 'DTM'] }),
          draftRowView({ draft_row_id: 9, capability_path: ['WLAN', '11ax'] }),
        ],
      }),
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-suggest`);
    const pathInput = await screen.findByTestId('test-plans-add-row-path');
    expect(pathInput).toHaveAttribute('list', 'test-plans-add-row-path-suggestions-d-suggest');
    const suggestions = await screen.findByTestId('test-plans-add-row-path-suggestions');
    const options = Array.from(suggestions.querySelectorAll('option')).map((option) =>
      option.getAttribute('value'),
    );
    expect(options).toEqual(['BLE / DTM', 'WLAN / 11ax']);
  });

  it('hides the add-row form and remove control for a non-editable (published) draft (M5)', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-1', status: 'published' })],
      detail: draftDetailView({ draft_id: 'd-1', status: 'published', rows: [draftRowView({})] }),
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await userEvent.click(await screen.findByTestId('test-plans-detail-button'));
    await waitFor(() => expect(screen.getByTestId('test-plans-detail-table')).toBeInTheDocument());
    expect(screen.queryByTestId('test-plans-add-row-form')).not.toBeInTheDocument();
    expect(screen.queryByTestId('test-plans-remove-row')).not.toBeInTheDocument();
  });

  it('removes a row from an editable draft and refetches detail + list (M3)', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-1', status: 'draft' })],
      detail: draftDetailView({
        draft_id: 'd-1',
        status: 'draft',
        rows: [draftRowView({ draft_row_id: 7 })],
      }),
    });
    routePost({});
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await userEvent.click(await screen.findByTestId('test-plans-detail-button'));
    await waitFor(() => expect(screen.getByTestId('test-plans-remove-row')).toBeInTheDocument());
    const detailBefore = getCallCount(DETAIL_PATH);
    const listBefore = getCallCount(LIST_PATH);
    await userEvent.click(screen.getByTestId('test-plans-remove-row'));
    await waitFor(() =>
      expect(headlessClient.DELETE).toHaveBeenCalledWith(REMOVE_ROW_PATH, {
        params: { path: { project_id: PROJECT_ID, draft_id: 'd-1', draft_row_id: 7 } },
      }),
    );
    await waitFor(() => expect(getCallCount(DETAIL_PATH)).toBeGreaterThan(detailBefore));
    await waitFor(() => expect(getCallCount(LIST_PATH)).toBeGreaterThan(listBefore));
  });

  it('removes the row immediately while the delete request is still pending', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-opt-del', status: 'draft', row_count: 1 })],
      detail: draftDetailView({
        draft_id: 'd-opt-del',
        status: 'draft',
        rows: [draftRowView({ draft_row_id: 7, capability_path: ['BLE', 'DTM'] })],
      }),
    });
    headlessClient.routes({
      [REMOVE_ROW_PATH]: { delete: () => new Promise<never>(() => undefined) },
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-opt-del`);
    await userEvent.click(await screen.findByTestId('test-plans-remove-row'));

    await waitFor(() => expect(screen.getByTestId('test-plans-detail-empty')).toBeInTheDocument());
    expect(screen.queryByText('BLE / DTM')).not.toBeInTheDocument();
    expect(screen.getByTestId('test-plans-detail-row-count')).toHaveTextContent('0');
    const row = screen.getByTestId('test-plans-row');
    expect(within(row).getAllByRole('cell')[1]).toHaveTextContent('0');
  });

  it('validates a draft and renders the issue summary + rows (M4)', async () => {
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-1', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-1', status: 'draft', rows: [draftRowView({})] }),
    });
    routePost({
      validate: {
        draft_id: 'd-1',
        error_count: 1,
        warning_count: 0,
        issues: [
          {
            issue_type: 'unknown_capability',
            severity: 'error',
            message: 'Capability path is not in scope',
            capability_path: ['BLE', 'DTM'],
          },
          {
            issue_type: 'unknown_capability',
            severity: 'error',
            message: 'Second invalid capability',
            capability_path: ['BLE', 'HDR'],
          },
        ],
      },
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await userEvent.click(await screen.findByTestId('test-plans-detail-button'));
    await userEvent.click(await screen.findByTestId('test-plans-validate'));
    await waitFor(() =>
      expect(headlessClient.POST).toHaveBeenCalledWith(VALIDATE_PATH, {
        params: { path: { project_id: PROJECT_ID, draft_id: 'd-1' } },
      }),
    );
    expect(await screen.findByTestId('test-plans-validate-summary')).toBeInTheDocument();
    expect(screen.getByTestId('test-plans-validate-groups')).toBeInTheDocument();
    expect(screen.getByTestId('test-plans-validate-group')).toHaveTextContent('2');
    expect(screen.getAllByTestId('test-plans-validate-issue')).toHaveLength(2);
    expect(screen.getByText('Capability path is not in scope')).toBeInTheDocument();
  });

  it('shows the clean validation message when the draft has no issues (M4)', async () => {
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-1', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-1', status: 'draft', rows: [draftRowView({})] }),
    });
    routePost({ validate: { draft_id: 'd-1', error_count: 0, warning_count: 0, issues: [] } });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await userEvent.click(await screen.findByTestId('test-plans-detail-button'));
    await userEvent.click(await screen.findByTestId('test-plans-validate'));
    expect(await screen.findByTestId('test-plans-validate-clean')).toBeInTheDocument();
    expect(screen.queryByTestId('test-plans-validate-issue')).not.toBeInTheDocument();
    expect(postCallCount(VALIDATE_PATH)).toBe(1);
  });

  it('exports draft rows as CSV and replaces rows through existing row endpoints (M5)', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-1', status: 'draft' })],
      detail: draftDetailView({
        draft_id: 'd-1',
        status: 'draft',
        rows: [
          draftRowView({ draft_row_id: 7, capability_path: ['BLE', 'DTM'], test_type: 'PSD' }),
        ],
      }),
    });
    headlessClient.PUT.mockResolvedValue(
      headlessOk('put', ROWS_PATH, { draft_id: 'd-1', replaced_count: 1, rows: [] }),
    );

    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await userEvent.click(await screen.findByTestId('test-plans-detail-button'));
    const csv = await screen.findByTestId('test-plans-bulk-csv');
    expect((csv as HTMLTextAreaElement).value).toContain('BLE / DTM,PSD');

    await userEvent.clear(csv);
    await userEvent.type(
      csv,
      'capability_path,test_type,mode_family,antenna,tone,location\nBLE / DTM / 1M,OBW,1M,ANT1,,CH0',
    );
    await userEvent.click(screen.getByTestId('test-plans-import-csv'));

    // Single atomic PUT replace-all (no DELETE-loop + POST-loop) — the full
    // desired row set in one request.
    await waitFor(() =>
      expect(headlessClient.PUT).toHaveBeenCalledWith(ROWS_PATH, {
        params: { path: { project_id: PROJECT_ID, draft_id: 'd-1' } },
        body: {
          rows: [
            {
              capability_path: ['BLE', 'DTM', '1M'],
              test_type: 'OBW',
              mode_family: '1M',
              antenna: 'ANT1',
              tone: null,
              location: 'CH0',
            },
          ],
        },
      }),
    );
    expect(headlessClient.DELETE).not.toHaveBeenCalled();
    expect(await screen.findByTestId('test-plans-bulk-success')).toBeInTheDocument();
  });

  it('shows reload conflict handling when bulk replacement races a changed draft (M5)', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-1', status: 'draft' })],
      detail: draftDetailView({
        draft_id: 'd-1',
        status: 'draft',
        rows: [draftRowView({ draft_row_id: 7 })],
      }),
    });
    headlessClient.PUT.mockResolvedValue(
      headlessProblem('put', ROWS_PATH, 409, problemDetails(409, 'DRAFT_ROW_CONFLICT')),
    );

    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await userEvent.click(await screen.findByTestId('test-plans-detail-button'));
    await userEvent.click(await screen.findByTestId('test-plans-import-csv'));

    expect(await screen.findByTestId('test-plans-bulk-error')).toHaveTextContent(
      /다시 불러오|Reload/,
    );
    await userEvent.click(screen.getByTestId('test-plans-reload-detail'));
    await waitFor(() => expect(getCallCount(DETAIL_PATH)).toBeGreaterThan(1));
  });

  it('shows a draft versus published metadata diff and states the row-level gap (M5)', async () => {
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-1', status: 'draft' })],
      detail: draftDetailView({
        draft_id: 'd-1',
        status: 'draft',
        rows: [draftRowView({ draft_row_id: 1 }), draftRowView({ draft_row_id: 2 })],
      }),
      publications: [
        {
          plan_id: 'plan-1',
          draft_id: 'd-1',
          project_id: PROJECT_ID,
          status: 'published',
          row_count: 1,
          published_at: '2026-06-16T00:00:00+00:00',
        },
      ],
    });

    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await userEvent.click(await screen.findByTestId('test-plans-detail-button'));
    expect(await screen.findByTestId('test-plans-diff-table')).toBeInTheDocument();
    expect(screen.getByTestId('test-plans-diff-draft-count')).toHaveTextContent('2');
    expect(screen.getByTestId('test-plans-diff-published-count')).toHaveTextContent('1');
    expect(screen.getByTestId('test-plans-diff-delta')).toHaveTextContent('1');
    expect(screen.getByTestId('test-plans-diff-gap')).toHaveTextContent(/row-level|행 단위/);
  });
});

const IMPORT_PATH = '/headless/projects/{project_id}/test-plan/imports';

function importAudit(over: Partial<ImportAudit> = {}): ImportAudit {
  return {
    workbook_filename: 'plan.xlsx',
    workbook_sha256: 'deadbeef',
    sheet_name: 'Test Plan',
    parser_version: '1',
    raw_row_count: 10,
    legend_skipped_count: 1,
    accepted_count: 7,
    issue_count: 1,
    excluded_count: 1,
    by_technology: [{ technology: 'BT', accepted: 7, issues: 1, excluded: 1 }],
    ...over,
  };
}

describe('TestPlansRoute — Excel import (Phase 4 L5)', () => {
  it('hides the import form for a reader without test_plan:author', async () => {
    authenticateAs(['test_plan:read']);
    mockGetDefault();
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await waitFor(() => expect(screen.getByTestId('test-plans-empty')).toBeInTheDocument());
    expect(screen.queryByTestId('test-plans-import')).not.toBeInTheDocument();
  });

  it('keeps the native File input accessible while presenting ko/en picker state', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    mockGetDefault();
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);

    const input = await screen.findByTestId('test-plans-import-file');
    const status = screen.getByTestId('test-plans-import-file-status');
    const picker = screen.getByTestId('test-plans-import-file-picker');

    expect(input).toHaveClass('sr-only');
    expect(picker).toBeVisible();
    expect(input).toHaveAccessibleName(t('routes.testPlans.sectionImport'));
    expect(input).toHaveAttribute('aria-describedby', status.id);
    expect(input).toHaveAttribute('accept', '.xlsx,.xlsm,.xls');
    expect(picker).toHaveAccessibleName(t('routes.testPlans.sectionImport'));
    expect(picker).toHaveAttribute('aria-controls', input.id);
    expect(picker).toHaveAttribute('data-file-state', 'empty');
    expect(status).toHaveTextContent(t('routes.testPlans.sectionImport'));

    setLocale('en');
    await waitFor(() => {
      expect(input).toHaveAccessibleName(t('routes.testPlans.sectionImport'));
      expect(picker).toHaveAccessibleName(t('routes.testPlans.sectionImport'));
      expect(status).toHaveTextContent(t('routes.testPlans.sectionImport'));
    });

    const file = new File(['xlsx'], 'plan.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    await userEvent.upload(input, file);

    expect((input as HTMLInputElement).files?.[0]).toBe(file);
    expect(picker).toHaveAttribute('data-file-state', 'selected');
    expect(status).toHaveTextContent(
      `${t('routes.testPlans.sectionImport')}: ${file.name} (${file.size} B)`,
    );
  });

  it('keeps the native picker keyboard-operable and locked during import, then retries the same file after an error', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    mockGetDefault();
    let resolveImport: ((value: HeadlessEnvelope<'post', typeof IMPORT_PATH>) => void) | undefined;
    headlessClient.routes({
      [IMPORT_PATH]: {
        post: () =>
          new Promise<HeadlessEnvelope<'post', typeof IMPORT_PATH>>((resolve) => {
            resolveImport = resolve;
          }),
      },
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);

    const user = userEvent.setup();
    const input = await screen.findByTestId('test-plans-import-file');
    const picker = screen.getByTestId('test-plans-import-file-picker');
    const submit = screen.getByTestId('test-plans-import-submit');
    const file = new File(['xlsx'], 'plan-retry.xlsx');
    const nativeClick = vi.spyOn(input, 'click');

    picker.focus();
    await user.keyboard('{Enter}');
    expect(nativeClick).toHaveBeenCalledTimes(1);

    await user.upload(input, file);
    await user.click(submit);
    await waitFor(() => expect(postCallCount(IMPORT_PATH)).toBe(1));
    expect(input).toBeDisabled();
    expect(picker).toBeDisabled();
    expect(submit).toBeDisabled();

    resolveImport?.(
      headlessProblem('post', IMPORT_PATH, 422, problemDetails(422, 'DRAFT_UNPROCESSABLE')),
    );
    await screen.findByTestId('test-plans-import-error');
    expect(input).not.toBeDisabled();
    expect(picker).not.toBeDisabled();
    expect(submit).not.toBeDisabled();
    expect((input as HTMLInputElement).files?.[0]).toBe(file);
    expect(screen.getByTestId('test-plans-import-file-status')).toHaveTextContent(
      `${t('routes.testPlans.sectionImport')}: ${file.name} (${file.size} B)`,
    );
    expect(screen.getByTestId('test-plans-import-error')).toHaveTextContent(
      t('errors.draftUnprocessable'),
    );
  });

  it('shows the import form for an author and uploads a workbook (multipart)', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    mockGetDefault();
    headlessClient.routes({
      [IMPORT_PATH]: {
        post: () =>
          headlessOk('post', IMPORT_PATH, {
            import_id: 'imp-1',
            draft_id: 'imported-9',
            audit: importAudit(),
            issues: [{ row_number: 5, severity: 'error', field: 'Band', message: 'unknown band' }],
            excluded: [{ row_number: 9, reason: 'channel-sounding', detail: 'CS_SYNC_1M' }],
          }),
      },
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    const fileInput = await screen.findByTestId('test-plans-import-file');
    const file = new File(['workbook-bytes'], 'plan.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    await userEvent.upload(fileInput, file);
    await userEvent.click(screen.getByTestId('test-plans-import-submit'));

    // POST hit the import endpoint with a FormData body (multipart serializer).
    await waitFor(() => expect(postCallCount(IMPORT_PATH)).toBe(1));
    // ⚠️ `bodySerializer` is part of the *declared* request too — it lives on
    // openapi-fetch's `RequestOptions`, so the derived init carries it and the
    // multipart path stays checked without a hand-written signature.
    const opts = headlessRequest(headlessClient.POST, 'post', IMPORT_PATH);
    expect(opts.params.path.project_id).toBe(PROJECT_ID);
    // ⚠️ `BodySerializer<T>` is declared `(body) => any` by openapi-fetch, so the
    // derived init cannot say what comes out. Narrowed by a real `instanceof`
    // rather than an `as FormData`: the check the test wants to make is exactly
    // the check that produces the type.
    const serialized: unknown = opts.bodySerializer?.(opts.body);
    expect(serialized).toBeInstanceOf(FormData);
    if (!(serialized instanceof FormData)) throw new Error('bodySerializer did not build FormData');
    expect(serialized.get('file')).toBeInstanceOf(File);

    // Success surfaces the new draft id + honest audit + issue/excluded tables.
    await waitFor(() => expect(screen.getByTestId('test-plans-import-status')).toBeInTheDocument());
    expect(screen.getByTestId('test-plans-import-status')).toHaveTextContent('imported-9');
    expect(screen.getByTestId('test-plans-import-issues')).toBeInTheDocument();
    expect(screen.getByTestId('test-plans-import-excluded')).toBeInTheDocument();
    // A draft WAS created → the file input is cleared (ready for the next upload),
    // contrasting the total-rejection path which preserves it.
    expect((fileInput as HTMLInputElement).files).toHaveLength(0);
  });

  it('renders an info status (not success) when no rows were importable', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    mockGetDefault();
    headlessClient.routes({
      [IMPORT_PATH]: {
        post: () =>
          headlessOk('post', IMPORT_PATH, {
            import_id: 'imp-2',
            draft_id: null,
            audit: importAudit({ accepted_count: 0, issue_count: 9, excluded_count: 1 }),
            issues: [{ row_number: 2, severity: 'error', field: 'Band', message: 'x' }],
            excluded: [],
          }),
      },
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    const file = new File(['x'], 'empty.xlsx');
    const fileInput = await screen.findByTestId('test-plans-import-file');
    await userEvent.upload(fileInput, file);
    await userEvent.click(screen.getByTestId('test-plans-import-submit'));
    await waitFor(() => expect(screen.getByTestId('test-plans-import-status')).toBeInTheDocument());
    // info tone (no draft) — issues table present, excluded absent (empty).
    expect(screen.getByTestId('test-plans-import-issues')).toBeInTheDocument();
    expect(screen.queryByTestId('test-plans-import-excluded')).not.toBeInTheDocument();
    // Total rejection (draft_id === null) PRESERVES the picked file so the
    // operator can fix + re-upload without re-picking (chamber-and-draft Phase 4).
    expect((fileInput as HTMLInputElement).files).toHaveLength(1);
  });

  it('invalidates drafts + publications on a successful import', async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, 'invalidateQueries');
    authenticateAs(['test_plan:read', 'test_plan:author']);
    mockGetDefault();
    headlessClient.routes({
      [IMPORT_PATH]: {
        post: () =>
          headlessOk('post', IMPORT_PATH, {
            import_id: 'imp-3',
            draft_id: 'd-3',
            audit: importAudit(),
            issues: [],
            excluded: [],
          }),
      },
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}`);
    await userEvent.upload(
      await screen.findByTestId('test-plans-import-file'),
      new File(['x'], 'plan.xlsx'),
    );
    await userEvent.click(screen.getByTestId('test-plans-import-submit'));
    await waitFor(() => expect(screen.getByTestId('test-plans-import-status')).toBeInTheDocument());
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: queryKeys.testPlans.drafts(PROJECT_ID),
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: queryKeys.testPlans.publications(PROJECT_ID),
    });
    invalidateSpy.mockRestore();
  });
});

/**
 * W2-C — editing safety and publish-gate honesty for the draft workbench.
 *
 * Three defects sat behind one shape: state the server owns was copied into
 * `useState` and re-seeded by an unconditional effect, so a refetch silently
 * reverted whatever the operator had typed. On the bulk CSV surface that is not
 * merely annoying — "가져오기" is a PUT replace-all, so confirming a reverted
 * textarea rewrites the ENTIRE server row set from stale content.
 */
describe('W2-C — draft editing safety and publish gate', () => {
  /** Render with a handle on the cache so a server-side change can be simulated
   *  the way it really happens: an invalidation followed by a refetch. */
  function renderWithClient(entry: string): QueryClient {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const ui: ReactElement = (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[entry]}>
          <TestPlansRoute />
        </MemoryRouter>
      </QueryClientProvider>
    );
    render(ui);
    return queryClient;
  }

  /** Swap what the detail endpoint returns, then force the open panel to reread
   *  it — i.e. "somebody else changed this draft while you were editing". */
  async function serverRowsBecome(
    queryClient: QueryClient,
    rows: DraftDetail['rows'],
  ): Promise<void> {
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-1', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-1', status: 'draft', rows }),
    });
    const before = getCallCount(DETAIL_PATH);
    await queryClient.invalidateQueries({ queryKey: queryKeys.testPlans.draft(PROJECT_ID, 'd-1') });
    await waitFor(() => expect(getCallCount(DETAIL_PATH)).toBeGreaterThan(before));
  }

  async function openDraftWithRows(rows: DraftDetail['rows']): Promise<QueryClient> {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-1', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-1', status: 'draft', rows }),
    });
    routePost({});
    const queryClient = renderWithClient(`/test-plans?project=${PROJECT_ID}&draft=d-1`);
    await screen.findByTestId('test-plans-bulk-csv');
    return queryClient;
  }

  function csvField(): HTMLTextAreaElement {
    const field = screen.getByTestId('test-plans-bulk-csv');
    if (!(field instanceof HTMLTextAreaElement)) throw new Error('bulk CSV is not a textarea');
    return field;
  }

  /** Nth match, narrowed — bare index access is `T | undefined` under the
   *  strict build config. */
  function nth(testId: string, index: number): HTMLElement {
    const found = screen.getAllByTestId(testId)[index];
    if (found === undefined) throw new Error(`no \`${testId}\` at index ${index}`);
    return found;
  }

  it('keeps an edited CSV when the draft changes on the server (S4)', async () => {
    const queryClient = await openDraftWithRows([draftRowView({ draft_row_id: 7 })]);

    const edited =
      'capability_path,test_type,mode_family,antenna,tone,location\nWLAN / 11ax,OBW,HE20,ANT2,,CH36';
    fireEvent.change(csvField(), { target: { value: edited } });

    await serverRowsBecome(queryClient, [
      draftRowView({ draft_row_id: 7 }),
      draftRowView({ draft_row_id: 8, capability_path: ['BT', 'EDR'] }),
    ]);

    // The operator's text survives — and the destructive import is HELD until
    // they decide, because it would now overwrite rows they have never seen.
    expect(csvField().value).toBe(edited);
    expect(screen.getByTestId('test-plans-bulk-unsaved')).toBeInTheDocument();
    expect(screen.getByTestId('test-plans-bulk-stale')).toBeInTheDocument();
    expect(screen.getByTestId('test-plans-import-csv')).toBeDisabled();
  });

  it('still follows the server while the CSV is untouched (S5)', async () => {
    const queryClient = await openDraftWithRows([draftRowView({ draft_row_id: 7 })]);
    expect(csvField().value).toContain('BLE / DTM');

    await serverRowsBecome(queryClient, [
      draftRowView({ draft_row_id: 9, capability_path: ['WLAN', '11be'] }),
    ]);

    // Edit safety must not cost freshness: with nothing typed there is nothing
    // to protect, so the editor tracks the server.
    await waitFor(() => expect(csvField().value).toContain('WLAN / 11be'));
    expect(screen.queryByTestId('test-plans-bulk-unsaved')).not.toBeInTheDocument();
    expect(screen.getByTestId('test-plans-import-csv')).not.toBeDisabled();
  });

  it('releases the destructive import only after the overwrite is acknowledged (S4)', async () => {
    const queryClient = await openDraftWithRows([draftRowView({ draft_row_id: 7 })]);
    fireEvent.change(csvField(), {
      target: {
        value:
          'capability_path,test_type,mode_family,antenna,tone,location\nWLAN / 11ax,OBW,HE20,ANT2,,CH36',
      },
    });
    await serverRowsBecome(queryClient, [
      draftRowView({ draft_row_id: 7 }),
      draftRowView({ draft_row_id: 8 }),
    ]);
    headlessClient.PUT.mockResolvedValue(
      headlessOk('put', ROWS_PATH, { draft_id: 'd-1', replaced_count: 1, rows: [] }),
    );

    expect(screen.getByTestId('test-plans-import-csv')).toBeDisabled();
    fireEvent.click(screen.getByTestId('test-plans-bulk-stale-acknowledge'));
    await waitFor(() => expect(screen.getByTestId('test-plans-import-csv')).not.toBeDisabled());

    fireEvent.click(screen.getByTestId('test-plans-import-csv'));
    // Atomicity is untouched: still ONE PUT replace-all, never a delete loop.
    await waitFor(() => expect(headlessClient.PUT).toHaveBeenCalledTimes(1));
    expect(headlessClient.DELETE).not.toHaveBeenCalled();
  });

  it('expires the overwrite acknowledgement when the server moves again (S4)', async () => {
    const queryClient = await openDraftWithRows([draftRowView({ draft_row_id: 7 })]);
    fireEvent.change(csvField(), { target: { value: 'capability_path\nWLAN / 11ax' } });
    await serverRowsBecome(queryClient, [
      draftRowView({ draft_row_id: 7 }),
      draftRowView({ draft_row_id: 8 }),
    ]);
    fireEvent.click(screen.getByTestId('test-plans-bulk-stale-acknowledge'));
    await waitFor(() => expect(screen.getByTestId('test-plans-import-csv')).not.toBeDisabled());

    // A third party writes again between the acknowledgement and the click. The
    // consent was given for a specific server state, so it must not carry over —
    // otherwise the guard is bypassable by simply acknowledging early.
    await serverRowsBecome(queryClient, [
      draftRowView({ draft_row_id: 7 }),
      draftRowView({ draft_row_id: 8 }),
      draftRowView({ draft_row_id: 9 }),
    ]);

    await waitFor(() => expect(screen.getByTestId('test-plans-import-csv')).toBeDisabled());
    expect(screen.getByTestId('test-plans-bulk-stale')).toBeInTheDocument();
  });

  it('drops the local edit and reloads the server rows on demand (S4)', async () => {
    const queryClient = await openDraftWithRows([draftRowView({ draft_row_id: 7 })]);
    fireEvent.change(csvField(), { target: { value: 'capability_path\nWLAN / 11ax' } });
    await serverRowsBecome(queryClient, [
      draftRowView({ draft_row_id: 9, capability_path: ['WLAN', '11be'] }),
    ]);

    fireEvent.click(screen.getByTestId('test-plans-bulk-stale-discard'));

    await waitFor(() => expect(csvField().value).toContain('WLAN / 11be'));
    expect(screen.queryByTestId('test-plans-bulk-stale')).not.toBeInTheDocument();
  });

  it('will not wipe a draft from a single click on an emptied CSV', async () => {
    await openDraftWithRows([draftRowView({ draft_row_id: 7 })]);
    headlessClient.PUT.mockResolvedValue(
      headlessOk('put', ROWS_PATH, { draft_id: 'd-1', replaced_count: 0, rows: [] }),
    );

    fireEvent.change(csvField(), { target: { value: '' } });

    // `PUT { rows: [] }` is a legal request the server honours literally — one
    // click here used to delete every row of the draft with no undo.
    expect(screen.getByTestId('test-plans-bulk-wipe')).toBeInTheDocument();
    expect(screen.getByTestId('test-plans-import-csv')).toBeDisabled();
    expect(headlessClient.PUT).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('test-plans-bulk-wipe-acknowledge'));
    await waitFor(() => expect(screen.getByTestId('test-plans-import-csv')).not.toBeDisabled());
    fireEvent.click(screen.getByTestId('test-plans-import-csv'));
    await waitFor(() =>
      expect(headlessClient.PUT).toHaveBeenCalledWith(ROWS_PATH, {
        params: { path: { project_id: PROJECT_ID, draft_id: 'd-1' } },
        body: { rows: [] },
      }),
    );
  });

  it('does not carry one draft’s unsaved CSV into another draft', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    headlessClient.routes({
      [DETAIL_PATH]: {
        get: (_path, opts) => {
          const { draft_id: id } = (opts as { params: { path: { draft_id: string } } }).params.path;
          return headlessOk(
            'get',
            DETAIL_PATH,
            draftDetailView({
              draft_id: id,
              status: 'draft',
              rows: [draftRowView({ draft_row_id: 1, capability_path: ['BLE', id] })],
            }),
          );
        },
      },
      [PUBLICATIONS_PATH]: {
        get: () => headlessOk('get', PUBLICATIONS_PATH, { publications: [] }),
      },
      [GENERATION_CATALOGUE_PATH]: {
        get: () => headlessOk('get', GENERATION_CATALOGUE_PATH, GENERATION_CATALOGUE),
      },
      [LIST_PATH]: {
        get: () =>
          headlessOk('get', LIST_PATH, {
            drafts: [
              draftSummary({ draft_id: 'd-1', status: 'draft' }),
              draftSummary({ draft_id: 'd-2', status: 'draft' }),
            ],
            next_cursor: null,
          }),
      },
    });
    routePost({});
    renderWithClient(`/test-plans?project=${PROJECT_ID}&draft=d-1`);
    await screen.findByTestId('test-plans-bulk-csv');

    fireEvent.change(csvField(), { target: { value: 'capability_path\nDRAFT-ONE-ONLY' } });
    expect(screen.getByTestId('test-plans-bulk-unsaved')).toBeInTheDocument();

    // Switch drafts through the list — the panel must be treated as different
    // data, not as the same panel with new props, or draft 1's unsaved rows
    // become a replace-all candidate for draft 2.
    const rows = screen.getAllByTestId('test-plans-detail-button');
    fireEvent.click(nth('test-plans-detail-button', rows.length - 1));

    await waitFor(() => expect(csvField().value).toContain('d-2'));
    expect(csvField().value).not.toContain('DRAFT-ONE-ONLY');
    expect(screen.queryByTestId('test-plans-bulk-unsaved')).not.toBeInTheDocument();
  });

  it('stops presenting a validation result once the draft changes (S6)', async () => {
    const queryClient = await openDraftWithRows([draftRowView({ draft_row_id: 7 })]);
    routePost({ validate: { draft_id: 'd-1', error_count: 0, warning_count: 0, issues: [] } });

    fireEvent.click(screen.getByTestId('test-plans-validate'));
    await screen.findByTestId('test-plans-validate-clean');
    expect(screen.getByTestId('test-plans-validation-state')).toHaveAttribute(
      'data-state',
      'fresh',
    );

    await serverRowsBecome(queryClient, [
      draftRowView({ draft_row_id: 7 }),
      draftRowView({ draft_row_id: 8 }),
    ]);

    // The clean verdict described rows that no longer exist. Keeping it on
    // screen as "이상 없음" is exactly how an unchecked plan gets published.
    await waitFor(() =>
      expect(screen.getByTestId('test-plans-validation-state')).toHaveAttribute(
        'data-state',
        'stale',
      ),
    );
    expect(screen.queryByTestId('test-plans-validate-clean')).not.toBeInTheDocument();
    expect(screen.getByTestId('test-plans-validate-stale')).toBeInTheDocument();
  });

  it('blocks publish on fresh errors only, and never conflates unvalidated with failed (S7)', async () => {
    const queryClient = await openDraftWithRows([draftRowView({ draft_row_id: 7 })]);

    // Unvalidated is NOT a failure: the backend does not require validation, so
    // inventing a client-side block would be a fabricated rule.
    expect(screen.getByTestId('test-plans-validation-state')).toHaveAttribute(
      'data-state',
      'unvalidated',
    );
    expect(screen.getByTestId('test-plans-publish')).not.toBeDisabled();
    expect(screen.queryByTestId('test-plans-publish-blocked-hint')).not.toBeInTheDocument();

    routePost({
      validate: {
        draft_id: 'd-1',
        error_count: 2,
        warning_count: 0,
        issues: [{ severity: 'error', issue_type: 'missing_channel', message: 'x' }],
      },
    });
    fireEvent.click(screen.getByTestId('test-plans-validate'));
    await screen.findByTestId('test-plans-validate-summary');

    // A fresh, real failure DOES block — and says why.
    expect(screen.getByTestId('test-plans-publish')).toBeDisabled();
    expect(screen.getByTestId('test-plans-publish-blocked-hint')).toBeInTheDocument();

    await serverRowsBecome(queryClient, [draftRowView({ draft_row_id: 9 })]);

    // Once the rows change, that failure is a record of the past. Continuing to
    // block on it would be judging content nobody validated.
    await waitFor(() => expect(screen.getByTestId('test-plans-publish')).not.toBeDisabled());
    expect(screen.getByTestId('test-plans-validation-state')).toHaveAttribute(
      'data-state',
      'stale',
    );
  });

  it('attributes a failed row removal to the row that failed (S10)', async () => {
    await openDraftWithRows([draftRowView({ draft_row_id: 7 }), draftRowView({ draft_row_id: 8 })]);
    headlessClient.DELETE.mockResolvedValue(
      headlessProblem('delete', REMOVE_ROW_PATH, 409, problemDetails(409, 'DRAFT_ROW_CONFLICT')),
    );

    fireEvent.click(nth('test-plans-remove-row', 1));

    const error = await screen.findByTestId('test-plans-remove-row-error');
    // Hoisting the mutation to the parent must not cost the per-row attribution
    // the old per-row hook gave for free.
    expect(error).toHaveTextContent('8');
    expect(screen.getAllByTestId('test-plans-remove-row-error')).toHaveLength(1);
  });
});

/**
 * W3-6 M2 (2026-07-31) — draft archive.
 *
 * `POST .../drafts/{draft_id}/archive` (`archive_test_plan_draft`) shipped with
 * zero frontend consumption: a draft that turned out to be wrong could only be
 * left in the list forever. These cases seal the wiring plus the three
 * properties that make it honest — the action is offered only where it can
 * succeed, its irreversibility is stated BEFORE it is reachable (the backend has
 * no unarchive operation), and its invalidation set describes what actually
 * changed.
 */
describe('TestPlansRoute draft archive (W3-6 M2)', () => {
  const ARCHIVE_PATH = '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/archive';

  /** Route POSTs so archive resolves independently of publish/validate. */
  function routeArchive(
    result: HeadlessOkBody<'post', typeof ARCHIVE_PATH> = draftDetailView({ status: 'archived' }),
  ): void {
    headlessClient.routes({
      [ARCHIVE_PATH]: { post: () => headlessOk('post', ARCHIVE_PATH, result) },
      [VALIDATE_PATH]: {
        post: () =>
          headlessOk('post', VALIDATE_PATH, {
            draft_id: 'draft-1',
            error_count: 0,
            warning_count: 0,
            issues: [],
          }),
      },
      [PUBLISH_PATH]: { post: () => headlessOk('post', PUBLISH_PATH, publication()) },
    });
  }

  it('requires an explicit confirmation before the archive request is sent (S6)', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-arc', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-arc', status: 'draft', rows: [draftRowView({})] }),
    });
    routeArchive();
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-arc`);
    await waitFor(() => expect(screen.getByTestId('test-plans-archive')).toBeInTheDocument());

    // The warning is not on screen before the operator reaches for the action…
    expect(screen.queryByTestId('test-plans-archive-confirm')).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId('test-plans-archive'));

    // …and the first click ARMS rather than commits: no request has been sent.
    expect(screen.getByTestId('test-plans-archive-confirm')).toBeInTheDocument();
    expect(postCallCount(ARCHIVE_PATH)).toBe(0);
    // The copy states the absence of a way back — the headless API exposes no
    // unarchive operation, so a vague "are you sure" would understate it.
    expect(screen.getByTestId('test-plans-archive-irreversible')).toHaveTextContent('되돌릴 수 없');

    // Backing out sends nothing and closes the warning.
    await userEvent.click(screen.getByTestId('test-plans-archive-cancel'));
    expect(screen.queryByTestId('test-plans-archive-confirm')).not.toBeInTheDocument();
    expect(postCallCount(ARCHIVE_PATH)).toBe(0);
  });

  it('archives a DRAFT on confirmation and reports success (M2)', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-arc', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-arc', status: 'draft', rows: [draftRowView({})] }),
    });
    routeArchive();
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-arc`);
    await waitFor(() => expect(screen.getByTestId('test-plans-archive')).toBeInTheDocument());

    await userEvent.click(screen.getByTestId('test-plans-archive'));
    await userEvent.click(screen.getByTestId('test-plans-archive-confirm-button'));

    await waitFor(() =>
      expect(headlessClient.POST).toHaveBeenCalledWith(ARCHIVE_PATH, {
        params: { path: { project_id: PROJECT_ID, draft_id: 'd-arc' } },
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId('test-plans-archive-success')).toBeInTheDocument(),
    );
  });

  it('invalidates drafts + draft on archive — and NOT publications (S5)', async () => {
    // Copying the publish handler wholesale would invalidate `publications`
    // too, asserting that archiving changed what is published. It did not:
    // archiving is how a draft is retired WITHOUT publishing it. The negative
    // half of this assertion is the one that catches the copy-paste.
    const invalidateSpy = vi.spyOn(QueryClient.prototype, 'invalidateQueries');
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-inv', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-inv', status: 'draft', rows: [draftRowView({})] }),
    });
    routeArchive();
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-inv`);
    await waitFor(() => expect(screen.getByTestId('test-plans-archive')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('test-plans-archive'));
    await userEvent.click(screen.getByTestId('test-plans-archive-confirm-button'));
    await waitFor(() =>
      expect(screen.getByTestId('test-plans-archive-success')).toBeInTheDocument(),
    );

    // Both reads that now describe a different draft, through the SAME factory
    // the reads use (so the key cannot drift).
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: queryKeys.testPlans.drafts(PROJECT_ID),
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: queryKeys.testPlans.draft(PROJECT_ID, 'd-inv'),
    });
    expect(invalidateSpy).not.toHaveBeenCalledWith({
      queryKey: queryKeys.testPlans.publications(PROJECT_ID),
    });
    invalidateSpy.mockRestore();
  });

  it('does not offer archive for a published or already-archived draft (S6)', async () => {
    // Both would 409. A control whose only possible outcome is a server
    // rejection is a fabricated next action.
    for (const status of ['published', 'archived']) {
      authenticateAs(['test_plan:read', 'test_plan:author']);
      routeGet({
        drafts: [draftSummary({ draft_id: 'd-term', status })],
        detail: draftDetailView({ draft_id: 'd-term', status, rows: [draftRowView({})] }),
      });
      routeArchive();
      const view = render(
        <QueryClientProvider
          client={
            new QueryClient({
              defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
            })
          }
        >
          <MemoryRouter initialEntries={[`/test-plans?project=${PROJECT_ID}&draft=d-term`]}>
            <TestPlansRoute />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await waitFor(() => expect(screen.getByTestId('test-plans-readiness')).toBeInTheDocument());
      await waitFor(() =>
        expect(screen.getByTestId('test-plans-detail-status')).toBeInTheDocument(),
      );
      expect(screen.queryByTestId('test-plans-archive')).not.toBeInTheDocument();
      view.unmount();
    }
  });

  it('does not offer archive without test_plan:author (S6)', async () => {
    authenticateAs(['test_plan:read']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-ro', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-ro', status: 'draft', rows: [draftRowView({})] }),
    });
    routeArchive();
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-ro`);
    await waitFor(() => expect(screen.getByTestId('test-plans-readiness')).toBeInTheDocument());
    expect(screen.getByTestId('test-plans-publish-denied')).toBeInTheDocument();
    expect(screen.queryByTestId('test-plans-archive')).not.toBeInTheDocument();
  });

  it('surfaces a 409 from a concurrent transition instead of hiding it (M2)', async () => {
    // The frontend gate mirrors the DRAFT-only rule, but the backend is the
    // authority: another operator can publish between render and click. That
    // response must be readable, not swallowed.
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      drafts: [draftSummary({ draft_id: 'd-race', status: 'draft' })],
      detail: draftDetailView({ draft_id: 'd-race', status: 'draft', rows: [draftRowView({})] }),
    });
    headlessClient.routes({
      [ARCHIVE_PATH]: {
        post: () =>
          headlessProblem(
            'post',
            ARCHIVE_PATH,
            409,
            problemDetails(409, 'CONFLICT', { detail: 'conflict' }),
          ),
      },
    });
    renderTestPlans(`/test-plans?project=${PROJECT_ID}&draft=d-race`);
    await waitFor(() => expect(screen.getByTestId('test-plans-archive')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('test-plans-archive'));
    await userEvent.click(screen.getByTestId('test-plans-archive-confirm-button'));
    await waitFor(() => expect(screen.getByTestId('test-plans-archive-error')).toBeInTheDocument());
    expect(screen.getByTestId('test-plans-archive-error')).toHaveTextContent('보관할 수 없');
  });
});
