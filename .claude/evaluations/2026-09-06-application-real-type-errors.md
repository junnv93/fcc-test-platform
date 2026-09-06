# application 층의 «실제 타입 오류» 44건 — strict 를 켜기 전에 (2026-09-06)

기준 `84f2955` · mypy 2.3.1 · 선언대로 설치한 venv · `QT_QPA_PLATFORM=offscreen`

## 왜 이 순서인가

`application` 전체를 한 웨이브로 strict 로 켜면 189건이 딸려 오고, 그것은
explicit-files 규율과 충돌한다. 그런데 189건은 **성격이 둘로 갈린다**:

| 성격 | 건수 | strict 가 필요한가 |
|---|---:|---|
| 실제 타입 오류 | **44** | **아니다 — 오늘도 보고된다** |
| no-untyped-def(선언 누락) | 145 | 그렇다 |

44건은 strict 와 무관하게 mypy 가 이미 보고한다. 먼저 처분하면 남는 145건이 **순수
선언 작업**이 되어 다음 웨이브를 모듈 단위로 자를 수 있다.

## 실측 0 — 분할 방식에 제약이 하나 있다 (직접 쟀다)

mypy 모듈 패턴의 `*` 는 **점으로 구분된 성분 전체**만 대체한다:

    [mypy-…application.central_*]                      → 0건   ❌ 매치 안 됨
    [mypy-…application.central_chamber_write_adapter]  → 12건  ✅ 동작
    [mypy-…application.*]                              → 145건 ✅

즉 「`central_` 로 시작하는 어댑터군만」은 **표현할 수 없다.** 남은 145건 중 **93건**이
그 어댑터군에 있으므로, 다음 웨이브는 모듈 이름을 하나씩 나열해야 한다. 그 사실을
`mypy.ini` 주석에 남겼다 — 다음 사람이 같은 것을 다시 재지 않도록.

## 44건은 «네 가지» 성격이었다 — 섞어서 처분하지 않았다

### ① 선언되지 않은 기본값이 센티널로 흐른다 (arg-type 5)

    app_title=( read_text(env, …) or defaults['app_title'].default )

⚠️ **`Field.default` 는 「기본값」이 아니다.** 필드에 기본값이 선언돼 있지 않으면 그
자리에는 `dataclasses.MISSING` **객체**가 들어 있고 **예외 없이** `str` 필드로 흘러간다.

**주입으로 갈랐다.** `app_title` 을 맨 앞으로 옮기고 default 를 지운다(파이썬이 허용하는
배치다 — 기본값 없는 필드가 앞에 온다):

| | 무슨 일이 일어나나 |
|---|---|
| **전** `defaults[…].default` | **예외 0건.** `app_title = <dataclasses._MISSING_TYPE object at 0x7f…>` · 타입 `_MISSING_TYPE` |
| **후** `declared_default(…)` | `ValueError: field 'app_title' declares no default, so its fallback would be the dataclasses.MISSING sentinel` |

⚠️ **오늘 안 터지는 이유가 「우연」이라는 것도 쟀다.** 이 패턴으로 읽는 필드는 16개이고
그중 기본값 없는 필드는 0개다 — 두 목록이 우연히 포개져 있고 아무도 그 포개짐을 검사하지
않았다. 그리고 `HeadlessApiConfig.db_path` 는 **이미 기본값 없는 필드**다(맨 앞이라
정의는 통과한다). 오늘의 방어는 **필드 순서**에 얹혀 있다.

### ② 가드가 «있는데» 타입에 안 보이던 자리 (union-attr 24 + 일부)

22건이 한 파일에 있었고, 저자는 **이미 가드를 썼다**:

    equipment = (manifest.get('equipment')
                 if isinstance(manifest.get('equipment'), Mapping) else {})

같은 키를 **두 번** 읽는다. `isinstance` 는 첫 번째 호출의 결과를 좁히고 대입되는 것은
**두 번째 호출의 결과**다 — 런타임은 옳고 타입만 안 보였다. 한 번만 읽는 헬퍼
(`_mapping_at` · `_sequence_at`)로 그 가드를 **보이게** 만들었다.

⚠️ 감사 포트 건도 같은 계열이었다. `central_project_write_adapter.py:324` 의
`self._audit.append_event_in_transaction(...)` 은 감사 누락이 **아니다** —
294줄에 `if self._audit is None: raise` 가 이미 있다. 324줄이 306줄에서 정의된 **중첩
함수 안**이라 바깥의 좁힘이 클로저로 가지 않을 뿐이다. 좁힌 값을 지역 이름에 묶었다.

같은 처방을 `local_user_store.unlock_account`(가드 410줄 / 호출 450줄)와
`local_auth_service` 의 회전 되돌림에 적용했다. 후자는 저자가 *"claimed 를 묻지
_revocations is not None 만 묻지 않는다"* 고 **산문으로** 적어 둔 불변식이라,
「claim 한 목록」을 값으로 들어 그 산문을 **타입**으로 바꿨다.

