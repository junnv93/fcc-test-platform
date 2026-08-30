/**
 * Numeric positive-id parsing — single source of truth (fe-error-id-ssot,
 * 2026-06-14).
 *
 * Several routes accept an operator-typed integer id (session_id, request_id)
 * and must reject non-numeric / out-of-range input before it costs a round
 * trip. `reports.tsx::parsePositiveId` and `sessions.tsx::parseSessionParam`
 * each re-declared the identical `^\d+$` + `Number.isSafeInteger` guard under
 * a different name; a change to the validation rule on one would silently let a
 * bad id through on the other. The predicate lives here so both delegate to one
 * definition — mirroring the established `project-id.ts` (UUID) SSOT.
 */

/**
 * Parse `raw` into a positive integer, or `null` when it is not a syntactically
 * valid positive integer id.
 *
 * `Number.isSafeInteger` guards precision loss: `Number('99999999999999999')`
 * passes the `^\d+$` regex but silently rounds to a wrong id sent to the API.
 */
export function parsePositiveId(raw: string): number | null {
  const trimmed = raw.trim();
  if (!/^\d+$/u.test(trimmed)) return null;
  const value = Number(trimmed);
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}
