# Plan: 계획된 조건을 «세지 않고 나열하는» 읽기

Author: younhs21-dexter / collaborator (초안: Claude 세션)
Date: 2026-09-09
Status: draft
Slug: plan-conditions-read
Spec: `intent/plan-conditions-read/spec.md`

## 1. 바뀌는 파일 — 순서대로

⚠️ 1~2 는 **다른 레인**(`fcc-test-contracts`)이다. 그 레인이 태그를 내기 전에는
3 이후가 CI 에서 빌드되지 않는다 — 순서가 그래서 중요하다.

| # | 파일 | 무엇을 | 왜 이 순서 | 요구 |
|---|---|---|---|---|
| 1 | `packages/fcc-test-kernel/…/central_contract/surface_project_results.py` (**contracts**) | `ROUTES` · `PERMISSIONS` · `OPERATION_QUERY` · `RESPONSE_HEADERS` · `PlanConditionEnvelope`/`List` 스키마 · `OPERATIONS` | 경로와 스키마가 여기 있다. 이것 없이는 platform 이 붙일 자리가 없다 | R1·R3·R4 |
| 2 | (contracts 릴리스) `kernel-v0.5.5` · 필요 시 `v0.1.27` | 태그 발행 | **사람 승인** — §4 | — |
| 3 | `fcc_test_platform/domain/ports/output/central_read_port.py` | `read_plan_conditions` 선언 | 포트가 계약이고 어댑터가 그것을 구현한다 | R1·R5 |
| 4 | `fcc_test_platform/application/central_read_adapter.py` | 칼럼 9 · 커서 · 도메인 · SQL 6형태 · 메서드 | 포트 뒤 | R1·R2·R3 |
| 5 | `fcc_test_platform/application/central_read_service.py` | `project_plan_conditions` + `_plan_condition_envelope` | 어댑터 뒤 | R2·R3·R5 |
| 6 | `fcc_test_platform/api/platform_routes.py` | 어댑터 메서드 + 라우트 함수 + 핸들러 등록 | 서비스 뒤 | R1·R4 |
| 7 | `requirements-central.txt` · `pyproject.toml` | 커널 핀 두 곳 | **2 이후** | — |
| 8 | `docs/api/platform-api.openapi.json` · `packages/api-artifacts/artifacts/platform-api.openapi.json` | `scripts/export_platform_openapi.py` 로 재생성 | 6 이후 | R1 |
| 9 | `apps/web/src/api/generated/platform-api.types.ts` | `npm run codegen` | 8 이후 | — |
| 10 | `apps/web/src/api/platform-client.ts` | `fetchPlanConditionsPage` + 타입 | 9 이후 | R3 |
| 11 | `apps/web/src/ui/ItemTable.tsx` (신규) | 상태 셋 × 모드 그룹 표 | — | R6·R7 |
| 12 | `apps/web/src/routes/test-items.tsx` (신규) | 계열 하나의 항목 화면 + 필터 | 10·11 뒤 | R6·R7 |
| 13 | `apps/web/src/routes/console-home.tsx` | 계열 행 → 링크, 배정/이슈 표시 | 12 뒤 | R6 |
| 14 | `apps/web/src/shared/route-links.ts` · `app.tsx` · `locales/{en,ko}.json` · `styles/global.css` | 경로·문구·스타일 | — | R6·R7 |
| 15 | `apps/web/src/shared/current-operator.ts` (신규) | 토큰의 `employee_id` 를 읽는 한 곳 | 13 뒤 | (§7 Q2 미정) |

## 2. 무엇으로 검증하나

| 요구 | 검사 | 지금 이 검사는 |
|---|---|---|
| R1 | `tests/test_platform_plan_conditions_read.py::test_returns_one_row_per_planned_condition` | **새로 만든다** |
| R2 | 〃 `::test_envelope_carries_test_item_verbatim` | **새로 만든다** |
| R3 | 〃 `::test_cursor_resumes_and_terminates` / `::test_technology_facet_filters` | **새로 만든다** |
| R4 | 〃 `::test_requires_platform_read` | **새로 만든다** |
| R5 | 〃 `::test_backend_failure_raises_not_empty` | **새로 만든다** |
| R6·R7 | `apps/web/tests/ui/item-table.test.tsx` — 세 상태가 행으로 나오고, 미착수 행에 모드·항목·분이 있다 | **새로 만든다** |
| 무결성 | 〃 `::test_states_partition_the_plan` — `완료 + 배정 + 미착수 == 계획 총수` | **새로 만든다** |
| 계약 대조 | 기존 `tests/test_platform_read_api_fe_p0d.py` 계열이 커서 도메인/아리티를 스키마 SSOT 와 대조 | **이미 있다** — 새 키셋이 그 축에 얹힌다 |

