# Spec: 시험용 대역이 세션 uuid 의 target 스코프를 못 지킨다

Intent: ./intent.md
Author: Claude (초안) / 세션 fcc-delivery-final-c1
Date: 2026-09-07
Status: accepted
Slug: fake-resolver-honours-target-scope

## 0. 이 스펙이 서 있는 판정

intent 가 실증한 것을 한 줄로 옮긴다 — **인계문의 이분법은 축이 잘못 잡혀 있었다.**

인계문은 「① 대역을 실물의 «키 규약»에 맞춘다 / ② 그 조회가 대역에서 무의미함을
명시한다」를 놓고 **3-튜플 캐시 키**를 축으로 삼았다. 재 보면 그 축은 실물 전용이다:

* 실물의 `_session_cache` 는 순수 함수
  `uuid5(ns, session_uuid_name(provider, chamber, key, target))` 의 **메모이제이션**이다.
  실증: **miss 경로 == hit 경로**(같은 질문 2회, 값 동일). 캐시를 통째로 지워도 관측
  가능한 동작이 같다. → 「3-튜플이라는 **키 모양**」은 대역이 베낄 대상이 **아니다**.
* 대역의 `_session_uuid` 는 캐시가 아니라 **등록부**다. 값을 «계산»하지 않고 «돌려준다».
  같은 이름의 dict 지만 **역할의 종이 다르다.**

그러나 그 키가 인코딩하는 **target 스코프는 최적화가 아니라 포트가 선언한 계약**이다
(`domain/ports/output/central_id_resolver_port.py:90-94`). 그리고 대역은 그 계약을
**위반한다** — 한 chamber 두 target 에서 실물은 갈리고(True) 대역은 안 갈린다(False).

> **판정: ①이 옳다. 단 「실물의 캐시 키를 복사」해서가 아니라 「포트가 선언한 스코프를
> 대역의 등록부 이디엄으로 표현」해서.**

선례 `PR #109` 와의 차이도 적어 둔다. #109 는 **동작이 옳았고** 타입이 그 사실을
말하지 않던 경우라 「전용 조회로 타입에 알린다」가 답이었다. 여기는 **동작이 틀렸다** —
포트가 금지하는 충돌을 대역이 만든다. 그래서 같은 답이 아니다.

## 1. 요구사항

- **R1.** 대역은 target-스코프 등록을 **가질 수 있다** — `register_session` 이
  `target_identity` 를 받아 reader 가 읽는 3-튜플 키를 만든다.
- **R2.** 대역은 target-스코프 등록을 **존중한다** — 한 chamber 에 두 target 을
  등록하면 두 uuid 가 갈린다. 실물과 같은 답이다.
- **R3.** 대역은 caller 가 **선언한** 스코프를 조용히 넘어가지 않는다 — 그
  `(chamber, local_id)` 에 target-스코프 등록이 하나라도 있는데 물은 target 이 그중에
  없으면 `CentralIdResolutionError` 로 loud fail 한다.
- **R4.** 대역은 caller 가 **선언하지 않은** 무관심은 존중한다 — 그
  `(chamber, local_id)` 에 target-스코프 등록이 하나도 없으면 오늘과 똑같이
  target-무관 항목을 돌려준다. 오늘 초록인 8건의 동작은 **바뀌지 않는다.**
- **R5.** reader 가 읽는 키 모양의 집합과 writer 가 만들 수 있는 키 모양의 집합이
  **같다.** 이 등호는 «선언에서 파생»한다 — reader 를 AST 로 읽어 모양을 뽑고,
  writer 를 실제로 불러 만들어진 모양과 대조한다. 상수로 박지 않는다.
- **R6.** target 축이 **실행된다** — 빌더→대역 경로를 target 이 비어있지 않은 채로
  통과시키는 픽스처가 생긴다. 오늘은 그 경로가 한 번도 실행되지 않는다.
- **R7.** 두 구현이 같은 성질에 대해 **같은 답**을 낸다는 것이 하나의 검사로 봉인된다.
  실물의 값을 베껴 적지 않고, 두 구현에 같은 질문을 던져 대조한다.

## 2. 범위 밖 (Non-goals)

* **실물(`postgres_central_id_resolver.py`)을 고치지 않는다.** 실물은 옳다.
  이 스펙의 어떤 요구사항도 그 파일을 바꾸지 않는다.
* **chamber 정규화 갈라짐을 고치지 않는다.** 실물 `normalize_chamber_id(None) →
  '__fcc_legacy__'`, 대역 `str(None or '') → ''`. 같은 메서드 안에 있는 **둘째**
  갈라짐이고, 한 판에 두 축을 움직이면 결과의 원인을 말할 수 없다. 별도 intent.
* **`register_session` 을 지우지 않는다.** 공개 표면 축소는 별도 판정이다.
* **커널(`fcc-test-contracts`)을 고치지 않는다.** 키 «파생»의 SSOT
  (`measurement_target_key` · `session_uuid_name`)는 이미 옳고, 이 판은 그것을
  부르기만 한다. **커널 태그 발행 없음.**
