"""이 상자 «안»을 가리키는데 실재하지 않는 import 대상을 봉인한다 (2026-09-05).

**이 축은 형제 봉인 셋이 구조적으로 볼 수 없는 것을 본다.** 2026-09-05 에
`scripts/` 알맹이 34건을 패키지로 옮기며 물었다 — *옮기다 지연 import 의 대상을
틀렸을 때 무엇이 잡아 주는가.* 답은 **아무것도 안 잡는다** 였다. 탐침
(`from fcc_test_platform.application.gone_away import Missing` 을 함수 «안»에
심었다)으로 실측한 결과:

| 봉인 | 결과 | 왜 못 잡나 |
|---|---|---|
| `test_platform_api_name_resolution` | 통과 | **이름이 묶이는가**를 묻는다. `from X.Y import Z` 는 `X.Y` 가 없어도 `Z` 를 묶는다 |
| `test_supply_closure_axis` | 통과 | 그 원장은 서드파티·타 레인 미해소를 잰다. 이 상자 **«내부»** 경로는 대상이 아니다 |
| `import-linter` | 통과 | 없는 모듈은 간선을 만들지 않는다 — 파일 수만 늘고 **신호가 0** |

셋 다 결함이 아니라 **축이 다르다.** 그래서 질문이 다르다:

    이 상자 안을 가리키는 import 의 대상이 실제로 해소되는가.

⚠️ **모듈 최상단이 아니라 함수 안의 지연 import 가 이 축의 존재 이유다.** 최상단이면
import 하는 순간 터지므로 어느 시험이든 잡는다. 함수 안이면 **그 함수가 실제로 불릴
때에만** 드러나고, 스크립트 알맹이는 지연 import 가 많다. 파일이 옮겨 다니는 웨이브가
정확히 이 위험을 만든다.

⚠️ **`0건` 이 「깨끗하다」인지 「안 돌았다」인지 구별돼야 한다.** 그래서 검사한 파일
수와 내부 import 수를 함께 돌려주고, 비-공허성으로 그 둘을 단언한다. 대상을 하나도
안 읽고 초록이 되는 봉인은 봉인이 아니다.
"""
from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = 'fcc_test_platform'
_SKIP = {'.git', '.venv', 'node_modules', '__pycache__', '.mypy_cache', '.pytest_cache'}


def _internal_import_targets(tree: ast.AST, package: str):
    """`package.` 로 시작하는 import 대상과 그 줄 번호.

    상대 import(`level > 0`)는 제외한다 — 그것은 파일 위치가 정하므로 이 축이
    아니라 파이썬 자신이 즉시 판정한다.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = [node.module] if node.module else []
        elif isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        else:
            continue
        for module in modules:
            if module and module.startswith(package + '.'):
                yield module, node.lineno


def stale_internal_imports(root: Path, package: str = _PACKAGE):
    """해소되지 않는 내부 import 목록과, 검사한 파일 수·내부 import 수."""
    stale: list[str] = []
    files = imports = 0
    for path in sorted(root.rglob('*.py')):
        if any(part in _SKIP for part in path.parts):
            continue
        files += 1
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:  # pragma: no cover — 레인에 없다
            continue
        for module, lineno in _internal_import_targets(tree, package):
            imports += 1
            try:
                resolved = importlib.util.find_spec(module) is not None
            except (ModuleNotFoundError, ImportError, ValueError):
                resolved = False
            if not resolved:
                # 스캔 뿌리의 «부모» 기준 — 저장소 밖(반증용 임시 트리)에서도 성립한다
                stale.append(f'{path.relative_to(root.parent)}:{lineno} → {module}')
    return stale, files, imports


class TestNoImportPointsAtSomethingThisBoxDoesNotHave(unittest.TestCase):
    """진탐 축 — 상자 안을 가리키는 미해소 import 가 0건."""

    def test_the_package_has_no_stale_internal_imports(self):
        stale, files, imports = stale_internal_imports(_REPO_ROOT / _PACKAGE)
        self.assertGreater(files, 100, '검사 대상 파일이 갑자기 줄었다')
        self.assertGreater(imports, 100, '내부 import 를 하나도 못 봤다 — 축이 꺼졌다')
        self.assertEqual(
            stale, [],
            '이 import 들의 대상이 이 상자에 없다 — 그 줄에 도달하면 ImportError 다:\n'
            + '\n'.join(stale),
        )

    def test_the_shells_have_no_stale_internal_imports(self):
        """껍데기도 같은 축으로 본다 — 알맹이를 잘못 부르면 CLI 가 그 자리에서 죽는다."""
        stale, files, imports = stale_internal_imports(_REPO_ROOT / 'scripts')
        self.assertGreater(files, 20, '검사 대상 파일이 갑자기 줄었다')
        self.assertGreater(imports, 20, '내부 import 를 하나도 못 봤다 — 축이 꺼졌다')
        self.assertEqual(
            stale, [],
            '이 import 들의 대상이 이 상자에 없다 — 그 줄에 도달하면 ImportError 다:\n'
            + '\n'.join(stale),
        )


class TestTheScanActuallyJudges(unittest.TestCase):
    """반증 축 — 틀린 상태를 만들어 «운다»는 것을 확인한다.

    ⚠️ 형제 봉인들이 통과시킨 바로 그 형태(함수 안 지연 import)를 쓴다. 최상단
    import 로 시험하면 이 봉인이 실제로 덮는 자리를 시험하지 않은 것이 된다.
    """

    def test_a_synthetic_lazy_offender_is_flagged_with_file_line_and_module(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / _PACKAGE
            root.mkdir()
            (root / 'victim.py').write_text(
                'def later():\n'
                f'    from {_PACKAGE}.application.gone_away import Missing\n'
                '    return Missing\n',
                encoding='utf-8',
            )
            stale, files, imports = stale_internal_imports(root)

        self.assertEqual(files, 1)
        self.assertEqual(imports, 1)
        self.assertEqual(len(stale), 1, f'탐침을 못 잡았다: {stale}')
        self.assertIn('victim.py:2', stale[0])
        self.assertIn(f'{_PACKAGE}.application.gone_away', stale[0])

    def test_a_resolvable_lazy_import_is_not_flagged(self):
        """반대 방향 — 옳은 상태에서 조용해야 한다. 안 그러면 면제 목록으로 꺼진다."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / _PACKAGE
            root.mkdir()
            (root / 'ok.py').write_text(
                'def later():\n'
                f'    from {_PACKAGE}.identity_policy import __name__ as n\n'
                '    return n\n',
                encoding='utf-8',
            )
            stale, _, imports = stale_internal_imports(root)

        self.assertEqual(imports, 1)
        self.assertEqual(stale, [])

    def test_a_relative_import_is_out_of_scope_rather_than_stale(self):
        """상대 import 는 파이썬이 즉시 판정한다 — 이 축이 흉내낼 자리가 아니다."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / _PACKAGE
            root.mkdir()
            (root / 'rel.py').write_text(
                'def later():\n'
                '    from .gone_away import Missing\n'
                '    return Missing\n',
                encoding='utf-8',
            )
            stale, _, imports = stale_internal_imports(root)

        self.assertEqual(imports, 0, '상대 import 를 셌다 — 축을 넘었다')
        self.assertEqual(stale, [])


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
