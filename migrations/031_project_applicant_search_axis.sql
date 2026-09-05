-- 031_project_applicant_search_axis.sql
-- Applicant becomes the single requesting-party axis (2026-09-04).
--
-- Two columns held the same party: `customer` (고객사) and `applicant_name`
-- (신청자). The FCC report cover names ONE requesting party, so operators picked
-- whichever box they saw first and the same company ended up split across both.
-- The `?q` search axis indexed only `customer` (domain SSOT
-- project_directory_query.PROJECT_SEARCH_COLUMNS), so a project whose party was
-- typed into the applicant box **could not be found at all** — search was
-- structurally blind to half the corpus.
--
-- The domain retires `customer` (project_metadata_edit.RETIRED_PROJECT_META_FIELDS)
-- and moves the axis to `applicant_name`. This file is the FORWARD half of that
-- move: it builds the new indexes. The value merge + column drop is 032, which
-- MUST run after this one so the search axis is never index-less while serving.
--
-- Source of truth is docs/platform/central_db_schema.v1.json (`projects.indexes`);
-- the DDL exporter renders the same statements into 001 for a FRESH DB. Never
-- hand-edit either artifact — regenerate with scripts/export_platform_central_db_ddl.py.
--
-- Why each index exists:
--
--   * idx_projects_search_applicant_name (GIN trigram on lower(applicant_name))
--     Replaces idx_projects_search_customer one-for-one on the new axis.
--     `LOWER(col) LIKE '%q%'` is unanchored, so no B-tree can serve it; trigram
--     GIN can, and the planner BitmapOr-s it with the other two search indexes.
--   * idx_projects_applicant_directory (lower(applicant_name), created_at DESC,
--     id DESC) WHERE applicant_name IS NOT NULL
--     Serves the NEW applicant suggestion lookup (GET /platform/applicants),
--     which takes the most recent row per normalized applicant via
--     ROW_NUMBER() OVER (PARTITION BY lower(applicant_name)
--                        ORDER BY created_at DESC, id DESC).
--     Index order == partition/order of the window, so the window runs over an
--     ordered scan instead of sorting the whole candidate set. PARTIAL because a
--     row with no applicant can never be a suggestion — indexing it is pure bloat.
--
-- Dialect: PostgreSQL only (like 001-030). The SQLite test shim does NOT run these
-- incremental .sql files, so the PG-only syntax here is intentional.
--
-- Non-transactional: every statement uses CONCURRENTLY so the migration never
-- takes an ACCESS EXCLUSIVE lock on `projects` while the platform API is serving.
-- scripts/platform_db_migrate.py auto-detects the CONCURRENTLY keyword in the
-- executable body and applies the file statement-by-statement in autocommit.
-- Hazard (same as 010): a failed CREATE INDEX CONCURRENTLY leaves an INVALID
-- index behind. Each CREATE is preceded by a DROP ... CONCURRENTLY IF EXISTS so a
-- retry self-heals, and the runner's INVALID-index guard is the backstop.
--
-- Reversibility (--rollback annotations, Liquibase formatted-SQL convention):
-- LOSSLESS and total — indexes carry no data, so dropping the two returns the DB
-- to its pre-031 shape exactly. idx_projects_search_customer is NOT recreated
-- here: it still exists at this point (032 drops it with the column), so a
-- rollback of 031 alone leaves the old axis intact and serving.
--
--rollback DROP INDEX CONCURRENTLY IF EXISTS "idx_projects_applicant_directory";
--rollback DROP INDEX CONCURRENTLY IF EXISTS "idx_projects_search_applicant_name";

CREATE EXTENSION IF NOT EXISTS pg_trgm;

DROP INDEX CONCURRENTLY IF EXISTS "idx_projects_search_applicant_name";
CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_projects_search_applicant_name"
    ON "projects" USING gin (lower(applicant_name) gin_trgm_ops);

DROP INDEX CONCURRENTLY IF EXISTS "idx_projects_applicant_directory";
CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_projects_applicant_directory"
    ON "projects" (lower(applicant_name), "created_at" DESC, "id" DESC)
    WHERE applicant_name IS NOT NULL;

-- Expression indexes carry their OWN statistics, and CREATE INDEX does not
-- collect them. Without this ANALYZE the planner prices `LOWER(col) LIKE ...`
-- off default selectivity and can keep choosing a sequential scan even though the
-- trigram index is present — i.e. the migration would appear to have done nothing.
-- Read-only with respect to row data; safe to re-run. (032 re-runs it after the
-- backfill, because the backfill changes the column's value distribution.)
ANALYZE "projects";
