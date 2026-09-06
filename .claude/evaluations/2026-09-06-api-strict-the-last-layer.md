# `api` strict — 마지막 층

**날짜**: 2026-09-06 · **PR**: `feat/api-strict-the-last-layer-20260906`
**선행 판정 보고**: `.claude/evaluations/2026-09-06-what-the-api-133-are.md`
**work-claim**: `.claude/work-claims/api-strict-the-last-layer-20260906.json`

## Why

`api` 는 `fcc_test_platform` 에서 strict 가 아닌 마지막 층이었다. `mypy.ini` 의
「아직 아니다」에는 사유가 하나 적혀 있었다 — 133건이 `platform_routes.py` 단일
파일에 몰려 있어 strict 를 켜는 것이 곧 「그 파일을 분해할지」(설계서 §9)를
선점하는 것처럼 보인다는 것.

**그 둘은 독립이었다.** 이 웨이브는 파일을 분해하지 않고, contract-as-data
(`PLATFORM_API_OPERATIONS` + 동적 등록)를 건드리지 않고, 133 → 0 을 냈다. 선언을
붙이는 일과 파일을 가르는 일은 다른 질문이었고, 전자가 후자를 선점하지 않는다.

## What

두 커밋이다. 순서는 `application` 웨이브(44 → 145)와 같다 — **성격을 먼저 가른다.**

### ① 실제 타입 오류 22건 (`002f0d5`)

겉모습은 대부분 같았다: `payload.get('x')`(=`Any | None`)가 서비스의 `str` 선언과
안 맞는다. 처분은 셋으로 갈렸고, **가른 기준은 하류 정규화기다.**

| 하류의 철자 | 처분 | 건수 |
|---|---|---|
| `'' if v is None else str(v).strip()` 뒤 빈값 raise | 라우트에서 `_body_text` 로 접는다 | 12 |
| `str(v or '')` | 라우트에서 `or ''` — 같은 `or` 의미 | 2 |
| 커널 `Any` 검증기(메시지가 값을 이름으로 댄다) | **서비스 선언이 거짓말** — `object` 로 넓힌다 | 3 |

셋째 줄이 요점이다. `normalize_status(value: Any)` 는 「무엇이든 받아 **특정
메시지로** 거절한다」가 계약 전체이므로, 라우트가 `None` 을 `''` 로 접으면
`unsupported sample status: None` 이 `... ''` 로 바뀐다. 거기서는 라우트가 아니라
`CentralSampleInventoryService.change_status(status: str, expected_version: int)` 가
거짓말이었다 — 자기 첫 두 문장이 그 값을 `Any` 검증기에 그대로 넘기면서.

⚠️ `str(v)` 와 `v or ''` 는 **다르다**(falsy 비-문자열에서 갈린다). 「비슷한
정규화」를 새로 쓰지 않고 하류의 철자를 그대로 베낀 이유다.

나머지 넷: `_require_claim_write_service` 부재(형제 아홉은 다 있었다) · 조건부
`**kwargs` · `parts: list[str]` · 아래 🔴.

### ② 선언 111건 (이 커밋)

80건은 AST 로 **추론**했다 — 본문의 `return` 식이 부르는 어댑터/도우미의 선언된
반환형을 읽어 붙였다. 추측이 아니다: 읽을 수 없는 것은 `MANUAL` 로 남겼고, 틀린
추론은 mypy 가 본문에서 잡는다(**적용 후 새 오류 0건** — 80건 전부 맞았다).
나머지 31건은 하나씩 봤다.

## How — 실행이 선행 보고를 두 군데 확장했다

선행 보고는 「133 = 순수 선언 111 + 실제 오류 22」라고 적었다. 그 측정은 **그
시점의 참**이었지 최종값이 아니었다.

### 🔴 ⓐ 선언은 층을 따라 전파된다

`_require_*_service` 아홉의 **반환**을 선언하자 그 뒤의 서비스 호출이 `Any` 를
벗었고, **없던 실제 오류가 새로 드러났다.** 그 파급이 찾아낸 것:

- `Mapping` 을 `dict` 라 부른 자리 **여덟** (어댑터가 서비스보다 좁게 선언).
- 🔴 `dict` 를 `list` 라 부른 자리 **하나** — `list_test_equipment_lists`. 라우트
  주석까지 「Plain array of the project's equipment lists」라고 적혀 있었는데
  `CentralTestEquipmentListService.list_lists` 는 처음부터
  `{'lists': [...], 'test_items': [...]}` 를 돌려준다. **응답은 객체였고 선언과
  주석만 틀려 있었다.**
- ①과 같은 종류의 `arg-type` 이 다섯 더 (`plan_id`·`reason`·`test_item_key`·
  `test_report_id`·`equipment_config`) + 결과 선택 서비스 셋. 같은 기준으로 갈랐다.