* **오늘 초록인 8건의 «단언»을 바꾸지 않는다.** 그 시험들은 target 없는 세계를
  검증하고 그 세계는 생산에 존재한다. 픽스처를 «바꾸지» 않고 «더한다».
* **`delivered_test_run_baseline.json` 에 실패 이름을 추가하지 않는다.**

## 3. 설계 결정과 근거

| # | 결정 | 버린 대안 | 근거 |
|---|---|---|---|
| **D1** | 대역이 target 스코프를 지키게 한다(①) | ② 「대역에서 무의미함을 명시」 | target 스코프는 포트 docstring 이 선언한 **계약**이지 실물의 세부가 아니다. ②는 그 계약이 대역으로 «영원히 검증 불가»임을 굳히는 선택이다. |
| **D2** | 실물의 3-튜플 «키 모양»은 복사하지 않는다 | 대역의 dict 를 `dict[tuple[str,str,int], str]` 로 맞춤 | 실증: 실물의 miss 경로 == hit 경로. 그 dict 는 관측 불가한 캐시다. 등록부에 캐시 키를 이식하면 두 자료구조의 «종»이 섞인다. |
| **D3** | 미등록 target 은 «다른 등록이 있을 때만» loud (R3+R4) | (b) 언제나 loud / (a) 언제나 조용 | (b) 는 target 을 정당하게 무시하는 8건을 깨뜨린다 — 오탐을 내는 게이트는 우회를 가르친다. (a) 는 결함을 남긴다. 가르는 축은 「caller 가 스코프를 **선언했는가**」다. |
| **D4** | `_has_target_scoped` 는 dict 를 **그때 훑는다** | 별도 인덱스 집합을 유지 | 두 번째 자료구조는 갈라진다. 등록부는 시험용이라 항목이 한 줌이고, 훑는 것 자체가 «파생»이다. |
| **D5** | R5 의 키 모양 집합을 **AST 로 파생** | 검사에 `{int, 2-tuple, 3-tuple}` 을 상수로 적음 | 상수는 네 번째 모양이 생긴 날 조용해진다. 이 레포가 이미 「집합은 선언에서 파생하라」로 기록한 형태다. |
| **D6** | R7 을 **두 구현 대조**로 봉인 | 실물의 기대값을 문자열로 적어 대역과 비교 | 값을 베끼면 실물이 바뀐 날 검사가 «과거를 검사한 초록»이 된다. 질문을 둘 다에게 던지고 «성질»을 비교한다. |
| **D7** | 픽스처를 **더한다** | 기존 픽스처에 model_number·sample_code 를 넣음 | 넣으면 target 없는 세계를 검증하던 8건이 사라진다. 그 세계는 legacy 행으로 생산에 존재하고 `session_uuid_name` 이 일부러 보존한다. |

## 4. 영향받는 경계

* **레인**: `fcc-test-platform` **만**. `fcc-test-contracts` 는 닿지 않는다 —
  커널 태그 발행 **불필요**. (키 파생 SSOT 는 이미 커널에 있고 이 판은 소비만 한다.)
* **DB**: 마이그레이션 **없음**. 이 판은 시험용 대역과 검사만 만진다.
* **API 계약**: OpenAPI 표면 **안 바뀜**. `CentralIdResolverPort` 는 내부 driven port 다.
* **프론트엔드**: 시각 골든 **안 바뀜**.
* **provider 비공개 레포**: **닿지 않음**.

**Debt-Accepted-By**: (없음) — 이 판은 기준선에 실패 이름을 추가하지 않는다.

## 5. 정책 확인

무엇을 확인했는지로 적는다.

* **인증/RBAC**: 확인함 — 해당 없음. `CentralIdResolverPort` 는 인증 경계 뒤의
  driven port 이고, 이 판은 그 구현 중 «시험용 대역» 하나와 검사만 만진다.
  `githooks`·`scripts/lane_check.py`·`.github/workflows/`·`CLAUDE.md` 어느 것도
  건드리지 않으므로 **T1(게이트 자신)에 걸리지 않는다.**
* **개인정보**: 확인함 — 새 PII 없음. 다루는 값은 chamber id · 로컬 세션 정수 ·
  `model|sample` 형태의 측정 대상 키 셋뿐이고, 셋 다 이미 이 경계를 통과하던 값이다.
  새 필드를 만들지 않는다.
* **오프라인 동작**: 확인함 — 영향 없음. 세션 uuid 경로는 **DB 를 만지지 않는다**
  (`test_session_uuid_does_not_touch_db` 가 그것을 봉인하고 있고, 이 스펙의 검사도
  「부르면 터지는 `connection_factory`」로 같은 축을 다시 확인한다).

## 6. 성공 판정

intent §Success criteria 를 «무엇이 그 값을 내는가»로 옮긴다.
측정 조건은 intent 머리의 리그와 같다(새 venv · contracts 0.1.26 · kernel 0.5.4 ·
mypy 2.3.1 · `MYPYPATH="."`).

