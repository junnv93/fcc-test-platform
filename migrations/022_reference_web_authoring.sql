-- 022_reference_web_authoring.sql
-- Wave B-1 (2026-08-11) — a third provenance kind, and the nullability it needs.
--
-- WHY AN INCREMENTAL MIGRATION AT ALL
--   001 is rendered from docs/platform/central_db_schema.v1.json, but only for a
--   FRESH database. A central DB that already ran 001..021 never sees the new
--   render, so the delta lives here. Additive + idempotent, safe to re-run
--   (019 is the precedent for this exact shape).
--
-- WHY A THIRD KIND
--   Operator decision 2026-08-10 (CLAUDE.md Deployment Policy §4): the tester
--   never opens the workbook again. A revision can then be born on the web with
--   no workbook behind it at all. Labelling that WORKBOOK is a false audit
--   record for exactly the values with the least paper trail; labelling it
--   FORK_EDIT claims an ancestry it does not have.
--
-- WHY THE SNAPSHOT COLUMNS BECOME NULLABLE — AND WHY THAT IS NOT A RELAXATION
--   source_snapshot_id / source_manifest_sha256 are facts about a workbook
--   snapshot. A web-authored revision has no such snapshot, so there is no value
--   to put there and NOT NULL would force an invented one.
--
--   But simply dropping NOT NULL would weaken the importer path too: the guarantee
--   "a WORKBOOK revision can always be traced back to the sheet it came from"
--   would quietly disappear, and nothing would notice until an audit asked. So
--   NOT NULL is REPLACED, not removed — a CHECK ties the nullability to the
--   provenance. That is the same move 019 made when it applied its CHECK after
--   the backfill rather than trusting the column.
--
-- THREE-WAY PARITY
--   domain enum RevisionProvenanceKind × central_db_schema.v1.json allowed_values
--   × the CHECK below. Sealed by tests/test_reference_provenance_kind.py, which
--   parses all three from the files rather than restating an expectation.
--
--   The constraint NAME must keep matching what the exporter renders into 001,
--   or a fresh DB and an upgraded DB end up with differently-named constraints.
--
-- DIALECT: PostgreSQL, like 001..021. The SQLite test shim builds from exporter
-- DDL and does not execute these files.

BEGIN;

-- 1) Widen the provenance vocabulary. CHECK has no ADD ... IF NOT EXISTS, so
--    DROP IF EXISTS first keeps this idempotent (007/019 precedent).
ALTER TABLE "reference_revisions"
  DROP CONSTRAINT IF EXISTS "ck_reference_revisions_provenance_kind";

ALTER TABLE "reference_revisions"
  ADD CONSTRAINT "ck_reference_revisions_provenance_kind"
  CHECK ("provenance_kind" IN ('WORKBOOK', 'FORK_EDIT', 'WEB_AUTHORED'));

-- 2) Let the snapshot link be absent.
ALTER TABLE "reference_revisions"
  ALTER COLUMN "source_snapshot_id" DROP NOT NULL,
  ALTER COLUMN "source_manifest_sha256" DROP NOT NULL;

-- 3) ...but only for the kind that legitimately has none. This is the guarantee
--    NOT NULL used to give the importer, restated where it is still true.
ALTER TABLE "reference_revisions"
  DROP CONSTRAINT IF EXISTS "ck_reference_revisions_snapshot_link";

ALTER TABLE "reference_revisions"
  ADD CONSTRAINT "ck_reference_revisions_snapshot_link"
  CHECK (
    "provenance_kind" = 'WEB_AUTHORED'
    OR ("source_snapshot_id" IS NOT NULL AND "source_manifest_sha256" IS NOT NULL)
  );

COMMIT;
