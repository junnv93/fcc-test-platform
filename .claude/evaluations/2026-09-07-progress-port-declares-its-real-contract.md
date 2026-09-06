# 진행 릴레이 포트가 자기 계약을 적게 한다

PR #132 이 «드러내기만» 하고 남긴 🔴 ⓒ(`2026-09-06-api-strict-the-last-layer.md`)를 닫는다.

## 무엇이 결함이었나 — 좁힘은 «세 겹»이었다

들어온 문제 기술은 「포트가 publish-only 라 선언하는데 릴레이가 `.subscribe()` 를
부른다」였다. 실측해 보니 좁힘이 한 곳이 아니었다:

| 겹 | 자리 | 무엇을 숨겼나 |
|---|---|---|
| ① | `ChamberProgressBroadcastPort` | `subscribe` 를 아예 안 적었다 |
| ② | `ChamberProgressBroadcaster.subscribe` | `-> AsyncIterator[...]` 라 적어, **자기가 실제로 돌려주는** `_SubscriptionScope`(=`__aenter__` 보유)를 숨겼다 |
| ③ | `platform_routes` WS 핸들러 | 포트로도 구상 클래스로도 `async with` 를 정당화할 수 없어 `cast` 로 둘을 건너뛰었다 |

**②를 그냥 두면 ①만 고쳐도 `cast` 를 못 걷는다** — 포트가 `subscribe` 를 적어도 구현이
컨텍스트 매니저가 아니라고 «스스로» 말하기 때문이다. 세 갈래(포트 확장 / 읽기 포트
분리 / 어댑터 2인자) 중 무엇을 고르든 ②는 따로 고쳐야 했다.

## 판정: 포트를 넓힌다 (갈래 ①)

전제였던 「publish-only」의 근거를 먼저 찾으라는 지시가 있었다. **근거가 없었다 —
있던 것은 거짓 사실 진술 하나다.**

- 포트 docstring 은 *"subscribe 는 WS driving adapter 가 인프라 broadcaster 에 **직접
  접근**"* 이라 적고 그로부터 「좁은 의존이 도메인 경계를 publish-only 로 유지한다」를
  끌어낸다. 그 진술은 **태어난 날부터 거짓**이다: 이 릴레이를 만든 커밋(모노레포
  `0f2aaeef`, 2026-06-18)의 WS 핸들러도 `broadcaster = adapter.progress_broadcaster`
  로, 즉 **포트 타입 프로퍼티**를 통해 받았다. 오늘 이 레포에서는 가능하지도 않다 —
  `fcc_test_platform.api` 층은 `infrastructure` 에서 이름을 **0개** import 한다(AST).
- ⚠️ 포트가 인용하는 **ADR-0015 는 포트 모양을 결정하지 않는다.** 172줄에서
  `publish|subscribe|port` 는 2회뿐이고 둘 다 *노드의 기존* 버스를 서술한다. 그 ADR 이
  정한 것은 전송 **방향**(노드 push vs 중앙 pull)이다.

넓히는 쪽을 고른 나머지 근거(실측):

- **이 레인의 규칙은 「능력 하나에 포트 하나」가 아니다.** 28개 포트 모듈 실측:
  Read/Write 로 갈린 쌍은 **전부 구현 클래스가 둘**인 경우다(reference · project ·
  equipment · chamber · report · sample-inventory). 한 객체가 받치는 포트는 이질적
  능력을 함께 담는다 — `PlatformIngestionTransaction`(upsert+commit+rollback) ·
  `CentralResultSelectionPort`(list+append) · `CentralChamberWritePort` 는 이름이
  Write 인데 `read_*` 를 둘 담는다. 진행 버스는 **한 객체**다.
- 이 구현이 스스로 「복제」라 적는 원본 `SessionEventPort`(노드 레인)는
  publish + subscribe + **dispose** 셋을 함께 선언한다. 구조가 같은 두 버스가 반대
  모양의 포트를 가질 이유는 위의 거짓 문장뿐이었다.

⚠️ **읽기 포트 분리(갈래 ②)를 버린 이유**가 곧 위 세 번째 항목이다. 가르면 조립
루트가 같은 객체를 두 인자로 넘기게 되는데(그래서 갈래 ②는 사실상 갈래 ③를
동반한다), 이 레인에 그런 선례가 **없다** — 갈린 포트 쌍은 언제나 구현도 갈려 있다.

## 적합성을 «검사되게» 만든 자리

포트를 넓히면 「구현이 정말 그 모양인가」를 누가 검사하는가가 새 질문이 된다.
답: 아무도 안 했다 — 구현이 포트 타입을 만나는 자리는 조립 루트뿐인데
`fcc_test_platform.api_composition` 은 `mypy.ini` 의 네 strict 절 어디에도 매치되지
않는다(게이트는 그 모듈에 mypy 를 부르지 않는다).

