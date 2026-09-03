# ADR-0001 — 공유 커널의 접두사와 최상위 이름 소유권

Status: Accepted — **1·2단계 완료, 3단계 실측 완료·실행 대기** (2026-09-03)
Date: 2026-09-03

> 이 저장소의 첫 ADR 이다. 앞선 결정들은 provider 저장소의 `docs/adr/` 에 있고,
> 레인 분리 이후 **이 레인이 단독으로 지는 결정**이 생겼으므로 여기서 번호를 다시 시작한다.

## 문제

두 배포 단위가 같은 최상위 import 이름 **넷**을 주장한다:
`domain` · `application` · `infrastructure` · `logger_config`.

Python 은 이 상황에서 오류를 내지 않는다. `sys.path` 순서로 한쪽을 고르고, 그 답이
**기계마다 다르다**(실측 2026-09-03):

| | `domain` · `application` · `infrastructure` · `logger_config` |
|---|---|
| 챔버 PC (`PYTHONPATH=src`) | provider 저장소의 `src/` 가 이긴다 |
| 중앙 PC | 설치된 휠이 이긴다 |

그 상태에서 실제로 갈라졌다. `domain/` 의 양쪽 공통 87파일 중 **4개의 내용이 달랐고**
둘은 기능 차이였다. **같은 import 이름으로 서로 다른 코드가 돌았고, 그것을 보고하는
검사가 0건이었다.** import 는 성공하고 테스트도 통과한다 — 그저 다른 코드가 돈다.

## 공식 지침이 허용하는 것은 둘뿐이다

`packaging.python.org` — *Packaging namespace packages*:

1. **PEP 420 native namespace** — 요건이 절대적이다: *"그 namespace 를 쓰는 **모든**
   배포판이 `__init__.py` 를 생략해야 한다. 하나라도 그러지 않으면 namespace 논리가
   실패하고 다른 하위 패키지들이 import 불가가 된다."*
   ⚠️ **현재 성립하지 않는다** — `domain`·`infrastructure` 가 **양쪽 다**
   `__init__.py` 를 갖고, `application` 은 한쪽만 갖는 혼합 상태다.
2. **고유 접두사** — 같은 문서가 제시하는 「간단한 대안」. 충돌이 **구조적으로 불가능**해진다.

같은 사이트의 *src layout vs flat layout* 이 증상을 그대로 이름 붙인다 — *"인터프리터가
현재 디렉터리를 import 경로 첫 항목에 넣는다. 로컬 패키지가 설치된 패키지와 이름이
같으면 로컬이 쓰인다."* provider 저장소의 `PYTHONPATH=src` 가 정확히 그 형태다.

## 결정

**고유 접두사를 갖는 별도 배포판으로 공유 커널을 옮긴다.**
계약 레인(`fcc-test-contracts`)을 확장하지 않는다.

### 왜 계약 레인이 아닌가 — 파생된 이유

`repository-split.md` §Contracts Lane Dependency-Free 가 **provider 어휘의 계약 레인
승격을 금지**한다(ADR-0010 D-8). 실측 2026-09-03: 공유 폐포 53개 중 **9개가 코드에
provider 어휘를 진다**(docstring 제외 후. 포함하면 16이고, 그 차이가 이 판정을 뒤집는다).
그중 3개는 `domain/services/unlicensed/` 아래라 디렉터리 이름부터 provider 소유다.

즉 계약 레인 확장은 그 규칙을 정면으로 어긴다.

### 왜 「네 번째 배포판」의 옛 기각이 지금은 적용되지 않는가

2026-08-12 에 「계층을 레인으로 승격」이 기각됐고, 그때 적힌 비용은 매니페스트 축이었다 —
`lanes` 4→5 · `owners` 중복 · `cross_lane_import_baseline` 12키 재해석 · `packaging/`.
**실측 2026-09-03: 그 기전이 양쪽 저장소에서 전부 0이다.**

```
packaging/                     FCC 0 · platform 0
cross_lane_import_baseline     FCC 0 · platform 0
ExtractionLanePolicy           FCC 0 · platform 0
```

