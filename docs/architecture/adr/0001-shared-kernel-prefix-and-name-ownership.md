# ADR-0001 — 공유 커널의 접두사와 최상위 이름 소유권

Status: Accepted — 1단계 실행 대기
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
중앙만 도달    47   → platform 소유로 충분
provider만    282   → provider 소유로 충분
양쪽 도달      53   ← 진짜 공유 커널
```

서드파티 의존 **0**(4건은 전부 `fcc_test_contracts` — first-party 다).

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
(`check_import_name_ownership.py` 가 본다) · 공유 폐포가 53 → 43
(`check_shared_kernel_closure.py` 가 본다).

⚠️ **1단계 실행의 선행 조건** — 계약 저장소 로컬 체크아웃이 `origin/main` 보다 **11 커밋
뒤**이고 자기 pre-push 레인 게이트가 막는다(실측 2026-09-03). 낡은 트리 위에서 시작하면
안 된다. 그 저장소를 먼저 최신화하고 게이트를 통과시킨 뒤 시작한다.

### 2단계 이후 — 남은 43개

`application`(24파일 · import 95건) → `infrastructure`(15 · 11) → `domain`(91 · 359)
순으로, 각 단계마다 폐포로 다시 재서 닫힌 클러스터를 고른다.

⚠️ **이름을 완전히 놓으려면 중앙 전용 47개도 `fcc_test_platform.*` 아래로 가야 한다.**
`domain/` 에 파일이 하나라도 남으면 platform 은 그 이름을 계속 주장한다.
platform 쪽 총 규모: 약 100파일 · import 약 470건.

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
- 배포 크기가 준다 — 지금 platform 휠은 도달하지 않는 모듈 **31개**를 싣는다
  (`domain` 79/91 · `application` 10/24 · `infrastructure` 10/15).
- PEP 420 경로 스캔 비용이 없다(접두사는 정규 패키지 하나다).
- ⚠️ 소비는 계속 pip 직접 참조(`@ git+https://…@tag`)다. resolver 도움이 없으므로
  **선언↔설치 대조가 「어느 코드가 돌았나」의 유일한 답**으로 남는다.

## 근거

- `packaging.python.org` — *Packaging namespace packages* · *src layout vs flat layout*
- `.claude/rules/repository-split.md` §Contracts Lane Dependency-Free · §Shared Kernel Delivery
- `.claude/rules/check-axis-blindness.md` — 이 웨이브에서 세 번 발화했다
- 실측 전량: `docs/platform/shared_kernel_closure.baseline.json` · PR #29 · #32 · #33
