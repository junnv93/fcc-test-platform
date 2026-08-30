-- 024_test_session_provenance.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (세션 출처 관측 축).
--
-- 001_initial_central_db.sql is applied only on FIRST boot
-- (docker-entrypoint-initdb.d), so a central DB created before this change will
-- NOT pick the columns up by re-running 001. This migration adds them additively
-- and idempotently (safe to re-run).
--
-- Source of truth is docs/platform/central_db_schema.v1.json — 001 is regenerated
-- from it by scripts/export_platform_central_db_ddl.py. For a fresh DB, 001
-- already contains both columns AND the CHECK; 024 is a no-op there.
--
-- Purely additive: two nullable columns, no default, no backfill, no index change.
-- Every existing row keeps behaving exactly as before (NULL ⇒ 미선언).
--
-- Domain (운영자 판정 2026-08-16): 챔버 PC 는 웹 세션을 받는 PC 이거나 받지 않는 PC 이며
-- 한 PC 가 둘 다일 수 없다(포트 승인이 PC 마다 따로 나기 때문). 그 위에 "프로젝트는
-- 시작한 모드로 끝난다" 가 **운영 규칙**으로 놓였는데, 규칙이 지켜졌는지 볼 수단이
-- 하나도 없었다 — 이 테이블에 경로/모드 칸이 0개였다.
--
--   * session_origin   — 이 측정이 **웹 세션으로 왔나**. 합성 루트가 **선언**하고
--                        클라이언트는 보낼 수 없다(요청 스키마에 없다). 어휘는 provider
--                        중립이다: 웹 세션을 받지 않은 PC 가 무엇으로 측정했는지는
--                        provider 의 일이고 중앙은 알지 않는다 — 알면 provider 가 늘
--                        때마다 중앙 마이그레이션이 필요해진다.
--   * workbook_handle  — 그 세션이 쓴 업로드 워크북의 불투명 핸들 verbatim. 핸들이
--                        **내용 지문 파생**이라 이 한 칸이 "두 챔버가 같은 계획을
--                        썼는가" 를 조회 한 번으로 답하게 만든다.
--
-- ⚠️ NULL 은 "모름"이지 'LOCAL_PROGRAM' 이 아니다 — backfill 을 하지 않는 이유가 그것
-- 이다. 기본값으로 토큰을 채우면 선언 이전에 유입된 웹 세션이 "로컬"이라고 거짓말하고,
-- 그 거짓말은 조사 시점에 진실과 구분되지 않는다.
--
-- ⚠️ 이 축은 **관측이지 게이트가 아니다.** 두 칸 때문에 거부되는 측정·세션·유입은 없다.
-- CHECK 는 어휘를 잠그기 위한 것이고(도메인 enum × 스키마 allowed_values × 이 CHECK 의
-- 3자 parity), 값은 언제나 우리 코드의 enum 에서 나오므로 클라이언트가 그것을 유발할 수
-- 없다. 알 수 없는 토큰을 실은 노드는 유입 전체를 실패시키지 않고 그 칸만 비운다
-- (application/headless/outbox_session_provenance_enrichment.py).
--
-- 설계 근거: .claude/exec-plans/active/2026-08-16-per-pc-mode-and-runtime-exclusion.md

BEGIN;

ALTER TABLE "test_sessions"
    ADD COLUMN IF NOT EXISTS "session_origin" TEXT;

ALTER TABLE "test_sessions"
    ADD COLUMN IF NOT EXISTS "workbook_handle" TEXT;

-- 신선한 DB(001 재생성본)와 업그레이드된 DB 가 **같은 제약**을 갖게 한다. 제약이 한쪽에만
-- 있으면 어휘 위반이 배포에 따라 다르게 답하고, 그 차이는 그 배포에서만 드러난다.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_test_sessions_session_origin'
          AND conrelid = '"test_sessions"'::regclass
    ) THEN
        ALTER TABLE "test_sessions"
            ADD CONSTRAINT "ck_test_sessions_session_origin"
            CHECK ("session_origin" IN ('WEB_SESSION', 'LOCAL_PROGRAM'));
    END IF;
END $$;

COMMIT;
