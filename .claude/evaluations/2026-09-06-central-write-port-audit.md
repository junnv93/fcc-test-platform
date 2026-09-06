# central write 어댑터 11개 strict — **포트 감사 보고** (2026-09-06)

기준 `84f2955` + PR #112 · mypy 2.3.1 · 선언대로 설치한 venv

이 웨이브의 산출물은 「선언 59건」이 아니라 **포트 감사 보고**다. PR #112 가 실증한
것을 축으로 삼았다: **타입을 정확히 적는 순간 포트 계약이 검사 대상이 된다.**

## 실측 0 — 장부가 «모듈»을 못 받는 상태였다

PR #109 가 만든 `STRICT_PACKAGES` 는 `section[:-len('.*')]` 로 **무조건** 끝 두 글자를
잘랐다. 패키지 글롭만 받는 형태다. 모듈 절을 넣으면 이름이
`…central_user_write_adapt` 로 잘린다.

⚠️ **그 실패는 조용하지 않다**(실측): `mypy -p <없는 이름>` 은 **exit 2** 이고
「N source files」 줄도 없다 — PR #109 가 세운 두 검사(종료코드 · 증거 먼저)가 각각
잡는다. 그래도 장부가 모듈을 **받을 수 있어야** 다음 묶음을 자를 수 있으므로 형태를
고쳤다: `.*` 로 끝나면 패키지(`-p`), 아니면 모듈(`-m`)이다. 실행 팔은 그 쌍에서 파생한다.

## 실측 1 — 59건은 전부 `cursor`·`connection` 이었다

11파일 전수로 「이 레인이 커서·연결에 무엇을 요구하는가」를 셌다:

    cursor.execute   66회      connection.cursor   15회
    cursor.fetchall  23회      connection.commit   21회
    cursor.close     13회      connection.rollback  3회
    cursor.fetchone  10회      connection.close     1회
    cursor.rowcount   2회

커널 `DbCursor` 는 `rowcount` · `execute` · `close` 를 약속한다 — 이 레인은
`fetchall` · `fetchone` 을 **더** 요구한다. 그 「더」를 `application/central_db_surfaces.py`
**한 곳**에 적었다(11개 사본을 두면 그중 하나가 조용히 갈린다).

⚠️ 여기서 **없는 결함을 보고할 뻔했다.** 어댑터 주석이 *"`rowcount` is declared by the
`DbCursor` port Protocol"* 이라고 적기에 「포트에 없는 것을 사칭한다」고 의심했는데,
커널 포트는 `rowcount: int` 를 **속성으로** 선언한다 — 내 grep 이 메서드만 봐서 놓친
것이다. 주석이 옳았다.

## 실측 2 — 트랜잭션 러너 8개가 «아무거나 넘긴다»고 약속하고 있었다

본문의 인자를 `RowCursor` 로 적자 **23건의 새 오류**가 한 형태로 나왔다:

    def _in_transaction(self, body: Callable[[object], …])

이 러너는 언제나 **커서**를 넘긴다. `Callable` 은 인자에 대해 **반변**이므로, 커서를
받는 본문은 `object` 를 받는 자리에 들어갈 수 없다 — 그 거짓이 그 순간 드러났다.
여덟(+ claim 의 `_run_once`)을 `Callable[[RowCursor], _T]) -> _T` 로 고쳤다.

**이것이 「선언 작업」이 아니라는 증거다.** 59건을 적었더니 23건이 새로 나왔고, 그중
하나도 「주석 누락」이 아니었다.

## 실측 3 — 포트가 «이미 선언한» 타입을 어댑터가 안 적고 있었다

