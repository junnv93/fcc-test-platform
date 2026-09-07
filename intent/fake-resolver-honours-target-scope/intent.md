# Intent: 시험용 대역이 세션 uuid 의 target 스코프를 못 지킨다

Author: Claude (초안) / 세션 fcc-delivery-final-c1
Date: 2026-09-07
Status: draft
Slug: fake-resolver-honours-target-scope

## Problem

**`CentralIdResolverPort` 의 두 구현이 같은 질문에 다르게 답한다. 그런데 시험은
그 질문을 한 번도 던지지 않아서, 화면이 초록이다.**

측정 조건을 먼저 적는다 — 이 레포는 「리그가 답을 바꾼다」를 이미 여러 번 치렀다.
`origin/main@51b8cdf`, 새 `python3 -m venv` 뒤 `pip install -e '.[test]'`
(`fcc-test-contracts` 0.1.26 · `fcc-test-kernel` 0.5.4 — 배포판에 `py.typed` 있음 ·
mypy 2.3.1), `MYPYPATH="."`. 이 리그에서 `mypy -p fcc_test_platform` 은
`Success: no issues found in 215 source files` 이고
`tests/test_architecture_gate_conformance.py` 는 18 passed + 9 subtests 다.

### ① 포트가 선언한 것

`fcc_test_platform/domain/ports/output/central_id_resolver_port.py:90-94` —

> ``target_identity`` scopes the uuid to one measurement target. It is required
> for the same reason ``chamber_id`` is: a chamber PC keeps one measurement
> database per target and each numbers its sessions from 1, so without it two
> devices measured on one chamber share a uuid.

즉 **「한 chamber 에서 두 target 을 재면 세션 uuid 가 갈린다」는 포트의 계약**이다.
구현마다 달라도 되는 세부가 아니다.

### ② 두 구현에 같은 질문을 던진 결과 (실증)

세션 경로는 DB 를 안 쓰므로(`test_session_uuid_does_not_touch_db`) 실물도 그대로
부를 수 있다. `connection_factory` 는 부르면 터지는 함수를 넣었고, 안 터졌다.

| 물음 | `PostgresCentralIdResolver` | `InMemoryCentralIdResolver` |
|---|---|---|
| chamber 없음 · target 둘 → uuid 갈리나 | **True** | **False** — 같은 uuid |
| chamber 있음 · target 둘 → uuid 갈리나 | **True** | `CentralIdResolutionError` |

대역이 쓴 등록 형태는 `session_uuid_by_local_id={11: 'central-session-uuid-AAA'}` —
**오늘 이 레포의 모든 시험이 쓰는 바로 그 형태**다.

즉 대역은 **포트가 금지하는 충돌을 조용히 만든다.** 두 target 이 한 uuid 를 받는다.

### ③ 왜 아무도 못 봤나 — 초록의 «출처»

목표를 인계한 관찰은 「대역에서는 언제나 miss 해 fallback 으로 간다」였다.
재 보니 **그보다 나쁘다: 그 조회는 한 번도 실행되지 않는다.**

* `outbox_envelope_builder.py:114` 가 target 을
  `measurement_target_key(context['model_number'], context['sample_code'])` 로 판다.
* 커널의 그 함수는 **둘 다** 있어야 비어있지 않은 값을 낸다 — 실측:
  `('M1', None) → ''`, `('M1','S1') → 'M1|S1'`.
* `tests/test_outbox_envelope_builder.py` 의 픽스처 `context` 에는 `model_number` 도
  `sample_code` 도 **없다**(실측: 그 파일에 두 이름의 출현 0건).

그래서 모든 시험에서 `target_identity == ''` 이고, 대역의 `if target:` 가지도
실물의 target 축도 **실행되지 않는다.** 오늘의 초록은 「fallback 경로를 검증한
초록」조차 아니고 **「target 축이 없는 세계를 검증한 초록」**이다.

이것을 레포 전량으로 확인했다 — 도구 범위를 함께 적는다:
`grep -rn --exclude-dir=.git --exclude-dir=node_modules`, 모든 파일형.

