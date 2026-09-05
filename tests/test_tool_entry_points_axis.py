"""운영 도구의 진입점 선언이 패키지와 «어긋날 수 없게» 한다 (2026-09-05).

## 왜 선언이 필요했나

`scripts/` 는 파이썬 패키지가 아니라 휠이 나르지 못한다. 2026-09-05 에 도구 로직
14,268줄을 패키지로 옮겨 휠이 나르게 됐지만, **부르는 방법이 없었다** — 이 배포판을
설치해도 명령이 하나도 생기지 않았다(태그 `v0.1.9` 실측: 진입점 0개). 그래서 이
레인을 소비하는 쪽은 도구를 부르려고 이 저장소를 통째로 들고 있어야 했고, 그것이
「사본이 둘」을 유지시키던 마지막 이유였다.

## 왜 «파생 + 봉인» 이지 «동적 생성» 이 아닌가

setuptools 는 `dynamic = ["scripts"]` 로 표를 파일에서 읽을 수 있다. 그러나 그것은
**목록을 다른 파일로 옮길 뿐** 손으로 유지하는 사실은 그대로이고, 빌드 시점에
패키지를 들여다보지도 못한다. PEP 621 이 정적 선언을 요구하는 이유가 있다 —
**코드를 실행하지 않고 메타데이터를 읽을 수 있어야** 한다.

그래서 표준 형태는 「정적 선언 + 드리프트를 불가능하게 만드는 검사」다. 이 파일이
그 검사다. SSOT 는 패키지 자신이고, 표는 그 투영이다.

## SSOT 규칙

    fcc_test_platform/<name>_cli.py 가 최상위 `main()` 을 가지면 그것이 도구다.

⚠️ 실측 2026-09-05 에 이 규칙은 **손질 없이** 성립했다 — `*_cli.py` 인데 `main()`
없는 것 0개, `_cli` 가 아닌데 `main()` 있는 것 0개. 예외 목록이 필요 없다는 것이
이 규칙을 고른 이유다. **예외 목록을 가진 규칙은 그 목록이 조용히 자란다.**
"""
from __future__ import annotations

import ast
import fnmatch
import tomllib
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO_ROOT / 'pyproject.toml'
_PACKAGE = 'fcc_test_platform'
_COMMAND_PREFIX = 'fcc-platform-'


def _pyproject() -> dict:
    return tomllib.loads(_PYPROJECT.read_text(encoding='utf-8'))


def command_name(module_stem: str) -> str:
    """모듈 이름에서 명령 이름을 «파생»한다 — 도구마다 정하지 않는다."""
    return _COMMAND_PREFIX + module_stem[: -len('_cli')].replace('_', '-')


