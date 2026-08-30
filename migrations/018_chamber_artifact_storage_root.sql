-- 018_chamber_artifact_storage_root.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (챔버별 플롯 저장 위치).
--
-- 001_initial_central_db.sql is applied only on FIRST boot
-- (docker-entrypoint-initdb.d), so a central DB created before this change will
-- NOT pick the column up by re-running 001. This migration adds it additively
-- and idempotently (safe to re-run).
--
-- Source of truth is docs/platform/central_db_schema.v1.json — 001 is regenerated
-- from it by scripts/export_platform_central_db_ddl.py. For a fresh DB, 001
-- already contains the column; 018 is a no-op there.
--
-- Purely additive: nullable column, no default, no backfill, no index change.
-- Every existing row keeps behaving exactly as before (NULL ⇒ the node falls back
-- to the workbook Save Data 'File Structure' cell, which is today's behaviour).
--
-- Domain: 플롯을 어디에 저장하느냐는 **그 챔버 PC 의 속성**이다 — 운영자 의도는
-- "웹 UI 에서 챔버 PC 의 저장 위치를 지정"이었고, 오늘은 측정 시작 요청마다 지정하거나
-- 시험원이 워크북 칸에 손으로 적는다. 워크북 칸은 손으로 적는 자리라 로컬 경로가
-- 들어오기 쉽고, 그러면 플롯이 챔버 PC 에만 남아 심사 때 증거가 없다.
-- 설계 근거: .claude/exec-plans/active/2026-08-09-plot-custody-web-and-chamber.md

BEGIN;

ALTER TABLE "chamber_nodes"
    ADD COLUMN IF NOT EXISTS "artifact_storage_root" TEXT;

COMMIT;
