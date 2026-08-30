import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useSearchParams } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ERROR_POLL_MAX_CONSECUTIVE_FAILURES,
  queryKeys,
  REFETCH_STRATEGIES,
} from '@/api/query-config';
import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import {
  ReportsRoute,
  canCancelReportRequest,
  describeError,
  parsePositiveId,
  reportRequestPollInterval,
} from '@/routes/reports';

import {
  getBodyRoutes,
  headlessOk,
  headlessProblem,
  problemDetails,
} from './helpers/headless-contract';
import { headlessRequest, spyHeadlessTransport } from './helpers/headless-transport';
import { tableView } from './helpers/responsive-table';

import type { HeadlessGetBodies } from './helpers/headless-contract';
import type { HeadlessRequestInit } from './helpers/headless-contract';
import type { HeadlessTransportMocks } from './helpers/headless-transport';
import type * as HeadlessClientModuleNS from '@/api/headless-client';
import type { ReactElement } from 'react';

type HeadlessClientModule = typeof HeadlessClientModuleNS;

/**
 * FE-P6 (2026-05-26) — report / artifact view tests.
 *
 * Auth via the real `auth/session.ts` SSOT; the Headless API client is mocked
 * (keyed by the openapi-fetch path template). Covers id parsing, RBAC gating,
 * queue stats, report request status + outputs missing-diagnostics (`exists`),
 * and session artifact lookup.
 */

/**
 * The per-node client the project flow routes through.
 *
 * ⚠️ It cannot go through `spyHeadlessTransport()` — that spies the *default*
 * client instance, and this one is manufactured per node base URL. But it speaks
 * the **same contract**, so its stubs are built with the same contract-derived
 * builders; leaving them hand-assembled would keep exactly the hole this wave
 * closes, one client to the side.
 */
const nodeHeadlessClient = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
})) as unknown as Pick<HeadlessTransportMocks, 'GET' | 'POST'>;
const createHeadlessClientForBaseUrl = vi.hoisted(() =>
  // Typed with the base-URL param (mirrors the real factory) so a test can
  // install a per-node implementation that dispatches on the URL.
  vi.fn((_baseUrl: string) => nodeHeadlessClient),
);
// ⚠️ **Partial** mock, and only for the per-node factory. The operation helpers
// now live in this module and close over its internal `headlessClient`, so a
// whole-module mock would substitute the helpers themselves and this route test
// would pass against a client that never runs (`diagnostics.test.tsx` names the
// same failure for the session surface). `importOriginal` keeps the real
// helpers; the default-client transport is spied below instead.
vi.mock('@/api/headless-client', async (importOriginal) => ({
  ...(await importOriginal<HeadlessClientModule>()),
  createHeadlessClientForBaseUrl,
}));

const headlessClient = spyHeadlessTransport();

const SUBMIT_REPORT_PATH = '/headless/sessions/{session_id}/reports';
const GRANT_DOWNLOAD_PATH = '/headless/reports/{request_id}/outputs/download';
const REPORT_STATUS_PATH = '/headless/reports/{request_id}';
const OUTPUTS_PATH = '/headless/reports/{request_id}/outputs';
const STATS_PATH = '/report-automation/stats';
const PREFLIGHT_PATH = '/headless/reports/preflight';
const CANCEL_REQUEST_PATH = '/report-automation/requests/{request_id}/cancel';

// P5-B: the report surface reads the deployed provider's descriptor to resolve
// an area → technologies scope. Only `fetchProviderUiDescriptor` is consumed
// (full mock — mirrors provider-descriptor.test.tsx, avoids platform-client
// module-load runtime-config coupling).
const platformApi = vi.hoisted(() => ({
  fetchProviderUiDescriptor: vi.fn(),
  fetchProjectReportSessions: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';

// A minimal descriptor carrying only the workbench_area → technologies mapping
// the report scope reads (the selector ignores every other descriptor field).
function descriptorWithMapping(mapping: Record<string, string[]>): unknown {
  return {
    provider_id: 'fcc-unlicensed-conducted',
    display_name: 'unlicensed-conducted',
    ui_version: 1,
    features: [],
    test_plan_tables: [],
    equipment: [],
    reference_tables: [],
    correction_tables: [],
    workbench_area_technologies: mapping,
  };
}

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
    accessToken: makeJwt({ sub: 'op-1', [CLAIM_PERMISSIONS]: permissions }),
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
}

/**
 * Stub GET bodies by path.
 *
 * The keys are `keyof paths` and each body is checked against **that
 * operation's** declared success shape, so a fixture that drifts from the wire
 * no longer passes silently. A path this suite does not list answers 404, which
 * several tests depend on — declared through `{ unrouted: 'not-found' }` rather
 * than left as an unstated default.
 */
function mockHeadless(handlers: HeadlessGetBodies): void {
  headlessClient.routes(getBodyRoutes(handlers), { unrouted: 'not-found' });
}

/** The two-output fixture the download tests share (one present, one missing). */
function mockDownloadableOutputs(): void {
  authenticateAs(['report_automation:read', 'headless:read']);
  mockHeadless({
    '/report-automation/stats': {
      queued: 0,
      running: 0,
      completed: 1,
      failed: 0,
      cancelled: 0,
      oldest_queued_request_id: null,
    },
    '/headless/reports/{request_id}': {
      id: 9,
      status: 'completed',
      session_id: 3,
      error_message: '',
    },
    '/headless/reports/{request_id}/outputs': [
      {
        request_id: 9,
        file_name: 'a.docx',
        relative_path: 's3/a.docx',
        byte_size: 1024,
        exists: true,
        storage_backend: 'filesystem',
      },
      {
        request_id: 9,
        file_name: 'b.docx',
        relative_path: 's3/b.docx',
        byte_size: null,
        exists: false,
        storage_backend: 'filesystem',
      },
    ],
  });
}

/** A live (far-future TTL) signed grant. */
function grantDownload(): void {
  headlessClient.POST.mockResolvedValue(
    headlessOk('post', GRANT_DOWNLOAD_PATH, {
      download_url: '/headless/reports/outputs/download?token=SIGNED',
      expires_at: '2999-01-01T00:00:00Z',
    }),
  );
}

interface SignedDownloadStub {
  readonly fetchMock: ReturnType<typeof vi.fn>;
  readonly anchorClick: ReturnType<typeof vi.fn>;
  readonly assign: ReturnType<typeof vi.fn>;
  readonly restore: () => void;
}

/**
 * Stub the browser edges `runSignedDownload` touches (M3): the raw `fetch` of
 * the signed URL, the object-URL plumbing jsdom does not implement, the anchor
 * click, and `window.location.assign` — the last one purely so a regression back
 * to top-level navigation is *observable* rather than silently passing.
 */
function stubSignedDownload(
  stream: { ok: boolean; status?: number; body?: unknown } = { ok: true },
): SignedDownloadStub {
  const fetchMock = vi.fn(() =>
    Promise.resolve({
      ok: stream.ok,
      status: stream.status ?? 200,
      json: () => Promise.resolve(stream.body),
      blob: () => Promise.resolve(new Blob(['bytes'])),
    }),
  );
  const originalFetch = globalThis.fetch;
  vi.stubGlobal('fetch', fetchMock);

  /* eslint-disable @typescript-eslint/unbound-method -- capturing the originals
     verbatim so `restore()` puts back exactly what was there; binding them would
     silently substitute a different function for the rest of the suite. */
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  const originalAnchorClick = HTMLAnchorElement.prototype.click;
  /* eslint-enable @typescript-eslint/unbound-method */
  URL.createObjectURL = vi.fn(() => 'blob:stub');
  URL.revokeObjectURL = vi.fn();

  const anchorClick = vi.fn();
  HTMLAnchorElement.prototype.click = anchorClick;

  const assign = vi.fn();
  const originalLocation = window.location;
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: Object.assign({}, originalLocation, { assign }),
  });

  return {
    fetchMock,
    anchorClick,
    assign,
    restore: () => {
      vi.stubGlobal('fetch', originalFetch);
      URL.createObjectURL = originalCreate;
      URL.revokeObjectURL = originalRevoke;
      HTMLAnchorElement.prototype.click = originalAnchorClick;
      Object.defineProperty(window, 'location', {
        configurable: true,
        value: originalLocation,
      });
    },
  };
}