`repository-split.md` 자신이 그 사실을 적는다 — *"상자 모델 자체를 퇴역시켜 … 전부
사라졌다. **결함은 고쳐져서가 아니라 대상이 없어져서 닫혔다.**"* 그러므로 그 기각은
**퇴역한 모델에 묶인 결정**이고, 오늘의 pip-소비 세계에서는 그 비용이 존재하지 않는다.

### 왜 새 저장소가 필요 없는가

계약 저장소가 **이미 두 배포물을 낸다** — Python `fcc-test-contracts`(루트 `pyproject.toml`)와
npm `@fcc/api-artifacts`(`packages/api-artifacts/package.json`). 「한 저장소, 여러
배포판」이 이 계열의 확립된 패턴이므로 새 배포판은 `packages/` 아래에 선다.

### 그리고 9개를 분류할 필요가 없다

접두사 문제는 **이름**의 문제이지 **내용 소유권**의 문제가 아니다. 두 레인이 접두사가
붙은 하나의 이름으로 import 하면, 그 안의 코드가 의미상 누구 것인지와 무관하게 충돌이
사라진다. 그래서 이 결정은 «어휘 9개가 누구 것인가» 를 **묻지 않고** 내려진다.

⚠️ 그 9개의 분류를 시도했다가 기각한 파생이 하나 있다 — *중앙 DB 스키마가 그 어휘를
담는가*. 실측하니 토큰이 **산문(설명문)에만** 나오고 `coverage_technology` 는
`{"type": "text"}` 로 열거 제약이 없다. 즉 그 파생은 산문을 세는 것이었고,
같은 날 두 번 밟은 오류(산문을 어휘로 · 파일명을 경로로)와 같은 형태라 기각했다.

## 이동 단위는 파일이 아니라 폐포다

`repository-split.md` §Shared Kernel Delivery (2026-08-12): *"**정공은 목록이 아니라
폐포다.**"* 이 웨이브가 그것을 값비싸게 재확인했다 — 한 세션이 공유 커널을 「15개」로
보고했고 그것은 **그 세션이 복사한 집합**이었다. 폐포로 재니:

```
중앙만 도달    49   → platform 소유로 충분
provider만    291   → provider 소유로 충분
양쪽 도달      64   ← 진짜 공유 커널
```

서드파티 의존 **0**(형제 레인 의존 6건은 전부 `fcc_test_contracts` — first-party 다).

> ⚠️ **이 표는 2026-09-03 에 정정됐다. 처음 적힌 수는 47 · 282 · 53 이었다.**
> 폐포 워커가 `from <패키지> import <서브모듈>` 형식을 간선으로 세지 않아
> **11개를 놓쳤다** — `node.module`(= 패키지)만 담았고, 그 패키지의
> `__init__.py` 가 순수 docstring 이라 탐색이 거기서 멈췄다.
> 놓친 것: `central_contract` 의 표면 9개 + `api_operation_factory` +
> `domain/services/reference_entry_edit_policy.py`.
>
> **AST 축에서 「import 안 함」과 「서브모듈로 import 함」이 같은 값이었다** —
> 그리고 틀리는 방향이 나쁜 쪽이다. 이 문서의 완료 오라클이 *「공유 폐포 0」*
> 인데, 과소계수하는 워커는 **아직 공유 중인데도 0** 을 낸다.
>
> 정정이 코드 회귀가 아님의 증거: 그 커밋이 만진 파일은 워커와 그 봉인 둘뿐이고
> `application/`·`domain/`·`infrastructure/` 변경 **0건**이다. 새 11개는 전부
> 새로 도달된 표면에서 직접(9) 또는 추이적으로(2) 도달한다.
> 봉인: `tests/test_shared_kernel_closure.py::TestSubmoduleImportsAreEdgesToo`
> (과잉계수 방지 팔을 함께 갖는다 — 속성 import 를 모듈로 오인하면 red).

## 단계 — 각 단계가 독립으로 검증된다

