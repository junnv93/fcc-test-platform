import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useKeysetPagination } from '@/shared/use-keyset-pagination';

import type { ReactNode } from 'react';

/**
 * useKeysetPagination — shared keyset data-layer SSOT tests. Proves the hook
 * flattens loaded pages, advances by the supplied cursor adapter (works for both
 * the platform `nextCursor` and headless `next_cursor` conventions), and keeps a
 * stable fetchNextPage reference (so a claims-style auto-advance effect is safe).
 */

function wrapper(): (props: { children: ReactNode }) => JSX.Element {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function QueryWrapper({ children }: { children: ReactNode }): JSX.Element {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }
  return QueryWrapper;
}

interface PlatformLikePage {
  items: readonly number[];
  nextCursor: string | null;
}

describe('useKeysetPagination', () => {
  it('flattens loaded pages into rows', async () => {
    const fetchPage = vi.fn(() => Promise.resolve({ items: [1, 2, 3], nextCursor: null }));
    const { result } = renderHook(
      () =>
        useKeysetPagination<number, PlatformLikePage>({
          queryKey: ['k1'],
          fetchPage,
          getNextCursor: (page) => page.nextCursor ?? undefined,
        }),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.rows).toEqual([1, 2, 3]);
    expect(result.current.hasNextPage).toBe(false);
    expect(result.current.pageCount).toBe(1);
  });

  it('advances to the next page via the cursor adapter and concatenates rows', async () => {
    const fetchPage = vi.fn((cursor?: string) =>
      Promise.resolve(
        cursor === undefined
          ? { items: [1, 2], nextCursor: 'c1' }
          : { items: [3, 4], nextCursor: null },
      ),
    );
    const { result } = renderHook(
      () =>
        useKeysetPagination<number, PlatformLikePage>({
          queryKey: ['k2'],
          fetchPage,
          getNextCursor: (page) => page.nextCursor ?? undefined,
        }),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect(result.current.rows).toEqual([1, 2]));
    expect(result.current.hasNextPage).toBe(true);

    result.current.fetchNextPage();
    await waitFor(() => expect(result.current.rows).toEqual([1, 2, 3, 4]));
    expect(result.current.hasNextPage).toBe(false);
    expect(result.current.pageCount).toBe(2);
    // The second fetch was called with the first page's cursor.
    expect(fetchPage).toHaveBeenLastCalledWith('c1');
  });

  it('works with the headless next_cursor convention via the adapter', async () => {
    interface HeadlessLikePage {
      items: readonly string[];
      next_cursor: string | null;
    }
    const fetchPage = vi.fn(() => Promise.resolve({ items: ['a'], next_cursor: null }));
    const { result } = renderHook(
      () =>
        useKeysetPagination<string, HeadlessLikePage>({
          queryKey: ['k3'],
          fetchPage,
          getNextCursor: (page) => page.next_cursor ?? undefined,
        }),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect(result.current.rows).toEqual(['a']));
  });

  it('does not fetch when disabled', () => {
    const fetchPage = vi.fn(() => Promise.resolve({ items: [], nextCursor: null }));
    renderHook(
      () =>
        useKeysetPagination<number, PlatformLikePage>({
          queryKey: ['k4'],
          enabled: false,
          fetchPage,
          getNextCursor: (page) => page.nextCursor ?? undefined,
        }),
      { wrapper: wrapper() },
    );
    expect(fetchPage).not.toHaveBeenCalled();
  });

  it('keeps a stable fetchNextPage reference across re-renders', async () => {
    const fetchPage = vi.fn(() => Promise.resolve({ items: [1], nextCursor: null }));
    const { result, rerender } = renderHook(
      () =>
        useKeysetPagination<number, PlatformLikePage>({
          queryKey: ['k5'],
          fetchPage,
          getNextCursor: (page) => page.nextCursor ?? undefined,
        }),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const first = result.current.fetchNextPage;
    rerender();
    expect(result.current.fetchNextPage).toBe(first);
  });
});
