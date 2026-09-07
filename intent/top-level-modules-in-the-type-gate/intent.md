# Intent: 타입 게이트가 부르지 않는 자리 — 최상위 모듈

Author: Claude (초안) / 세션 fcc-delivery-final-b1
Date: 2026-09-07
Status: accepted
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

그리고 45건은 서식 문제가 아니다. **다만 「결함」도 아니었다** — 아래 정정을 보라.

> 🔄 **2026-09-07 정정 (초안 작성 중, 승인 전).** 이 절의 첫 판은 45건을 「런타임
> 크래시 다섯 · 캐시가 영원히 안 맞는 자리 넷 · `None` 인덱싱 둘」이라고 적었다.
> **셋 다 틀렸다.** 오류 메시지의 «모양»을 읽고 코드를 안 읽은 결과다.
>
> * `int >= None` 여섯은 전부 `_int(x) is None or _int(x) <= 0` 형태다. `or` 가
>   **단락 평가**하므로 둘째 항은 첫째가 참일 때 아예 평가되지 않는다. 실행으로
>   확인했다 — `byte_size` 없는 entry 를 그 분기에 태우면 `TypeError` 가 아니라
>   `invalid_byte_size` 로 «정상 분기»한다. mypy 가 잡는 것은 `_int()` 를 **두 번
>   부르기 때문에 첫 호출의 좁힘이 둘째로 전파되지 않는다**는 사실뿐이다.
> * 캐시 키 arity 는 **호출자 전수를 세서** 닫혔다(AST). `register_session` —
>   튜플 키를 쓰는 유일한 자리 — 은 **호출자 0건**이다. `InMemoryCentralIdResolver`
>   생성자 호출 4건은 전부 시험이고 전부 `int` 키다. 즉 `dict[int, str]` 선언은
>   **오늘의 모든 호출자에 대해 참**이다. 내가 근거로 삼은 것은 docstring 의
>   *"populated by the composition root"* 였고, 그 산문은 낡았다 — 운영 경로는
>   `postgres_central_id_resolver` 다.
> * `None` 인덱싱 둘은 함수 안 **중첩 class body** 안에 있다. mypy 는 중첩 스코프로
>   좁힘을 옮기지 않는다. 선형 코드상 `ids` 는 그 지점에서 이미 대입된 뒤다.
>
> 이 레포가 두 줄로 적어 둔 것을 그대로 밟았다 — *「넓은 선언」을 좁힐 때는 호출자
> 전수를 세라. 산문 주석을 읽고 판정하면 틀린다.* 정정을 지우지 않고 남기는 이유는,
> 다음 사람이 mypy 출력의 **계열 이름**(`operator` · `index` · `union-attr`)을 곧바로
> 심각도로 번역하지 않게 하기 위해서다.

### 그러면 45건은 무엇인가 — 그리고 왜 그것이 «더» 문제인가

실제로 코드를 읽은 것은 45건 중 **18건**이다 — `extraction_evidence`(3) ·
`ingestion_execution_evidence`(2) · `central_id_resolver`(3) ·
`cross_session_result_selection_evidence_cli`(2) · `cutover_live_workflow_cli`(2) ·
`db_migration_collect_cli`(2) · `frontend_qa_evidence`(1) · `signed_download`(1) ·
`cutover_bundle_cli`(1) · `central_migration_readiness_cli`(1). 예외 없이 셋 중 하나였다.

| 형태 | 예 | 오늘 터지나 |
|---|---|---|
| **① 이중 호출로 좁힘이 전파되지 않음** | `_int(x) is None or _int(x) <= 0` · `g.get(k) if isinstance(g.get(k), Mapping) else {}` | 아니오 |
| **② 중첩 스코프에서 좁힘이 버려짐** | 함수 안 class body 의 `ids['provider_id']` | 아니오 |
| **③ «적히지 않은 앎»** | `status == _QUARANTINE_MOVED` 면 `path` 가 `None` 이 아닌데, `_QuarantineOutcome` 에 그 말이 없다 | 아니오 |

