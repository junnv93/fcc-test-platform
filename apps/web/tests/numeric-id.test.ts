import { describe, expect, it } from 'vitest';

import { parsePositiveId } from '@/shared/numeric-id';

/**
 * Numeric positive-id parsing SSOT seal (fe-error-id-ssot, 2026-06-14).
 *
 * The single definition consumed by reports.tsx (request id) and sessions.tsx
 * (session id via parseSessionParam). Replaces the two byte-identical inline
 * copies that previously drifted under different names.
 */
describe('parsePositiveId', () => {
  it('accepts positive integers (trimmed)', () => {
    expect(parsePositiveId('5')).toBe(5);
    expect(parsePositiveId('  42 ')).toBe(42);
  });

  it('rejects non-positive / non-integer / non-numeric input', () => {
    expect(parsePositiveId('0')).toBeNull();
    expect(parsePositiveId('-1')).toBeNull();
    expect(parsePositiveId('1.5')).toBeNull();
    expect(parsePositiveId('abc')).toBeNull();
    expect(parsePositiveId('')).toBeNull();
  });

  it('rejects ids that lose precision past MAX_SAFE_INTEGER', () => {
    // Passes /^\d+$/ but Number() silently rounds — must be rejected so a wrong
    // id never reaches the API.
    expect(parsePositiveId('99999999999999999')).toBeNull();
  });
});
