-- 009_measurement_id_created_at_defaults.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (Option A — DB owns
-- measurement id/created_at).
--
-- Root cause it closes: measurement_results / measurement_attempts declared
-- `id` and `created_at` NOT NULL with NO DB default. The shared ingestion mapper
-- (build_platform_ingestion_batch) deliberately OMITS both columns, so every
-- caller of the mapper had to stamp them. dev_seed stamped them; the production
-- CentralBackendSyncAdapter did NOT — so a live sync INSERT violated NOT NULL.
-- 001_initial_central_db.sql now renders these DEFAULTs (gen_random_uuid() /
-- now()) for a FRESH DB via the exporter; this migration brings an EXISTING DB
-- to the SAME state additively and idempotently.
--
-- After this migration the DB is the single owner of both values: the mapper's
-- omission is correct for ALL callers (dev_seed + production sync), and the
-- caller-stamp drift class is eliminated at its source.
--
-- Source of truth is docs/platform/central_db_schema.v1.json (measurement_results
-- / measurement_attempts columns id.default + created_at.default); the DDL
-- exporter renders the SAME DEFAULT clauses into 001.
--
-- Dialect: PostgreSQL only (like 001-008 — gen_random_uuid / now()). The central
-- DB is Postgres; the SQLite test shim does NOT run these incremental .sql files.
--
-- Idempotent: ALTER COLUMN ... SET DEFAULT is re-runnable (it overwrites, never
-- duplicates), and pgcrypto (gen_random_uuid) is already installed by 001.
-- Purely additive — no data touched, no column dropped, existing rows keep the
-- id/created_at values the caller previously stamped.

-- measurement_results: hand id/created_at ownership to the DB.
ALTER TABLE "measurement_results"  ALTER COLUMN "id"         SET DEFAULT gen_random_uuid();
ALTER TABLE "measurement_results"  ALTER COLUMN "created_at" SET DEFAULT now();

-- measurement_attempts: same.
ALTER TABLE "measurement_attempts" ALTER COLUMN "id"         SET DEFAULT gen_random_uuid();
ALTER TABLE "measurement_attempts" ALTER COLUMN "created_at" SET DEFAULT now();
