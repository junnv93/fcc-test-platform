"""이 상자의 웹 표면은 **자기가 갖지 않은 이름**을 부르지 않는다 (2026-09-02).

■ 왜 이 파일이 생겼나

배포된 v0.1.7 의 ``fcc_test_platform/api/platform_routes.py:1890`` 이 정의도 import 도
없는 ``_utc_now_iso()`` 를 불렀다. 그 이름은 ``fcc_test_platform/application/`` 아래
**세 모듈에 각각** 정의돼 있어 *어디에나 있는 이름*처럼 읽혔지만, 부르는 자리에는
없었다. 도달하면 반드시 죽는 코드였고 — 노드가 자격 없이 불러 403 에서 끝났기 때문에
**한 번도 도달한 적이 없었다.** 403 은 「권한이 없다」로 읽히므로 아무도 그 뒤를
의심하지 않았다.

■ 왜 「그 라우트의 성공 경로 테스트」만으로는 부족한가

그것은 이 라우트를 고친다. 다음 라우트를 고치지 않는다. 결함의 부류는 *한 자리의
오타* 가 아니라 **「부르는 이름이 이 스코프에서 해소되는가를 아무도 묻지 않는다」** 이고,
파이썬은 그 질문을 호출 시점까지 미룬다. 웹 표면에서 그 지연은 운영자에게 500 이다.

■ 왜 모듈 스코프가 아니라 어휘 스코프인가 (실측 2026-09-02)

같은 검사를 「모듈 전역에 있는가」로만 물으면 ``platform_routes.py`` 에서 **오탐 3 ·
진탐 1** 이 나온다 — ``_emit_page`` · ``_deny_if_throttled`` · ``_heartbeat`` 는 전부
중첩 ``def`` 라 모듈 전역에 없지만 완벽히 정당하다. 축이 언어보다 거칠면 사람이 검사를
끄고, 꺼진 검사는 없는 검사다. 어휘 스코프로 물으면 ``fcc_test_platform/`` 106 파일에서
**오탐 0 · 진탐 1** 이다.

■ 이 검사가 보지 못하는 것 (그러므로 여기 적는다)

* ``getattr``/``globals()``/문자열 eval 로 만든 이름 — 정적으로 볼 수 없다.
* 런타임에 모듈 딕셔너리에 주입되는 이름 — 이 상자에는 그런 패턴이 없다.
* **타입 전용 이름**: ``from __future__ import annotations`` 가 있으면 annotation 은
  문자열이라 미해소여도 죽지 않는다. 이 검사는 그것도 보고한다 — 죽지는 않지만
  ``typing.get_type_hints`` 를 깨뜨리므로 결함이 맞다.
"""
from __future__ import annotations

import ast
import builtins
import pathlib
import unittest


#: 검사 대상 — 이 상자가 **온전히 소유**하는 패키지. ``domain/`` 과 ``application/`` 은
#: 모노레포에서 미러링되는 트리라 여기서 고치면 그쪽과 갈라진다(그 트리의 알려진
#: 미해소 이름 1건은 장부가 이름으로 갖는다).
_PACKAGE = 'fcc_test_platform'

_BUILTINS = frozenset(dir(builtins)) | {
    '__file__', '__name__', '__doc__', '__package__', '__spec__', '__loader__',
    '__builtins__', '__debug__',
}