function renderReports(area?: string, projectId?: string): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const params = new URLSearchParams();
  if (area !== undefined) params.set('area', area);
  if (projectId !== undefined) params.set('project', projectId);
  const query = params.toString();
  const entry = query === '' ? '/reports' : `/reports?${query}`;
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <ReportsRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
}

// Route-transition harness (P5-C): a small in-router control that flips the
// `?project=` search param WITHOUT remounting <ReportsRoute>, so a test can
// exercise a legacy → project (and back) context switch inside one live React
// Router element — the exact situation the state-leak guard must survive.
function ReportContextNav(): JSX.Element {
  const [, setSearchParams] = useSearchParams();
  return (
    <div>
      <button type="button" data-testid="nav-legacy" onClick={() => setSearchParams({})}>
        legacy
      </button>
      <button
        type="button"
        data-testid="nav-project"
        onClick={() => setSearchParams({ project: PROJECT_ID })}
      >
        project
      </button>
    </div>
  );
}

function renderReportsWithNav(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/reports']}>
        <ReportContextNav />
        <ReportsRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
}

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
  headlessClient.GET.mockReset();
  headlessClient.POST.mockReset();
  nodeHeadlessClient.GET.mockReset();
  nodeHeadlessClient.POST.mockReset();
  // Reset to the default (URL-agnostic) factory so a per-node override set inside
  // one test cannot leak into the next (mockClear alone keeps the implementation).
  createHeadlessClientForBaseUrl.mockReset();
  createHeadlessClientForBaseUrl.mockImplementation(() => nodeHeadlessClient);
  platformApi.fetchProviderUiDescriptor.mockReset();
  platformApi.fetchProjectReportSessions.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('parsePositiveId', () => {
  it('accepts positive integers (trimmed) and rejects everything else', () => {
    expect(parsePositiveId('5')).toBe(5);
    expect(parsePositiveId('  42 ')).toBe(42);
    expect(parsePositiveId('0')).toBeNull();
    expect(parsePositiveId('-1')).toBeNull();
    expect(parsePositiveId('1.5')).toBeNull();
    expect(parsePositiveId('abc')).toBeNull();
    expect(parsePositiveId('')).toBeNull();
  });
});

describe('describeError', () => {
  it('maps the documented download status set, including 409/410 (FE-P6)', () => {
    const at = (status?: number): string =>
      describeError(Object.assign(new Error('x'), { status }));
    expect(at(403)).toContain('권한');
    expect(at(404)).toContain('찾을 수 없');
    expect(at(409)).toContain('변경');
    expect(at(410)).toContain('만료');
    expect(at(undefined)).toContain('연결할 수 없');
    expect(at(500)).toBe('요청이 실패했습니다.');
  });
});

describe('ReportsRoute RBAC', () => {
  it('denies the queue/request panels without report_automation:read', async () => {
    authenticateAs(['headless:read']); // artifacts allowed, reports denied
    mockHeadless({});
    renderReports();
    expect(await screen.findByTestId('auth-failure-permission_denied')).toBeInTheDocument();
    expect(screen.queryByTestId('queue-stats')).not.toBeInTheDocument();
    // artifacts subtree still renders its own lookup form
    expect(screen.getByTestId('session-id-input')).toBeInTheDocument();
  });
});

describe('ReportsRoute submit report (Phase 5)', () => {
  it('hides the generate panel without report_automation:control', async () => {
    authenticateAs(['report_automation:read', 'headless:read']);
    mockHeadless({
      '/report-automation/stats': { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 },
    });
    renderReports();
    await waitFor(() => expect(screen.getByTestId('queue-stats')).toBeInTheDocument());
    expect(screen.queryByTestId('reports-submit')).not.toBeInTheDocument();
  });

  it('does not fetch the provider descriptor when the panel is RBAC-hidden under ?area=', async () => {
    // A read-only principal (report_automation:read + headless:read, but NOT
    // report_automation:control) entering /reports?area= must NOT trigger the
    // provider descriptor fetch: the generate panel is hidden, so its descriptor
    // query stays disabled (canControl gate) — no wasted/leaky provider read.
    authenticateAs(['report_automation:read', 'headless:read']);
    mockHeadless({
      '/report-automation/stats': { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 },
    });
    platformApi.fetchProviderUiDescriptor.mockResolvedValue(
      descriptorWithMapping({ unlicensed_conducted: ['BT', 'BLE', 'DTS', 'UNII'] }),
    );
    renderReports('unlicensed_conducted');
    await waitFor(() => expect(screen.getByTestId('queue-stats')).toBeInTheDocument());
    expect(screen.queryByTestId('reports-submit')).not.toBeInTheDocument();
    expect(platformApi.fetchProviderUiDescriptor).not.toHaveBeenCalled();
  });

  it('runs a preflight then submits + polls (advisory not blocking)', async () => {
    authenticateAs(['report_automation:control', 'report_automation:read', 'headless:read']);
    mockHeadless({
      '/report-automation/stats': { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 },
      '/headless/reports/{request_id}': {
        id: 77,
        status: 'running',
        session_id: 12,
      },
      '/headless/reports/preflight': {
        session_id: 12,
        published_plan_id: 'plan-x',
        per_tech: [
          {
            technology: 'BT',
            measured_count: 4,
            planned_total: 4,
            kind: 'complete',
            complete: true,
          },
          {
            technology: 'WLAN',
            measured_count: 2,
            planned_total: 6,
            kind: 'incomplete',
            complete: false,
          },
        ],
        data_quality: [{ code: 'MISSING_CENTER_FREQ', message: '중심 주파수 누락', row_order: 12 }],
        missing_sources: [
          {
            technology: 'DTS',
            section: '6 dB Bandwidth',
            table_name: '6 dB Bandwidth',
            channel: null,
            label: '6 dB Bandwidth',
            reason: 'missing_row',
          },
        ],
        has_incomplete: true,
        has_data_quality_warnings: true,
        has_missing_sources: true,
      },
    });
    headlessClient.POST.mockResolvedValue(
      headlessOk('post', SUBMIT_REPORT_PATH, { request_id: 77, session_id: 12, status: 'queued' }),
    );
    renderReports();
    // Stage 1 — preflight (does NOT generate).
    await userEvent.type(await screen.findByTestId('submit-session-input'), '12');
    await userEvent.click(screen.getByTestId('report-preflight-check'));
    // Preflight summary renders: per-tech rows + data-quality + missing-sources (T2).
    expect(await screen.findByTestId('preflight-summary')).toBeInTheDocument();
    expect(screen.getAllByTestId('preflight-tech-row')).toHaveLength(2);
    expect(screen.getByTestId('preflight-data-quality')).toBeInTheDocument();
    expect(screen.getByTestId('preflight-missing-sources')).toBeInTheDocument();
    // GET preflight carried the session_id query.
    await waitFor(() =>
      expect(headlessClient.GET).toHaveBeenCalledWith(
        '/headless/reports/preflight',
        expect.objectContaining({ params: { query: { session_id: 12 } } }),
      ),
    );
    // Stage 2 — the generate button is enabled despite the gaps (not blocking).
    const generate = await screen.findByTestId('report-submit');
    expect(generate).toBeEnabled();
    await userEvent.click(generate);
    await waitFor(() =>
      expect(headlessClient.POST).toHaveBeenCalledWith(
        '/headless/sessions/{session_id}/reports',
        expect.objectContaining({ params: { path: { session_id: 12 } } }),
      ),
    );
    expect(await screen.findByTestId('submit-success')).toHaveTextContent('77');
    await waitFor(() =>
      expect(screen.getByTestId('submit-status')).toHaveTextContent(/running|실행/i),
    );
  });

  it('shows an all-clear preflight when complete with no warnings', async () => {
    authenticateAs(['report_automation:control', 'report_automation:read', 'headless:read']);
    mockHeadless({
      '/report-automation/stats': { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 },
      '/headless/reports/preflight': {
        session_id: 7,
        published_plan_id: 'plan-y',
        per_tech: [
          {
            technology: 'BLE',
            measured_count: 8,
            planned_total: 8,
            kind: 'complete',
            complete: true,
          },
        ],
        data_quality: [],
        missing_sources: [],
        has_incomplete: false,
        has_data_quality_warnings: false,
        has_missing_sources: false,
      },
    });
    renderReports();
    await userEvent.type(await screen.findByTestId('submit-session-input'), '7');
    await userEvent.click(screen.getByTestId('report-preflight-check'));
    expect(await screen.findByTestId('preflight-all-clear')).toBeInTheDocument();
    expect(screen.queryByTestId('preflight-data-quality')).not.toBeInTheDocument();
    expect(screen.queryByTestId('preflight-missing-sources')).not.toBeInTheDocument();
  });

  it('still surfaces the generate button when preflight itself fails', async () => {
    authenticateAs(['report_automation:control', 'report_automation:read', 'headless:read']);
    // No preflight handler → GET 404; the panel must still offer generation.
    mockHeadless({
      '/report-automation/stats': { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 },
    });
    renderReports();
    await userEvent.type(await screen.findByTestId('submit-session-input'), '5');
    await userEvent.click(screen.getByTestId('report-preflight-check'));
    expect(await screen.findByTestId('preflight-error')).toBeInTheDocument();
    expect(screen.getByTestId('report-submit')).toBeEnabled();
  });

  it('surfaces a submit error (e.g. missing output_dir → 400)', async () => {
    authenticateAs(['report_automation:control']);
    mockHeadless({
      '/headless/reports/preflight': {
        session_id: 5,
        published_plan_id: null,
        per_tech: [],
        data_quality: [],
        missing_sources: [],
        has_incomplete: false,
        has_data_quality_warnings: false,
        has_missing_sources: false,
      },
    });
    headlessClient.POST.mockResolvedValue(
      headlessProblem(
        'post',
        SUBMIT_REPORT_PATH,
        400,
        problemDetails(400, 'VALIDATION_ERROR', { detail: 'output_dir is required' }),
      ),
    );
    renderReports();
    await userEvent.type(await screen.findByTestId('submit-session-input'), '5');
    await userEvent.click(screen.getByTestId('report-preflight-check'));
    await userEvent.click(await screen.findByTestId('report-submit'));
    expect(await screen.findByTestId('submit-error')).toBeInTheDocument();
  });
});

