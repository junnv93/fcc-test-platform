# `api/` 133건이 무엇인가 — 설계서 §9 미해결에 근거를 준다

**날짜**: 2026-09-06 · **산출**: 판정 보고 · **코드 수정 0 · `mypy.ini` 수정 0 · strict 미점화**
**요청**: `fcc-delivery-final-bf` 배분 · **충돌면 확인**: `fcc-delivery-final-5c` (mypy.ini 무충돌 회신)

## 측정 범위 — 이것을 먼저 적는 이유

설계서의 ruff 수치가 범위 미상이라 실측과 2배 어긋난 전례(1,665 vs 3,292)가 있다.
그래서 모든 수 앞에 범위를 못 박는다.

```
mypy 2.3.1 (compiled) · 격리 venv (mypy 단독 설치)
설정: python_version=3.11 · ignore_missing_imports=True · follow_imports=silent
      disallow_untyped_defs=False  +  [mypy-fcc_test_platform.api.*] disallow_untyped_defs=True
명령: MYPYPATH="$PWD" mypy --config-file <위 ini> -p fcc_test_platform.api
```

⚠️ `mypy.ini` 는 **건드리지 않았다.** 위 설정은 별도 파일이고, 그것이 이 웨이브의
제약(「strict 를 켜면 판정을 선점한다」)을 지키면서 같은 수를 얻는 방법이다.

## ⓪ 133 은 재현된다 — 그리고 **53 커밋 동안 한 건도 안 움직였다**

배분자는 「지금은 재현되지 않을 가능성이 높다」고 예측했다. 근거도 합리적이었다 —
`#109`(infrastructure strict) · `#112`(포트 `CentralChamberWritePort` 2→7) ·
`#115`(write 어댑터 11모듈, 트랜잭션 러너 시그니처 23건)가 전부 api 가 소비하는
타입을 건드렸다. **반증됐다.**

| 트리 | 결과 |
|---|---|
| `522d1ec` (그 수를 낸 자리) | `Found 133 errors in 1 file (checked 2 source files)` |
| `06e3e92` (현재 `origin/main`) | `Found 133 errors in 1 file (checked 2 source files)` |

⚠️ **개수 일치는 집합 일치가 아니므로 이름 집합으로 갈랐다.** 메시지 집합이 완전
일치하고 **줄번호까지 동일**하다. 실제로 `platform_routes.py` 는 두 커밋 사이 53개
커밋 동안 **한 글자도 안 움직였다**(`git diff --stat` 공집합).

**해석**: `follow_imports = silent` 라도 상류 시그니처 개선이 이 파일의 오류를 못
줄인다. 아래 ①이 이유를 말한다 — 133 중 111 이 **이 파일 자신의 선언 부재**이고,
그것은 상류 타입과 무관하다.

## ① 오류 코드별 분포 — 선언 111 : 실제 타입 22

```
111  [no-untyped-def]      ← 97 반환형 없음 · 10 파라미터 없음 · 4 둘 다
 18  [arg-type]
  2  [union-attr]
  1  [var-annotated]
  1  [attr-defined]
```

| 층 | 선언 누락 : 실제 타입 | 비 |
|---|---|---|
| infrastructure | 17 : 3 | 5.7 |
| application | 145 : 44 | 3.3 |
| **api** | **111 : 22** | **5.0** |

api 의 비율은 infrastructure 에 가깝다 — **선언을 다는 일이 압도적이고 논리 결함은
적다.** application 웨이브가 44건을 먼저 처분하고 145건을 남긴 것과 같은 모양이다.

## ② 어느 구조에 몰려 있는가 (AST 판정, grep 아님)

```
 92  create_platform_router  (최상위 함수 하나)
 33  PlatformApiAdapter      (클래스 하나)
  8  나머지 소형 헬퍼 6개
```

**중첩 깊이**: 133 중 **122 가 깊이 2** — 즉 중첩 함수다. 그중 88 이
`create_platform_router` 바로 안에 있다.