### ③ 선언이 실제 요구보다 «좁던» 자리 (attr-defined 7 · override 1)

`CentralChamberWritePort`(Protocol)는 `register_chamber` · `append_heartbeat` **둘만**
약속하는데 서비스는 **일곱을** 부른다. 구체 어댑터는 다섯을 다 구현하지만, 포트를
만족하는 «다른» 구현체는 타입 검사를 통과하고 **런타임에 죽는다.** 다섯을 포트에
선언했다(시그니처는 발명하지 않고 서비스 호출부에서 그대로 읽었다).

`selected_source` 는 `Optional[Mapping]` 을 돌려준다고 적었는데 포트는
`Optional[SelectedSource]` 를 약속한다 — 구현이 포트보다 **적게** 약속하던 자리다.
실측: `SELECTED_SOURCE_COLUMNS` 25개와 `SelectedSource` 키 25개가 **정확히 일치**한다.
어댑터는 이미 그 키 집합을 런타임에 검사한다. 그래서 `cast` 를 쓰되, 그것을 떠받치는
**두 축**을 명시했다 — 그 런타임 가드와, 컬럼↔키 동등성을 재는 **새 봉인**.

`object` 로 선언돼 있던 두 협력자(`audit_writer` · `rotation_throttle`)도 여기 속한다.
`object` 는 「무엇을 갖춘 객체를 넣어야 하는가」를 코드에서 지운다. 요구하는 표면만
로컬 Protocol 로 적었다.

⚠️ 그 과정에서 **내 주석이 한 번 틀렸다.** `_RotationThrottle.charge` 의 반환을
「서비스는 None 인지만 본다」고 적었는데, 서비스는 `.retry_after_seconds` 와 `.limit` 를
실제로 읽는다. mypy 가 즉시 짚었고 정정했다 — `limit` 를 `object` 로 적었을 때도 같은
일이 일어나 `int` 로 좁혔다. **틀린 주석이 그 자리에서 빨개진 것이 이 웨이브의 값이다.**

### ④ 형이 맞지 않던 자리 (call-overload 2 · assignment 1 · arg-type 3)

* `int(value)` 에 `object` 가 오던 두 자리 — **조용히 0/기본값으로 접지 않는다.**
  세션 버전을 0 으로 접으면 비교가 「항상 일치」로 무너지고, TTL 을 기본값으로 접으면
  만료 판정이 틀린 기준을 쓴다. 옛 `int()` 도 그런 형에는 죽었다 — 무엇이 왔는지를
  메시지에 담아 죽게 했다.
* `last_exc: Optional[Exception] = exc.__cause__ or exc` — `__cause__` 는
  `BaseException` 이다. 좁혀 적은 것이 조용한 거짓이었다.
* `json.loads(raw)` 에 `object` — 무엇을 파싱할 수 있는지가 예외 처리기에 숨어 있었다.
* `start_measurement(base_url, **kwargs)` — `**dict` 확장은 포트의 키워드 표면을
  **하나도 검사하지 않는다.** 어댑터가 인자마다 `if X is not None:` 으로 body 를 만드는
  것을 확인하고(즉 키를 빼는 것과 None 을 넘기는 것이 **등가**), 명시 호출로 바꿨다 —
  노드가 받는 body 는 한 바이트도 달라지지 않고 검사만 되살아난다.

## ② session/headless 만 strict 로 켰다

두 하위 패키지는 strict 에서 **0건**이다(실측). 장부(`STRICT_SECTIONS`)와 실행 팔에
층을 더하는 것은 PR #109 가 만든 형태 그대로 **두 줄 추가**로 끝났다.

주입 확인: `application/session` 에 주석 없는 함수를 넣자
`AssertionError: fcc_test_platform.application.session strict 가 깨졌다` — **층 이름을 댄다.**

## 수 회계 (같은 rig, 전후)

| | 전 | 후 |
|---|---:|---:|
| mypy application 실제 타입 오류 | **44** | **0** |
| mypy domain · infrastructure · session · headless | 0 · 0 · — · — | 0 · 0 · **0** · **0** |
| ruff F821 | 0 | 0 |
| `lane_check` exit | 0 | 0 |
| passed | 3,205 | **3,213** (+8 = 새 봉인) |
| skipped | **27** | **27** (다섯 웨이브 내내 불변) |
| subtests | 832 | **838** (+6 = 층·설정별 subTest) |

## 남은 것

`application` 나머지 **145건**(31파일, 전부 선언 누락) · `api` 133건.
자르는 방법과 그 제약은 `mypy.ini` 주석에 수치로 적었다.
