import { QueryClient } from '@tanstack/react-query';

import { REFETCH_STRATEGIES } from '@/api/query-config';

/**
 * TanStack Query SSOT — singleton QueryClient with sensible defaults.
 *
 * - `retry: 1` — backend 4xx (handled by status_token from backend OBS-2)
 *   should not silently retry; only 5xx/network gets one retry.
 * - default refetch tuning delegates to `REFETCH_STRATEGIES.NORMAL`
 *   (fe-query-key-cache-ssot SSOT): operator views tolerate slightly stale
 *   data (WS events invalidate explicitly when real-time matters) and avoid
 *   a refetch burst on window focus. Override per-query with another strategy
 *   when liveness matters (e.g. `CRITICAL` for active-run polling).
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        // Retry network errors / 5xx once; never retry 4xx.
        const status = extractHttpStatus(error);
        if (status !== null && status >= 400 && status < 500) return false;
        return failureCount < 1;
      },
      retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 8000),
      staleTime: REFETCH_STRATEGIES.NORMAL.staleTime,
      gcTime: REFETCH_STRATEGIES.NORMAL.gcTime,
      refetchInterval: REFETCH_STRATEGIES.NORMAL.refetchInterval,
      refetchOnWindowFocus: REFETCH_STRATEGIES.NORMAL.refetchOnWindowFocus,
      refetchOnReconnect: 'always',
      networkMode: 'online',
    },
    mutations: {
      retry: 0,
      networkMode: 'online',
    },
  },
});

function extractHttpStatus(error: unknown): number | null {
  if (error && typeof error === 'object') {
    const record = error as Record<string, unknown>;
    const status = record['status'] ?? record['statusCode'];
    if (typeof status === 'number') return status;
  }
  return null;
}
