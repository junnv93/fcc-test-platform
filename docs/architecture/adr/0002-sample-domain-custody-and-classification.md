# ADR-0002 — 시료 도메인: 반입/반출을 1급 사건으로, 분류를 스키마로

Status: Accepted (2026-09-04)
Date: 2026-09-04

> 운영자가 아래 8개 결정에 직접 답했다. 이 문서는 그 답과, 답을 묻기 전에 실측한
> 근거를 함께 남긴다. 추측으로 메운 칸은 없다.

## 문제

시료 도메인은 축이 둘이다.

* **시험 실무자 축** — `sample_intakes` 가 `samples` 에 1:N append-only 이고
  `intake_date/bl/ap/cp/csc/rf_cal/hw_rev/note/tech_group` 를 갖는다.
  요구가 그대로 스키마에 있다. **이 축은 이미 옳다.**
* **PM 축** — `intake_cert` · `received_date` · `released_date` · `note` 가 전부
  **단일 TEXT** 다. 실제 데이터는 그 한 칸에 값을 줄바꿈으로 쌓는다.

PM 축의 실제 데이터(운영자 제공, 시료 1건):

```
반입증: 20251104-1432333773 / 20251027-1724065293 / 20251017-1827080742 / 20250930-1031009813
수령한 날짜: 2025-10-28 / 2025-09-30
반출한 날짜: 2025-10-23 김용태 프로님 / 2025-10-17 김용태프로님
Note: 11/4일 재 반입 / 10/28일 재 반입 / 10/23일 NR n41/48 CEM 디버깅건으로 임시 반출 / 10/21일 재 반입
```

**엑셀 셀을 그대로 옮겨놓은 형태**다. 그래서 이 시료가 언제 몇 번 나갔다 들어왔는지
질의할 수 없고, 반출과 반입을 짝지어 현재 보유 상태를 계산할 수 없고, 정렬·필터·감사가
안 되고, 반출 사유가 자유 텍스트에 묻힌다.

⚠️ **한 칸에 쌓인 값끼리 개수가 맞지 않는다.** 위 데이터에서 반입증은 4개, 수령 날짜는
2개, 반출 날짜는 2개인데 Note 가 가리키는 반입은 3회다. 줄 순서로 짝지으면 **틀린 짝이
생긴다.** 이것이 아래 결정 9(기존 데이터)의 근거다.

## 실측 (2026-09-04)

묻기 전에 코드에서 확인한 사실. 결정의 근거이며, 브리프의 서술 중 둘을 정정한다.

| 실측 | 근거 |
|---|---|
| `test_category` 는 이미 Conducted/Radiated 자리다 | `sample_inventory_exporter._write_rf` 가 `test_category != sheet_name` 로 거르고 `sheet_name ∈ ('Conduction','Radiation')` |
| Device/Accessory 는 저장되는 곳이 **없다** | 같은 exporter 의 PM 시트에서 `'Device'` 가 **하드코딩** |
| `serial_number` 와 `smsn` 은 서로 다른 값이다 | `PM_HEADERS` 에 `'SMSN'` 과 `'S/N or\nIMEI'` 가 **별도 컬럼**, export 가 각각에 매핑 |
| `Sample Description` 은 저장되지 않고 파생된다 | export 시점에 `f'{model}_{category} {sample_number}'` 로 조립 |
| 그 파생식은 **손실적**이다 | 실제 값 `SM-TEST1_Main Conduction #1_Dummy Batt` 에서 `Main` 과 `_Dummy Batt` 를 만들어낼 수 없다 |
| `sample_code` 는 `NOT NULL` 인데 쓰는 곳이 없다 | 앱 코드 전역에서 참조 0건(폼·테스트·live-proof 스크립트 제외) |
| 화면이 `sample_intakes` 의 1:N 을 **노출하지 않는다** | `SampleEditor` 는 `latest_intake` 하나만 채우고, `SampleHistory` 는 입고 이력이 아니라 `sample_inventory_revisions` 를 보여준다 |
| 웹 CRUD 권한은 **하나로 통합돼 있다** | `029` 가 `platform:sample-write` 를 넣으며 *"replaces the retired PM/RF import split"*. `platform:sample-pm-write`/`-intake-write` 는 `003` 이 만든 **엑셀 import 전용** 권한이다 |
| PM/RF 엑셀 export 가 **고장나 있다** | `TEMPLATE_DIRECTORY` 가 `tests/fixtures/sample_inventory_templates/` 인데 파일은 `tests/fixtures/sample_inventory/` 에 있다 → `FileNotFoundError` |

## 결정

### 1. `serial_number` 와 `smsn` — 둘 다 유지, 폼에 남긴다

