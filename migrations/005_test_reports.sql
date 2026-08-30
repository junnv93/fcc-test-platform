-- 005_test_reports.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (Phase G test_reports).
-- (003 = Phase D pm_tester_rbac, 004 = Phase 6 progress tables; this is 005.)
--
-- 001_initial_central_db.sql is applied only on FIRST boot
-- (docker-entrypoint-initdb.d) with CREATE TABLE IF NOT EXISTS — so a central DB
-- created before Phase G will NOT pick up test_reports by re-running 001. This
-- migration adds it additively and idempotently (safe to re-run).
--
-- Source of truth is docs/platform/central_db_schema.v1.json — every table /
-- column here MUST already exist there (001 is regenerated from it by
-- scripts/export_platform_central_db_ddl.py; sealed by
-- tests/test_test_report_migration.py). For a fresh DB, 001 already contains
-- test_reports; 005 is a no-op there.
--
-- Purely additive (no existing table / column / index is modified). report_number
-- is intentionally NOT a column — it is derived (S-{management_number}-{edition})
-- by report_number_policy, mirroring the derived fcc_id.

-- Phase G: per-project 성적서(test certificate) instances (1:N to projects).
CREATE TABLE IF NOT EXISTS "test_reports" (
    "id" UUID PRIMARY KEY,
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "edition" TEXT NOT NULL,
    "date_of_issue" TEXT,
    "date_tested_start" TEXT,
    "date_tested_end" TEXT,
    "prepared_by" TEXT,
    "prepared_site" TEXT,
    "rev_history_json" JSONB,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);
-- Natural key: one report row per (project, edition). report_number = derived
-- S-{management_number}-{edition}, so this unique index also makes report_number
-- unique within a project and is the create-time race-safe ON CONFLICT target.
CREATE UNIQUE INDEX IF NOT EXISTS "ux_test_reports_project_edition" ON "test_reports" ("project_id", "edition");
CREATE INDEX IF NOT EXISTS "idx_test_reports_project" ON "test_reports" ("project_id");
