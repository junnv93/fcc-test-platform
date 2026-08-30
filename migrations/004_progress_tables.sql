-- 004_progress_tables.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (Phase 6 progress).
-- (003 is reserved by the concurrent Phase D pm_tester_rbac migration; this is 004.)
--
-- 001_initial_central_db.sql is applied only on FIRST boot
-- (docker-entrypoint-initdb.d) with CREATE TABLE IF NOT EXISTS — so a central DB
-- created before Phase 6 will NOT pick up the two progress tables by re-running
-- 001. This migration adds them additively and idempotently (safe to re-run).
--
-- Source of truth is docs/platform/central_db_schema.v1.json — every table /
-- column here MUST already exist there (001 is regenerated from it by
-- scripts/export_platform_central_db_ddl.py; sealed by
-- tests/test_progress_tables_migration.py). For a fresh DB, 001 already contains
-- both tables; 004 is a no-op there.
--
-- These are purely additive (no existing table / view is modified — the F1
-- provider-scoped progress rollup uses a SEPARATE read model and never mutates
-- coverage_by_condition_hash / its FE-P0a seals).

-- Phase 6: provider-owned standard-time catalog (test_type_canonical → planned_minutes).
CREATE TABLE IF NOT EXISTS "standard_time_catalog" (
    "id" UUID PRIMARY KEY,
    "provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "test_type_canonical" TEXT NOT NULL,
    "planned_minutes" NUMERIC NOT NULL,
    "version" INTEGER NOT NULL,
    "source" TEXT NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "updated_by" TEXT
);
-- One catalog row per (provider, canonical test type). The in-app edit upsert is
-- keyed on this; the unique index makes that upsert race-safe (ON CONFLICT target).
CREATE UNIQUE INDEX IF NOT EXISTS "ux_standard_time_catalog_provider_test_type" ON "standard_time_catalog" ("provider_id", "test_type_canonical");

-- Phase 6: time-weighted progress denominator (append-only, snapshot-frozen).
CREATE TABLE IF NOT EXISTS "published_plan_expectation" (
    "id" UUID PRIMARY KEY,
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "plan_id" TEXT NOT NULL,
    "condition_hash" TEXT NOT NULL,
    "coverage_technology" TEXT NOT NULL,
    "raw_test_type" TEXT NOT NULL,
    "test_type_canonical" TEXT,
    "progress_bucket_id" TEXT,
    "progress_area" TEXT NOT NULL,
    "planned_minutes_snapshot" NUMERIC,
    "catalog_version" INTEGER,
    "pricing_status" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);
-- Natural key: one expectation row per (project, provider, plan, condition).
-- provider_id is part of identity because the progress read join is provider-
-- scoped; two headless providers must never overwrite each other's denominator.
CREATE UNIQUE INDEX IF NOT EXISTS "ux_published_plan_expectation_project_provider_plan_condition" ON "published_plan_expectation" ("project_id", "provider_id", "plan_id", "condition_hash");
-- F1 join axis: the progress rollup joins coverage on (project_id, provider_id,
-- condition_hash) — provider_id scopes the join so one headless's measurement
-- never satisfies another headless's expectation.
CREATE INDEX IF NOT EXISTS "idx_published_plan_expectation_join" ON "published_plan_expectation" ("project_id", "provider_id", "condition_hash");
-- Rollup axis: GROUP BY (project, provider, area, bucket) for the progress view.
CREATE INDEX IF NOT EXISTS "idx_published_plan_expectation_rollup" ON "published_plan_expectation" ("project_id", "provider_id", "progress_area", "progress_bucket_id");

-- Progress read path narrows latest attempts by project before joining by
-- provider + condition. Additive index only; existing coverage materialization
-- and FE-P0a seals are untouched.
CREATE INDEX IF NOT EXISTS "idx_measurement_attempts_progress_join" ON "measurement_attempts" ("project_id", "provider_id", "condition_hash", "is_latest");