`serial_number` = `S/N or IMEI`, `smsn` = `SMSN`. PM 엑셀 양식의 별도 두 칸이다.

⚠️ **SMSN 의 정의는 이 저장소 어디에도 없다.** 스키마·API·폼·로케일에 이름만 있고,
샘플 데이터에서는 비어 있다. 엑셀 양식을 스키마로 옮기면서 따라 들어온 칸이며 무슨
값을 적기로 한 것인지는 PM 팀만 안다. 운영자 판단으로 **현상 유지**한다.

### 2. `Sample Description` 은 저장 컬럼, `sample_code` 는 은퇴

`samples.sample_description` 을 신설한다. PM 팀이 적은 이름을 그대로 보관한다 —
파생식이 만들어낼 수 없는 `Main`·`_Dummy Batt` 같은 부분이 살아남는다.

`sample_code` 는 **등록 폼에서 내린다.** 컬럼과 기존 데이터는 보존하고(`NOT NULL` 이라
쓰기 경로가 계속 채워야 하므로 `sample_number` 를 복사한다 — 기존 동작 그대로),
사람이 입력하는 칸에서만 사라진다.

`sample_description` 이 비어 있으면 export 는 **기존 파생식으로 되돌아간다.**
그래야 이 변경이 기존 데이터의 엑셀 출력을 깨지 않는다.

### 3. `test_category` = Conducted/Radiated 로 확정, CHECK 는 걸지 않는다

의미를 확정하되 값 제한은 애플리케이션 경계가 한다. 등록 폼을 **드롭다운**으로 바꿔
오타를 막는다.

이 저장소의 선례를 따른 것이다. `test_equipment_lists.test_item_key` 주석:
*"컬럼은 text 로 두고 CHECK 를 걸지 않는다 — provider 확장(mmWave/UWB)이 곧 다른
성적서라 CHECK 로 굳히면 확장마다 중앙 마이그레이션이 필요하다; 검증은 애플리케이션
경계가 한다."*

### 4. Conducted/Radiated 는 **시료의 속성**이다

한 물리 시료는 Conducted 전용이거나 Radiated 전용이다. 실측과 일치하므로
`samples.test_category` 를 그대로 둔다. RF 엑셀 export 의 시트 분리 기준도 그대로다.

### 5. 발신자/수신자의 「이름 / 연락처」는 **분리하지 않는다**

`'담당자1 /010-0000-0001'` 처럼 한 칸에 적힌 대로 보관한다. 연락처로 정렬·검색할 일이
없으므로 분리해도 입력 칸만 늘고 얻는 것이 없다.

### 6. 반출 기록의 「날짜 + 사람」은 **분리한다**

`'2025-10-17 김용태프로님'` 을 `occurred_on`(날짜)과 `counterparty`(상대방)로 나눈다.
「이 시료가 언제 나갔나」와 「누구에게 나갔나」를 각각 질의·정렬할 수 있게 된다.

### 7. 반입증은 **반입 사건의 속성**이다 (시료의 속성이 아니다)

운영자 원문:

> 반입증이란 고객사에서 우리 시험소로 샘플이 전달될 때 고객사 측에서 우리에게 전달해
> 주는 문서이고, 한번 반입 샘플 댓수가 12대 이런 식으로 들어온다.

즉 반입증은 **한 번의 납품(배치)에 하나**이고 **여러 시료가 같은 번호를 공유**한다.
한 시료에 반입증이 4개 찍힌 것은 그 시료가 4번 들어왔고 매번 다른 납품 문서에 실려
왔다는 뜻이다 — 개수 불일치가 이것으로 설명된다.

⭐ **그러므로 별도의 반입증 테이블은 만들지 않는다.** custody 사건이
`intake_cert_number` 를 갖고, 배치는 `(project_id, intake_cert_number)` 로 묶으면
자연히 복원된다. 정규화를 한 단계 더 하는 것은 조인만 늘리고 얻는 것이 없다.

### 8. Accessory 는 Conducted/Radiated 를 갖지 않는다

`sample_kind = 'Accessory'` 이면 등록 폼이 `test_category` 칸을 숨긴다. CHECK 는
걸지 않으므로(결정 3) 기존 데이터는 영향받지 않는다.

### 9. 기존 TEXT 데이터는 **자동 변환하지 않는다**

한 칸에 쌓인 값끼리 개수가 맞지 않으므로(위 「문제」 참조) 줄 순서로 짝지으면 틀린
반입/반출 짝이 만들어진다. **틀린 사건을 만드는 것보다 변환하지 않는 것이 낫다.**

