"""이 레인의 central DB 어댑터들이 **실제로 요구하는** 커서·연결 표면.

커널의 ``DbCursor`` 는 ``rowcount`` · ``execute`` · ``close`` 를, ``DbConnection`` 은
``cursor`` · ``commit`` · ``rollback`` 을 약속한다. 그런데 이 레인의 write 어댑터 11개는
**결과 행을 읽는다** — 실측(2026-09-06, 그 11파일 전수):

    cursor.execute   66회      connection.cursor   15회
    cursor.fetchall  23회      connection.commit   21회
    cursor.close     13회      connection.rollback  3회
    cursor.fetchone  10회      connection.close     1회
    cursor.rowcount   2회

즉 커널 포트가 약속하지 않는 ``fetchall`` · ``fetchone`` 을 **더** 요구한다. 그 「더」를
적지 않고 인자를 ``Any`` 로 두면 타입이 이 레인의 요구를 말하지 않게 되고, 커서 대역을
만드는 사람은 그 답을 어댑터 11개를 읽어서 알아내야 한다.

⚠️ **모듈마다 사본을 두지 않는다.** 같은 표면을 11번 선언하면 그중 하나가 조용히 갈리는
날이 온다 — 그때 「어느 것이 진짜 요구인가」를 아무도 답할 수 없다. 여기가 그 한 곳이다.

⚠️ **커널 포트를 상속한다.** 새로 쓰면 「이 레인은 커널과 무관한 것을 요구한다」로
읽히지만, 사실은 **그 포트가 약속한 것 전부에 더해** 읽기를 요구하는 것이다.

⚠️ ``close`` 는 ``RowConnection`` 에 넣지 **않는다.** 실측상 연결을 닫는 자리는 한
군데뿐이고 거기서도 ``getattr`` 로 「있으면 닫는」다 — 필수 표면과 선택 표면을 한 자리에
섞으면 그 구분이 사라진다(같은 이유로 커서의 ``description`` 도 여기 없다).
"""
from __future__ import annotations

from types import TracebackType
from typing import Any, Optional, Protocol, Sequence

from fcc_test_kernel.domain.ports.output.platform_database_port import (
    DbConnection,
    DbCursor,
)

__all__ = ['RowCursor', 'RowConnection', 'ScriptCursor', 'ScriptConnection',
           'require_row']


class RowCursor(DbCursor, Protocol):
    """결과 행을 읽을 수 있는 커서 — 커널 ``DbCursor`` + 읽기 둘.

    ✅ **여기 있던 덮어쓰기 둘은 지워졌다** (2026-09-07, `kernel-v0.5.4`).

    한때 이 클래스는 커널 포트의 `execute`·`rowcount` 를 `# type: ignore[override]`
    로 덮어썼다. 커널이 `Any` 로 보이던 동안 아무도 「이 포트가 실제 드라이버를
    기술하는가」를 묻지 못했고, `py.typed` 를 켜고 물어보니 psycopg 이 커널 포트를
    만족하지 못했기 때문이다::

        execute    커널: (str, tuple) -> None      psycopg: (…) -> Cursor
        rowcount   커널: 설정 가능 변수             psycopg: 읽기 전용 property

    그 덮어쓰기에는 *「이것은 우회이지 수리가 아니다 — 창이 열리면 지워라, 지워도
    초록이면 상류가 고쳐진 것이다」* 가 적혀 있었다. `kernel-v0.5.4` 가 그 두 줄을
    고쳤고(반환을 `object` 로, 인자를 위치 전용으로, `rowcount` 를 읽기 전용
    property 로), **지웠더니 초록이었다.** 그 문장이 자기 조건을 스스로 충족했다.

    ⚠️ 그러므로 이제 이 클래스가 더하는 것은 **읽기 둘뿐이다.** 다시 커널 멤버를
    덮어쓰고 싶어지면, 그것은 상류가 또 드라이버를 잘못 적었다는 신호이지 이 레인이
    특별하다는 뜻이 아니다 — 위와 같이 «지울 조건»을 적어 두고 상류를 고쳐라.
    """

    def fetchall(self) -> Sequence: ...

    def fetchone(self) -> Optional[Sequence]: ...


class RowConnection(DbConnection, Protocol):
    """그런 커서를 내주는 연결 — 반환 타입만 좁힌다."""

    def cursor(self) -> RowCursor: ...


