-- 012_report_run_ingestion_parent.sql
-- Report-output ingestion creates report_runs in the same transaction as its
-- children. Keep the creation timestamp database-owned for upgraded central DBs,
-- matching the canonical schema and freshly exported 001 DDL.
ALTER TABLE "report_runs" ALTER COLUMN "created_at" SET DEFAULT now();

--rollback ALTER TABLE "report_runs" ALTER COLUMN "created_at" DROP DEFAULT;
