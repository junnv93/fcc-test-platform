# mypy strict 를 infrastructure 로 한 층 — 20건의 실측 (2026-09-06)

기준 `4ebd580` · mypy 2.3.1 · 선언대로 설치한 venv · `QT_QPA_PLATFORM=offscreen`

## 왜 이 층인가 — 비용을 먼저 쟀다

PR #97 이 남긴 문장이 착수 근거다: *"mypy.ini 는 범위가 `fcc_test_platform.domain.*` 라
tests/ 도 apps/web/scripts/ 도 사정권 밖이다"*. **범위 밖이 결함이 숨는 자리**라는 것이
그 PR 에서 실증됐다(F821 셋 전부 범위 밖).

층별 비용(`--disallow-untyped-defs`):

| 층 | 오류 | 파일 |
|---|---:|---:|
| domain | 0 | — (이미 strict) |
| **infrastructure** | **20** | **3** |
| application | 189 | 36 |
| api | 133 | 1 |

infrastructure 가 압도적으로 싸다. 그리고 api 의 133건은 `platform_routes.py` **단일
파일**에 몰려 있고 그 파일을 분해할지 조립 산출물로 둘지는 설계서 §9 의 미해결 질문이라,
지금 strict 를 켜면 그 판정을 선점하게 된다 — 그래서 이번 웨이브는 한 층이다.

## 실측 1 — [index] 3건은 «선언 누락»이 아니라 저자가 알고 있던 사실이었다

`central_project_reference_adapter.py` 의 세 자리는 전부 `revision` / `next_revision`
이고 출처가 같다:

    SELECT COALESCE(MAX(revision_number), 0) + 1 AS revision_number
    FROM project_result_reference_revisions WHERE ...

같은 파일의 `source` · `target` · `row` 는 **전부 `is None` 가드를 갖는다.** 가드가 없는
셋만 집계 SQL 이다. GROUP BY 가 없는 집계는 대상 행이 0개여도 **언제나 정확히 한 행**을
돌려주므로 — **저자가 빼먹은 것이 아니라 알고 있었다.**

⚠️ 그래서 처분은 「가드를 흩뿌리기」가 **아니다.** 세 자리에 `if x is None: raise` 를
넣으면 타입은 조용해지지만 읽는 사람에게는 *「여기도 빈손일 수 있다」* 는 **거짓말**이
남고, 다음 사람은 그 가드를 보고 「집계도 빈손일 수 있구나」로 배운다.

처분: `_fetch_exactly_one` 을 두어 그 믿음을 **이름**으로 적었다.

## 실측 2 — 그 처분이 «Optional 만 숨긴 것»이 아님을 주입으로 갈랐다

집계 질의가 행 0개를 내도록 주입(착지 확인: `fetchall() -> []`):

| | 무슨 일이 일어나나 |
|---|---|
| 처분 **전** (`_fetch_one`) | 반환 `None` — **그 자리에서는 아무 일도 안 일어난다.** 호출부에서 `TypeError: 'NoneType' object is not subscriptable` — **어느 질의였는지 남지 않는다.** |
| 처분 **후** (`_fetch_exactly_one`) | `CentralProjectReferenceError: aggregate query must return exactly one row, got 0: SELECT COALESCE(MAX(revision_number), 0) + 1 …` |

「아무 일도 안 일어나면 Optional 만 숨긴 것」이라는 판정 기준을 그대로 통과한다 —
**전은 조용하고 후는 질의를 이름으로 댄다.**

## 실측 3 — 봉인은 오늘의 두 자리가 아니라 «내일의 세 번째 자리»를 지킨다

`tests/test_aggregate_read_arity_invariant.py` 가 셋을 묻는다:

1. 빈 결과 → 이름을 가진 오류이고 **메시지가 질의를 담는가**
2. 두 행 → 그것도 오류인가 (「하나 이상」이 아니라 **「정확히 하나」**)
3. **드리프트** — 모듈을 AST 로 훑어 `MAX(` 를 담은 SQL 이 느슨한 `_fetch_one` 에
   넘어가면 red

3번이 없으면 이 봉인은 오늘의 두 자리만 지키고 내일 추가되는 세 번째는 다시 조용해진다.
주입으로 확인했다 — 집계 SQL 을 `_fetch_one` 으로 조회하는 자리를 새로 만들자
`409행: SELECT COALESCE(MAX(revision_number)…` 로 red.

