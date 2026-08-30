import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useOptimisticMutation } from '@/shared/use-optimistic-mutation';

import type { ReactNode } from 'react';

/**
 * useOptimisticMutation — optimistic write SSOT tests (fe-optimistic-mutation-hook,
 * Increment 3). Proves the contract: onMutate applies the optimistic transform to
 * the cache immediately (cancel + snapshot + setQueryData), onError rolls the
 * cache back to the snapshot, onSettled invalidates the read so it reconciles
 * with central truth — and the fallback (no optimisticUpdate) touches nothing on
 * mutate, invalidates ONLY on success (never on error), and never calls
 * setQueryData (byte-identical to the old `onSuccess: invalidateQueries` path).
 */

interface CachedList {
  readonly items: readonly string[];
}

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function wrapperFor(client: QueryClient): (props: { children: ReactNode }) => JSX.Element {
  return function QueryWrapper({ children }: { children: ReactNode }): JSX.Element {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

const KEY = ['list', 'p1'] as const;

describe('useOptimisticMutation — optimistic apply', () => {
  it('applies the optimistic transform to the cache on mutate (before the write resolves)', async () => {
    const client = makeClient();
    client.setQueryData<CachedList>([...KEY], { items: ['a'] });

    // A write that never settles until we release it — so we can observe the
    // optimistic state while the mutation is still pending.
    const deferred: { resolve: (value: { id: string }) => void } = {
      resolve: () => undefined,
    };
    const mutationFn = vi.fn(
      () =>
        new Promise<{ id: string }>((resolve) => {
          deferred.resolve = resolve;
        }),
    );

    const { result } = renderHook(
      () =>
        useOptimisticMutation<{ id: string }, { value: string }, CachedList>({
          mutationFn,
          queryKey: [...KEY],
          optimisticUpdate: (current, v) => ({
            items: [...(current?.items ?? []), v.value],
          }),
        }),
      { wrapper: wrapperFor(client) },
    );

    result.current.mutate({ value: 'b' });

    // Cache reflects the optimistic insert immediately — the response ({id:'b'})
    // is NOT what landed in the cache; the transform's variable ('b') is.
    await waitFor(() => {
      expect(client.getQueryData<CachedList>([...KEY])).toEqual({ items: ['a', 'b'] });
    });

    deferred.resolve({ id: 'b' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('can optimistically update more than one cache target from one write', async () => {
    const client = makeClient();
    const siblingKey = ['list', 'summary'] as const;
    client.setQueryData<CachedList>([...KEY], { items: ['a'] });
    client.setQueryData<CachedList>([...siblingKey], { items: ['1'] });

    const deferred: { resolve: (value: { id: string }) => void } = {
      resolve: () => undefined,
    };
    const mutationFn = vi.fn(
      () =>
        new Promise<{ id: string }>((resolve) => {
          deferred.resolve = resolve;
        }),
    );

    const { result } = renderHook(
      () =>
        useOptimisticMutation<{ id: string }, { value: string }, CachedList>({
          mutationFn,
          queryKey: [...KEY],
          optimisticUpdate: (current, v) => ({
            items: [...(current?.items ?? []), v.value],
          }),
          extraOptimisticUpdates: [
            {
              queryKey: [...siblingKey],
              optimisticUpdate: (current, v) => ({
                items: [...((current as CachedList | undefined)?.items ?? []), v.value],
              }),
            },
          ],
        }),
      { wrapper: wrapperFor(client) },
    );

    result.current.mutate({ value: 'b' });

    await waitFor(() => {
      expect(client.getQueryData<CachedList>([...KEY])).toEqual({ items: ['a', 'b'] });
      expect(client.getQueryData<CachedList>([...siblingKey])).toEqual({ items: ['1', 'b'] });
    });

    deferred.resolve({ id: 'b' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });
});

describe('useOptimisticMutation — error rollback', () => {
  it('restores the snapshot when the write fails', async () => {
    const client = makeClient();
    client.setQueryData<CachedList>([...KEY], { items: ['a'] });

    const mutationFn = vi.fn(() =>
      Promise.reject(Object.assign(new Error('boom'), { status: 409 })),
    );

    const { result } = renderHook(
      () =>
        useOptimisticMutation<{ id: string }, { value: string }, CachedList>({
          mutationFn,
          queryKey: [...KEY],
          optimisticUpdate: (current, v) => ({
            items: [...(current?.items ?? []), v.value],
          }),
        }),
      { wrapper: wrapperFor(client) },
    );

    result.current.mutate({ value: 'b' });

    await waitFor(() => expect(result.current.isError).toBe(true));
    // Rolled back to the pre-optimistic snapshot.
    expect(client.getQueryData<CachedList>([...KEY])).toEqual({ items: ['a'] });
    // The ApiError shape (HTTP status) survives to the route's error branch.
    expect(result.current.error?.status).toBe(409);
  });

  it('rolls back every optimistic target when a multi-cache write fails', async () => {
    const client = makeClient();
    const siblingKey = ['list', 'summary'] as const;
    client.setQueryData<CachedList>([...KEY], { items: ['a'] });
    client.setQueryData<CachedList>([...siblingKey], { items: ['1'] });

    const { result } = renderHook(
      () =>
        useOptimisticMutation<{ id: string }, { value: string }, CachedList>({
          mutationFn: () => Promise.reject(Object.assign(new Error('boom'), { status: 409 })),
          queryKey: [...KEY],
          optimisticUpdate: (current, v) => ({
            items: [...(current?.items ?? []), v.value],
          }),
          extraOptimisticUpdates: [
            {
              queryKey: [...siblingKey],
              optimisticUpdate: (current, v) => ({
                items: [...((current as CachedList | undefined)?.items ?? []), v.value],
              }),
            },
          ],
        }),
      { wrapper: wrapperFor(client) },
    );

    result.current.mutate({ value: 'b' });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(client.getQueryData<CachedList>([...KEY])).toEqual({ items: ['a'] });
    expect(client.getQueryData<CachedList>([...siblingKey])).toEqual({ items: ['1'] });
  });
});

describe('useOptimisticMutation — settle invalidation', () => {
  it('invalidates the query key on settle (success)', async () => {
    const client = makeClient();
    client.setQueryData<CachedList>([...KEY], { items: ['a'] });
    const invalidate = vi.spyOn(client, 'invalidateQueries');

    const { result } = renderHook(
      () =>
        useOptimisticMutation<{ id: string }, { value: string }, CachedList>({
          mutationFn: () => Promise.resolve({ id: 'b' }),
          queryKey: [...KEY],
          optimisticUpdate: (current, v) => ({ items: [...(current?.items ?? []), v.value] }),
        }),
      { wrapper: wrapperFor(client) },
    );

    result.current.mutate({ value: 'b' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: [...KEY] });
  });

  it('invalidates explicit invalidateKeys instead of just the queryKey', async () => {
    const client = makeClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const extra = ['list', 'sync'] as const;

    const { result } = renderHook(
      () =>
        useOptimisticMutation<{ id: string }, { value: string }, CachedList>({
          mutationFn: () => Promise.resolve({ id: 'b' }),
          queryKey: [...KEY],
          invalidateKeys: [[...KEY], [...extra]],
          optimisticUpdate: (current, v) => ({ items: [...(current?.items ?? []), v.value] }),
        }),
      { wrapper: wrapperFor(client) },
    );

    result.current.mutate({ value: 'b' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: [...KEY] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: [...extra] });
  });
});

describe('useOptimisticMutation — fallback (no optimisticUpdate)', () => {
  it('touches no cache on mutate but still invalidates on success (central-write-then-refresh)', async () => {
    const client = makeClient();
    client.setQueryData<CachedList>([...KEY], { items: ['a'] });
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const setData = vi.spyOn(client, 'setQueryData');

    const { result } = renderHook(
      () =>
        useOptimisticMutation<{ id: string }, { value: string }, CachedList>({
          mutationFn: () => Promise.resolve({ id: 'b' }),
          queryKey: [...KEY],
          // no optimisticUpdate → fallback mode
        }),
      { wrapper: wrapperFor(client) },
    );

    result.current.mutate({ value: 'b' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // Cache was never optimistically written — unchanged until the (mocked) read
    // refetches. The hook itself made no setQueryData call.
    expect(client.getQueryData<CachedList>([...KEY])).toEqual({ items: ['a'] });
    expect(setData).not.toHaveBeenCalled();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: [...KEY] });
  });

  it('does NOT invalidate on error (byte-identical to old onSuccess: invalidateQueries)', async () => {
    const client = makeClient();
    client.setQueryData<CachedList>([...KEY], { items: ['a'] });
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const setData = vi.spyOn(client, 'setQueryData');

    const { result } = renderHook(
      () =>
        useOptimisticMutation<{ id: string }, { value: string }, CachedList>({
          mutationFn: () => Promise.reject(Object.assign(new Error('boom'), { status: 500 })),
          queryKey: [...KEY],
          // no optimisticUpdate → fallback mode
        }),
      { wrapper: wrapperFor(client) },
    );

    result.current.mutate({ value: 'b' });
    await waitFor(() => expect(result.current.isError).toBe(true));

    // The old central-write-then-refresh path only invalidated on success.
    // A failed write has nothing to reconcile (it never landed centrally), so
    // the fallback must NOT invalidate — and never optimistically wrote anyway.
    expect(setData).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
    expect(result.current.error?.status).toBe(500);
  });
});
