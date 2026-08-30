-- Web-owned sample inventory (contract web-sample-inventory, 2026-08-24).
-- This migration is additive and idempotent. Existing samples receive an active
-- version-1 projection plus one baseline revision; no legacy session is assigned
-- a fabricated sample snapshot.
BEGIN;

ALTER TABLE "samples"
    ADD COLUMN IF NOT EXISTS "note" TEXT,
    ADD COLUMN IF NOT EXISTS "status" TEXT DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS "row_version" INTEGER DEFAULT 1,
    ADD COLUMN IF NOT EXISTS "deleted_at" TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS "deleted_by" TEXT;

UPDATE "samples"
SET "status" = 'active'
WHERE "status" IS NULL;

UPDATE "samples"
SET "row_version" = 1
WHERE "row_version" IS NULL OR "row_version" < 1;

ALTER TABLE "samples"
    ALTER COLUMN "status" SET DEFAULT 'active',
    ALTER COLUMN "status" SET NOT NULL,
    ALTER COLUMN "row_version" SET DEFAULT 1,
    ALTER COLUMN "row_version" SET NOT NULL;

ALTER TABLE "samples" DROP CONSTRAINT IF EXISTS "ck_samples_status";
ALTER TABLE "samples"
    ADD CONSTRAINT "ck_samples_status" CHECK ("status" IN ('active', 'deleted'));

ALTER TABLE "sample_intakes"
    ADD COLUMN IF NOT EXISTS "tech_group" TEXT;

ALTER TABLE "test_sessions"
    ADD COLUMN IF NOT EXISTS "sample_snapshot_json" TEXT,
    ADD COLUMN IF NOT EXISTS "sample_snapshot_schema_version" TEXT;

ALTER TABLE "test_sessions" DROP CONSTRAINT IF EXISTS "test_sessions_sample_id_fkey";
ALTER TABLE "test_sessions" DROP CONSTRAINT IF EXISTS "fk_test_sessions_sample_id";
ALTER TABLE "test_sessions"
    ADD CONSTRAINT "fk_test_sessions_sample_id"
    FOREIGN KEY ("sample_id") REFERENCES "samples"("id") ON DELETE SET NULL;

ALTER TABLE "test_sessions" DROP CONSTRAINT IF EXISTS "ck_test_sessions_web_snapshot_complete";
ALTER TABLE "test_sessions"
    ADD CONSTRAINT "ck_test_sessions_web_snapshot_complete"
    CHECK (
        "session_origin" <> 'WEB_SESSION'
        OR (
            "project_id" IS NOT NULL
            AND "sample_snapshot_json" IS NOT NULL
            AND "sample_snapshot_schema_version" IS NOT NULL
        )
    ) NOT VALID;

