/**
 * Project-id validation — single source of truth (FE-P8, 2026-06-13).
 *
 * Both the coverage dashboard (`routes/projects.tsx`) and the membership roster
 * (`routes/membership.tsx`) gate their central reads behind a client-side UUID
 * check so a non-uuid `project_id` never costs a round trip (the backend 400s
 * it). The regex + predicate live here so the two routes share one definition
 * instead of each re-declaring the pattern (SSOT — a drift in one would let an
 * invalid id through on the other).
 */

// uuid (any version) — backend 400s a non-uuid project_id, so gate client-side
// to avoid a spurious round-trip.
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu;

/** True when `raw` (trimmed) is a syntactically valid UUID project id. */
export function isValidProjectId(raw: string): boolean {
  return UUID_RE.test(raw.trim());
}
