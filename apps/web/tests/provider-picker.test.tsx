import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import { ProvidersRoute } from '@/routes/providers';

import type { ReactElement } from 'react';

/**
 * WEB-PROVIDER-UI-0 — backend-driven ProviderPicker tests.
 *
 * The picker options come from `fetchProviderList` (GET /platform/providers),
 * never a browser-local list. Selecting an option writes the `?provider=` deep
 * link; a direct/deep-linked id restores the selection.
 */

const platformApi = vi.hoisted(() => ({
  fetchProviderList: vi.fn(),
  fetchProviderUiDescriptor: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

const PROVIDER_A = 'fcc-unlicensed-conducted';
const PROVIDER_B = 'fcc-unlicensed-radiated';

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

function providerList(): unknown {
  return [
    { provider_id: PROVIDER_A, display_name: 'unlicensed-conducted', ui_version: 1 },
    { provider_id: PROVIDER_B, display_name: 'unlicensed-radiated', ui_version: 1 },
  ];
}

function descriptorFor(providerId: string): unknown {
  return {
    provider_id: providerId,
    display_name: 'unlicensed-conducted',
    ui_version: 1,
    features: [],
    test_plan_tables: [],
    equipment: [],
    reference_tables: [],
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
  platformApi.fetchProviderList.mockReset();
  platformApi.fetchProviderUiDescriptor.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ProviderPicker (backend-first)', () => {
  it('populates the picker options from the backend provider list', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchProviderList.mockResolvedValue(providerList());
    renderProviders('/providers');

    const picker = await screen.findByTestId('provider-picker');
    await waitFor(() =>
      expect(picker.querySelectorAll('option[value]:not([value=""])')).toHaveLength(2),
    );
    // Labels are the backend display names, values are the provider ids.
    expect(screen.getByRole('option', { name: 'unlicensed-conducted' })).toHaveValue(PROVIDER_A);
    expect(screen.getByRole('option', { name: 'unlicensed-radiated' })).toHaveValue(PROVIDER_B);
    expect(platformApi.fetchProviderList).toHaveBeenCalledTimes(1);
  });

  it('selecting a provider writes the ?provider= deep link and loads the descriptor', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchProviderList.mockResolvedValue(providerList());
    platformApi.fetchProviderUiDescriptor.mockResolvedValue(descriptorFor(PROVIDER_B));
    renderProviders('/providers');

    const picker = await screen.findByTestId('provider-picker');
    await waitFor(() =>
      expect(picker.querySelectorAll('option[value]:not([value=""])')).toHaveLength(2),
    );
    await userEvent.selectOptions(picker, PROVIDER_B);

    await waitFor(() =>
      expect(platformApi.fetchProviderUiDescriptor).toHaveBeenCalledWith(PROVIDER_B),
    );
    expect(picker).toHaveValue(PROVIDER_B);
  });

  it('restores the picker selection from a direct ?provider= deep link', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchProviderList.mockResolvedValue(providerList());
    platformApi.fetchProviderUiDescriptor.mockResolvedValue(descriptorFor(PROVIDER_A));
    renderProviders(`/providers?provider=${PROVIDER_A}`);

    const picker = await screen.findByTestId('provider-picker');
    await waitFor(() => expect(picker).toHaveValue(PROVIDER_A));
    expect(platformApi.fetchProviderUiDescriptor).toHaveBeenCalledWith(PROVIDER_A);
  });

  it('keeps an unregistered deep-linked id selectable (direct lookup preserved)', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchProviderList.mockResolvedValue(providerList());
    platformApi.fetchProviderUiDescriptor.mockResolvedValue(descriptorFor('custom-provider'));
    renderProviders('/providers?provider=custom-provider');

    const picker = await screen.findByTestId('provider-picker');
    // The id is not in the fetched list, but the picker still represents it so
    // the direct-lookup / deep-link selection is not silently dropped.
    await waitFor(() => expect(picker).toHaveValue('custom-provider'));
    expect(platformApi.fetchProviderUiDescriptor).toHaveBeenCalledWith('custom-provider');
  });
});

/** An API error shape describeApiError understands (`.status` is the HTTP code;
 *  absent = network/offline). */
function apiError(status?: number): Error & { status?: number } {
  const e = new Error('list load failed') as Error & { status?: number };
  if (status !== undefined) e.status = status;
  return e;
}

describe('ProviderPicker list states (loading / error / empty)', () => {
  it('shows a loading note and disables the select while the list is loading', async () => {
    authenticateAs(['platform:read']);
    // Never resolves — the query stays in its loading state.
    platformApi.fetchProviderList.mockReturnValue(new Promise(() => undefined));
    renderProviders('/providers');

    expect(await screen.findByTestId('provider-picker-loading')).toBeInTheDocument();
    expect(screen.getByTestId('provider-picker')).toBeDisabled();
    expect(screen.queryByTestId('provider-picker-error')).not.toBeInTheDocument();
    expect(screen.queryByTestId('provider-picker-empty')).not.toBeInTheDocument();
  });

  it('surfaces a forbidden note on a 403 list failure (platform:read missing)', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchProviderList.mockRejectedValue(apiError(403));
    renderProviders('/providers');

    const banner = await screen.findByTestId('provider-picker-error');
    expect(banner).toHaveTextContent('권한이 없어요');
    // The select stays enabled so a deep-linked / typed id still resolves.
    expect(screen.getByTestId('provider-picker')).not.toBeDisabled();
  });

  it('surfaces an unavailable note on a 503 list failure (registry down)', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchProviderList.mockRejectedValue(apiError(503));
    renderProviders('/providers');

    const banner = await screen.findByTestId('provider-picker-error');
    expect(banner).toHaveTextContent('일시적으로 사용할 수 없습니다');
  });

  it('surfaces a generic list-failure note on a non-status (network) error', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchProviderList.mockRejectedValue(apiError(undefined));
    renderProviders('/providers');

    const banner = await screen.findByTestId('provider-picker-error');
    expect(banner).toHaveTextContent('불러오지 못했습니다');
  });

  it('shows an empty note when the registry returns no providers', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchProviderList.mockResolvedValue([]);
    renderProviders('/providers');

    expect(await screen.findByTestId('provider-picker-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('provider-picker-error')).not.toBeInTheDocument();
    // The direct-entry lookup form is still available (backend-first list is a
    // convenience, not a gate).
    expect(screen.getByTestId('provider-input')).toBeInTheDocument();
  });

  it('keeps a deep-linked id resolvable even when the list fails to load', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchProviderList.mockRejectedValue(apiError(503));
    platformApi.fetchProviderUiDescriptor.mockResolvedValue(descriptorFor(PROVIDER_A));
    renderProviders(`/providers?provider=${PROVIDER_A}`);

    // List failed, but the deep-linked descriptor lookup still fires and the
    // picker still represents the selection (synthetic option).
    await waitFor(() =>
      expect(platformApi.fetchProviderUiDescriptor).toHaveBeenCalledWith(PROVIDER_A),
    );
    expect(screen.getByTestId('provider-picker')).toHaveValue(PROVIDER_A);
    expect(screen.getByTestId('provider-picker-error')).toBeInTheDocument();
  });
});
