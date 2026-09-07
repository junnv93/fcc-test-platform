"""Conformance seal: 이 저장소의 어떤 파이썬 파일도 **정의되지 않은 이름을 읽지 않는다**.

2026-09-06 에 이 축이 세 개의 진짜 결함을 한꺼번에 드러냈고, 셋 다 **아무 게이트에도
걸리지 않은 채** main 에 있었다:

  * ``tests/test_dependency_audit_workflow.py`` — 파서가 `_Token` 을 8곳에서 부르는데
    그 정의는 2026-08-31 분할 때 모노레포에 남았다. 그 모듈은 테스트가 0개라
    **아무도 그 함수를 부르지 않았고**, 그래서 수집되어 초록이었다.
  * ``tests/test_architecture_conformance.py`` — `_baseline_lookup()` 이 이 레포에
    없는 `TestGodObjectGuard` 를 읽었다. 그 함수를 부르는 곳이 0개라 조용했다.
  * ``apps/web/scripts/run-test-plan-generation-worker.py`` — `time.sleep(
    limits.poll_interval_seconds)` 의 `limits` 가 2026-08-15 crossing-closure 때
    지워졌다. **런타임 결함**이고, 대기열이 비는 첫 순간에만 `NameError` 로 죽는다.

세 결함의 공통 형태: **부르지 않는 코드의 이름 오류는 실행으로 잡히지 않는다.** 그리고
이 저장소의 정적 게이트는 그것을 볼 수 없었다 — ``tests/`` 도 ``apps/web/scripts/``
도 ``-p fcc_test_platform`` **아래에 있지 않아** mypy 의 사정권 밖이고,
``.importlinter`` 는 모듈 간 의존 방향만 본다.

⚠️ **2026-09-07 정정 — 결론은 그대로이고 «사유»가 죽었다.** 이 자리는 한때
*"`mypy.ini` 는 범위를 ``fcc_test_platform.domain.*`` 로 한정하므로"* 라고 적었다.
그 범위는 그 뒤 ``[mypy-fcc_test_platform.*]`` 전량이 됐고(`#158`·`#159`), 그래도
위 결론은 참이다 — 두 디렉터리는 범위가 얼마나 넓어지든 그 패키지 «밖»이기
때문이다. **전제가 죽었는데 결론이 살아남는 형태가 가장 오래 간다**: 결론이 맞으니
아무도 전제를 다시 읽지 않는다. 그래서 사유를 «범위의 크기»가 아니라 «트리의
위치»로 다시 적는다 — 그쪽은 게이트가 넓어져도 낡지 않는다.

── 왜 새 워크플로 YAML 이 아니라 테스트 한 파일인가 ────────────────────────────
실질 게이트는 ``githooks/pre-push`` → ``scripts/lane_check.py`` → pytest 다.
``.github/workflows/checks.yml`` 도 **같은** `lane_check.py` 를 부르므로(그 파일이
SSOT), 검사를 pytest 에 붙이면 훅과 CI 양쪽에서 동시에 발화한다. 워크플로를 새로
만들면 게이트가 둘로 갈라지고, 그중 하나만 도는 날이 온다.

파일명에 ``conformance`` 가 들어가므로 ``tests/conftest.py::
_INVARIANT_FILENAME_TOKENS`` 가 ``invariant`` 마커를 자동 부착한다 — 경량 CI 레인
(``-m "invariant and not hardware and not gui and not bench"``)에도 함께 실린다.

── 왜 서드파티 린터를 부르지 않는가 ───────────────────────────────────────────
`ruff` 는 이 레포의 선언된 의존성이 아니고, 넣으려면 ``pyproject.toml`` 을 고쳐야
하는데 그 파일은 ``.extraction-layout.json`` 이 예약한 배송 경로다(``mypy.ini`` 의
머리말이 같은 사유를 적는다). 그리고 도구 부재 시 ``skipTest`` 로 도망가면 **초록으로
보이는 무검증**이 된다. 그래서 형제 모듈
``tests/test_dependency_audit_workflow.py`` 가 PyYAML 대신 stdlib 미니 파서를 쓴
것과 같은 선택을 한다 — 아래 검사기는 ``ast`` 만 쓴다.

⚠️ **동등성은 주장이 아니라 실측이다.** 2026-09-06, 위 세 결함을 담은 기준 트리
(``074d588``)에 대해 ``ruff 0.16.6 --select F821`` 과 아래 검사기는 **같은 11건**을
냈다 — 같은 파일, 같은 줄, 같은 이름. 처분 후에는 양쪽 모두 0건이다.

── 설계: 의도적으로 «확실한 것만» 잡는다 (documented limitation) ──────────────
아래 검사기는 파이썬의 스코프 규칙을 구현하지 **않는다.** 모듈 안 어디에서든 한 번이라도
묶이는 이름(대입 · import · def/class · 인자 · except-as · global/nonlocal · match
패턴)을 전부 모아, **그 어디에도 없는** 이름을 읽는 자리만 보고한다.

  * 놓치는 것: 다른 함수의 지역 이름을 읽는 경우(스코프 오류). 그건 F821 의
    일부지만 스코프 분석 없이는 **거짓 양성 없이** 판정할 수 없다.
  * 놓치지 않는 것: 이 저장소가 실제로 겪은 형태 — 정의가 다른 레포로 떠났거나
    삭제가 절반만 이뤄져 **파일 어디에도 없는** 이름. 세 결함 전부 이 형태다.

거짓 양성 0이 이 설계의 값이다. 게이트가 무고한 red 를 내면 다음 사람이 그것을 끈다.
"""
from __future__ import annotations

