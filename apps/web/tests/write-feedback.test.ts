import { describe, expect, it } from 'vitest';

import { selectLatestWriteMessage } from '@/shared/write-feedback';

describe('selectLatestWriteMessage', () => {
  it('returns null when no entry has succeeded', () => {
    expect(
      selectLatestWriteMessage([
        { isSuccess: false, submittedAt: 5, message: 'a' },
        { isSuccess: false, submittedAt: 9, message: 'b' },
      ]),
    ).toBeNull();
  });

  it('returns the single successful entry message', () => {
    expect(
      selectLatestWriteMessage([
        { isSuccess: false, submittedAt: 5, message: 'a' },
        { isSuccess: true, submittedAt: 9, message: 'b' },
      ]),
    ).toBe('b');
  });

  it('picks the most-recently-submitted success when two succeeded', () => {
    // assign succeeded earlier (t=10); revoke succeeded later (t=20) → revoke wins.
    expect(
      selectLatestWriteMessage([
        { isSuccess: true, submittedAt: 10, message: 'assign' },
        { isSuccess: true, submittedAt: 20, message: 'revoke' },
      ]),
    ).toBe('revoke');
    // order independence: same data, reversed input order, same winner.
    expect(
      selectLatestWriteMessage([
        { isSuccess: true, submittedAt: 20, message: 'revoke' },
        { isSuccess: true, submittedAt: 10, message: 'assign' },
      ]),
    ).toBe('revoke');
  });

  it('ignores a non-success entry even if it is newer', () => {
    // a newer-but-failed write must not mask the older successful one.
    expect(
      selectLatestWriteMessage([
        { isSuccess: true, submittedAt: 10, message: 'ok' },
        { isSuccess: false, submittedAt: 30, message: 'failed' },
      ]),
    ).toBe('ok');
  });

  it('keeps the first occurrence on a timestamp tie (deterministic)', () => {
    expect(
      selectLatestWriteMessage([
        { isSuccess: true, submittedAt: 10, message: 'first' },
        { isSuccess: true, submittedAt: 10, message: 'second' },
      ]),
    ).toBe('first');
  });
});
