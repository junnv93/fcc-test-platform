-- 006_progress_plan_published_at.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (Phase 6 P6L-3).
-- (005 is reserved by the Phase G test_reports migration; this is 006.)
--
-- Adds published_plan_expectation.plan_published_at so the progress read can apply
-- read-side latest-wins (per (project_id, provider_id) only the MAX(plan_published_at)
-- plan's rows are rolled up — ROW_NUMBER window). A central DB created before P6L-3
-- will NOT pick up the column by re-running 001 (CREATE TABLE IF NOT EXISTS skips an
-- existing table), so this adds it additively + idempotently.
--
-- Source of truth is docs/platform/central_db_schema.v1.json (published_plan_expectation
-- already carries plan_published_at there; 001 is regenerated from it by
-- scripts/export_platform_central_db_ddl.py). For a fresh DB, 001 already has the
-- column and this migration is a no-op (ADD COLUMN IF NOT EXISTS).
--
-- Purely additive: ADD COLUMN only (nullable, no default backfill). Existing rows
-- get NULL plan_published_at, which the read window treats as oldest — they are
-- naturally superseded the next time the owning plan re-syncs with its publish time.
-- No table/view is dropped or rewritten; the F1 progress join and coverage
-- materialization are untouched.

ALTER TABLE "published_plan_expectation"
    ADD COLUMN IF NOT EXISTS "plan_published_at" TIMESTAMPTZ;
