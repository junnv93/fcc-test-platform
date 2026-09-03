# 순수성 판정을 계층 축으로 — 「AST 로 하면 된다」가 답이 아니었다 (2026-09-03)

## 문제

순수성 가드의 명제는 *「도메인 정책이 인프라를 부르지 않는다」* 다.
이 저장소는 그것을 **두 가지 축**으로 재고 있었고 **둘 다 접두사에 눈이 멀었다**:

| 축 | 어디 | 무엇을 봤나 |
|---|---|---|
| 문자열 | `test_reference_web_authoring.py` | `'from infrastructure' in source` |
| AST 최상위 | `test_platform_project_directory_invariants.py` | `node.module.split('.')[0]` |

커널 이관이 모듈에 접두사를 붙이는 순간:

    from infrastructure.db import x                  → 잡힌다
    from fcc_test_kernel.infrastructure.db import x  → **안 잡힌다**

**이관 당일에 조용히 약해지는** 자리다. 그리고 red 가 나지 않으므로 아무도 모른다.

## ⚠️ 내 첫 처방이 임시방편이었고, 두 번째 진단도 틀렸다

첫 처방은 금지어에 접두사판을 **더하는** 것이었다:

    'from infrastructure', 'from fcc_test_kernel.infrastructure', …

**접두사가 또 늘면 같은 자리에서 또 낡는다.** 목록을 손으로 늘리는 것은 이 계열이
반복해서 값을 치른 형태다.

그래서 형제 세션에 *「AST 로 import 의 최상위 이름을 뽑아 계층 이름 집합과
대조하는 것이 정공」* 이라고 적어 보냈다. **그것도 틀렸다** — 이 저장소가 이미
그렇게 하고 있었고(`_imported_roots`), **최상위 이름은 접두사가 붙는 순간
`fcc_test_kernel` 이 된다.**

> **「AST 로 하면 된다」가 답이 아니었다.** 도구가 아니라 **판정 단위**가 문제였다.

## 판정 — first-party 접두사를 벗기고 남은 첫 절

`tests/_layer_of_import.py`

    infrastructure.db.x                      → infrastructure
    fcc_test_kernel.infrastructure.db.x      → infrastructure
    fcc_test_platform.application.svc        → application
    psycopg                                  → psycopg      (접두사 없음, 그대로)

⚠️ 접두사 **목록**이 아니라 **패턴**(`fcc_test_`)이다 —
`check_shared_kernel_closure.py` 가 같은 이유로 목록판을 버렸다(`fcc_test_kernel`
이 생기자 **한 커밋 만에** 낡았다).

⚠️ 그리고 배포판을 통째로 import 하면(`import fcc_test_kernel`) 그 이름이 남는다.
삼키면 「배포판을 통째로 부른다」와 「아무것도 안 부른다」가 같은 값이 된다.

## 판별력 실측 (메모리 변이 · 트리 잔류 확인)

    접두사 없는 위반 (옛 축도 잡던 것)          🔴
    ⚠️ 접두사 붙은 위반 (옛 축은 못 잡던 것)     🔴
    형제 레인 접두사 위반 (아직 없는 레인)       🔴
    깨끗한 모듈 (대조)                        🟢

마지막 팔이 중요하다 — 셋만 red 면 「전부 red 로 만드는 검사」와 구분되지 않는다.

## 비-공허성

계층 축의 정답이 **빈 집합**이다(순수한 모듈은 아무 계층도 안 부른다).
그러므로 이 헬퍼를 쓰는 검사는 **대상이 실제로 import 를 갖는지** 따로 확인해야
한다 — 헬퍼가 그것을 대신 답해 주지 않는다. 그 팔을 호출부에 넣었고,
`test_a_module_with_no_imports_yields_nothing` 이 그 사실을 헬퍼 쪽에 기록한다.

## 왜 지금인가

3단계(`domain` 87파일)가 이 두 가드가 지키는 모듈들을 움직인다.
**문자열/최상위 축인 채로 이관하면 이관 당일에 조용히 약해진다.**
형제 세션이 같은 조건을 걸었고(그쪽 P0 가 같은 형태다), 그 지적이 맞았다.
