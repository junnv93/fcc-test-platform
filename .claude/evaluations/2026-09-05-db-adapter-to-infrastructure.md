# DB 어댑터를 infrastructure 로 옮긴다 — S3 (2026-09-05)

`application` 의 `psycopg` 의존 **2건 → 0건**. `.importlinter` 의 `app-no-db` 계약이
등재 없이 KEPT 가 됐고, 그 판정을 이제 기계가 한다.

## Why

S1·S2(`8f779cb`, 이미 `origin/main`)가 세 계약을 켜면서 `app-no-db` 를 BROKEN 2건으로
baseline 에 등재했다. 그 등재는 **한 방향으로만 줄도록** 설계됐고, 줄이는 작업이 S3 다.
설계서 §6.3 이 처분을 이름으로 적어 두었다.

## What

| 산출물 | 성격 |
|---|---|
| `infrastructure/adapters/driven/central_project_reference_adapter.py` | 이전(rename). **내용 변경 0줄** |
| `fcc_test_platform/central_db_settings.py` | 신규. 설정만 — 드라이버를 모른다 |
| `infrastructure/adapters/driven/central_db_connection.py` | 신규. 연결 생성의 **유일한** 구현 |
| `fcc_test_platform/central_db_config.py` | 파사드로 재작성. `__all__` 8개 **불변** |
| `application/runtime_config.py` | import 대상을 파사드 → 설정 모듈로 |
| `api_composition.py` | 중복 lazy-connect 제거, driven 어댑터로 위임 |
| `.importlinter` | 등재 2건 제거 + 처분 기록 |
| `tests/test_architecture_gate_conformance.py` | 정책 축을 「이름 집합」에서 「부재」로 강화 |
| `scripts/` · `tests/` 소비자 2 | import 경로 한 줄씩 |

## How — 판정 넷

### ① 위반 ②는 「옮기기」로 안 풀린다 — 모듈을 갈라야 했다

직접 위반 ①은 파일 이동으로 끝난다. 간접 위반 ②는 다르다:

```
application.runtime_config -> central_db_config (l.26) -> psycopg (l.135)
```

`central_db_config` 은 **자기 docstring 과 이미 모순**이었다. 16–20행이 "this module
never imports psycopg" 라 선언하는데 135행에 `import psycopg` 가 있다. 둘 다 거짓말은
아니다 — 함수 «안»의 지연 import 라 런타임에는 정말 안 끌어온다(frozen-exe 주장 성립).
그러나 import-linter 의 정적 그래프는 지연 import 를 일반 import 와 구분하지 않는다.
설계서 §2.2 가 contracts 레인에 import-linter 를 기각한 바로 그 성질이다.

그래서 설계서가 「설정 읽기와 연결 생성을 분리」라고 쓴 것은 우연이 아니다. 팩토리를
들어내면 그 모듈은 **자기 docstring 이 이미 주장하던 형태**가 되고, 정적 그래프와
런타임 사실이 처음으로 일치한다.

### ② 이름을 없앨 수 없었다 — 이 레인 밖에 소비자가 있다

`build_central_db_connection_factory` 는 이 레포 안에서 **소비자가 0**이다
(`git grep` 전수: 자기 정의와 `__all__` 뿐). 「죽은 코드」로 읽히는 모양이다.

실제로는 모노레포 `src/progress_expectation_sync_composition.py:40` 이
`from fcc_test_platform.central_db_config import (CENTRAL_DB_ENV, CentralDbConfig,
build_central_db_connection_factory)` 로 가져간다. `requirements-central.txt:96` 이
`fcc-test-platform @ ...@v0.1.8` 로 이 배포판을 고정한다. **배송 방향이 역전돼
모노레포가 pip 로 소비하므로, 공표된 이름을 없애면 다음 판올림에서 그쪽이 깨진다.**

그래서 `central_db_config` 은 파사드로 남고 `__all__` 8개가 **분리 전과 동일**하다.
소비자는 이 분리를 보지 못한다.

### ③ 구현은 «셋»이 아니라 하나로 모았다