**주입 확인** — 각 새 검사에 일부러 깨진 입력을 넣어 빨개지는지 본다:
- R2: envelope 에서 `test_item` 을 `progress_area` 로 바꿔 본다.
- R3: 커서 칼럼에 envelope 에 없는 이름(`raw_test_type`)을 넣어 본다.
  ⚠️ 이것은 가정이 아니다 — **실제로 그렇게 만들었다가 첫 페이지가 KeyError →
  500 이 됐다**(2026-09-09). 그 회귀를 잡는 검사가 되어야 한다.
- R5: 커넥션 팩토리가 던지게 하고 응답이 빈 배열이 아닌지 본다.
- R7: 계획 행 하나에서 `raw_test_type` 을 비우고 화면이 이름을 «지어내지»
  않는지 본다.

🔴 **오늘 시점: 위 검사가 하나도 없다.** 구현은 수동 관측으로만 확인했다
(§spec 6). 이 계획은 그것을 부채로 남기지 않기 위해 여기에 적는다.

## 3. 게이트 통과 계획

- `scripts/lane_check.py` — ⚠️ **이 기계에서 아직 돌리지 못했다**(platform venv
  없음). 즉 「이름 집합이 기준선과 같다」를 확인하지 못했다. PR 의 `lane-check`
  가 최종 판정이다.
- `delivered_test_run_baseline.json` 에 **아무것도 추가하지 않는다.**
- 🔴 별건 하나: `tests/test_frontend_visual_language.py` 가 현재 **9건 빨갛다.**
  이 레인의 선언된 실패는 0 이다. 그 9건은 `aa97ab1`(어제 커밋)이 남긴 것이고
  이 계획의 범위가 아니다 — 고치거나 등재하려면 별도 결정이고, 등재는 **T2** 라
  `Debt-Accepted-By:` 가 필요하다.
- 커밋 메시지 17항목 자가점검 + `Self-Audit: 17-items` 트레일러.

## 4. 승인 지점 (사람이 눌러야 하는 것)

1. **커널 태그 발행** — `kernel-v0.5.5`(subdirectory) + 루트 아티팩트가 바뀌면
   `v0.1.27`. 소유자(`junnv93`)에게 **따로** 묻는다. CLAUDE.md 가 「진행해라
   하나로 묶지 말라」고 명시한 항목이다.
2. **핀 갱신 후 재설치** — `pip install -r requirements-central.txt`.
   지금 이 기계는 핀(`kernel-v0.5.4`)과 설치본이 **갈라져 있다**.
3. **시각 골든 갱신** — 사람이 비교한 뒤.
4. Keycloak `fcc-dev` 의 unmanaged attribute 허용 + `employee_id` 매퍼는 **dev
   전용**으로 이미 넣었다. 운영 IdP 반영은 이 계획 밖이고 별도 결정이다.

## 5. 되돌리는 법

- **platform 쪽**: 브랜치 하나를 버리면 된다. 저장소는 만지지 않았고
  마이그레이션도 없다 — 롤백에 데이터 손실이 없다.
- **contracts 쪽**: 태그는 «되돌릴 수 없다». 발행한 뒤 문제가 있으면
  `kernel-v0.5.6` 으로 앞으로 고친다. 그래서 §4-1 이 별도 승인이다.
- **FE 생성물**: `npm run codegen` 이 아티팩트에서 재생성하므로, 아티팩트를
  되돌리면 타입도 되돌아온다.

## 6. 범위 밖으로 새는 것을 막는 선언

작업 중 마주쳤지만 **이 계획에서 하지 않는다.** 발견되면 새 intent 로 올린다.

- 조건 축(채널·대역폭·안테나·변조)을 중앙 테이블로 실어 나르기 — 마이그레이션.
- 배정·완료 입력·조건 생성 — **쓰기**. 이 화면은 뷰어다.
- 「내 것」의 서버측 필터 — 사번 SSOT 가 정해지기 전까지 계약에 넣지 않는다.
- `tests/test_frontend_visual_language.py` 9건 — 별건(§3).
- 진행률 롤업(`/progress`)의 시간 가중 계산 — 건드리지 않는다.

**Scope-Extended-By**: (없음)