def _scope_bindings(node) -> set:
    """이 스코프가 묶는 이름 전부.

    ⚠️ **흐름 비민감**하게 모은다 — 파이썬이 그렇게 한다. 함수 안 어디서든 대입되는
    이름은 그 함수 전체에서 지역이고, 대입보다 앞선 사용은 ``NameError`` 가 아니라
    ``UnboundLocalError`` 다. 그 둘을 섞으면 이 검사가 다른 결함을 말하게 된다.
    """
    out: set = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        a = node.args
        for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs):
            out.add(arg.arg)
        if a.vararg:
            out.add(a.vararg.arg)
        if a.kwarg:
            out.add(a.kwarg.arg)
    body = node.body if isinstance(node.body, list) else [node.body]
    stack = list(body)
    while stack:
        n = stack.pop()
        # 중첩 스코프로는 내려가지 않는다 — 그쪽 이름은 그쪽 것이다.
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
            continue
        if isinstance(n, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp,
                          ast.GeneratorExp)):
            continue
        if isinstance(n, ast.Import):
            for al in n.names:
                out.add((al.asname or al.name).split('.')[0])
        elif isinstance(n, ast.ImportFrom):
            for al in n.names:
                out.add(al.asname or al.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            out.update(n.names)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        for child in ast.iter_child_nodes(n):
            stack.append(child)
    return out


def _comprehension_targets(node) -> set:
    out: set = set()
    for gen in node.generators:
        for t in ast.walk(gen.target):
            if isinstance(t, ast.Name):
                out.add(t.id)
    return out


def unresolved_names(source: str, filename: str = '<source>') -> list:
    """``(name, lineno)`` — 이 소스에서 어느 스코프로도 해소되지 않는 Load 이름들."""
    tree = ast.parse(source, filename)
    found: list = []

    def visit(node, scopes) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                visit(d, scopes)
            for sub in ast.iter_child_nodes(node.args):
                visit(sub, scopes)
            if node.returns is not None:
                visit(node.returns, scopes)
            child = scopes + [('function', _scope_bindings(node))]
            for stmt in node.body:
                visit(stmt, child)
            return
        if isinstance(node, ast.Lambda):
            for sub in ast.iter_child_nodes(node.args):
                visit(sub, scopes)
            visit(node.body, scopes + [('function', _scope_bindings(node))])
            return
        if isinstance(node, ast.ClassDef):
            for d in node.decorator_list:
                visit(d, scopes)
            for b in node.bases:
                visit(b, scopes)
            for kw in node.keywords:
                visit(kw.value, scopes)
            child = scopes + [('class', _scope_bindings(node))]
            for stmt in node.body:
                visit(stmt, child)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                             ast.GeneratorExp)):
            child = scopes + [('function', _comprehension_targets(node))]
            for i, gen in enumerate(node.generators):
                # 첫 iterable 만 바깥 스코프에서 평가된다 (파이썬 의미론).
                visit(gen.iter, scopes if i == 0 else child)
                for cond in gen.ifs:
                    visit(cond, child)
            if isinstance(node, ast.DictComp):
                visit(node.key, child)
                visit(node.value, child)
            else:
                visit(node.elt, child)
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            name = node.id
            if name in _BUILTINS:
                return
            for i in range(len(scopes) - 1, -1, -1):
                kind, names = scopes[i]
                # ⚠️ 클래스 스코프는 중첩 스코프의 조회 사슬에 **없다**. 메서드 안에서
                # 맨이름으로 클래스 속성을 부르면 그것은 실제로 NameError 다.
                if kind == 'class' and i != len(scopes) - 1:
                    continue
                if name in names:
                    return
            found.append((name, node.lineno))
            return
        for child in ast.iter_child_nodes(node):
            visit(child, scopes)

    module_scope = ('module', _scope_bindings(tree))
    for stmt in tree.body:
        visit(stmt, [module_scope])
    return found


class TestTheWebSurfaceOnlyCallsNamesItHas(unittest.TestCase):
    """진탐 축 — 배송되는 패키지에 미해소 이름이 0건."""

    def test_no_unresolved_names_in_the_delivered_package(self):
        root = pathlib.Path(__file__).resolve().parent.parent / _PACKAGE
        self.assertTrue(root.is_dir(), f'{root} 가 없다 — 검사 대상을 잃었다')

        offenders = []
        scanned = 0
        for path in sorted(root.rglob('*.py')):
            scanned += 1
            for name, lineno in unresolved_names(
                path.read_text(encoding='utf-8'), str(path),
            ):
                rel = path.relative_to(root.parent)
                offenders.append(f'{rel}:{lineno}: {name}')

        # 비-공허성 — 대상을 하나도 안 읽고 초록이 되는 것을 막는다.
        self.assertGreater(scanned, 50, '검사 대상 파일이 갑자기 줄었다')
        self.assertEqual(
            offenders, [],
            '이 이름들은 부르는 자리의 어느 스코프에도 없다 — 도달하면 NameError 다:\n'
            + '\n'.join(offenders),
        )


