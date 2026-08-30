import { useInfiniteQuery, type QueryKey } from '@tanstack/react-query';
import { useCallback, useMemo } from 'react';

/**
 * useKeysetPagination — shared keyset data-layer SSOT (web-keyset-pagination-ssot,
 * 2026-06-13).
 *
 * The platform read routes (coverage / claims / membership) and the headless
 * session route (attempts) all page over an opaque keyset cursor and then
 * flatten the loaded pages. Each route hand-rolled the same
 * `useInfiniteQuery({ initialPageParam, getNextPageParam })` +
 * `data?.pages.flatMap((p) => p.items) ?? []` boilerplate. This hook owns that
 * pattern once so the cursor convention and the flatten cannot drift per route.
 *
 * The two page shapes differ — platform pages carry `nextCursor` (from the
 * `X-Next-Cursor` header helper), headless pages carry `next_cursor` — so the
 * caller supplies a `getNextCursor` adapter rather than the hook hardcoding a
 * field name. The fetch closure captures the route's id / facet, so the hook
 * stays agnostic to the request signature.
 *
 * `fetchNextPage` is a STABLE reference (useCallback over React Query's stable
 * `fetchNextPage`) so an auto-advance effect (claims) can depend on it without
 * re-firing every render.
 */
export interface KeysetPaginationOptions<TPage> {
  readonly queryKey: QueryKey;
  /** When `false`, the query is disabled (no fetch) — e.g. an invalid id. */
  readonly enabled?: boolean;
  /** Fetch one page; `cursor === undefined` requests the first page. */
  readonly fetchPage: (cursor?: string) => Promise<TPage>;
  /** Extract the next-page cursor from a page (`undefined` ⇒ last page). */
  readonly getNextCursor: (page: TPage) => string | undefined;
}

export interface KeysetPaginationResult<T> {
  /** All loaded pages flattened in order. */
  readonly rows: T[];
  readonly isLoading: boolean;
  readonly isError: boolean;
  readonly isSuccess: boolean;
  /** Any fetch in flight (first load, refetch, OR next-page). Lets the
   *  query-view SSOT flag a stale-while-revalidate refetch. */
  readonly isFetching: boolean;
  readonly error: unknown;
  readonly hasNextPage: boolean;
  readonly isFetchingNextPage: boolean;
  /** Load the next page. Stable reference across renders. */
  readonly fetchNextPage: () => void;
  /** Number of pages loaded so far (for an auto-advance circuit breaker). */
  readonly pageCount: number;
}

export function useKeysetPagination<T, TPage extends { items: readonly T[] }>(
  options: KeysetPaginationOptions<TPage>,
): KeysetPaginationResult<T> {
  const { queryKey, enabled, fetchPage, getNextCursor } = options;
  const query = useInfiniteQuery({
    queryKey,
    ...(enabled !== undefined ? { enabled } : {}),
    queryFn: ({ pageParam }) => fetchPage(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last: TPage) => getNextCursor(last),
  });

  const rows = useMemo(
    () => query.data?.pages.flatMap((page) => [...page.items]) ?? [],
    [query.data],
  );

  // React Query guarantees `fetchNextPage` identity is stable, so this callback
  // stays stable too — safe as an effect dependency.
  const { fetchNextPage: queryFetchNextPage } = query;
  const fetchNextPage = useCallback(() => {
    void queryFetchNextPage();
  }, [queryFetchNextPage]);

  return {
    rows,
    isLoading: query.isLoading,
    isError: query.isError,
    isSuccess: query.isSuccess,
    isFetching: query.isFetching,
    error: query.error,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage,
    pageCount: query.data?.pages.length ?? 0,
  };
}
