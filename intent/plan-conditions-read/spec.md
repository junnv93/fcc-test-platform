# Spec: 계획된 조건을 «세지 않고 나열하는» 읽기

Author: younhs21-dexter / collaborator (초안: Claude 세션)
Date: 2026-09-09
Status: draft
Slug: plan-conditions-read
Intent: `intent/plan-conditions-read/intent.md`

## 1. 요구사항

- **R1.** `GET /platform/projects/{project_id}/plan-conditions` 가
  `published_plan_expectation` 의 그 프로젝트 행을 **조건 단위로** 돌려준다.
- **R2.** 각 행은 최소한 `condition_hash` · `technology` · `test_item` ·
  `progress_bucket_id` · `progress_area` · `planned_minutes` 를 싣는다.
  `test_item` 은 저장소의 `raw_test_type` 을 **verbatim** 으로 옮긴 것이다.
- **R3.** 커버리지·claims 와 **같은 페이지네이션 계약**을 쓴다: `limit` 없으면
  전량, 있으면 커서(`cursor`) 재개, `technology` 패싯 지원.
- **R4.** 권한은 `platform:read`. 새 권한을 만들지 않는다.
- **R5.** 실패는 **크게** 실패한다 — 연결·질의 실패는 `CentralReadError` 로
  올라가 5xx 가 된다. 조용한 빈 배열은 「계획이 없다」로 읽혀 화면이 「할 일
  없음」을 그린다.
- **R6.** 실무자 화면(`/test-items`)이 이 읽기를 커버리지·claims 와
  `condition_hash` 로 조인해 한 계열의 조건을 **완료 / 배정 / 미착수** 세 상태로
  표시한다.
- **R7.** 미착수 행이 **이름을 갖는다**(모드 + 시험항목 + 계획 시간).

## 2. 범위 밖 (Non-goals)

- **쓰기 없음.** 배정(acquire) · 해제 · 완료 입력 · 조건 생성 — 전부 밖이다.
  이 화면은 뷰어이고, 쓰기는 작업 화면의 일이다.
- **조건 축을 중앙으로 실어 나르지 않는다.** 채널·대역폭·안테나·변조는
  마이그레이션 + 계획 발행 경로 변경이고 별건이다(§7 Q1).
- **「내 것」 필터를 계약에 넣지 않는다.** 서버는 전부 주고, 화면이 사번으로
  가른다. 신원 규칙이 정해지지 않았고(§7 Q2), 계약에 넣으면 그 미정이 계약에
  굳는다.
- 진행률 집계(`/progress`)는 **건드리지 않는다.** 그쪽은 시간 가중 롤업이고
  이 읽기는 원본 행이다. 둘은 다른 질문에 답한다.
- 새 테이블·새 뷰·새 인덱스 없음.

## 3. 설계 결정과 근거

| # | 결정 | 버린 대안 | 근거 |
|---|---|---|---|
| D1 | 새 엔드포인트 신설 | `/coverage?include=planned` 로 기존 경로 확장 | 커버리지는 「측정된 것」이라는 이름과 뜻을 갖는다. 거기에 측정되지 않은 행을 섞으면 그 이름이 거짓이 되고, 이미 그 계약을 쓰는 화면들이 조용히 다른 것을 받는다 |
| D2 | 테이블을 직접 읽는다 | 새 뷰(view)를 만든다 | 집계가 없다 — 계획은 발행 시점에 이미 조건 단위다. 뷰는 「무엇을 접었는가」가 있을 때 값을 하고, 여기엔 접을 것이 없다 |
| D3 | 커서 = `(progress_area, condition_hash)` | `(progress_area, raw_test_type, condition_hash)` | ⚠️ 커서 칼럼 이름은 SQL «과» envelope 양쪽에 있어야 한다 — `_read_page` 가 다음 커서를 마지막 envelope 에서 뽑는다(`items[-1][column]`). `raw_test_type` 은 `test_item` 으로 이름을 바꿔 내보내므로 커서에 쓸 수 없다. 실측: 넣었더니 첫 페이지가 KeyError → 500. `condition_hash` 가 (project, provider, plan) 안에서 유니크라 둘로 이미 전순서다 |
| D4 | `raw_test_type` → `test_item` 으로 이름을 바꿔 노출 | 저장소 칸 이름 그대로 | 부르는 쪽이 계획·커버리지를 조인한다. `technology` 축은 두 계약이 같은 이름을 써야 하고(계획 저장소는 `coverage_technology`), 「원본 시험 유형」이라는 저장소 어휘는 API 계약의 어휘가 아니다 |
| D5 | 기술 패싯 필터는 `coverage_technology` 칸 | `technology` | 계획 테이블에 `technology` 칸이 **없다**. 없는 칸으로 필터하면 SELECT 가 통째로 죽는다 |
| D6 | 「내 것」 판정을 화면에서 | 서버가 필터해서 준다 | §2 참조. 그리고 남의 배정을 «지우면» 아무도 안 잡은 것처럼 보여 두 사람이 같은 조건을 재게 된다 — claim 원장이 막으려는 바로 그 일이다 |
| D7 | 미착수의 «이름»을 만들지 않는다 | 모드 이름을 복제해 채운다 | 구별되지 않는 세 조건을 구별되는 것처럼 그리면, 실무자는 그 차이를 장비 앞에서 발견한다. 없는 것은 없다고 적는다(§StatTile `unavailable` 과 같은 판단) |

