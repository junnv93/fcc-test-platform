/**
 * categorizeQueryStatus — self-healing query-phase SSOT (C2 query-hooks-ssot, 2026-06-17).
 *
 * A live-polling read (chamber progress) distinguishes four phases that plain
 * `isPending`/`isError`/`isSuccess` booleans cannot express on their own,
 * because React Query *retains the last successful data across a transient
 * refetch error*:
 *
 *   - `loading`        — first fetch in flight, nothing to show yet.
 *   - `hardError`      — errored with NO data ever (initial fetch failed) → full
 *                        error state.
 *   - `transientError` — a poll failed but the last-known data is still held →
 *                        a non-destructive note while polling self-heals, with
 *                        the stale metrics kept visible.
 *   - `ready`          — fresh successful data.
 *
 * Both chamber-progress consumers (`ChamberRunRow`, `ChamberProgress`) hand-rolled
 * the `isError && data === undefined` (hard) vs `isError && data !== undefined`
 * (transient) split independently; one edit to either drifted the two views
 * apart. This pure function is the single definition, consumed via
 * `useChamberProgressPolling`.
 */
import { type ApiError } from '@/shared/api-error';

/** The four mutually-exclusive phases of a self-healing polled query. */
export type QueryPhase<TData> =
  | { readonly kind: 'loading' }
  | { readonly kind: 'hardError'; readonly error: ApiError | null }
  | {
      readonly kind: 'transientError';
      readonly data: TData;
      readonly error: ApiError | null;
    }
  | { readonly kind: 'ready'; readonly data: TData };

/** The minimal React Query result surface `categorizeQueryStatus` reads. */
export interface QueryStatusSnapshot<TData> {
  readonly data: TData | undefined;
  readonly isError: boolean;
  readonly error: ApiError | null;
}

/**
 * Collapse a query's `(data, isError, error)` into a single {@link QueryPhase}.
 *
 * Byte-identical to the inlined branches: error-with-no-data is `hardError`,
 * error-with-retained-data is `transientError`, any present data is `ready`, and
 * the remaining state (pending, no data, no error) is `loading`.
 */
export function categorizeQueryStatus<TData>(query: QueryStatusSnapshot<TData>): QueryPhase<TData> {
  if (query.isError && query.data === undefined) {
    return { kind: 'hardError', error: query.error };
  }
  if (query.isError && query.data !== undefined) {
    return { kind: 'transientError', data: query.data, error: query.error };
  }
  if (query.data !== undefined) {
    return { kind: 'ready', data: query.data };
  }
  return { kind: 'loading' };
}

/**
 * List-route counterpart to {@link categorizeQueryStatus} (E2/E4 SSOT).
 *
 * The data-table routes (sessions / jobs / projects) each hand-rolled
 * `isLoading` / `isError` / `isSuccess` branching, which two ways drifted from
 * the self-healing contract:
 *
 *   1. a refetch error with retained data flips `isError` true and `isSuccess`
 *      false, so a naive `{isError && <ErrorState/>}` + `{isSuccess && <Table/>}`
 *      pair *hides the loaded rows behind a hard error* on every transient poll
 *      failure — exactly the regression `categorizeQueryStatus` was written to
 *      prevent for chamber progress.
 *   2. a background refetch (`isFetching` with data) was indistinguishable from
 *      a ready state, so routes could not show a non-destructive "refreshing"
 *      affordance while keeping the stale rows visible.
 *
 * `categorizeListQuery` collapses a list query into the six display states the
 * routes need — initial loading, hard error (no data), transient error (data
 * retained), background refetch (stale-while-revalidate), empty, and ready — so
 * every data route branches on ONE `view.kind` instead of re-deriving the
 * split. Keyset rows are never `undefined` (the hook flattens to `[]`), so the
 * caller supplies `hasLoaded` explicitly rather than inferring it from the
 * array, which lets both `useQuery` and `useKeysetPagination` feed this helper.
 */
export interface ListQuerySnapshot<TItem> {
  /** Loaded rows (flattened for keyset). Empty array is a valid loaded state. */
  readonly items: readonly TItem[];
  /** A successful fetch has populated data at least once (`data !== undefined`
   *  for `useQuery`, `isSuccess` for keyset). Distinguishes "no data yet" from
   *  "loaded but empty". */
  readonly hasLoaded: boolean;
  readonly isError: boolean;
  readonly error: ApiError | null;
  /** Any fetch in flight — used to flag a stale-while-revalidate refetch. */
  readonly isFetching: boolean;
}

/** The display phases of a list query. `ready` carries the orthogonal
 *  `isEmpty` / `isRefetching` / `transientError` flags so a route never has to
 *  re-inspect the raw query booleans. */
export type ListQueryView<TItem> =
  | { readonly kind: 'loading' }
  | { readonly kind: 'hardError'; readonly error: ApiError | null }
  | {
      readonly kind: 'ready';
      readonly items: readonly TItem[];
      readonly isEmpty: boolean;
      /** A background refetch is in flight while the loaded rows stay visible. */
      readonly isRefetching: boolean;
      /** A refetch failed but the last-known rows are retained (non-destructive). */
      readonly transientError: ApiError | null;
    };

/** Collapse a list query's `(items, hasLoaded, isError, error, isFetching)` into
 *  a single {@link ListQueryView}. Before any successful load, an error is a
 *  hard error and anything else is loading; once data has loaded, the view is
 *  always `ready` — an error there is `transientError` (rows kept), a fetch in
 *  flight is `isRefetching`, and an empty array is `isEmpty`. */
export function categorizeListQuery<TItem>(
  snapshot: ListQuerySnapshot<TItem>,
): ListQueryView<TItem> {
  if (!snapshot.hasLoaded) {
    return snapshot.isError ? { kind: 'hardError', error: snapshot.error } : { kind: 'loading' };
  }
  return {
    kind: 'ready',
    items: snapshot.items,
    isEmpty: snapshot.items.length === 0,
    isRefetching: snapshot.isFetching,
    transientError: snapshot.isError ? snapshot.error : null,
  };
}