class TestTheResolverItself(unittest.TestCase):
    """⚠️ 검사기의 두 축을 **둘 다** 잰다.

    양성만 재면 「전부 신고하는」 검사기도 통과하고, 음성만 재면 「아무것도 신고하지
    않는」 검사기도 통과한다. 두 고장 모두 위 검사를 초록으로 만든다.
    """

    def _names(self, source: str) -> list:
        return [n for n, _ in unresolved_names(source)]

    # ── 양성: 잡아야 하는 것 ──────────────────────────────────────────────
    def test_it_reports_the_defect_this_file_exists_for(self):
        """2026-09-02 결함의 최소 재현 — 메서드가 부르는 모듈 헬퍼가 이 모듈에 없다."""
        self.assertEqual(self._names(
            'class A:\n'
            '    def go(self):\n'
            '        return {"received_at": _utc_now_iso()}\n'
        ), ['_utc_now_iso'])

    def test_a_class_attribute_is_not_visible_to_its_own_methods(self):
        """클래스 스코프는 조회 사슬에 없다 — 이것은 실제로 NameError 다."""
        self.assertEqual(self._names(
            'class A:\n'
            '    LIMIT = 3\n'
            '    def go(self):\n'
            '        return LIMIT\n'
        ), ['LIMIT'])

    def test_a_name_bound_only_in_a_sibling_function_is_not_visible(self):
        self.assertEqual(self._names(
            'def a():\n'
            '    helper = 1\n'
            'def b():\n'
            '    return helper\n'
        ), ['helper'])

    # ── 음성: 잡으면 안 되는 것 ──────────────────────────────────────────
    def test_a_nested_def_resolves(self):
        """실측된 오탐 3건의 모양 — 모듈 스코프만 보는 검사기는 여기서 넘어졌다."""
        self.assertEqual(self._names(
            'def outer():\n'
            '    def _emit_page(x):\n'
            '        return x\n'
            '    return _emit_page(1)\n'
        ), [])

    def test_a_closure_over_an_enclosing_function_resolves(self):
        self.assertEqual(self._names(
            'def outer():\n'
            '    total = 0\n'
            '    def inner():\n'
            '        return total\n'
            '    return inner\n'
        ), [])

    def test_comprehension_walrus_except_and_with_targets_resolve(self):
        self.assertEqual(self._names(
            'import json\n'
            'def go(rows, fh):\n'
            '    out = [r for r in rows if r]\n'
            '    keys = {k: v for k, v in rows}\n'
            '    if (n := len(out)):\n'
            '        out.append(n)\n'
            '    try:\n'
            '        json.load(fh)\n'
            '    except ValueError as exc:\n'
            '        return exc\n'
            '    with open("x") as handle:\n'
            '        return handle, keys\n'
        ), [])

    def test_imports_defaults_decorators_and_builtins_resolve(self):
        self.assertEqual(self._names(
            'from functools import wraps\n'
            'DEFAULT = 3\n'
            '@wraps\n'
            'def go(limit=DEFAULT, *args, **kwargs):\n'
            '    return len(args) + limit + len(kwargs)\n'
        ), [])

    def test_a_conditional_import_still_binds(self):
        """``if TYPE_CHECKING:`` 안의 import 도 모듈 스코프를 묶는다."""
        self.assertEqual(self._names(
            'from typing import TYPE_CHECKING\n'
            'if TYPE_CHECKING:\n'
            '    from x import Thing\n'
            'def go(t: "Thing"):\n'
            '    return Thing\n'
        ), [])

    def test_a_global_declaration_resolves(self):
        self.assertEqual(self._names(
            'def go():\n'
            '    global _cache\n'
            '    _cache = 1\n'
            '    return _cache\n'
        ), [])


if __name__ == '__main__':
    unittest.main()
