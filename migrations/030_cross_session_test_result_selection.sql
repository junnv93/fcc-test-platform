-- Cross-session project-result selection and generic reference revisions.
-- Additive, append-only, provider-scoped repair. This migration deliberately
-- creates no historical selection events or reference revisions: absent history
-- means automatic latest and no reusable reference.
BEGIN;

CREATE TABLE IF NOT EXISTS "project_result_selection_events" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "condition_hash" TEXT NOT NULL,
    "action" TEXT NOT NULL,
    "attempt_id" UUID REFERENCES "measurement_attempts"("id"),
    "revision" INTEGER NOT NULL,
    "predecessor_event_id" UUID REFERENCES "project_result_selection_events"("id"),
    "expected_revision" INTEGER NOT NULL,
    "actor_subject" TEXT NOT NULL,
    "occurred_at" TIMESTAMPTZ NOT NULL,
    "reason" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "ck_project_result_selection_action_attempt"
        CHECK (("action" = 'selected' AND "attempt_id" IS NOT NULL)
            OR ("action" = 'cleared' AND "attempt_id" IS NULL)),
    CONSTRAINT "ck_project_result_selection_action"
        CHECK ("action" IN ('selected', 'cleared')),
    CONSTRAINT "ck_project_result_selection_revision_positive"
        CHECK ("revision" > 0 AND "expected_revision" >= 0
            AND "revision" = "expected_revision" + 1)
);

CREATE UNIQUE INDEX IF NOT EXISTS "ux_project_result_selection_partition_revision"
    ON "project_result_selection_events"
        ("project_id", "provider_id", "condition_hash", "revision");
CREATE INDEX IF NOT EXISTS "idx_project_result_selection_partition_latest"
    ON "project_result_selection_events"
        ("project_id", "provider_id", "condition_hash", "revision", "occurred_at");
CREATE INDEX IF NOT EXISTS "idx_project_result_selection_attempt"
    ON "project_result_selection_events" ("attempt_id");

