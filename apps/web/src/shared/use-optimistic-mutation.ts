import {
  useMutation,
  useQueryClient,
  type QueryKey,
  type UseMutationResult,
} from '@tanstack/react-query';

import type { ApiError } from '@/shared/api-error';

/**
 * useOptimisticMutation — optimistic write SSOT (fe-optimistic-mutation-hook,
 * Increment 3, 2026-06-13).
 *
 * Before this hook every `apps/web` write route (claim acquire/release,
 * membership assign/revoke) was *central-write-then-refresh*: the route fired
 * the mutation and, only on success, `invalidateQueries`'d the read so the UI
 * caught up after a full server round-trip. The operator saw nothing change
 * until the refetch landed. The equipment-management platform's
 * `use-optimistic-mutation` hook proves the better pattern — apply the change to
 * the cache immediately, roll back on error, and reconcile with central truth on
 * settle.
 *
 * This hook owns that pattern once so each route does not re-roll (and cannot
 * drift) the cancel → snapshot → setQueryData → rollback → invalidate dance:
 *
 *   - `onMutate`  — `cancelQueries` (so an in-flight refetch can't clobber the
 *                   optimistic value) → snapshot the current cache → apply
 *                   `optimisticUpdate` via `setQueryData` (functional updater).
 *   - `onError`   — restore every snapshotted entry (rollback).
 *   - `onSettled` — (optimistic mode only) invalidate `invalidateKeys` (default
 *                   `[queryKey]`) so the cache reconciles with central state
 *                   regardless of outcome (success confirms the optimistic value,
 *                   error re-fetches over the rolled-back cache).
 *
 * Type safety (TData ≠ TCachedData): the mutation *response* type (`TData`,
 * e.g. `ClaimEventEnvelope`) is NOT the cache shape (`TCached`, e.g.
 * `InfiniteData<PlatformPage<ActiveClaimEnvelope>>`). The cache is therefore
 * NEVER written by assigning the response (`setQueryData(key, responseData)`) —
 * the only write is `optimisticUpdate(current, variables)`, derived from the
 * mutation *variables*. Rollback restores the captured snapshot (already
 * `TCached`). The companion seal (`tests/test_frontend_optimistic_mutation.py`)
 * proves routes never call `setQueryData` directly.
 *
 * Fallback (byte-identical to the old behavior): when `optimisticUpdate` is
 * omitted the hook performs NO cancel/snapshot/setQueryData — it is a plain
 * central-write-then-refresh mutation that invalidates ONLY on success
 * (mutate → onSuccess invalidate), exactly like the old `onSuccess:
 * invalidateQueries` routes. An error invalidates nothing (no central round-trip
 * to reconcile against — the write never landed). This differs from optimistic
 * mode, where settle-time reconciliation must run on error too to re-fetch over
 * the rolled-back cache. A route can adopt the hook for the shared invalidation
 * wiring without going optimistic.
 */

/** How the optimistic write addresses the cache. */
export type OptimisticUpdateScope = 'exact' | 'matching';

/** One optimistic cache target. The primary `queryKey` + `optimisticUpdate`
 *  pair remains first-class, but some routes need one write to update more than
 *  one read model immediately (for example, a draft detail row set AND its
 *  sibling drafts-list row_count summary). `extraOptimisticUpdates` keeps that
 *  multi-target shape inside the shared hook rather than forcing each route to
 *  re-roll cancel/snapshot/rollback for the second key. */
export interface OptimisticMutationTarget<TVariables> {
  readonly queryKey: QueryKey;
  readonly optimisticUpdate: (current: unknown, variables: TVariables) => unknown;
  readonly optimisticUpdateScope?: OptimisticUpdateScope;
}

/**
 * Snapshot of the cache captured in `onMutate`, threaded to `onError` for
 * rollback. `[key, data]` pairs — one for `exact`, possibly many for `matching`
 * (every query whose key prefix-matches `queryKey`). Empty when no
 * `optimisticUpdate` was supplied (fallback mode — nothing to roll back).
 */
export interface OptimisticMutationContext<TCached> {
  readonly previous: readonly (readonly [QueryKey, TCached | undefined])[];
}