분리 이전에 같은 lazy-connect 로직이 **두 벌** 있었다 —
`central_db_config.build_central_db_connection_factory` 와
`api_composition._build_central_connection_factory`. 후자만 살아 있었고 전자는
호출자가 없었다. driven 어댑터를 새로 만들면서 그대로 두면 **세 벌**이 된다. 둘 다
새 SSOT 로 위임시켜 하나로 모았다. 공표된 이름 셋은 전부 남아 있다.

### ④ `.extraction-layout.json` 은 «고치지 않는» 것이 맞았다 — 실패로 배웠다

장부를 참으로 유지하려고 이전한 파일의 **값 한 줄**을 새 경로로 고쳤다. 전체 스위트가
즉시 빨개졌다 — 실패 6 + 수집 오류 3:

```
RelocationAmbiguity: 'src/application/platform' was delivered to more than one
location: ['fcc_test_platform/application',
           'fcc_test_platform/infrastructure/adapters/driven']
```

기제는 설치된 `fcc_test_contracts/common/tree_artifacts.py:_delivered_directory` 다.
**디렉터리**를 해소할 때 접두사 아래 모든 항목의 목적지 집합을 만들고, 후보가 둘이면
거부한다. 한 디렉터리에서 파일 하나만 옮기면 그 순간 후보가 둘이 된다. 이 레인에서
`'src/application/platform'` 을 디렉터리로 해소하는 자리가 **9곳 / 6파일**이다.

되돌린 근거 셋:
1. 모노레포 원본 `src/application/platform/central_project_reference_adapter.py` 는
   **이미 삭제됐다**(`git cat-file -e HEAD:...` 실패). 그 항목은 순수 출처 기록이다.
2. 장부의 값은 살아 있는 해소기의 입력이고, 그 디렉터리 의미론은 「이 디렉터리의 파일
   하나만 딴 데 갔다」를 **표현할 수 없다.**
3. 거부는 결함이 아니라 설계다 — 조용히 한쪽을 고르는 대신 이름을 대고 멈춘다.

## Verification

| 검사 | 결과 |
|---|---|
| import-linter — 착수 전 | `Analyzed 211 files, 921 dependencies` · 3 kept (**app-no-db 는 등재 2건**) |
| import-linter — 등재를 뺀 탐침(착수 전) | `application DB driver isolation` **BROKEN**, 사슬 2개가 설계서와 일치 |
| import-linter — 착지 후 | `Analyzed 213 files, 927 dependencies` · **3 kept, 0 broken, 등재 0건** |
| mypy `domain/*` strict (S1 회귀 확인) | `Success: no issues found in 54 source files` |
| 게이트 봉인 테스트 | **9 passed** — 실행 팔 둘 포함(도구를 워크트리 venv 에 직접 설치) |
| 반증 ⓐ 해소된 등재를 되살리면 | `exit=1` (`No matches for ignored import`) |
| 반증 ⓑ `application` 에 psycopg 재투입 | `exit=1` (`application DB driver isolation BROKEN`) |
| 반증 ⓒ 새 등재를 몰래 추가하면 | 봉인 테스트 `exit=1` — 계약 이름을 대고 멈춘다 |
| 런타임 축 7종(공표 표면 · frozen-exe · 팩토리 · 중복 수렴) | **7/7 PASS** |
| 전체 스위트 — 실패 **이름 집합** | 변경 전 **0** = 변경 후 **0**, 양방향 차집합 공집합. 8/8 묶음 완주 · 3,054 → 3,053 passed |

### 「일했다는 증거」를 함께 본 자리

* import-linter: 종료코드가 아니라 `Analyzed N files, M dependencies` 와 ` KEPT` 3회를
  본다. 이 레포는 `python -m importlinter.cli` 가 `__main__.py` 부재로 **아무 출력 없이
  exit=0** 이던 값을 이미 치렀다.
* mypy: `54 source files` — 「0개를 검사했다」가 「오류 없다」로 읽히지 않게.
* 통과 수가 1 줄어든 것은 회귀가 아니다 — 정책 축 테스트 하나가 사라지고 그 자리를
  3계약 subTest 가 대신했다(서브테스트 159 → 160). 개수가 아니라 **이름 집합**으로
  재는 이유가 이것이다.
