# Plan: 시험용 대역이 세션 uuid 의 target 스코프를 못 지킨다

Intent: ./intent.md
Spec: ./spec.md
Engineer: 세션 fcc-delivery-final-c1
Date: 2026-09-07
Status: accepted
Slug: fake-resolver-honours-target-scope
Branch: feature/fake-resolver-honours-target-scope

## 1. 바뀌는 파일 — 순서대로

| # | 파일 | 무엇을 | 왜 이 순서 | 대응 요구사항 |
|---|---|---|---|---|
| 1 | `tests/test_central_id_resolver_target_scope.py` | **새 파일.** 두 구현 대조 봉인 + AST 파생 등호 + R3/R4 분기 | **먼저 온다.** 결함이 지금 «살아 있으므로» 이 봉인은 고치기 전에 red 여야 한다. 그 red 가 주입 확인이다 — 인위적으로 고장을 넣는 것보다 강하다 | R2·R3·R4·R5·R7 |
| 2 | `fcc_test_platform/central_id_resolver.py` | `register_session` 에 `target_identity` 추가 · `resolve_session_uuid` 에 R3 가드 · `_has_target_scoped` 헬퍼 · `_SessionKey` 주석을 판정으로 교체 | 1 이 red 임을 본 «뒤»에 온다. 그래야 초록이 무엇을 보고 난 초록인지 말할 수 있다 | R1·R2·R3·R4 |
| 3 | `tests/test_outbox_envelope_builder.py` | target 을 싣는 픽스처와 시험을 **더한다**(기존 8건은 손대지 않음) | 2 뒤. 빌더→대역 경로가 target 을 실제로 흘려야 이 시험이 의미를 갖는다 | R6 |
| 4 | `intent/fake-resolver-honours-target-scope/plan.md` | 이 파일 — 주입 확인 결과를 채운다 | 마지막. 결과를 적으려면 결과가 있어야 한다 | — |
| 5 | `.claude/work-claims/fake-resolver-honours-target-scope-20260907.json` | `status` · `result` 갱신 | 마지막 | — |

⚠️ **`fcc_test_platform/postgres_central_id_resolver.py` 는 이 표에 없다.** 실물은
옳고, 이 판은 그 파일을 한 글자도 바꾸지 않는다. diff 에 그 이름이 나오면 T4 다.

## 2. 무엇으로 검증하나

| 요구사항 | 검사 | 지금 이 검사는 |
|---|---|---|
| R1 (writer 가 3-튜플을 만든다) | `…target_scope.py::TestKeyShapes::test_writer_can_make_every_key_shape_the_reader_reads` | 새로 만든다 |
| R2 (두 target 이 갈린다) | `…target_scope.py::TestBothImplementations::test_both_split_two_targets_on_one_chamber` | 새로 만든다 |
| R3 (선언한 스코프를 안 넘어간다) | `…target_scope.py::TestDeclaredScope::test_declared_scope_is_not_silently_bypassed` | 새로 만든다 |
| R4 (선언 안 한 무관심은 존중) | `…target_scope.py::TestDeclaredScope::test_undeclared_scope_still_serves_the_agnostic_entry` | 새로 만든다 |
| R5 (모양 집합 등호, AST 파생) | R1 과 같은 검사 — 한 검사가 두 요구사항을 진다 | 새로 만든다 |
| R6 (target 축이 실행된다) | `test_outbox_envelope_builder.py::TestTargetScopedSession::test_two_targets_one_session_do_not_share_a_uuid` | 새로 만든다 |
| R7 (두 구현이 같은 답) | R2 와 같은 검사 — 실물의 값을 «베끼지 않고» 둘 다에게 묻는다 | 새로 만든다 |

**주입 확인** — 축별로 «하나씩» 놓았다. 「빨개졌다」는 「내가 주장한 이유로
빨개졌다」가 아니기 때문이다. 측정 조건: 새 venv(contracts 0.1.26 · kernel 0.5.4 ·
mypy 2.3.1) · `QT_QPA_PLATFORM=offscreen` · `-p no:randomly`.

