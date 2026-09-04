-- 시료 도메인 재설계 — PM 축 반입/반출을 1급 사건으로 (ADR-0002, 2026-09-04).
--
-- 이 마이그레이션은 additive 이고 idempotent 하다. 기존 컬럼을 하나도 지우지 않고
-- 기존 값을 하나도 바꾸지 않는다.
--
-- ⚠️ 기존 intake_cert / received_date / released_date / note 는 그대로 둔다.
-- 실제 데이터는 그 한 칸에 값을 줄바꿈으로 쌓아 왔는데, 반입증 4개 · 수령 2개 ·
-- 반출 2개 · Note 3회처럼 **개수가 서로 맞지 않는다**. 줄 순서로 짝지으면 틀린
-- 반입/반출 짝이 만들어진다. 운영자 판단(ADR-0002 결정 9): 자동 변환하지 않고
-- 원문을 보존한다. 화면이 새 사건 목록과 기존 원문을 함께 보여주고, 사람이 보고
-- 옮긴다. 옮기지 않아도 한 줄도 잃지 않는다.
BEGIN;

-- ── 시료 분류 (ADR-0002 결정 2·8) ────────────────────────────────────────────
-- sample_kind: 'Device' | 'Accessory'. 지금까지 저장되는 곳이 아예 없었다 —
-- PM 엑셀 export 가 'Device' 를 하드코딩하고 있었다.
-- sample_description: PM 이 적는 구분 이름. 지금까지는 export 시점에
-- '{model}_{test_category} {sample_number}' 로 조립했는데, 실제 값
-- 'SM-TEST1_Main Conduction #1_Dummy Batt' 의 'Main' 과 '_Dummy Batt' 를 그 식으로는
-- 만들어낼 수 없다. 손실적 파생을 저장으로 바꾼다.
--
-- 둘 다 CHECK 를 걸지 않는다 (결정 3): 값 제한은 애플리케이션 경계(커널의
-- SAMPLE_KINDS / TEST_CATEGORIES)가 하고 폼은 드롭다운이다. test_equipment_lists.
-- test_item_key 가 같은 판단을 먼저 내렸다.
ALTER TABLE "samples"
    ADD COLUMN IF NOT EXISTS "sample_kind" TEXT,
    ADD COLUMN IF NOT EXISTS "sample_description" TEXT;

-- ── PM 축 custody 사건 (ADR-0002 결정 6·7) ───────────────────────────────────
-- 시험 실무자 축의 sample_intakes 와 대칭이다: 같은 시료에 1:N 으로 쌓이고,
-- 화면·API·감사가 한 벌의 관례로 처리한다. 절반은 이미 있었고 이것이 나머지 절반이다.
--
-- intake_cert_number 는 시료가 아니라 **이 사건**의 속성이다. 반입증은 고객사가 한 번의
-- 납품에 한 장 발행하고 그 납품에 실린 시료 여럿(12대 단위)이 같은 번호를 공유한다
-- (운영자 확인). 배치는 (project_id, intake_cert_number) 로 묶으면 복원되므로 별도의
-- 반입증 테이블을 두지 않는다 — 정규화를 한 단계 더 해도 조인만 늘고 얻는 것이 없다.
--
-- occurred_on / counterparty 가 나뉘어 있는 것은 실제 데이터가 '2025-10-17 김용태프로님'
-- 처럼 한 칸에 붙어 있었기 때문이다. 나눠야 '언제 나갔나'와 '누구에게 나갔나'를 각각
-- 질의·정렬할 수 있다.
--
-- occurred_on 이 TEXT 인 것은 의도다. 이 도메인의 날짜는 전부 사람이 적는 자유 텍스트이고
-- (samples.received_date, sample_intakes.intake_date, test_reports.date_tested_start 가
-- 모두 그렇다) '2025/09/30' 과 '10/21' 이 섞여 들어온다. DATE 로 굳히면 적힌 대로 적을 수
-- 없게 되고, 파싱 실패를 마이그레이션 실패로 바꾼다.
CREATE TABLE IF NOT EXISTS "sample_custody_events" (
    "id" UUID PRIMARY KEY,
    "sample_id" UUID NOT NULL REFERENCES "samples"("id"),
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "event_type" TEXT NOT NULL,
    "occurred_on" TEXT,
    "counterparty" TEXT,
    "intake_cert_number" TEXT,
    "reason" TEXT,
    "note" TEXT,
    "actor_subject" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

-- ⭐ 여기에는 CHECK 를 건다 — test_category 와 달리 확장 가능한 어휘가 아니라 닫힌
-- 이분법이고, **보유 상태 계산의 입력**이다(가장 최근 사건이 'received' 면 보유 중).
-- 제3의 값이 들어오면 그 계산이 오류 없이 조용히 틀린다.
ALTER TABLE "sample_custody_events" DROP CONSTRAINT IF EXISTS "ck_sample_custody_events_event_type";
ALTER TABLE "sample_custody_events"
    ADD CONSTRAINT "ck_sample_custody_events_event_type"
    CHECK ("event_type" IN ('received', 'released'));

-- 시료 상세: 한 시료의 사건을 기록 순서로 읽는다. 목록 화면의 '가장 최근 사건 1건'
-- (= 보유 상태)도 이 인덱스가 받는다.
CREATE INDEX IF NOT EXISTS "idx_sample_custody_events_sample_created"
    ON "sample_custody_events" ("sample_id", "created_at" DESC, "id" DESC);

-- 배치 질의: '이 반입증으로 들어온 12대는 무엇인가'. 반입증이 없는 사건(반출 등)은
-- 이 축의 질의 대상이 아니므로 부분 인덱스로 제외한다.
CREATE INDEX IF NOT EXISTS "idx_sample_custody_events_project_cert"
    ON "sample_custody_events" ("project_id", "intake_cert_number")
    WHERE "intake_cert_number" IS NOT NULL;

COMMIT;