import ast
import builtins
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

#: 스캔에서 제외하는 디렉터리. 소스가 아니거나 **사본**인 곳만 — 판정을 좁히는
#: 목적이 아니다. ``build/`` 는 non-editable 설치가 남기는 트리 사본이고
#: (``scripts/lane_check.py`` 의 ``CONFOUNDING_ARTIFACTS`` 가 같은 것을 거부한다),
#: 나머지는 도구가 만든 디렉터리다.
_EXCLUDED_DIR_PARTS = frozenset({
    '.git', '.venv', 'venv', 'node_modules', 'build', 'dist', '__pycache__',
})

#: 모듈 실행 시 파이썬이 넣어 주는 이름 — 파일 안에서 묶이지 않지만 정의돼 있다.
_MODULE_DUNDERS = frozenset({
    '__file__', '__name__', '__doc__', '__spec__', '__package__', '__loader__',
    '__builtins__', '__debug__', '__path__', '__annotations__', '__dict__',
    '__class__',
})

_DEFINED_ELSEWHERE = frozenset(dir(builtins)) | _MODULE_DUNDERS

#: ``from x import *`` 는 어떤 이름이 들어오는지 정적으로 알 수 없다. 그런 파일은
#: 판정할 수 없고, **판정할 수 없다는 사실 자체가 봉인 대상**이다 — 목록이 조용히
#: 자라면 사각지대가 자라는 것이고, 그때 「검출 0건」은 「깨끗하다」와 구분되지 않는다.
#: 실측 2026-09-06: 이 저장소에 정확히 1개.
_STAR_IMPORT_BLIND_SPOT = frozenset({
    'scripts/_keycloak_chamber_admin.py',
})


def _python_files() -> list[Path]:
    return sorted(
        path
        for path in REPO_ROOT.rglob('*.py')
        if not (_EXCLUDED_DIR_PARTS & set(path.relative_to(REPO_ROOT).parts))
    )


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def collect_bound_names(tree: ast.AST) -> tuple[set[str], bool]:
    """모듈 안에서 **어디에서든** 묶이는 이름 전부와, star-import 여부."""
    names: set[str] = set()
    has_star = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == '*':
                    has_star = True
                    continue
                names.add(alias.asname or alias.name.split('.')[0])
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.MatchAs) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names, has_star


