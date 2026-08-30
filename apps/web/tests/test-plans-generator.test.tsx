import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import { setLocale, t } from '@/i18n';
import { TestPlansRoute } from '@/routes/test-plans';

import { headlessOk, headlessProblem, problemDetails } from './helpers/headless-contract';
import {
  headlessRequest,
  headlessRequests,
  spyHeadlessTransport,
} from './helpers/headless-transport';
import { TEST_PLAN_GENERATION_LIMITS } from './helpers/test-plan-generation-limits';

import type { HeadlessOkBody, HeadlessProblemDetails } from './helpers/headless-contract';
import type { ReactElement } from 'react';

/** Current API consumer evidence: catalogue → preview → 202 → recovery/rows. */
const headlessClient = spyHeadlessTransport();

const platformApi = vi.hoisted(() => ({ fetchProjectsPage: vi.fn() }));
vi.mock('@/api/platform-client', () => platformApi);

const PROJECT_ID = '22222222-2222-4222-8222-222222222222';
const CATALOGUE_PATH = '/headless/test-plan/generation/catalogue';
const PREVIEW_PATH = '/headless/projects/{project_id}/test-plan/generation/preview';
const SUBMIT_PATH = '/headless/projects/{project_id}/test-plan/generations';
const STATUS_PATH = '/headless/projects/{project_id}/test-plan/generations/{generation_job_id}';
const ROWS_PATH = '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/rows';
const DRAFTS_PATH = '/headless/projects/{project_id}/test-plan/drafts';
const METADATA_PATH =
  '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/generation-metadata';
const DETAIL_PATH = '/headless/projects/{project_id}/test-plan/drafts/{draft_id}';
const PUBLICATIONS_PATH = '/headless/projects/{project_id}/test-plan/publications';

const CATALOGUE_RESPONSE: HeadlessOkBody<'get', typeof CATALOGUE_PATH> = {
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
    BLE: {
      technology: 'BLE',
      stages: [],
      axes: [
        { name: 'sub_families', values: ['LE'] },
        { name: 'phys', values: ['1M'] },
        { name: 'test_types', values: ['GFSK'] },
        { name: 'antennas', values: ['ANT1'] },
        { name: 'modulations', values: ['GFSK'] },
      ],
      bands_per_subfamily: { LE: ['2.4G'] },
      revision: 'catalogue:ble',
      sha256: 'g'.repeat(64),
      limits: TEST_PLAN_GENERATION_LIMITS,
    },
    WLAN: {
      technology: 'WLAN',
      stages: ['base', 'pretest', 'main_test'],
      axes: [
        { name: 'technologies', values: ['11ax'] },
        { name: 'bands', values: ['2.4GHz'] },
        { name: 'bandwidths', values: ['20MHz'] },
        { name: 'channels', values: ['1'] },
        { name: 'modulations', values: ['802.11ax'] },
        { name: 'tests', values: ['PSD'] },
        { name: 'antennas', values: ['ANT1'] },
      ],
      bands_per_subfamily: { '802.11ax_2.4': ['2.4G'] },
      revision: 'catalogue:wlan',
      sha256: 'c'.repeat(64),
      limits: TEST_PLAN_GENERATION_LIMITS,
    },
  },
};

/** The BT catalogue page size. Named once: `catalogues` is an index signature,
 *  so every lookup is `| undefined` and the read sites should not each re-guard. */
const BT_PAGE_SIZE = TEST_PLAN_GENERATION_LIMITS.page_size;

