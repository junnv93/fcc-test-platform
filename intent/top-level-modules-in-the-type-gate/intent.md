# Intent: 타입 게이트가 부르지 않는 자리 — 최상위 모듈

Author: Claude (초안) / 세션 fcc-delivery-final-b1
Date: 2026-09-07
Status: draft
Slug: top-level-modules-in-the-type-gate

## Problem

**`mypy.ini` 는 「`fcc_test_platform` 전량이 strict」라고 적고 있지만, 실측하면
그 패키지의 절반 가까이가 게이트에 한 번도 넘어간 적이 없다.**

측정 조건을 먼저 적는다 — 이 레포는 「리그가 답을 바꾼다」를 이미 여러 번 치렀다.
main `b3c619e`, `python3 -m venv` 뒤 `pip install -e '.[test]'` 로 선언대로 설치한
환경(`fcc-test-contracts` 0.1.26 · `fcc-test-kernel` 0.5.4 · mypy 2.3.1 · fastapi 있음),
게이트가 쓰는 것과 같은 `PYTHONPATH`/`MYPYPATH`.

| 게이트가 실제로 부르는 것 | 결과 | mypy 가 센 source files |
|---|---|---|
| `mypy -p fcc_test_platform.domain` | Success | 51 |
| `mypy -p fcc_test_platform.infrastructure` | Success | 11 |
| `mypy -p fcc_test_platform.application` | Success | 75 |
| `mypy -p fcc_test_platform.api` | Success | 2 |
| **합** | **0건** | **139** |
| `mypy -p fcc_test_platform` (게이트가 **안** 부름) | **45 errors / 16 files** | **215** |

네 층이 나란히 0 인 것은 참이다. 그런데 **그 넷을 다 합쳐도 패키지가 아니다.**
차이 76 은 `fcc_test_platform/` 바로 아래 놓인 모듈들이고(디스크에서 세면 `.py`
파일 75개 — 두 수가 다른 이유는 아래 §Constraints), 45건의 오류가 **전부** 거기 있다.

### 왜 이 구멍이 «보이지 않았나»

`mypy.ini` 의 api 절 주석은 *"마지막 층이고, 이것으로 `fcc_test_platform` 전량이
strict 다"* 라고 적는다. 그 문장을 쓴 사람이 틀린 것이 아니라, **네 층의 합집합이
패키지와 같다고 가정**한 것이다. 그리고 그 가정을 반증할 수 있는 검사가 없었다 —
`TestTheGatesActuallyRun` 은 `STRICT_PACKAGES` 를 순회하며 「각 층이 0인가」만 묻고,
「이 넷이 패키지를 덮는가」는 **아무도 묻지 않는다.**

이것은 이 레포가 이름을 붙여 둔 형태다: *공허 통과의 둘째 종류 — 집합이 비는 것이
아니라 **집합이 틀린 것**.* 검사는 성실히 돌고, 139개 파일을 정말로 검사하고,
정직하게 초록을 보고한다. 그 초록이 말하지 않는 것은 **76이 밖에 있다**는 사실뿐이다.

### 그 자리에 무엇이 있나

`pyproject.toml` 의 콘솔 스크립트 **38개 전부**가 최상위 모듈을 가리킨다(실측).
즉 이 레인이 «바깥에 발행하는 진입점 전량»이 게이트 밖에 있다. 그 안에는 시험
증거를 만드는 CLI 들이 있다 — `cutover_bundle_cli` · `keyset_cursor_live_proof_cli` ·
`cross_session_result_selection_evidence_cli` · `central_migration_readiness_cli` ·
`api_composition`.

그리고 45건은 서식 문제가 아니라 **결함**이다. 성격별로 셋만 든다.

* **런타임 크래시가 되는 비교 다섯** — `int >= None`.
  `extraction_evidence.py:154,263,287` · `frontend_qa_evidence.py:79` ·
  `ingestion_execution_evidence.py:112,167`. 왼쪽이 `int | None` 인데 `None` 이 오면
  `TypeError` 다. 증거 생성 CLI 가 증거를 못 만들고 죽는 자리다.
* **캐시 키 arity 불일치 넷** — `central_id_resolver.py:68,94,98` ·
  `postgres_central_id_resolver.py:232,237`. `dict[int, str]` 로 선언된 캐시를
  `tuple[str, int]` 로 조회한다. 예외는 안 나지만 **캐시가 영원히 안 맞는다** —
  즉 조용히 매번 원격을 친다. 이 계열은 「느리다」로만 나타나서 사람이 못 찾는다.
