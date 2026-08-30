-- 017_artifact_custody.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (플롯 원본 보관 현황 조회 축).
--
-- 001_initial_central_db.sql is applied only on FIRST boot
-- (docker-entrypoint-initdb.d) with CREATE TABLE IF NOT EXISTS — so a central DB
-- created before this change will NOT pick up these tables by re-running 001.
-- This migration adds them additively and idempotently (safe to re-run).
--
-- Source of truth is docs/platform/central_db_schema.v1.json — every table /
-- column / index here MUST already exist there (001 is regenerated from it by
-- scripts/export_platform_central_db_ddl.py). For a fresh DB, 001 already
-- contains everything below; 017 is a no-op there.
--
-- Purely additive (no existing table / column / index is modified or dropped).
--
-- Domain: 플롯(측정 스크린샷)은 FCC 심사 증거이고 **원본이 권위**다(회사 파일서버 /
-- 챔버 PC 로컬). 중앙은 그 보관소를 열 수 없으므로 **판정하지 않는다** — 판정은 증거가
-- 있는 노드에서 나오고 이 테이블들은 그것을 받아 보관한다. 참조 데이터 origin/replica
-- 계층의 **거울상**이다: 참조는 중앙→로컬 PULL, 보관 판정은 로컬→중앙 PUSH.
-- 설계 근거: .claude/exec-plans/active/2026-08-09-plot-custody-web-and-chamber.md

BEGIN;

-- 세션당 하나의 관측 스냅샷.
--
-- ⚠️ session_id FK 를 **두지 않는다.** 자연키가 test_sessions 와 같은
-- (provider_id, chamber_id, provider_session_id) 이므로 읽기 시 그 키로 조인하면,
-- 나중에 test_sessions.project_id 가 해소될 때(중앙이 model_name 으로 프로젝트를
-- 찾는 시점) 이 스냅샷의 프로젝트 귀속이 **백필 없이** 따라온다. FK 를 두면 스냅샷이
-- 세션 부모행보다 먼저 도착할 때 거절되거나 두 번째 정체성 파생이 필요해진다.
--
-- observed_at 은 **노드가 관측한 시각** = watermark 다. reported_at(중앙 수신 시각)과
-- 다르며, latest-wins 판정은 observed_at 으로 한다 — 재시도로 늦게 도착한 낡은 관측이
-- 새 판정을 덮으면 화면이 과거로 되돌아간다.
CREATE TABLE IF NOT EXISTS "artifact_custody_snapshots" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "chamber_id" TEXT NOT NULL,
    "provider_session_id" TEXT NOT NULL,
    "status" TEXT NOT NULL CONSTRAINT "ck_artifact_custody_snapshots_status" CHECK ("status" IN ('verified', 'missing', 'diverged', 'unknown')),
    "verified_count" INTEGER NOT NULL DEFAULT 0,
    "missing_count" INTEGER NOT NULL DEFAULT 0,
    "diverged_count" INTEGER NOT NULL DEFAULT 0,
    "unknown_count" INTEGER NOT NULL DEFAULT 0,
    "roots_json" TEXT,
    "session_label" TEXT,
    "observed_at" TIMESTAMPTZ NOT NULL,
    "reported_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 조치 가능한 항목만(비-verified). verified 행을 담지 않는 것은 숨김이 아니라 규모
-- 문제다 — 개수는 스냅샷 카운터에 남고, 담으면 세션당 약 1,000행이 중앙으로 복제된다.
CREATE TABLE IF NOT EXISTS "artifact_custody_findings" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "snapshot_id" UUID NOT NULL REFERENCES "artifact_custody_snapshots"("id"),
    "relative_path" TEXT NOT NULL,
    "status" TEXT NOT NULL CONSTRAINT "ck_artifact_custody_findings_status" CHECK ("status" IN ('missing', 'diverged', 'unknown')),
    "artifact_type" TEXT,
    "expected_sha256" TEXT,
    "observed_sha256" TEXT,
    "reason" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- latest-wins 의 백킹 인덱스이자 자연키.
CREATE UNIQUE INDEX IF NOT EXISTS "ux_artifact_custody_snapshots_session"
    ON "artifact_custody_snapshots" ("provider_id", "chamber_id", "provider_session_id");
CREATE INDEX IF NOT EXISTS "idx_artifact_custody_snapshots_status"
    ON "artifact_custody_snapshots" ("status");
CREATE INDEX IF NOT EXISTS "idx_artifact_custody_findings_snapshot"
    ON "artifact_custody_findings" ("snapshot_id");

-- 지문 축 (plot-dual-custody ③). 중앙 artifacts 자연키는 relative_path 인데 그것은
-- **가변 주소**다 — 새 캡처가 옛 것을 _history/ 로 밀어내면 주소가 바뀐다. 지문으로
-- 같은 바이트를 찾을 수 있어야 이동을 추적하고 중복을 탐지한다.
--
-- ⚠️ **unique 가 아니다.** 같은 바이트가 두 주소에 정당하게 존재할 수 있고(동일 플롯의
-- 재캡처, latest 와 _history 가 같은 내용인 순간), unique 로 두면 정당한 적재가 실패한다.
CREATE INDEX IF NOT EXISTS "idx_artifacts_provider_sha256"
    ON "artifacts" ("provider_id", "sha256");

COMMIT;
