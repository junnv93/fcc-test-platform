# Plan: 최상위 모듈의 strict 축

Intent: ./intent.md
Spec: ./spec.md
Engineer: 세션 fcc-delivery-final-b1
Date: 2026-09-07
Status: accepted
Slug: top-level-modules-in-the-type-gate
Branch: feature/top-level-modules-in-the-type-gate

> ⚠️ `plan.md` 는 PR 로 따로 올리지 않습니다. feature 브랜치에 코드와 함께 올라가고,
> **§1 의 표가 diff 와 대조됩니다** — 표에 없는 파일이 diff 에 있으면 T4 가 발화합니다.

## 0. 이 계획이 실제로 한 일 — 한 문단

`mypy.ini` 의 네 층 절을 **한 절**(`[mypy-fcc_test_platform.*]`)로 접어 최상위 모듈
75개를 게이트 «안»으로 들였다. 게이트가 mypy 에 넘기는 source files 가 **139 → 215**
로 움직였다(실측). 그 확대가 드러낸 **183건**을 닫았고, 닫는 도중 선언이 상류의 `Any`
를 벗기며 **45건이 새로 드러나** 그것도 닫았다(합 228). `cast` 0건, `# type: ignore`
**순증가 −1**. 그리고 이 결함이 다시 생기지 못하게 하는 봉인을 세웠다 —
「선언된 strict 범위가 패키지의 모든 모듈을 덮는가」.

## 1. 바뀌는 파일 — 순서대로

### ① 게이트 자신 (T1 경로)

| # | 파일 | 무엇을 | 왜 | 요구 |
|---|---|---|---|---|
| 1 | `mypy.ini` | 네 층 절 → `[mypy-fcc_test_platform.*]` 한 절. 층별 웨이브의 사유 주석은 지우지 않고 새 절 위로 옮김 | 열거는 «다음 파일이 조용히 빠지는 자리»다. 와일드카드는 그 구멍을 구성상 갖지 않는다 | R1 |
| 2 | `tests/test_architecture_gate_conformance.py` | `STRICT_SECTIONS` 접기 + `_module_names` · `_pattern_matches` · `_modules_not_covered` 파생 + `TestTheStrictScopeCoversThePackage` 3팔 | 「네 층의 합집합이 패키지인가」를 **묻는 자리가 없었다.** 그것이 이 결함이 초록 뒤에서 산 이유다 | R1 R2 R7 R8 R9 |

### ② 공유 표면 SSOT

| # | 파일 | 무엇을 | 왜 | 요구 |
|---|---|---|---|---|
| 3 | `fcc_test_platform/application/central_db_surfaces.py` | `ScriptCursor` · `ScriptConnection` Protocol 추가 · `require_row` 헬퍼 추가 · `RowCursor.description` 을 `Any` → `Sequence[Sequence[Any]] \| None` | 스크립트가 요구하는 표면은 write 어댑터의 것과 **다르다**(넷 + 컨텍스트 매니저, 실측). 기존 둘을 넓히면 어댑터 11개의 시험 대역이 쓰지도 않는 넷을 구현해야 한다. 그래도 «파일»은 나누지 않는다 — 이 파일 머리말이 「여기가 그 한 곳」이라고 적는다 | R3 |

> ⚠️ 이 파일은 **strict 4층 안**이다. 추가는 가산적이고, `mypy -p fcc_test_platform.application` 이
> 추가 뒤에도 `Success / 75` 임을 확인했다(넓히지 않았다는 증거).

### ③ 최상위 모듈 — 선언만 (새로 드러난 오류 없음)