⚠️ **읽지 않은 27건에 대해서는 아무 말도 하지 않는다.** 「전부 무해하다」는 재지 않은
주장이고, 이 문서가 방금 그 실수로 정정을 달았다. 성격 분류는 `spec.md` 에서 45건
전수로 완성한다.

**터지지 않는다는 것이 이 의도를 약하게 만들지 않는다 — 강하게 만든다.** 터지지
않으니 사람이 안 봤고, 게이트 밖이니 기계도 안 봤다. 그렇게 76개 파일에 「선언이
값보다 좁은」 자리가 쌓였고, 그 위에서 다음 사람이 코드를 고칠 때 타입은 아무것도
지켜 주지 않는다. 특히 ③ 은 이 레포가 이미 이름을 붙여 둔 계급이다 — *타입 오류가
「적히지 않은 앎」일 수 있다. 가드를 흩뿌리면 그 앎이 거짓말이 된다. 믿음에 이름을
붙여라.*

## Proposed outcome

* `mypy.ini` 의 strict 범위가 **네 층의 열거가 아니라 패키지 하나**가 된다.
  그러면 다음에 최상위에 놓이는 모듈이 **구성상** 게이트 안에서 태어난다.
  (이 레포는 같은 교훈을 이미 한 번 샀다: `application` 의 24모듈 열거에서
  `central_read_adapter` 가 이름 규약을 안 따라 두 웨이브 동안 조용히 빠져 있었고,
  와일드카드로 접으면서 그 구멍이 사라졌다.)
* 게이트가 mypy 에 넘기는 source files 가 **139 → 215** 로 움직인다. 이 수가 이
  변경의 「진짜 켜졌는가」 판정이다 — 절 이름이 틀리면 조용히 0건이고, 그 출력은
  「위반 없음」과 구별되지 않는다.
* 45건이 **`cast` 나 `# type: ignore` 가 아니라 코드로** 닫힌다. ①은 값을 변수에
  묶어서(호출도 절반으로 준다), ②는 좁힌 값을 중첩 스코프에 «넘겨서», ③은 **믿음에
  이름을 붙여서** — `status` 와 `path` 의 관계를 타입이 말하게 한다. 셋 다 선언이
  값보다 진실해지는 방향이지, 검사를 무르는 방향이 아니다.
* 그리고 **다시 이 구멍이 생기지 않게 하는 봉인**이 하나 붙는다 — 「선언된 strict
  범위가 `fcc_test_platform` 을 덮는가」를 묻는 검사. 오늘 없는 것이 정확히 그것이고,
  없었기 때문에 이 결함이 초록 뒤에서 살았다.

## Affected users and systems

* **사람**
  * *개발자* — 게이트 범위가 넓어진다. 최상위 모듈을 고칠 때 선언을 요구받는다.
  * *시험원* — **오늘은 변화 없음.** 읽은 18건 중 오늘 터지는 것은 없었다(§Problem
    정정). 값은 「지금 고쳐진다」가 아니라 「다음에 이 CLI 를 고치는 사람을 타입이
    지켜 준다」이다. 그 CLI 들이 만드는 것이 시험 증거이므로 조용한 손상이 가장 비싼
    자리다.
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
4. **45건이 성격별로 «전수» 분류되고 처분 방식이 적혔다** — `spec.md` 가 45건을
   ①②③(또는 새로 드러나는 넷째)로 하나도 빠짐없이 나눈다. 오늘 18건만 읽었다는
   사실이 그 전수 분류의 출발점이다. 그리고 **③ 계열은 회귀 시험을 갖는다** —
   ③ 은 「타입이 몰랐던 앎」이므로 타입 검사만으로 지키면 게이트를 끄는 날 같이
   사라진다. ①②는 좁힘의 문제라 타입 검사 자신이 회귀를 막는다.
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
