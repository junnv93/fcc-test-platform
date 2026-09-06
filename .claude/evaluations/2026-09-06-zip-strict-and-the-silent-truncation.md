# `zip()` 이 조용히 자르는 축 — B905 53건의 실측 (2026-09-06)

기준 `445bb62`(PR #97 병합 후) · ruff 0.16.6 격리 venv · 선언대로 설치
(contracts v0.1.22 · kernel v0.5.0) · `QT_QPA_PLATFORM=offscreen`

## 무엇을 쟀나

`ruff --isolated --select F,B` 가 146건이고 그중 **B905 53건**이 최대 단일 축이다.
정체는 대부분 `dict(zip(COLUMNS, row))` — 컬럼 목록과 행 튜플의 길이가 어긋나면
zip 이 짧은 쪽에 맞춰 **조용히 자른다**. 그 필드는 dict 에서 사라지고 예외는 0건이다.

⚠️ 이것이 가설이 아니라는 증거는 코드 안에 있었다.
`central_project_read_adapter.py` 68줄: *"이 두 가지는 **위치로 대응**해야 한다"*,
106줄: *"예전에는 SELECT 에만 있고 출력 튜플에 없어서 `dict(zip(...))` 가 버리고 있었다"*.
**그 사고는 이미 일어났고 주석으로만 막혀 있었다.**

## 실측 1 — 53건은 한 종류가 아니다. 세기 전에 갈랐다

| 부류 | 건수 | 처분 |
|---|---:|---|
| DB 어댑터 `dict(zip(COLUMNS, row))` | 46 | `strict=True` |
| 계약 파생점 `tuple(zip(FIELDS, SELECT_ITEMS))` | 2 | `strict=True` |
| 길이가 **이미 가드된** 자리 | 2 | `strict=True` (가드가 바로 위) |
| 두 계약 접근자 대조 | 1 | `strict=True` (파생점이 갈라져 있어 더 실질적) |
| 파싱 결과 둘 — **색인이 어긋날 수 있었다** | 1 | 필터를 맞춰 정렬 수리 + `strict=True` |
| **자름이 진단의 부품인 자리** | 1 | **`strict=False`** + 사유 |

⚠️ **B905 는 `strict=True` 를 요구하지 않는다. «명시적 선택»을 요구한다.** 길이가
같아야 하는 곳은 `True`, 잘라내는 것이 의도인 곳은 `False` + 사유 — 둘 다 규칙을
만족시키고, 후자를 `True` 로 밀면 멀쩡한 설계를 깨뜨린다.

⚠️ 그리고 **ruff 의 자동수정을 쓰지 않았다.** B905 의 (unsafe) 자동수정은
`strict=False` 를 넣는다 — 동작 보존이 목적이라 **린터만 초록으로 만들고 조용한 자름은
그대로 둔다.** 53건 전부에 그것을 돌렸다면 이 문서의 실측 2·3·4 는 하나도 나오지 않았다.

## 실측 2 — 계약 파생점: `strict=True` 는 공허하지 않다 (주입으로 갈랐다)

`_META_SELECT_ITEMS` 는 `EDITABLE_PROJECT_META_FIELDS` 를 순회해 만들어지므로 **오늘은**
길이가 구조적으로 같다. 그래서 「붙였는데 아무것도 안 잡는 것 아닌가」를 먼저 물었다.

주입: 그 파생 생성기를 `EDITABLE_PROJECT_META_FIELDS[:-1]` 로 한 칸 짧게 만든다
(착지 확인: 86줄).

    strict=True  → import 시점에 ValueError: zip() argument 2 is shorter than argument 1
    strict 없음  → PROJECT_LIST_COLUMNS 가 13 → 12 로 «조용히» 줄고 예외 0건
                   사라진 필드: test_standard
                   PROJECT_DETAIL_COLUMNS 도 12 → 11

즉 없었다면 **프로젝트 목록 API 에서 시험규격이 사라진 채 아무 데서도 안 터진다.**
공허하지 않다.

## 실측 3 — 붙이자마자 «오늘 실제로 어긋나는» 자리 둘이 빨개졌다

전량 실행이 선언에 없는 실패 2건을 냈다. 둘 다 진짜였고 성격이 반대다.

### (가) 시험 대역이 낡아 있었다 — 그리고 그 낡음이 값을 옮기고 있었다

`tests/test_platform_project_entry.py` 의 `detail_row` 는 **13개 값**인데
`PROJECT_DETAIL_COLUMNS` 는 **12개**다. `dict(zip(...))` 가 남는 하나를 버리면서
한 칸씩 밀려 있었다:

    manufacturer ← 'active'            (원래 status 값)
    status       ← None
    created_at   ← None                ('2026-06-23T00:00:00Z' 는 버려짐)

이 시험이 그 필드들을 단언하지 않아 **초록이었다.** 어댑터 106줄 주석이 경고한 형태가
**시험 대역 안에 살아 있었다.**

처분: 대역 행을 `PROJECT_DETAIL_COLUMNS` 에서 파생시킨다(`_DETAIL_VALUES` dict →
튜플). 커널이 필드를 더하거나 빼도 대역이 함께 움직이므로 어긋날 방법이 없다.
같은 파일의 다른 대역(samples 14 · intakes 11)은 실측 결과 이미 일치했다.

### (나) 자름이 진단의 «부품»인 자리가 하나 있었다

`central_result_selection_adapter.py` 의 `_fetch_rows` 는 짧은 행이 오면 zip 이 잘라
**키가 모자란 dict** 를 만들고, `read_selected_source` 가 그 키 집합을 보고
*"selected source row does not satisfy the full event-attempt-session shape"* 라는
도메인 메시지로 거절한다. `strict=True` 는 그 가드보다 **먼저** 터져 호출자에게
*"central selection read failed: zip() argument 2 is shorter than argument 1"* 만 남긴다.

`test_selected_source_rejects_legacy_four_column_event_only_rows` 가 그 퇴화를 잡았다.
처분: 이 한 자리만 `strict=False` + 사유. 자름은 사고가 아니라 설계다.

## 실측 4 — 잘리는 것이 아니라 «어긋나는» 자리 하나

`db_migration_collect_cli.py` 의 `_index_columns` 는 빈 항목을 건너뛰는데
(`if not column: continue`) `_index_orders` 의 `keys` 는 건너뛰지 않는다. 빈 항목이
중간에 있으면 두 목록의 **색인이 밀려** `orders` 가 엉뚱한 열에 붙는다 — 잘리는 것이
아니라 **틀린 값이 실린다**.

⚠️ 오늘 `pg_indexes.indexdef` 는 빈 항목을 내지 않으므로 도달하지 않는다. 그래서
`strict=False` 로 덮으면 그 어긋남이 «의도»로 기록된다. 필터를 같게 맞춰 두 목록이
구조적으로 정렬되게 하고 `strict=True` 를 붙였다.

## 수 회계 (같은 rig, 전후)

| | 전 | 후 |
|---|---:|---:|
| `lane_check` exit | 0 | 0 |
| passed | 3,138 | 3,138 |
| skipped | 27 | 27 |
| subtests | 828 | 828 |
| ruff B905 | 53 | **0** |

시험을 잃지도, `skipTest` 로 도망가지도 않았다.

## 겹침

열린 PR 8건(#90 #92 #95 #99 #100 #102 #103 #104)의 파일과 교차한 결과 겹치는 파일은
`fcc_test_platform/bench_project_result_selection_cli.py` **하나**(PR #90)뿐이고,
그 PR 의 hunk 는 23·724줄, 이 웨이브의 대상은 **643줄**이라 겹치지 않는다.

## 근거

- `ruff --isolated --select B905 --no-cache` 로 53건을 좌표까지 열거한 뒤,
  각 자리를 **AST 로** 찾아 닫는 괄호 앞에 삽입했다(문자열 치환 아님 —
  같은 줄에 zip 이 둘이거나 호출이 여러 줄이어도 어긋나지 않는다).
- 주입은 착지를 좌표로 확인한 뒤 측정하고, 복원 뒤 `git diff --stat` 0줄을 확인했다.

---

# ② 진짜 버그 계열 넷 — 11건 (2026-09-06)

같은 ①단계(F,B)의 나머지 중 「자동수정 불가 + 버그 냄새」인 넷을 처분했다. 네 계열이
성격이 전부 다르고, **그중 하나는 가설이 반증됐다.**

## B023 2건 — 가설이 반증됐다 (거짓 양성이었다)

`tests/test_frontend_i18n_parity.py` 의 `record` 클로저가 루프 변수 `rel` 을 잡는다.
착수 전 가설은 *"검사가 «마지막 항목만» 보고 있을 수 있다"* 였다.

**주입으로 반증했다.** 서로 다른 `.tsx` 두 개(`app.tsx` · `auth/failure-ui.tsx`)에
영어 리터럴을 넣고 돌렸다:

    보고된 파일: ['app.tsx', 'auth/failure-ui.tsx']
    두 파일이 «각자» 경로로 보고되나 → True

즉 늦은 바인딩 사고는 **일어나지 않고 있었다.** `record` 는 정의된 그 반복 안에서만
동기 호출되므로 호출 시점의 `rel` 이 언제나 옳다.

그래도 고쳤다 — 다만 **버그 수리가 아니라 구조적 경화**로 적는다. 오늘의 안전은
「호출 지점이 반복 밖으로 나가지 않는다」에 얹혀 있고, 누가 이 함수를 목록에 모았다가
뒤에서 부르면 그날 전부 마지막 파일의 `rel` 을 쓴다. 기본 인자 바인딩이 그 가능성을
없앤다.

⚠️ **「고치면 red 가 난다」를 기대했다가 안 났을 때, 그것을 「고쳤으니 됐다」로 읽지
마라.** red 가 안 난 이유가 「원래 안 깨져 있었다」인지 「검사가 못 본다」인지는
주입으로만 갈린다.

## B017 2건 — 「무엇이든 예외면 통과」

`tests/test_token_revocation_custody.py` 의 `assertRaises(Exception)` 둘. 그 형태는
오타로 인한 `AttributeError` 도, 대역이 던지는 `KeyError` 도 통과로 센다.

실측: 두 자리 다 `InvalidCredentialsError('invalid refresh token')` 을 던진다. 타입과
메시지를 함께 단언하도록 좁혔다.

## F841 5건 — 「왜 쟀는지」를 물은 결과 다섯 다 중복이었다

| 자리 | 왜 안 쓰였나 |
|---|---|
| `test_central_db_migration_runner.py` `migs` ×3 | 바로 아래 `self._run(dp, …)` 이 `reconcile(migrations_dir=tmp)` 을 부르고 **그 안에서 같은 발견을 한다.** 바깥 호출은 순수 함수라 부작용도 없다 |
| `test_frontend_architecture_conformance.py` `app_src` | `_app_route_modules()` 가 자기 안에서 `_app_route_source()` 를 부른다(4581줄) |
| `test_reference_coupled_publish.py` `caught` | 메시지 단언은 **형제 시험**(198·253줄)이 한다. 이 자리는 거절 «여부»만 묻는다 |

⚠️ 같은 파일의 다른 `as caught` 둘은 실제로 `caught.exception` 을 읽으므로 건드리지
않았다 — 이름이 같다고 처분이 같지 않다.

## B034 2건 — `re.split(..., 1)` 의 위치 인자

`maxsplit=1` 로 이름을 붙였다. 위치 인자는 파이썬 3.13 에서 폐기 예고됐고, 무엇보다
`1` 이 `maxsplit` 인지 `flags` 인지 읽는 사람이 세어 봐야 한다.

## 수치

`ruff --isolated --select B905,B023,B017,B034,F841` **0건**.
lane_check exit 0, 3,138 passed / 27 skipped / 828 subtests — ① 이후와 동일.
