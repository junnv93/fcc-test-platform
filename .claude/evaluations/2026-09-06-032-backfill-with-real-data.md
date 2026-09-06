# `032` 의 병합을 «데이터로» 태워 봤다 — 그리고 옮긴 값이 검색 축에 걸린다

측정 2026-09-06 · 기준 `main` = `5893d7e` · 세션 fcc-delivery-final (a241f0f7)

## Why — 오늘의 적용이 답하지 «못한» 것

같은 날 개발 PC 의 중앙 스택에 031~035 를 적용했다. 그런데 그 실행에서 **`032` 의
백필은 한 행도 하지 않았다** — 충돌하던 프로젝트를 운영자 판정으로 지웠고, 그 결과
`customer` 에 값이 있는 행이 0건이 됐기 때문이다.

⚠️ **중앙에서는 그 경로가 정상 경로다.** 실제 프로젝트들은 `customer` 에 값이 있고
`applicant_name` 이 비어 있으며, `032` 의 `UPDATE … SET applicant_name = btrim(customer)`
가 그 값을 옮긴다. 지난 세션의 리허설(2026-09-05)도 **컬럼 473 · 인덱스 152 의 «수»**만
대조했지 «값이 옮겨지는가»는 보지 않았다.

즉 **시험원이 중앙에서 첫날 할 일**(신청자명으로 프로젝트를 찾는 것)이 지나는 경로를
아무도 데이터로 태워 보지 않았다.

## How — 적용 «전» 백업이 그 재료였다

§4 에서 받은 백업(`backup_pre_deploy_20260906_1318.sql`, 976K)이 `customer text` 를
담은 pre-032 판이다. 그것을 **일회용 사본**에 복원하고, `032` 자신의 문서가 적은
진리표 네 줄을 그대로 심었다(운영·개발 DB 무접촉, 사본은 삭제했다):

| project_code | `customer` | `applicant_name` | `032` 의 선언된 처분 |
|---|---|---|---|
| `CEN-A-001` | 한국방송통신전파진흥원 | `NULL` | BACKFILL |
| `CEN-B-002` | 두원공과대학교 | `''` | BACKFILL |
| `CEN-C-003` | 삼성전자 | 같은 값 | 이미 병합됨 |
| `SM-DEVPC-0901` | 개발검증 | **다른 값** | **CONFLICT → 거부** |

## Verification — 실측

### ① 거부가 났고, **네 행 모두 손대지지 않았다**

    {"ok": false, "error": "migration 032 refuses to drop projects.customer:
     1 project(s) … Offending projects: SM-DEVPC-0901 (customer='개발검증',
     applicant_name='DevPC Applicant')"}

거부 뒤 네 행이 전부 원래 값 그대로였다 — **부분 백필이 없다.** `032` 가 「전체가 한
트랜잭션이라 거부는 DB 를 건드리지 않는다」고 주장하는 그 성질이 실제로 성립한다.
그리고 원장에는 `031` 만 들어갔다 — 러너의 마이그레이션별 트랜잭션 경계도 맞다.

### ② 충돌을 해소하니 값이 **옮겨졌다**

    CEN-A-001 | applicant_name=한국방송통신전파진흥원   ← customer 에서 옮겨짐
    CEN-B-002 | applicant_name=두원공과대학교          ← customer 에서 옮겨짐
    CEN-C-003 | applicant_name=삼성전자                ← 원래 값 유지
    SM-DEVPC-0901 | applicant_name=DevPC Applicant     ← 해소한 값 유지
    projects.customer 칸 = 0                            ← 사라짐

### ③ ★ 옮긴 값이 **화면의 검색 축에 걸린다**

술어를 지어내지 않고 구현의 SSOT 에서 파생했다 —
`PROJECT_SEARCH_COLUMNS = ('management_number', 'project_code', 'applicant_name')`
(⚠️ `customer` 가 그 집합에 **없다**. 031 이 축을 옮겼다는 뜻이다). 그 술어 그대로:

    q='한국방송'   → CEN-A-001      ← 퇴장한 칸에만 있던 값으로 걸린다
    q='두원공과'   → CEN-B-002      ← 〃
    q='삼성전자'   → CEN-C-003
    q='없는신청자' → (0건)          ← 대조군: 검색이 실제로 거른다

⚠️ **대조군이 있어야 「걸렸다」가 뜻을 갖는다.** 필터가 아무것도 안 하면 전부 걸린다.

### ④ 나머지 축

`idx_projects_search_applicant_name`(trigram) · `idx_projects_applicant_directory` 둘 다
사본에 있고, `sample_custody_events` 표가 있으며 `samples.metadata_json` ·
`projects.name` 은 사라졌다.

## ⚠️ 이 관측에는 «게이트»가 없다 — 그리고 그 자리가 지금 이 레인에서 안 돈다

`032` 의 진리표를 자동으로 확인하는 검사는 없다. 자연스러운 자리는
`tests/integration/test_central_db_e2e_live.py` 의 `test_migration_029_…` 옆이다 —
그 파일은 마이그레이션별 «구체» 증명을 라이브 DB 에 대고 하는 선례다.

**그런데 그 증명이 이 레인에서 돌지 않는다.** 두 이유를 실측했다:

1. `docs/api/headless_provider_registry.json` 을 요구한다 — **provider 레포의
   아티팩트**이고 이 레인에도 모노레포에도 없다(`--provider-code` 로 우회 가능).
2. `git ls-tree -r <sha> docs/platform/migrations` 를 부른다 →
   `fatal: not a tree object`. **이 레인은 그 트리를 `migrations/` 로 배달한다** —
   증명이 모노레포의 배치를 전제로 쓰였다.

그래서 이 커밋은 **검사를 붙이지 않는다.** 돌려 볼 수 없는 자리에 검증되지 않은
단계를 싣는 것은 「있다는 사실이 지켜진다는 뜻이 되지 않는」 형태를 하나 더 만드는
것이다. 대신 이 문서가 관측과 **그 자리가 왜 비어 있는지**를 이름으로 남긴다.

## 후속

1. 라이브 증명을 이 레인에서 돌 수 있게 만드는 것이 선행 과제다(배치 해소를
   `resolve_repo_artifact` 로 옮기고, provider 아티팩트 의존을 선택적으로).
   그것이 되면 `migration_032` 단계는 위 ①~③ 을 그대로 옮기면 된다.
2. ⚠️ 이 측정은 **개발 PC 의 데이터 모양**으로 했다. 중앙의 실제 분포(충돌 건수,
   `customer` 만 있는 행의 수)는 여전히 **중앙에서만** 알 수 있다 —
   `fcc-platform-central-migration-readiness` 의 `refusal-guard` 축이 그것을 답한다.
3. 백업은 `~/backup_pre_deploy_20260906_1318.sql` 에 있다. 이 실험을 다시 하려면
   그 파일과 위 진리표 네 줄이면 된다.
