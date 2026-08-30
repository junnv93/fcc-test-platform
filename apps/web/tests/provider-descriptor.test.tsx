import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import { ProvidersRoute } from '@/routes/providers';

import type { ReactElement } from 'react';

/**
 * WEB-PROVIDER-UI-0 — read-only provider UI descriptor viewer tests.
 *
 * Auth via the real `auth/session.ts` SSOT; the platform API client is mocked
 * (only `fetchProviderUiDescriptor` is consumed). Covers RBAC gating
 * (platform:read), schema-first descriptor render (feature matrix + tables),
 * and the 404 (unknown provider) error path.
 */

const platformApi = vi.hoisted(() => ({
  fetchProviderUiDescriptor: vi.fn(),
  // The picker fetches the provider list on render; default to an empty list so
  // these descriptor-focused tests are unaffected by it.
  fetchProviderList: vi.fn().mockResolvedValue([]),
}));
vi.mock('@/api/platform-client', () => platformApi);

const PROVIDER_ID = 'fcc-unlicensed-conducted';

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

function descriptorFixture(): unknown {
  return {
    provider_id: PROVIDER_ID,
    display_name: 'unlicensed-conducted',
    ui_version: 1,
    features: [
      { feature_id: 'test_plan_edit', label: 'test plan edit', status: 'planned' },
      { feature_id: 'job_submission', label: 'job submission', status: 'supported' },
    ],
    test_plan_tables: [
      {
        table_id: 'test_plan',
        label: 'Test Plan',
        sheet_name: 'Test Plan',
        row_identity_source: 'Col.HISTORY_CONDITION',
        row_identity_columns: ['Test'],
        columns: [],
      },
    ],
    equipment: [
      {
        group_id: 'chamber_config',
        label: 'Chamber Config',
        sheet_name: 'Chamber Config',
        fields: [],
      },
    ],
    reference_tables: [
      {
        table_id: 'analyzer_settings',
        label: 'Analyzer Settings',
        sheet_name: 'Analyzer Settings',
        columns: [],
      },
    ],
    correction_tables: [],
  };
}

function renderProviders(entry: string): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <ProvidersRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
}

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
  platformApi.fetchProviderUiDescriptor.mockReset();
  // Re-arm the picker's list fetch each test (afterEach restoreAllMocks wipes the
  // hoisted default) so the picker query never resolves undefined.
  platformApi.fetchProviderList.mockReset();
  platformApi.fetchProviderList.mockResolvedValue([]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ProvidersRoute RBAC', () => {
  it('denies the viewer without platform:read', async () => {
    authenticateAs(['headless:read']);
    renderProviders(`/providers?provider=${PROVIDER_ID}`);
    expect(await screen.findByTestId('auth-failure-permission_denied')).toBeInTheDocument();
    expect(platformApi.fetchProviderUiDescriptor).not.toHaveBeenCalled();
  });
});

describe('ProvidersRoute descriptor render', () => {
  it('renders the feature matrix + tables read-only', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchProviderUiDescriptor.mockResolvedValue(descriptorFixture());
    renderProviders(`/providers?provider=${PROVIDER_ID}`);

    await waitFor(() => expect(screen.getByTestId('descriptor')).toBeInTheDocument());
    // R2: display name is the operator-facing identity; the internal provider id
    // lives in the collapsed admin/diagnostics <details> (still in the DOM).
    expect(screen.getByTestId('descriptor-display-name')).toHaveTextContent('unlicensed-conducted');
    expect(screen.getByTestId('descriptor-provider-id')).toHaveTextContent(PROVIDER_ID);
    expect(screen.getByTestId('descriptor-diagnostics')).toBeInTheDocument();
    expect(screen.getAllByTestId('feature-row')).toHaveLength(2);
    expect(screen.getAllByTestId('test-plan-table')).toHaveLength(1);
    // R2: internal descriptor identifiers (row identity source / feature·table
    // ids / sheet names) are NOT rendered in the operator tables.
    expect(screen.queryByTestId('row-identity-source')).not.toBeInTheDocument();
    expect(screen.queryByText('Col.HISTORY_CONDITION')).not.toBeInTheDocument();
    // The feature status renders a localized label, not the raw backend token.
    expect(screen.queryByText('planned')).not.toBeInTheDocument();
    expect(platformApi.fetchProviderUiDescriptor).toHaveBeenCalledWith(PROVIDER_ID);
    // Read-only: no edit/save controls.
    expect(screen.queryByRole('button', { name: /저장|save|publish/iu })).not.toBeInTheDocument();
  });

  it('surfaces a 404 for an unknown provider', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchProviderUiDescriptor.mockRejectedValue(
      Object.assign(new Error('missing'), { status: 404 }),
    );
    renderProviders('/providers?provider=unknown-provider');
    await waitFor(() => expect(screen.getByTestId('descriptor-error')).toBeInTheDocument());
  });
});
