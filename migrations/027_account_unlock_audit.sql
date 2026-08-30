-- 027_account_unlock_audit.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (관리자 계정 잠금 해제 축).
--
-- 001_initial_central_db.sql is applied only on FIRST boot
-- (docker-entrypoint-initdb.d), so a central DB created before this change keeps
-- the OLD CHECK constraint and would reject every ``account.unlocked`` audit row.
-- Source of truth is docs/platform/central_db_schema.v1.json — 001 is regenerated
-- from it by scripts/export_platform_central_db_ddl.py, so 027 is a no-op on a
-- fresh DB.
--
-- ⚠️ **이것이 왜 조용하지 않은 실패인가.** 감사 INSERT 는 잠금 해제 UPDATE 와 **같은
-- 트랜잭션**에 있다(audit_events 의 계약). 그러므로 CHECK 가 옛 목록이면 감사 INSERT 가
-- 터지고 **해제 자체가 롤백된다** — 즉 증상은 "감사 로그가 없다" 가 아니라 "잠금 해제
-- operation 이 배포된 DB 에서 영구히 실패한다" 이다. 그 원자성이 설계이므로(감사 없는
-- 변경 금지) 이 마이그레이션은 선택이 아니다.
--
-- ⚠️ **DROP 후 ADD 다 — PostgreSQL 은 CHECK 를 제자리에서 넓힐 수 없다.** 두 문장이
-- 한 트랜잭션 안에 있으므로 창은 열리지 않는다. 그리고 방향이 **넓히는 쪽만**이라
-- 기존 행은 전부 새 목록을 만족한다(검증 스캔이 실패할 수 없다).
--
-- ⚠️ **`NOT VALID` 을 쓰지 않는다.** 그것은 기존 행 검증을 건너뛰는 최적화인데, 여기서는
-- 넓히는 변경이라 검증이 실패할 수 없고, `NOT VALID` 로 남기면 그 제약이 *부분적으로만*
-- 집행된다는 사실이 스키마에 영구히 남는다.
--
-- 멱등: 제약을 이름으로 DROP IF EXISTS 한 뒤 다시 만든다. 재실행 안전.
--
-- Domain: 훔친 액세스 토큰 하나로 비밀번호 변경 문에서 5회 틀리면 계정이 잠기고, 그
-- 상태를 푸는 HTTP operation 이 **0개**였다(ADR-0021 D-8 은 카운터 제거를 금지한다 —
-- 그것은 무제한 추측 = 계정 탈취다). 운영자 판정(2026-08-22): **관리자 해제 + 자동 만료
-- 둘 다**. 자동 만료는 이미 동작하므로 남는 것은 관리자 해제 하나다.
--
-- 설계 근거: .claude/exec-plans/active/2026-08-23-identity-envelope-observation-and-unlock.md §4

BEGIN;

ALTER TABLE "audit_events"
    DROP CONSTRAINT IF EXISTS "ck_audit_events_event_type";

ALTER TABLE "audit_events"
    ADD CONSTRAINT "ck_audit_events_event_type"
    CHECK ("event_type" IN (
        'claim.acquired',
        'claim.released',
        'claim.expired',
        'membership.assigned',
        'membership.revoked',
        'account.unlocked'
    ));

COMMIT;