이름별로 쪼갤 수 없다. `logger_config` 하나도 `domain`·`infrastructure` 를 끌어온다 —
**폐포가 단위다.** 그러나 폐포 안에 **닫힌 부분 클러스터**가 있고, 그것이 단계가 된다.

### 1단계 — 로깅/알림 클러스터 (10파일). 실행 준비 완료

`logger_config.py` 의 폐포가 정확히 이 10개에서 닫힌다:

```
logger_config.py
domain/models/log_event.py                      domain/models/notification_event.py
domain/ports/output/log_event_port.py           domain/ports/output/notification_event_port.py
infrastructure/adapters/driven/in_memory_log_bus.py
infrastructure/adapters/driven/in_memory_notification_bus.py
infrastructure/logging/json_formatter.py        infrastructure/logging/log_handler.py
infrastructure/logging/session_log_custody.py
```

실측한 적합성:

| 판정항 | 값 |
|---|---|
| 10개가 전부 공유 폐포 안인가 | **예 (10/10)** |
| provider 어휘 | **0** |
| 클러스터 밖 의존 | `fcc_test_contracts` 뿐 |
| platform 쪽 import 재작성 | **2파일 · 2건** |
| platform 테스트가 import 하나 | **아니오** (문자열 언급 1건뿐) |

완료 판정: platform 이 `logger_config` 를 최상위로 **선언하지 않는다**
(`check_import_name_ownership.py` 가 본다) · 공유 폐포가 64 → 54
(`check_shared_kernel_closure.py` 가 본다. ⚠️ 처음 적힌 53 → 43 은 위 워커 결함
때문의 과소계수였다 — 옮긴 파일 수 10 은 변하지 않는다).

⚠️ **1단계 실행의 선행 조건** — 계약 저장소 로컬 체크아웃이 `origin/main` 보다 **11 커밋
뒤**이고 자기 pre-push 레인 게이트가 막는다(실측 2026-09-03). 낡은 트리 위에서 시작하면
안 된다. 그 저장소를 먼저 최신화하고 게이트를 통과시킨 뒤 시작한다.

### 2단계 — 완료 (2026-09-03)

`application` 공유 20에서 출발해 고정점까지 확장하니 **31에서 닫혔다**
(`application` 20 + `domain` 11). 그 31이 `fcc-test-kernel 0.2.0` 으로 갔고
중앙 전용 4는 `fcc_test_platform/application/` 아래로 갔다.

**최상위 이름 `application` 을 놓았다.** 공유 폐포 54 → 24.

⚠️ 폐포가 31 줄었는데 **1이 늘었다**(`domain/services/reference_hashing.py`).
회귀가 아니라 이름을 놓은 것의 직접적 귀결이다 — 게이트의 provider 씨앗은
*「최상위가 공유 이름이 아닌 파일」* 이라, `application` 이 공유 이름에서 빠지자
provider 의 `application/**` 이 **씨앗으로 승격**해 그것에 도달한다.

### 3단계 — 실측 완료, 실행 대기 (2026-09-03)

남은 공유 폐포 24 = `domain` 22 · `infrastructure` 2.
**두 이름의 클러스터가 각각 이미 닫혀 있다(유출 0)** — 즉 서로 독립이고,
2단계처럼 한쪽이 다른 쪽을 끌어오지 않는다. 순서를 자유롭게 고를 수 있다.

#### 3a — `infrastructure` (10파일). **작다**

| 판정항 | 값 |
|---|---|
| 총 파일 | **10** |
| 공유 → `fcc_test_kernel.infrastructure.*` | **2** |
| 중앙 전용 → `fcc_test_platform.infrastructure.*` | **8** |
| 폐포 유출 | **0** |
| provider 어휘 | **0** |
| 클러스터 밖 의존 | `fcc_test_contracts` 1 · `fcc_test_kernel` 1 |
| 중앙 재작성 | **5파일 · 8줄** |

#### 3b — `domain` (76파일)

| 판정항 | 값 |
|---|---|
| 총 파일 | **76** |
| 공유 → `fcc_test_kernel.domain.*` | **22** |
| 중앙 전용 → `fcc_test_platform.domain.*` | **54** |
| 폐포 유출 | **0** |
| provider 어휘 | **5** |
| 클러스터 밖 의존 | `fcc_test_kernel` 2 |
| 중앙 재작성 | **132파일 · 276줄** |

