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

from typing import Any, Optional, Protocol, Sequence

from fcc_test_kernel.domain.ports.output.platform_database_port import (
    DbConnection,
    DbCursor,
)

__all__ = ['RowCursor', 'RowConnection']


class RowCursor(DbCursor, Protocol):
    """결과 행을 읽을 수 있는 커서 — 커널 ``DbCursor`` + 읽기 둘.

    ⚠️ **커널 포트의 두 줄을 여기서 «바로잡는다»** (실측 2026-09-06).
    커널이 `Any` 로 보이던 동안 아무도 「이 포트가 실제 드라이버를 기술하는가」를
    묻지 못했다. `py.typed` 를 켜고 물어보니 psycopg 이 커널 포트를 만족하지 않는다::

        execute    커널: (str, tuple) -> None      psycopg: (…) -> Cursor
        rowcount   커널: 설정 가능 변수             psycopg: 읽기 전용 property

    `Protocol` 의 변수 선언은 «설정 가능»을 요구하므로 두 번째가 특히 걸린다.
    ⚠️ 그래서 **상속은 유지하고 두 멤버만 덮어쓴다** — 상속을 끊으면 「이 레인이
    커널 포트와 무관한 것을 요구한다」는 거짓이 남는다. 요구하는 것은 커널 포트
    «그대로에» 읽기 둘을 더한 것이고, 다만 커널 쪽 두 줄이 드라이버를 잘못 적었다.

    ⚠️ 이것은 **이 레인의 우회이지 수리가 아니다.** 정공은 커널 포트를 고치는 것이고
    그것은 새 커널 태그를 뜻한다. 그 창이 열리면 여기 덮어쓴 둘을 지워라 —
    지워도 초록이면 상류가 고쳐진 것이다.
    """

    @property
    def rowcount(self) -> int: ...  # type: ignore[override]

    def execute(self, statement: str, parameters: tuple) -> Any: ...  # type: ignore[override]

    def fetchall(self) -> Sequence: ...

    def fetchone(self) -> Optional[Sequence]: ...


class RowConnection(DbConnection, Protocol):
    """그런 커서를 내주는 연결 — 반환 타입만 좁힌다."""

    def cursor(self) -> RowCursor: ...