기존 `intake_cert`·`received_date`·`released_date`·`note` 컬럼은 **그대로 둔다**
— 한 줄도 잃지 않는다. 화면은 둘 다 보여준다: 위에 새 custody 사건 목록, 아래에
기존 원문 칸(읽기 전용 아카이브). 사람이 보고 옮길 수 있고, 옮기지 않아도 잃지 않는다.

## 스키마

```sql
ALTER TABLE "samples"
  ADD COLUMN "sample_kind" TEXT,          -- 'Device' | 'Accessory'  (결정 8, CHECK 없음)
  ADD COLUMN "sample_description" TEXT;   -- PM 이 적는 구분 이름     (결정 2)

CREATE TABLE "sample_custody_events" (    -- 결정 6·7, PM 축 1급 사건
  "id" UUID PRIMARY KEY,
  "sample_id" UUID NOT NULL REFERENCES "samples"("id"),
  "project_id" UUID NOT NULL REFERENCES "projects"("id"),
  "event_type" TEXT NOT NULL,             -- 'received' | 'released'
  "occurred_on" TEXT,                     -- 날짜만 (결정 6)
  "counterparty" TEXT,                    -- 상대방 (결정 6)
  "intake_cert_number" TEXT,              -- 반입증 = 납품 배치 문서 (결정 7)
  "reason" TEXT,                          -- 반출 사유
  "note" TEXT,
  "actor_subject" TEXT NOT NULL,
  "created_at" TIMESTAMPTZ NOT NULL,
  "updated_at" TIMESTAMPTZ NOT NULL
);
```

`event_type` 에는 CHECK 를 건다 — 결정 3의 예외다. `test_category` 와 달리 이 값은
**보유 상태 계산의 입력**이고(가장 최근 사건이 `received` 면 보유 중), 제3의 값이
생기면 그 계산이 조용히 틀린다. 확장 가능성이 있는 어휘가 아니라 **닫힌 이분법**이다.

### 왜 `sample_intakes` 와 대칭인가

시험 실무자 축이 이미 `samples` 에 1:N append-only 다. PM 축을 같은 모양으로 세우면
두 축이 같은 규칙을 따르고, 화면·API·감사가 한 벌의 관례로 처리된다. 절반은 이미
있었고 이 ADR 은 나머지 절반을 맞춘다.

### 정정은 삭제 + 재입력이다 (수정이 아니다)

custody 사건은 **추가 · 조회 · 삭제**만 할 수 있다. PATCH 는 두지 않는다.

이 표는 사람이 손으로 적는 물리적 사건의 원장이고, 사람은 날짜를 오타 낸다. 그래서
정정 수단이 반드시 필요하다. 둘 중 삭제를 고른 이유:

* 수정은 흔적 없이 과거를 바꿀 수 있다 — '10/23 반출'이 어느 날 '10/24 반출'이 되고
  아무도 모른다.
* 삭제는 **보인다**. 줄이 사라지고, 다시 적으면 새 `created_at` 과 `actor_subject` 가
  붙는다. 잘못 적은 줄을 지우고 다시 적는 것은 PM 이 종이 대장에서 하던 일 그대로다.

행 자체가 `actor_subject` · `created_at` 을 갖는다. `sample_intakes` 는 행위자를 아예
갖지 않으므로, custody 축은 그보다 더 많이 기억한다.

⚠️ **`sample_inventory_revisions` 에는 쓰지 않는다.** 그 원장은 `samples` 현재 투영의
전체 스냅샷이고, 그 스냅샷 모양(`fcc.sample.inventory.snapshot.v1`)은 측정 세션이
함께 쓴다. custody 사건을 그 안에 넣으면 측정 스냅샷 계약이 이 축의 변경마다 흔들린다.
custody 는 자기 행에 행위자와 시각을 갖는 것으로 충분하다.

## 결과

* 「이 시료가 지금 우리에게 있나」가 **계산 가능**해진다 — 가장 최근 사건의 `event_type`.
* 「이 반입증으로 들어온 12대」가 **질의 가능**해진다 — `intake_cert_number` 로 묶기.
* 반출 사유가 자유 텍스트에서 나와 **자기 칸**을 갖는다.
* 기존 데이터는 **한 줄도 잃지 않는다** — 원문 칸이 그대로 남는다.
* 시험 실무자 축의 1:N 이 **화면에 처음으로 보인다**(지금은 최신 1건만 보인다).

## 범위 밖

* 진행률(progress) 도메인 — 테스트플랜 발행·수행이 검증된 뒤 초기 목표부터 다시
  잡기로 운영자가 판단해 별도 대기 중이다.
* 배치 등록 화면 — 반입증 하나로 12대를 한 번에 등록하는 흐름은 결정 7 이 자연히
  가리키는 다음 단계지만, 이번 범위가 아니다. 스키마는 그것을 막지 않는다.
