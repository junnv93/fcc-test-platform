import type { Translate } from '@/ui';

/** Only a DRAFT-status draft can be published — an already-published/archived
 *  one is terminal. A single predicate so the publish affordance and any future
 *  gating share one definition. Exported for unit testing. */
export function isPublishableDraft(status: string): boolean {
  return status.trim().toLowerCase() === 'draft';
}

/** The three draft lifecycle statuses, in workbench display order. The single
 *  list the drafts-list status filter's options AND its `?status=` URL-param
 *  validation both derive from — so the filter can never offer / accept a value
 *  the domain doesn't model (URL-state Slice 2). */
export const DRAFT_STATUS_VALUES = ['draft', 'published', 'archived'] as const;

export type DraftStatusValue = (typeof DRAFT_STATUS_VALUES)[number];

const DRAFT_STATUS_UNKNOWN_TOKEN = 'unknown';

/** The i18n leaf token for a draft lifecycle label. Known states reuse their
 *  canonical token; an unknown / forward-compatible state degrades to the
 *  generic `unknown` branch rather than leaking the unresolved locale key. */
export function draftStatusLabelToken(status: string): DraftStatusValue | 'unknown' {
  const value = status.trim().toLowerCase();
  return (DRAFT_STATUS_VALUES as readonly string[]).includes(value)
    ? (value as DraftStatusValue)
    : DRAFT_STATUS_UNKNOWN_TOKEN;
}

/** Human-facing draft status label. Unknown states still surface the raw token,
 *  but through a localized fallback string instead of `routes.testPlans.status.*`
 *  leaking to the UI. */
export function draftStatusLabel(t: Translate, status: string): string {
  const token = draftStatusLabelToken(status);
  return token === DRAFT_STATUS_UNKNOWN_TOKEN
    ? t('routes.testPlans.status.unknown', { status: status.trim() || '—' })
    : t(`routes.testPlans.status.${token}`);
}

/** Narrow an arbitrary `?status=` URL value to a known draft status, or `null`
 *  ("all" / unrecognised). Case-insensitive and trimmed, mirroring how the row
 *  status labels are matched. A crafted or stale value degrades to "all" rather
 *  than silently hiding every row. */
export function normalizeDraftStatusFilter(raw: string): DraftStatusValue | null {
  const value = raw.trim().toLowerCase();
  return (DRAFT_STATUS_VALUES as readonly string[]).includes(value)
    ? (value as DraftStatusValue)
    : null;
}