⚠️ **재작성 술어를 함께 적는다** — *「그 이름을 언급하는 import 줄」* 이다.
모듈별 건수의 합으로 세면 한 줄이 여러 패턴에 걸려 중복 계수된다(2단계 실측:
같은 대상이 술어에 따라 102 와 198).

#### 3단계의 선행 조건 둘

1. **태그 push 승인** — 커널 배포판이 갱신된다.
2. ⚠️ **순수성 가드가 계층 축이어야 한다.** 문자열 축(`'from infrastructure'`)도
   AST **최상위** 축(`node.module.split('.')[0]`)도 접두사에 눈이 먼다 —
   `fcc_test_kernel.infrastructure` 는 둘 다 통과한다. 즉 **이관 당일에 조용히
   약해지는** 자리다. 이 레인은 `tests/_layer_of_import.py` 로 옮겼고(2026-09-03),
   provider 레인도 같은 조건을 걸었다.

## 완료 판정 — 숫자가 아니라 게이트

이 웨이브가 세운 두 게이트가 그대로 완료 오라클이다:

- `scripts/check_import_name_ownership.py` — **두 기계 모두에서 exit 0.**
  지금은 챔버 조건에서 exit 1(위반 2)이다.
- `scripts/check_shared_kernel_closure.py` — **공유 폐포 0.**
  지금은 53이고 기준선이 그것을 래칫한다.

둘 다 파생이다 — 배포판 목록도 이름 목록도 하드코딩하지 않는다.

## 결과

- provider 저장소는 자기 `src/` 를 그대로 두어도 된다. platform 이 그 이름을 주장하지
  않게 되는 순간 충돌이 사라지므로, **이 결정의 실행은 platform 쪽에서 시작할 수 있다.**
- 배포 크기가 준다. 실측 2026-09-03(2단계 후):

  ```
  domain           싣는다 76 · 도달 67 · 도달 안 함  9
  infrastructure   싣는다 10 · 도달  5 · 도달 안 함  5
  ```

  ⚠️ 처음 이 자리에 적힌 것은 「**31개** (`domain` 79/91 · `application` 10/24 ·
  `infrastructure` 10/15)」였고, 그 수는 1단계 이전 것이다. `application` 은
  2단계에서 통째로 사라졌으므로 이 표에 없다.
- PEP 420 경로 스캔 비용이 없다(접두사는 정규 패키지 하나다).
- ⚠️ 소비는 계속 pip 직접 참조(`@ git+https://…@tag`)다. resolver 도움이 없으므로
  **선언↔설치 대조가 「어느 코드가 돌았나」의 유일한 답**으로 남는다.

## 근거

- `packaging.python.org` — *Packaging namespace packages* · *src layout vs flat layout*
- `.claude/rules/repository-split.md` §Contracts Lane Dependency-Free · §Shared Kernel Delivery
- `.claude/rules/check-axis-blindness.md` — **이 결정의 실행에서 여덟 번 발화했다.**
  폐포 워커가 `from pkg import submodule` 을 놓친 것(11개 과소계수) · 폐포가
  패키지 데이터를 못 본 것(커널 15모듈 사망) · `src/` 를 훑는데 그 디렉터리가
  없어 **한 파일도 안 본** 검사 · 배선을 재면서 배포 내용에 걸린 검사 ·
  접두사가 붙자 안 걸리게 된 순수성 금지어(문자열 축 **과 AST 최상위 축 둘 다**) ·
  기준선을 0 으로 만들자 「전부 통과」와 「0건 수집」이 같은 값이 된 것.
- 실측 전량: `docs/platform/shared_kernel_closure.baseline.json`
  · PR #29 #32 #33 (게이트 신설) · #38 (워커 결함) · #39 (2단계) · #44 (계층 축)
  · 계약 레인 #16 #17 (커널 1·2단계)
  · `.claude/evaluations/2026-09-03-*.md` (여섯 편)
