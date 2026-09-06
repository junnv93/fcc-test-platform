# 두 번째 후보는 결함이 아니었다 — 그리고 무엇이 둘을 가르는가

**날짜**: 2026-09-06 · **닫는 것**: PR #124 가 *「결함으로 확인하지 않았다」* 고
이름으로 남긴 항목 하나 · **코드 수정 0**

PR #124 는 「입력은 주입되는데 한 축만 환경을 읽는」 봉인 하나를 고치면서, AST 로
후보 12건을 세고 그중 **같은 모양** 하나를 확인하지 않은 채 남겼다:

```
fcc_test_platform/download_proxy.py::resolve_download_grant   (now 주입 + is_file·stat 직접)
```

**판정: 결함이 아니다.** 아래가 근거이고, 마지막 절이 둘을 가르는 규칙이다.

## 왜 아닌가 — 넷을 쟀다

**① 파일 읽기가 전부 «주입된 좌표» 아래에서 일어난다.**

```python
def resolve_download_grant(*, grant, storage_roots: Mapping[str, Path | str], now): ...
    root = _storage_root(storage_roots, storage_backend)     # ← 호출자가 준다
    path = (root / relative_path).resolve()
    _require_within_root(path, root)                          # ← 그 밖으로 못 나간다
    if not path.is_file(): ...                                # ← 좌표는 주입됐다
    byte_size=path.stat().st_size
```

`storage_roots` 가 주입이고, `_require_within_root` 가 해소 경로를 그 뿌리 안으로
가둔다. **호출자가 좌표를 완전히 정한다.**

**② 그 시험이 실제로 파일을 만들어 쓴다.** `tests/test_platform_download_proxy.py` 는
`tmp_path` 를 5곳에서 쓰고 파일 생성이 5곳이다 — 좌표를 시험이 소유한다.

**③ 리그 넷에서 같은 답.** 트리 밖 설치(egg-info 없음) · `TMPDIR` 변경 · CI 형태
(egg-info 있음) · cwd 를 트리 밖으로 — 전부 `5 passed`.

**④ 같은 주입 · 다른 주변 환경에서 산출이 «바이트 동일».** 같은 tmp 뿌리 구조를 주고
`cwd`·`TMPDIR`·`HOME`·`PYTHONHASHSEED` 를 바꿔 세 번 호출했다. `byte_size` ·
`media_type` · `headers` · `sha256` · 뿌리 포함 여부 전부 동일.

## 무엇이 둘을 가르는가 — **좌표가 주입되는가**

| | `_is_editable(name)` (PR #124 가 고친 것) | `resolve_download_grant` |
|---|---|---|
| 무엇을 읽나 | `importlib.metadata` 가 **`sys.path` 를 훑는다** | 파일시스템 |
| 좌표는 | **없다** — 주변 환경이 답을 정한다 | **주입된다**(`storage_roots`) |
| 시험이 통제하나 | ❌ 못 한다 — 개발자의 «설치 방식»이 샌다 | ✅ `tmp_path` 로 완전히 |
| 판정이 리그에 따라 | **갈린다**(3-way 실측) | **안 갈린다**(4-rig 실측) |

> **환경을 읽는 것이 결함이 아니다. 시험이 통제할 수 없는 좌표에서 읽는 것이 결함이다.**

⚠️ 그래서 PR #124 의 검출기가 남긴 다른 행들도 같은 물음으로 갈아야 한다. 예를 들어
같은 파일의 `_ships_init_py(name, roots[d])` 는 파일시스템을 읽지만 `roots` 가
주입이라 이 규칙으로 **무해**다. 검출기는 «모양»을 세고, 결함 여부는 이 물음이 답한다.

## 이 판정이 남기는 것

- 후보 12건 중 **실측 결함 1건**(PR #124 에서 수리) · **무해 확정 1건**(이 문서).
- 남은 10건은 clock 주입 + 사설 헬퍼 경유라 그 검출기로 갈리지 않는다. 갈리려면
  헬퍼 안까지 따라가는 검출기가 필요하고, 그것은 이 웨이브가 만들지 않았다.
- ⚠️ **없는 결함을 「고쳤다」로 만들지 않았다.** 이 문서가 그 판정의 전부다.

## 재현
```
python3 -m venv rig && rig/bin/pip install -e '<worktree>[test]'
rig/bin/pytest -q -p no:randomly tests/test_platform_download_proxy.py     # 리그를 바꿔 가며
# ④ 는 같은 tmp 뿌리를 주고 cwd·TMPDIR·HOME·PYTHONHASHSEED 를 바꿔 세 번 호출해 산출을 대조
```
기준 커밋 `5c7c8ae`(당시 `origin/main`).
