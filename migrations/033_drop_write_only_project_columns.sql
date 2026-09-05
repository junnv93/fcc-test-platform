-- 033_drop_write_only_project_columns.sql
-- Drop two columns that are WRITTEN but never READ (2026-09-04).
--
-- ## The evidence, per column
--
--   * projects.name
--     Every INSERT path fills it (the column is NOT NULL, so they must), and
--     NOTHING reads it: no SELECT in the platform read adapters names it, no API
--     envelope carries it, no screen renders it. The value is always a copy of
--     project_code — and since ADR-0017 D1 pinned `project_code == model name`,
--     it is the THIRD copy of one string (project_code, device_models.model_name,
--     and this). A NOT NULL column that only ever receives a duplicate of another
--     column is pure obligation: every new write path must learn to fill it, and
--     no reader ever benefits.
--
--   * device_models.metadata_json
--     The project service writes a literal NULL into it on every create and no
--     code path ever writes anything else or reads it back. An empty extension
--     slot is not free: it appears in the INSERT column list, in the schema
--     contract, and in every fixture that mirrors the DDL — a shape people
--     maintain in exchange for nothing. If a model ever needs structured
--     metadata, adding a column then is a one-line additive migration; keeping an
--     always-NULL one now buys no time.
--
-- Deliberately NOT dropped, though they are equally unread HERE: users.azure_ad_id
-- / employee_id / department / phone_number. The provider lane seals those as
-- "Azure 승인 전 대기 칸" (test_ems_schema_parity) — they are RESERVED, not dead,
-- and the difference is that someone has committed to filling them.
--
-- Dialect: PostgreSQL only. Transactional: no CONCURRENTLY, so the runner
-- executes the whole file in one transaction.
--
-- ## Reversibility
--
-- SHAPE-ONLY, and that is honest here rather than a limitation: both columns are
-- restorable to their exact prior CONTENT. `projects.name` was always a copy of
-- `project_code`, so the rollback backfills it from that column and the restored
-- data is byte-identical to what was dropped. `device_models.metadata_json` was
-- always NULL, so an empty column IS its prior content. This is the rare drop
-- that loses no information at all — which is precisely why it is safe to make.
--
--rollback ALTER TABLE "device_models" ADD COLUMN IF NOT EXISTS "metadata_json" JSONB;
--rollback ALTER TABLE "projects" ADD COLUMN IF NOT EXISTS "name" TEXT;
--rollback UPDATE "projects" SET "name" = "project_code" WHERE "name" IS NULL;
--rollback ALTER TABLE "projects" ALTER COLUMN "name" SET NOT NULL;

BEGIN;

ALTER TABLE "projects" DROP COLUMN IF EXISTS "name";
ALTER TABLE "device_models" DROP COLUMN IF EXISTS "metadata_json";

COMMIT;
