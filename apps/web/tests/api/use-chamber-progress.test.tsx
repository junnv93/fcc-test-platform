import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { REFETCH_STRATEGIES } from '@/api/query-config';
import { toApiError } from '@/api/to-api-error';
import {
  chamberProgressRefetchInterval,
  useChamberProgressPolling,
} from '@/api/use-chamber-progress';

import type { ReactNode } from 'react';

/**
 * useChamberProgressPolling SSOT tests — proves the shared chamber-progress
 * polling hook (consumed by both `ChamberRunRow` and `ChamberProgress`) wires
 * the queryFn to the requested chamber id and maps the query into the
 * {@link categorizeQueryStatus} phase: a successful fetch ⇒ `ready` with the
 * snapshot, an initial fetch error (no data) ⇒ `hardError` with the error.
 *
 * The polling cadence + transient self-heal (last-known data retained across a
 * poll error) is exercised end-to-end in `chambers.test.tsx`; this unit test
 * pins the hook's return-shape contract.
 */
const platformApi = vi.hoisted(() => ({
  fetchChamberProgress: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

function wrapper(): (props: { children: ReactNode }) => JSX.Element {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function QueryWrapper({ children }: { children: ReactNode }): JSX.Element {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }
  return QueryWrapper;
}

const snapshot = {
  chamber_id: 'cham-1',
  progress: { is_running: false, completed: 7, total: 10, ratio: 0.7 },
};

beforeEach(() => {
  platformApi.fetchChamberProgress.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('useChamberProgressPolling', () => {
  it('starts in the loading phase before the first fetch resolves', () => {
    // A promise that never settles keeps the query in its initial pending state.
    platformApi.fetchChamberProgress.mockReturnValue(
      new Promise((resolve) => {
        void resolve;
      }),
    );
    const { result } = renderHook(() => useChamberProgressPolling('cham-1'), {
      wrapper: wrapper(),
    });
    expect(result.current.phase.kind).toBe('loading');
    expect(result.current.snapshot).toBeUndefined();
    expect(result.current.error).toBeNull();
  });

  it('fetches the requested chamber and resolves to the ready phase', async () => {
    platformApi.fetchChamberProgress.mockResolvedValue(snapshot);
    const { result } = renderHook(() => useChamberProgressPolling('cham-1'), {
      wrapper: wrapper(),
    });

    await waitFor(() => expect(result.current.phase.kind).toBe('ready'));
    expect(platformApi.fetchChamberProgress).toHaveBeenCalledWith('cham-1');
    expect(result.current.snapshot).toEqual(snapshot);
    if (result.current.phase.kind === 'ready') {
      expect(result.current.phase.data).toEqual(snapshot);
    }
  });

  it('maps an initial fetch error (no data yet) to the hardError phase', async () => {
    const error = toApiError('chamber progress lookup failed', 503);
    platformApi.fetchChamberProgress.mockRejectedValue(error);
    const { result } = renderHook(() => useChamberProgressPolling('cham-1'), {
      wrapper: wrapper(),
    });

    await waitFor(() => expect(result.current.phase.kind).toBe('hardError'));
    expect(result.current.snapshot).toBeUndefined();
    expect(result.current.error).toBe(error);
  });
});

/**
 * refetchInterval policy SSOT — seals the three polling-cadence branches
 * directly (independent of React Query timing) so a future edit cannot silently
 * drift the self-healing cadence: an active run polls on the CRITICAL cadence,
 * an initial fetch error (no data yet) keeps retrying on the same cadence rather
 * than parking forever, and a terminal not-running success stops polling.
 */
describe('chamberProgressRefetchInterval', () => {
  const running = {
    chamber_id: 'cham-1',
    progress: { is_running: true, completed: 3, total: 10, ratio: 0.3 },
  };
  const done = {
    chamber_id: 'cham-1',
    progress: { is_running: false, completed: 10, total: 10, ratio: 1 },
  };

  it('polls on the CRITICAL cadence while the run is active', () => {
    expect(
      chamberProgressRefetchInterval({ data: running, status: 'success', dataUpdatedAt: 1 }),
    ).toBe(REFETCH_STRATEGIES.CRITICAL.refetchInterval);
  });

  it('keeps retrying on the CRITICAL cadence on an initial error with no data', () => {
    expect(
      chamberProgressRefetchInterval({ data: undefined, status: 'error', dataUpdatedAt: 0 }),
    ).toBe(REFETCH_STRATEGIES.CRITICAL.refetchInterval);
  });

  it('stops polling on a terminal not-running success', () => {
    expect(
      chamberProgressRefetchInterval({ data: done, status: 'success', dataUpdatedAt: 1 }),
    ).toBe(false);
  });
});
