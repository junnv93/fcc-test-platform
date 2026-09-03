# 공유 커널 2단계 — 최상위 이름 `application` 을 놓았다 (2026-09-03)

## 이동 단위는 목록이 아니라 폐포다 — 그리고 그것이 경계를 정했다

`application` 공유 20파일에서 출발해 고정점까지 확장하니 **31에서 닫혔다**:

    application 20   central_contract 19 + session/api_contracts
    domain      11   models 5 + services 6

| 판정항 | 값 |
|---|---|
| 31개가 전부 공유 폐포 안인가 | **예 (31/31)** |
| 공유가 아닌 것(커널로 못 감) | **0** |
| 클러스터 밖 의존 | `fcc_test_contracts` 뿐 |
| provider 어휘 | **4** |
| platform 재작성 | 77파일 · 135줄 (+ 정정 6파일) |

⚠️ provider 어휘 4는 1단계(0)와 다르다. **그것이 이 배포판이 계약 레인이 아닌
이유다** — ADR-0010 D-8 은 계약 레인의 provider 어휘를 금지하지만, 커널은
접두사로 충돌만 없애므로 어휘를 묻지 않는다.

## ⚠️ 실패 셋 — 전부 「검사의 축이 대상을 못 보는」 계급이었다

### 1. 폐포가 볼 수 없는 의존 — 패키지 데이터

코드 31파일만 옮기자 커널의 **15개 모듈이 죽었다**:

    ReferenceCatalogError: decision catalogue resource is unavailable

`reference_catalog.py` 가 import 시점에
`importlib.resources.files(__package__).joinpath('decision_catalogue.json')` 로
읽는다. **폐포는 import 축이라 그 간선을 구조적으로 못 본다** — import 문이 아니다.

축을 하나 더 만드는 대신 **더 강한 오라클**을 세웠다(계약 저장소
`tests/test_kernel_imports_standalone.py`): 커널의 모든 모듈을 실제로 import 한다.
자원 누락 · 패키징 누락 · 접두사 재작성 누락이 한 번에 붉는다.

### 2. 내 재작성이 과잉이었다 — 81개 테스트를 깼다

    ImportError: cannot import name 'fcc_id_policy' from 'fcc_test_kernel.domain.services'

`from domain.services import X` 를 **전부** 커널로 보냈는데 그 패키지에서 옮긴 건
6개뿐이다. 판정 단위가 패키지가 아니라 **가져오는 이름**이어야 했다.

⚠️ **이것이 같은 날 고친 폐포 결함과 같은 구문이다** — `from pkg import submodule`.
워커에서는 그 이름을 **놓쳤고**, 재작성기에서는 그 이름을 **뭉갰다**. 같은 구문에서
반대 방향으로 두 번 틀렸다.

### 3. 경로를 하드코딩한 검사 7건

`resolve_repo_artifact(__file__, 'src/domain/services/…')` 로 원본을 읽던 검사들이
`FileNotFoundError` 로 죽었다. **이 저장소가 이미 그 답을 갖고 있었다** —
`tests/_moved_module_source.py` 의 docstring 이 정확히 이 상황을 적는다:

> 경로를 하드코딩한 테스트는 *트리*에 대해 단언하지, 검사하려는 *코드*에 대해
> 단언하지 않는다 — 그리고 그 둘은 같은 것이기를 그만두었다.

즉 이 7건은 **이관이 깨뜨린 것이 아니라 이관이 드러낸 것**이다.

⚠️ 그리고 고치면서 게이트 하나가 **조용히 약해지는 것**을 잡았다:
`test_the_policy_is_domain_pure` 의 금지어가 `'from infrastructure'` 인데,
모듈이 커널로 가면 `'from fcc_test_kernel.infrastructure'` 라 **걸리지 않는다.**
접두사 붙은 이름을 금지어에 함께 넣었다 — 판정은 계층 이름이지 접두사가 아니다.

## 오라클

### 이름 소유권 — `application` 이 위반에서 사라졌다

    이전(낡은 휠)   위반 2: application · domain
    이후(새 휠)     위반 2: domain · infrastructure

⚠️ **개수가 같지만 회귀가 아니다.** `infrastructure` 는 **내 변경 전에도 위반이었고
낡은 설치가 가리고 있었다** — `main` 의 `pyproject.toml:113` 이 이미
`"infrastructure*"` 를 선언한다(같은 날 앞선 웨이브). venv 에 설치된 휠이
그 선언 이전 것이라 `packages_distributions()` 가 그 이름을 안 돌려줬다.

**「pytest 환경은 운영자 환경이 아니다」의 실례다.** 이미지는 `pip install .` 로
현재 pyproject 를 쓰므로 그 이름을 싣는다.

⚠️ 그리고 **editable 설치에서는 이 오라클이 판정 불가다**(설치 루트 밖 해소가 정상).
판정하려면 비-editable 로 설치해 재고, `build/` 를 지운 뒤 editable 로 되돌려야 한다
(`lane_check` 가 `build/` 오염을 막는다).

### 공유 폐포 — 54 → 24

    줄어든 것 31   이관분
    늘어난 것  1   domain/services/reference_hashing.py

⚠️ **+1 은 회귀가 아니라 이름을 놓은 것의 직접적 귀결이다.** 게이트의 provider
씨앗은 *「최상위가 공유 이름이 아닌 파일」* 인데, `application` 이 더는 공유
이름이 아니게 되면서 provider 의 `application/**` 이 **씨앗으로 승격**했고
그것이 `reference_hashing` 에 도달한다. 양쪽이 실제로 부르는 것을 grep 로
독립 확인했다(중앙 2곳 · provider 5곳). **3단계가 옮길 대상을 알려준 셈이다.**

## 회귀 0

`lane_check`: 선언된 실패 17 / 관측된 실패 17 — 일치.
2,883 passed · 737 subtests.

## 남은 것

최상위 이름 **`domain`(87파일)** 과 **`infrastructure`(10파일)** 둘.
공유 폐포 24 중 `domain` 23 · `infrastructure` 1.
