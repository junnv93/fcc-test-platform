/**
 * The one test-plan generation `limits` fixture, typed against the contract.
 *
 * ## Why this file exists
 *
 * There were **five** literals of this object across four files: a complete one
 * in the e2e fixture (reused three times within it) and **four partial** ones
 * hand-rolled in `test-plans.test.tsx`, `test-plans-row-scale.test.tsx` and
 * `test-plans-generator.test.tsx` — that last file carried **two**, BT and WLAN.
 *
 * ⚠️ An earlier version of this comment said "four copies … three partial",
 * which counted *files* and called them literals. The partials carried the four
 * members `GenerateTestPlanForm.tsx` actually reads
 * (`page_size`, `browser_cache_page_limit`, `dom_row_limit`,
 * `initial_payload_row_limit`) and silently omitted the other eight the backend
 * declares as **required**.
 *
 * That was invisible while the transport stubs were untyped: nothing compared a
 * fixture against `TestPlanGenerationLimits`. Typing the stubs surfaced it
 * immediately, which is the point of the exercise — a partial fixture means the
 * route was being proven against a response no server can send.
 *
 * ## Why one definition rather than four completed ones
 *
 * Completing each copy would have written the twelve members out four more
 * times, and the copy that drifts is always the one nobody is looking at. The
 * shared object is annotated with the generated type, so a backend change to
 * `TestPlanGenerationLimits` fails **here** rather than in whichever suite runs
 * first.
 *
 * The values are deterministic test data (the e2e values, unchanged — the four
 * read members were already identical across all five literals, so consolidating
 * changes no assertion). The *field names and shape* are the contract.
 *
 * ⚠️ One type-level change, stated because the migration claim is "changes no
 * assertion": the e2e fixture's export lost its `as const`, so its members widen
 * from literal types to `number`. No consumer reads them as literals (they are
 * compared with `toBe`/`String()`), and the annotation against the generated
 * type is the stronger check — but it is a change, and the playwright lane
 * exports it.
 */
import type { HeadlessApiPaths } from '@/api/headless-client';
import type { SuccessResponseJSON } from 'openapi-typescript-helpers';

type CatalogueResponse = SuccessResponseJSON<
  HeadlessApiPaths['/headless/test-plan/generation/catalogue']['get']
>;

/** The contract's `TestPlanGenerationLimits`, reached through the operation that returns it. */
export type TestPlanGenerationLimits = CatalogueResponse['catalogues'][string]['limits'];

export const GENERATION_PAGE_SIZE = 250;
export const GENERATION_CACHE_PAGE_LIMIT = 2;
export const GENERATION_DOM_ROW_LIMIT = 300;
export const GENERATION_SEEDED_ROWS = 16_000;
export const GENERATION_IDLE_TIMEOUT_MS = 1_000;

export const TEST_PLAN_GENERATION_LIMITS: TestPlanGenerationLimits = {
  representative_sample_size: 8,
  hard_row_limit: GENERATION_SEEDED_ROWS,
  page_size: GENERATION_PAGE_SIZE,
  lease_seconds: 60,
  poll_interval_seconds: 2,
  claim_batch_size: 1,
  idle_fold_p95_ms: 100,
  keyset_page_p95_ms: 5,
  serialized_page_bytes: 128 * 1024,
  initial_payload_row_limit: 0,
  browser_cache_page_limit: GENERATION_CACHE_PAGE_LIMIT,
  dom_row_limit: GENERATION_DOM_ROW_LIMIT,
};
