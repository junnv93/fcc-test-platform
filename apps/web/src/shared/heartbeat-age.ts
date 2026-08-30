/**
 * Heartbeat age SSOT — server-anchored, wall-clock-following (M4,
 * fe-w2-b-execution-freshness, 2026-07-28).
 *
 * `ChamberAdminPanel` used to derive the age locally as
 * `server_time − last_heartbeat_at`, where `server_time` is the instant carried
 * by the availability *payload*. Both terms are therefore frozen at fetch time,
 * so the cell displayed a constant — "12s" — for as long as the page stayed
 * open, no matter how long the chamber had actually been silent. On a screen
 * whose entire job is to say how fresh things are, that is not a cosmetic bug:
 * it is the screen asserting something untrue, and the list was on
 * `REFETCH_STRATEGIES.IMPORTANT` (`refetchInterval: false`), so nothing else was
 * going to correct it either.
 *
 * The naive repair — `Date.now() − last_heartbeat_at` — trades one wrong answer
 * for another: it promotes the operator's PC clock to the authority on when the
 * chamber last spoke. A workstation five minutes fast would report every chamber
 * as five minutes stale, which is worse than frozen because it looks plausible.
 *
 * So the derivation is ANCHORED. One observation pins the two timelines
 * together:
 *
 *     anchor = { serverTime, observedAtMs }
 *              ^ server said this  ^ client clock read this at that moment
 *
 *     offset          = observedAtMs − serverTime
 *     estimatedServer = clientNow − offset
 *
 * Only the client clock's *rate* is trusted (elapsed time), never its absolute
 * reading. A constant skew Δ appears in `observedAtMs` and in `clientNow` alike
 * and cancels in the subtraction — sealed as an explicit property in
 * `tests/shared/heartbeat-age.test.ts` ("a skewed client clock cancels out").
 *
 * Shape follows `shared/percent-display.ts`: a pure, i18n-free derivation
 * (`heartbeatAgeSeconds`) and a separate rendering step (`formatHeartbeatAge`).
 * Keeping them apart is what lets the offset-cancellation property be checked as
 * arithmetic instead of by comparing display strings.
 *
 * Error direction, deliberately chosen: between polls the age is extrapolated,
 * so a heartbeat that arrives right after a fetch is not reflected until the
 * next one. The displayed age is then too LARGE — the screen looks staler than
 * reality. That is the safe direction; the opposite (claiming freshness we
 * cannot support) is the failure mode this module exists to prevent.
 */
import { useEffect, useState } from 'react';

import { t } from '@/i18n';

/** Re-read cadence for the ticking clock. It equals the finest rendered
 *  resolution (whole seconds) — a slower tick would make the number jump
 *  (13s → 16s) and read as a broken clock rather than a running one. */
export const HEARTBEAT_AGE_TICK_INTERVAL_MS = 1_000;

const MS_PER_SECOND = 1_000;
/** Display resolution ladder — an age is shown in the largest unit that keeps
 *  the leading number meaningful, so a long-dead chamber reads "3d", not
 *  "259200s". */
const SECONDS_PER_MINUTE = 60;
const MINUTES_PER_HOUR = 60;
const HOURS_PER_DAY = 24;

/**
 * A server instant paired with the client-clock reading taken when that payload
 * was observed.
 *
 * `observedAtMs` is TanStack Query's `dataUpdatedAt` at every call site — the
 * moment the response landed in the cache. It is deliberately NOT captured into
 * component state: a second copy of "when did this data arrive" would be a
 * second source of truth, free to drift from the query that owns the data.
 */
export interface ServerClockAnchor {
  /** ISO-8601 instant authored by the server (`server_time` in the payload). */
  readonly serverTime: string;
  /** `Date.now()` as read when that payload was observed (`dataUpdatedAt`). */
  readonly observedAtMs: number;
}

/**
 * Project a client-clock reading onto the server's timeline, or `null` when the
 * anchor cannot support the projection.
 *
 * `observedAtMs <= 0` is the "never observed" case (`dataUpdatedAt` is 0 before
 * a query has ever resolved) — not "observed at the epoch". Returning `null`
 * rather than a number is what stops the UI inventing an age for data it does
 * not have.
 */
export function estimateServerNowMs(anchor: ServerClockAnchor, clientNowMs: number): number | null {
  const serverMs = Date.parse(anchor.serverTime);
  if (Number.isNaN(serverMs)) return null;
  if (!Number.isFinite(anchor.observedAtMs) || anchor.observedAtMs <= 0) return null;
  if (!Number.isFinite(clientNowMs)) return null;
  return clientNowMs - (anchor.observedAtMs - serverMs);
}

/**
 * Seconds since a chamber last reported, on the server's timeline.
 *
 * `null` means "no age to report" — never heard from, or an unparseable /
 * unanchored input. The caller renders that as its own arm rather than as a
 * number, so an unknown age can never be mistaken for a fresh one.
 */
export function heartbeatAgeSeconds(
  anchor: ServerClockAnchor,
  lastHeartbeatAt: string | null | undefined,
  clientNowMs: number,
): number | null {
  if (lastHeartbeatAt === null || lastHeartbeatAt === undefined || lastHeartbeatAt === '') {
    return null;
  }
  const heartbeatMs = Date.parse(lastHeartbeatAt);
  if (Number.isNaN(heartbeatMs)) return null;
  const serverNowMs = estimateServerNowMs(anchor, clientNowMs);
  if (serverNowMs === null) return null;
  // Clamped at 0: a node whose own clock runs slightly ahead of the central
  // service stamps a heartbeat "in the future". "0s" is the honest reading of
  // that; a negative duration is not a thing an operator can act on.
  return Math.max(0, Math.floor((serverNowMs - heartbeatMs) / MS_PER_SECOND));
}

/**
 * Render an age through the locale bundles.
 *
 * Before this promotion the unit suffixes (`s`/`m`/`h`/`d`) and the word
 * `'unknown'` were inline English literals inside a component, so a Korean
 * operator read English units on an otherwise Korean screen.
 */
export function formatHeartbeatAge(seconds: number | null): string {
  if (seconds === null) return t('ui.heartbeatAge.unknown');
  if (seconds < SECONDS_PER_MINUTE) return t('ui.heartbeatAge.seconds', { value: seconds });
  const minutes = Math.floor(seconds / SECONDS_PER_MINUTE);
  if (minutes < MINUTES_PER_HOUR) return t('ui.heartbeatAge.minutes', { value: minutes });
  const hours = Math.floor(minutes / MINUTES_PER_HOUR);
  if (hours < HOURS_PER_DAY) return t('ui.heartbeatAge.hours', { value: hours });
  return t('ui.heartbeatAge.days', { value: Math.floor(hours / HOURS_PER_DAY) });
}

/**
 * A clock reading that advances on the tick cadence, so anything derived from it
 * re-renders as time passes.
 *
 * Subscribe ONCE per panel and pass the reading down to the rows. A hook per row
 * would put N intervals on a fleet table and wake the browser N times a second
 * to produce the same instant.
 */
export function useClockTick(intervalMs: number = HEARTBEAT_AGE_TICK_INTERVAL_MS): number {
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return nowMs;
}