| 자리 | 어댑터 | 포트 |
|---|---|---|
| `update_chamber_storage_root(artifact_storage_root)` | 미주석 | `Optional[str]` (PR #112) |
| `update_chamber_web_session_approval(accepts_web_sessions)` | 미주석 | `Optional[bool]` (PR #112) |
| `update_candidate_rows(additions, removals)` | 미주석 | `Sequence[Mapping[str, object]]` · `Sequence[str]` |

타입을 발명하지 않았다 — **포트에서 베꼈다.** 방향이 생긴 것이 이 웨이브의 값이다.

## 실측 4 — 포트가 준 타입이 «조용한 오기» 하나를 드러냈다

`update_candidate_rows` 의 `additions` 를 포트대로 `Mapping[str, object]` 로 적자:

    json.dumps(list(entry.get('test_condition_ids') or []))
    → No overload variant of "list" matches argument type "object"

들여다보니 **문자열이 오면 글자 단위로 쪼갠다** — 조건 하나를 세 개로 만드는 조용한
오기다. `_condition_ids()` 로 옮겨 문자열을 이름으로 거절한다(None/빈 값은 옛 동작 그대로).

## 실측 5 — `_fetchone` 은 «Mapping 또는 원시 튜플»이었다

`central_sample_inventory_write_adapter._fetchone` 은 드라이버가 `description` 을 주지
않고 호출자가 `columns` 도 안 주면 **행 튜플을 그대로** 돌려준다. 좁혀서 언제나
`Mapping` 이라고 적으면 거짓이고, 넓게 적으면 8개 호출부가 빨개진다.

본문을 보면 계약이 정확하다: **`columns` 를 주면 마지막 `return row` 에 도달할 수 없다.**
`@overload` 로 그 사실을 적었다 — 동작은 한 줄도 바뀌지 않는다. 그리고 `_next_revision`
은 실제로 `isinstance(row, Mapping)` 으로 그 두 경우를 갈라 처리한다(저자는 알고 있었다).

## 실측 6 — **포트 감사 표** (이 웨이브의 산출물)

`domain/ports/output` 에 **파일 26개 · Protocol 33개**가 있다.
(⚠️ 착수 근거는 「포트 26개」라고 적었는데, 그것은 **파일** 수다. Protocol 로 세면 33개다.)

이 웨이브가 다루는 write 포트 **12개**의 상태:

| 포트 | 약속 메서드 | `@runtime_checkable` | 적합성 단언 |
|---|---:|---|---|
| ArtifactCustody | 1 | ✅ | **없음** |
| Audit | 1 | ✅ | 있음 |
| Chamber | **7** | ✅ | 있음 |
| Claim | 2 | ✅ | 있음 |
| Membership | 2 | ✅ | **없음** |
| Progress | 2 | **❌** | **없음** |
| Project | 4 | ✅ | 있음 |
| Reference | 5 | ✅ | 있음 |
| Report | 1 | ✅ | 있음 |
| SampleInventory | 6 | **❌** | **없음** |
| TestEquipmentList | 4 | ✅ | 있음 |
| User | 1 | ✅ | **없음** |

    적합성 단언이 있는 포트   6 / 11 (Audit 제외 · 이번 strict 대상 기준)
    @runtime_checkable       9 / 11
    감사된 포트 / 전체        12 / 33

⚠️ 둘(`Progress` · `SampleInventory`)은 `@runtime_checkable` 이 아니라
`isinstance` 로 **잴 수조차 없다.**

⚠️ `CentralSampleInventoryWritePort` 는 그 어댑터 모듈이 **이름조차 언급하지 않는다**
(실측: 그 파일에 `Central…Port` 문자열 0건). 오늘은 여섯 메서드가 정확히 맞아 구조적으로
만족하지만 **둘을 잇는 것이 코드에 없다** — 포트가 일곱 번째를 얻는 날(챔버 포트가 방금
그랬듯) 아무 데서도 소리가 나지 않는다.

## 그 구멍을 봉인했다

`tests/test_write_port_conformance.py` — 12쌍 **전부**에 대해 「포트가 약속한 이름이
어댑터에 있는가」를 **메서드 집합**으로 대조한다. 인스턴스도 `@runtime_checkable` 도
필요 없으므로 위 두 예외를 포함해 전부 덮는다.

⚠️ 짝은 **클래스 이름**에서 파생한다. 모듈 이름으로 지으면 5/12 가 조용히 빠진다
(실측: 포트 모듈 이름이 균일하지 않다).

주입 확인: 포트에 어댑터가 갖지 않은 메서드를 하나 더하자 —
`central_chamber_write_adapter: PostgresCentralChamberWriteAdapter 에 없는 것
['purge_chamber_history']`.

## 수 회계 (같은 rig, 전후)

| | 전 | 후 |
|---|---:|---:|
| mypy 11모듈 strict | **59** | **0** |
| domain · infrastructure · session · headless | 0 · 0 · 0 · 0 | 0 · 0 · 0 · 0 (회귀 없음) |
| `lane_check` exit | 0 | 0 |
| passed | 3,213 | **3,216** (+3 = 새 봉인) |
| skipped | **27** | **27** (여섯 웨이브 내내 불변) |
| subtests | 838 | **861** (+23 = 새 게이트 대상 11 + 봉인) |

## 남은 것

`application` 잔여 **86건** — read 어댑터 13 · central 기타 21 · 비-central 52.
포트 33개 중 **21개가 아직 감사되지 않았다.**