* **`None` 인덱싱 둘** — `cross_session_result_selection_evidence_cli.py:893,909`.
  `dict[str, str] | None` 을 그대로 `[...]` 한다.

## Proposed outcome

* `mypy.ini` 의 strict 범위가 **네 층의 열거가 아니라 패키지 하나**가 된다.
  그러면 다음에 최상위에 놓이는 모듈이 **구성상** 게이트 안에서 태어난다.
  (이 레포는 같은 교훈을 이미 한 번 샀다: `application` 의 24모듈 열거에서
  `central_read_adapter` 가 이름 규약을 안 따라 두 웨이브 동안 조용히 빠져 있었고,
  와일드카드로 접으면서 그 구멍이 사라졌다.)
* 게이트가 mypy 에 넘기는 source files 가 **139 → 215** 로 움직인다. 이 수가 이
  변경의 「진짜 켜졌는가」 판정이다 — 절 이름이 틀리면 조용히 0건이고, 그 출력은
  「위반 없음」과 구별되지 않는다.
* 위의 결함 45건이 **`cast` 나 `# type: ignore` 가 아니라 코드로** 닫힌다.
* 그리고 **다시 이 구멍이 생기지 않게 하는 봉인**이 하나 붙는다 — 「선언된 strict
  범위가 `fcc_test_platform` 을 덮는가」를 묻는 검사. 오늘 없는 것이 정확히 그것이고,
  없었기 때문에 이 결함이 초록 뒤에서 살았다.

## Affected users and systems

* **사람**
  * *개발자* — 게이트 범위가 넓어진다. 최상위 모듈을 고칠 때 선언을 요구받는다.
  * *시험원* — 간접적. 증거 생성 CLI 가 `TypeError` 로 죽는 경로 다섯이 닫힌다.
* **레인**
  * `fcc-test-platform` — **여기서만** 일어난다.
  * `fcc-test-contracts` — **닿지 않는다.** 커널 태그가 필요 없다. 근거: 이 변경은
    이 레인의 `mypy.ini` 와 최상위 모듈만 만진다. 커널의 거짓말하던 선언 넷은
    이미 `v0.1.26` / `kernel-v0.5.4` 에서 처분됐고, 이 리그는 그것을 설치한 상태다
    (`fcc_test_contracts` 에 `py.typed` 있음을 실측 — 없으면 커널 타입이 전부 `Any`
    가 되어 같은 커밋이 다른 수를 낸다).
  * provider 비공개 레포 — 닿지 않는다.
* **게이트 자신** — `mypy.ini` 와 `tests/test_architecture_gate_conformance.py` 를
  만진다. 즉 **방아쇠 T1 경로**다(`intent/README.md`: 게이트를 고치는 권한은 그
  게이트가 막는 것보다 세다). T1 은 오늘 advisory 이지만, 그 사실이 이 의도를
  면제하지는 않는다 — `spec.md` 에 근거를 남긴다.

## Constraints

* **`cast` · `# type: ignore` 로 막지 않는다.** 그러면 결함이 «보존»된 채 초록이 된다.
  이 레포는 그 형태에 이름을 붙여 뒀다.
* **기준선(`delivered_test_run_baseline.json`)에 실패 이름을 넣지 않는다.** 넣으면
  방아쇠 T2(부채 등재)이고, 그것은 이 의도가 하려는 일의 반대다.
* **`[mypy]` 전역 `disallow_untyped_defs` 는 계속 `False`** 여야 한다. 게이트 봉인이
  그것을 단언한다(`test_the_declared_layers_are_strict_and_the_rest_is_not_yet`).
  범위는 `fcc_test_platform` 이지 저장소 전체가 아니다 — `tests/` 와
  `apps/web/scripts/` 는 밖에 남는다.
* **`STRICT_SECTIONS` 는 한 곳에서만 파생한다.** `mypy.ini` 와
  `tests/test_architecture_gate_conformance.py` 두 곳에 적으면 갈라진다.
* **부분 와일드카드는 쓸 수 없다.** `[mypy-…application.central_*]` 는 0건 매치다
  (이 레포가 실측해 두 곳에 적어 놓았다). 「최상위 모듈만」도 같은 이유로 표현할 수
  없다 — 표현 가능한 것은 `fcc_test_platform.*`(전부) 뿐이다.