## 실측 4 — 정확한 타입이 «커널 포트가 부족하다»는 것을 드러냈다

17건의 `no-untyped-def` 중 7건이 `cursor` 인자였다. 커널의 `DbCursor` 는 `execute` 와
`close` 만 약속하는데 **이 어댑터는 `fetchall` 도 쓴다.** `Any` 로 덮으면 그 요구가
사라지므로, 모듈 안에 요구를 적었다:

    class _RowCursor(DbCursor, Protocol):      # fetchall 을 «더» 요구한다
    class _RowConnection(DbConnection, Protocol):   # 그 커서를 내주는 연결

⚠️ 둘 다 커널 포트를 **상속**한다. 새로 쓰면 「커널과 무관한 무언가를 요구한다」로
읽히지만 사실은 **포트가 약속한 것 전부에 더해** 읽을 수 있는 커서를 요구하는 것이다.

⚠️ 그리고 **요구를 층마다 좁혔다**: `_serializable` 과 `_lock_reference_identity` 는
행을 읽지 않으므로 `_RowCursor` 가 아니라 `DbCursor` 다. 필요보다 넓은 타입을 적으면
「이 함수가 결과를 본다」는 거짓이 남는다.

생성자 주석도 `Callable[[], DbConnection]` → `Callable[[], _RowConnection]` 으로 옮겼다 —
그 요구를 호출자에게 말하는 자리가 거기뿐이다. 이 한 줄이 `DbConnection` import 를
미사용으로 만들 뻔했고(ruff F401 1건), 지우는 대신 **상속으로** 살렸다.

openpyxl 은 `py.typed` 를 싣지 않는다(실측). 그래서 스타일 목록의 원소는 `Any` 일 수밖에
없지만, **`Any` 라고 «적는» 것과 주석을 비워 두는 것은 다르다** — 목록이라는 것과 높이가
없을 수 있다는 것은 여기서 확정된다.

## 실측 5 — 장부가 문자열 하나였고, 그 형태가 잘못된 유인을 만든다

`tests/test_architecture_gate_conformance.py` 의
`STRICT_SECTION = 'mypy-fcc_test_platform.domain.*'` **문자열 하나**가 「domain 만 strict」
를 봉인했다. 범위를 넓히면 그 검사가 빨개지는데, 그 red 는 회귀가 아니라 **「장부를 같이
고치라」**는 신호다.

⚠️ 그런데 문자열 하나짜리 장부는 그 신호를 **「고쳐야 할 검사」로 보이게** 만들어, 다음
사람이 범위를 넓히는 대신 검사를 되돌리도록 유도한다. 집합(`STRICT_SECTIONS`)으로 두면
층을 더하는 일이 **한 줄 추가**가 되고 그 한 줄이 곧 선언이다.

그리고 실행 팔도 함께 고쳤다. 옛 팔은 `domain` 만 돌렸다 — 층을 더해도 **초록인 채로 새
층을 안 본다.** 이제 층 목록을 `STRICT_PACKAGES` 에서 **파생**해 전부 돈다(손으로 두 번
적으면 갈라진다).

주입 확인: infrastructure 에 주석 없는 함수를 넣자
`AssertionError: fcc_test_platform.infrastructure strict 가 깨졌다` — **층 이름을 댄다.**

## 수 회계 (같은 rig, 전후)

| | 전 | 후 |
|---|---:|---:|
| mypy `infrastructure` (strict) | **20** | **0** |
| mypy `domain` | 0 | 0 (회귀 없음) |
| import-linter | 3 kept | 3 kept |
| ruff F401 (infrastructure) | 0 | 0 |
| `lane_check` exit | 0 | 0 |
| passed | 3,181 | **3,185** (+4 = 새 봉인) |
| skipped | **27** | **27** (네 웨이브 내내 불변) |
| subtests | 828 | **832** (+4 = 층별 subTest) |

## 남은 것

application(189) · api(133) 은 이번 웨이브가 아니다. api 는 `platform_routes.py` 단일
3,670줄이고 설계서 §9 의 미해결 질문을 선점하게 된다 — mypy.ini 주석에 그 사유를 적었다.
S11 ①단계 잔여(F401 68 등)도 성격이 달라 섞지 않았다.
