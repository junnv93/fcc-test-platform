import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import { t } from '@/i18n';
import { InventoryRoute } from '@/routes/inventory';

import type { ReactElement } from 'react';

const platformApi = vi.hoisted(() => ({
  fetchProjectsPage: vi.fn(),
  fetchSampleInventory: vi.fn(),
  fetchSample: vi.fn(),
  createSample: vi.fn(),
  patchSample: vi.fn(),
  changeSampleStatus: vi.fn(),
  softDeleteSample: vi.fn(),
  hardDeleteSample: vi.fn(),
  fetchSampleHistory: vi.fn(),
  // 모듈 전체를 대체하는 mock 이므로 새 함수가 여기 없으면 컴포넌트가 undefined 를
  // 호출하고 조용히 에러 상태로 앉는다 — 테스트는 통과하면서.
  fetchSampleIntakes: vi.fn(),
  fetchSampleCustodyEvents: vi.fn(),
  appendSampleCustodyEvent: vi.fn(),
  deleteSampleCustodyEvent: vi.fn(),
  exportSampleInventory: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const SAMPLE_ID = '22222222-2222-4222-8222-222222222222';

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
    accessToken: makeJwt({ sub: 'sample-editor@corp', [CLAIM_PERMISSIONS]: permissions }),
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
}

function sample(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    sample_id: SAMPLE_ID,
    project_id: PROJECT_ID,
    sample_number: '#1',
    sample_code: 'S-1',
    test_category: 'Main Conduction',
    label_number: 'LBL-1',
    smsn: 'SMSN-1',
    serial_number: 'SN-1',
    intake_cert: 'CERT-1',
    assigned_team: 'RF',
    sender: 'Sender',
    receiver: 'Receiver',
    received_date: '2026-08-01',
    released_date: null,
    note: 'note',
    status: 'active',
    row_version: 1,
    intake_count: 1,
    latest_intake: {
      id: 'intake-1',
      sample_id: SAMPLE_ID,
      intake_date: '2026-08-02',
      tech_group: 'WLAN',
      bl: 'BL-1',
      ap: 'AP-1',
      cp: 'CP-1',
      csc: 'CSC-1',
      rf_cal: 'CAL-1',
      hw_rev: 'HW-1',
      note: null,
    },
    ...over,
  };
}

function renderRoute(entry: string): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <InventoryRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
}

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
  for (const mock of Object.values(platformApi)) mock.mockReset();
  platformApi.fetchProjectsPage.mockResolvedValue({ items: [], nextCursor: null });
  platformApi.fetchSampleIntakes.mockResolvedValue({ items: [] });
  platformApi.fetchSampleCustodyEvents.mockResolvedValue({ items: [] });
  platformApi.fetchSampleInventory.mockResolvedValue({
    items: [sample()],
    next_cursor: null,
    as_of: null,
    filters: {},
  });
  platformApi.fetchSample.mockResolvedValue(sample());
  platformApi.fetchSampleHistory.mockResolvedValue({
    items: [
      {
        revision_id: 'revision-1',
        sample_id: SAMPLE_ID,
        project_id: PROJECT_ID,
        revision_number: 1,
        event_type: 'created',
        changed_fields: ['sample_number'],
        actor_subject: 'seed',
        occurred_at: '2026-08-24T00:00:00Z',
        snapshot: sample(),
      },
    ],
    next_cursor: null,
  });
  platformApi.patchSample.mockImplementation(
    (_projectId, _sampleId, body: Record<string, unknown>) => sample({ row_version: 2, ...body }),
  );
  platformApi.softDeleteSample.mockResolvedValue(sample({ status: 'deleted', row_version: 2 }));
  platformApi.changeSampleStatus.mockResolvedValue(sample({ status: 'active', row_version: 3 }));
  platformApi.hardDeleteSample.mockResolvedValue({ hard_deleted: true, sample_id: SAMPLE_ID });
  platformApi.exportSampleInventory.mockResolvedValue({
    blob: new Blob(['xlsx']),
    filename: 'server-named.xlsx',
  });
  authenticateAs(['platform:read', 'platform:sample-write', 'platform:sample-hard-delete']);
});