describe('ReportsRoute submit report types (P5-B)', () => {
  // Shared preflight/stats/POST scaffolding — the tests differ only in the area
  // context + descriptor mock, and assert the resulting POST body.
  function armSubmit(): void {
    authenticateAs(['report_automation:control', 'report_automation:read', 'headless:read']);
    mockHeadless({
      '/report-automation/stats': { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 },
      '/headless/reports/preflight': {
        session_id: 12,
        published_plan_id: null,
        per_tech: [],
        data_quality: [],
        missing_sources: [],
        has_incomplete: false,
        has_data_quality_warnings: false,
        has_missing_sources: false,
      },
    });
    headlessClient.POST.mockResolvedValue(
      headlessOk('post', SUBMIT_REPORT_PATH, { request_id: 77, session_id: 12, status: 'queued' }),
    );
  }

  /**
   * The submit request the screen actually sent, typed against the contract.
   *
   * `headlessRequest` finds the call by operation and refuses to guess when
   * there is not exactly one, so the hand filter, the not-called guard and the
   * `as { body: … }` cast all collapse into naming the operation.
   *
   * The return type is derived, not `Record<string, unknown>` — and the
   * derivation immediately said something the cast had hidden: this operation's
   * body is **optional** on the wire, so the value can be `undefined`. The
   * assertion below is the honest form of what the cast asserted silently.
   */
  function reportsPostBody(): NonNullable<
    HeadlessRequestInit<'post', '/headless/sessions/{session_id}/reports'>['body']
  > {
    const body = headlessRequest(
      headlessClient.POST,
      'post',
      '/headless/sessions/{session_id}/reports',
    ).body;
    if (body === undefined) throw new Error('report submit POST carried no body');
    return body;
  }

  async function runPreflightAndGenerate(): Promise<void> {
    await userEvent.type(await screen.findByTestId('submit-session-input'), '12');
    await userEvent.click(screen.getByTestId('report-preflight-check'));
    await userEvent.click(await screen.findByTestId('report-submit'));
    await waitFor(() =>
      expect(headlessClient.POST).toHaveBeenCalledWith(
        '/headless/sessions/{session_id}/reports',
        expect.anything(),
      ),
    );
  }

  it('includes report_types from the area descriptor mapping', async () => {
    armSubmit();
    platformApi.fetchProviderUiDescriptor.mockResolvedValue(
      descriptorWithMapping({ unlicensed_conducted: ['BT', 'BLE', 'DTS', 'UNII'] }),
    );
    renderReports('unlicensed_conducted');
    // The descriptor fetch targets the deployed provider (id is a deployment
    // fact; the report_types VALUES come from its descriptor — ADR-0010).
    await waitFor(() =>
      expect(platformApi.fetchProviderUiDescriptor).toHaveBeenCalledWith(
        'fcc-unlicensed-conducted',
      ),
    );
    // Commit a session so Stage 2 (scope hint + generate button) renders.
    await userEvent.type(await screen.findByTestId('submit-session-input'), '12');
    await userEvent.click(screen.getByTestId('report-preflight-check'));
    // Honest scope feedback renders the resolved technologies before generate.
    expect(await screen.findByTestId('report-types-scope')).toHaveTextContent('BT, BLE, DTS, UNII');
    await userEvent.click(await screen.findByTestId('report-submit'));
    await waitFor(() =>
      expect(headlessClient.POST).toHaveBeenCalledWith(
        '/headless/sessions/{session_id}/reports',
        expect.anything(),
      ),
    );
    expect(reportsPostBody()).toEqual({
      generated_by: 'op-1',
      template_profile: 'fcc-default',
      report_types: ['BT', 'BLE', 'DTS', 'UNII'],
    });
  });

  it('omits report_types with no area (legacy full-scope body byte-equivalent)', async () => {
    armSubmit();
    renderReports(); // no ?area=
    await runPreflightAndGenerate();
    // No descriptor query runs (no waterfall) and the body is byte-identical to
    // the pre-P5-B payload.
    expect(platformApi.fetchProviderUiDescriptor).not.toHaveBeenCalled();
    expect(reportsPostBody()).toEqual({
      generated_by: 'op-1',
      template_profile: 'fcc-default',
    });
    expect(screen.queryByTestId('report-types-scope')).not.toBeInTheDocument();
  });

  it('omits report_types when the descriptor has no mapping for the area', async () => {
    armSubmit();
    platformApi.fetchProviderUiDescriptor.mockResolvedValue(
      descriptorWithMapping({ unlicensed_conducted: ['BT'] }), // mmwave unmapped
    );
    renderReports('mmwave');
    await waitFor(() =>
      expect(platformApi.fetchProviderUiDescriptor).toHaveBeenCalledWith(
        'fcc-unlicensed-conducted',
      ),
    );
    await runPreflightAndGenerate();
    expect(reportsPostBody()).toEqual({
      generated_by: 'op-1',
      template_profile: 'fcc-default',
    });
    expect(screen.queryByTestId('report-types-scope')).not.toBeInTheDocument();
  });

  it('omits report_types when the descriptor fetch fails', async () => {
    armSubmit();
    platformApi.fetchProviderUiDescriptor.mockRejectedValue(
      Object.assign(new Error('descriptor 404'), { status: 404 }),
    );
    renderReports('unlicensed_conducted');
    await waitFor(() => expect(platformApi.fetchProviderUiDescriptor).toHaveBeenCalled());
    await runPreflightAndGenerate();
    expect(reportsPostBody()).toEqual({
      generated_by: 'op-1',
      template_profile: 'fcc-default',
    });
  });

  it('omits report_types for an unrecognized area param (no descriptor fetch)', async () => {
    armSubmit();
    renderReports('not_a_real_area');
    await runPreflightAndGenerate();
    expect(platformApi.fetchProviderUiDescriptor).not.toHaveBeenCalled();
    expect(reportsPostBody()).toEqual({
      generated_by: 'op-1',
      template_profile: 'fcc-default',
    });
  });
});

