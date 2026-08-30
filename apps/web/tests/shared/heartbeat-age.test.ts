import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setLocale } from '@/i18n';
import {
  estimateServerNowMs,
  formatHeartbeatAge,
  heartbeatAgeSeconds,
  HEARTBEAT_AGE_TICK_INTERVAL_MS,
  useClockTick,
  type ServerClockAnchor,
} from '@/shared/heartbeat-age';

/**
 * heartbeat-age SSOT unit seals (fe-w2-b-execution-freshness M4, 2026-07-28).
 *
 * The promoted module replaces `ChamberAdminPanel`'s local `heartbeatAge`,
 * whose denominator was the *fetch-time* `server_time` snapshot — so the cell
 * froze at "12s" while the wall clock kept moving. The two properties that make
 * the replacement correct rather than merely different are sealed here:
 *
 *   P1 the age advances with the client clock between fetches, and
 *   P2 a skewed client clock does NOT move the age (offset cancellation).
 *
 * P2 is why the derivation is anchored rather than clock-based: the naive fix
 * ("use Date.now() − heartbeat") would read the operator's PC clock as an
 * absolute reference and be wrong by exactly that PC's skew.
 */

/** Server said this instant; the client observed that payload at `observedAtMs`. */
const ANCHOR: ServerClockAnchor = {
  serverTime: '2026-06-16T00:00:00+00:00',
  observedAtMs: Date.parse('2026-06-16T00:00:00+00:00'),
};
const HEARTBEAT_30S_AGO = '2026-06-15T23:59:30+00:00';

describe('estimateServerNowMs — client readings projected onto the server timeline', () => {
  it('at the observation instant the estimate IS the server instant', () => {
    expect(estimateServerNowMs(ANCHOR, ANCHOR.observedAtMs)).toBe(Date.parse(ANCHOR.serverTime));
  });

  it('advances one-for-one with the client clock after the observation', () => {
    const later = estimateServerNowMs(ANCHOR, ANCHOR.observedAtMs + 5_000);
    expect(later).toBe(Date.parse(ANCHOR.serverTime) + 5_000);
  });

  it('rejects an unusable anchor rather than inventing a timeline', () => {
    // `dataUpdatedAt` is 0 until a query has ever resolved — that is "no
    // observation", not "observed at the epoch".
    expect(estimateServerNowMs({ ...ANCHOR, observedAtMs: 0 }, 1_000)).toBeNull();
    expect(estimateServerNowMs({ ...ANCHOR, serverTime: 'not-a-date' }, 1_000)).toBeNull();
  });
});

describe('heartbeatAgeSeconds — P1 the age flows with the wall clock', () => {
  it('is the server-side gap at the observation instant', () => {
    expect(heartbeatAgeSeconds(ANCHOR, HEARTBEAT_30S_AGO, ANCHOR.observedAtMs)).toBe(30);
  });

  it('grows as the client clock advances between fetches (the "12s frozen" defect)', () => {
    expect(heartbeatAgeSeconds(ANCHOR, HEARTBEAT_30S_AGO, ANCHOR.observedAtMs + 1_000)).toBe(31);
    expect(heartbeatAgeSeconds(ANCHOR, HEARTBEAT_30S_AGO, ANCHOR.observedAtMs + 90_000)).toBe(120);
  });

  it('has no age to report when the chamber has never been heard from', () => {
    for (const absent of [null, undefined, '']) {
      expect(heartbeatAgeSeconds(ANCHOR, absent, ANCHOR.observedAtMs)).toBeNull();
    }
    expect(heartbeatAgeSeconds(ANCHOR, 'not-a-date', ANCHOR.observedAtMs)).toBeNull();
  });

  it('never reports a negative age when a clock runs backwards', () => {
    // A heartbeat stamped slightly ahead of the server instant (clock skew on
    // the node) must read as 0, not as a negative duration.
    expect(heartbeatAgeSeconds(ANCHOR, '2026-06-16T00:00:05+00:00', ANCHOR.observedAtMs)).toBe(0);
  });
});