⚠️ **그 88 은 데코레이터로 등록된 핸들러가 «아니다».** 오류가 난 def 133개 중 125개가
**데코레이터가 없다**(`router.get` 3 · `router.websocket` 1 · `app.middleware` 1 ·
`staticmethod` 2 · `wraps` 1 이 전부).

배선을 AST 로 확인했다. 라우트 등록 호출은 **셋뿐**이다:

```python
# platform_routes.py:3213
for name, handler in route_handlers.items():
    method, path = PLATFORM_API_ROUTES[name]
    router.add_api_route(path, route_error_boundary(handler), methods=[method])
```

**88개 중첩 def 를 지역 `route_handlers` dict 에 모아 루프 하나가 등록한다.**
즉 질문의 셋 중 답은 **동적 라우트 등록부**이고, 더 정확히는 *「그 등록부가 품는
핸들러 본문들」* 이다.

**§9 에 기여하는 사실**: 이 분포는 「분해하면 무엇이 줄어드는가」를 말한다 —
분해해도 **오류 수는 안 준다**(선언은 어느 파일에 있든 달아야 한다). 분해가 바꾸는
것은 **한 함수가 88개 중첩 def 를 품는 구조**이고, 그것은 타입 문제가 아니라 구조
문제다. ⚠️ 그러므로 **133 은 「분해하라」의 논거가 될 수 없다.** 논거가 되려면
「이 구조 때문에 선언을 달 수 없다」가 참이어야 하는데, 실측은 그 반대를 가리킨다:
깊이 2의 평범한 중첩 함수 122개이고 각각에 반환형을 다는 것을 막는 것이 없다.

## ③ §8 의 `OperationSpec(TypedDict)` — 제안은 맞았고, **위치가 다르다**

### ⚠️ 발견 1 — 그 dict 는 이 레포에 없다

설계서는 `OPERATIONS` 라고 적었다. 그 이름의 dict 는 `fcc_test_platform/` 어디에도
없다. 실체는 **`PLATFORM_API_OPERATIONS`** 이고, 정의처는:

```
fcc_test_kernel.application.central_contract.api_contracts    ← 다른 레포
platform 은 kernel-v0.5.0 핀으로 받는다 (pyproject.toml:127)
```

**그러므로 §8 의 제안은 이 레포 안의 주석 작업이 아니라 «2-레포 + 커널 태그» 작업이다.**
실현 가능성 판정이 통째로 달라지는 사실이고, 설계서가 이것을 반영하지 않았다.

### 발견 2 — 제안한 다섯 필드는 전부 실재한다. 하나가 빠져 있다

`PLATFORM_API_OPERATIONS` 는 **80 operation**이고 키 집합은:

| 키 | 등장 | 제안에 있나 |
|---|---:|---|
| `request` | 80/80 | ✅ |
| `response` | 80/80 | ✅ |
| `permission` | 80/80 | ✅ |
| `error_responses` | 64/80 | ✅ |
| `allowed_during_password_change` | 3/80 | ✅ |
| **`response_media_type`** | **1/80** | ❌ **설계서에 없다** |

셋은 `total=True`(80/80), 셋은 `total=False`. 설계서가 놓친 `response_media_type` 은
`api_schema.py:682` 가 실제로 읽는다 — TypedDict 를 제안대로 정의하면 **그 소비가
`typeddict-item` 으로 빨개진다.**

### 발견 3 — `Literal[True]` 전제는 **참이다** (실측)

`allowed_during_password_change` 는 나타나는 3개 operation 전부에서 값이 `True` 다
(값 집합 `{True}`). 즉 *「키가 있으면 반드시 True」* 를 `Literal[True]` 로 못박는
설계서의 안은 오늘 데이터에 대해 성립한다. 탐침으로 확인:

```
error: Incompatible types (expression has type "Literal[False]",
       TypedDict item "allowed_during_password_change" has type "Literal[True]")  [typeddict-item]
```

## ④ 두 소비 지점 — **한쪽은 전부 새고, 다른 쪽은 절반만 검사된다**

### 먼저 기제를 격리해서 쟀다

mypy 는 TypedDict 에 대해 **첨자는 검사하고 `.get()` 은 검사하지 않는다.**

