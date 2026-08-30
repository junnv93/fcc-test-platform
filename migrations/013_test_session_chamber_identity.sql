-- FCC central migration 013: qualify provider-local session identity by chamber.
--
-- The initial schema used (provider_id, provider_session_id) as its natural key.
-- A provider can operate more than one chamber, and local SQLite session ids are
-- allocated independently on each PC. Existing rows cannot be retrospectively
-- attributed to a chamber, so they receive the reserved legacy sentinel. New
-- chamber traffic supplies its real chamber_id and uses the three-column key.
-- Apply this migration under the normal central migration lock before enabling
-- multi-chamber result ingestion.

BEGIN;

ALTER TABLE "test_sessions"
    ADD COLUMN IF NOT EXISTS "chamber_id" TEXT;

UPDATE "test_sessions"
   SET "chamber_id" = '__fcc_legacy__'
 WHERE "chamber_id" IS NULL;

ALTER TABLE "test_sessions"
    ALTER COLUMN "chamber_id" SET DEFAULT '__fcc_legacy__',
    ALTER COLUMN "chamber_id" SET NOT NULL;

DROP INDEX IF EXISTS "ux_test_sessions_provider_session";
CREATE UNIQUE INDEX IF NOT EXISTS "ux_test_sessions_provider_chamber_session"
    ON "test_sessions" ("provider_id", "chamber_id", "provider_session_id");

COMMIT;