그래서 구현이 Port Protocol 을 **명시 상속**한다 — 이 레인의 선례 5/34
(`PostgresCentralProjectReferenceAdapter` 외). ⚠️ 이 선택은 취향이 아니다:
`runtime_checkable` 의 `isinstance` 는 메서드 **존재**만 보므로 이번 결함(②, 반환
타입 좁힘)을 **원리적으로** 못 잡는다.

## Verification

- 리그: 전용 venv + `pip install -e '.[test]'`(CI `checks.yml:90` 과 같은 줄).
  ⚠️ 리그가 답을 바꾼다는 것이 직전 웨이브의 값비싼 교훈이라 여기 적는다.
- ⚠️ **두 번 쟀다.** 작업 중 `origin/main` 이 `0da711d` → `85c43a4` 로 움직였고
  (#133 타입 53건 · #134 형제 핀 v0.1.12), 그 판올림이 **선언된 핀을 바꿨다**
  (contracts v0.1.22 → v0.1.23). 그래서 재기저 후 venv 를 **새로 만들어** 선언대로
  다시 설치하고 전량을 다시 쟀다 — 낡은 리그의 초록을 재기저 뒤에 그대로 인용하면
  「무엇 위에서 쟀나」가 거짓이 된다.

  | 기저 | 설치 | 대조군(base) | 이 브랜치 | mypy 네 팩 |
  |---|---|---|---|---|
  | `0da711d` | contracts 0.1.22 | 3277 passed · 0 failed | 3279 passed · 0 failed | 전량 Success |
  | `85c43a4` | contracts 0.1.23 | 3277 passed · 0 failed | 3279 passed · 0 failed | 전량 Success |

  두 기저에서 차분이 같다(+2 검사 · +2 subtest = 이번에 더한 것 전부).
  대조군은 «같은 트리·같은 venv» 를 그 커밋으로 되돌려 실행했고
  (`git diff origin/main --stat` 0줄로 확인), 네 번 다 exit 0 으로 **완주**했다.
- `mypy -p fcc_test_platform.{domain,infrastructure,application,api}` → 넷 다
  `Success` (54 · 11 · 75 · 2 파일). 변경 전후 동일.
- `scripts/lane_check.py` → exit 0, 「선언한 그대로」(위 표).
- `mypy -m fcc_test_platform.api_composition`(게이트 밖, 직접 호출): **3 → 2**.
  사라진 하나가 `"object" has no attribute "dispose"` 다 — 그 호출이 이제 선언에
  묶였다. 남은 둘은 이름이 같고 줄만 밀렸다(무관·기존).

### 주입으로 이빨 확인 (넣어 red → 복원 green → 소스 바이트 동일)

| 주입 | 기대 | 관측 |
|---|---|---|
| A. 구현의 `subscribe` 반환을 옛 `AsyncIterator` 로 되돌린다 | infrastructure red | `[override] Return type ... incompatible with ... ChamberProgressSubscription` |
| B. 포트에서 `subscribe` 선언을 뗀다 | api red | `"ChamberProgressBroadcastPort" has no attribute "subscribe"` |
| C. 구현에서 명시 상속을 뗀다 | 시험 red | `test_the_implementation_declares_the_port_explicitly` FAILED |
| F. `_SubscriptionScope.__aenter__` 를 뗀다 | 시험 red | `test_the_wired_broadcaster_speaks_the_relay_spelling` FAILED |

⚠️ **`dispose` 축에는 게이트가 없다.** 포트가 그것을 적고 조립 루트의 필드가 그
타입을 갖게 됐지만, 그 모듈이 strict 집합 밖이라 게이트는 부르지 않는다. 「선언은
했고 검사는 안 된다」를 초록으로 읽지 않도록 여기 적는다.

## 곁가지로 드러난 것 — 같은 형태의 거짓 진술 하나 더

`_PrincipalResolver` docstring 이 *"조립 루트가 strict 이므로 이 Protocol 은 그 호출
지점에서 검사된다"* 라고 적었다. **틀렸고, 이번에 정정했다.** 주입으로 확인:

1. 그 호출 자리에 `resolve` 없는 `object()` 를 넣어도 게이트의 네 팩 전량 `Success`.
2. `api_composition` 에 mypy 를 직접 불러도 오류 수가 그대로였다 — `create_router`
   가 **선언 없는 def** 라 본문을 건너뛰기 때문이다.
3. `--check-untyped-defs` 를 켜야 비로소 `[arg-type]` 이 나온다.

고치지 않고 **정정만** 했다: 고치려면 `create_router` 에 반환 선언을 붙여야 하고,
그러면 같은 본문의 `api_adapter: object` 도 함께 드러나 이번 웨이브의 범위를 넘는다.

## 후속

- `fcc_test_platform.api_composition` 은 strict 집합 밖이다. 그 모듈에는 지금 오류가
  둘 있고(위 Verification), 위 `_PrincipalResolver` 축도 거기서 검사된다. 소유 웨이브 미정.
- 설계서 §9(`platform_routes.py` 분해 여부)는 이번에도 **답하지 않았다** — 여전히
  다른 작업이 걸려 있지 않은 순수한 설계 질문이다.
