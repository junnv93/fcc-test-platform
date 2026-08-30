-- 002_sample_inventory_columns.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (Phase A/B/C + E).
--
-- 001_initial_central_db.sql is applied only on FIRST boot
-- (docker-entrypoint-initdb.d), and its CREATE TABLE statements are
-- IF NOT EXISTS — so a central DB that was created before Phase A/B/C will NOT
-- pick up the new projects/samples columns by re-running 001. This migration
-- brings such an existing DB up to the current schema additively and
-- idempotently (safe to re-run).
--
-- Source of truth is docs/platform/central_db_schema.v1.json — every column
-- added here MUST already exist there (sealed by tests/test_sample_inventory_migration.py).
-- For a fresh DB, 001 already contains all of these; 002 is a no-op there.

-- Phase A/B: projects management/status + 성적서 표지 메타.
ALTER TABLE "projects" ADD COLUMN IF NOT EXISTS "management_number" TEXT;
-- Phase A unique guarantee for the Report Number basis (S-{management_number}-…).
-- ALTER ADD COLUMN above does not recreate the unique index on an existing DB.
CREATE UNIQUE INDEX IF NOT EXISTS "ux_projects_management_number" ON "projects" ("management_number");
ALTER TABLE "projects" ADD COLUMN IF NOT EXISTS "status" TEXT;
ALTER TABLE "projects" ADD COLUMN IF NOT EXISTS "fcc_grantee_code" TEXT;
ALTER TABLE "projects" ADD COLUMN IF NOT EXISTS "applicant_name" TEXT;
ALTER TABLE "projects" ADD COLUMN IF NOT EXISTS "applicant_address" TEXT;
ALTER TABLE "projects" ADD COLUMN IF NOT EXISTS "eut_description" TEXT;
ALTER TABLE "projects" ADD COLUMN IF NOT EXISTS "test_standard" TEXT;

-- Phase C: samples PM 칸 인벤토리 메타 (전부 nullable text).
ALTER TABLE "samples" ADD COLUMN IF NOT EXISTS "sample_number" TEXT;
ALTER TABLE "samples" ADD COLUMN IF NOT EXISTS "test_category" TEXT;
ALTER TABLE "samples" ADD COLUMN IF NOT EXISTS "label_number" TEXT;
ALTER TABLE "samples" ADD COLUMN IF NOT EXISTS "smsn" TEXT;
ALTER TABLE "samples" ADD COLUMN IF NOT EXISTS "intake_cert" TEXT;
ALTER TABLE "samples" ADD COLUMN IF NOT EXISTS "assigned_team" TEXT;
ALTER TABLE "samples" ADD COLUMN IF NOT EXISTS "sender" TEXT;
ALTER TABLE "samples" ADD COLUMN IF NOT EXISTS "receiver" TEXT;
ALTER TABLE "samples" ADD COLUMN IF NOT EXISTS "received_date" TEXT;
ALTER TABLE "samples" ADD COLUMN IF NOT EXISTS "released_date" TEXT;
-- Phase E natural key: the Excel-import upsert is keyed by (project_id, sample_number).
-- This unique index makes that upsert race-safe (ON CONFLICT target). NULL
-- sample_number rows (legacy / non-inventory samples) stay distinct (NULLs are
-- not equal in a unique index), so this never collides with pre-Phase-C rows.
CREATE UNIQUE INDEX IF NOT EXISTS "ux_samples_project_sample_number" ON "samples" ("project_id", "sample_number");

-- Phase C: sample_intakes intake-history table (시험원 입고 칸, 1:N to samples).
-- CREATE TABLE IF NOT EXISTS is already idempotent for both fresh and existing DBs.
CREATE TABLE IF NOT EXISTS "sample_intakes" (
    "id" UUID PRIMARY KEY,
    "sample_id" UUID NOT NULL REFERENCES "samples"("id"),
    "intake_date" TEXT,
    "bl" TEXT,
    "ap" TEXT,
    "cp" TEXT,
    "csc" TEXT,
    "rf_cal" TEXT,
    "hw_rev" TEXT,
    "note" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_sample_intakes_sample" ON "sample_intakes" ("sample_id");

-- Phase E: durable import-run audit (write counts + diagnostics per import, same
-- transaction as the writes). CREATE TABLE IF NOT EXISTS is idempotent.
CREATE TABLE IF NOT EXISTS "sample_import_runs" (
    "id" UUID PRIMARY KEY,
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "model_name" TEXT,
    "samples_created" INTEGER NOT NULL,
    "samples_updated" INTEGER NOT NULL,
    "intakes_appended" INTEGER NOT NULL,
    "intakes_skipped" INTEGER NOT NULL,
    "sample_count" INTEGER NOT NULL,
    "diagnostics_json" JSONB,
    "created_at" TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_sample_import_runs_project" ON "sample_import_runs" ("project_id", "created_at");