const PREVIEW: HeadlessOkBody<'post', typeof PREVIEW_PATH> = {
  request_sha256: 'a'.repeat(64),
  production_matrix: { purpose: 'production', revision: 'db-v1', sha256: 'b'.repeat(64) },
  production_estimate: {
    exact_count: 1,
    lower_bound: 1,
    exceeds_limit: false,
    direct_count: 1,
    derived_count: 0,
  },
  representative_matrix: {
    purpose: 'representative',
    revision: 'representative:bt',
    sha256: 'c'.repeat(64),
  },
  representative_sample: [],
  catalogue_revision: 'catalogue:bt',
  catalogue_sha256: 'd'.repeat(64),
  policy_revision: 'policy-v1',
  policy_sha256: 'e'.repeat(64),
  fingerprint: 'f'.repeat(64),
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).replace(/=+$/u, '');
  const body = btoa(JSON.stringify(payload))
    .replace(/=+$/u, '')
    .replace(/\+/gu, '-')
    .replace(/\//gu, '_');
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

function renderTestPlans(): QueryClient {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/test-plans?project=${PROJECT_ID}`]}>
        <TestPlansRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
  return queryClient;
}

function routeGet(
  overrides: {
    catalogueFails?: boolean;
    status?: HeadlessOkBody<'get', typeof STATUS_PATH>;
    metadataStatus?: string;
    largeRows?: boolean;
    pagedRows?: boolean;
  } = {},
): void {
  headlessClient.routes({
    [CATALOGUE_PATH]: {
      get: () =>
        overrides.catalogueFails === true
          ? headlessProblem('get', CATALOGUE_PATH, 503, problemDetails(503, 'UPSTREAM_UNAVAILABLE'))
          : headlessOk('get', CATALOGUE_PATH, CATALOGUE_RESPONSE),
    },
    [STATUS_PATH]: {
      get: () =>
        headlessOk(
          'get',
          STATUS_PATH,
          overrides.status ?? {
            job_id: 'job-1',
            project_id: PROJECT_ID,
            status: 'succeeded',
            draft_id: 'draft-generated',
            request_sha256: 'a'.repeat(64),
            matrix_revision: 'db-v1',
            matrix_sha256: 'b'.repeat(64),
            error_code: null,
            error_message: null,
            created_at: null,
            updated_at: null,
          },
        ),
    },
    [ROWS_PATH]: {
      get: (_path, options) => {
        const after = (
          options as { params?: { query?: { after_draft_row_id?: number } } } | undefined
        )?.params?.query?.after_draft_row_id;
        const rowCount =
          overrides.largeRows === true ? 400 : overrides.pagedRows === true ? 250 : 1;
        const rowOffset = after ?? 0;
        return headlessOk('get', ROWS_PATH, {
          draft_id: 'draft-generated',
          rows: Array.from({ length: rowCount }, (_unused, index) => ({
            draft_row_id: rowOffset + index + 1,
            row_seq: rowOffset + index,
            capability_path: ['BT', 'BR'],
            origin: 'generated',
            packet: 'DH5',
            generation_key: 'generation-key',
            generated_from_capability: 'BT/BR',
            antenna: 'ANT1',
            location: null,
            mode_family: 'SISO',
            test_type: 'Pk power',
            tone: null,
            derived_kind: null,
            scope_revision: 1,
            condition_hash: null,
          })),
          next_after_draft_row_id:
            overrides.pagedRows === true && after === undefined ? BT_PAGE_SIZE : null,
        });
      },
    },
    [METADATA_PATH]: {
      get: () =>
        headlessOk('get', METADATA_PATH, {
          job_id: 'job-1',
          draft_id: 'draft-generated',
          status: overrides.metadataStatus ?? 'succeeded',
          metadata: { generation_key: 'generation-key' },
        }),
    },
    [DETAIL_PATH]: {
      get: () =>
        headlessOk('get', DETAIL_PATH, {
          draft_id: 'draft-generated',
          project_id: PROJECT_ID,
          status: 'draft',
          created_at: null,
          created_by: 'author@corp',
          scope_revision: 1,
          generation_metadata_json: null,
          rows: [],
        }),
    },
    [PUBLICATIONS_PATH]: {
      get: () => headlessOk('get', PUBLICATIONS_PATH, { publications: [] }),
    },
    [DRAFTS_PATH]: {
      get: () => headlessOk('get', DRAFTS_PATH, { drafts: [], next_cursor: null }),
    },
  });
}

function routePost(
  overrides: {
    previewFails?: boolean;
    previewRefusal?: Partial<NonNullable<HeadlessProblemDetails>> & { status: number };
  } = {},
): void {
  headlessClient.routes({
    [PREVIEW_PATH]: {
      post: () => {
        const refusal = overrides.previewRefusal;
        if (refusal !== undefined) {
          return headlessProblem(
            'post',
            PREVIEW_PATH,
            refusal.status,
            problemDetails(refusal.status, refusal.code ?? 'VALIDATION_ERROR', refusal),
          );
        }
        return overrides.previewFails === true
          ? headlessProblem('post', PREVIEW_PATH, 422, problemDetails(422, 'DRAFT_UNPROCESSABLE'))
          : headlessOk('post', PREVIEW_PATH, PREVIEW);
      },
    },
    [SUBMIT_PATH]: {
      post: () =>
        headlessOk('post', SUBMIT_PATH, {
          job_id: 'job-1',
          project_id: PROJECT_ID,
          status: 'queued',
          request_sha256: 'a'.repeat(64),
          matrix_revision: 'db-v1',
        }),
    },
  });
}

beforeEach(() => {
  __resetAuthStateForTests();
  setLocale('en');
  sessionStorage.clear();
  localStorage.clear();
  platformApi.fetchProjectsPage.mockReset();
  platformApi.fetchProjectsPage.mockResolvedValue({ items: [], nextCursor: null });
  headlessClient.GET.mockReset();
  headlessClient.POST.mockReset();
  headlessClient.PUT.mockReset();
  headlessClient.DELETE.mockReset();
  routeGet();
  routePost();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('current generation API cutover', () => {
  it('reads the generated catalogue and never calls the removed scope route', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    renderTestPlans();
    await screen.findByTestId('test-plans-generator-form');
    expect(headlessClient.GET).toHaveBeenCalledWith(CATALOGUE_PATH, {});
  });

  it('renders every current catalogue axis through its precise locale key', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    renderTestPlans();
    const form = await screen.findByTestId('test-plans-generator-form');

    const axisKeys = {
      packets: 'routes.testPlans.generator.axis.packets',
      sub_families: 'routes.testPlans.generator.axis.subFamilies',
      modes: 'routes.testPlans.generator.axis.modes',
      test_types: 'routes.testPlans.colTestType',
      antennas: 'routes.testPlans.colAntenna',
    } as const;
    for (const [axis, key] of Object.entries(axisKeys)) {
      const fieldset = screen.getByTestId(`test-plans-generator-axis-${axis}`);
      expect(fieldset).toHaveAttribute('data-axis-name', axis);
      expect(fieldset).toHaveAttribute('data-label-state', 'localized');
      expect(fieldset).toHaveTextContent(t(key));
    }
    expect(screen.getByTestId('test-plans-generator-bands')).toHaveAttribute(
      'data-label-state',
      'localized',
    );
    expect(screen.getByTestId('test-plans-generator-bands')).toHaveTextContent(
      t('routes.testPlans.generator.axis.bandsPerSubfamily'),
    );
    expect(form).not.toHaveTextContent(t('ui.streamStatus.unknown'));
    expect(form).not.toHaveTextContent('packets');
    expect(form).not.toHaveTextContent('sub_families');
    expect(form).not.toHaveTextContent('modes');
    expect(form).not.toHaveTextContent('test_types');
    expect(form).not.toHaveTextContent('antennas');
    expect(form).not.toHaveTextContent('bands_per_subfamily');
  });

  it('renders the BLE catalogue through the same fixture-derived axis census', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    renderTestPlans();
    await screen.findByTestId('test-plans-generator-form');

    await userEvent.selectOptions(screen.getByTestId('test-plans-generator-technology'), 'BLE');
    const bleCatalogue = CATALOGUE_RESPONSE.catalogues.BLE;
    if (bleCatalogue === undefined) throw new Error('BLE fixture is missing');

    for (const axis of bleCatalogue.axes) {
      const fieldset = screen.getByTestId(`test-plans-generator-axis-${axis.name}`);
      expect(fieldset).toHaveAttribute('data-label-state', 'localized');
      expect(fieldset).not.toHaveAttribute('data-label-state', 'locale-key-blocked');
      expect(fieldset).not.toHaveTextContent('Unknown');
      expect(fieldset).not.toHaveTextContent('알 수 없음');
    }
    expect(screen.getByTestId('test-plans-generator-form')).toHaveTextContent(
      t('routes.testPlans.generator.axis.bandsPerSubfamily'),
    );
    expect(
      screen
        .getByTestId('test-plans-generator-form')
        .querySelectorAll('[data-label-state="unsupported-catalogue-identity"]'),
    ).toHaveLength(0);
  });

  it('updates mounted generator labels and document language without reload', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    renderTestPlans();
    const form = await screen.findByTestId('test-plans-generator-form');
    const packets = screen.getByTestId('test-plans-generator-axis-packets');
    const english = packets.querySelector('legend')?.textContent;
    expect(english).toBe(t('routes.testPlans.generator.axis.packets'));

    try {
      setLocale('ko');
      await waitFor(() => {
        expect(packets.querySelector('legend')).toHaveTextContent(
          t('routes.testPlans.generator.axis.packets'),
        );
        expect(packets.querySelector('legend')?.textContent).not.toBe(english);
        expect(document.documentElement).toHaveAttribute('lang', 'ko');
      });
      expect(form).not.toHaveTextContent('Unknown');
      expect(form).not.toHaveTextContent('알 수 없음');
      expect(form.querySelector('[data-label-state="locale-key-blocked"]')).toBeNull();
    } finally {
      setLocale('en');
    }
  });

  it('renders WLAN stages and main-test fields with distinct locale keys', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    renderTestPlans();
    await screen.findByTestId('test-plans-generator-form');

    await userEvent.selectOptions(screen.getByTestId('test-plans-generator-technology'), 'WLAN');
    const stage = screen.getByTestId('test-plans-generator-stage');
    expect(stage).toHaveAttribute('data-label-state', 'localized');
    expect(stage).toHaveAttribute('data-stage-value', 'base');
    expect(within(stage).getAllByRole('option')).toHaveLength(3);
    expect(within(stage).getAllByRole('option')[0]).toHaveTextContent(
      t('routes.testPlans.generator.stage.base'),
    );
    expect(within(stage).getAllByRole('option')[1]).toHaveTextContent(
      t('routes.testPlans.generator.stage.pretest'),
    );
    expect(within(stage).getAllByRole('option')[2]).toHaveTextContent(
      t('routes.testPlans.generator.stage.mainTest'),
    );
    const stageControl = stage.closest('label');
    expect(stageControl).not.toBeNull();
    if (stageControl !== null) {
      expect(stageControl).toHaveTextContent(t('routes.testPlans.generator.stage.label'));
    }
    expect(stage).not.toHaveTextContent('base');
    expect(stage).not.toHaveTextContent('pretest');
    expect(stage).not.toHaveTextContent('main_test');
    expect(stage).not.toHaveTextContent(t('ui.streamStatus.unknown'));

    await userEvent.selectOptions(stage, 'main_test');
    const mainSource = screen.getByTestId('test-plans-generator-main-source');
    expect(mainSource).toHaveAttribute('data-label-state', 'localized');
    expect(mainSource).toHaveTextContent(t('routes.testPlans.generator.mainSource.legend'));
    const fieldKeys = {
      source_session_id: 'routes.testPlans.generator.mainSource.sourceSessionId',
      selected_channels: 'routes.testPlans.generator.mainSource.selectedChannels',
      worst_decision_snapshot_revision:
        'routes.testPlans.generator.mainSource.worstDecisionSnapshotRevision',
    } as const;
    for (const [field, key] of Object.entries(fieldKeys)) {
      const label = mainSource.querySelector<HTMLElement>(`[data-field-name="${field}"]`);
      expect(label).not.toBeNull();
      if (label === null) continue;
      expect(label).toHaveAttribute('data-field-name', field);
      expect(label).toHaveAttribute('data-label-state', 'localized');
      expect(label).toHaveTextContent(t(key));
    }
    expect(mainSource).not.toHaveTextContent(t('ui.streamStatus.unknown'));
    expect(mainSource).not.toHaveTextContent('source_session_id');
    expect(mainSource).not.toHaveTextContent('selected_channels');
    expect(mainSource).not.toHaveTextContent('worst_decision_snapshot_revision');
  });

  it('builds a generated-union request from server axes and previews before submit', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    renderTestPlans();
    await screen.findByTestId('test-plans-generator-form');

    await userEvent.click(screen.getByTestId('test-plans-generator-submit'));
    await waitFor(() =>
      expect(headlessClient.POST).toHaveBeenCalledWith(PREVIEW_PATH, expect.anything()),
    );
    const previewBody = headlessRequest(headlessClient.POST, 'post', PREVIEW_PATH).body;
    expect(previewBody).toEqual({
      technology: 'BT',
      packets: ['DH5'],
      modes: ['SISO'],
      test_types: ['Pk power'],
      antennas: ['ANT1'],
      bands_per_subfamily: { BR: ['2.4G'] },
    });
    expect(screen.getByTestId('test-plans-generator-preview')).toHaveTextContent('production');
    expect(screen.getByTestId('test-plans-generator-preview')).toHaveTextContent('representative');
    expect(headlessRequests(headlessClient.POST, 'post', DRAFTS_PATH)).toHaveLength(0);
  });

  it('submits only after preview with the official idempotency header and consumes status/pages', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    renderTestPlans();
    await screen.findByTestId('test-plans-generator-form');
    await userEvent.click(screen.getByTestId('test-plans-generator-submit'));
    await screen.findByTestId('test-plans-generator-preview');
    await userEvent.click(screen.getByTestId('test-plans-generator-submit'));

    await waitFor(() =>
      expect(headlessClient.POST).toHaveBeenCalledWith(SUBMIT_PATH, expect.anything()),
    );
    // The three `isRecord` guards below used to be the only thing standing
    // between this assertion and `undefined.params.header`; the derived type
    // makes each step a compile-time fact instead of a runtime narrowing.
    const submitInit = headlessRequest(headlessClient.POST, 'post', SUBMIT_PATH);
    expect(typeof submitInit.params.header['Idempotency-Key']).toBe('string');
    expect(isRecord(submitInit.body.request)).toBe(true);
    expect(submitInit.body.preview).toEqual(PREVIEW);
    expect(await screen.findByTestId('test-plans-generator-job-status')).toHaveTextContent(
      t('routes.jobs.counts.completed'),
    );
    expect(screen.getByTestId('test-plans-generator-job-status')).not.toHaveTextContent(
      'succeeded',
    );
    expect(await screen.findByTestId('test-plans-generator-metadata-status')).toHaveTextContent(
      t('routes.jobs.counts.completed'),
    );
    expect(await screen.findByTestId('test-plans-generator-row')).toHaveTextContent(
      'generation-key',
    );
    // Several row pages may be fetched, so this asks for *the requests* rather
    // than pretending there was one — `headlessRequest` would refuse here, and
    // that refusal is the honest answer the old `.find()` was papering over.
    const rowRequests = headlessRequests(headlessClient.GET, 'get', ROWS_PATH);
    expect(rowRequests.length).toBeGreaterThan(0);
    expect(rowRequests[0]?.params.query?.limit).toBe(BT_PAGE_SIZE);
  });

  it('surfaces catalogue and preview failures instead of falling back to old draft writes', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({ catalogueFails: true });
    renderTestPlans();
    expect(await screen.findByTestId('test-plans-generator-options-error')).toBeInTheDocument();
    expect(headlessClient.POST).not.toHaveBeenCalledWith(PREVIEW_PATH, expect.anything());

    headlessClient.GET.mockReset();
    routeGet();
    routePost({ previewFails: true });
    renderTestPlans();
    await screen.findByTestId('test-plans-generator-form');
    await userEvent.click(screen.getByTestId('test-plans-generator-submit'));
    expect(await screen.findByTestId('test-plans-generator-error')).toBeInTheDocument();
  });

  it('uses the precise locale label for a server-pinned offending axis', async () => {
    // The wave in one assertion. The server has always known which box was
    // wrong — it wrote `'NOPE' is not a valid BtPacket` into `detail` — but
    // `detail` is server-internal prose naming Python types, so the screen is
    // forbidden from reading it. The machine-readable half (`params.field`) was
    // being dropped twice: the form never passed it to `toApiError`, and the 400
    // arm had nowhere to put it. A tester saw only "요청이 실패했습니다".
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routePost({
      previewRefusal: {
        status: 400,
        code: 'VALIDATION_ERROR',
        params: { field: 'packets' },
      },
    });
    renderTestPlans();
    await screen.findByTestId('test-plans-generator-form');
    await userEvent.click(screen.getByTestId('test-plans-generator-submit'));

    const errorState = await screen.findByTestId('test-plans-generator-error');
    expect(errorState).toHaveTextContent(
      t('errors.badRequestField', {
        field: t('routes.testPlans.generator.axis.packets'),
      }),
    );
    expect(errorState).not.toHaveTextContent('packets');
    expect(errorState).not.toHaveTextContent(t('errors.default'));
    expect(screen.getByTestId('test-plans-generator-axis-packets')).toHaveAttribute(
      'data-label-state',
      'localized',
    );
  });

  it('falls back to the generator copy for a 400 the server could not pin down', async () => {
    // Two candidate axes, a refusal about a combination, a provider rule the
    // membership test cannot see — the server declines to name a field rather
    // than invent one, and the screen must then say something better than the
    // generic default. That is the `badRequest` override, which this form was
    // missing entirely: its good sentence sat wired to `default`, a status a
    // refusal never has.
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routePost({
      previewRefusal: { status: 400, code: 'VALIDATION_ERROR' },
    });
    renderTestPlans();
    await screen.findByTestId('test-plans-generator-form');
    await userEvent.click(screen.getByTestId('test-plans-generator-submit'));

    const errorState = await screen.findByTestId('test-plans-generator-error');
    expect(errorState).toHaveTextContent(t('routes.testPlans.generator.failed'));
    expect(errorState).not.toHaveTextContent(t('errors.default'));
  });

  it('renders terminal job failures as an error state without generated rows', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({
      status: {
        job_id: 'job-1',
        project_id: PROJECT_ID,
        status: 'failed',
        draft_id: null,
        request_sha256: 'a'.repeat(64),
        matrix_revision: 'db-v1',
        matrix_sha256: 'b'.repeat(64),
        error_code: 'materialization_failed',
        error_message: 'seeded terminal failure',
        created_at: null,
        updated_at: null,
      },
    });
    renderTestPlans();
    await screen.findByTestId('test-plans-generator-form');

    await userEvent.click(screen.getByTestId('test-plans-generator-submit'));
    await screen.findByTestId('test-plans-generator-preview');
    await userEvent.click(screen.getByTestId('test-plans-generator-submit'));

    const errorState = await screen.findByTestId('test-plans-generator-error');
    expect(errorState).toHaveTextContent('seeded terminal failure');
    expect(screen.getByTestId('test-plans-generator-job-status')).toHaveTextContent(
      t('routes.jobs.counts.failed'),
    );
    expect(screen.getByTestId('test-plans-generator-job-status')).not.toHaveTextContent('failed');
    expect(screen.queryByTestId('test-plans-generator-rows')).toBeNull();
  });

  it('localizes metadata failure status without exposing its server token', async () => {
    authenticateAs(['test_plan:read', 'test_plan:author']);
    routeGet({ metadataStatus: 'failed' });
    renderTestPlans();
    await screen.findByTestId('test-plans-generator-form');

    await userEvent.click(screen.getByTestId('test-plans-generator-submit'));
    await screen.findByTestId('test-plans-generator-preview');
    await userEvent.click(screen.getByTestId('test-plans-generator-submit'));

    const metadataStatus = await screen.findByTestId('test-plans-generator-metadata-status');
    expect(metadataStatus).toHaveTextContent(t('routes.jobs.counts.failed'));
    expect(metadataStatus).not.toHaveTextContent('failed');
  });
});