| # | 관측 방법 | 지금 | 판정 |
|---|---|---|---|
| S1 | `grep -rn --exclude-dir=.git target_identity tests/` 파일 수 | 0 | **≥1** |
| S2 | `tests/test_central_id_resolver_target_scope.py::…::test_both_implementations_split_two_targets` | 없음 | **pass** |
| S3 | 같은 파일 `…::test_writer_can_make_every_key_shape_the_reader_reads` (AST 파생 등호) | 없음 | **pass** |
| S4 | 같은 파일 `…::test_declared_scope_is_not_silently_bypassed` (R3) | 없음 | **pass** |
| S5 | 같은 파일 `…::test_undeclared_scope_still_serves_the_agnostic_entry` (R4) | 없음 | **pass** |
| S6 | `tests/test_outbox_envelope_builder.py` — target 있는 픽스처가 빌더를 통과 | 없음 | **pass** |
| S7 | `mypy -p fcc_test_platform` | `0 / 215 files` | **안 움직임** |
| S8 | `tests/test_architecture_gate_conformance.py` | 18 passed + 9 subtests | **안 움직임** |
| S9 | `lane_check` 선언된/관측된 실패 | 0 / 0 (전량 3383 passed · 859 subtests) | **0 / 0, 추가 0** |

⚠️ **S7~S9 는 「좋아진다」가 아니라 「안 움직인다」가 판정이다.** 이 판이 그 축들을
움직이면 축이 둘이 되고, 그러면 어느 쪽이 원인인지 말할 수 없다.

**주입 확인**: S2~S5 각각에 대해 「갈라짐을 되살렸을 때 빨개지는가」를 확인하고 결과를
`plan.md` 에 적는다. 확인은 **쓰던 리그를 망가뜨리지 않고** 트리 사본에서 한다.

## 7. 열린 질문

1. 대역이 target-무관 등록만 가진 상태에서 target 을 물으면 어떻게 답해야 하나?
   → 답(운영자 — 2026-09-07): **(c).** 그 `(chamber, local_id)` 에 target-스코프
      등록이 하나라도 있으면 loud, 없으면 오늘처럼 target-무관 항목을 준다. 가르는
      축은 「caller 가 스코프를 **선언했는가**」이고, 「선언하지 않은 무관심」을
      깨뜨리는 것은 오탐이다. → R3 · R4 · D3.

2. `register_session` 은 호출부가 0건인데 넓히는 것이 옳은가?
   → 답(운영자 — 2026-09-07): **넓힌다.** 0건은 「고칠 필요 없다」가 아니라 「지뢰가
      아직 안 밟혔다」다. reader 가 읽는 모양을 writer 가 못 만드는 비대칭 자체가
      다음 사람이 밟을 자리다. 「지운다」는 공개 표면 축소라 별도 intent. → R1 · R5.

3. 오늘의 픽스처를 target 있는 것으로 바꿀 것인가, 더할 것인가?
   → 답(세션 fcc-delivery-final-c1 — 개발 담당, 2026-09-07): **더한다.** target 이 빈
      세계도 생산에 존재하고(`measurement_target_key` 는 model·sample 중 하나만 없어도
      `''`), `session_uuid_name` 이 그 경우 legacy 식별자 공간을 일부러 보존한다.
      바꾸면 그 세계를 검증하던 8건이 사라진다. → R6 · D7 · Non-goals.

4. chamber 정규화 갈라짐을 이 판에서 함께 고칠 것인가?
   → 답(세션 fcc-delivery-final-c1 — 개발 담당, 2026-09-07): **고치지 않는다.** 「두
      축이 함께 움직이면 하나를 고정하라」에 정면으로 걸린다. 이 판 뒤에도 두 구현이
      갈리면 그 원인이 chamber 정규화라는 것이 «남은 하나»라 오히려 선명해진다.
      → Non-goals · work-claim `non_scope`.

5. R5 의 「reader 가 읽는 키 모양」을 AST 로 파생할 때, 파생기 자신이 여러 줄 표현이나
   변수 경유 접근을 놓치면 등호가 «공허하게» 통과하지 않는가?
   → 답(세션 fcc-delivery-final-c1 — 개발 담당, 2026-09-07): **놓칠 수 있고, 그래서
      주입으로 확인한다.** 파생기는 `self._session_uuid` 에 대한 `Subscript` 와
      `.get(...)` 인자를 **AST 로** 읽는다(줄 단위 정규식이 아니다 — 이 레포는 「줄
      단위 스캔은 여러 줄 표현을 못 본다」를 하루에 네 번 밟은 기록이 있다). 그리고
      `plan.md` §2 의 주입 확인에 **「네 번째 키 모양을 reader 에 넣으면 등호가
      빨개지는가」**를 항목으로 넣는다. 파생기가 눈이 멀었으면 그 주입이 통과하고,
      통과하면 그것이 결함으로 보고된다.