describe('ReportsRoute project report sessions (P5-C)', () => {
  it('selects a project report session and submits to that node without rendering internal ids', async () => {
    authenticateAs(['report_automation:control', 'report_automation:read', 'headless:read']);
    mockHeadless({
      '/report-automation/stats': { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 },
    });
    platformApi.fetchProjectReportSessions.mockResolvedValue([
      {
        project_id: PROJECT_ID,
        submit_session_id: 42,
        node_id: 'fcc-unlicensed-conducted-node-a',
        node_name: 'Node A',
        node_base_url: 'http://node-a:8000',
        latest_measured_at: '2026-07-03T01:02:03Z',
        latest_verdict: 'PASS',
        completed_conditions: 8,
        technologies: ['BT', 'BLE'],
      },
    ]);
    nodeHeadlessClient.GET.mockImplementation((path: string) =>
      Promise.resolve(
        path === PREFLIGHT_PATH
          ? headlessOk('get', PREFLIGHT_PATH, {
              session_id: 42,
              published_plan_id: null,
              per_tech: [],
              data_quality: [],
              missing_sources: [],
              has_incomplete: false,
              has_data_quality_warnings: false,
              has_missing_sources: false,
            })
          : headlessOk('get', REPORT_STATUS_PATH, { id: 91, status: 'queued', session_id: 42 }),
      ),
    );
    nodeHeadlessClient.POST.mockResolvedValue(
      headlessOk('post', SUBMIT_REPORT_PATH, { request_id: 91, session_id: 42, status: 'queued' }),
    );

    renderReports(undefined, PROJECT_ID);

    await waitFor(() =>
      expect(platformApi.fetchProjectReportSessions).toHaveBeenCalledWith(PROJECT_ID),
    );
    expect(await screen.findByTestId('reports-project-context')).toBeInTheDocument();
    expect(screen.getByTestId('reports-project-context-workspace')).toHaveAttribute(
      'href',
      `/projects?project=${PROJECT_ID}`,
    );
    expect(screen.getByTestId('reports-project-context-chambers')).toHaveAttribute(
      'href',
      `/chambers?project=${PROJECT_ID}`,
    );
    expect(await screen.findByTestId('report-session-select')).toHaveTextContent('Node A');
    expect(screen.getByTestId('report-session-select')).not.toHaveTextContent(PROJECT_ID);
    expect(screen.queryByTestId('submit-session-input')).not.toBeInTheDocument();

    await userEvent.click(screen.getByTestId('report-preflight-check'));
    await waitFor(() =>
      expect(nodeHeadlessClient.GET).toHaveBeenCalledWith('/headless/reports/preflight', {
        params: { query: { session_id: 42 } },
      }),
    );
    await userEvent.click(await screen.findByTestId('report-submit'));
    await waitFor(() =>
      expect(createHeadlessClientForBaseUrl).toHaveBeenCalledWith('http://node-a:8000'),
    );
    await waitFor(() =>
      expect(nodeHeadlessClient.POST).toHaveBeenCalledWith(
        '/headless/sessions/{session_id}/reports',
        expect.objectContaining({ params: { path: { session_id: 42 } } }),
      ),
    );
    expect(headlessClient.POST).not.toHaveBeenCalledWith(
      '/headless/sessions/{session_id}/reports',
      expect.anything(),
    );
  });

  it('polls status on the node captured at submit time, not a node selected while submit is pending', async () => {
    // Race seal (P5-C): the operator preflights node A, hits generate (submit
    // in-flight), then re-selects + preflights node B before the POST resolves.
    // The submit target is captured atomically at click time, so the status poll
    // MUST follow node A — reading the (now node-B) component state would split
    // the POST target from the status-poll target.
    authenticateAs(['report_automation:control', 'report_automation:read', 'headless:read']);
    mockHeadless({
      '/report-automation/stats': { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 },
    });
    platformApi.fetchProjectReportSessions.mockResolvedValue([
      {
        project_id: PROJECT_ID,
        submit_session_id: 42,
        node_id: 'fcc-unlicensed-conducted-node-a',
        node_name: 'Node A',
        node_base_url: 'http://node-a:8000',
        latest_measured_at: '2026-07-03T01:02:03Z',
        latest_verdict: 'PASS',
        completed_conditions: 8,
        technologies: ['BT'],
      },
      {
        project_id: PROJECT_ID,
        submit_session_id: 77,
        node_id: 'fcc-unlicensed-conducted-node-b',
        node_name: 'Node B',
        node_base_url: 'http://node-b:8000',
        latest_measured_at: '2026-07-03T04:05:06Z',
        latest_verdict: 'PASS',
        completed_conditions: 5,
        technologies: ['BLE'],
      },
    ]);

    // Distinct per-node clients so the status poll's routing is observable (which
    // node's GET received the /headless/reports/{request_id} status call).
    const preflightResponse = (sessionId: number) =>
      headlessOk('get', PREFLIGHT_PATH, {
        session_id: sessionId,
        published_plan_id: null,
        per_tech: [],
        data_quality: [],
        missing_sources: [],
        has_incomplete: false,
        has_data_quality_warnings: false,
        has_missing_sources: false,
      });
    const nodeA = { GET: vi.fn(), POST: vi.fn() };
    const nodeB = { GET: vi.fn(), POST: vi.fn() };
    const clientsByUrl: Record<string, typeof nodeA> = {
      'http://node-a:8000': nodeA,
      'http://node-b:8000': nodeB,
    };
    createHeadlessClientForBaseUrl.mockImplementation(
      (url: string) => clientsByUrl[url] ?? nodeHeadlessClient,
    );

    nodeA.GET.mockImplementation((path: string) =>
      Promise.resolve(
        path === PREFLIGHT_PATH
          ? preflightResponse(42)
          : headlessOk('get', REPORT_STATUS_PATH, { id: 91, status: 'running', session_id: 42 }),
      ),
    );
    // node A submit stays pending until we explicitly resolve it.
    let resolveNodeAPost: () => void = () => undefined;
    nodeA.POST.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveNodeAPost = () =>
            resolve(
              headlessOk('post', SUBMIT_REPORT_PATH, {
                request_id: 91,
                session_id: 42,
                status: 'queued',
              }),
            );
        }),
    );
    nodeB.GET.mockImplementation((path: string) =>
      Promise.resolve(
        path === PREFLIGHT_PATH
          ? preflightResponse(77)
          : headlessOk('get', REPORT_STATUS_PATH, { id: 200, status: 'running', session_id: 77 }),
      ),
    );
    nodeB.POST.mockResolvedValue(
      headlessOk('post', SUBMIT_REPORT_PATH, { request_id: 200, session_id: 77, status: 'queued' }),
    );

    renderReports(undefined, PROJECT_ID);

    // Node A is the default (first) option — preflight then generate (pending).
    await screen.findByTestId('report-session-select');
    await userEvent.click(screen.getByTestId('report-preflight-check'));
    await waitFor(() =>
      expect(nodeA.GET).toHaveBeenCalledWith('/headless/reports/preflight', {
        params: { query: { session_id: 42 } },
      }),
    );
    await userEvent.click(await screen.findByTestId('report-submit'));
    await waitFor(() => expect(nodeA.POST).toHaveBeenCalled());

    // While the node A submit is still pending, move the selector to node B and
    // preflight it — this mutates the live requestNodeBaseUrl to node B.
    await userEvent.selectOptions(
      screen.getByTestId('report-session-select'),
      'http://node-b:8000::77',
    );
    await userEvent.click(screen.getByTestId('report-preflight-check'));
    await waitFor(() =>
      expect(nodeB.GET).toHaveBeenCalledWith('/headless/reports/preflight', {
        params: { query: { session_id: 77 } },
      }),
    );

    // Now let the node A submit resolve.
    resolveNodeAPost();

    // The status poll follows node A (the captured node), by both client and key.
    await waitFor(() =>
      expect(nodeA.GET).toHaveBeenCalledWith('/headless/reports/{request_id}', {
        params: { path: { request_id: 91 } },
      }),
    );
    // Node B (selected while pending) must never receive the status call, and the
    // shared (null-node) client must not either.
    expect(nodeB.GET).not.toHaveBeenCalledWith('/headless/reports/{request_id}', expect.anything());
    expect(headlessClient.GET).not.toHaveBeenCalledWith(
      '/headless/reports/{request_id}',
      expect.anything(),
    );
  });

  it('discards a committed legacy numeric target when the route switches into a project context (no node leak)', async () => {
    authenticateAs(['report_automation:control', 'report_automation:read', 'headless:read']);
    // Legacy flow: the shared (node-agnostic) headlessClient handles preflight,
    // submit and status polling for a raw numeric session id.
    mockHeadless({
      '/report-automation/stats': { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 },
      '/headless/reports/{request_id}': {
        id: 55,
        status: 'running',
        session_id: 3,
      },
      '/headless/reports/preflight': {
        session_id: 3,
        published_plan_id: null,
        per_tech: [],
        data_quality: [],
        missing_sources: [],
        has_incomplete: false,
        has_data_quality_warnings: false,
        has_missing_sources: false,
      },
    });
    headlessClient.POST.mockResolvedValue(
      headlessOk('post', SUBMIT_REPORT_PATH, { request_id: 55, session_id: 3, status: 'queued' }),
    );
    // Project flow: preflight/submit/status must route to the selected session's
    // node_base_url via the per-node client.
    platformApi.fetchProjectReportSessions.mockResolvedValue([
      {
        project_id: PROJECT_ID,
        submit_session_id: 42,
        node_id: 'fcc-unlicensed-conducted-node-a',
        node_name: 'Node A',
        node_base_url: 'http://node-a:8000',
        latest_measured_at: '2026-07-03T01:02:03Z',
        latest_verdict: 'PASS',
        completed_conditions: 8,
        technologies: ['BT', 'BLE'],
      },
    ]);
    nodeHeadlessClient.GET.mockImplementation((path: string) =>
      Promise.resolve(
        path === PREFLIGHT_PATH
          ? headlessOk('get', PREFLIGHT_PATH, {
              session_id: 42,
              published_plan_id: null,
              per_tech: [],
              data_quality: [],
              missing_sources: [],
              has_incomplete: false,
              has_data_quality_warnings: false,
              has_missing_sources: false,
            })
          : headlessOk('get', REPORT_STATUS_PATH, { id: 91, status: 'queued', session_id: 42 }),
      ),
    );
    nodeHeadlessClient.POST.mockResolvedValue(
      headlessOk('post', SUBMIT_REPORT_PATH, { request_id: 91, session_id: 42, status: 'queued' }),
    );

    renderReportsWithNav(); // starts at /reports (legacy, no project context)

    // Legacy Stage 1+2: commit a numeric session, run preflight, submit — the
    // shared client (node-agnostic) is used and the submit result is shown.
    await userEvent.type(await screen.findByTestId('submit-session-input'), '3');
    await userEvent.click(screen.getByTestId('report-preflight-check'));
    await userEvent.click(await screen.findByTestId('report-submit'));
    await waitFor(() =>
      expect(headlessClient.POST).toHaveBeenCalledWith(
        '/headless/sessions/{session_id}/reports',
        expect.objectContaining({ params: { path: { session_id: 3 } } }),
      ),
    );
    expect(await screen.findByTestId('submit-success')).toBeInTheDocument();

    // Switch the SAME router element to /reports?project=<uuid>.
    await userEvent.click(screen.getByTestId('nav-project'));

    // The stale legacy Stage 2 is gone: the numeric input is replaced by the
    // session selector, and the committed preflight/submit result no longer show.
    await waitFor(() => expect(screen.getByTestId('report-session-select')).toBeInTheDocument());
    expect(screen.queryByTestId('submit-session-input')).not.toBeInTheDocument();
    expect(screen.queryByTestId('reports-preflight')).not.toBeInTheDocument();
    expect(screen.queryByTestId('submit-result')).not.toBeInTheDocument();

    // Isolate the project-phase client usage from the legacy calls made above.
    headlessClient.POST.mockClear();
    headlessClient.GET.mockClear();

    // The selector loads the project's sessions; select + confirm must route
    // ONLY to the node client, never the shared headlessClient.
    await waitFor(() =>
      expect(platformApi.fetchProjectReportSessions).toHaveBeenCalledWith(PROJECT_ID),
    );
    expect(await screen.findByTestId('report-session-select')).toHaveTextContent('Node A');
    await userEvent.click(screen.getByTestId('report-preflight-check'));
    await waitFor(() =>
      expect(nodeHeadlessClient.GET).toHaveBeenCalledWith('/headless/reports/preflight', {
        params: { query: { session_id: 42 } },
      }),
    );
    await userEvent.click(await screen.findByTestId('report-submit'));
    await waitFor(() =>
      expect(createHeadlessClientForBaseUrl).toHaveBeenCalledWith('http://node-a:8000'),
    );
    await waitFor(() =>
      expect(nodeHeadlessClient.POST).toHaveBeenCalledWith(
        '/headless/sessions/{session_id}/reports',
        expect.objectContaining({ params: { path: { session_id: 42 } } }),
      ),
    );
    // No project-path preflight/submit ever hit the shared (null-node) client.
    expect(headlessClient.POST).not.toHaveBeenCalledWith(
      '/headless/sessions/{session_id}/reports',
      expect.anything(),
    );
    expect(headlessClient.GET).not.toHaveBeenCalledWith(
      '/headless/reports/preflight',
      expect.anything(),
    );
  });
});