# ── 스크립트 표면 (2026-09-07) ────────────────────────────────────────────────
#
# ⚠️ **왜 위의 둘을 «넓히지» 않고 새로 두는가.**
#
# 위 `RowConnection`/`RowCursor` 는 이 레인의 **write 어댑터 11개**가 요구하는 표면이고,
# 이 파일의 머리말이 *「필수 표면과 선택 표면을 한 자리에 섞으면 그 구분이 사라진다」*
# 며 `close` 를 일부러 뺐다고 적는다. 거기에 넷을 더하면 그 어댑터들의 **시험 대역**이
# 전부 쓰지도 않는 넷을 구현해야 한다 — 넓히는 것이 곧 «지금 지켜지는 것»을 무르는 일이
# 된다.
#
# ⚠️ **그래도 «파일»은 나누지 않는다.** 같은 머리말이 *「모듈마다 사본을 두지 않는다 …
# 여기가 그 한 곳이다」* 라고 적는다. 새 표면도 그 한 곳에 둔다.
#
# ── 요구 표면은 «파생»했다 (실측 2026-09-07, AST 로 22파일 전수)
#
#     connection.cursor 65 · commit 26 · close 13 · rollback 9 · autocommit 4
#     cursor.execute 130 · fetchone 45 · fetchall 17 · close 8 · executemany 6
#            · rowcount 6 · description 2
#     with connection.cursor() 57회 · with _connect(dsn) 20회
#
# 위 둘이 «약속하지 않는» 것은 정확히 넷이다 —
# `connection.close` · `connection.autocommit` · `cursor.description` ·
# `cursor.executemany` — 그리고 **컨텍스트 매니저**. write 어댑터들은 `with` 를 한 번도
# 쓰지 않는다(실측 0건). 즉 이것은 「더 큰 같은 것」이 아니라 **다른 표면**이다.


class ScriptCursor(RowCursor, Protocol):
    """CLI·증거 스크립트가 요구하는 커서 — `RowCursor` + 넷.

    ⚠️ **`execute` 를 여기서 «넓힌다».** 커널 포트는 `parameters` 를 필수로 적는데,
    그 근거는 *「호출 66곳 전부 위치 인자」* 라는 **어댑터 쪽 실측**이다. 스크립트
    쪽은 다르다 — `cursor.execute('SELECT 1')` 처럼 파라미터 없는 DDL·상수 질의가
    있다. 기본값을 주는 것은 인자를 «더» 받는 것이므로 하위 타입으로서 정당하고,
    좁은 포트를 받는 기존 소비자는 그대로 돈다(실측: 둘 다 통과).

    ⚠️ `description` 을 **읽기 전용 property** 로 적는다. 위 `rowcount` 와 같은
    이유다 — 설정 가능 변수로 적으면 property 로 구현한 psycopg 이 탈락한다.
    """

    @property
    def description(self) -> Sequence[Sequence[Any]] | None: ...

    def execute(self, statement: str, parameters: tuple = ..., /) -> object: ...

    def executemany(self, statement: str, parameters: Sequence[tuple], /) -> object: ...

    def __enter__(self) -> 'ScriptCursor': ...

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc_val: BaseException | None,
                 exc_tb: TracebackType | None) -> Any: ...


class ScriptConnection(RowConnection, Protocol):
    """그런 커서를 내주는 연결 — `RowConnection` + `close`·`autocommit`·`with`."""

    autocommit: bool

    def cursor(self) -> ScriptCursor: ...

    def close(self) -> None: ...

    def __enter__(self) -> 'ScriptConnection': ...

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc_val: BaseException | None,
                 exc_tb: TracebackType | None) -> Any: ...


def require_row(cursor: RowCursor) -> Sequence:
    """바로 앞 질의가 **반드시 한 행을 준다**는 믿음에 이름을 붙인다.

    ■ 왜 이것이 있는가

    `fetchone()` 의 `Optional[Sequence]` 는 **맞는 선언**이다 — PEP 249 는 행이
    없으면 `None` 을 주라고 적는다. 그런데 이 레인의 스크립트에는 그 반환을 곧바로
    첨자하거나 풀어 쓰는 자리가 31곳 있었다(실측 2026-09-07)::

        ids['provider'] = str(cursor.fetchone()[0])
        database_name, server, port = cursor.fetchone()

    그 자리들이 «틀린» 것은 아니다. `SELECT current_database()` 는 언제나 한 행을
    준다. 그러나 바로 옆줄의 `SELECT id FROM providers WHERE provider_id = %s` 는
    **행이 없을 수 있고**, 그때 나오는 것은
    ``TypeError: 'NoneType' object is not subscriptable`` 이다 — 무엇을 찾다 실패했는지
    말하지 않는 예외다.

    ■ 왜 `assert` 나 `cast` 가 아닌가

    둘 다 「행이 반드시 있다」는 **주장을 지우기만** 하고 아무 데도 적지 않는다.
    이 레포는 그 형태에 이름을 붙여 두었다 — *가드를 흩뿌리면 그 앎이 거짓말이
    된다. 믿음에 이름을 붙여라.* 이 함수가 그 이름이다.

    ■ 바뀌는 것과 바뀌지 않는 것

    바뀌지 않는다: 행이 있으면 예전과 똑같이 그 행을 준다.
    바뀐다: 행이 없을 때 `TypeError` 대신 `LookupError` 가 나고 메시지가 원인을
    말한다. 실측 2026-09-07 — 이 31자리를 감싸는 좁은 예외 핸들러는 **없다**
    (`except TypeError` 0건, 넓은 `except Exception` 18건은 둘 다 잡는다).
    """
    row = cursor.fetchone()
    if row is None:
        raise LookupError(
            'query returned no row where exactly one was required '
            '(the traceback names the call site and therefore the query)')
    return row