| 주입 | 무엇을 되돌렸나 | 결과 | 판정 |
|---|---|---|---|
| **①** | 소스 «전체»를 origin/main 으로 | 5 failed / 3 passed | ⚠️ **엉뚱한 축에 착지.** 5건 전부 `TypeError: unexpected keyword argument 'target_identity'` 였고, 동작 축도 등호도 «실행되지 않았다». 아래 A·B 로 갈랐다 |
| **A** | writer 는 고친 채 **R3 가드만** 제거 | 2 failed / 6 passed | ✅ 동작 축만 움직였다 — `test_declared_scope_is_not_silently_bypassed` · `test_the_two_worlds_are_scoped_per_local_session` |
| **B** | 가드는 둔 채 **3-튜플 대입만** 되돌림 | 4 failed | ✅ 등호가 `Items in the first set but not the second` 로, 갈라짐이 `'uuid-for-B' == 'uuid-for-B' : 한 chamber 의 두 target 이 …` 로 |
| **C-1** | reader 에 **4번째 키 모양**(한 줄) | 1 failed | ✅ 등호 red |
| **C-2** | 같은 것을 **여러 줄** 형태로 | 1 failed | ✅ 등호 red — **spec §7-5 의 질문에 실증으로 답했다.** AST 는 줄바꿈을 보지 않는다 |
| **D** | 파생기를 눈멀게(`_no_such_attribute`) | 2 failed | ✅ `test_the_derivation_is_not_vacuous` 가 «빈 집합끼리의 등호»를 잡는다 |
| **R6-a** | 3-튜플 대입 되돌림 | 2 failed | ✅ 빌더 경로 red |
| **R6-b** | R3 가드만 제거 | **처음엔 25 passed** | ❌ **공허했다 — 그리고 그것이 이 주입의 수확이다** (아래) |

### ⚠️ 주입이 «내 시험 하나가 공허함»을 잡았다

`test_an_unregistered_target_is_loud_when_scope_was_declared` 를 처음
`chamber_id='CH-1'` 로 구성했다. 그러면 가드를 제거해도 fallback 인
`self._session_uuid[('CH-1', 11)]` 이 **어차피 miss** 해 `KeyError` → 같은 예외로
터진다. 즉 그 시험은 **가드 유무와 무관하게 초록**이었다 — 「집합이 틀린」 공허 통과이고,
성실히 보고하며 지나간다. 비어있음 단언으로는 안 잡힌다.

고친 형태: **chamber 를 일부러 안 넘긴다.** 그러면 fallback 이 «성공할» 상황이 되고,
가드가 유일한 방어선이 된다. 재주입 결과 **1 failed** — 이빨이 생겼다.

같은 이름의 시험이 봉인 파일 쪽에는 chamber 없이 구성돼 있어 주입 A 에서 정확히
빨개졌다. **두 시험의 «구성»이 갈랐다.** 이름이 같다고 같은 것을 재지 않는다.

### 고친 뒤 관측값

| 대상 | 값 |
|---|---|
| `tests/test_central_id_resolver_target_scope.py` (새 파일) | **8 passed, 4 subtests** |
| `tests/test_outbox_envelope_builder.py` | **25 passed, 2 subtests** (before 22 → after 25, 기존 단언 무변경) |
| `tests/test_postgres_central_id_resolver.py` (안 건드린 실물) | **16 passed** |
| `mypy -p fcc_test_platform` | **Success — 0 issues / 215 source files** (안 움직임) |
| `tests/test_architecture_gate_conformance.py` | **18 passed + 9 subtests** (안 움직임) |

## 3. 게이트 통과 계획

* `scripts/lane_check.py` — 실패 이름 집합이 `delivered_test_run_baseline.json` 과
  같아야 한다. 기준선(pre-push 실측, `origin/main@51b8cdf`): **선언된 실패 0 / 관측된
  실패 0**, `3383 passed, 27 skipped, 859 subtests passed in 132.72s`.
  ⚠️ 그 132.72s 는 이 기계의 load average 6.35 구간에 «걸쳐» 잰 값이다. 시간은
  판정 대상이 아니고 **실패 이름 집합**이 판정 대상이다.
