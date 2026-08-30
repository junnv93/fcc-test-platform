/**
 * Chamber progress WS → React Query cache bridge (멀티챔버 P7/B4, 2026-06-18).
 *
 * The central progress relay (`/platform/chambers/events`) pushes real-time
 * `ChamberProgressEvent`s. This module writes each event into the SAME query
 * cache keys the polling fallback uses (`queryKeys.chambers.progress(id)` +
 * `queryKeys.chambers.list()`), so the UI renders fresher data without a second
 * source of truth. The polling hook (`useChamberProgressPolling`) is left intact:
 * when the WS is down, polling keeps populating the same keys (stale fallback).
 *
 * `applyChamberProgressEvent` is a pure cache mutation (fed a `QueryClient`), so
 * it is unit-testable without a live socket or React render.
 */
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import {
  createChamberProgressStream,
  parseChamberProgressSnapshot,
  type ChamberProgressEvent,
  type ChamberStreamStatus,
} from './chamber-events';
import { queryKeys } from './query-config';

import type { ChamberMeasurementSnapshot } from './platform-client';

/**
 * Ordering verdict for one event against the last applied watermark
 * (fe-data-layer-robustness M2, 2026-07-19). Pure so the rule is sealable
 * without a QueryClient.
 *
 * WebSocket delivery is ordered *per connection*, but this stream reconnects:
 * after a drop, the relay replays from its own buffer, so a stale frame can
 * arrive after a newer poll or a newer post-reconnect frame and roll the
 * displayed progress backwards. The previous code wrote every event
 * unconditionally, so an operator could watch a run go 42% → 17% → 42%.
 *
 * `occurred_at` is optional on the wire. When either side lacks it there is no
 * ordering information, so the event is accepted (fail-open): dropping
 * timestamp-less events would silence a relay that never stamps them, which is
 * strictly worse than the ordering risk it avoids.
 */
export function isChamberEventFresh(
  watermark: string | undefined,
  occurredAt: string | null | undefined,
): boolean {
  if (watermark === undefined || occurredAt === null || occurredAt === undefined) {
    return true;
  }
  return occurredAt >= watermark;
}

/**
 * Apply one progress event to the query cache by refreshing the per-chamber
 * progress snapshot key — the EXACT key the polling fallback
 * (`useChamberProgressPolling`) populates and the UI rows render from
 * (`snapshot.progress.*`). No second source of truth: a WS event and a poll
 * write the same key, so the freshest of the two wins. The availability list
 * query is intentionally NOT touched — the UI reads progress from the per-chamber
 * key, not the list row (whose `progress` field is unused), so patching it would
 * be dead work.
 *
 * Two M2 guards run before the write, and either one leaves the cache
 * **untouched**:
 *
 *   1. **Shape** — the snapshot is re-validated with the same runtime check the
 *      parser uses. `applyChamberProgressEvent` is exported and typed, so a
 *      caller holding a hand-built (or `any`-widened) event could otherwise put
 *      a partial object behind a complete-looking type.
 *   2. **Order** — {@link isChamberEventFresh} drops events older than the last
 *      applied one, and the accepted event advances the watermark.
 */
export function applyChamberProgressEvent(
  queryClient: QueryClient,
  event: ChamberProgressEvent,
): void {
  const progress = parseChamberProgressSnapshot(event.progress);
  if (progress === null) return;

  const watermarkKey = queryKeys.chambers.progressWatermark(event.chamber_id);
  const watermark = queryClient.getQueryData<string>(watermarkKey);
  if (!isChamberEventFresh(watermark, event.occurred_at)) return;

  const snapshot: ChamberMeasurementSnapshot = {
    chamber_id: event.chamber_id,
    progress,
  };
  queryClient.setQueryData(queryKeys.chambers.progress(event.chamber_id), snapshot);
  if (event.occurred_at !== null && event.occurred_at !== undefined) {
    queryClient.setQueryData(watermarkKey, event.occurred_at);
  }
}

/**
 * Open the chamber progress relay for the lifetime of the mounting component and
 * stream every event into the query cache. Returns the connection status so the
 * UI can surface "live" vs "polling" (the polling fallback always runs in
 * parallel; the WS only adds freshness). Idempotent teardown on unmount.
 */
export function useChamberProgressStream(): { status: ChamberStreamStatus } {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<ChamberStreamStatus>('connecting');

  useEffect(() => {
    const handle = createChamberProgressStream({
      onEvent: (event) => applyChamberProgressEvent(queryClient, event),
      onStatus: setStatus,
    });
    return () => handle.close();
  }, [queryClient]);

  return { status };
}
