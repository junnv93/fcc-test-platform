-- 026_local_identity.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (신원 축 EMS 정합).
--
-- 001_initial_central_db.sql is applied only on FIRST boot
-- (docker-entrypoint-initdb.d), so a central DB created before this change will
-- NOT pick these columns up by re-running 001. This migration adds them
-- additively and idempotently (safe to re-run).
--
-- Source of truth is docs/platform/central_db_schema.v1.json — 001 is regenerated
-- from it by scripts/export_platform_central_db_ddl.py. For a fresh DB, 001
-- already contains every column and the index; 026 is a no-op there.
--
-- 설계 근거: .claude/exec-plans/active/2026-08-21-ems-parity-local-identity.md §2, §4
--            docs/adr/0021-local-identity-and-ems-schema-parity.md
--
-- ─────────────────────────────────────────────────────────────────────────────
-- 왜 이 축이 필요한가
--
-- FCC `users` 는 **OIDC 토큰의 투영**이었다 — 컬럼이 issuer·subject·display_name·
-- email·enabled 뿐이라 *토큰 없이 존재하는 사용자*를 표현할 칸이 없다. 그래서 로그인이
-- 브라우저 PKCE 를 요구하고, `crypto.subtle` 은 보안 컨텍스트(https 또는 localhost)에서만
-- 정의되므로 평문 HTTP 로 노출된 중앙 PC 에서는 로그인이 **원리적으로 불가능**했다.
-- 설정으로 풀리는 문제가 아니다.
--
-- EMS(equipment_management_system)는 같은 사내 인원을 쓰고 둘 다 Azure SSO 가 종착이며
-- 승인 전까지 내부 사용자관리라는 같은 처지다. 그쪽 스키마로 정렬하면 나중에 EMS
-- users 를 옮길 때 키 번역이 필요 없다(운영자 판정 2026-08-21).
--
-- ⚠️ 왜 지금인가: 운영자가 2026-08-21 에 `docker compose down -v` 로 중앙 DB 를 비웠다.
-- **이관할 사용자 데이터가 0건이다.** 실운영 후 같은 변경은 데이터 이관이 붙는다.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- 왜 한 마이그레이션인가 (P1 컬럼 + P3 잠금 컬럼을 나누지 않는다)
--
-- EMS 는 이것을 둘로 나눴다(0062 password_hash, 0065 auth lifecycle). 여기서는 나누지
-- 않는다 — **로그인은 되는데 잠금은 없는 중간 상태**가 정확히 계획서가 금지하는 배포
-- 형상(§3: "P2 와 P3 사이에 배포하지 않는다")이기 때문이다. 한 마이그레이션이면 그
-- 중간 상태가 스키마 수준에서 존재할 수 없다.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- 왜 `role` 컬럼이 없는가 — 이것은 정합의 **의도된 예외**다
--
-- EMS `users.role` 은 여기 오지 않는다. FCC 는 권한을 `rbac_role_grants` 별도 축에
-- 이미 갖고 있고, EMS 의 role 을 복제하면 **권한 SSOT 가 둘이 된다**. 둘이 되면 어느
-- 쪽이 참인지 코드마다 답이 갈리고, 그 불일치는 권한 상승으로 나타난다. EMS 사용자를
-- 이관할 때 그 값은 grants 로 **번역**한다. 같은 사유로 `is_system_admin` 도 없다.
--
-- 나머지 미복제 컬럼과 각각의 사유는 tests/test_ems_schema_parity.py 의
-- ABSENT 가 소유하고, EMS 가 컬럼을 더하면 그 테스트가 **이름을 대며
-- red** 가 된다(파티션이 EMS 집합을 더 이상 덮지 못하므로).

BEGIN;

-- ── 신원·조직 칸 (EMS 정합) ──────────────────────────────────────────────────
--
-- ⚠️ 타입이 EMS 의 varchar(N) 이 아니라 TEXT 인 것은 취향이 아니다. 이 저장소의 DDL
-- 은 central_db_schema.v1.json 에서 생성되고 그 TYPE_MAP 에 varchar 가 없다. TEXT 와
-- varchar(N) 은 PostgreSQL 에서 저장 형태가 같고 varchar 는 길이 검사만 더한다 — 그
-- 검사는 여기서 application/common/identity.py::EMAIL_MAX_LENGTH 가 소유한다.
-- (타입 어휘를 넓히는 것은 이 표 하나를 위해 공유 exporter 를 바꾸는 일이라 기각.)

-- 로그인 자격증명. EMS 와 **같은 컬럼명**이어야 EMS users 이관이 매핑 표를 요구하지
-- 않는다(EMS 0062_add_user_password_hash.sql).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "password_hash" TEXT;

-- Azure 승인 전에는 비어 있는 대기 칸. "Azure 가 종착이지만 지금은 내부 관리"라는
-- 운영자 판정이 스키마에 새겨지는 자리다 — EMS 가 정확히 그 형상을 갖는다.
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "azure_ad_id" TEXT;

ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "employee_id" TEXT;
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "department" TEXT;
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "position" TEXT;
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "site" TEXT;
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "phone_number" TEXT;
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "last_login" TIMESTAMPTZ;