* **⚠️ mypy 의 「N source files」는 `.py` 파일 수가 아니다.** `__init__.py` 가 없는
  디렉터리를 더 센다(실측: `api/` 는 `.py` 1개인데 2를 보고한다 — `api/` 와 루트
  `fcc_test_platform/` 둘 다 네임스페이스 패키지다). 그래서 이 문서는 **215·139 를
  mypy 의 수치로, 75 를 디스크의 수치로** 나눠 적었고, 판정도 mypy 수치끼리 한다.
* **호출자를 세지 않고 「의도적으로 넓다」고 판정하지 않는다.** 넓은 선언을 좁힐 때는
  호출자 전수를 센다. 산문 주석을 근거로 삼으면 틀린다.

## Success criteria

1. **범위가 실제로 움직였다** — 게이트가 mypy 에 넘기는 source files 가
   `139`(4회 호출의 합) → `215`(1회 호출)로 바뀐다. 켜기 전후를 같은 리그에서 잰다.
2. **`mypy -p fcc_test_platform` 이 `Success`** — 오류 0. 시작값은 228
   (`no-untyped-def` 183 + 실제 타입 오류 45)이고, **183 은 하한**이다(§Open questions Q1).
3. **`cast` 와 `# type: ignore` 순증가 0** — 이 브랜치의 diff 에서 센다.
4. **결함 45건이 이름으로 닫혔다** — 위 세 계열(비교 5 · 캐시 키 4 · `None` 인덱싱 2)이
   `spec.md` 에 하나씩 처분 방식과 함께 적히고, 그중 최소 한 계열은 **회귀 시험**을
   갖는다(타입 검사만으로 지키면 게이트를 끄는 날 같이 사라진다).
5. **새 봉인이 이빨을 갖는다** — 「선언된 strict 범위가 패키지를 덮는가」 검사를
   주입으로 확인한다: 범위를 다시 네 층 열거로 되돌리면 그 검사가 **빨개져야** 한다.
   주입이 착지했는지까지 확인한다(이 레포는 「공허한 주입」을 한 세션에 세 번 겪었다).
6. **`lane_check` 선언된 실패 0 / 관측된 실패 0**, 그리고 **대조군**: merge-base 에서
   같은 리그로 한 번 더 재서 신규 red 가 0.

## Open questions

* **Q1 — 183 은 얼마나 늘어나나?** PR #132 가 같은 자리에 적었다: *"`_require_*_service`
  아홉의 반환을 선언하자 그 뒤의 서비스 호출이 `Any` 를 벗으면서 «없던» 실제 오류가
  새로 드러났다."* 즉 최종값은 228 보다 크다. **얼마나 큰지는 해 봐야 안다** —
  이것은 답을 미루는 것이 아니라, 답이 측정에서만 나오는 종류다. `spec.md` 에 중간
  측정값을 남긴다.
  → 답( ):
* **Q2 — 네 층 절을 지울 것인가, 남길 것인가?** `[mypy-fcc_test_platform.*]` 는 넷을
  «전부» 포섭한다(실측: 그 절 하나만 추가했을 때 228건으로, 전역
  `--disallow-untyped-defs` 와 **정확히 같은 수**). 남기면 게이트가 mypy 를 5회
  호출하고 뒤의 4회는 새로 재는 것이 없다. 지우면 층별 subTest 라벨이 사라진다.
  구조 문서(`.importlinter`)가 층을 이미 지키므로 지우는 쪽이 맞아 보이지만,
  **「범위를 넓히는 변경」과 「기존 선언을 지우는 변경」을 한 PR 에 섞는 것**이
  옳은지는 승인자의 판단이다.
  → 답( ):
* **Q3 — 시험 코드는 계속 밖인가?** `tests/`(현재 게이트 밖)에도 `no-untyped-def` 가
  얼마나 있는지 이 의도는 재지 않았다. 밖에 두는 것이 오늘의 선언이고, 이 의도는
  그것을 바꾸지 않는다. 다만 「전량 strict」라는 말이 **또** 부정확해지지 않도록,
  범위 밖 목록을 `mypy.ini` 에 명시적으로 남긴다(오늘도 남아 있다).
  → 답( ):
