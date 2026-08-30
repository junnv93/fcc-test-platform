/**
 * Project progress rollup helpers — shared SSOT (Phase 6 wiring, 2026-07-03).
 *
 * These pure helpers were introduced in `routes/progress.tsx` for the
 * time-weighted progress dashboard. Phase 6 entry-flow wiring makes a *second*
 * consumer (`routes/fields.tsx` progress badges) need the same honest-rollup
 * rules, so the helpers move here: a route module must not become the import
 * origin of business logic for another route module (route→route coupling +
 * SSOT drift risk). `progress.tsx` and `fields.tsx` both import from here.
 *
 * The honesty rules are the whole point of the shared SSOT and MUST NOT be
 * re-derived per consumer:
 *   - `percent === null` means "no priced time" → render "시간 미설정"
 *     (가짜 0% 금지). The backend `ProgressBucketRollup` DTO decides this; the
 *     frontend never fabricates a 0%.
 *   - unpriced / unbucketable are surfaced as *condition counts*, never minutes.
 *
 * These are the same functions `progress.tsx` shipped (byte-identical logic) —
 * only their home moved. `rollupForArea` / `progressBadgeStatus` are the new
 * fields-badge additions layered on the same rules.
 */

import type { ProgressBucketEnvelope } from '@/api/platform-client';

const _ELEVEN_AX_SUFFIX = '_11ax';

/** 분 → 시간 문자열(소수 1자리). 시험원 진행률 단위가 시간(H)이므로 분을 시간으로 환산. */
export function formatHours(minutes: number): string {
  return (minutes / 60).toFixed(1);
}

/** percent → ProgressBar tone. 100%=완료(pass), 진행 중(running), 0%=시작 전(accent).
 *  null(시간 미설정)은 호출측에서 바 대신 배지로 분기하므로 여기 도달하지 않는다. */
export function percentTone(percent: number): 'pass' | 'running' | 'accent' {
  if (percent >= 100) return 'pass';
  if (percent > 0) return 'running';
  return 'accent';
}

/** percent → StatusBadge status for the fields entry-card progress badge.
 *  Distinct from {@link percentTone} because StatusKind has no 'accent': a
 *  0% badge (priced but untouched) uses 'draft' (◇ not-yet-started). Shares the
 *  same honesty rule — null(시간 미설정) → 'stale' (가짜 0% 금지). */
export function progressBadgeStatus(
  percent: number | null,
): 'pass' | 'running' | 'draft' | 'stale' {
  if (percent === null) return 'stale';
  if (percent >= 100) return 'pass';
  if (percent > 0) return 'running';
  return 'draft';
}

/** progress_bucket_id → 표시 라벨. null=미분류. `unii_2a_11ax` → `UNII 2A (11ax)`.
 *  버킷 어휘는 provider 가 산출(백엔드 SSOT) — 프론트는 표기만 정규화(파생 0). */
export function formatBucketLabel(bucketId: string | null, unbucketedLabel: string): string {
  if (bucketId === null) return unbucketedLabel;
  const isAx = bucketId.endsWith(_ELEVEN_AX_SUFFIX);
  const base = isAx ? bucketId.slice(0, -_ELEVEN_AX_SUFFIX.length) : bucketId;
  let label: string;
  if (base.startsWith('unii_')) {
    label = `UNII ${base.slice('unii_'.length).toUpperCase()}`;
  } else {
    label = base.toUpperCase();
  }
  return isAx ? `${label} (11ax)` : label;
}

export interface ProgressAreaGroup {
  readonly area: string;
  readonly buckets: readonly ProgressBucketEnvelope[];
}

/** rollup 배열을 분야(area)별로 그룹화. 백엔드가 (area, bucket) 정렬해 보내므로
 *  연속 그룹화로 순서를 보존한다(클라이언트 재정렬 0). */
export function groupByArea(rollups: readonly ProgressBucketEnvelope[]): ProgressAreaGroup[] {
  const order: string[] = [];
  const byArea = new Map<string, ProgressBucketEnvelope[]>();
  for (const row of rollups) {
    let arr = byArea.get(row.progress_area);
    if (arr === undefined) {
      arr = [];
      byArea.set(row.progress_area, arr);
      order.push(row.progress_area);
    }
    arr.push(row);
  }
  return order.map((area) => ({ area, buckets: byArea.get(area) ?? [] }));
}

export interface ProgressTotals {
  readonly plannedMinutes: number;
  readonly completedMinutes: number;
  readonly percent: number | null;
  readonly unpricedConditions: number;
  readonly unbucketableConditions: number;
}

/** 전체 합산 — priced 분(planned/completed) 합 + percent(planned=0 → null=시간 미설정)
 *  + unpriced/unbucketable condition 합. 가짜 0% 금지 규칙을 합산에도 적용. */
export function overallTotals(rollups: readonly ProgressBucketEnvelope[]): ProgressTotals {
  let planned = 0;
  let completed = 0;
  let unpriced = 0;
  let unbucketable = 0;
  for (const r of rollups) {
    planned += r.planned_minutes;
    completed += r.completed_minutes;
    unpriced += r.unpriced_conditions ?? 0;
    unbucketable += r.unbucketable_conditions ?? 0;
  }
  return {
    plannedMinutes: planned,
    completedMinutes: completed,
    percent: planned > 0 ? (100 * completed) / planned : null,
    unpricedConditions: unpriced,
    unbucketableConditions: unbucketable,
  };
}

/** Rollup a single workbench area's buckets from the full project progress list.
 *  Returns `null` when the area has no rows at all (no measured/planned data for
 *  it yet) so the caller renders "시간 미설정" rather than a fabricated 0% — the
 *  fields entry-card badge honesty rule, reusing {@link overallTotals}. */
export function rollupForArea(
  rollups: readonly ProgressBucketEnvelope[],
  areaId: string,
): ProgressTotals | null {
  const buckets = rollups.filter((r) => r.progress_area === areaId);
  if (buckets.length === 0) return null;
  return overallTotals(buckets);
}