CREATE TABLE IF NOT EXISTS "project_result_reference_revisions" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "producer_provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "revision_number" INTEGER NOT NULL,
    "reference_type" TEXT NOT NULL,
    "schema_version" TEXT NOT NULL,
    "source_selection_event_id" UUID NOT NULL REFERENCES "project_result_selection_events"("id"),
    "source_attempt_id" UUID NOT NULL REFERENCES "measurement_attempts"("id"),
    "source_session_id" UUID NOT NULL REFERENCES "test_sessions"("id"),
    "source_sample_id" UUID REFERENCES "samples"("id"),
    "source_chamber_id" TEXT,
    "payload_json" JSONB NOT NULL,
    "content_sha256" TEXT NOT NULL,
    "state" TEXT NOT NULL,
    "created_by" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "retired_by" TEXT,
    "retired_at" TIMESTAMPTZ,
    "retirement_reason" TEXT,
    CONSTRAINT "ck_project_result_reference_state"
        CHECK ("state" IN ('published', 'retired')),
    CONSTRAINT "ck_project_result_reference_revision_positive"
        CHECK ("revision_number" > 0),
    CONSTRAINT "ck_project_result_reference_hash"
        CHECK ("content_sha256" ~ '^[0-9a-fA-F]{64}$'),
    CONSTRAINT "ck_project_result_reference_retirement"
        CHECK (("state" = 'published' AND "retired_by" IS NULL
                AND "retired_at" IS NULL AND "retirement_reason" IS NULL)
            OR ("state" = 'retired' AND "retired_by" IS NOT NULL
                AND "retired_at" IS NOT NULL AND "retirement_reason" IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS "ux_project_result_reference_project_provider_type_revision"
    ON "project_result_reference_revisions"
        ("project_id", "producer_provider_id", "reference_type", "revision_number");
CREATE INDEX IF NOT EXISTS "idx_project_result_reference_source_selection"
    ON "project_result_reference_revisions" ("source_selection_event_id");
CREATE INDEX IF NOT EXISTS "idx_project_result_reference_provider_state"
    ON "project_result_reference_revisions"
        ("producer_provider_id", "state", "reference_type", "schema_version");
CREATE INDEX IF NOT EXISTS "idx_project_result_reference_project_created"
    ON "project_result_reference_revisions" ("project_id", "created_at", "id");

-- The local Session snapshot pair is nullable for legacy sessions.  Keep the
-- two ALTER statements independent so an interrupted/partial deployment that
-- already added either column converges on rerun without manufacturing a
-- partial contract or rewriting existing session bytes.
ALTER TABLE "test_sessions"
    ADD COLUMN IF NOT EXISTS "project_result_reference_snapshot_json" TEXT;
ALTER TABLE "test_sessions"
    ADD COLUMN IF NOT EXISTS "project_result_reference_snapshot_schema_version" TEXT;

-- Keep the historical attempt indexes: the ingestion writer still supports the
-- three-column ON CONFLICT target for legacy replays. Add the provider-scoped
-- identity/indexes without changing the fresh 001 compatibility surface.
CREATE UNIQUE INDEX IF NOT EXISTS "ux_measurement_attempts_provider_session_condition_attempt"
    ON "measurement_attempts"
        ("provider_id", "session_id", "condition_hash", "attempt_number");
CREATE INDEX IF NOT EXISTS "idx_measurement_attempts_project_provider_condition_hash"
    ON "measurement_attempts" ("project_id", "provider_id", "condition_hash");
DROP INDEX IF EXISTS "idx_measurement_attempts_is_latest";
CREATE INDEX IF NOT EXISTS "idx_measurement_attempts_is_latest"
    ON "measurement_attempts"
        ("project_id", "provider_id", "condition_hash", "is_latest");
DROP INDEX IF EXISTS "idx_measurement_attempts_project_provider_condition_recency";
CREATE INDEX "idx_measurement_attempts_project_provider_condition_recency"
    ON "measurement_attempts"
        ("project_id", "provider_id", "condition_hash",
         "measured_at" DESC NULLS LAST, "created_at" DESC, "id" DESC)
    WHERE "status" = 'completed';

DO $$
DECLARE
    attempt_count BIGINT;
    partition_count BIGINT;
BEGIN
    -- The duplicate latest guard below is intentionally explicit so migration
    -- verification can distinguish a failed repair from an empty partition.
    CREATE TEMP TABLE "_030_attempt_snapshot" ON COMMIT DROP AS
    SELECT COUNT(*) AS attempt_count,
           COUNT(DISTINCT ("project_id", "provider_id", "condition_hash"))
               AS partition_count,
           COALESCE(md5(string_agg(
               "id"::text || ':' || md5("result_json"::text) || ':'
                   || COALESCE("provenance_json"::text, ''),
               '|' ORDER BY "id"::text
           )), md5('')) AS payload_checksum
    FROM "measurement_attempts";
    SELECT COUNT(*), COUNT(DISTINCT ("project_id", "provider_id", "condition_hash"))
      INTO attempt_count, partition_count
      FROM "measurement_attempts";
    RAISE NOTICE '030 before is_latest repair: attempts=%, provider_partitions=%',
        attempt_count, partition_count;
END $$;

UPDATE "measurement_attempts"
SET "is_latest" = false;

WITH ranked AS (
    SELECT "id",
           ROW_NUMBER() OVER (
               PARTITION BY "project_id", "provider_id", "condition_hash"
               ORDER BY "measured_at" DESC NULLS LAST,
                        "created_at" DESC,
                        "id" DESC
           ) AS "rn"
    FROM "measurement_attempts"
    WHERE "status" = 'completed'
)
UPDATE "measurement_attempts" AS target
SET "is_latest" = (ranked."rn" = 1)
FROM ranked
WHERE target."id" = ranked."id";

-- Rebuild the materialized projection because PostgreSQL cannot alter the
-- identity/column set of a materialized view in place. No dependent platform
-- view uses this object in the historical schema.
DROP MATERIALIZED VIEW IF EXISTS "coverage_by_condition_hash";
CREATE MATERIALIZED VIEW "coverage_by_condition_hash" AS
SELECT a."project_id",
       a."provider_id",
       a."technology",
       a."condition_hash",
       a."session_id" AS "latest_session_id",
       a."operator" AS "latest_operator",
       a."measured_at" AS "latest_measured_at",
       a."verdict" AS "latest_verdict",
       a."attempt_number" AS "latest_attempt_number",
       agg."attempt_count",
       agg."distinct_session_count",
       agg."distinct_operator_count"
FROM "measurement_attempts" a
JOIN (
    SELECT "project_id", "technology", "condition_hash", "provider_id",
           COUNT(*) AS "attempt_count",
           COUNT(DISTINCT "session_id") AS "distinct_session_count",
           COUNT(DISTINCT "operator") AS "distinct_operator_count"
    FROM "measurement_attempts"
    WHERE "status" = 'completed'
    GROUP BY "project_id", "technology", "condition_hash", "provider_id"
) agg
 ON (agg."project_id" IS NOT DISTINCT FROM a."project_id")
 AND agg."provider_id" = a."provider_id"
 AND agg."technology" = a."technology"
 AND agg."condition_hash" = a."condition_hash"
WHERE a."is_latest" = true
  AND a."status" = 'completed';

CREATE UNIQUE INDEX IF NOT EXISTS "ux_coverage_by_condition_hash"
    ON "coverage_by_condition_hash" ("project_id", "provider_id", "technology", "condition_hash");
CREATE INDEX IF NOT EXISTS "idx_coverage_by_condition_hash_operator"
    ON "coverage_by_condition_hash" ("latest_operator");
CREATE INDEX IF NOT EXISTS "idx_coverage_by_condition_hash_measured"
    ON "coverage_by_condition_hash" ("latest_measured_at");
REFRESH MATERIALIZED VIEW "coverage_by_condition_hash";

DO $$
DECLARE
    latest_count BIGINT;
    duplicate_count BIGINT;
    eligible_partition_count BIGINT;
    repaired_partition_count BIGINT;
    after_attempt_count BIGINT;
    after_partition_count BIGINT;
    after_checksum TEXT;
BEGIN
    SELECT COUNT(*) INTO eligible_partition_count
      FROM (
          SELECT "project_id", "provider_id", "condition_hash"
          FROM "measurement_attempts"
          WHERE "status" = 'completed'
          GROUP BY "project_id", "provider_id", "condition_hash"
      ) eligible;
    SELECT COUNT(*) INTO latest_count
      FROM "measurement_attempts" WHERE "is_latest" = true;
    SELECT COUNT(*) INTO duplicate_count
      FROM (
          SELECT "project_id", "provider_id", "condition_hash"
          FROM "measurement_attempts"
          WHERE "is_latest" = true
          GROUP BY "project_id", "provider_id", "condition_hash"
          HAVING COUNT(*) > 1
      ) duplicates;
    IF duplicate_count > 0 THEN
        RAISE EXCEPTION '030 is_latest repair left % duplicate provider partitions',
            duplicate_count;
    END IF;
    SELECT COUNT(*) INTO repaired_partition_count
      FROM (
          SELECT "project_id", "provider_id", "condition_hash"
          FROM "measurement_attempts"
          WHERE "status" = 'completed' AND "is_latest" = true
          GROUP BY "project_id", "provider_id", "condition_hash"
          HAVING COUNT(*) = 1
      ) exact_partitions;
    IF repaired_partition_count <> eligible_partition_count THEN
        RAISE EXCEPTION '030 expected one latest per eligible provider partition: eligible=%, repaired=%',
            eligible_partition_count, repaired_partition_count;
    END IF;
    SELECT COUNT(*), COUNT(DISTINCT ("project_id", "provider_id", "condition_hash")),
           COALESCE(md5(string_agg(
               "id"::text || ':' || md5("result_json"::text) || ':'
                   || COALESCE("provenance_json"::text, ''),
               '|' ORDER BY "id"::text
           )), md5(''))
      INTO after_attempt_count, after_partition_count, after_checksum
      FROM "measurement_attempts";
    IF after_attempt_count <> (SELECT attempt_count FROM "_030_attempt_snapshot")
       OR after_partition_count <> (SELECT partition_count FROM "_030_attempt_snapshot")
       OR after_checksum <> (SELECT payload_checksum FROM "_030_attempt_snapshot") THEN
        RAISE EXCEPTION '030 attempt facts changed during latest repair';
    END IF;
    RAISE NOTICE '030 preserved attempts=%, partitions=%, checksum=%',
        after_attempt_count, after_partition_count, after_checksum;
    RAISE NOTICE '030 after is_latest repair: latest_attempts=%', latest_count;
END $$;

COMMIT;