* **새 실패를 기준선에 추가하지 않는다.** `Debt-Accepted-By` 는 비어 있고, 비어
  있는 것이 옳다.
* `mypy -p fcc_test_platform` = `0 / 215 files` 를 유지한다. 새 파일 둘은
  `tests/` 아래라 **이 게이트의 대상이 아니다**(`mypy.ini` 가 그 범위를 명시한다).
  즉 215 는 «안 움직이는 것»이 옳다 — 움직이면 내가 뭔가를 잘못 놓은 것이다.
* 커밋 메시지 17항목 자가점검(`githooks/commit-msg`)이 붙는다.
* `githooks/pre-commit` 축 3 — 승인 아티팩트(`plan.md`)와 코드를 **한 커밋에 섞지
  않는다.** plan 은 feature 브랜치에 «먼저» 따로 올린다.

## 4. 승인 지점 (사람이 눌러야 하는 것)

**없음.** 태그 push 없음 · 컨테이너 재기동 없음 · DB 마이그레이션 없음 · 배포 없음 ·
커널 태그 발행 없음.

⚠️ 다만 **기계 공유 축**이 하나 있다: 형제 세션 `fcc-delivery-final-8c` 가 모노레포
bench 레인을 재고 있고, p95 는 순서통계량이라 선점 몇 번으로 오염된다. 그쪽이
「bench 창 닫음」을 줄 때까지 **pytest·mypy·lane_check·pip install 을 새로 시작하지
않는다.** 이것은 승인 지점이 아니라 조율 지점이다.

## 5. 되돌리는 법

`git revert` 하나로 되돌아간다. 되돌릴 수 없는 것이 없다:

* DB 스키마 변경 없음 · 마이그레이션 없음.
* 태그 발행 없음 — 되돌리려고 태그를 옮길 일이 없다.
* 공개 API 축소 없음 — `register_session` 의 새 인자는 **기본값이 있는 키워드**라
  기존 호출 형태가 전부 그대로 돈다(오늘 호출부는 0건이지만, 그것이 이유는 아니다).
* 되돌리면 대역은 다시 target 을 무시한다. 그것이 **되돌림의 비용**이고,
  새 검사가 그 순간 red 로 그 사실을 말한다.

## 6. 범위 밖으로 새는 것을 막는 선언

작업 중 발견될 수 있지만 **이 계획에서 하지 않을 것**. 발견되면 새 intent 로 올린다.

1. **chamber 정규화 갈라짐** — 실물 `normalize_chamber_id(None) → '__fcc_legacy__'`,
   대역 `str(None or '') → ''`. 이미 재서 이름을 붙였다. 같은 메서드 안에 있어서
   「한 번에 고치는 게 싸다」는 유혹이 있지만, 두 축을 함께 움직이면 결과의 원인을
   말할 수 없다.
2. **`register_session` 삭제** — 호출부 0건이라는 사실은 삭제의 근거가 될 수 있지만
   공개 표면 축소는 별도 판정이다.
3. **실물의 캐시를 지우는 것** — 실증상 관측 불가한 최적화이므로 지워도 답이 같다.
   그러나 「지워도 되는 것」과 「지워야 하는 것」은 다른 명제다.
4. **`tests/` 를 mypy 게이트 안으로 넣는 것** — `mypy.ini` 가 「재지 않았다」고 명시적
   으로 적어 둔 자리다. 이 판이 `tests/` 에 파일을 더한다는 이유로 건드리지 않는다.
5. **다른 시험용 대역들** — 같은 형태(포트 계약을 대역이 못 지킴)가 다른 포트에도
   있을 수 있다. 세지 않았고, 세지 않았다고 적는다.

**Scope-Extended-By**: (없음)