describe('InventoryRoute direct CRUD surface', () => {
  it('passes project/team/status/as-of/include-deleted filters to the authoritative list', async () => {
    renderRoute(
      `/inventory?project=${PROJECT_ID}&team=RF&status=all&as_of=2026-08-24T00:00:00Z&include_deleted=true`,
    );
    await waitFor(() => expect(platformApi.fetchSampleInventory).toHaveBeenCalled());
    expect(platformApi.fetchSampleInventory).toHaveBeenCalledWith({
      projectId: PROJECT_ID,
      team: 'RF',
      status: 'all',
      asOf: '2026-08-24T00:00:00Z',
      includeDeleted: true,
      limit: 100,
    });
    expect(screen.getByTestId('inventory-as-of-notice')).toBeInTheDocument();
  });

  it('sends the complete editable record and expected version on save', async () => {
    const user = userEvent.setup();
    renderRoute(`/inventory?project=${PROJECT_ID}&sample=${SAMPLE_ID}`);
    await user.clear(await screen.findByTestId('sample-editor-assigned_team'));
    await user.type(screen.getByTestId('sample-editor-assigned_team'), 'SAR');
    await user.click(screen.getByTestId('sample-editor-save'));
    await waitFor(() => expect(platformApi.patchSample).toHaveBeenCalled());
    expect(platformApi.patchSample.mock.calls[0]?.[2]).toMatchObject({
      expected_version: 1,
      sample_number: '#1',
      assigned_team: 'SAR',
    });
  });

  it('keeps viewer controls read-only while retaining the server detail', async () => {
    authenticateAs(['platform:read']);
    renderRoute(`/inventory?project=${PROJECT_ID}&sample=${SAMPLE_ID}`);
    expect(await screen.findByTestId('sample-editor-sample_number')).toBeDisabled();
    expect(screen.queryByTestId('sample-editor-save')).toBeNull();
    expect(screen.queryByTestId('sample-soft-delete')).toBeNull();
  });

  it('uses the server-provided export filename for both template actions', async () => {
    const user = userEvent.setup();
    const anchorClick = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined);
    const createObjectURL = vi.fn(() => 'blob:sample');
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL });
    try {
      renderRoute(`/inventory?project=${PROJECT_ID}`);
      await user.click(await screen.findByTestId('sample-export-pm'));
      await user.click(screen.getByTestId('sample-export-rf'));
      await waitFor(() => expect(platformApi.exportSampleInventory).toHaveBeenCalledTimes(2));
      expect(platformApi.exportSampleInventory.mock.calls[0]).toEqual([
        PROJECT_ID,
        'pm-status',
        { status: 'active', includeDeleted: false },
      ]);
      expect(createObjectURL).toHaveBeenCalled();
    } finally {
      anchorClick.mockRestore();
    }
  });

  it('follows the opaque list cursor and renders all 101 distinct rows', async () => {
    const firstPage = Array.from({ length: 100 }, (_, index) =>
      sample({
        sample_id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`,
        sample_number: `#${index + 1}`,
      }),
    );
    const secondPage = [
      sample({
        sample_id: '00000000-0000-4000-8000-000000000101',
        sample_number: '#101',
      }),
    ];
    platformApi.fetchSampleInventory
      .mockResolvedValueOnce({
        items: firstPage,
        next_cursor: 'opaque-page-2',
        as_of: null,
        filters: {},
      })
      .mockResolvedValueOnce({ items: secondPage, next_cursor: null, as_of: null, filters: {} });

    const user = userEvent.setup();
    renderRoute(`/inventory?project=${PROJECT_ID}`);
    await screen.findByTestId('inventory-sample-#100');
    await user.click(screen.getByTestId('inventory-load-more'));
    await screen.findByTestId('inventory-sample-#101');

    expect(platformApi.fetchSampleInventory).toHaveBeenNthCalledWith(2, {
      projectId: PROJECT_ID,
      includeDeleted: false,
      limit: 100,
      after: 'opaque-page-2',
    });
    expect(screen.getAllByTestId(/^inventory-sample-#/)).toHaveLength(101);
  });

  it('follows the opaque history cursor without dropping the first 100 revisions', async () => {
    const firstPage = Array.from({ length: 100 }, (_, index) => ({
      revision_id: `revision-${index + 1}`,
      sample_id: SAMPLE_ID,
      project_id: PROJECT_ID,
      revision_number: index + 1,
      event_type: 'updated',
      changed_fields: ['note'],
      actor_subject: 'seed',
      occurred_at: `2026-08-${String((index % 28) + 1).padStart(2, '0')}T00:00:00Z`,
      snapshot: sample({ note: `note-${index + 1}` }),
    }));
    const secondPage = [{ ...firstPage[0], revision_id: 'revision-101', revision_number: 101 }];
    platformApi.fetchSampleHistory
      .mockResolvedValueOnce({ items: firstPage, next_cursor: 'history-page-2' })
      .mockResolvedValueOnce({ items: secondPage, next_cursor: null });

    const user = userEvent.setup();
    renderRoute(`/inventory?project=${PROJECT_ID}&sample=${SAMPLE_ID}`);
    await screen.findByTestId('sample-history-list');
    await user.click(screen.getByTestId('sample-history-load-more'));
    // The witness for "the second page rendered" is the revision-101 row, not the
    // wording of its label. Deriving the expected text from the same t() SSOT the
    // component calls removes a copy coupling that broke this test once already —
    // the literal it used to carry guessed a Korean word the bundle does not use.
    // Independence is preserved on the axis this test owns: a wrong key falls back
    // to the raw key string and never interpolates 101, so the assertion still
    // fails if SampleHistory.tsx renders the wrong key or drops the parameter.
    await waitFor(() =>
      expect(
        screen.getByText(t('routes.sampleInventory.history.revision', { revision: 101 })),
      ).toBeInTheDocument(),
    );

    expect(platformApi.fetchSampleHistory).toHaveBeenNthCalledWith(
      2,
      PROJECT_ID,
      SAMPLE_ID,
      'history-page-2',
      100,
    );
    expect(screen.getByTestId('sample-history-list').querySelectorAll('li')).toHaveLength(101);
  });

  it('offers a 409 reload and retains the selected detail until the user accepts it', async () => {
    const conflict = Object.assign(new Error('version conflict'), { status: 409 });
    platformApi.patchSample.mockRejectedValueOnce(conflict);
    const user = userEvent.setup();
    renderRoute(`/inventory?project=${PROJECT_ID}&sample=${SAMPLE_ID}`);
    await user.click(await screen.findByTestId('sample-editor-save'));
    expect(await screen.findByTestId('sample-editor-reload')).toBeInTheDocument();
    expect(screen.getByTestId('sample-editor-sample_number')).toBeInTheDocument();

    await user.click(screen.getByTestId('sample-editor-reload'));
    await waitFor(() => expect(platformApi.fetchSample).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId('sample-editor-sample_number')).toBeInTheDocument();
  });

  it('retains selection and detail when hard delete is rejected', async () => {
    platformApi.hardDeleteSample.mockRejectedValueOnce(
      Object.assign(new Error('forbidden'), { status: 403 }),
    );
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const user = userEvent.setup();
    try {
      renderRoute(`/inventory?project=${PROJECT_ID}&sample=${SAMPLE_ID}`);
      await user.click(await screen.findByTestId('sample-hard-delete'));
      await screen.findByTestId('sample-status-error');
      expect(screen.getByTestId('sample-editor-sample_number')).toBeInTheDocument();
      expect(screen.getByTestId('sample-hard-delete')).toBeInTheDocument();
    } finally {
      confirm.mockRestore();
    }
  });
});
