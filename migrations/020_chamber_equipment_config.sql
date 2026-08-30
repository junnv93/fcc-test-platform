-- 020_chamber_equipment_config.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (챔버별 계측기 연결 설정).
--
-- 001_initial_central_db.sql is applied only on FIRST boot
-- (docker-entrypoint-initdb.d), so a central DB created before this change will
-- NOT pick the column up by re-running 001. This migration adds it additively
-- and idempotently (safe to re-run).
--
-- Source of truth is docs/platform/central_db_schema.v1.json — 001 is regenerated
-- from it by scripts/export_platform_central_db_ddl.py. For a fresh DB, 001
-- already contains the column; 020 is a no-op there.
--
-- Purely additive: nullable column, no default, no backfill, no index change.
-- Every existing row keeps behaving exactly as before (NULL ⇒ the node reads the
-- workbook Chamber Config sheet, which is today's behaviour).
--
-- Domain: 분석기/BT 테스터/스위치박스의 GPIB·LAN 주소는 **그 방의 속성**이다.
-- 오늘 그것을 바꾸는 유일한 방법은 GUI 로 워크북 Chamber Config 시트를 여는 것이라,
-- 웹 단독 운영에서는 재배선 뒤 분석기 IP 하나를 바꿀 수단이 없다
-- (CLAUDE.md Deployment Policy §5-(a) 의 임계 경로 2건 중 하나).
--
-- 왜 map 한 칸인가 (컬럼당 하나가 아니라):
--   ADR-0018 D-6 이 세 축을 갈랐다 — 공통 껍데기는 platform 이 1회 구현하고, 고유
--   필드 목록은 각 provider 가 자기 레포의 descriptor 로 싣는다. 'Analyzer LAN:' 같은
--   키를 컬럼으로 승격하면 그 provider 어휘가 중앙 스키마에 들어오고, provider 가 늘
--   때마다 중앙 마이그레이션이 필요해진다. platform 은 이 값을 **불투명한 map** 으로만
--   본다: 키를 아는 것은 provider descriptor 와 노드 도메인뿐이다.
--
-- 왜 default '{}'::jsonb 가 아닌가:
--   NULL 은 "아무도 설정한 적 없다"이고 '{}' 는 "비우기로 결정했다"이다. 노드 폴백
--   규칙("미설정이면 워크북")이 그 구분 위에 서 있으므로 둘을 접으면 안 된다. 018 이
--   같은 이유로 같은 선택을 했다.
--
-- 왜 자가 등록이 이 컬럼을 쓰지 못하는가:
--   노드는 매 부팅 자기를 등록하고 자기가 어느 장비와 말해야 하는지 **모른다**. 018 의
--   artifact_storage_root 는 이미 등록 요청 스키마에 실려 있었기에 COALESCE fill-only
--   로 운영자 소유권을 소급해 붙여야 했다. 이 축은 첫날부터 전용 PATCH 를 가지므로
--   등록 컬럼 목록에 **아예 넣지 않는다** — 방어가 아니라 부재다.
--
-- 설계 근거: .claude/exec-plans/active/2026-08-10-split6-equipment-config-values.md

BEGIN;

ALTER TABLE "chamber_nodes"
    ADD COLUMN IF NOT EXISTS "equipment_config_json" JSONB;

COMMIT;
