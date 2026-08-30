/**
 * Write-feedback selection — shared SSOT (web-mutation-feedback-ssot, 2026-06-13).
 *
 * A route often owns more than one write mutation (claim acquire + release;
 * membership assign + revoke). After a success the mutation stays `isSuccess`
 * until the next call, so two mutations can both report success and the route
 * must announce only the MOST RECENT one. This pure selector encodes that rule
 * once — `submittedAt` (the TanStack Query mutation timestamp) breaks the tie —
 * so each route does not re-derive it (and cannot drift).
 */

/** One candidate write outcome: a mutation's success flag, its submit timestamp,
 *  and the message to announce if it is the most recent success. */
export interface WriteFeedbackEntry {
  readonly isSuccess: boolean;
  /** TanStack Query `mutation.submittedAt` (ms epoch; 0 when never submitted). */
  readonly submittedAt: number;
  readonly message: string;
}

/**
 * The message of the most-recently-submitted successful entry, or `null` when
 * none has succeeded. Non-success entries are ignored; ties (equal timestamps)
 * keep the first occurrence — deterministic for a stable caller order.
 */
export function selectLatestWriteMessage(entries: readonly WriteFeedbackEntry[]): string | null {
  let best: WriteFeedbackEntry | null = null;
  for (const entry of entries) {
    if (!entry.isSuccess) continue;
    if (best === null || entry.submittedAt > best.submittedAt) best = entry;
  }
  return best === null ? null : best.message;
}
