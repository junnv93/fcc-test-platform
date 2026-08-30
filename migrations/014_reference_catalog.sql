-- 014_reference_catalog.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (Wave 3 reference catalog).
--
-- 001_initial_central_db.sql is applied only on FIRST boot
-- (docker-entrypoint-initdb.d) with CREATE TABLE IF NOT EXISTS — so a central DB
-- created before Wave 3 will NOT pick up reference_revisions / reference_entries
-- by re-running 001. This migration adds them additively and idempotently (safe
-- to re-run). For a fresh DB, 001 already contains both tables and 014 is a no-op.
--
-- Source of truth is docs/platform/central_db_schema.v1.json — every table /
-- column / index here MUST already exist there (001 is regenerated from it by
-- scripts/export_platform_central_db_ddl.py). This file is a verbatim additive
-- slice of that generated DDL, so a drift between the two is a test failure
-- rather than a silent divergence.
--
-- WHAT THESE TABLES ARE. They are the authoritative ORIGIN of measurement
-- reference data. The chamber PC's logs/reference_catalog.db holds the same
-- revisions as a read-only REPLICA. The direction matters: the workbook is a
-- one-time import source that lands here, publishing is a human review step in
-- the web UI, and chamber nodes PULL a delivery bundle. An unreachable central
-- therefore degrades a chamber to a stale replica, never to a stopped chamber —
-- the mirror image of the measurement-result axis, which flows local→central
-- while keeping zero central round-trips inside the offline measurement loop.
--
-- Purely additive: no existing table, column, index or constraint is modified,
-- and no data is deleted. Rollback is a matter of not using the tables.

-- Reference revisions: one lifecycle row per (provider, family, profile, scope).
CREATE TABLE IF NOT EXISTS "reference_revisions" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "family" TEXT NOT NULL,
    "profile_id" TEXT NOT NULL,
    "scope_kind" TEXT NOT NULL CONSTRAINT "ck_reference_revisions_scope_kind" CHECK ("scope_kind" IN ('room', 'project')),
    "scope_id" TEXT NOT NULL,
    "revision_number" INTEGER NOT NULL,
    "state" TEXT NOT NULL CONSTRAINT "ck_reference_revisions_state" CHECK ("state" IN ('CANDIDATE', 'PUBLISHED', 'RETIRED')),
    "version" INTEGER NOT NULL DEFAULT 1,
    "etag" TEXT NOT NULL,
    "content_sha256" TEXT NOT NULL,
    "source_snapshot_id" TEXT NOT NULL,
    "source_manifest_sha256" TEXT NOT NULL,
    "official_manifest_sha256" TEXT,
    "forked_from_revision_id" UUID REFERENCES "reference_revisions"("id"),
    "created_by" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_by" TEXT NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "approved_by" TEXT,
    "approved_at" TIMESTAMPTZ,
    "approval_reason" TEXT,
    "published_by" TEXT,
    "published_at" TIMESTAMPTZ,
    "publish_reason" TEXT,
    "retired_by" TEXT,
    "retired_at" TIMESTAMPTZ,
    "retirement_reason" TEXT
);

-- Reference entries: the runtime lookup rows one revision carries. payload_json
-- is opaque to the platform — its field set is the provider's
-- PROJECTION_FIELD_CONTRACT, and normalising it into central columns would fork
-- that contract into a third schema (the boundary forbidden_platform_columns draws).
CREATE TABLE IF NOT EXISTS "reference_entries" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "revision_id" UUID NOT NULL REFERENCES "reference_revisions"("id"),
    "entry_order" INTEGER NOT NULL,
    "reference_id" TEXT NOT NULL,
    "identity_key" TEXT NOT NULL,
    "payload_json" JSONB NOT NULL,
    "test_condition_ids_json" JSONB,
    "effective_from" TEXT,
    "effective_to" TEXT,
    "source_sheet_name" TEXT,
    "source_row_number" INTEGER,
    "content_sha256" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Identity + revision number is the natural key; it is also the ON CONFLICT
-- target that makes candidate creation race-safe.
CREATE UNIQUE INDEX IF NOT EXISTS "ux_reference_revisions_identity_number" ON "reference_revisions" ("provider_id", "family", "profile_id", "scope_id", "revision_number");
-- PARTIAL unique: at most one PUBLISHED revision per identity. The local replica
-- can only DETECT a second published revision after the fact
-- (AmbiguousPublishedRevisionError); the origin refuses to create it at all.
CREATE UNIQUE INDEX IF NOT EXISTS "ux_reference_revisions_published" ON "reference_revisions" ("provider_id", "family", "profile_id", "scope_id") WHERE state = 'PUBLISHED';
CREATE INDEX IF NOT EXISTS "idx_reference_revisions_scope" ON "reference_revisions" ("provider_id", "scope_kind", "scope_id", "family");
CREATE INDEX IF NOT EXISTS "idx_reference_revisions_state" ON "reference_revisions" ("provider_id", "state", "updated_at");

CREATE UNIQUE INDEX IF NOT EXISTS "ux_reference_entries_revision_reference" ON "reference_entries" ("revision_id", "reference_id");
-- entry_order preserves source row order so a replica projects rows in the order
-- seeding would have produced them.
CREATE UNIQUE INDEX IF NOT EXISTS "ux_reference_entries_revision_order" ON "reference_entries" ("revision_id", "entry_order");
CREATE INDEX IF NOT EXISTS "idx_reference_entries_identity" ON "reference_entries" ("revision_id", "identity_key");
