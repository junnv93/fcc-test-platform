# 핀이 «보이게» 만드는 것 — 넓은 선언 여덟과 진짜 결함 넷

날짜 2026-09-06 · 브랜치 `fix/object-subscript-and-kwargs-33-20260906` · 기저 `origin/main@0da711d`
claim `.claude/work-claims/mypy-widened-types-33-20260906.json`

## Why

형제 세션 `fcc-delivery-final-5c` 가 계약 핀을 올리고 있다
(`v0.1.22 → v0.1.25`, `kernel-v0.5.0 → kernel-v0.5.1`). 그 판올림이 담은 것 중
**동작을 하나도 바꾸지 않는 파일 둘**이 이 커밋의 전제다 —
`fcc_test_kernel/py.typed` 와 `fcc_test_contracts/py.typed`.

PEP 561: 그 마커가 없으면 mypy 는 설치된 그 패키지를 «미타입»으로 보고 import 전체를
`Any` 로 만든다. 그리고 이 레인의 `mypy.ini` 는 `ignore_missing_imports = True` 라
`[import-untyped]` 경고조차 안 뜬다. **아무 신호 없이 0건 검사된다.**

### 실측 — 트리를 고정하고 커널만 움직였다

`origin/main@0da711d` 무변경 · 선언대로 설치(fastapi·psycopg[binary]·bcrypt·`[test]` 전량) ·
mypy 2.3.1(`pyproject.toml:233` 이 `mypy>=2.3.1` 로 선언한 그것) ·
커널/계약은 로컬 태그 워크트리에서 `--no-deps` 설치.

| | 전체 패키지 | domain | infrastructure | application | api | **게이트 4층 합** |
|---|---:|---:|---:|---:|---:|---:|
| kernel **0.5.0** (py.typed 없음) | 65 | 0 | 0 | 0 | 0 | **0** |
| kernel **0.5.1** (py.typed 있음) | 177 | 0 | 1 | 69 | 9 | **79** |

🔴 **오늘 게이트는 «완전 초록»이고, 핀이 켜지는 순간 79 다.** 그 79는 새로 생긴 결함이
아니라 **줄곧 있었으나 아무도 안 본 것**이다. 이 레포가 반복해 만난
「안 봤다 = 위반 없다」의 가장 큰 사례다.

⚠️ **그리고 전체 65 중 네 층이 0 이라는 것은, 그 65가 전부 `fcc_test_platform/` 최상위
모듈에 산다는 뜻이다** — `STRICT_SECTIONS` 넷 중 어느 것도 그 자리를 안 본다.
층을 넷 다 켜도 그 파일들은 여전히 게이트 밖이다.

⚠️ **이 기계 어디에도 mypy 가 설치돼 있지 않다**(`/usr/bin/python3` · 공유 `.venv` ·
`~/.local/bin` 전수). `test_architecture_gate_conformance.py:442` 의 mypy 팔은
`find_spec('mypy') is None` 이면 **skip** 이고, `skipping` 은 초록과 같은 모양이다.
CI 는 `checks.yml:90` 의 `pip install -e '.[test]'` 로 mypy 를 얻으므로 **이빨이 있다** —
즉 「내 손에서는 초록인데 올리면 빨갛다」가 되고, 그 원인이 아무 데도 안 적혀 있다.

## What

핀은 **올리지 않는다**(그 두 줄과 `requirements-central.txt` 는 5c 의 것이다).
이 커밋은 그 핀이 켜졌을 때 빨개질 자리 중 **파일 겹침 0 인 몫**을 미리 닫는다.

8파일 · +147/−65 · **`cast` 0 · `# type: ignore` 0.**

| 파일 | 전 | 후 |
|---|---:|---:|
| `central_db_live_proof_cli.py` | 28 | **0** |
| `application/local_auth_service.py` | 10 | **0** |
| `api/platform_routes.py` | 9 | 5 |
| `check_auth_mode_pairing_cli.py` | 4 | **0** |
| `provider_identity_live_proof_cli.py` | 3 | 1 |
| `bench_project_result_selection_cli.py` | 3 | **0** |
| `application/published_plan_progress_glue.py` | 1 | **0** |
| `application/central_claim_write_service.py` | 1 | **0** |

### 갈래 넷 — 처방이 다르다

**① 선언이 「그 자리에서 이미 적힌 사실」을 버린다 (15건)**
`def _ingest() -> object:` 가 세 자리. 그런데 그 본문이 부르는
`execute_platform_ingestion_plan` 은 **바로 그 파일에서** `-> IngestionExecutionResult`
라고 선언한다. 넓혀진 것이 아니라 **손으로 적어 버린 것**이다. 되돌렸다.

**② 값 종류가 섞인 dict 리터럴이 합류한다 (12건)**
`state = {'postgresql_setting': str(...), 'fk_sample_id': {...}}` 는
`dict[str, Collection[str]]` 로 합류하고(`str` 과 `dict` 의 상한), 바깥 첨자가
`object`/`Collection[str]` 을 내놓아 안쪽 첨자에서 죽는다. **중첩 dict 를 이름 있는
지역변수로 올리고 «그 값»을 읽게 했다** — 같은 객체이므로 동작이 동일하고,
방금 만든 값을 컨테이너로 되읽는 우회가 사라진다.