* 전체 스위트: 묶음마다 요약 줄의 존재를 확인한다. 첫 시도 두 번이 각각 98% · 91%
  지점에서 **요약 없이** 끊겼는데, `| tail` 파이프 때문에 셸은 `exit=0` 을 돌려주고
  있었다. 종료코드만 봤다면 「전량 초록」으로 기록했을 것이다.

## ⚠️ 이번에 실제로 겪은 오진 — grep 이 레포 «밖»을 못 본다

착수 전에 `.extraction-layout.json` 의 소비자를 이렇게 찾았다:

```
grep -rn "extraction-layout\|extraction_layout" --include='*.py' tests/ scripts/ fcc_test_platform/ githooks/
```

답은 「읽는 곳 3군데, 전부 `.is_file()` 존재 확인뿐」이었고, 그래서 값을 고쳐도 안전하다고
판정했다. **진짜 소비자는 설치된 `fcc_test_contracts` 안에 있었다** — 레포 안만 훑는 grep
에는 원리적으로 안 잡히는 자리다. 에러도 경고도 없이 그럴듯한 답이 나왔다.

이것은 설계서 §7 방법론 교훈 ③ 표의 **여섯 번째 사례**다(그 표는 다섯을 싣는다).
앞의 다섯과 같은 모양이다 — 도구가 조용히 부분집합을 주고, 그 부분집합이 결론을 뒤집었다.

**교정된 판정법:** 「이 파일을 누가 읽는가」는 레포 grep 이 아니라 **설치된 의존까지
포함한 심볼 검색**으로 물어야 한다. 값이 싼 반증은 「고치고 전체 스위트를 돌린다」였고,
이번에는 그것이 실제로 잡았다.

## 남긴 것 — 판정하지 않은 비대칭

`application/` 에는 `connection_factory` 를 잡는 어댑터가 **24개** 있고, 이번에 옮긴 것은
그중 하나뿐이다. 나머지 23개는 `DbConnection` 프로토콜만 잡고 드라이버를 import 하지
않아 계약상 위반이 아니다. 그래서 「DB 어댑터는 driven 에 산다」는 규칙과 실제 배치는
아직 어긋나 있다.

이 커밋은 그것을 판정하지 않는다 — 설계서 S3 의 범위가 아니고, 23파일 이동은 별도
웨이브다.

### 그리고 이 방향이 만드는 사각지대 하나 (오늘은 손실 0)

`test_platform_rbac_membership_audit_fe_p8.py` 의 세 검사는
`resolve_repo_artifact(__file__, 'src/application/platform').rglob('*.py')` 로 훑는다.
`infrastructure/adapters/driven/` 은 그 접두사 «밖»이므로, S3 가 여는 길을 따라
어댑터를 옮기면 그 봉인들의 스캔 집합에서 빠진다.

이번 이동의 손실은 **0** 이다 — 실측: 옮긴 464줄에 audit `event_type` 토큰 0건 ·
`audit_events` 언급 없음 · role 리터럴 0건. 그리고 지금 그 범위를 driven 까지 넓혀도
방출 집합이 변하지 않아(`application/` 73파일이 7/7 토큰을 이미 채우고 driven 5파일은
0건) 등호가 유지된다 — **넓히는 것은 안전하다.**

그럼에도 이 커밋은 넓히지 «않았다». 그 세 검사는 RBAC·감사 봉인이고 S3 의 산출물이
아니다 — 남의 봉인 범위를 지나가며 바꾸면, 바꾼 이유가 그 파일 옆에 남지 않는다.
대신 `.importlinter` 의 계약 3 주석이 **다음에 옮기는 사람이 읽을 자리**에 조건과
처방을 적어 두었다. 다만 **되돌아가는 길은 기계가 막는다**: 새 어댑터가 `application/` 에서
드라이버를 잡으면 `app-no-db` 가 이름을 대고 멈추고, `broken_contract_guidance` 가
갈 자리를 알려 준다.