def undefined_names(source: str, filename: str = '<seal>') -> list[tuple[int, str]]:
    """읽히는데 이 모듈 어디에도 묶이지 않는 이름 ``(줄번호, 이름)`` 목록.

    ``SyntaxError`` 는 삼키지 않고 올린다 — 「못 읽었다」가 「깨끗하다」로 보이는
    것이 이 게이트가 없애려는 형태 자체다.
    """
    tree = ast.parse(source, filename=filename)
    bound, has_star = collect_bound_names(tree)
    if has_star:
        return []
    return sorted(
        (node.lineno, node.id)
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id not in bound
        and node.id not in _DEFINED_ELSEWHERE
    )


class TestTheCorpusIsReadable(unittest.TestCase):
    """판정의 전제 — 스캔이 실제로 파일을 보고 있고, 못 본 파일이 없다."""

    def test_the_corpus_is_not_empty(self):
        files = _python_files()
        self.assertGreater(
            len(files), 100,
            '스캔 대상이 비었거나 급감했다 — 그러면 아래 「검출 0건」은 '
            '「깨끗하다」가 아니라 「아무것도 안 봤다」이다.',
        )

    def test_every_python_file_parses(self):
        """파싱 실패는 **건너뛰지 않고 red** 다 (실측 2026-09-06: 0건)."""
        broken: list[str] = []
        for path in _python_files():
            try:
                ast.parse(path.read_text(encoding='utf-8'), filename=_rel(path))
            except SyntaxError as exc:
                broken.append(f'{_rel(path)}:{exc.lineno}: {exc.msg}')
        self.assertEqual(
            broken, [],
            '파싱되지 않는 파이썬 파일이 있다 — 그 파일은 아래 이름 판정에서 '
            f'통째로 빠지고, 빠진 것과 깨끗한 것이 같은 값이 된다:\n  '
            + '\n  '.join(broken),
        )

    def test_the_star_import_blind_spot_has_not_grown(self):
        """``import *`` 파일은 판정 불가 — 그 목록이 자라는 것을 봉인한다."""
        observed = set()
        for path in _python_files():
            _, has_star = collect_bound_names(
                ast.parse(path.read_text(encoding='utf-8'), filename=_rel(path))
            )
            if has_star:
                observed.add(_rel(path))
        self.assertEqual(
            observed, set(_STAR_IMPORT_BLIND_SPOT),
            '``from x import *`` 를 쓰는 파일 집합이 선언과 다르다. 새로 생겼다면 '
            '그 파일은 이 게이트의 사각지대이므로, 목록을 늘리기 전에 star import '
            '자체를 없앨 수 있는지 먼저 답하라. 사라졌다면 목록에서 빼라 — '
            f'선언={sorted(_STAR_IMPORT_BLIND_SPOT)} 관측={sorted(observed)}',
        )


class TestNoUndefinedNames(unittest.TestCase):
    """봉인 본체 — 정의되지 않은 이름을 읽는 자리가 0개다."""

    def test_no_python_file_reads_an_undefined_name(self):
        findings: list[str] = []
        for path in _python_files():
            for lineno, name in undefined_names(path.read_text(encoding='utf-8'), _rel(path)):
                findings.append(f'{_rel(path)}:{lineno}: 정의되지 않은 이름 `{name}`')
        self.assertEqual(
            findings, [],
            '이 파일들이 어디에도 묶이지 않은 이름을 읽는다. 부르지 않는 코드라면 '
            '실행으로는 절대 드러나지 않고, 부르는 코드라면 그 자리에서 NameError 다. '
            '「정의가 다른 레포에 남았다」거나 「삭제가 절반만 됐다」는 신호이므로, '
            '이름을 되살릴지 그 코드를 지울지 **판정**하라:\n  ' + '\n  '.join(findings),
        )