| # | 파일 | 요구 |
|---|---|---|
| 4 | `fcc_test_platform/artifact_storage.py` | R3 |
| 5 | `fcc_test_platform/backup_restore_drill.py` | R3 |
| 6 | `fcc_test_platform/chamber_token_evidence_cli.py` | R3 |
| 7 | `fcc_test_platform/cutover_readiness.py` | R3 |
| 8 | `fcc_test_platform/db_migration_evidence.py` | R3 |
| 9 | `fcc_test_platform/deployment_evidence_workflow_cli.py` | R3 |
| 10 | `fcc_test_platform/extraction_runner_cli.py` | R3 |
| 11 | `fcc_test_platform/frontend_deployment_evidence.py` | R3 |
| 12 | `fcc_test_platform/hardware_smoke_evidence.py` | R3 |
| 13 | `fcc_test_platform/idp_deployment_collect_cli.py` | R3 |
| 14 | `fcc_test_platform/outbox_envelope_builder.py` | R3 R4 |
| 15 | `fcc_test_platform/provider_ingestion_plan.py` | R3 |
| 16 | `fcc_test_platform/rbac_assignment_evidence.py` | R3 |
| 17 | `fcc_test_platform/db_migration_runner_cli.py` | R3 |
| 18 | `fcc_test_platform/api_app.py` | R3 |
| 19 | `fcc_test_platform/api_composition.py` | R3 |

### ④ 최상위 모듈 — 선언이 «없던 오류»를 드러낸 자리

| # | 파일 | 드러난 것 | 처분 | 요구 |
|---|---|---|---|---|
| 20 | `fcc_test_platform/extraction_evidence.py` | `int(value)` 에 `object` | `SupportsInt`/`SupportsIndex` 로 좁힘 | R3 R4 |
| 21 | `fcc_test_platform/performance_smoke.py` | 같음 ×2 (`int`·`float`) | 같음 | R3 R4 |
| 22 | `fcc_test_platform/idp_deployment_evidence.py` | 같음 | 같음 | R3 R4 |
| 23 | `fcc_test_platform/report_reconstruction_evidence.py` | 같음 | 같음 | R3 R4 |
| 24 | `fcc_test_platform/frontend_qa_evidence.py` | 같음 | 같음 | R3 R4 |
| 25 | `fcc_test_platform/artifact_sync_evidence.py` | 같음 | 같음 | R3 R4 |
| 26 | `fcc_test_platform/ingestion_execution_evidence.py` | 같음 | 같음 | R3 R4 |
| 27 | `fcc_test_platform/download_policy.py` | 같음 | 같음 | R3 R4 |
| 28 | `fcc_test_platform/identity_policy.py` | 같음 | 같음 | R3 R4 |
| 29 | `fcc_test_platform/cutover_workflow_hints.py` | 같음 | 같음 | R3 R4 |
| 30 | `fcc_test_platform/provider_ingestion.py` | 같음 — **단 여기는 가드가 없고 일부러 raise 한다** | 같은 `TypeError` 를 계약을 이름으로 말하며 던진다 | R3 R4 |
| 31 | `fcc_test_platform/bench_project_result_selection_cli.py` | `fetchone()[0]` ×6 | `require_row` | R3 R4 |
| 32 | `fcc_test_platform/central_db_live_proof_cli.py` | `fetchone()[0]` ×21 | `require_row` | R3 R4 |
| 33 | `fcc_test_platform/db_migration_collect_cli.py` | `fetchone()[0]` ×1 · `cursor.description` 이 `None` 일 수 있음 | `require_row` · 규약 위반을 이름으로 거절 | R3 R4 |
| 34 | `fcc_test_platform/rbac_assignment_collect_cli.py` | `cursor.description` 이 `None` 일 수 있음 | 같음 | R3 R4 |
| 35 | `fcc_test_platform/keyset_cursor_live_proof_cli.py` | `fetchone()[0]` ×1 · `json.loads(object)` | `require_row` · 텍스트/매핑으로 좁힘 | R3 R4 |
| 36 | `fcc_test_platform/db_migrate_cli.py` | — (연결 표면 선언) | `ScriptConnection` | R3 |
| 37 | `fcc_test_platform/cross_session_result_selection_evidence_cli.py` | `fetchone()[0]` ×3 · **태그된 유니온**(`('won', Mapping)` 대 `('conflict', None)`)에서 payload 가 안 좁혀짐 | `require_row` · 승자 불변식에 이름 | R3 R4 |
| 38 | `fcc_test_platform/cutover_live_workflow_cli.py` | `_render_object -> object` 가 최상위 호출자를 깨뜨림 | `_render_mapping` 으로 「Mapping 을 주면 dict 가 나온다」에 이름 | R3 R4 |
| 39 | `fcc_test_platform/ingestion_execution_evidence_cli.py` | — (연결 팩토리 선언) | `Callable[[], ScriptConnection]` | R3 |
| 40 | `fcc_test_platform/frontend_browser_qa_cli.py` | 드라이버 표면이 어디에도 안 적혀 있음 | `_WebDriver` · `_WebElement` Protocol (**usage 에서 파생**) | R3 |
| 41 | `fcc_test_platform/keycloak_chamber_admin.py` | 관리 클라이언트 표면이 **산문에만** 있음 | `_AdminClient` Protocol — docstring 을 타입으로 옮김 | R3 |
| 42 | `fcc_test_platform/check_auth_mode_pairing_cli.py` | 폴백 스텁의 시그니처가 실물과 **다름** — `# type: ignore[misc]` 가 그것을 누르고 있었음 | 실물과 같은 시그니처로 다시 씀. **ignore 두 개가 사라진다** | R3 R4 |
| 43 | `fcc_test_platform/provider_identity_live_proof_cli.py` | 레지스트리가 `None` 일 수 있음 · `boom` 은 언제나 던짐 | 부재를 이름으로 거절 · `NoReturn` | R3 R4 |

