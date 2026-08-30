-- 025_chamber_web_session_approval.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (챔버 모드 축 — 승인 절반).
--
-- 001_initial_central_db.sql is applied only on FIRST boot
-- (docker-entrypoint-initdb.d), so a central DB created before this change will
-- NOT pick the column up by re-running 001. This migration adds it additively
-- and idempotently (safe to re-run).
--
-- Source of truth is docs/platform/central_db_schema.v1.json — 001 is regenerated
-- from it by scripts/export_platform_central_db_ddl.py. For a fresh DB, 001
-- already contains the column and the widened view; 025 is a no-op there.
--
-- Purely additive: one nullable column + CREATE OR REPLACE of an existing view.
-- No default, no backfill, no index change.
--
-- Domain (운영자 판정 2026-08-16): 챔버 PC 는 웹 세션을 받는 PC 이거나 받지 않는 PC 이며
-- 한 PC 가 둘 다일 수 없다(포트 승인이 PC 마다 따로 난다). 그 사실에는 축이 **둘** 있고
-- 권위가 다르다 — **승인**("이 챔버는 웹이 허용됐다", 중앙이 소유하는 회사 정책의 사실)과
-- **실현**("나는 실제로 리스너를 열었다", 노드가 heartbeat 로 만드는 관측). 이 컬럼은
-- **승인 절반**이고, 실현은 이미 있는 heartbeat 원장에서 파생한다.
--
-- ⚠️ **한 칸으로 접지 않는다.** 둘의 **불일치가 곧 신호**다 — 승인됐는데 리스너가 없으면
-- 배포 미완이고, 승인 안 났는데 리스너가 떠 있으면 회사 정책 위반이다. 접으면 그 두 사실을
-- 구분할 수 없다.
--
-- ⚠️ **NULLABLE 이고 3-상태다.** NULL = 아무도 판정하지 않았다 / true = 승인 / false =
-- 명시적 미승인. **backfill 하지 않는다** — *"결정했고 아니다"* 와 *"아무도 안 봤다"* 는
-- 운영자가 할 일이 다르고, false 로 채우면 그 구분이 소급해서 사라진다.
--
-- ⚠️ **`enabled` 를 재사용하지 않는다.** 그 컬럼은 이미 운영상 enable/disable 의미를 갖고,
-- 배포 정책을 거기 섞으면 둘 다 읽을 수 없게 된다.
--
-- ⚠️ **자가 등록은 이 칸을 쓰지 못한다** — 노드는 자기가 승인됐는지 **모른다**. 020
-- (equipment_config_json) 과 같은 형상으로 등록 컬럼 목록에 **아예 없다**(방어가 아니라
-- 부재). 018(artifact_storage_root)의 COALESCE fill-only 방어는 그 값이 이미 등록 요청에
-- 실려 있어 소급 방어가 필요했던 경우이고, 여기서는 애초에 실으면 안 된다. 화면의 관리자
-- 패널이 **등록 재-POST 로** 챔버를 편집하므로 그 부재가 곧 보호다.
--
-- ⚠️ 이 축은 **관측이지 게이트가 아니다.** 이 컬럼 때문에 거부되는 측정·세션·유입·등록은
-- 없다. 정책 위반조차 막지 않는다 — 시범 단계에 과하다(운영자 판정 2026-08-16).
--
-- 설계 근거: .claude/exec-plans/active/2026-08-16-per-pc-mode-and-runtime-exclusion.md §4

BEGIN;

ALTER TABLE "chamber_nodes"
    ADD COLUMN IF NOT EXISTS "accepts_web_sessions" BOOLEAN;

-- 대조를 보려면 가용성 뷰가 승인 칸을 함께 내야 한다. 뷰는 `chamber_nodes.*` 가 아니라
-- **명시 컬럼 목록**이므로 컬럼 추가만으로는 절대 보이지 않는다.
--
-- ⚠️ ALTER 가 **먼저**다 — 뷰가 아직 없는 컬럼을 참조하면 이 트랜잭션이 실패한다.
-- 001 의 exporter 도 같은 이유로 additive upgrade 를 뷰보다 앞에 렌더한다.
--
-- ⚠️⚠️ **새 컬럼은 SELECT 목록의 맨 끝이다 — 취향이 아니라 PostgreSQL 의 제약이다.**
-- `CREATE OR REPLACE VIEW` 는 기존 뷰의 컬럼을 **중간에 삽입하거나 재배열할 수 없다**.
-- 실측(PostgreSQL 16.14, 이 저장소의 중앙 컨테이너):
--
--     CREATE VIEW probe_v AS SELECT a, c FROM probe_t;
--     CREATE OR REPLACE VIEW probe_v AS SELECT a, b, c FROM probe_t;
--     -- ERROR:  cannot change name of view column "c" to "b"
--     CREATE OR REPLACE VIEW probe_v AS SELECT a, c, b FROM probe_t;
--     -- CREATE VIEW   ← 끝에 붙이는 것은 된다
--
-- 그래서 `accepts_web_sessions` 가 자기 테이블 형제(heartbeat_ttl_seconds) 옆이 아니라
-- 맨 끝에 있다. 보기에는 어색하지만, 형제 옆에 두면 이 마이그레이션이 **이미 배포된
-- 모든 중앙 DB에서 실패**한다. codex 교차 검증(2026-08-16)이 이것을 P1 으로 잡았고
-- 위 실측이 그 지적을 확인했다.
CREATE OR REPLACE VIEW "chamber_availability" AS
    SELECT n.chamber_id, n.name, n.base_url, n.enabled, n.heartbeat_ttl_seconds, latest.reported_status, latest.last_heartbeat_at, latest.heartbeat_expires_at, latest.session_id, latest.progress_json, latest.last_error_json, n.accepts_web_sessions FROM chamber_nodes n LEFT JOIN (SELECT ranked.chamber_id, ranked.reported_status, ranked.occurred_at AS last_heartbeat_at, ranked.expires_at AS heartbeat_expires_at, ranked.session_id, ranked.progress_json, ranked.last_error_json FROM (SELECT h.chamber_id, h.reported_status, h.occurred_at, h.expires_at, h.session_id, h.progress_json, h.last_error_json, ROW_NUMBER() OVER (PARTITION BY h.chamber_id ORDER BY h.occurred_at DESC) AS rn FROM chamber_heartbeat_events h) ranked WHERE ranked.rn = 1) latest ON latest.chamber_id = n.chamber_id;

COMMIT;