CREATE TABLE IF NOT EXISTS "sample_inventory_revisions" (
    "id" UUID PRIMARY KEY,
    "sample_id" UUID NOT NULL REFERENCES "samples"("id"),
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "revision_number" INTEGER NOT NULL,
    "event_type" TEXT NOT NULL,
    "snapshot_json" JSONB NOT NULL,
    "changed_fields_json" JSONB NOT NULL,
    "actor_subject" TEXT NOT NULL,
    "occurred_at" TIMESTAMPTZ NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

ALTER TABLE "sample_inventory_revisions" DROP CONSTRAINT IF EXISTS "ck_sample_inventory_revisions_event_type";
ALTER TABLE "sample_inventory_revisions"
    ADD CONSTRAINT "ck_sample_inventory_revisions_event_type"
    CHECK ("event_type" IN ('created', 'updated', 'status_changed', 'restored', 'baseline'));

CREATE UNIQUE INDEX IF NOT EXISTS "ux_sample_inventory_revisions_sample_revision"
    ON "sample_inventory_revisions" ("sample_id", "revision_number");
CREATE INDEX IF NOT EXISTS "idx_sample_inventory_revisions_project_occurred_sample"
    ON "sample_inventory_revisions" ("project_id", "occurred_at", "sample_id");
CREATE INDEX IF NOT EXISTS "idx_sample_inventory_revisions_sample_occurred"
    ON "sample_inventory_revisions" ("sample_id", "occurred_at");

-- Backfill the canonical, complete baseline snapshot from the existing current
-- projection. The database revision is allowed to contain operational sample
-- values; export/privacy boundaries are responsible for excluding PII. The
-- audit ledger is intentionally not an import run.
INSERT INTO "sample_inventory_revisions" (
    "id", "sample_id", "project_id", "revision_number", "event_type",
    "snapshot_json", "changed_fields_json", "actor_subject", "occurred_at", "created_at"
)
SELECT
    gen_random_uuid(),
    s."id",
    s."project_id",
    1,
    'baseline',
    jsonb_build_object(
        'schema_version', 'fcc.sample.inventory.snapshot.v1',
        'captured_at', COALESCE(s."updated_at", s."created_at"),
        'project', jsonb_build_object(
            'project_id', s."project_id",
            'project_code', p."project_code",
            'model_name', dm."model_name",
            'management_number', p."management_number"
        ),
        'sample', jsonb_build_object(
            'sample_id', s."id",
            'sample_number', s."sample_number",
            'sample_code', s."sample_code",
            'test_category', s."test_category",
            'label_number', s."label_number",
            'smsn', s."smsn",
            'serial_number', s."serial_number",
            'intake_cert', s."intake_cert",
            'assigned_team', s."assigned_team",
            'sender', s."sender",
            'receiver', s."receiver",
            'received_date', s."received_date",
            'released_date', s."released_date",
            'note', s."note",
            'status', s."status"
        ),
        'latest_intake', (
            SELECT jsonb_build_object(
                'intake_date', i."intake_date", 'bl', i."bl", 'ap', i."ap",
                'cp', i."cp", 'csc', i."csc", 'rf_cal', i."rf_cal",
                'hw_rev', i."hw_rev", 'note', i."note", 'tech_group', i."tech_group"
            )
            FROM "sample_intakes" i
            WHERE i."sample_id" = s."id"
            ORDER BY i."created_at" DESC, i."id" DESC
            LIMIT 1
        ),
        'sample_revision', 1,
        'row_version', s."row_version"
    ),
    '[]'::jsonb,
    'system:migration:029',
    COALESCE(s."updated_at", s."created_at"),
    COALESCE(s."updated_at", s."created_at")
FROM "samples" s
LEFT JOIN "projects" p ON p."id" = s."project_id"
LEFT JOIN LATERAL (
    SELECT dm."model_name"
    FROM "device_models" dm
    WHERE dm."project_id" = s."project_id"
    ORDER BY dm."created_at" DESC, dm."id" DESC
    LIMIT 1
) dm ON TRUE
WHERE NOT EXISTS (
    SELECT 1 FROM "sample_inventory_revisions" r
    WHERE r."sample_id" = s."id"
);

-- Backfill only legacy WEB_SESSION rows whose project/sample identity is a real
-- same-project join.  The snapshot is the canonical v1 shape used by the
-- platform service: no model, intake, or revision value is invented when the
-- old database does not contain it.  The lateral model/intake reads also make
-- the result one-row-per-session deterministic when an old database contains
-- duplicate model metadata.
UPDATE "test_sessions" ts
SET
    "sample_snapshot_json" = snapshot."snapshot_json",
    "sample_snapshot_schema_version" = 'fcc.sample.inventory.snapshot.v1'
FROM (
    SELECT
        ts0."id" AS "session_id",
        jsonb_build_object(
            'schema_version', 'fcc.sample.inventory.snapshot.v1',
            'captured_at', COALESCE(ts0."started_at", s."updated_at", s."created_at"),
            'project', jsonb_build_object(
                'project_id', p."id",
                'project_code', p."project_code",
                'model_name', dm."model_name",
                'management_number', p."management_number"
            ),
            'sample', jsonb_build_object(
                'sample_id', s."id",
                'sample_number', s."sample_number",
                'sample_code', s."sample_code",
                'test_category', s."test_category",
                'label_number', s."label_number",
                'smsn', s."smsn",
                'serial_number', s."serial_number",
                'intake_cert', s."intake_cert",
                'assigned_team', s."assigned_team",
                'sender', s."sender",
                'receiver', s."receiver",
                'received_date', s."received_date",
                'released_date', s."released_date",
                'note', s."note",
                'status', s."status"
            ),
            'latest_intake', latest."latest_intake",
            'sample_revision', COALESCE(revision."sample_revision", 1),
            'row_version', s."row_version"
        )::text AS "snapshot_json"
    FROM "test_sessions" ts0
    JOIN "samples" s
      ON s."id" = ts0."sample_id"
     AND s."project_id" = ts0."project_id"
    JOIN "projects" p ON p."id" = ts0."project_id"
    LEFT JOIN LATERAL (
        SELECT dm0."model_name"
        FROM "device_models" dm0
        WHERE dm0."project_id" = p."id"
        ORDER BY dm0."created_at" DESC, dm0."id" DESC
        LIMIT 1
    ) dm ON TRUE
    LEFT JOIN LATERAL (
        SELECT jsonb_build_object(
            'intake_date', i."intake_date",
            'bl', i."bl",
            'ap', i."ap",
            'cp', i."cp",
            'csc', i."csc",
            'rf_cal', i."rf_cal",
            'hw_rev', i."hw_rev",
            'note', i."note",
            'tech_group', i."tech_group"
        ) AS "latest_intake"
        FROM "sample_intakes" i
        WHERE i."sample_id" = s."id"
        ORDER BY i."created_at" DESC, i."id" DESC
        LIMIT 1
    ) latest ON TRUE
    LEFT JOIN LATERAL (
        SELECT MAX(r."revision_number") AS "sample_revision"
        FROM "sample_inventory_revisions" r
        WHERE r."sample_id" = s."id"
    ) revision ON TRUE
    WHERE ts0."session_origin" = 'WEB_SESSION'
      AND ts0."sample_snapshot_json" IS NULL
      AND ts0."sample_snapshot_schema_version" IS NULL
) snapshot
WHERE ts."id" = snapshot."session_id";

-- A pre-029 WEB_SESSION row without a complete project/sample join cannot be
-- given a truthful snapshot. Keep its snapshot columns NULL and clear the
-- stale origin marker to the existing unknown/legacy state so the final
-- validated invariant applies to every new WEB_SESSION row without fabricating
-- provenance. LOCAL_PROGRAM rows are intentionally untouched.
UPDATE "test_sessions"
SET "session_origin" = NULL
WHERE "session_origin" = 'WEB_SESSION'
  AND (
      "project_id" IS NULL
      OR "sample_id" IS NULL
      OR "sample_snapshot_json" IS NULL
      OR "sample_snapshot_schema_version" IS NULL
  );

-- The upgrade must finish with a database-owned, validated constraint. A
-- NOT VALID constraint above only protects new writes; this statement is the
-- evidence-bearing completion point for the pre-029 backfill.
ALTER TABLE "test_sessions"
    VALIDATE CONSTRAINT "ck_test_sessions_web_snapshot_complete";

ALTER TABLE "audit_events" DROP CONSTRAINT IF EXISTS "ck_audit_events_event_type";
ALTER TABLE "audit_events"
    ADD CONSTRAINT "ck_audit_events_event_type"
    CHECK ("event_type" IN (
        'claim.acquired', 'claim.released', 'claim.expired',
        'membership.assigned', 'membership.revoked', 'account.unlocked',
        'sample.hard_deleted'
    ));

-- The unified write permission replaces the retired PM/RF import split. Seed it
-- for already-deployed project roles before the API cutover reaches them.
INSERT INTO "permissions" ("id", "permission_key", "description")
VALUES (
    gen_random_uuid(),
    'platform:sample-write',
    'Create and edit all web sample and append-only intake fields; status changes and ordinary delete also write sample revisions.'
)
ON CONFLICT ("permission_key") DO NOTHING;

INSERT INTO "role_permissions" ("role_id", "permission_id")
SELECT r."id", p."id"
FROM "roles" r, "permissions" p
WHERE r."role_key" IN ('project_engineer', 'project_admin', 'project_pm')
  AND p."permission_key" = 'platform:sample-write'
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS "global_role_grants" (
    "role_key" TEXT NOT NULL REFERENCES "roles"("role_key"),
    "permission_key" TEXT NOT NULL REFERENCES "permissions"("permission_key")
);
CREATE UNIQUE INDEX IF NOT EXISTS "ux_global_role_grants_role_permission"
    ON "global_role_grants" ("role_key", "permission_key");
CREATE INDEX IF NOT EXISTS "idx_global_role_grants_permission"
    ON "global_role_grants" ("permission_key");

-- Global system-admin grant is separate from project membership grants. The
-- natural keys make this block safe on fresh databases and repeatable upgrades.
INSERT INTO "permissions" ("id", "permission_key", "description")
VALUES (
    gen_random_uuid(),
    'platform:sample-hard-delete',
    'Global system_admin-only physical deletion of sample operational rows; leaves a PII-free audit tombstone.'
)
ON CONFLICT ("permission_key") DO NOTHING;

INSERT INTO "roles" ("id", "role_key", "description")
VALUES (
    gen_random_uuid(),
    'system_admin',
    'Global platform operator. May physically delete sample operational rows only through the dedicated hard-delete operation.'
)
ON CONFLICT ("role_key") DO NOTHING;

INSERT INTO "global_role_grants" ("role_key", "permission_key")
VALUES ('system_admin', 'platform:sample-hard-delete')
ON CONFLICT DO NOTHING;

INSERT INTO "role_permissions" ("role_id", "permission_id")
SELECT r."id", p."id"
FROM "roles" r, "permissions" p
WHERE r."role_key" = 'system_admin'
  AND p."permission_key" = 'platform:sample-hard-delete'
ON CONFLICT DO NOTHING;

COMMIT;
