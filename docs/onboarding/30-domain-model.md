# 도메인 — 시험이 데이터로 어떻게 남는가

> **시험원은 도메인을 압니다. 시스템 이름을 모릅니다.**
> **개발자는 시스템을 압니다. 도메인을 모릅니다.**
> 이 문서는 그 둘을 잇습니다.
>
> 모든 표 이름과 컬럼 수는 **2026-09-07, `migrations/` 실측**입니다.

---

## 1. 전체 사슬 — 시험 하나가 남기는 것

```
  providers  ─────────────┐   「어느 분야인가」 (KC · Unlicensed · mmWave …)
                          │
  projects                │   「무슨 시험 건인가」  ← 의뢰 단위
     │                    │
     ├── samples          │   「어느 시료인가」     ← 실물 하나하나
     │      │             │
     │      └──────┐      │
     │             ▼      ▼
     │        test_sessions        「언제 · 무엇을 · 어떻게 측정했나」
     │             │
     │             ├── measurement_results     결과
     │             │        └── measurement_attempts    시도 (재측정 포함)
     │             ├── artifacts               산출물 (플롯 · 로그 · 원자료)
     │             └── jobs                    작업 (측정 요청 단위)
     │
     └── test_reports                          성적서


  chamber_nodes    ← 독립. 어느 프로젝트에도 묶이지 않는다 (분야 중립)
```

⚠️ **`chamber_nodes` 가 이 그림에서 «떨어져» 있는 것이 설계입니다.**
챔버는 특정 프로젝트나 분야에 속하지 않습니다 — 오늘 이 시험을 하고 내일 저 시험을 합니다.

---

## 2. 각 개념 — 시험원의 말과 시스템의 이름

| 시험원이 말하는 것 | 시스템 이름 | 컬럼 수 | 무엇을 담나 |
|---|---|---|---|
| 「어느 분야 시험」 | `providers` | 10 | 분야 정체성. **자연키** `provider_id` |
| 「이번 시험 건」 | `projects` | 11 | 의뢰 단위 |
| 「이 시료」 | `samples` | **24** | 실물 하나. 접수·보관·분류 이력을 갖는다 |
| 「이 모델」 | `device_models` | 6 | 기종 |
| 「시료를 받았다」 | `sample_intakes` | 13 | 접수 기록 |
| 「시료를 누가 갖고 있나」 | `sample_custody_events` | 12 | **보관 이력 — 추가만 된다** |
| 「측정을 돌렸다」 | `test_sessions` | 18 | 한 번의 측정 실행 |
| 「이 값이 나왔다」 | `measurement_results` | 14 | 측정 결과 |
| 「다시 쟀다」 | `measurement_attempts` | **21** | 시도. **결과 하나에 여러 시도** |
| 「플롯 · 로그 파일」 | `artifacts` | 11 | 산출물 |
| 「성적서」 | `test_reports` | 11 | |
| 「장비 목록」 | `test_equipment_lists` | 12 | 성적서에 실리는 사용 장비 |
| 「챔버」 | `chamber_nodes` | 11 | 측정 기계. **시험 종류 칸 없음** |
| 「기준값 · 참조 데이터」 | `reference_revisions` | **29** | 판(revision)으로 관리된다 |

---

## 3. 왜 «시도»와 «결과»가 나뉘어 있나

시험원의 현실에서 **한 항목을 여러 번 재는 일**이 흔합니다 —
설정이 틀렸거나, 장비가 튀었거나, 조건을 바꿔 다시 재거나.

```
measurement_results (결과 1건)
    ├── measurement_attempts  시도 ①   ← 실패
    ├── measurement_attempts  시도 ②   ← 실패
    └── measurement_attempts  시도 ③   ← 채택
```

**결과는 「무엇이 이 항목의 값인가」이고, 시도는 「그 값에 이르기까지 무슨 일이
있었나」입니다.** 둘을 하나로 합치면 재측정 이력이 사라지고,
**성적서에 실린 값이 몇 번째 시도였는지 아무도 답할 수 없게 됩니다.**

⚠️ **`measurement_attempts` 가 21컬럼으로 «결과보다 많은» 것이 그 증거입니다** —
시도가 담는 정보가 더 많습니다.

---

## 4. 「추가만 되는」 표들 — 지우지도 고치지도 않습니다

몇몇 표는 **INSERT 만 허용**되고 기존 행을 고치지 않습니다.

| 표 | 왜 |
|---|---|
| `chamber_heartbeat_events` | 「그때 그 챔버가 무엇을 보고했나」는 과거 사실이다 |
| `sample_custody_events` | 시료를 누가 언제 가졌나 — **고치면 추적이 무의미해진다** |
| `claim_events` | 누가 무엇을 점유했나 |
| `audit_events` | 감사 기록 |
| `project_result_selection_events` | 어느 결과를 성적서에 넣기로 했나 |

⚠️ **`chamber_heartbeat_events` 에는 「OFFLINE」이 «저장되지 않습니다».**
스키마 주석이 그 이유를 적습니다 —
*"OFFLINE is NEVER stored — it is a **derived** state: 마지막 heartbeat 가
`heartbeat_ttl_seconds` 보다 오래됐으면(또는 heartbeat 가 없으면) 읽는 쪽이
**주입된 시계에 대고 OFFLINE 을 파생한다.**"*

**「죽었다」는 사실은 «누가 보고할 수 있는 것이 아닙니다» — 죽은 것은 보고하지 못하므로.**
그래서 부재로부터 파생합니다.

---

## 5. `provider_id` — 이 프로젝트에서 가장 많이 사고 난 값

```
providers.provider_id  =  "fcc-unlicensed-conducted"     ← 자연키. 사람이 정한 이름
providers.id           =  UUID                            ← 대리키. 기계가 만든 번호
```