| 이름 | 소스 파일 | **시험 파일** |
|---|---|---|
| `target_identity` | 6 | **0** |
| `session_uuid_name` | 2 | **0** |
| `measurement_target_key` | 3 | **0** |

chamber 축에는 봉인이 있다 —
`tests/test_postgres_central_id_resolver.py::test_chamber_scoped_local_ids_are_distinct_and_stable`.
**그 target 형제가 없다.** 축 하나가 통째로 비어 있다.

### ④ 그리고 대역 안의 비대칭

대역의 `resolve_session_uuid` 는 3-튜플 `(chamber, target, local_id)` 로 «읽는데»,
같은 클래스의 공개 writer `register_session` 은 그 키를 **만들 수 없다**
(실측: `register_session(11,'x',chamber_id='CH-1')` → `[('CH-1', 11)]`).
3-튜플은 죽은 코드가 아니다 — 생성자로 주입하면 hit 한다(실측: 갈림=True).
**도달 불가가 아니라, 자기 writer 로는 못 쓰는 reader** 다.

`register_session` 은 오늘 **호출부가 0건**이다(AST 기준: `ast.Call` 의 함수 이름
매치, 레포 473개 `.py` 전량). 정의만 있다. 그러니 다음 사람이 그것을 처음 부르는
날, 2-튜플이 기록되고 3-튜플 조회가 미끄러져 target 을 조용히 무시한다.

## Proposed outcome

**대역이 포트의 target 스코프 계약을 지키고, 그것을 «시험이 실제로 던지는 질문»으로
바꾼다.**

해결되면 달라지는 것:

1. 대역에 두 target 을 «등록할 수 있고», 등록했으면 대역이 그 둘을 «가른다».
2. 대역의 reader 와 writer 가 같은 키 모양을 다룬다 — 비대칭이 사라진다.
3. 「한 chamber 두 target 은 uuid 를 공유하지 않는다」가 **두 구현 모두에 대해**
   실행되는 검사가 된다. 오늘은 어느 쪽에도 없다.
4. 그 검사는 «갈라짐을 되살리면 빨개진다» — 주입으로 실증한 뒤에 착지한다.

## Affected users and systems

* **사람 — 개발자.** 오늘 이 대역으로 시험을 쓰는 사람은 「target 을 넘겼으니
  검증됐다」고 읽지만 실제로는 그 축이 실행되지 않는다. 잘못된 안심이 대상이다.
* **사람 — 시험원(간접).** 이 충돌이 생산에서 실현되면 한 chamber 에서 잰 두 시료가
  중앙에서 한 세션으로 접힌다. 실물은 그 결함을 이미 갖고 있지 않다 — 이 판은
  **그것이 그대로임을 잴 수 있게** 만드는 판이다.
* **레인 — `fcc-test-platform` 만.** `fcc-test-contracts` 는 **닿지 않는다**:
  키 «파생»(`measurement_target_key` · `provider_session_natural_key`)은 커널의
  SSOT 에 있고 이 판은 그것을 부르기만 한다. **커널 태그 발행 불필요.**
* **provider 비공개 레포** — 닿지 않는다. `CentralIdResolverPort` 는 platform 내부다.
* **DB · OpenAPI · 프론트엔드** — 닿지 않는다. 이 판은 시험용 대역과 그 검사뿐이다.

## Constraints

* **실물을 고치지 않는다.** 실물은 옳다(위 ② 표). 실증까지 있다: miss 경로와 hit
  경로가 같은 값을 낸다 — 캐시는 관측 불가한 최적화이고, 대역이 «캐시 키 모양»을
  베낄 이유가 없다.
* **한 판에 한 축만 움직인다.** 대역에는 갈라짐이 **둘** 있다 — target 축과 chamber
  정규화 축(`normalize_chamber_id(None) → '__fcc_legacy__'` 대 `'' `). 둘째는 재서
  이름을 붙였고 **이 판에서 건드리지 않는다.** 함께 움직이면 어느 쪽이 원인인지
  말할 수 없다.