describe('ReportsRoute queue stats', () => {
  it('renders queue counts from /report-automation/stats', async () => {
    authenticateAs(['report_automation:read', 'headless:read']);
    mockHeadless({
      '/report-automation/stats': {
        queued: 2,
        running: 1,
        completed: 7,
        failed: 1,
        cancelled: 0,
        oldest_queued_request_id: 5,
      },
    });
    renderReports();
    expect(screen.getByTestId('reports-workbench-overview')).toBeInTheDocument();
    expect(screen.getByTestId('reports-workbench')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '성적서 작업 흐름' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('stat-completed')).toHaveTextContent('7'));
    expect(screen.getByTestId('stat-queued')).toHaveTextContent('2');
  });
});

describe('ReportsRoute request lookup + missing diagnostics', () => {
  it('shows request status and flags a missing output file', async () => {
    authenticateAs(['report_automation:read', 'headless:read']);
    mockHeadless({
      '/report-automation/stats': {
        queued: 0,
        running: 0,
        completed: 1,
        failed: 0,
        cancelled: 0,
        oldest_queued_request_id: null,
      },
      '/headless/reports/{request_id}': {
        id: 9,
        status: 'completed',
        session_id: 3,
        error_message: '',
      },
      // FE-P6-DL: raw absolute path no longer returned — relative_path only.
      '/headless/reports/{request_id}/outputs': [
        {
          request_id: 9,
          file_name: 'a.docx',
          relative_path: 's3/a.docx',
          byte_size: 1024,
          exists: true,
          storage_backend: 'filesystem',
        },
        {
          request_id: 9,
          file_name: 'b.docx',
          relative_path: 's3/b.docx',
          byte_size: null,
          exists: false,
          storage_backend: 'filesystem',
        },
      ],
    });
    renderReports();
    await userEvent.type(await screen.findByTestId('request-id-input'), '9');
    await userEvent.click(screen.getByTestId('request-lookup'));

    // R5: the badge renders the localized status label (ko default '완료'), NOT
    // the raw backend token 'completed'.
    await waitFor(() => expect(screen.getByTestId('request-status')).toHaveTextContent('완료'));
    expect(screen.getByTestId('request-status')).not.toHaveTextContent('completed');
    expect(tableView('outputs-table').getAllByTestId('output-row')).toHaveLength(2);
    expect(tableView('outputs-table').getByTestId('output-missing')).toBeInTheDocument();
    expect(tableView('outputs-table').getByTestId('output-available')).toBeInTheDocument();
  });

  it('downloads an existing output via the signed grant flow (FE-P6-DL)', async () => {
    const download = stubSignedDownload();
    try {
      mockDownloadableOutputs();
      grantDownload();

      renderReports();
      await userEvent.type(await screen.findByTestId('request-id-input'), '9');
      await userEvent.click(screen.getByTestId('request-lookup'));
      await waitFor(() =>
        expect(tableView('outputs-table').getAllByTestId('output-row')).toHaveLength(2),
      );

      const [availableButton, missingButton] = screen.getAllByTestId('output-download');
      if (!availableButton || !missingButton) throw new Error('expected two download buttons');
      expect(availableButton).toBeEnabled(); // a.docx exists
      expect(missingButton).toBeDisabled(); // b.docx missing

      await userEvent.click(availableButton);
      await waitFor(() =>
        expect(headlessClient.POST).toHaveBeenCalledWith(
          '/headless/reports/{request_id}/outputs/download',
          { params: { path: { request_id: 9 } }, body: { relative_path: 's3/a.docx' } },
        ),
      );
      // The grant is spent in-page against the signed URL — no RBAC header, no
      // top-level navigation (M3). Before this wave the assertion here was
      // `window.location.assign(download_url)`.
      await waitFor(() =>
        expect(download.fetchMock).toHaveBeenCalledWith(
          '/headless/reports/outputs/download?token=SIGNED',
        ),
      );
      expect(download.fetchMock.mock.calls[0]?.length).toBe(1); // no init ⇒ no headers
      await waitFor(() => expect(download.anchorClick).toHaveBeenCalled());
      expect(download.assign).not.toHaveBeenCalled();
      // The route survived the download: the looked-up request + output list are
      // still on screen.
      expect(screen.getByTestId('request-detail')).toBeInTheDocument();
      expect(tableView('outputs-table').getAllByTestId('output-row')).toHaveLength(2);
    } finally {
      download.restore();
    }
  });

  // S6 — a failing download stream surfaces IN the SPA. Before M3 the browser
  // navigated to the raw problem+json and every one of these assertions was
  // structurally unreachable.
  it.each([
    {
      label: '409 integrity conflict',
      status: 409,
      body: { code: 'DOWNLOAD_INTEGRITY_CONFLICT' },
      expected: /변경되어 다운로드를 중단/,
    },
    {
      label: '410 expired grant',
      status: 410,
      body: { code: 'DOWNLOAD_EXPIRED' },
      expected: /유효 시간이 지났습니다/,
    },
    {
      label: '404 missing file',
      status: 404,
      body: { code: 'NOT_FOUND' },
      expected: /찾을 수 없습니다/,
    },
  ])(
    'surfaces a $label download failure without leaving the SPA',
    async ({ status, body, expected }) => {
      const download = stubSignedDownload({ ok: false, status, body });
      try {
        mockDownloadableOutputs();
        grantDownload();

        renderReports();
        await userEvent.type(await screen.findByTestId('request-id-input'), '9');
        await userEvent.click(screen.getByTestId('request-lookup'));
        await waitFor(() =>
          expect(tableView('outputs-table').getAllByTestId('output-row')).toHaveLength(2),
        );

        const [availableButton] = screen.getAllByTestId('output-download');
        if (!availableButton) throw new Error('expected a download button');
        await userEvent.click(availableButton);

        const error = await screen.findByTestId('download-error');
        expect(error).toHaveTextContent(expected);
        expect(download.assign).not.toHaveBeenCalled();
        expect(download.anchorClick).not.toHaveBeenCalled();
        // SPA context intact.
        expect(screen.getByTestId('request-detail')).toBeInTheDocument();
        expect(tableView('outputs-table').getAllByTestId('output-row')).toHaveLength(2);
      } finally {
        download.restore();
      }
    },
  );

  it('re-issues an already-expired grant instead of spending it (M3)', async () => {
    const download = stubSignedDownload();
    try {
      mockDownloadableOutputs();
      // First grant is born expired (a slow round-trip / resumed laptop); the
      // second is fresh. Spending the first would be a guaranteed 410.
      headlessClient.POST.mockResolvedValueOnce(
        headlessOk('post', GRANT_DOWNLOAD_PATH, {
          download_url: '/stale?token=OLD',
          expires_at: '2000-01-01T00:00:00Z',
        }),
      ).mockResolvedValue(
        headlessOk('post', GRANT_DOWNLOAD_PATH, {
          download_url: '/fresh?token=NEW',
          expires_at: '2999-01-01T00:00:00Z',
        }),
      );

      renderReports();
      await userEvent.type(await screen.findByTestId('request-id-input'), '9');
      await userEvent.click(screen.getByTestId('request-lookup'));
      await waitFor(() =>
        expect(tableView('outputs-table').getAllByTestId('output-row')).toHaveLength(2),
      );

      const [availableButton] = screen.getAllByTestId('output-download');
      if (!availableButton) throw new Error('expected a download button');
      await userEvent.click(availableButton);

      await waitFor(() => expect(download.fetchMock).toHaveBeenCalledWith('/fresh?token=NEW'));
      expect(download.fetchMock).not.toHaveBeenCalledWith('/stale?token=OLD');
    } finally {
      download.restore();
    }
  });

  it('disables lookup + warns on an invalid id', async () => {
    authenticateAs(['report_automation:read']);
    mockHeadless({
      '/report-automation/stats': {
        queued: 0,
        running: 0,
        completed: 0,
        failed: 0,
        cancelled: 0,
        oldest_queued_request_id: null,
      },
    });
    renderReports();
    await userEvent.type(await screen.findByTestId('request-id-input'), 'abc');
    expect(screen.getByTestId('request-lookup')).toBeDisabled();
    expect(screen.getByTestId('request-id-invalid')).toBeInTheDocument();
  });
});

