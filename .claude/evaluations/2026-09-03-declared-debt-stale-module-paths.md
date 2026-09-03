# 선언된 부채 17 → 11 — 경로가 아니라 모듈에게 물었다 (2026-09-03)

## 무엇이 부채였나

`delivered_test_run_baseline.json` 이 17개 node-id 를 실패로 **선언**하고
`lane_check` 가 그것을 붙잡고 있었다. 숨겨진 결함이 아니라 명시적 부채다.

그 17의 **지배 유형이 분리 잔류**였다 — 실패 사유를 전수로 세니:

    9   FileNotFoundError: src/application/… 또는 src/domain/… 를 찾는다
    4   assert ()
    4   the composed registry offers no provider at all
    …

`src/` 는 이 레인에 없다. 추출(2026-08-30)이 그 모듈들을 `fcc_test_platform/`
아래로 옮겼는데 검사들이 모노레포 시절 경로를 그대로 들고 있었다.

## 답은 이미 이 저장소에 있었다

`tests/_moved_module_source.py`:

> 경로를 하드코딩한 테스트는 *트리*에 대해 단언하지, 검사하려는 *코드*에 대해
> 단언하지 않는다 — 그리고 그 둘은 같은 것이기를 그만두었다.

같은 웨이브가 커널 2단계에서 이 헬퍼로 7건을 고쳤다. 같은 처방을 부채에 적용했다.

## 갚은 것 — 6 node-id

| 검사 | 옛 축 | 새 축 |
|---|---|---|
| `test_probe_suffix_ssot_is_shared_with_the_policy` | `src/domain/services/rate_limit_policy.py` | `fcc_test_contracts.common.rate_limit_policy` |
| `test_no_coverage_table_string_in_ingestion_modules` | `src/application/headless/platform_*.py` ×4 | `fcc_test_platform.provider_*` ×4 |
| `TestNoOpAuditGrainPolicyStructuralGuard` ×3 | `src/application/platform/*.py` | `fcc_test_platform.application.*` |
| `test_no_psycopg_module_level_import` | 같음 ×6 | 같음 ×6 |

### 매핑의 증거는 추측이 아니었다

`platform_ingestion` → `provider_ingestion` 은 이름이 바뀌었으므로 추측이 될
수 있었다. 그러나 **같은 클래스의 형제 검사가 이미
`fcc_test_platform.provider_ingestion_plan` 을 import 하고 있었다** —
매핑이 그 파일 안에 이미 적혀 있었고, 낡은 것은 그 옆의 튜플뿐이었다.

## ⚠️ 고치는 도중 두 번 틀렸다

1. **다중행 import 문 한가운데에 import 를 삽입**해 `SyntaxError` 를 냈다.
   정규식이 「첫 import 줄 다음」을 찾았는데 그 줄이 `(` 로 열려 있었다.

2. **한 목록 안에 두 축을 섞었다.** `test_no_psycopg_module_level_import` 의
   여섯 항목 중 둘만 모듈 이름으로 바뀌고 넷은 경로로 남았다 — 내 치환이
   지도에 있는 셋만 봤기 때문이다. 섞인 목록은 다음 사람이 어느 쪽이 맞는지
   알 수 없다. 여섯 전부를 모듈 축으로 통일했다.

## 왜 이 정정이 게이트를 낮추는 것이 아닌가

`moved_module_source` 는 **없는 모듈에 예외를 낸다**(`ModuleSourceUnavailable`).
경로판은 `FileNotFoundError` 로 죽거나 — 더 나쁘게 — 우연히 존재하는 다른 파일을
읽고 **통과**할 수 있었다. 즉 새 축이 옛 축보다 **엄격하다.**

비-공허성 팔도 함께 넣었다: 목록이 비면 `for` 가 0회 돌아 「전부 통과」가 된다.

## 기준선 축소 — 게이트가 지시한 방향

    선언된 실패 17 / 관측된 실패 11
    ⚠️ 선언됐는데 실패하지 않은 것 6개 — 선언이 낡았다
       고쳐서 그런 것이면 --write-baseline 으로 선언을 줄여라.

⚠️ **이것은 「기준선을 관측값으로 덮어써서 초록을 만드는 것」의 반대다.**
게이트의 경고가 겨냥하는 것은 **늘어난** 실패를 따라 올리는 것이고, 여기는
**줄어든** 방향이라 게이트 자신이 축소를 지시한다.

## 남은 11 — 이 레인이 갚을 수 없는 것들

| 수 | 성격 |
|---|---|
| 4 | `test_platform_reference_composition` — 합성이 provider 를 하나도 못 찾는다 |
| 1 | `test_project_result_selection_performance` — `benchmark_harness` 가 이 레인에 없다 (provider · 계약 레인에만) |
| 1 | `TestRunbookExists` — `chamber-real-measurement-staging-runbook.md` 가 **provider 저장소에 있다** |
| 5 | cutover · chamber API · postgres writer 계열 |

⚠️ 앞의 셋은 **이 레인에 없는 자산을 단언한다.** 이번 세션이 중앙 자산 게이트를
provider 에서 가져온 것과 **정확히 반대 방향의 이관**이 필요하다 — 그리고
그 판정은 소유자가 해야 하므로 여기서 임의로 지우지 않는다.