**③ 선언이 모든 실제 호출자보다 넓다 (8건) — 세면 닫힌다**

| 선언 | 호출자 전수 | 처분 |
|---|---|---|
| `clock: Callable[[], object]` | 생산 `api_composition.py:772` + 시험 둘, **셋 다 `datetime`** | `Callable[[], datetime]` |
| `rate_limit_policy: object = None` | 생산 `config.rate_limit`(=`RateLimitPolicy`) + 시험 전수 `None`\|`RateLimitPolicy`. 소비자 셋 다 `RateLimitPolicy \| None` 요구 | `Optional[RateLimitPolicy]` |

🔴 **`clock` 을 좁혀야 하는 것은 스타일이 아니라 고장 예방이다.**
`is_lock_active` 는 총함수라 비-`datetime` 을 `(AttributeError, TypeError, ValueError)` 로
잡아 **`True`(=잠김)** 로 접는다(계약 레인 `login_throttle_policy.py:172`). 즉 오배선이
타입 오류가 아니라 **「전원 로그인 실패」**로 나타난다. 이 문장이 없으면 다음 사람이
이 좁힘을 정리로 읽고 되돌린다.

⚠️ `clock` 옆 주석의 *"시험은 가짜를 주입하므로 구체 클래스로 좁히는 것도 거짓이다"* 는
`store`·`hasher` 에 대한 말이고 `clock` 에는 해당하지 않았다. **산문을 읽고
「의도적이다」로 판정하면 틀린다 — 호출자를 세야 한다.**

**④ 사실이 두 문장에 흩어져 검사기가 볼 수 없다 (7건) — 한 자리로 모은다**

- `**common` 펼침 7건 → `_SharedTokenClaims(TypedDict)`. dict 를 두 호출부에 풀어
  적으면 **「두 토큰이 같은 값을 쓴다」는 불변식이 코드에서 사라진다.** 모양만 적었다.
- `_MIGRATION_FILE_PATTERN.match(...).group(...)` → 위 루프에서 이미 매치된 이름에
  정규식을 **다시** 돌리고 있었다. 매치를 버리지 않고 쌍으로 들고 나오게 했다
  (쌍 정렬로 `selected.sort()` 의 순서를 보존).
- `check_auth_mode_pairing_cli` 의 `None` 검사와 사용이 갈라져 있었다 → `for/else` 로
  한 자리에서 검사하고 그 자리에서 쓴다. ⚠️ 그 위 주석이 *"`None` 과 `''` 를 뭉개지
  마라"* 를 명시하므로 `or ''`·`str()` 로 접지 **않았다**.

### 진짜 결함 넷 (동작이 바뀔 수 있던 것)

1. **`published_plan_progress_glue.py:43`** — 커널이 `TestPlanRow.condition_hash` 를
   *"materialize 전에는 None"* 으로 두는데, 이 모듈의 전제(「materialized rows」)가
   **산문으로만** 있었다. 미materialize 행 하나면 `None` 이 진행률 ingest 의
   **조인 키**로 조용히 실려 간다(P6.2 봉인 대상). 전제를 실행 가능하게 만들었다.
2. **`provider_identity_live_proof_cli.py:96`** — `fetchone()[0]` 무검사.
3. **`check_auth_mode_pairing_cli.py:65`** — 폴백이 `WEB_AUTH_STRATEGIES = {}` 인데
   진짜 선언은 `frozenset` 이다. 비어 있음은 같지만 **종**이 다르다.
4. **`central_db_live_proof_cli.py` 의 익명 연결 람다** →
   `build_central_db_connection_factory` 재사용. 라이브 증명이 증명해야 하는 것은
   **생산 배선**이고, 따로 열면 증명 대상이 갈린다.

### 🔴 §8 의 거짓 이분법 — 설계서 판정이 뒤집힌다

설계서 §8 과 이 웨이브의 지시는 `platform_routes.py` 의 그 자리(`6b667d4:548-549`
= `0da711d:633-634`)를 *「fail-closed 를 지키려면 타입 검사를 포기해야 한다」* 로
판정하고 **무접촉 대상**으로 지정했다. 갈래를 둘만 봤다:

| 형태 | fail-closed | 검사 |
|---|:---:|:---:|
| ① `OPS.get(op) or {}` + `.get('permission')` | ✅ | ❌ |
| ② `OPS[op]` | ❌ | ✅ |
| ③ `OPS.get(op)` + `is None` 분기 + 첨자 | ✅ | ✅ |

**③ 을 아무도 안 봤다.** 이 커밋이 ③ 으로 바꿨고, 5c 가 오타(`permisson`)를 주입해
갈랐다 — ① 은 «못 잡고» ③ 은 `TypedDict "OperationSpec" has no key "permisson"` 로
잡는다. 부재 동작은 동일(`required = ''` → 차단).

