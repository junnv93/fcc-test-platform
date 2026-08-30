-- 011_ingestion_owned_defaults.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (Option A — the DB owns
-- ingestion-supplied ids/timestamps). Completes what 009 started for the two
-- measurement tables, extending the SAME ownership rule to every remaining table
-- the shared ingestion mapper writes.
--
-- Root cause it closes (two halves of one defect class):
--
-- 1. `measurement_results.session_id` / `measurement_attempts.session_id` are NOT
--    NULL FKs to `test_sessions(id)`, but NO production code created that parent
--    row — the ingestion pipeline wrote only measurement tables. The FK held in
--    every test and demo because `scripts/dev_seed/central.py` and
--    `scripts/platform_central_db_live_proof.py` seed a `test_sessions` row up
--    front. A real deployment has neither seed, so the FIRST live sync into a
--    fresh central DB failed on the session FK and no measurement ever landed.
--    The pipeline now upserts the session parent row FIRST, in the same
--    single-session transaction (INGESTION_TABLE_ORDER[0]).
--
-- 2. `test_sessions`, `artifacts` and `report_outputs` declared `id` / `created_at`
--    / `updated_at` NOT NULL with NO default, while the shared mapper omits them —
--    exactly the caller-stamp drift 009 removed for measurement ids. dev_seed
--    stamps them; the production sync adapter does not. Handing ownership to the
--    DB makes the mapper's omission correct for ALL callers.
--
-- `test_sessions.id` deliberately gets NO default: it is caller-supplied and
-- deterministic (uuid5 over provider_id + local session id), which is what makes a
-- re-sync idempotent and keeps the offline measurement loop free of central
-- round-trips. `artifacts.id` / `report_outputs.id` have no such derivation, so
-- they take `gen_random_uuid()` like the measurement tables.
--
-- Source of truth is docs/platform/central_db_schema.v1.json; the DDL exporter
-- renders the SAME DEFAULT clauses into 001 for a FRESH DB. This migration brings
-- an EXISTING DB to that state additively.
--
-- Dialect: PostgreSQL only (like 001-010). The SQLite test shim does NOT run these
-- incremental .sql files.
--
-- Idempotent: ALTER COLUMN ... SET DEFAULT is re-runnable (it overwrites, never
-- duplicates), and pgcrypto (gen_random_uuid) is already installed by 001. Purely
-- additive — no data touched, no column dropped, existing rows keep the values
-- their caller previously stamped.

-- test_sessions: measurement FK parent, now written by ingestion.
ALTER TABLE "test_sessions"  ALTER COLUMN "created_at" SET DEFAULT now();
ALTER TABLE "test_sessions"  ALTER COLUMN "updated_at" SET DEFAULT now();

-- artifacts: ingestion target whose mapper omits id/created_at.
ALTER TABLE "artifacts"      ALTER COLUMN "id"         SET DEFAULT gen_random_uuid();
ALTER TABLE "artifacts"      ALTER COLUMN "created_at" SET DEFAULT now();

-- report_outputs: same. (Its report_run_id FK still requires a report_runs parent
-- created by the report pipeline — that is a separate contract, not a default.)
ALTER TABLE "report_outputs" ALTER COLUMN "id"         SET DEFAULT gen_random_uuid();
ALTER TABLE "report_outputs" ALTER COLUMN "created_at" SET DEFAULT now();
