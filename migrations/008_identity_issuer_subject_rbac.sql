-- P2/P3 identity hardening for already-deployed central DBs.
-- * user identity is keyed by (issuer, subject), not subject alone.
-- * project_member_permissions exposes issuer + enabled for service-side authz.
-- Existing subject-only rows are explicitly classified under the legacy issuer.
--
-- Operational note: the concurrent index statements in this file must not be
-- wrapped by an outer transaction.
--
-- Reversibility (--rollback annotations, Liquibase formatted-SQL convention):
-- the `--rollback <stmt>` lines below are the DOWN migration. They are ordinary
-- SQL comments to the forward runner (scripts/platform_db_migrate.py executes the
-- whole file, and `--` lines are inert), so the FORWARD apply is byte-safe — the
-- annotations add zero executable forward statements. A rollback-aware caller
-- recovers the DOWN statements by stripping the `--rollback ` prefix (parsed by
-- tests/test_central_db_migration_runner.py::parse_rollback_statements, the SSOT
-- for the convention; promote it into platform_db_migrate.py when the runner gains
-- a `rollback` subcommand — see tech-debt-tracker auth-jit-provisioning P1).
--
-- Rollback scope = the uniqueness axis ONLY, and it is LOSSLESS:
--   * (issuer, subject) UNIQUE is dropped; subject-only UNIQUE is restored.
--   * the `issuer` column + its legacy default + the backfilled
--     'urn:fcc:identity:legacy' classification are RETAINED (no DROP COLUMN →
--     no data loss). A re-applied forward migration is idempotent over them.
--   * the project_member_permissions view enrichment (user_issuer / user_enabled)
--     is additive and read-only; it is intentionally NOT reverted (the pre-008
--     body is not preserved in any migration, and reverting it would break the
--     fail-closed authz read that consumes user_enabled unless the application is
--     rolled back in lockstep — a code change, not a schema change).
-- Fail-closed hazard: restoring the subject-only UNIQUE fails LOUDLY if post-008
-- data holds the same subject under two different issuers (only legal AFTER this
-- migration). That is the correct signal — such a collision must be reconciled
-- deliberately before a rollback, never silently dropped (mirrors 007's CHECK add).
-- Under PostgreSQL the failing CREATE UNIQUE INDEX CONCURRENTLY leaves an INVALID
-- index that must be dropped before retrying.

ALTER TABLE "users"
    ADD COLUMN IF NOT EXISTS "issuer" TEXT DEFAULT 'urn:fcc:identity:legacy';

ALTER TABLE "users"
    ALTER COLUMN "issuer" SET DEFAULT 'urn:fcc:identity:legacy';

UPDATE "users"
SET "issuer" = 'urn:fcc:identity:legacy'
WHERE "issuer" IS NULL OR btrim("issuer") = '';

ALTER TABLE "users"
    ALTER COLUMN "subject" SET NOT NULL;

ALTER TABLE "users"
    ALTER COLUMN "issuer" SET NOT NULL;

DROP INDEX CONCURRENTLY IF EXISTS "ux_users_issuer_subject";

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "ux_users_issuer_subject"
    ON "users" ("issuer", "subject");

ALTER TABLE "users"
    DROP CONSTRAINT IF EXISTS "users_subject_key";

DROP INDEX CONCURRENTLY IF EXISTS "ux_users_subject";

-- DROP + CREATE (not CREATE OR REPLACE): a deployed DB's pre-008 view ends at
-- user_enabled, so REPLACE would try to insert user_issuer before it and Postgres
-- rejects that as a column rename ("cannot change name of view column"). DROP +
-- CREATE is reorder-safe and idempotent (the view carries no data and has no
-- dependent objects); on the autocommit path each statement self-commits, so a
-- re-run after a partial apply simply recreates the view. Fresh DBs build the same
-- final column order from 001's base view.
DROP VIEW IF EXISTS "project_member_permissions";
CREATE VIEW "project_member_permissions" AS
    SELECT
        pm.project_id,
        u.subject AS user_subject,
        p.permission_key,
        pm.role_key,
        pm.assigned_at,
        pm.expires_at,
        u.issuer AS user_issuer,
        u.enabled AS user_enabled
    FROM project_membership pm
    JOIN users u ON u.id = pm.user_id
    JOIN roles r ON r.role_key = pm.role_key
    JOIN role_permissions rp ON rp.role_id = r.id
    JOIN permissions p ON p.id = rp.permission_id;

-- ===========================================================================
-- ROLLBACK (DOWN) — reverse of the uniqueness axis above; lossless.
-- Order: drop the (issuer, subject) UNIQUE first, then restore subject-only
-- UNIQUE. CONCURRENTLY-safe (no outer transaction). The `issuer` column and its
-- legacy-classified data are deliberately preserved (see header).
-- ===========================================================================
--rollback DROP INDEX CONCURRENTLY IF EXISTS "ux_users_issuer_subject";
--rollback CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "ux_users_subject" ON "users" ("subject");
