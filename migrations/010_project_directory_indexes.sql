-- 010_project_directory_indexes.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (W3 백엔드 — 프로젝트
-- 디렉터리 스케일).
--
-- 001_initial_central_db.sql now renders five additional `projects` indexes plus
-- the pg_trgm extension — but that only applies to a FRESH DB. An existing DB
-- still answers `GET /platform/projects` with a full sequential scan + sort, so
-- the new keyset pagination and `?q` search would be correct but not fast. This
-- migration brings an existing DB to the SAME state additively and idempotently.
--
-- Source of truth is docs/platform/central_db_schema.v1.json
-- (`required_extensions` + `projects.indexes`); the DDL exporter renders the same
-- five CREATE INDEX statements + the same extension into 001. Never hand-edit
-- either artifact — regenerate with scripts/export_platform_central_db_ddl.py.
--
-- Why each index exists (measured on PostgreSQL 16.14, 50 000 seeded projects —
-- deliberately ~10x beyond the realistic corpus, throwaway DB, EXPLAIN ANALYZE):
--
--   * idx_projects_directory (created_at, id)
--     Serves `ORDER BY created_at DESC, id DESC LIMIT n` via Index Only Scan
--     BACKWARD — an ASC index covers a fully-reversed sort, so no DESC index is
--     needed. `id` is the tie-breaker that makes the sort a TOTAL order; without
--     it two projects sharing a created_at can straddle a page boundary and be
--     returned twice or skipped entirely.
--     Chosen for ?status=all: 0.049 ms (was 289 ms unbounded / 12.4 ms LIMIT-only).
--   * idx_projects_status_directory (status, created_at, id)
--     Same order under the ?status filter (the default view). It earns its keep
--     over the previous index when the filtered status is the selective one:
--     ?status=completed (10% of rows) chose THIS index at 0.061 ms.
--   * idx_projects_search_* (GIN trigram on LOWER(column))
--     `LOWER(col) LIKE '%q%'` is unanchored, so no B-tree can serve it. Trigram
--     GIN can. Worst case (a search matching nothing, which a directory search box
--     produces constantly while the operator types): 27.6 ms Seq Scan -> 0.11 ms
--     BitmapOr over the three indexes. The planner correctly falls back to the
--     ordering index when the search matches broadly — the two index families
--     cover different selectivity regimes, which is why both are shipped.
--
-- Dialect: PostgreSQL only (like 001-009). The SQLite test shim does NOT run these
-- incremental .sql files (it builds its schema from the exporter DDL / hand-written
-- fixtures), so the PG-only syntax here is intentional, not a portability gap.
--
-- Non-transactional: every statement uses CONCURRENTLY so the migration never
-- takes an ACCESS EXCLUSIVE lock on `projects` while the platform API is serving.
-- scripts/platform_db_migrate.py auto-detects the CONCURRENTLY keyword in the
-- executable body and applies the file statement-by-statement in autocommit.
-- Hazard (same as 008): a failed CREATE INDEX CONCURRENTLY leaves an INVALID
-- index behind. Each CREATE is preceded by a DROP ... CONCURRENTLY IF EXISTS so a
-- retry self-heals, and the runner's INVALID-index guard is the backstop.
--
-- Reversibility (--rollback annotations, Liquibase formatted-SQL convention — the
-- same convention 008 established): the `--rollback <stmt>` lines below are the
-- DOWN migration. They are ordinary SQL comments to the forward runner, so the
-- FORWARD apply is byte-safe. Rollback scope is LOSSLESS and total: indexes carry
-- no data, so dropping all five returns the DB to its pre-010 shape exactly. The
-- pg_trgm EXTENSION is deliberately NOT dropped — it is additive, harmless, and
-- another object could come to depend on it; DROP EXTENSION would then cascade.
--
--rollback DROP INDEX CONCURRENTLY IF EXISTS "idx_projects_search_customer";
--rollback DROP INDEX CONCURRENTLY IF EXISTS "idx_projects_search_project_code";
--rollback DROP INDEX CONCURRENTLY IF EXISTS "idx_projects_search_management_number";
--rollback DROP INDEX CONCURRENTLY IF EXISTS "idx_projects_status_directory";
--rollback DROP INDEX CONCURRENTLY IF EXISTS "idx_projects_directory";

CREATE EXTENSION IF NOT EXISTS pg_trgm;

DROP INDEX CONCURRENTLY IF EXISTS "idx_projects_directory";
CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_projects_directory"
    ON "projects" ("created_at", "id");

DROP INDEX CONCURRENTLY IF EXISTS "idx_projects_status_directory";
CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_projects_status_directory"
    ON "projects" ("status", "created_at", "id");

DROP INDEX CONCURRENTLY IF EXISTS "idx_projects_search_management_number";
CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_projects_search_management_number"
    ON "projects" USING gin (lower(management_number) gin_trgm_ops);

DROP INDEX CONCURRENTLY IF EXISTS "idx_projects_search_project_code";
CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_projects_search_project_code"
    ON "projects" USING gin (lower(project_code) gin_trgm_ops);

DROP INDEX CONCURRENTLY IF EXISTS "idx_projects_search_customer";
CREATE INDEX CONCURRENTLY IF NOT EXISTS "idx_projects_search_customer"
    ON "projects" USING gin (lower(customer) gin_trgm_ops);

-- Expression indexes carry their OWN statistics, and CREATE INDEX does not
-- collect them. Without this ANALYZE the planner prices `LOWER(col) LIKE ...`
-- off default selectivity and can keep choosing a sequential scan even though the
-- trigram index is present — i.e. the migration would appear to have done nothing.
-- Measured: 37.1 ms (stale stats, Seq Scan) vs 0.248 ms (fresh stats, BitmapOr).
-- Read-only with respect to row data; safe to re-run.
ANALYZE "projects";