* **오늘 초록인 시험 8건의 «의미»가 바뀌는 것을 감춰서는 안 된다.** 그 시험들은
  target 이 빈 세계를 검증한다. 그 사실을 지우지 말고 적어야 한다.
* **기준선에 실패 이름을 추가하지 않는다.** 이 판은 부채 등재가 아니다.
* **승인 지점 없음** — 태그 push 도 컨테이너 재기동도 없다.

## Success criteria

관측되는 값으로 적는다. 조건은 위 Problem 머리의 리그와 같다.

| # | 지금 | 이 판 뒤 |
|---|---|---|
| S1 | `target_identity` 를 이름으로 쓰는 **시험 파일 0개** | **≥1** — 두 구현을 모두 부르는 파일 |
| S2 | 대역: chamber 1개 · target 2개 → uuid **같음** | **다름** (등록된 경우) |
| S3 | `register_session` 이 만들 수 있는 키 모양 **2종** | **3종** — reader 가 읽는 것 전부 |
| S4 | 새 검사의 주입 실증 **없음** | 갈라짐 복원 시 **red**, 기록으로 남김 |
| S5 | `mypy -p fcc_test_platform` **0건 / 215 files** | **바뀌지 않음** |
| S6 | 게이트 **18 passed + 9 subtests** | **바뀌지 않음** |
| S7 | `lane_check` 선언된 실패 집합 | **바뀌지 않음** (추가 0) |

⚠️ S5·S6·S7 은 「좋아진다」가 아니라 **「안 움직인다」**가 판정이다. 이 판이 그
축들을 건드리면 축이 둘이 되고, 그러면 원인을 말할 수 없다.

## Open questions

1. **대역이 target 을 «모르는» 등록만 갖고 있을 때, target 을 물으면 어떻게 답해야
   하나?** 오늘은 조용히 target-무관 항목을 돌려준다(= 충돌). 후보 셋:
   (a) 그대로 둔다 — `{11:'AAA'}` 는 「스코프 무관하게 11은 AAA」라는 «선언»이므로
       그것을 존중하는 것이 옳다. (b) 언제나 loud. (c) **등록부에 그 
       `(chamber, local_id)` 의 target-스코프 항목이 «하나라도» 있으면 loud, 없으면
       (a)** — 「선언하지 않은 무관심」과 「선언한 스코프를 무시함」을 가른다.
   초안 의견은 (c) 다. (b) 는 오늘 초록인 8건을 전부 깨뜨리는데, 그 8건은 target 을
   «신경 쓰지 않는다»고 정당하게 선언한 시험이다.
   → 답( ):

2. **오늘의 픽스처를 target 있는 것으로 바꿔야 하나, 아니면 target 있는 픽스처를
   «더해야» 하나?** 바꾸면 8건이 검증하던 「target 없는 세계」가 사라진다 — 그 세계도
   생산에 존재한다(model·sample 이 없는 legacy 행). 더하면 두 세계가 다 남는다.
   초안 의견은 **더한다**.
   → 답( ):

3. **`register_session` 의 호출부가 0건인데 그것을 넓히는 것이 옳은가?** 「아무도 안
   쓰는 메서드를 고친다」로 읽힐 수 있다. 반대 근거: reader 는 그 키를 읽고 있고,
   비대칭 자체가 다음 사람이 밟을 지뢰다. 대안은 `register_session` 을 «지우는» 것인데
   그것은 공개 표면 축소라 별도 판정이다.
   → 답( ):

4. **chamber 정규화 갈라짐을 이 intent 가 이름으로만 남기고 넘기는 것이 맞나?**
   같은 클래스의 같은 메서드 안에 있어서 「한 번에 고치는 게 싸다」는 반론이 가능하다.
   초안 의견은 **넘긴다** — 이 레포가 반복해 기록한 「두 축이 함께 움직이면 하나를
   고정하라」에 정면으로 걸린다.
   → 답( ):
