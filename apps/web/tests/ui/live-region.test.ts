import { describe, expect, it } from 'vitest';

import {
  ASSERTIVE_LIVE_REGION_KINDS,
  LIVE_REGION_KINDS,
  LIVE_REGION_RULINGS,
  liveRegionProps,
} from '@/ui/live-region';

/**
 * W4-A M4 — the urgency ruling table.
 *
 * The point of this module is that the assertive/polite decision is made ONCE
 * and written down. These assertions guard the two ways that degrades: a kind
 * whose role stops matching its declared urgency (the table lying about what
 * it emits), and a kind added without a reason (the table becoming a second
 * place to copy `role="alert"` from, which is what it replaced).
 */
describe('live-region ruling table', () => {
  it('covers every declared kind exactly once', () => {
    expect(new Set(LIVE_REGION_KINDS).size).toBe(LIVE_REGION_KINDS.length);
    expect([...LIVE_REGION_KINDS].sort()).toEqual(Object.keys(LIVE_REGION_RULINGS).sort());
  });

  it.each(LIVE_REGION_KINDS)('states why %s sits on its axis', (kind) => {
    const rationale = LIVE_REGION_RULINGS[kind].rationale;
    // A one-word "urgent" is not a reason; the next person adding a surface
    // has to be able to test their case against this sentence.
    expect(rationale.trim().length).toBeGreaterThan(40);
  });

  it.each(LIVE_REGION_KINDS)('emits a role consistent with the urgency of %s', (kind) => {
    const ruling = LIVE_REGION_RULINGS[kind];
    const props = liveRegionProps(kind);
    expect(props.role).toBe(ruling.role);
    if (ruling.urgency === 'off') {
      expect(props['aria-live']).toBeUndefined();
      expect(props.role).toBe('note');
    } else {
      // Explicit `aria-live` alongside the role: several screen readers only
      // treat `role="status"` as live when the attribute is present.
      expect(props['aria-live']).toBe(ruling.urgency);
      expect(props.role).toBe(ruling.urgency === 'assertive' ? 'alert' : 'status');
    }
  });

  it('keeps the urgent axis narrow and derived', () => {
    // Derived from the table, never re-listed — a kind promoted to assertive
    // shows up here without anyone remembering to update a second list.
    expect([...ASSERTIVE_LIVE_REGION_KINDS]).toEqual(
      LIVE_REGION_KINDS.filter((kind) => LIVE_REGION_RULINGS[kind].urgency === 'assertive'),
    );
    // Both current members answer "the operator cannot continue": the content
    // is gone, or the input was refused. Growth here is a design decision, not
    // a detail — the backend seal ratchets the files allowed to consume them.
    expect([...ASSERTIVE_LIVE_REGION_KINDS]).toEqual(['blockingFailure', 'inputRejected']);
  });
});
