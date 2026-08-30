-- 015_test_equipment_lists.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (프로젝트별 실사용 장비목록).
--
-- 001_initial_central_db.sql is applied only on FIRST boot
-- (docker-entrypoint-initdb.d) with CREATE TABLE IF NOT EXISTS — so a central DB
-- created before this change will NOT pick up these tables by re-running 001.
-- This migration adds them additively and idempotently (safe to re-run).
--
-- Source of truth is docs/platform/central_db_schema.v1.json — every table /
-- column / index here MUST already exist there (001 is regenerated from it by
-- scripts/export_platform_central_db_ddl.py). For a fresh DB, 001 already
-- contains both tables; 015 is a no-op there.
--
-- Purely additive (no existing table / column / index is modified or dropped).
--
-- Domain: 성적서 §6 TEST AND MEASUREMENT EQUIPMENT 표. EMS(사내 장비관리시스템)가
-- 표준 장비리스트의 SSOT 이고, 이 테이블들은 성적서 작성 시점에 pull 한 값을
-- 시험원이 실사용본으로 편집·확정한 불변 스냅샷이다. 설계 근거:
-- .claude/exec-plans/active/2026-08-07-report-equipment-list-domain-migration.md

BEGIN;

-- 프로젝트(또는 성적서 판)별 장비목록 헤더.
-- test_report_id 는 nullable — 시험원은 성적서 행을 만들기 전(시험 ~90% 시점)부터
-- 장비를 고를 수 있고, 그 단계의 목록은 프로젝트에만 귀속된다.
CREATE TABLE IF NOT EXISTS "test_equipment_lists" (
    "id" UUID PRIMARY KEY,
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "test_report_id" UUID REFERENCES "test_reports"("id"),
    "test_item_key" TEXT NOT NULL,
    "test_item_name" TEXT,
    "status" TEXT NOT NULL DEFAULT 'draft' CONSTRAINT "ck_test_equipment_lists_status" CHECK ("status" IN ('draft', 'confirmed')),
    "source_profile_key" TEXT,
    "source_revision_key" TEXT,
    "source_pulled_at" TIMESTAMPTZ,
    "confirmed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

-- 두 단계의 natural key 를 부분 unique 인덱스로 각각 봉인한다.
-- (a) 성적서 연결 전: 프로젝트 × 시험항목에 목록 하나.
CREATE UNIQUE INDEX IF NOT EXISTS "ux_test_equipment_lists_project_item"
    ON "test_equipment_lists" ("project_id", "test_item_key") WHERE test_report_id IS NULL;
-- (b) 성적서 연결 후: 성적서 판 × 시험항목에 목록 하나. 같은 모델에 여러 판을 내면서
--     장비가 달라질 수 있으므로 판마다 별도 행을 갖는다.
CREATE UNIQUE INDEX IF NOT EXISTS "ux_test_equipment_lists_report_item"
    ON "test_equipment_lists" ("test_report_id", "test_item_key") WHERE test_report_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS "idx_test_equipment_lists_project" ON "test_equipment_lists" ("project_id");
CREATE INDEX IF NOT EXISTS "idx_test_equipment_lists_report" ON "test_equipment_lists" ("test_report_id");

-- 목록에 속한 장비 / 시험용 소프트웨어 행. 확정 시점의 불변 스냅샷이며 EMS master 를
-- 조인하지 않는다(값 복사). 컬럼은 성적서 §6 의 두 표를 그대로 따른다.
-- calibration_due_date 는 date 가 아니라 text — 원천 값이 'N/A' 를 포함하고 형식도
-- 섞여 있어 제공된 그대로 보관한다(test_reports.date_tested_* 선례).
CREATE TABLE IF NOT EXISTS "test_equipment_list_items" (
    "id" UUID PRIMARY KEY,
    "list_id" UUID NOT NULL REFERENCES "test_equipment_lists"("id"),
    "item_type" TEXT NOT NULL CONSTRAINT "ck_test_equipment_list_items_item_type" CHECK ("item_type" IN ('equipment', 'test_software')),
    "section_name" TEXT,
    "sort_order" INTEGER NOT NULL,
    "description" TEXT,
    "manufacturer" TEXT,
    "model_name" TEXT,
    "serial_number" TEXT,
    "calibration_due_date" TEXT,
    "software_version" TEXT,
    "remarks" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS "idx_test_equipment_list_items_list_order" ON "test_equipment_list_items" ("list_id", "sort_order");
CREATE INDEX IF NOT EXISTS "idx_test_equipment_list_items_list_type" ON "test_equipment_list_items" ("list_id", "item_type");

COMMIT;