-- ── 인증 수명주기 칸 (P3 하드닝, EMS 0065 정합) ──────────────────────────────
--
-- ⚠️ EMS 는 이 셋을 `NOT NULL DEFAULT 0/false` 로 선언하고 FCC 는 **nullable** 이다.
-- 회피가 아니라 exporter 의 규칙이다 — `added_in` 컬럼은 required=false 여야 한다
-- (기존 행에 값이 없는데 NOT NULL 을 붙이면 ADD COLUMN 이 깨진다). 그 결과 기존
-- OIDC 행의 이 칸들은 NULL 이고, **읽는 쪽이 그 사실을 흡수한다**:
-- login_throttle_policy.fold_failed_attempts 가 비-정수를 0 으로 접고, 잠금 UPDATE 는
-- COALESCE 로 센다. 저장소 불변식을 가정하는 오라클은 저장소가 그것을 어긴 날 함께
-- 눈을 감는다.
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "failed_login_attempts" INTEGER;
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "locked_until" TIMESTAMPTZ;
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "last_failed_at" TIMESTAMPTZ;
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "password_changed_at" TIMESTAMPTZ;
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "force_password_change" BOOLEAN;
-- 토큰 무효화 카운터. 비밀번호가 바뀌면 증가하고, 그보다 낮은 세대의 토큰은 서명이
-- 유효해도 거부된다 — 그것이 "로그아웃/비번변경이 이미 발급된 토큰에 의미를 갖는"
-- 유일한 방법이다(JWT 는 발급 후 취소할 수 없다).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "session_version" INTEGER;

-- ── 로컬 신원의 이메일 유일성 ────────────────────────────────────────────────
--
-- ⚠️⚠️ **부분 조건 셋 전부가 하중을 받는다. 하나라도 빼면 결함이다.**
--
-- (1) `issuer = 'urn:fcc:identity:local'` — 이메일은 **로컬 사용자에게만** 신원 키다.
--     OIDC 사용자의 키는 (issuer, subject) 이고 email 은 클레임 투영일 뿐이라, 같은
--     사람이 issuer 마다 한 행씩 갖는 것이 **정상**이다 — 그것이 바로 이 웨이브가
--     보존하는 Keycloak→Azure 이행 경로다. 이 조건이 없으면 첫 OIDC 로그인은 성공하고
--     **두 번째 issuer 의 로그인이 unique violation → UserWriteError → 503** 이 된다.
--     즉 "기존 OIDC 경로 무변경"(계약 M-2)을 조용히 깨뜨린다. 계획서 §4 는 이 조건을
--     적지 않았고, 그 형태 그대로 넣었다면 실제로 깨졌다.
--
-- (2)(3) `email IS NOT NULL AND email <> ''` — users.email 은 nullable 이고 이메일
--     클레임을 안 싣는 토큰은 그것을 비운다. 이 조건이 없으면 **이메일 없는 기존 행
--     두 개가 서로 충돌**한다.
--
-- EMS 가 조건 없이 걸 수 있는 것(0075_normalize_email_lowercase_unique.sql)은 EMS
-- email 이 NOT NULL 이고 issuer 가 하나뿐이기 때문이다. 같은 SQL 을 복사하면 안 되는
-- 이유가 바로 그 두 전제의 부재다.
--
-- ⚠️ 로컬 행은 subject 가 **정규화된 이메일**이라 (issuer, subject) 유일 인덱스가 이미
-- 이메일 유일성을 준다. 이 인덱스는 그 위의 defense-in-depth 로, *"로컬 행의 email 이
-- 자기 subject 와 어긋나게 쓰였다"* 는 writer 결함 계열을 잡는다.
--
-- ⚠️ EMS 0075 와 달리 **기존 email 을 소문자로 UPDATE 하지 않는다.** 그 값들은 OIDC
-- 토큰이 실어 온 것이고 우리가 소유하지 않는다. 인덱스가 LOWER(email) 위에 있으므로
-- 저장값이 소문자일 필요도 없다. 로컬 행은 쓰는 시점에 이미 정규화되어 들어온다.
--
-- ⚠️ 이 CREATE 가 실패하면 그것은 **정당한 실패**다 — 같은 이메일을 가진 로컬 행이
-- 이미 둘이라는 뜻이고, 조용히 건너뛰면 그 중 어느 행으로 로그인되는지 정의되지 않는
-- 상태가 영구화된다.
--
-- ⚠️ 'urn:fcc:identity:local' 리터럴은 SQL 이 상수를 import 할 수 없어 여기 적힌다.
-- application.common.identity.LOCAL_IDENTITY_ISSUER 와의 결박은
-- tests/test_local_identity_policy.py 의 파생 단언이 소유한다(상수를 읽어 이 절에
-- 나타나는지 확인 — 둘 중 하나가 움직이면 red).
CREATE UNIQUE INDEX IF NOT EXISTS "ux_users_email_lower"
    ON "users" (LOWER("email"))
    WHERE "issuer" = 'urn:fcc:identity:local'
      AND "email" IS NOT NULL
      AND "email" <> '';

COMMIT;
