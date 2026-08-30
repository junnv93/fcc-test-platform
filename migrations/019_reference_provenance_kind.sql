-- FCC central migration 019: state a reference revision's provenance instead of
-- leaving it to be inferred (웹에서의 참조값 저작, 2026-08-09).
--
-- 001_initial_central_db.sql renders the column from the schema SSOT, but only
-- for a FRESH DB — an already-deployed central DB never picks up a column added
-- later. This migration brings an existing DB to the SAME sealed shape,
-- additively and idempotently (safe to re-run).
--
-- WHY the column exists at all:
--   source_snapshot_id answers "where did this edition start". For every
--   revision that has ever existed it ALSO answered "where did these values come
--   from", because the only way to make one was to import a workbook. Web
--   authoring splits those two questions: fork snapshot X, re-measure one port's
--   cable loss, and X is still the honest starting point while some values are
--   no longer X's. Leaving the reader to infer that from forked_from_revision_id
--   would give one field a quiet second meaning — the failure mode this
--   repository has paid for repeatedly — on an axis that carries audit evidence.
--
-- WHY 'WORKBOOK' is the correct backfill (not 'unknown', and not a nullable
-- column): every row that exists when this migration runs was created by the
-- workbook importer, which is the only writer that has ever existed. The value
-- is a known fact about those rows, not a guess, so a third "unknown" token
-- would be less true than the backfill and would force every reader to handle a
-- state that never occurs.
--
-- The vocabulary mirrors the domain enum RevisionProvenanceKind and is sealed
-- against it AND against this CHECK by a three-way parity test
-- (tests/test_reference_provenance_kind.py). The constraint buys integrity
-- against a corrupt writer; the test buys no-drift. Constraint name matches the
-- exporter-rendered 001 name (ck_reference_revisions_provenance_kind).
--
-- Dialect: PostgreSQL only (like 001-016). The SQLite test shim builds its
-- schema from the exporter DDL and does not run these incremental files.

BEGIN;

-- Nullable first: an existing populated table cannot take a NOT NULL column
-- without a value for the rows already there.
ALTER TABLE "reference_revisions"
    ADD COLUMN IF NOT EXISTS "provenance_kind" TEXT;

UPDATE "reference_revisions"
   SET "provenance_kind" = 'WORKBOOK'
 WHERE "provenance_kind" IS NULL;

ALTER TABLE "reference_revisions"
    ALTER COLUMN "provenance_kind" SET DEFAULT 'WORKBOOK',
    ALTER COLUMN "provenance_kind" SET NOT NULL;

-- CHECK constraints have no ADD ... IF NOT EXISTS form, so DROP IF EXISTS keeps
-- this idempotent (007 precedent). Applying it AFTER the backfill means a row
-- carrying an out-of-domain value would fail loudly here, which is the correct
-- fail-closed behaviour — an unexpected legacy value must be reconciled
-- deliberately rather than silently kept.
ALTER TABLE "reference_revisions"
    DROP CONSTRAINT IF EXISTS "ck_reference_revisions_provenance_kind";
ALTER TABLE "reference_revisions"
    ADD CONSTRAINT "ck_reference_revisions_provenance_kind"
    CHECK ("provenance_kind" IN ('WORKBOOK', 'FORK_EDIT'));

COMMIT;