def discovered_tools() -> dict[str, str]:
    """SSOT — 패키지에 실재하는 도구. `{명령 이름: target}`."""
    tools: dict[str, str] = {}
    for path in sorted((_REPO_ROOT / _PACKAGE).glob('*_cli.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        has_main = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'main'
            for node in tree.body
        )
        if has_main:
            tools[command_name(path.stem)] = f'{_PACKAGE}.{path.stem}:main'
    return tools


class TestTheTableMirrorsThePackage(unittest.TestCase):
    """진탐 축 — 선언과 패키지가 정확히 같다. 양방향이다."""

    def setUp(self):
        self.declared = _pyproject()['project'].get('scripts', {})
        self.discovered = discovered_tools()

    def test_every_tool_in_the_package_has_a_command(self):
        missing = sorted(set(self.discovered) - set(self.declared))
        # 비-공허성 — 도구를 하나도 못 찾고 초록이 되는 것을 막는다.
        self.assertGreater(len(self.discovered), 20, '도구를 거의 못 찾았다 — 축이 꺼졌다')
        self.assertEqual(
            missing, [],
            '이 도구들이 패키지에 있는데 명령이 없다 — 설치해도 부를 수 없다. '
            '`[project.scripts]` 에 더하라:\n'
            + '\n'.join(f'  {c} = "{self.discovered[c]}"' for c in missing),
        )

    def test_every_command_points_at_a_tool_that_exists(self):
        """⚠️ 반대 방향 — 도구가 사라졌는데 명령이 남으면 설치가 깨진다."""
        stale = sorted(set(self.declared) - set(self.discovered))
        self.assertEqual(
            stale, [],
            '이 명령들이 가리키는 도구가 패키지에 없다 — 설치본에서 부르면 '
            'ModuleNotFoundError 다. 도구를 옮겼다면 이 표도 따라 옮겨라:\n'
            + '\n'.join(f'  {c} = "{self.declared[c]}"' for c in stale),
        )

    def test_each_command_points_at_the_expected_target(self):
        """이름은 맞는데 target 이 다른 자리 — 표를 손으로 고치면 여기서 걸린다."""
        wrong = {
            c: (self.declared[c], self.discovered[c])
            for c in sorted(set(self.declared) & set(self.discovered))
            if self.declared[c] != self.discovered[c]
        }
        self.assertEqual(
            wrong, {},
            'target 이 파생 규칙과 다르다 (선언 vs 규칙): ' + repr(wrong),
        )


class TestNoCommandCanEscapeTheWheel(unittest.TestCase):
    """⚠️ 이 축은 «모든 게이트가 초록인» 결함을 막는다.

    휠이 싣는 것은 `[tool.setuptools.packages.find].include` 가 고른 것뿐이다.
    그런데 로컬·CI·pre-push 는 모두 **editable** 설치를 쓰므로 소스 트리가
    `sys.path` 에 얹히고, `scripts.*` 도 import 된다. 실측 2026-09-05:

        editable 설치            `import scripts` → 성공
        태그 v0.1.9 휠(비-editable) `import scripts` → 실패

    그래서 target 을 `scripts.platform_db_migrate:main` 로 쓰면 **어느 게이트도
    잡지 못하고 운영자 기계에서만 `ModuleNotFoundError`** 가 난다.

    ⚠️ 이 검사는 그래서 «무엇이 import 되는가»를 묻지 않는다 — 그 질문은 기계마다
    답이 다르다. **선언끼리 대조한다**: target 의 최상위 패키지가 휠이 싣겠다고
    선언한 패턴에 걸리는가. 어느 기계에서도 같은 답이 나온다.
    """

    def test_every_target_module_is_carried_by_the_wheel(self):
        data = _pyproject()
        declared = data['project'].get('scripts', {})
        include = data['tool']['setuptools']['packages']['find']['include']

        # 비-공허성 ① — 선언이 비어 있으면 이 검사는 아무것도 말하지 않는다.
        self.assertTrue(declared, '진입점 선언이 비었다 — 이 초록은 공허하다')
        # 비-공허성 ② — include 패턴을 못 읽으면 아래 대조가 무의미하다.
        self.assertTrue(include, 'packages.find.include 를 못 읽었다')

        escaped = {}
        for name, target in sorted(declared.items()):
            top = target.split(':', 1)[0].split('.', 1)[0]
            if not any(fnmatch.fnmatch(top, pattern) for pattern in include):
                escaped[name] = target

        self.assertEqual(
            escaped, {},
            '이 명령들의 target 이 휠에 실리지 않는 곳을 가리킨다. editable 설치'
            '(로컬·CI·pre-push)에서는 돌고 **실제 설치본에서만** 죽는다 — 어느 '
            f'게이트도 잡지 못하는 형태다. 휠이 싣는 것: {include}\n'
            + '\n'.join(f'  {c} = "{t}"' for c, t in escaped.items()),
        )


class TestCommandNamesAreDerivedNotInvented(unittest.TestCase):
    """이름을 손으로 지으면 규칙이 없어지고, 규칙이 없으면 다음 도구가 제멋대로다."""

    def test_every_command_name_follows_the_rule(self):
        declared = _pyproject()['project'].get('scripts', {})
        self.assertTrue(declared, '진입점 선언이 비었다 — 이 초록은 공허하다')
        offenders = [
            c for c, target in sorted(declared.items())
            if c != command_name(target.split(':', 1)[0].rsplit('.', 1)[-1])
        ]
        self.assertEqual(
            offenders, [],
            f'이 명령 이름이 파생 규칙(`{_COMMAND_PREFIX}` + 모듈에서 `_cli` 를 떼고 '
            f'`_`→`-`)과 다르다: {offenders}',
        )

    def test_no_two_commands_collide(self):
        declared = _pyproject()['project'].get('scripts', {})
        self.assertEqual(len(declared), len(set(declared)), '명령 이름이 겹친다')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
