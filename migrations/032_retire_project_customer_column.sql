-- 032_retire_project_customer_column.sql
-- Retire `projects.customer`; its values merge into `projects.applicant_name`.
--
-- CONTRACT: docs/platform/central_db_schema.v1.json no longer declares the column,
-- and the domain declares it retired (project_metadata_edit.RETIRED_PROJECT_META_FIELDS
-- maps 'customer' → 'applicant_name'). 001 renders a FRESH DB without it; this
-- migration brings an ALREADY-DEPLOYED DB to the same shape.
--
-- Ordering: 031 MUST have run first (it builds the applicant search + suggestion
-- indexes). Dropping the column here also drops idx_projects_search_customer
-- automatically — PostgreSQL removes indexes that depend on a dropped column —
-- so doing it in the other order would leave the `?q` axis index-less while the
-- API is serving.
--
-- ## Why this is expand-and-contract, and why it can REFUSE to run
--
-- The merge is only lossless when a project does not hold two DIFFERENT parties
-- in the two columns. For every other shape the direction is unambiguous:
--
--   | customer | applicant_name | action                                  |
--   |----------|----------------|-----------------------------------------|
--   | empty    | anything       | nothing to merge                        |
--   | value    | empty          | BACKFILL applicant_name := customer     |
--   | value    | same value     | already merged (case/whitespace-insens.)|
--   | value    | DIFFERENT      | **CONFLICT — cannot be decided here**   |
--
-- The last row is real data that only a human can adjudicate: dropping the column
-- would destroy a value the operator deliberately typed. A migration that
-- silently picks a winner is a migration that loses data quietly, so this one
-- RAISES instead, naming the offending projects. The operator resolves them
-- (edit the projects so both columns agree, or clear the stale one) and re-runs;
-- the whole file is one transaction, so a refusal leaves the DB untouched.
--
-- Dialect: PostgreSQL only (like 001-031). Transactional: no CONCURRENTLY here,
-- so scripts/platform_db_migrate.py executes the whole file in one transaction —
-- backfill, guard and DROP either all land or none do.
--
-- ## Reversibility
--
-- PARTIAL, and deliberately declared as such. The rollback annotations restore
-- the COLUMN and its index (shape), but NOT the values — a dropped column's data
-- is gone, and the backfilled rows are indistinguishable from rows an operator
-- typed into applicant_name afterwards. Restoring shape is enough for the API to
-- start again on the old code path; restoring values requires a backup restore.
-- This is the reason the guard above refuses conflicts rather than resolving
-- them: for every row this migration DOES touch, the value still exists in
-- applicant_name, so nothing is unrecoverable.
--
--rollback ALTER TABLE "projects" ADD COLUMN IF NOT EXISTS "customer" TEXT;
--rollback CREATE INDEX IF NOT EXISTS "idx_projects_search_customer" ON "projects" USING gin (lower(customer) gin_trgm_ops);

BEGIN;

-- 1) Refuse to destroy an adjudicable value. Named projects, not just a count —
--    an error the operator cannot act on is only marginally better than silence.
DO $$
DECLARE
    conflicting text;
BEGIN
    SELECT string_agg(format('%s (customer=%L, applicant_name=%L)',
                             "project_code", "customer", "applicant_name"),
                      '; ' ORDER BY "project_code")
      INTO conflicting
      FROM "projects"
     WHERE "customer" IS NOT NULL AND btrim("customer") <> ''
       AND "applicant_name" IS NOT NULL AND btrim("applicant_name") <> ''
       AND lower(btrim("customer")) <> lower(btrim("applicant_name"));

    IF conflicting IS NOT NULL THEN
        RAISE EXCEPTION
            'migration 032 refuses to drop projects.customer: % project(s) hold a '
            'DIFFERENT party in customer and applicant_name, and dropping the column '
            'would destroy the customer value. Resolve them first (make the two agree, '
            'or clear the stale one), then re-run. Offending projects: %',
            (SELECT count(*) FROM "projects"
              WHERE "customer" IS NOT NULL AND btrim("customer") <> ''
                AND "applicant_name" IS NOT NULL AND btrim("applicant_name") <> ''
                AND lower(btrim("customer")) <> lower(btrim("applicant_name"))),
            conflicting;
    END IF;
END $$;

-- 2) Merge. Only rows where applicant_name has nothing to lose. `updated_at` moves
--    because the row's report-cover meta genuinely changed.
UPDATE "projects"
   SET "applicant_name" = btrim("customer"),
       "updated_at" = now()
 WHERE "customer" IS NOT NULL AND btrim("customer") <> ''
   AND ("applicant_name" IS NULL OR btrim("applicant_name") = '');

-- 3) Contract. The dependent index idx_projects_search_customer goes with it.
ALTER TABLE "projects" DROP COLUMN IF EXISTS "customer";

-- 4) The backfill changed the value distribution of applicant_name, and both of
--    031's indexes are EXPRESSION indexes carrying their own statistics. Without
--    a fresh ANALYZE the planner prices `LOWER(applicant_name) LIKE ...` off the
--    pre-backfill picture and can fall back to a sequential scan.
ANALYZE "projects";

COMMIT;