describe('heartbeatAgeSeconds — P2 a skewed client clock cancels out (AC-10)', () => {
  it.each([
    ['one hour fast', 3_600_000],
    ['one hour slow', -3_600_000],
    ['a year fast', 365 * 24 * 3_600_000],
  ])('%s: the age is identical to an accurate clock', (_label, skewMs) => {
    const accurate = heartbeatAgeSeconds(ANCHOR, HEARTBEAT_30S_AGO, ANCHOR.observedAtMs + 7_000);
    // A constant skew shifts BOTH the observation reading and the current
    // reading by the same amount — the offset subtraction removes it.
    const skewedAnchor: ServerClockAnchor = {
      ...ANCHOR,
      observedAtMs: ANCHOR.observedAtMs + skewMs,
    };
    const skewed = heartbeatAgeSeconds(
      skewedAnchor,
      HEARTBEAT_30S_AGO,
      ANCHOR.observedAtMs + skewMs + 7_000,
    );
    expect(skewed).toBe(accurate);
    expect(skewed).toBe(37);
  });
});

describe('formatHeartbeatAge — resolution ladder through i18n (no inlined suffix)', () => {
  // `tests/setup.ts` pins the rendered locale to `ko`; these cases assert the
  // copy per locale explicitly and restore the pin afterwards.
  afterEach(() => {
    setLocale('ko');
  });

  it('renders each resolution band in en (byte-identical to the pre-promotion suffixes)', () => {
    setLocale('en');
    expect(formatHeartbeatAge(0)).toBe('0s');
    expect(formatHeartbeatAge(59)).toBe('59s');
    expect(formatHeartbeatAge(60)).toBe('1m');
    expect(formatHeartbeatAge(3_599)).toBe('59m');
    expect(formatHeartbeatAge(3_600)).toBe('1h');
    expect(formatHeartbeatAge(86_399)).toBe('23h');
    expect(formatHeartbeatAge(86_400)).toBe('1d');
  });

  it('renders Korean copy in ko (the suffixes were hardcoded English before)', () => {
    setLocale('ko');
    // The defect this repays: `${seconds}s` / `m` / `h` / `d` / `'unknown'` were
    // inline English literals, so a Korean operator read English units.
    expect(formatHeartbeatAge(30)).toBe('30초');
    expect(formatHeartbeatAge(60)).toBe('1분');
    expect(formatHeartbeatAge(3_600)).toBe('1시간');
    expect(formatHeartbeatAge(86_400)).toBe('1일');
    expect(formatHeartbeatAge(null)).not.toBe('unknown');
  });

  it('does not render a bare i18n key for any arm', () => {
    for (const locale of ['ko', 'en'] as const) {
      setLocale(locale);
      for (const seconds of [null, 0, 60, 3_600, 86_400]) {
        const rendered = formatHeartbeatAge(seconds);
        expect(rendered).not.toBe('');
        // A missing bundle entry resolves to the raw dotted key — that would be
        // a silent i18n regression, so assert the key never reaches the screen.
        expect(rendered).not.toContain('ui.heartbeatAge');
      }
    }
  });
});

describe('useClockTick — one subscription, monotone readings', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('re-reads the clock on the named tick cadence', () => {
    vi.useFakeTimers({ now: 1_700_000_000_000 });
    const { result } = renderHook(() => useClockTick());
    const first = result.current;

    act(() => {
      vi.advanceTimersByTime(HEARTBEAT_AGE_TICK_INTERVAL_MS);
    });
    expect(result.current).toBe(first + HEARTBEAT_AGE_TICK_INTERVAL_MS);

    act(() => {
      vi.advanceTimersByTime(HEARTBEAT_AGE_TICK_INTERVAL_MS * 3);
    });
    expect(result.current).toBe(first + HEARTBEAT_AGE_TICK_INTERVAL_MS * 4);
  });

  it('stops ticking once unmounted (no leaked interval)', () => {
    vi.useFakeTimers({ now: 1_700_000_000_000 });
    const { unmount } = renderHook(() => useClockTick());
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('ticks at the display resolution so the seconds digit never skips', () => {
    // The finest rendered band is whole seconds; a slower tick would make the
    // number jump (13s → 16s) and read as a broken clock.
    expect(HEARTBEAT_AGE_TICK_INTERVAL_MS).toBe(1_000);
  });
});
