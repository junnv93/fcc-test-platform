import { type components } from '@/api/generated/headless-api.types';

type DraftRow = components['schemas']['TestPlanDraftRowView'];

/**
 * Draft content fingerprint — the SSOT for "is a validation result still about
 * the draft currently on screen?" (W2-C M3).
 *
 * WHY A FINGERPRINT AND NOT A REVISION. `TestPlanDraftView` carries no
 * server-side revision/`updated_at` for the row set, so there is no authoritative
 * token to compare a validation result against. What validation actually reads is
 * the row content, so the honest client-side stand-in is a stable digest of that
 * same content: if the digest moved, the result the operator is looking at was
 * computed over different rows and is no longer a judgement of the current draft.
 *
 * Two rules make it safe to reason about:
 *  - it is a PURE function of the rows — no clock, no identity counter, no
 *    module state — so the same row set always yields the same token and a
 *    remount cannot manufacture a false "changed";
 *  - it errs toward STALE, never toward FRESH. Row order is part of the digest,
 *    so a reorder counts as a change. Claiming freshness we cannot prove is the
 *    failure mode this milestone exists to remove.
 *
 * Both separators are ASCII control characters (unit / record separator) that
 * cannot appear in a capability path or a test-type token, so no field value can
 * forge a boundary and make two different row sets collide.
 */
const FIELD_SEPARATOR = '\u001F';
const ROW_SEPARATOR = '\u001E';
const PATH_SEPARATOR = '/';

/** Fingerprint of an empty row set — distinct from "not yet fingerprinted". */
export const EMPTY_DRAFT_FINGERPRINT = '';

function cell(value: string | null | undefined): string {
  return value ?? '';
}

/** Stable content digest of a draft's row set. */
export function draftRowsFingerprint(rows: readonly DraftRow[]): string {
  return rows
    .map((row) =>
      [
        String(row.draft_row_id),
        row.capability_path.join(PATH_SEPARATOR),
        cell(row.test_type),
        cell(row.mode_family),
        cell(row.antenna),
        cell(row.tone),
        cell(row.location),
        cell(row.origin),
      ].join(FIELD_SEPARATOR),
    )
    .join(ROW_SEPARATOR);
}

/**
 * The three states a draft's validation can be in. They are genuinely different
 * facts and the readiness rail must not collapse them: `unvalidated` means the
 * operator has not asked, which is NOT a failure and must never be rendered as
 * one (the route's product frame forbids fabricated judgements).
 */
export type DraftValidationState = 'unvalidated' | 'fresh' | 'stale';

/**
 * Derive the validation state from what the validate mutation actually ran on.
 *
 * `validatedFingerprint` is the mutation's own `variables` — the fingerprint the
 * request was issued for. Pairing the result with its input this way means there
 * is no second copy of "what was validated" to fall out of sync with the result.
 */
export function draftValidationState(
  hasResult: boolean,
  validatedFingerprint: string | undefined,
  currentFingerprint: string,
): DraftValidationState {
  if (!hasResult || validatedFingerprint === undefined) return 'unvalidated';
  return validatedFingerprint === currentFingerprint ? 'fresh' : 'stale';
}
