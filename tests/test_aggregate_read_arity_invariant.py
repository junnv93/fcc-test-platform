"""집계 질의는 «반드시 한 행»이다 — 그 믿음을 타입이 아니라 검사로 붙든다.

`central_project_reference_adapter` 는 새 개정 번호를 이렇게 얻는다::

    SELECT COALESCE(MAX(revision_number), 0) + 1 AS revision_number
    FROM project_result_reference_revisions WHERE ...

GROUP BY 가 없는 집계는 **대상 행이 0개여도 언제나 정확히 한 행**을 돌려준다. 그래서
그 두 자리에는 `is None` 가드가 없었다 — 같은 파일의 ``source`` · ``target`` · ``row``
는 조회가 빈손일 수 있어 전부 가드를 갖는다. **저자가 빼먹은 것이 아니라 알고 있었다.**

문제는 그 앎이 **어디에도 적혀 있지 않았다**는 것이다. 반환 타입은 여전히
``Optional[dict]`` 이었고, mypy 는 그 자리를 「인덱싱할 수 없는 값」 3건으로 짚었다
(2026-09-06 실측, `fcc_test_platform.infrastructure` strict 20건 중 3건).

── 그 3건을 «가드로» 막지 않은 이유 ─────────────────────────────────────────────
세 자리에 ``if x is None: raise`` 를 흩뿌리면 타입은 조용해지지만, **읽는 사람에게는
「여기도 빈손일 수 있다」는 거짓말**이 남는다. 그러면 다음 사람이 그 가드를 보고
「집계도 빈손일 수 있구나」로 배운다.

대신 ``_fetch_exactly_one`` 을 두어 **믿음을 이름으로** 적었다. 그리고 그 믿음이 깨지면
조용히 넘어가지 않고 그 자리에서 죽는다.

⚠️ **여기까지만 하면 ``Optional`` 만 숨긴 것이다.** 「반드시 한 행」이 참인지 아무도
재지 않으면, 새 헬퍼는 그냥 타입을 벗기는 도구다. 아래 셋이 그것을 막는다:

  1. 빈 결과를 주면 **이름을 가진 오류**로 죽는가 (조용한 ``None`` 이 아니라)
  2. 두 행을 주면 그것도 죽는가 (「하나 이상」이 아니라 「정확히 하나」인가)
  3. 그리고 **다음 집계 자리가 느슨한 헬퍼로 돌아가지 못하는가** — 모듈을 AST 로 훑어
     ``MAX(`` 를 담은 SQL 이 ``_fetch_one`` 에 넘어가면 red 다. 이것이 없으면 이 봉인은
     오늘의 두 자리만 지키고, 내일 추가되는 세 번째 자리는 다시 조용해진다.
"""
from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

import fcc_test_platform.infrastructure.adapters.driven.central_project_reference_adapter as _adapter
from fcc_test_platform.domain.ports.output.central_project_reference_port import (
    CentralProjectReferenceError,
)


_ADAPTER = _adapter.PostgresCentralProjectReferenceAdapter

#: 집계 함수를 담은 SQL 은 이 헬퍼로만 조회해야 한다.
_STRICT_HELPER = '_fetch_exactly_one'
_LOOSE_HELPER = '_fetch_one'
_AGGREGATE_TOKENS = ('MAX(', 'COUNT(', 'SUM(', 'MIN(', 'AVG(')

_AGGREGATE_SQL = (
    'SELECT COALESCE(MAX(revision_number), 0) + 1 AS revision_number '
    'FROM project_result_reference_revisions WHERE project_id = %s'
)