**둘은 다른 것이고, 섞으면 시스템이 조용히 멈춥니다.**

실장비에서 발견된 값들:

| 어디 | 값 | 결과 |
|---|---|---|
| 계약 SSOT · 중앙 DB · 중앙 컨테이너 | `fcc-unlicensed-conducted` | ✅ |
| 챔버 PC 의 옛 설정 | `unlicensed` | 중앙이 **404** 로 거절 |
| 노드 런처 | `providers.id` (UUID) | 인입 배치가 **통째로** 거절 |
| 챔버 설정 «예시 파일» | `unlicensed` | ⚠️ **측정 PC 설정 때 복사하는 파일** |

⚠️ **증상이 원인을 가립니다.** 인입 거절은 **인증 두 층을 통과한 뒤**에 오므로
**인증 문제처럼 읽힙니다.** 그리고 운영자에게는 그냥 **「노드가 안 뜬다」**로 보입니다.

**설정 파일에 이 값을 손으로 적을 때는 계약 SSOT 와 «대조»하십시오.**
「키가 있는가」만 보는 검사는 위 넷을 **같은 값**으로 봅니다.

---

## 6. provider 개발자가 알아야 하는 것 — 내 데이터가 어디로 가나

당신의 챔버 노드가 측정을 마치면, 그 결과는 이 경로로 중앙에 들어갑니다:

```
챔버 노드가 측정
   ↓
결과를 중앙에 «인입» (ingestion)
   ↓
test_sessions · measurement_results · measurement_attempts · artifacts 에 행이 생긴다
   ↓
웹 화면이 그것을 읽는다
   ↓
성적서(test_reports)에 실린다
```

⚠️ **「HTTP 200 이 왔다」와 「행이 생겼다」는 다른 명제입니다.**
이 저장소에 그 기록이 있습니다 — *"변형 하나는 **HTTP 200 이면서 행 0**이다."*

**판정은 응답 코드가 아니라 «중앙 DB 의 행»으로 하십시오:**

```sql
select count(*) from test_sessions where provider_id = '<당신 provider>';
select count(*) from measurement_results where session_id = '<방금 그 세션>';
```

---

## 7. 성적서가 만들어지는 길

```
measurement_results        여러 건이 쌓인다
        ↓
project_result_selection_events    「이 결과를 쓰기로 한다」  ← 사람의 선택. 추가만 됨
        ↓
project_result_reference_revisions  선택된 것들의 «판»       (19 컬럼)
        ↓
report_runs → report_outputs → test_reports
```

⚠️ **「선택」이 별도 표로 기록되는 것이 중요합니다.** 성적서에 어떤 값이 들어갔는지가
아니라, **누가 언제 그 값을 고르기로 했는지**가 남습니다.
**성적서는 결과가 아니라 «결정»의 산물입니다.**

---

## 8. 챔버가 분야 중립인 것의 실제 의미

`chamber_nodes` 의 컬럼 11개 (실측):

```
id · chamber_id · name · base_url · enabled · heartbeat_ttl_seconds
artifact_storage_root · equipment_config_json · accepts_web_sessions
created_at · updated_at
```

**시험 종류를 적는 칸이 없습니다.**

| 뜻하는 것 | 결과 |
|---|---|
| 한 챔버에서 여러 분야의 시험을 한다 | 챔버를 provider 별로 나눠 사지 않아도 된다 |
| 챔버 자격증명은 **하나** | provider 마다 발급하지 않는다 |
| 분야는 **다른 칸**에 있다 | `FCC_CENTRAL_PROVIDER_ID` (§5) |

⚠️ **그런데 챔버 인증 이름의 접두사가 `fcc-chamber-` 입니다.**
그 `fcc` 는 **시스템 이름**이지 시험 종류가 아닙니다
(realm `fcc-dev`, 웹 audience `fcc-platform-frontend` 도 같은 접두사).
**이름이 사실과 반대로 생겼다는 것이 알려진 부채**이고,
바꾼다면 **다른 provider 가 붙기 전이 유일하게 싼 시점**입니다.

---

## 9. 마이그레이션 — 표 구조를 바꾸는 법

DB 표 구조는 손으로 고치지 않습니다. **`migrations/` 에 번호가 붙은 SQL 파일을
추가하고, 러너가 순서대로 적용**합니다. 오늘 **35개**입니다.

```
001_initial_central_db.sql          표 39개를 만든다
...
034_sample_custody_events.sql
035_drop_write_only_sample_column.sql
```

⚠️ **`migrations/` 는 `CODEOWNERS` 가 소유를 선언한 경로**입니다 —
방아쇠 **T1** 이 걸립니다(`02-how-we-collaborate.md` §3).
**되돌리기가 매우 어렵기 때문**입니다.

⚠️ **그리고 마이그레이션 적용은 «사람의 승인이 따로 필요한» 항목**입니다.

---

## 10. 더 볼 곳

| 무엇 | 어디 |
|---|---|
| 표의 전체 정의 | `docs/platform/central_db_schema.v1.json` · `migrations/001_initial_central_db.sql` |
| 시료 도메인의 불변식 | **계약 커널**(`fcc-test-contracts`)에 있습니다 — 필드 추가는 2-레포 작업 |
| API 표면 | `docs/api/platform-api.openapi.json` (65 경로) |
| 챔버 계약 | `docs/api/session-api.openapi.json` (6 경로) |
| 분야 지식 API | `docs/api/headless-api.openapi.json` (36 경로) |

⚠️ **`migrations/001` 의 주석은 «설계 근거»를 담고 있습니다.**
표 하나에 문단 하나가 붙어 있는 경우가 많습니다 —
그 표를 만질 일이 있으면 **먼저 그 주석을 읽으십시오.**