class TestTheCheckerHasTeeth(unittest.TestCase):
    """검사기 자신이 무력해지는 것을 막는다 — 「0건」이 「안 봤다」가 아님을 증명한다.

    ⚠️ 위 봉인은 검사기가 항상 빈 목록을 돌려줘도 초록이다. 그래서 검사기가 실제
    결함을 잡는다는 것을, 이 저장소가 겪은 **세 결함의 형태 그대로** 합성해 확인한다.
    """

    def test_it_catches_a_name_whose_definition_left(self):
        """① `_Token` 형태 — 타입 주석과 호출부만 남고 정의가 사라졌다.

        ⚠️ ``from __future__ import annotations`` 가 있어도 **주석 줄까지** 잡는다.
        그 import 는 런타임 평가만 미룰 뿐 AST 에는 이름이 그대로 남기 때문이고,
        ruff 도 같은 판정을 한다(실제 파일에서 F821 이 ``def`` 줄 123 을 짚었다).
        주석에만 있는 이름이라 런타임 오류는 아니지만, **파일 어디에도 없는 이름**인
        것은 같으므로 red 가 맞다.
        """
        source = (
            'from __future__ import annotations\n'
            'def _tokenize(text: str) -> list[_Token]:\n'
            '    return [_Token(0, text, None)]\n'
        )
        self.assertEqual(undefined_names(source), [(2, '_Token'), (3, '_Token')])

    def test_it_catches_a_class_attribute_read_on_a_missing_class(self):
        """② `TestGodObjectGuard` 형태 — 죽은 함수 안의 클래스 참조."""
        source = (
            'def _baseline_lookup(rel_path):\n'
            '    return TestGodObjectGuard._BASELINES.get(rel_path)\n'
        )
        self.assertEqual(undefined_names(source), [(2, 'TestGodObjectGuard')])

    def test_it_catches_a_half_finished_removal_in_a_live_loop(self):
        """③ `limits` 형태 — import 는 지워졌는데 사용처가 남았다."""
        source = (
            'import time\n'
            'def main():\n'
            '    while True:\n'
            '        time.sleep(limits.poll_interval_seconds)\n'
        )
        self.assertEqual(undefined_names(source), [(4, 'limits')])

    def test_a_keyword_argument_does_not_count_as_a_definition(self):
        """`f(limits=...)` 는 `limits` 를 묶지 않는다 — 실제로 겪은 오판 지점."""
        source = 'def f(**kw):\n    return kw\nf(limits=1)\nprint(limits)\n'
        self.assertEqual(undefined_names(source), [(4, 'limits')])

    def test_it_does_not_flag_names_bound_anywhere_in_the_module(self):
        """거짓 양성 0 — 이 설계의 값이다."""
        source = (
            'import os\n'
            'from pathlib import Path as P\n'
            'ALPHA = 1\n'
            'class C:\n'
            '    pass\n'
            'def f(beta, *args, gamma=2, **kwargs):\n'
            '    for delta in args:\n'
            '        with open(os.devnull) as handle:\n'
            '            try:\n'
            '                print(ALPHA, beta, gamma, kwargs, delta, handle, C, P)\n'
            '            except OSError as exc:\n'
            '                print(exc)\n'
            '    epsilon = [zeta for zeta in range(3)]\n'
            '    return epsilon, __file__\n'
        )
        self.assertEqual(undefined_names(source), [])

    def test_a_star_import_file_is_reported_as_unjudgeable_not_as_clean(self):
        """star import 파일은 0건을 돌려주지만, 그 사실은 위 봉인이 따로 센다."""
        source = 'from os.path import *\nprint(join("a", "b"))\n'
        self.assertEqual(undefined_names(source), [])
        _, has_star = collect_bound_names(ast.parse(source))
        self.assertTrue(has_star, 'star import 를 감지하지 못하면 사각지대 봉인이 공허해진다')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