```python
OPS[name]['permissionn']        # ✅ error: TypedDict "Spec" has no key "permissionn"
OPS[name].get('permissionn')    # ❌ 조용하다
(OPS.get(name) or {}).get('x')  # ❌ 조용하다
```

⚠️ `--strict` 로도 같다(측정). 그리고 `or {}` 는 **타입을 넓히지 않는다** —
`reveal_type` 이 `Spec` 을 낸다(`total=False` 라 빈 dict 가 적법한 `Spec` 이다).
즉 새는 축은 `or {}` 가 아니라 **`.get` 하나**다.

### 실제 소비의 접근 형태 (AST census, `06e3e92`)

| 소비 | 접근 | 검사되나 |
|---|---|---|
| ① 런타임 인가 `platform_routes.py:548-549` | `PLATFORM_API_OPERATIONS.get(op) or {}` → `contract.get('permission')` | ❌ **0/1** |
| ② OpenAPI `api_schema.py:753,795,946` | `PLATFORM_API_OPERATIONS[name]['permission']` | ✅ **3/3** |
| ② OpenAPI `api_schema.py:674,682,710` | `operation.get('response' / 'response_media_type' / 'request')` | ❌ **0/3** |

**답**: 한쪽이 Any 로 새는 것이 아니라 — 타입은 `Spec` 으로 정확히 좁혀진다 —
**`.get` 접근이 검사 밖이다.** 그리고 **런타임 인가 소비는 접근이 `.get` 하나뿐이라
전부 검사 밖**이다. OpenAPI 소비는 3/6 만 검사된다.

⚠️ 이것이 §8 의 *「검사기 없는 TypedDict 는 주석과 같은 효력이다」* 의 **두 번째
얼굴**이다. 검사기를 켜도, 소비가 `.get` 이면 여전히 주석이다.

## 판정에 필요한 사실 중 «아직 없는 것»

이 웨이브는 결론을 내지 않는다. 남은 것을 이름으로 댄다.

1. **`.get` → 첨자 전환의 비용이 안 재졌다.** 위 4건(`548` · `674` · `682` · `710`)을
   첨자로 바꾸면 검사가 6/6 이 되지만, `.get(키, 기본값)` 형태 둘은 기본값 처리를
   손으로 써야 한다. 그 diff 크기가 「TypedDict 가 값을 하는가」의 실제 가격이다.
2. **커널 쪽 착지 경로가 안 정해졌다.** `OperationSpec` 이 커널에 들어가면
   `fcc-test-contracts` 태그가 앞서고 platform 핀이 따라와야 한다. 그 순서와 승인
   지점(태그 push)이 §8 에 없다.
3. **「분해하라」의 논거는 여기서 나오지 않는다.** 133 은 선언 부채이지 구조 부채가
   아니다. 분해 판정은 **다른 축**(한 함수가 88개 중첩 def 를 품는 것이 읽기·변경·
   충돌에 무엇을 하는가)에서 나와야 하고, 그 축은 이 웨이브가 재지 않았다.
4. **api strict 를 켤 때의 실제 비용이 111 이 아닐 수 있다.** 선언을 달면 그 시그니처가
   상류와 대조되기 시작하므로 `arg-type` 류가 **늘 수 있다.** application 웨이브가
   44 → 0 을 먼저 처분하고 145 를 남긴 순서가 그 위험을 이미 보여 준다. 실측하려면
   샘플 몇 개에 선언을 달아 보는 별도 웨이브가 필요하다.

## 이 보고가 쓴 수의 재현법

```
git worktree add --detach <path> 06e3e92
python3 -m venv rig && rig/bin/pip install 'mypy>=2.3.1'
# 위 §측정 범위 의 ini 를 쓰고
MYPYPATH="$PWD" rig/bin/mypy --config-file <ini> -p fcc_test_platform.api
```

키 census 는 `kernel-v0.5.0` 을 격리 venv 에 설치해 `PLATFORM_API_OPERATIONS` 를 직접
셌다(`fcc-test-kernel 0.5.0`). 구조 판정은 전부 `ast` 이고 grep 이 아니다.