### ⑤ 장부

| # | 파일 | 무엇을 |
|---|---|---|
| 44 | `.claude/work-claims/top-level-modules-in-the-type-gate-20260907.json` | `branch` 를 feature 로 이관하고 `merged` 로 닫는다 |
| 45 | `intent/top-level-modules-in-the-type-gate/plan.md` | 이 파일 |

**Scope-Extended-By**: (없음)

> 🔴 **이 한 줄이 방금 T4·T5 를 «껐다».** 처음에 여기 `(없음 — 위 45개가 diff 전부다)`
> 라고 적었다. 사람 눈에는 「없음」인데 `scripts/human_judgment_triggers.py` 의
> `check_t4_t5` 는 **값이 비어 있지 않으면 어테스테이션으로 읽고 `return []` 한다** —
> 즉 T4 와 T5 가 **통째로 꺼진다.** 빈 값 판정은
> `^\s*(?:\(없음\)|없음|N/?A|-|—)?\s*$` 라서 괄호 안의 설명 한 조각이 그 판정을
> 벗어난다.
>
> 실증(주입, 2026-09-07): 이 계획의 §1 표에서 `frontend_browser_qa_cli.py` 행을 지우고
> 커밋했는데 **T4 가 침묵했다.** 그 문구를 서식대로 `(없음)` 으로 되돌리자 같은 주입에
> T4 가 발화했다. 즉 T4 를 끄는 데 3초가 걸리고, 꺼진 출력은 **초록과 같은 모양**이다.
>
> ⚠️ 이 계획은 그 판정기를 **고치지 않는다** — 게이트 자신을 고치는 것은 별개의 의도이고
> (`intent/README.md` T1), 여기서 손대면 그것 자체가 범위 확대다. 대신 **이름으로
> 보고한다**: 이 레포가 이미 적어 둔 두 문장이 여기서 만난다 — *「거부될 수 없는 승인은
> 게이트가 아니다」* 와 *「공허 통과는 두 종류다」*. 이것은 셋째 종류에 가깝다:
> **집합은 맞는데, 그것을 «보는 눈»이 꺼진 것.**

## 2. 순서와 그 근거

`spec.md` §7 Q1 이 고정한 순서를 그대로 따랐고, 중간 측정값을 여기 남긴다.