export interface OptimisticMutationOptions<TData, TVariables, TCached> {
  /** The write itself. Throws an {@link ApiError} (HTTP `status` decorated). */
  readonly mutationFn: (variables: TVariables) => Promise<TData>;
  /** Read cache key whose value reflects this write — from the
   *  `@/api/query-config` `queryKeys` factory (Increment 1), never inline. */
  readonly queryKey: QueryKey;
  /**
   * Pure cache transform: `(current cache value, mutation variables) → next`.
   * Receives the *variables* (not the response) so it can run before the server
   * answers. Omit for central-write-then-refresh (fallback, byte-identical).
   */
  readonly optimisticUpdate?: (
    current: TCached | undefined,
    variables: TVariables,
  ) => TCached | undefined;
  /** `'exact'` (default) writes the exact key; `'matching'` writes every query
   *  whose key prefix-matches `queryKey` (`setQueriesData`). */
  readonly optimisticUpdateScope?: OptimisticUpdateScope;
  /** Additional optimistic cache targets. Used when one write immediately
   *  changes more than one read model; each target is still reconciled through
   *  the same rollback + invalidate path. */
  readonly extraOptimisticUpdates?: readonly OptimisticMutationTarget<TVariables>[];
  /** Keys invalidated on settle. Defaults to `[queryKey]`. */
  readonly invalidateKeys?: readonly QueryKey[];
  /** Optional success side effect (e.g. analytics). The success *message* stays
   *  the route's concern (shared `selectLatestWriteMessage` selector). */
  readonly onSuccess?: (data: TData, variables: TVariables) => void;
}

export function useOptimisticMutation<TData, TVariables, TCached = unknown>(
  options: OptimisticMutationOptions<TData, TVariables, TCached>,
): UseMutationResult<TData, ApiError, TVariables, OptimisticMutationContext<TCached>> {
  const queryClient = useQueryClient();
  const {
    mutationFn,
    queryKey,
    optimisticUpdate,
    optimisticUpdateScope = 'exact',
    extraOptimisticUpdates = [],
    invalidateKeys,
    onSuccess,
  } = options;

  // Invalidate the reconciliation keys (default `[queryKey]`). Shared by both
  // modes; only the *trigger* differs (optimistic → onSettled, fallback → onSuccess).
  const invalidate = (): void => {
    for (const key of invalidateKeys ?? [queryKey]) {
      void queryClient.invalidateQueries({ queryKey: key });
    }
  };

  return useMutation<TData, ApiError, TVariables, OptimisticMutationContext<TCached>>({
    mutationFn,
    onMutate: async (variables) => {
      // Fallback mode — no transform supplied → plain central-write-then-refresh
      // (no cache touched here; reconciliation happens in onSettled).
      const targets: readonly OptimisticMutationTarget<TVariables>[] = [
        ...(optimisticUpdate
          ? [
              {
                queryKey,
                optimisticUpdate: (current: unknown, nextVariables: TVariables): unknown =>
                  optimisticUpdate(current as TCached | undefined, nextVariables),
                optimisticUpdateScope,
              } satisfies OptimisticMutationTarget<TVariables>,
            ]
          : []),
        ...extraOptimisticUpdates,
      ];
      if (targets.length === 0) return { previous: [] };

      const cancelled = new Set<string>();
      const previous: (readonly [QueryKey, TCached | undefined])[] = [];
      for (const target of targets) {
        const fingerprint = JSON.stringify(target.queryKey);
        // One write can fan out to multiple reads; cancel each distinct refetch
        // once so a stale server response cannot clobber any optimistic branch.
        if (!cancelled.has(fingerprint)) {
          await queryClient.cancelQueries({ queryKey: target.queryKey });
          cancelled.add(fingerprint);
        }

        if ((target.optimisticUpdateScope ?? 'exact') === 'matching') {
          const snapshots = queryClient.getQueriesData<TCached>({
            queryKey: target.queryKey,
          });
          queryClient.setQueriesData({ queryKey: target.queryKey }, (current) =>
            target.optimisticUpdate(current, variables),
          );
          previous.push(...snapshots);
          continue;
        }

        const current = queryClient.getQueryData<TCached>(target.queryKey);
        queryClient.setQueryData(target.queryKey, (old) => target.optimisticUpdate(old, variables));
        previous.push([target.queryKey, current]);
      }
      return { previous };
    },
    onError: (_error, _variables, context) => {
      // Roll back every snapshotted entry to its pre-optimistic value. Restoring
      // a captured TCached snapshot — never the (differently-shaped) response.
      for (const [key, data] of context?.previous ?? []) {
        queryClient.setQueryData(key, data);
      }
    },
    onSuccess: (data, variables) => {
      onSuccess?.(data, variables);
      // Fallback mode invalidates ONLY on success (byte-identical to the old
      // `onSuccess: invalidateQueries` central-write-then-refresh routes).
      // Optimistic mode defers invalidation to onSettled (runs on error too).
      if (!optimisticUpdate) invalidate();
    },
    // Optimistic mode only: reconcile with central truth regardless of outcome
    // (success → confirm the optimistic value; error → re-fetch over the
    // rolled-back cache). Fallback mode omits this — see onSuccess above.
    ...(optimisticUpdate ? { onSettled: () => invalidate() } : {}),
  });
}