describe('ReportsRoute session artifacts', () => {
  it('lists artifacts for a session id', async () => {
    authenticateAs(['headless:read']);
    mockHeadless({
      '/headless/sessions/{session_id}/artifacts': [
        {
          artifact_type: 'plot_png',
          provider_id: 'unlicensed',
          original_filename: 'plot.png',
          relative_path: 's3/plot.png',
          sha256: 'abc',
          byte_size: 10,
          created_at: '',
          storage_backend: 'filesystem',
          session_id: '3',
          result_id: '1',
        },
      ],
    });
    renderReports();
    await userEvent.type(await screen.findByTestId('session-id-input'), '3');
    await userEvent.click(screen.getByTestId('artifacts-lookup'));
    await waitFor(() => expect(screen.getByTestId('artifact-item')).toHaveTextContent('plot_png'));
  });
});

/**
 * S4/S5 — report lookup failure honesty (fe-w2-a-result-report-honesty M2).
 *
 * D2: every outputs branch in this panel was gated on `isSuccess`, so an
 * `outputs` lookup that FAILED rendered nothing at all. The operator saw a
 * request with no artifacts and re-ran generation to fix a problem that was
 * never generation's — while the four sibling queries in the same file
 * (`reportSessions`/`preflight`/`submit`/`stop`) all rendered their errors.
 *
 * D3: the lifecycle poll had no error branch either, and its `refetchInterval`
 * returned a fixed cadence unconditionally — so a failing poll hammered the node
 * every 2s for the rest of the session, silently.
 */