| 단계 | 무엇을 | 남은 오류 | 새로 «드러난» 것 |
|---|---|---:|---|
| 0 | 착수 (형제 #158 착지 직후) | **183** | — |
| 1 | `value: object` ×87 | 111 | **+14** (`int()`/`float()` 에 `object`) |
| 2 | ①의 14건 처분 | 97 | 0 |
| 3 | `connection: ScriptConnection` ×24 + 반환형 ×12 | 92 | **+31** (`fetchone()[0]`) |
| 4 | ③의 31건 처분 (`require_row`) | 61 | 0 |
| 5 | 나머지 인자·반환 ×94 + Protocol 둘 | 22 | +4 |
| 6 | 잔여 처분 | **0** | — |

**즉 「183 은 하한」이 실증됐다 — 실제로 처분한 것은 228건이다.** `mypy.ini` 의 api
웨이브 주석이 *「선언은 층을 따라 전파된다」* 고 적어 둔 것이 이번에도 참이었다.

⚠️ **3단계에서 내 도구가 결함을 냈다.** 반환형을 삽입하는 스크립트가 시그니처 끝의
`:` 를 찾을 때 **주석 안의 `:`** 를 골라, 세 자리의 주석을 훼손하고 반환형을 안 붙였다
(`# pragma -> None: no cover`). 문법상 유효해서 조용히 지나갈 수 있었는데 **게이트가
잡았다** — `no-untyped-def` 가 「안 붙었다」로 말했다. AST 로 전수 확인해 피해가
정확히 3자리임을 확인하고 손으로 고쳤다.

## 3. 하지 않은 것

* **죽은 코드를 지우지 않았다.** `register_session` 은 호출자 0건이다(AST 전수,
  2026-09-07). `spec.md` §7 Q4 의 판정대로 사실만 남긴다.
* **형제 #158 의 봉인을 지우지 않았다.** `test_the_whole_package_is_type_checked` 와
  `_expected_module_count` 는 그대로다. 내 변경으로 두 팔의 «수»가 215로 같아졌지만
  묻는 질문이 다르다(R5′).
* **`tests/` 를 게이트에 넣지 않았다.** 재지도 않았다 — 그 사실을 `mypy.ini` 에 적었다.
* **`[mypy]` 전역 `disallow_untyped_defs` 는 `False`** 그대로다.

## 4. 검증

| 요구 | 관측 | 결과 |
|---|---|---|
| R1 | `mypy.ini` 에 `[mypy-fcc_test_platform.*]`, `STRICT_SECTIONS` 한 줄 | ✅ |
| **R2** | 게이트 증거줄 `mypy 게이트가 돌았다 — fcc_test_platform: 215 source files` | ✅ **139 → 215** |
| R3 | `mypy -p fcc_test_platform --disallow-untyped-defs` | ✅ `Success / 215` |
| R4 | diff 계수 | ✅ `cast(` +0 · `type: ignore` **−1** (둘 삭제, 추가된 하나는 「쓰지 마라」는 주석) |
| R5′ | 형제 봉인 존치 | ✅ 무접촉 |
| R7 | `TestTheStrictScopeCoversThePackage` | ✅ 신설 |
| **R8** | 주입 — 네 층 열거로 되돌림 | ✅ **세 축 확인**: 착지(파일) · 수집 생존(18 collected) · 빨강(**정확히 76개**를 이름으로) |
| R9 | `STRICT_SECTIONS` 정의처 | ✅ 저장소에 1곳 |
| R10 | `lane_check` · merge-base 대조군 | §5 |

⚠️ **R8 의 주입은 한 번 «공허»했다.** 첫 시도는 `LAYER_ONLY_SECTIONS_BEFORE_20260907`
를 `STRICT_SECTIONS` **뒤에** 정의해 두고 참조해서 `NameError` 로 수집이 죽었다 —
출력이 비었고, 그것은 「빨갛다」가 아니라 **「안 돌았다」**였다. 파일 착지만 확인하고
멈췄으면 못 봤다. 그래서 위 표가 축을 셋으로 적는다.

그 경험이 검사 자체에 들어갔다: `test_this_check_has_teeth` 는 그 주입을 **상주**시켜
매번 확인한다. 한 번 손으로 주입하고 마는 것과 다르다 — 매처가 나중에 망가지면 그날
이 팔이 말한다.

## 5. 리그

측정은 전부 같은 리그다. 이 레포는 「리그가 답을 바꾼다」를 여러 번 치렀다.

```
python3 -m venv <dir> && <dir>/bin/pip install -e '.[test]'   (워크트리 안에서)
  contracts 0.1.26 · kernel 0.5.4 (양쪽 py.typed 있음) · fastapi 0.141.1
  psycopg 3.3.5 (py.typed 있음 — 그래서 ScriptConnection 이 «실제로» 대조된다)
  selenium 없음 (그래서 _WebDriver 는 이 파일의 여덟 함수만 검사한다)
  mypy 2.3.1 · python 3.12
push: FCC_LANE_CHECK_PYTHON=<dir>/bin/python git push ...
```