**교훈**: 「N 건이 순수 선언이다」는 그 선언을 붙이기 **전**의 측정이다. 다음 층에서
이 수를 예산으로 쓰지 마라.

### 🔴 ⓑ 측정 리그가 답을 바꾼다 — 21 대 60

같은 트리·같은 mypy 판(2.3.1)에서:

| 리그 | 남은 오류 |
|---|---|
| mypy 단독 설치(fastapi 없음) | **21** |
| CI 와 같은 설치 `pip install -e '.[test]'` | **60** |

fastapi 가 없으면 `ignore_missing_imports` 가 `Request` 를 `Any` 로 만들고, 그러면
`_normalize_request_body_args` 의 파급(39개 핸들러의 `request, body = ...` 재바인딩)이
**아예 보이지 않는다.** 선행 보고의 133 은 fastapi 없는 리그에서 잰 값이므로 그
수치 자체도 최종 예산이 아니었다.

⚠️ 이 게이트를 재는 리그는 반드시 `pip install -e '.[test]'` 여야 한다.
관련: `local-red-is-tree-times-environment`.

### 🔴 ⓒ 포트가 실제 계약보다 좁다 (드러내기만 했다)

`ChamberProgressBroadcastPort` 는 docstring 에서 스스로 「publish-only」라 선언하는데,
진행 WS 릴레이는 그 타입으로 받은 객체에 `.subscribe()` 를 부른다. 조립 루트가
배선하는 것은 언제나 구상 `ChamberProgressBroadcaster` 이고, 그 클래스 docstring 도
「포트를 구조적으로 충족(publish 표면) **+** WS 핸들러가 직접 소비할 async
subscribe」라고 스스로 적는다. **선언된 타입이 처음부터 좁았다.**

포트를 넓히는 것은 포트 주인의 판정이므로 이 웨이브는 api 쪽에서 좁혔다
(`_SubscribableProgressBroadcaster` Protocol + `cast`). 구조적 Protocol 이라 `api` 는
여전히 `infrastructure` 를 **한 줄도** import 하지 않는다.

⚠️ `cast` 는 아무것도 검사하지 않는다 — 검사 없는 Protocol 은 주석이다. 그래서
`tests/test_chamber_heartbeat_progress_broadcast.py` 에 봉인을 붙였고, `isinstance` 로
메서드 존재만 보지 않고 **라우트가 쓰는 철자**를 그대로 구동한다.

### `Any` 를 한 자리에 가뒀다

`_normalize_request_body_args` 의 첫 반환 원소만 `Any` 다. 사유: 39개 핸들러의
`request: Request` 는 **FastAPI 의 와이어 선언**이지 파이썬 보증이 아니고, 이 도우미는
직접 호출(시험) 경로가 그 선언을 일부러 어기기 때문에 존재한다. 그 사실을 기록하는
자리가 여기 하나다.

## Verification

- 리그: 전용 venv + `pip install -e '.[test]'`(CI `checks.yml:90` 과 같은 줄),
  cwd = 트리 루트. 선언대로 설치 — contracts 0.1.22 · kernel 0.5.0.
- **`mypy -p fcc_test_platform.api` → `Success: no issues found`.**
- 전량 시험 **3277 passed · 27 skipped · 0 failed** (완주 115초). 선언된
  `delivered_test_run_baseline.json` 의 「알려진 실패 0」과 일치.
- 게이트 이빨 확인(주입 → red, 복원 → green, 복원 후 소스 바이트 동일,
  `__pycache__` 제거 포함): `api` 축을 `STRICT_SECTIONS` 에 더한 뒤 未선언 함수를
  넣으면 `SUBFAILED(package='fcc_test_platform.api')` 로 정확히 그 축만 빨개진다.
- 릴레이 read 축 봉인 이빨 확인: `_SubscriptionScope.__aenter__` 제거 → red.

## 후속

- **`STRICT_SECTIONS` 는 이제 네 줄이고 `fcc_test_platform` 전량이 strict 다.**
  범위 **밖**으로 남는 것(`tests/`, `apps/web/scripts/`)을 `mypy.ini` 에 이름으로
  적었다 — 적지 않으면 다음 사람이 「저장소 전량 strict」로 읽는다.
- 🔴 `ChamberProgressBroadcastPort` 의 폭은 **미해결**이다. 포트를 넓힐지, 읽기
  포트를 따로 선언할지, 어댑터가 두 능력을 별도 인자로 받을지 — 포트 주인의 판정이다.
- 설계서 §9(분해 여부)는 이 웨이브가 **답하지 않았다.** 답하지 않고도 strict 가
  켜진다는 것이 이 웨이브의 결과이고, 그만큼 §9 는 이제 순수한 설계 질문이다.