describe('report outputs failure is a first-class render (S4)', () => {
  it('renders an error and NOT the empty state when the outputs lookup fails', async () => {
    authenticateAs(['report_automation:read']);
    // `/headless/reports/{request_id}` resolves; `/outputs` is unregistered, so
    // mockHeadless answers 404 → the outputs query lands in `isError`.
    mockHeadless({
      '/report-automation/stats': {
        queued: 0,
        running: 0,
        completed: 0,
        failed: 0,
        cancelled: 0,
        oldest_queued_request_id: null,
      },
      '/headless/reports/{request_id}': {
        id: 7,
        status: 'completed',
        session_id: 3,
        error_message: '',
      },
    });
    renderReports();
    await userEvent.type(await screen.findByTestId('request-id-input'), '7');
    await userEvent.click(screen.getByTestId('request-lookup'));

    // The failure is stated…
    const error = await screen.findByTestId('outputs-error');
    expect(error).toHaveTextContent(/찾을 수 없습니다/);
    // …and never dressed up as "this request produced no artifacts".
    expect(screen.queryByTestId('outputs-empty')).toBeNull();
    expect(screen.queryByTestId('outputs-table')).toBeNull();
  });

  it('still shows the empty state when the lookup SUCCEEDS with no outputs', async () => {
    authenticateAs(['report_automation:read']);
    mockHeadless({
      '/report-automation/stats': {
        queued: 0,
        running: 0,
        completed: 0,
        failed: 0,
        cancelled: 0,
        oldest_queued_request_id: null,
      },
      '/headless/reports/{request_id}': {
        id: 7,
        status: 'completed',
        session_id: 3,
        error_message: '',
      },
      '/headless/reports/{request_id}/outputs': [],
    });
    renderReports();
    await userEvent.type(await screen.findByTestId('request-id-input'), '7');
    await userEvent.click(screen.getByTestId('request-lookup'));

    expect(await screen.findByTestId('outputs-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('outputs-error')).toBeNull();
  });
});

describe('report request poll cadence (S5)', () => {
  it('parks on a terminal state', () => {
    for (const status of ['completed', 'failed', 'cancelled']) {
      expect(reportRequestPollInterval({ status }, 0)).toBe(false);
    }
  });

  it('polls at the CRITICAL cadence while healthy and non-terminal', () => {
    expect(reportRequestPollInterval({ status: 'running' }, 0)).toBe(
      REFETCH_STRATEGIES.CRITICAL.refetchInterval,
    );
    expect(reportRequestPollInterval(undefined, 0)).toBe(
      REFETCH_STRATEGIES.CRITICAL.refetchInterval,
    );
  });

  it('does NOT repeat a fixed interval forever while failing', () => {
    const base = REFETCH_STRATEGIES.CRITICAL.refetchInterval;
    const intervals: (number | false)[] = [];
    for (let failures = 1; failures <= ERROR_POLL_MAX_CONSECUTIVE_FAILURES + 2; failures += 1) {
      intervals.push(reportRequestPollInterval(undefined, failures));
    }
    // Never the unchanged base cadence — that is exactly the old behaviour.
    expect(intervals.filter((v) => v === base)).toHaveLength(0);
    // Strictly growing while still retrying…
    const retrying = intervals.filter((v): v is number => typeof v === 'number');
    expect(retrying.length).toBeGreaterThan(0);
    retrying.reduce((previous, current) => {
      expect(current).toBeGreaterThan(previous);
      return current;
    }, 0);
    // …and it eventually parks for good.
    expect(intervals.at(-1)).toBe(false);
    expect(reportRequestPollInterval(undefined, ERROR_POLL_MAX_CONSECUTIVE_FAILURES)).toBe(false);
  });
});

/**
 * W3-6 M3 (2026-07-31) — report-request cancel.
 *
 * `TERMINAL_REQUEST_STATES` has listed `'cancelled'` since FE-P6 and the badge
 * renders it, but `POST /report-automation/requests/{id}/cancel` had zero
 * frontend consumption: the screen displayed the outcome of an action no
 * operator could take. These cases seal the wiring AND the two properties the
 * contract makes the point of it — the action is offered only where it can
 * succeed, and the panel that owns the request is the one that refreshes.
 */
describe('report request cancel (W3-6 M3)', () => {
  const CANCEL_PATH = '/report-automation/requests/{request_id}/cancel';
  const CONTROL_PERMS = ['report_automation:control', 'report_automation:read', 'headless:read'];

  /** GET router whose `/headless/reports/{request_id}` payload can move between
   *  calls — the only way to observe "cancel → refetch → status changed". */
  function mockLookup(statuses: readonly string[]): { readonly calls: () => number } {
    let index = 0;
    headlessClient.routes(
      {
        [STATS_PATH]: {
          get: () =>
            headlessOk('get', STATS_PATH, {
              queued: 1,
              running: 1,
              completed: 0,
              failed: 0,
              cancelled: 0,
            }),
        },
        [REPORT_STATUS_PATH]: {
          get: () => {
            const status = statuses[Math.min(index, statuses.length - 1)] ?? 'running';
            index += 1;
            return headlessOk('get', REPORT_STATUS_PATH, {
              id: 9,
              status,
              session_id: 3,
              error_message: '',
            });
          },
        },
        [OUTPUTS_PATH]: { get: () => headlessOk('get', OUTPUTS_PATH, []) },
      },
      { unrouted: 'not-found' },
    );
    return { calls: () => index };
  }

  describe('canCancelReportRequest (the decision, isolated)', () => {
    it('is true only for a non-terminal KNOWN queue status', () => {
      expect(canCancelReportRequest('queued')).toBe(true);
      expect(canCancelReportRequest('running')).toBe(true);
      // Terminal — cancelling is meaningless and the server would reject it.
      expect(canCancelReportRequest('completed')).toBe(false);
      expect(canCancelReportRequest('failed')).toBe(false);
      expect(canCancelReportRequest('cancelled')).toBe(false);
    });

    it('normalizes casing/whitespace exactly like the badge label does', () => {
      expect(canCancelReportRequest('  RUNNING ')).toBe(true);
      expect(canCancelReportRequest(' Completed')).toBe(false);
    });

    it('offers nothing for an unmodelled or absent status', () => {
      // Derived over the KNOWN vocabulary, so a forward-compat token the
      // frontend does not model yields no affordance rather than a button that
      // is guaranteed to fail. `undefined` = the status has not loaded.
      expect(canCancelReportRequest('paused')).toBe(false);
      expect(canCancelReportRequest('')).toBe(false);
      expect(canCancelReportRequest(undefined)).toBe(false);
    });
  });

  it('offers cancel for a running request and hides it once terminal (S7)', async () => {
    authenticateAs(CONTROL_PERMS);
    mockLookup(['running']);
    renderReports();
    await userEvent.type(await screen.findByTestId('request-id-input'), '9');
    await userEvent.click(screen.getByTestId('request-lookup'));
    await waitFor(() => expect(screen.getByTestId('request-cancel')).toBeInTheDocument());
  });

  it('does not offer cancel for a completed request (S7)', async () => {
    authenticateAs(CONTROL_PERMS);
    mockLookup(['completed']);
    renderReports();
    await userEvent.type(await screen.findByTestId('request-id-input'), '9');
    await userEvent.click(screen.getByTestId('request-lookup'));
    await waitFor(() => expect(screen.getByTestId('request-detail')).toBeInTheDocument());
    expect(screen.queryByTestId('request-cancel')).not.toBeInTheDocument();
    expect(screen.queryByTestId('request-cancel-section')).not.toBeInTheDocument();
  });

  it('does not offer cancel without report_automation:control (S7)', async () => {
    // The lookup panel is only `report_automation:read`-gated, so a read-only
    // operator reaches this surface and must see the status without the write.
    authenticateAs(['report_automation:read', 'headless:read']);
    mockLookup(['running']);
    renderReports();
    await userEvent.type(await screen.findByTestId('request-id-input'), '9');
    await userEvent.click(screen.getByTestId('request-lookup'));
    await waitFor(() => expect(screen.getByTestId('request-detail')).toBeInTheDocument());
    expect(screen.queryByTestId('request-cancel')).not.toBeInTheDocument();
  });

  it('requires a confirmation before the cancel request is sent (M3)', async () => {
    authenticateAs(CONTROL_PERMS);
    mockLookup(['running']);
    headlessClient.POST.mockResolvedValue(
      headlessOk('post', CANCEL_REQUEST_PATH, { cancelled: true, request_id: 9 }),
    );
    renderReports();
    await userEvent.type(await screen.findByTestId('request-id-input'), '9');
    await userEvent.click(screen.getByTestId('request-lookup'));
    await waitFor(() => expect(screen.getByTestId('request-cancel')).toBeInTheDocument());

    await userEvent.click(screen.getByTestId('request-cancel'));
    // Armed, not committed: a single stray click cannot destroy in-flight work.
    expect(screen.getByTestId('request-cancel-warning')).toBeInTheDocument();
    expect(headlessClient.POST).not.toHaveBeenCalled();

    await userEvent.click(screen.getByTestId('request-cancel-keep'));
    expect(screen.queryByTestId('request-cancel-confirm')).not.toBeInTheDocument();
    expect(headlessClient.POST).not.toHaveBeenCalled();
  });

  it('cancels, refetches, and the request becomes `cancelled` on screen (S8, S9)', async () => {
    authenticateAs(CONTROL_PERMS);
    // running → (cancel) → cancelled: the state the screen has always been able
    // to DISPLAY is now one the operator can actually PRODUCE.
    mockLookup(['running', 'cancelled']);
    headlessClient.POST.mockResolvedValue(
      headlessOk('post', CANCEL_REQUEST_PATH, { cancelled: true, request_id: 9 }),
    );
    renderReports();
    await userEvent.type(await screen.findByTestId('request-id-input'), '9');
    await userEvent.click(screen.getByTestId('request-lookup'));
    await waitFor(() => expect(screen.getByTestId('request-cancel')).toBeInTheDocument());

    await userEvent.click(screen.getByTestId('request-cancel'));
    await userEvent.click(screen.getByTestId('request-cancel-confirm-button'));

    // No request body — the contract makes it optional and this screen collects
    // no reason, so nothing is invented.
    await waitFor(() =>
      expect(headlessClient.POST).toHaveBeenCalledWith(CANCEL_PATH, {
        params: { path: { request_id: 9 } },
      }),
    );
    // S9 — the badge now shows the localized cancelled label (ko '취소'), which
    // is the display-vs-action alignment this milestone is for…
    await waitFor(() => expect(screen.getByTestId('request-status')).toHaveTextContent('취소'));
    // …and the control retires itself, because `cancelled` is terminal.
    await waitFor(() => expect(screen.queryByTestId('request-cancel')).not.toBeInTheDocument());
  });

  it('invalidates the HOST panel query key, not a self-composed one (S8)', async () => {
    // `queryKeys.report.request` has two arities: the lookup panel reads
    // `(id)` and the submit panel's poll reads `(id, nodeBaseUrl)` — different
    // cache entries. A control that rebuilt the key itself would refresh the
    // other panel, and the failure is the quiet kind: the cancel succeeds while
    // the status on screen never moves. So the key is injected by the host.
    const invalidateSpy = vi.spyOn(QueryClient.prototype, 'invalidateQueries');
    authenticateAs(CONTROL_PERMS);
    mockLookup(['running', 'cancelled']);
    headlessClient.POST.mockResolvedValue(
      headlessOk('post', CANCEL_REQUEST_PATH, { cancelled: true, request_id: 9 }),
    );
    renderReports();
    await userEvent.type(await screen.findByTestId('request-id-input'), '9');
    await userEvent.click(screen.getByTestId('request-lookup'));
    await waitFor(() => expect(screen.getByTestId('request-cancel')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('request-cancel'));
    await userEvent.click(screen.getByTestId('request-cancel-confirm-button'));
    await waitFor(() => expect(headlessClient.POST).toHaveBeenCalled());

    // Exactly the lookup panel's key (no node segment) …
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: queryKeys.report.request(9) }),
    );
    // … and the queue counts, which the cancel also moved.
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: queryKeys.report.stats() });
    invalidateSpy.mockRestore();
  });

  it('routes the cancel to the node the request was submitted to (M3)', async () => {
    // The submit panel polls a node-routed request. The cancel POST must reach
    // that node, and it must invalidate the node-routed cache entry — not the
    // lookup panel's plain one.
    const invalidateSpy = vi.spyOn(QueryClient.prototype, 'invalidateQueries');
    authenticateAs(CONTROL_PERMS);
    mockHeadless({
      '/report-automation/stats': { queued: 1, running: 1, completed: 0, failed: 0, cancelled: 0 },
    });
    platformApi.fetchProjectReportSessions.mockResolvedValue([
      {
        project_id: PROJECT_ID,
        submit_session_id: 42,
        node_id: 'node-a',
        node_name: 'Node A',
        node_base_url: 'http://node-a:8000',
        latest_measured_at: '2026-07-31T01:02:03Z',
        latest_verdict: 'PASS',
        completed_conditions: 8,
        technologies: ['BT'],
      },
    ]);
    nodeHeadlessClient.GET.mockImplementation((path: string) =>
      Promise.resolve(
        path === REPORT_STATUS_PATH
          ? headlessOk('get', REPORT_STATUS_PATH, {
              id: 55,
              status: 'running',
              session_id: 42,
              error_message: '',
            })
          : headlessOk('get', PREFLIGHT_PATH, {
              session_id: 42,
              published_plan_id: null,
              per_tech: [],
              data_quality: [],
              missing_sources: [],
              has_incomplete: false,
              has_data_quality_warnings: false,
              has_missing_sources: false,
            }),
      ),
    );
    nodeHeadlessClient.POST.mockResolvedValue(
      headlessOk('post', SUBMIT_REPORT_PATH, { request_id: 55, session_id: 42, status: 'queued' }),
    );
    renderReports(undefined, PROJECT_ID);
    await waitFor(() => expect(screen.getByTestId('report-preflight-check')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('report-preflight-check'));
    await userEvent.click(await screen.findByTestId('report-submit'));
    await waitFor(() => expect(screen.getByTestId('submit-status')).toBeInTheDocument());

    nodeHeadlessClient.POST.mockResolvedValue(
      headlessOk('post', CANCEL_REQUEST_PATH, { cancelled: true, request_id: 55 }),
    );
    await userEvent.click(await screen.findByTestId('request-cancel'));
    await userEvent.click(screen.getByTestId('request-cancel-confirm-button'));

    // The cancel went to the NODE client, never the shared one.
    await waitFor(() =>
      expect(nodeHeadlessClient.POST).toHaveBeenCalledWith(CANCEL_PATH, {
        params: { path: { request_id: 55 } },
      }),
    );
    expect(headlessClient.POST).not.toHaveBeenCalledWith(CANCEL_PATH, expect.anything());
    // …and the node-routed cache entry is the one invalidated.
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: queryKeys.report.request(55, 'http://node-a:8000'),
      }),
    );
    invalidateSpy.mockRestore();
  });

  it('surfaces a cancel failure through the existing error taxonomy (M3)', async () => {
    // No invented ErrorCode: a 409 (the request reached a terminal state first)
    // renders through the same `describeError` ladder the rest of this screen
    // uses.
    authenticateAs(CONTROL_PERMS);
    mockLookup(['running']);
    headlessClient.POST.mockResolvedValue(
      headlessProblem(
        'post',
        CANCEL_REQUEST_PATH,
        409,
        problemDetails(409, 'CONFLICT', { detail: 'already finished' }),
      ),
    );
    renderReports();
    await userEvent.type(await screen.findByTestId('request-id-input'), '9');
    await userEvent.click(screen.getByTestId('request-lookup'));
    await waitFor(() => expect(screen.getByTestId('request-cancel')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('request-cancel'));
    await userEvent.click(screen.getByTestId('request-cancel-confirm-button'));
    await waitFor(() => expect(screen.getByTestId('request-cancel-error')).toBeInTheDocument());
    expect(screen.getByTestId('request-cancel-error')).toHaveTextContent(
      describeError({ status: 409 }),
    );
  });
});
