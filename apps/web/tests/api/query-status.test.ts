import { describe, expect, it } from 'vitest';

import { categorizeQueryStatus } from '@/api/query-status';
import { toApiError } from '@/api/to-api-error';

/**
 * categorizeQueryStatus SSOT tests — proves the self-healing phase split is
 * byte-identical to the hand-rolled branches the two chamber-progress views
 * inlined: error+no-data ⇒ `hardError`, error+retained-data ⇒ `transientError`
 * (the last-known data stays visible while polling self-heals), present data ⇒
 * `ready`, and the remaining pending/no-data state ⇒ `loading`.
 */
describe('categorizeQueryStatus', () => {
  const snapshot = { progress: { is_running: true, completed: 1, total: 4, ratio: 0.25 } };

  it('loading — pending, no data, no error', () => {
    expect(categorizeQueryStatus({ data: undefined, isError: false, error: null })).toEqual({
      kind: 'loading',
    });
  });

  it('ready — fresh successful data', () => {
    expect(categorizeQueryStatus({ data: snapshot, isError: false, error: null })).toEqual({
      kind: 'ready',
      data: snapshot,
    });
  });

  it('hardError — errored with NO data ever (initial fetch failed)', () => {
    const error = toApiError('progress failed', 503);
    expect(categorizeQueryStatus({ data: undefined, isError: true, error })).toEqual({
      kind: 'hardError',
      error,
    });
  });

  it('transientError — a poll failed but the last-known data is retained', () => {
    const error = toApiError('progress blip', undefined);
    expect(categorizeQueryStatus({ data: snapshot, isError: true, error })).toEqual({
      kind: 'transientError',
      data: snapshot,
      error,
    });
  });

  it('the four phases are mutually exclusive across the (data × isError) matrix', () => {
    const e = toApiError('x', 500);
    expect(categorizeQueryStatus({ data: undefined, isError: false, error: null }).kind).toBe(
      'loading',
    );
    expect(categorizeQueryStatus({ data: snapshot, isError: false, error: null }).kind).toBe(
      'ready',
    );
    expect(categorizeQueryStatus({ data: undefined, isError: true, error: e }).kind).toBe(
      'hardError',
    );
    expect(categorizeQueryStatus({ data: snapshot, isError: true, error: e }).kind).toBe(
      'transientError',
    );
  });
});