⚠️ 같은 문장이 계약 레인 `access_policy.py:156` 의 docstring 에도 박혀 있다(PR #47).
**그 문장이 이제 틀렸다** — 별건이고 새 태그가 필요하다.

⚠️ `str()` 강제를 뺀 것이 동작을 바꾸는지 실측했다: 80 operation 전수에서
`permission` 은 **80/80 존재 · 80/80 `str`**. 어긋나는 값이 생기면 이제 커널의
`OperationSpec` 선언 자리에서 잡힌다.

## How

- **핀 무접촉.** `pyproject.toml`·`requirements-central.txt` 를 안 만졌다.
- **`cast`·`# type: ignore` 0.** 넓은 선언은 좁히고, 흩어진 사실은 모으고,
  진짜 부재는 죽게 했다.
- **일괄 치환 금지.** 5c 가 일괄 치환으로 산문까지 바꿔 거짓 문장을 만든 사례를
  공유해 줬다. 전부 자리별로 확인했다.
- **`platform_routes.py` 의 `548-549`(내 기저 `633-634`)** — 5c 의 무접촉 지시를
  **어겼다.** 내가 「무접촉」이라고 답할 때 *내 트리의* 줄 번호로 확인했고, 그 번호는
  다른 커밋의 것이었다. 결과적으로 그 변경은 옳았고 5c 가 유지를 요청했지만,
  **판정 과정은 틀렸다** — 줄 번호는 커밋 없이는 뜻이 없다.

## Verification

**rig 둘. 트리는 같고 커널만 다르다.**

```
                      전체   domain infra application api   게이트합
착수 전 · kernel 0.5.1  177     0      1      69          9      79
착수 후 · kernel 0.5.1  124     0      1      57          5      63    (−53 / 게이트 −16)
착수 전 · kernel 0.5.0   65     0      0       0          0       0
착수 후 · kernel 0.5.0   45     0      0       0          0       0    (−20 / 회귀 0)
```

- **전량 pytest (CI 조건 = 현재 핀)**: `3271 passed · 11 skipped · 6 errors · 856 subtests`,
  141초 완주. ⚠️ 그 6 errors 는 **손대지 않은 `origin/main` 대조군에서 동일**하다
  (`test_repository_artifacts_resolve_for_the_caller.py`, 대조군 `3 passed / 6 errors`).
- **영향 영역 8파일**: `216 passed · 98 subtests`.
- **새 커널 rig 의 게이트 red 는 제 브랜치만으로는 안 사라진다** — 남은 63 중
  `infrastructure` 1 · `application` 57 은 5c 의 20파일 되돌리기가 닫는다.
  **이 PR 의 초록은 무증거이고, 증거는 위 rig 실행이다.**

### 남긴 것 — 전부 계약 레인 선언이라 여기서 못 고친다

| 자리 | 왜 |
|---|---|
| `platform_routes.py:853-855` | `validate_project_result_reference_request -> dict[str, object]` (커널) |
| `platform_routes.py:1714` | `ChamberProgressBroadcastPort` 의 폭. **#132 claim 이 「포트 주인의 판정」으로 이미 미해결 등재** |
| `platform_routes.py:3804` | `apply_correlation_response_headers(MutableMapping[str,str])` 인데 `__setitem__` 만 쓴다. starlette `MutableHeaders` 는 `pop`·`popitem`·`clear` 가 없어 구조적으로 만족 못 함 — **선언이 «쓰임보다» 넓은** 형태 |
| `provider_identity_live_proof_cli.py:93` | `build_central_db_connection_factory` 의 반환이 아직 `DbConnection`. **5c 의 커밋이 `RowConnection` 으로 닫는다** |

셋 다 `cast` 로 막을 수 있었고, 막았으면 결함이 «보존»된 채 초록이 됐다.

## 후속

1. **5c 착지 뒤 재측정.** 그쪽 20파일이 `application` 57 과 `infrastructure` 1 을 닫으면
   게이트가 몇으로 내려가는지가 「핀을 켜도 되는가」의 답이다.
2. **계약 레인 별건 셋** — `access_policy.py:156` docstring 정정(§8 이분법),
   `apply_correlation_response_headers` 파라미터를 «쓰는 것만» 요구하도록 좁힘,
   `DbCursor.execute`/`rowcount` 를 psycopg 실물에 맞춤(5c 가 레인 쪽에 우회를 두고
   「창이 열리면 지워라」를 적어 뒀다).
3. 🔴 **최상위 모듈이 게이트 밖이다.** 오늘 옛 핀에서도 45건이 거기 있고 아무도 안 본다.
   `STRICT_SECTIONS` 는 패키지 넷만 돈다 — 최상위 모듈을 어떻게 넣을지는 별도 판정이다.
4. 🔴 **로컬에 mypy 가 없어 `pre-push` 가 조용히 통과한다.** CI 는 잡는다. 그 갈라짐을
   어디에도 안 적어 두면 다음 사람이 같은 자리에서 놀란다.
