-- 007_project_status_domain.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (project-status-visibility).
--
-- 001_initial_central_db.sql now seals projects.status to the domain
-- {active, completed} via the exporter-rendered CHECK constraint
-- (ck_projects_status) — but that only applies to a FRESH DB. An existing DB has
-- `status TEXT` with no constraint and may hold NULL/'' rows. This migration
-- brings an existing DB to the SAME sealed state additively and idempotently
-- (safe to re-run): backfill any NULL/blank status to 'active' (the create-time
-- default the application already writes), then (re)apply the CHECK.
--
-- Source of truth is projects.status.allowed_values in
-- docs/platform/central_db_schema.v1.json (the DDL exporter renders the SAME
-- CHECK + constraint name into 001). Read-open by status + the complete/reopen
-- lifecycle depend on this domain being sealed.
--
-- Dialect: PostgreSQL only (like 001-006 — gen_random_uuid / ALTER ... ADD
-- CONSTRAINT etc.). The central DB is Postgres; the SQLite test shim does NOT
-- run these incremental .sql files (it builds its schema from the exporter DDL),
-- so the PG-only ALTER syntax here is intentional, not a portability gap.
--
-- Backfill scope: only NULL / '' are coerced to 'active' (the create-time app
-- default — the only values an unsealed projects.status has ever held). If an
-- out-of-domain value (e.g. a hypothetical 'archived') ever existed, the CHECK
-- ADD would fail loudly here — which is the correct fail-closed behavior (an
-- unexpected legacy value must be reconciled deliberately, not silently kept).

-- Backfill existing rows to the application default before constraining.
UPDATE "projects" SET "status" = 'active' WHERE "status" IS NULL OR "status" = '';

-- (Re)seal the status domain. DROP IF EXISTS keeps the migration idempotent
-- (CHECK constraints have no ADD ... IF NOT EXISTS form); the name matches the
-- exporter-rendered 001 constraint (ck_projects_status).
ALTER TABLE "projects" DROP CONSTRAINT IF EXISTS "ck_projects_status";
ALTER TABLE "projects" ADD CONSTRAINT "ck_projects_status" CHECK ("status" IN ('active', 'completed'));
