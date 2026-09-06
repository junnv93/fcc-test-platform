"""Conformance seal: **말하지 않으면 조용히 다르게 동작하는** 기본값 셋을 막는다.

2026-09-06 에 이 축이 진짜 결함 둘과 낡은 대역 하나를 드러냈다. 세 자리 다 «기본값이
조용히 일한다»는 같은 형태다 — 그리고 셋 다 **실행으로는 초록이었다.**

  * ``zip(a, b)`` — 길이가 어긋나면 짧은 쪽에 맞춰 **자른다.** 예외 0건.
    실측: ``dict(zip(COLUMNS, row))`` 가 53자리에 있었고, 그중
    ``tests/test_platform_project_entry.py`` 의 대역 행은 13개 값인데 컬럼은 12개라
    전체가 한 칸씩 밀려 있었다(``manufacturer`` 에 ``'active'`` 가 실리고
    ``status`` · ``created_at`` 이 ``None``). 그 시험은 그 필드를 단언하지 않아
    **초록이었다.**
  * ``assertRaises(Exception)`` — 오타로 인한 ``AttributeError`` 도, 대역이 던지는
    ``KeyError`` 도 「통과」다. 무엇을 막는지 말하지 않는 봉인이다.
  * ``re.split(p, s, 1)`` — 그 ``1`` 이 ``maxsplit`` 인지 ``flags`` 인지 읽는 사람이
    세어 봐야 한다. 파이썬 3.13 이 위치 인자를 폐기 예고했다.

── 왜 새 워크플로 YAML 이 아니라 테스트 한 파일인가 ────────────────────────────
실질 게이트는 ``githooks/pre-push`` → ``scripts/lane_check.py`` → pytest 이고,
``.github/workflows/checks.yml`` 도 **같은** ``lane_check.py`` 를 부른다. 검사를
pytest 에 붙이면 훅과 CI 양쪽에서 동시에 발화하고 게이트가 둘로 갈라지지 않는다.
파일명의 ``conformance`` 가 ``tests/conftest.py::_INVARIANT_FILENAME_TOKENS`` 의
자동 부착을 태워 경량 CI 레인에도 실린다.

── 왜 ruff 를 부르지 않는가 ────────────────────────────────────────────────────
선언된 의존성이 아니고, 넣으려면 ``pyproject.toml`` 을 고쳐야 하는데 그 파일은
``.extraction-layout.json`` 이 예약한 배송 경로다. 도구 부재 시 ``skipTest`` 로
도망가면 **초록으로 보이는 무검증**이 된다. 형제 모듈
``tests/test_undefined_name_conformance.py`` 와 같은 선택을 한다 — ``ast`` 만 쓴다.

⚠️ **경계는 주장이 아니라 실측이다.** 2026-09-06, 처분 전 트리에서
``ruff 0.16.6 --select B905`` 53건 / 이 검사기 53건, ``--select B034`` 2건 /
이 검사기 2건으로 **같은 집합**이었다. ``B017`` 은 ruff 가 ``as`` 로 받아 예외를
들여다보는 자리를 **봐준다**(실측: ``test_reference_coupled_publish.py`` 434·520 줄은
ruff 도 통과시킨다 — 그 둘은 *"무엇을 던지든 'forbidden' 이 새면 안 된다"* 는 의도적
광범위 포착이다). 이 검사기도 같은 경계를 쓴다: ``as`` 도 ``match=`` 도 없는 자리만
잡는다. 그러지 않으면 이 게이트는 멀쩡한 시험 둘을 빨갛게 만들고 다음 사람이 끈다.

── 봉인하지 «않는» 것 (documented limitation) ─────────────────────────────────
같은 웨이브가 함께 처분한 **B023**(루프 변수를 잡는 클로저)와 **F841**(잰 뒤 안 보는
지역 변수)은 여기 없다. 둘 다 파이썬 스코프 분석을 요구하고, 그것 없이 근사하면
거짓 양성이 난다 — 무고한 red 를 내는 게이트는 다음 사람이 끈다. **그래서 그 둘은
오늘 처분됐을 뿐 재발이 막히지는 않는다.** 그 사실을 여기 적어 두지 않으면 다음
사람이 이 파일을 「①단계 전부를 지킨다」로 읽는다.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

#: 소스가 아니거나 **사본**인 곳만 뺀다. ``build/`` 는 non-editable 설치가 남기는
#: 트리 사본이고 ``scripts/lane_check.py`` 의 ``CONFOUNDING_ARTIFACTS`` 도 거부한다.
_EXCLUDED_DIR_PARTS = frozenset({
    '.git', '.venv', 'venv', 'node_modules', 'build', 'dist', '__pycache__',
})

#: 「무엇이든 예외면 통과」로 읽히는 이름.
_BROAD_EXCEPTIONS = frozenset({'Exception', 'BaseException'})

#: 예외를 기대하는 헬퍼 — unittest 와 pytest 양쪽.
_RAISES_HELPERS = frozenset({'assertRaises', 'assertRaisesRegex', 'raises'})

#: ``re`` 함수 → 위치 인자로 허용되는 최대 개수. 그 뒤(``maxsplit`` / ``count`` /
#: ``flags``)를 위치로 넘기면 무엇을 넘겼는지 읽는 사람이 세어야 한다.
_RE_POSITIONAL_LIMIT = {'split': 2, 'sub': 3, 'subn': 3}


def _python_files() -> list[Path]:
    return sorted(
        path
        for path in REPO_ROOT.rglob('*.py')
        if not (_EXCLUDED_DIR_PARTS & set(path.relative_to(REPO_ROOT).parts))
    )


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _inspected_raises(tree: ast.AST) -> set[int]:
    """``as`` 로 받았거나 ``match=`` 를 준 raises 호출의 ``id`` 집합.

    그런 자리는 예외를 **들여다본다** — 넓게 잡되 무엇을 잡았는지 단언하는 형태이고,
    ruff B017 도 봐준다. 이 검사기가 그것까지 빨갛게 만들면 멀쩡한 시험이 죽는다.
    """
    inspected: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None and isinstance(item.context_expr, ast.Call):
                    inspected.add(id(item.context_expr))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and any(k.arg == 'match' for k in node.keywords):
            inspected.add(id(node))
    return inspected


def silent_defaults(source: str, filename: str = '<seal>') -> list[tuple[int, str]]:
    """이 모듈이 막는 세 형태의 ``(줄번호, 종류)`` 목록.

    ``SyntaxError`` 는 삼키지 않고 올린다 — 「못 읽었다」가 「깨끗하다」로 보이는 것이
    이 게이트가 없애려는 형태 자체다.
    """
    tree = ast.parse(source, filename=filename)
    inspected = _inspected_raises(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func

        # ① zip(a, b) — 자를지 말지를 말하지 않았다.
        if (isinstance(func, ast.Name) and func.id == 'zip' and len(node.args) >= 2
                and not any(k.arg == 'strict' for k in node.keywords)):
            found.append((node.lineno, 'zip() without an explicit strict='))

        # ② assertRaises(Exception) — 무엇을 막는지 말하지 않았다.
        name = _callee_name(func)
        if name in _RAISES_HELPERS and id(node) not in inspected:
            for arg in node.args:
                if isinstance(arg, ast.Name) and arg.id in _BROAD_EXCEPTIONS:
                    found.append((node.lineno, f'{name}({arg.id}) without inspecting it'))

        # ③ re.split(p, s, 1) — 그 1 이 무엇인지 말하지 않았다.
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id == 're' and func.attr in _RE_POSITIONAL_LIMIT
                and len(node.args) > _RE_POSITIONAL_LIMIT[func.attr]):
            found.append((node.lineno, f're.{func.attr}() passes maxsplit/count/flags positionally'))
    return sorted(found)


class TestTheCorpusIsReadable(unittest.TestCase):
    """판정의 전제 — 스캔이 실제로 파일을 보고 있고, 못 본 파일이 없다."""

    def test_the_corpus_is_not_empty(self):
        self.assertGreater(
            len(_python_files()), 100,
            '스캔 대상이 비었거나 급감했다 — 그러면 아래 「검출 0건」은 '
            '「깨끗하다」가 아니라 「아무것도 안 봤다」이다.',
        )

    def test_every_python_file_parses(self):
        broken: list[str] = []
        for path in _python_files():
            try:
                ast.parse(path.read_text(encoding='utf-8'), filename=_rel(path))
            except SyntaxError as exc:
                broken.append(f'{_rel(path)}:{exc.lineno}: {exc.msg}')
        self.assertEqual(
            broken, [],
            '파싱되지 않는 파이썬 파일이 있다 — 그 파일은 아래 판정에서 통째로 '
            f'빠지고, 빠진 것과 깨끗한 것이 같은 값이 된다:\n  ' + '\n  '.join(broken),
        )


class TestNoSilentDefaults(unittest.TestCase):
    """봉인 본체."""

    def test_no_file_relies_on_a_silent_default(self):
        findings: list[str] = []
        for path in _python_files():
            for lineno, kind in silent_defaults(path.read_text(encoding='utf-8'), _rel(path)):
                findings.append(f'{_rel(path)}:{lineno}: {kind}')
        self.assertEqual(
            findings, [],
            '기본값이 조용히 일하는 자리가 생겼다. 셋 다 «말하지 않으면 다르게 '
            '동작하는» 형태이고, 실행으로는 초록이다:\n'
            '  · zip  → 길이가 같아야 하면 strict=True, 자르는 것이 의도면 '
            'strict=False 와 사유를 적어라 (B905 는 True 가 아니라 «선택»을 요구한다)\n'
            '  · raises → 무엇을 막는지 이름으로 적거나, 넓게 잡되 as 로 받아 '
            '들여다봐라\n'
            '  · re    → maxsplit=/count=/flags= 로 이름을 붙여라\n  '
            + '\n  '.join(findings),
        )


class TestTheCheckerHasTeeth(unittest.TestCase):
    """「0건」이 「안 봤다」가 아님을 증명한다 — 실제로 겪은 세 형태를 합성해 확인한다."""

    def test_it_catches_a_column_row_zip(self):
        source = 'def f(cols, row):\n    return dict(zip(cols, row))\n'
        self.assertEqual(
            silent_defaults(source), [(2, 'zip() without an explicit strict=')])

    def test_it_accepts_both_explicit_choices(self):
        """``strict=False`` 도 통과다 — 이 게이트는 «선택»을 요구하지 True 를 요구하지 않는다."""
        self.assertEqual(silent_defaults('f = zip(a, b, strict=True)\n'), [])
        self.assertEqual(silent_defaults('f = zip(a, b, strict=False)\n'), [])

    def test_a_single_argument_zip_is_not_flagged(self):
        """인자가 하나면 어긋날 짝이 없다 — 거짓 양성 0 이 이 설계의 값이다."""
        self.assertEqual(silent_defaults('f = zip(rows)\n'), [])

    def test_it_catches_an_uninspected_broad_raises(self):
        source = (
            'class T:\n'
            '    def t(self):\n'
            '        with self.assertRaises(Exception):\n'
            '            boom()\n'
        )
        self.assertEqual(
            silent_defaults(source),
            [(3, 'assertRaises(Exception) without inspecting it')])

    def test_a_broad_raises_that_is_inspected_is_left_alone(self):
        """``as`` 로 받아 들여다보는 자리는 ruff B017 도 봐준다 — 경계를 맞춘다."""
        source = (
            'class T:\n'
            '    def t(self):\n'
            '        with self.assertRaises(Exception) as caught:\n'
            '            boom()\n'
            "        assert 'forbidden' not in str(caught.exception)\n"
        )
        self.assertEqual(silent_defaults(source), [])

    def test_a_named_exception_is_left_alone(self):
        source = (
            'class T:\n'
            '    def t(self):\n'
            '        with self.assertRaises(ValueError):\n'
            '            boom()\n'
        )
        self.assertEqual(silent_defaults(source), [])

    def test_it_catches_a_positional_re_maxsplit(self):
        source = "import re\nhead = re.split(r'[<>=]', item, 1)[0]\n"
        self.assertEqual(
            silent_defaults(source),
            [(2, 're.split() passes maxsplit/count/flags positionally')])

    def test_a_named_re_maxsplit_is_left_alone(self):
        source = "import re\nhead = re.split(r'[<>=]', item, maxsplit=1)[0]\n"
        self.assertEqual(silent_defaults(source), [])


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