class _Cursor:
    """행 수를 마음대로 정하는 커서 대역 — 이 봉인이 조작하는 유일한 축이다."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.executed: list[tuple[str, tuple]] = []
        self.description = (('revision_number',),)

    def execute(self, statement: str, parameters: tuple) -> None:
        self.executed.append((statement, parameters))

    def fetchall(self) -> list[tuple]:
        return list(self._rows)

    def close(self) -> None:
        pass


class TestTheAggregateReadIsExactlyOneRow(unittest.TestCase):
    """헬퍼가 자기 이름대로 행동하는가."""

    def test_one_row_is_returned(self):
        cursor = _Cursor([(7,)])
        row = _ADAPTER._fetch_exactly_one(cursor, _AGGREGATE_SQL, ('p1',))
        self.assertEqual(row, {'revision_number': 7})
        self.assertEqual(len(cursor.executed), 1, '질의를 한 번만 보내야 한다')

    def test_an_empty_result_raises_a_named_error_naming_the_query(self):
        """조용한 ``None`` 대신 **어느 질의였는지 말하는** 오류로 죽는다.

        ⚠️ 이 검사가 이 파일의 존재 이유다. 옛 코드에서 빈 결과는 ``None`` 이 되어
        호출부까지 흘러갔고, 거기서 나는 오류는
        ``'NoneType' object is not subscriptable`` — **어느 질의였는지 남지 않는다.**
        """
        with self.assertRaises(CentralProjectReferenceError) as refused:
            _ADAPTER._fetch_exactly_one(_Cursor([]), _AGGREGATE_SQL, ('p1',))
        message = str(refused.exception)
        self.assertIn('exactly one row', message)
        self.assertIn('got 0', message)
        self.assertIn(
            'MAX(revision_number)', message,
            '어느 질의가 믿음을 깼는지 메시지가 말해야 한다 — 그것이 None 보다 나은 이유다',
        )

    def test_two_rows_also_raise(self):
        """「하나 이상」이 아니라 «정확히 하나»다.

        두 행이 왔다는 것은 질의에 GROUP BY 가 붙었거나 대상이 바뀌었다는 뜻이고,
        그때 첫 행만 조용히 쓰면 **틀린 개정 번호로 INSERT 한다.**
        """
        with self.assertRaises(CentralProjectReferenceError) as refused:
            _ADAPTER._fetch_exactly_one(_Cursor([(7,), (9,)]), _AGGREGATE_SQL, ('p1',))
        self.assertIn('got 2', str(refused.exception))


class TestNoAggregateQueryUsesTheLooseHelper(unittest.TestCase):
    """드리프트 가드 — 내일 추가되는 집계 자리가 다시 조용해지지 못하게 한다."""

    @staticmethod
    def _sql_of(call: ast.Call) -> str:
        """호출의 인자에 들어 있는 문자열 리터럴 전부를 이어 붙인다.

        SQL 이 여러 줄에 걸친 인접 문자열로 쓰여 있으므로(``'SELECT ...' 'FROM ...'``)
        파서가 이미 하나로 합쳐 준 ``ast.Constant`` 를 그대로 읽는다.
        """
        parts = []
        for node in ast.walk(call):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                parts.append(node.value)
        return ' '.join(parts)

    def test_the_module_routes_every_aggregate_read_through_the_strict_helper(self):
        source = Path(inspect.getsourcefile(_adapter)).read_text(encoding='utf-8')
        tree = ast.parse(source)

        loose_aggregates: list[str] = []
        strict_calls = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr == _STRICT_HELPER:
                strict_calls += 1
                continue
            if node.func.attr != _LOOSE_HELPER:
                continue
            sql = self._sql_of(node)
            if any(token in sql for token in _AGGREGATE_TOKENS):
                loose_aggregates.append(f'{node.lineno}행: {" ".join(sql.split())[:90]}')

        self.assertGreater(
            strict_calls, 0,
            f'{_STRICT_HELPER} 를 부르는 곳이 0개다 — 헬퍼가 사라졌거나 이름이 바뀌었다. '
            '그러면 아래 판정은 아무것도 요구하지 않으면서 초록이 된다.',
        )
        self.assertEqual(
            loose_aggregates, [],
            f'집계 SQL 이 {_LOOSE_HELPER}(Optional 반환) 로 조회되고 있다. 집계는 언제나 '
            f'한 행이므로 {_STRICT_HELPER} 를 써라 — 그러면 그 믿음이 깨질 때 '
            '조용한 None 대신 질의 이름을 담은 오류가 난다:\n  '
            + '\n  '.join(loose_aggregates),
        )


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