## 4. 영향받는 경계

- **레인**: platform **+ contracts**. ⚠️ 경로·권한·쿼리 파라미터·OpenAPI 스키마가
  `fcc_test_kernel/application/central_contract/surface_project_results.py` 에
  있다. **커널 태그(`kernel-v0.5.5`)가 먼저** 나가고, platform 의 핀 두 파일이
  그다음이다. 순서가 바뀌면 CI 의 platform 이 그 surface 없는 커널로 빌드된다.
- **DB**: 마이그레이션 **없음**. 테이블·칼럼·인덱스 그대로.
  기존 인덱스 `idx_published_plan_expectation_rollup`
  `(project_id, provider_id, progress_area, progress_bucket_id)` 가 이 읽기의
  정렬 앞부분을 덮는다.
- **API 계약**: 바뀐다. 아티팩트 재생성이 계획에 있다
  (`scripts/export_platform_openapi.py` → `docs/api/` + `packages/api-artifacts/`),
  FE 타입도 재생성(`npm run codegen`).
- **프론트엔드**: 시각 골든이 **바뀐다** — 새 라우트 `/test-items` 와 실무자
  홈의 표시 변경. 사람이 비교한 뒤 갱신해야 한다.
- **provider 비공개 레포**: 닿지 않는다. 측정 코드는 이 경로를 부르지 않는다.

**Debt-Accepted-By**: (없음)

## 5. 정책 확인

- **인증/RBAC**: `platform:read` 하나로 충분하다. 새 권한 키를 만들지 않았고,
  `PERMISSIONS['list_project_plan_conditions'] = 'platform:read'` 로 기존 키를
  가리킨다. 프로젝트 멤버십 검사는 다른 읽기와 같은 `authorize(...)` 를 지난다.
- **개인정보**: 새 PII 없음. 응답에 사람이 나오는 칸은 없다 — `operator` 는
  커버리지·claims 가 이미 주던 값이고 이 계약에는 들어 있지 않다.
  ⚠️ 다만 화면이 «사번»을 표시하게 됐다. 사번은 이 조직 안에서 개인을
  가리키므로, 외부 공유 화면에서는 이름/사번 노출 여부를 따로 판단해야 한다.
- **오프라인 동작**: 이 읽기는 중앙 전용이다. 챔버 PC 의 오프라인 측정 루프는
  이 경로를 부르지 않으므로 중앙이 끊겨도 측정은 계속된다 — 끊기는 것은 이
  «화면»뿐이고, 그때는 R5 에 따라 크게 실패한다.

## 6. 성공 판정

| 요구 | 어떻게 관측하나 |
|---|---|
| R1·R2 | `curl …/plan-conditions?limit=200` 응답 첫 행에 `test_item` 이 있고, 총 행 수가 `SELECT count(*) FROM published_plan_expectation WHERE project_id=…` 와 같다 |
| R3 | 커서 두 페이지가 이어져 845N 에서 200 + 193 = 393 |
| R4 | 권한 없는 토큰으로 403 |
| R5 | DB 를 내린 상태에서 5xx (빈 배열 아님) |
| R6·R7 | `/test-items?model=…&family=2.4GHz WLAN` 에서 상태 셋이 모두 행으로 나오고, 미착수 행에 모드·시험항목·분이 있다 |
| 무결성 | 한 계열에서 `완료 + 배정 + 미착수 == 계획 총수` |

⚠️ **아직 테스트가 없다.** 위는 전부 수동 관측이고, `plan.md §2` 가 이것을
자동 검사로 옮기는 것을 계획에 넣는다.

## 7. 열린 질문

1. 조건 축(채널·대역폭·안테나·변조)을 중앙까지 실어 나르는 별건은 언제 하는가?
   그때까지 「구별되지 않는 행」은 화면이 그 사실을 적는 것으로 둔다.
   → 답( ):
2. 사번의 SSOT 는 어디인가 — IdP 사용자 속성인가 `users.employee_id` 인가?
   둘 다 두면 언젠가 갈라진다.
   → 답( ):
3. 이 경로의 기본 unbounded 를 유지하는가, 상한을 두는가?
   → 답( ):
4. `kernel-v0.5.5` 를 이 read 하나로 발행하는가, 다른 커널 변경과 묶는가?
   → 답( ):
