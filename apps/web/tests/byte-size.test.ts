import { describe, expect, it } from 'vitest';

import { formatByteSize } from '@/shared/byte-size';

/**
 * `formatByteSize` renders a server-supplied byte count (session-workbook-upload-ui, 2026-09-01).
 *
 * The unit under test is a *renderer*, and the property that matters is that it
 * never invents a number: every input comes from the wire (a 413's
 * `params.max`, an upload response's `size_bytes`). So the tests below check
 * fidelity and totality, not policy.
 */
describe('formatByteSize', () => {
  it('uses binary units, matching the backend ceiling it renders', () => {
    // The backend's default is literally `64 * 1024 * 1024`. Rendering that as
    // "67.1 MB" would print a number no runbook contains.
    expect(formatByteSize(64 * 1024 * 1024)).toBe('64 MiB');
    expect(formatByteSize(1024)).toBe('1 KiB');
    expect(formatByteSize(1024 * 1024 * 1024)).toBe('1 GiB');
  });

  it('keeps whole values whole and fractional values to one decimal', () => {
    expect(formatByteSize(1536)).toBe('1.5 KiB');
    expect(formatByteSize(8 * 1024 * 1024)).toBe('8 MiB');
  });

  it('never shows a fractional byte', () => {
    expect(formatByteSize(0)).toBe('0 B');
    expect(formatByteSize(1)).toBe('1 B');
    expect(formatByteSize(1023)).toBe('1023 B');
  });

  it('stops at the largest unit it knows rather than inventing one', () => {
    expect(formatByteSize(5 * 1024 ** 4)).toBe('5 TiB');
    expect(formatByteSize(5 * 1024 ** 5)).toBe('5120 TiB');
  });

  it('is total — an unusable count yields undefined, never "NaN MiB"', () => {
    expect(formatByteSize(Number.NaN)).toBeUndefined();
    expect(formatByteSize(Number.POSITIVE_INFINITY)).toBeUndefined();
    expect(formatByteSize(-1)).toBeUndefined();
  });

  it('is monotone — a larger count never renders as a smaller quantity', () => {
    // Guards the unit-stepping loop: an off-by-one in the exponent would make
    // 1 MiB print as "1 KiB", i.e. smaller than the 900 KiB below it. Checked by
    // reading the rendering BACK to a byte count, so the property is about what
    // the operator sees rather than about the input we already sorted.
    const UNIT_EXP: Readonly<Record<string, number>> = {
      B: 0,
      KiB: 1,
      MiB: 2,
      GiB: 3,
      TiB: 4,
    };
    const readBack = (n: number): number => {
      const rendered = formatByteSize(n);
      // A real narrowing rather than an assertion: the throw is also the more
      // useful failure, since it names the input that produced nothing.
      if (rendered === undefined) throw new Error(`formatByteSize(${n}) rendered nothing`);
      const [value, unit] = rendered.split(' ');
      const exponent = unit === undefined ? undefined : UNIT_EXP[unit];
      if (exponent === undefined) throw new Error(`unknown unit in "${rendered}"`);
      return Number(value) * 1024 ** exponent;
    };

    const samples = [0, 1, 1023, 1024, 1536, 900 * 1024, 1024 ** 2, 1024 ** 3, 1024 ** 4];
    const seen = samples.map(readBack);
    expect(seen).toEqual([...seen].sort((a, b) => a - b));
    // …and the read-back is faithful, not merely ordered: an exact power of two
    // must round-trip, so the assertion above cannot be satisfied by a renderer
    // that collapses everything to one unit.
    expect(readBack(1024 ** 2)).toBe(1024 ** 2);
    expect(readBack(1536)).toBe(1536);
  });
});
