"""테스트가 쓰는 서드파티 모듈은 **선언돼 있어야 한다** (2026-09-05).

■ 무엇이 이 축을 만들었나

`PyYAML` 이 `pyproject.toml` 의 `[test]` 에 **없었다.** 그런데 compose 계약 가드
(`test_central_docker_compose.py`)와 auth-mode pairing 가드
(`test_auth_mode_pairing.py`)가 그것으로 compose 를 읽는다. 결과(실측 2026-09-05,
`pip install -e '.[test]'` 와 동일한 환경):

    PyYAML 있음   166 passed,  0 skipped
    PyYAML 없음    24 failed, 126 passed, **21 skipped**

⚠️ **24건의 빨강보다 21건의 skip 이 더 나빴다.** 그 21건은 `try/except → skipTest`
가드가 「이 환경에서는 검사하지 않는다」로 접은 것인데, 접힌 대상이 하필
서비스 census · healthcheck 배선 · 빌드 대상 파생 · auth-mode pairing 이었다.
**계약이 돌지 않는데 CI 는 초록으로 보고했다.**

그래서 정공은 가드를 추가하는 것이 아니라 **의존성을 선언하고 가드를 걷어내는 것**
이었다. 선언된 의존성의 부재는 「검사 생략」이 아니라 **환경 결함**이다.

■ 왜 이 축이 필요한가

선언은 사람이 잊는다. 그리고 잊혔다는 사실은 **그 의존성이 우연히 설치돼 있는
환경에서는 보이지 않는다** — 개발 기계에는 대개 있다. 그래서 이 시험은 선언과
실사용을 **기계가** 대조한다.

■ 하드코딩하지 않는다

모듈→배포판 매핑은 `importlib.metadata.packages_distributions()`(표준 라이브러리)
가 답한다. 이름 표를 이 파일에 적으면 그것이 곧 다음 드리프트의 씨앗이다.
1차 모듈 판정도 마찬가지로 **트리에서 파생**한다 — `conftest` 가 `scripts/` 와
`tests/` 를 `sys.path` 에 넣으므로 그 두 곳의 파일명이 1차 여부의 근거다.
"""
from __future__ import annotations

import ast
import sys
import tomllib
import unittest
from importlib.metadata import packages_distributions
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = REPO_ROOT / 'tests'
PYPROJECT = REPO_ROOT / 'pyproject.toml'


def _declared_distributions() -> set[str]:
    """`pyproject.toml` 이 선언한 배포판 전부 (런타임 + 모든 extra)."""
    project = tomllib.loads(PYPROJECT.read_text(encoding='utf-8'))['project']
    requirements = list(project.get('dependencies', []))
    for extra in project.get('optional-dependencies', {}).values():
        requirements.extend(extra)
    return {canonicalize_name(Requirement(r).name) for r in requirements}


def _imported_top_level_modules() -> dict[str, set[str]]:
    """`tests/` 가 import 하는 최상위 모듈 이름 → 그것을 import 하는 파일들."""
    found: dict[str, set[str]] = {}
    for path in sorted(TESTS_ROOT.rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split('.')[0]
                found.setdefault(top, set()).add(str(path.relative_to(REPO_ROOT)))
    return found


def _is_first_party(module: str) -> bool:
    """이 저장소가 스스로 제공하는 모듈인가 — 트리에서 파생한다.

    `conftest` 가 `scripts/` 와 `tests/` 를 `sys.path` 에 넣으므로, 그 두 디렉터리와
    저장소 루트의 파일/패키지 이름이 그대로 1차 판정의 근거다. 목록을 손으로 적지
    않는 이유는 그 목록이 곧 다음 드리프트이기 때문이다.
    """
    if module == 'fcc_test_platform':
        return True
    for base in (REPO_ROOT, REPO_ROOT / 'scripts', TESTS_ROOT):
        if (base / f'{module}.py').is_file() or (base / module / '__init__.py').is_file():
            return True
        if (base / module).is_dir():
            return True
    return False


class TestEveryThirdPartyTestImportIsDeclared(unittest.TestCase):
    """테스트가 import 하는 서드파티 모듈은 모두 선언돼 있어야 한다."""

    def test_no_test_import_relies_on_an_undeclared_distribution(self):
        declared = _declared_distributions()
        mapping = packages_distributions()
        undeclared: dict[str, tuple[str, list[str]]] = {}

        for module, files in _imported_top_level_modules().items():
            if module in sys.stdlib_module_names or _is_first_party(module):
                continue
            distributions = {canonicalize_name(d) for d in mapping.get(module, [])}
            if not distributions:
                reason = '설치돼 있지 않아 어떤 배포판이 제공하는지 알 수 없다'
            elif not (distributions & declared):
                reason = f'{sorted(distributions)} 가 제공하지만 선언되지 않았다'
            else:
                continue
            undeclared[module] = (reason, sorted(files)[:4])

        if undeclared:
            lines = [
                'tests/ 가 import 하는데 pyproject 가 선언하지 않은 모듈이 있다.',
                '',
                '⚠️ 이것을 `try/except → skipTest` 로 덮지 마라. 그러면 그 모듈을 쓰는',
                '   검사가 **꺼진 채 초록**이 된다 — 2026-09-05 에 PyYAML 로 그 일이',
                '   실제로 있었고, 계약 가드 21건이 그렇게 조용히 멈춰 있었다.',
                '   정공은 `pyproject.toml` 의 해당 extra 에 선언하는 것이다.',
                '',
            ]
            for module, (reason, files) in sorted(undeclared.items()):
                lines.append(f'  {module}: {reason}')
                lines.extend(f'      ← {f}' for f in files)
            self.fail('\n'.join(lines))

    def test_the_axis_is_not_vacuous(self):
        """비-공허성 — 파생이 실제로 서드파티를 집는다.

        1차 판정이 너무 넓어져 모든 것을 걸러내면 이 시험은 아무것도 지키지 않으면서
        영원히 초록이 된다. 그래서 **알려진 서드파티가 실제로 스캔에 잡히는지**를 본다.
        """
        modules = _imported_top_level_modules()
        third_party = {
            m for m in modules
            if m not in sys.stdlib_module_names and not _is_first_party(m)
        }
        self.assertIn(
            'yaml', third_party,
            'compose 계약 가드가 PyYAML 로 compose 를 읽는다 — 스캔이 그것을 놓쳤다면 '
            '파생이 고장난 것이다.',
        )
        self.assertIn(
            canonicalize_name('PyYAML'), _declared_distributions(),
            'PyYAML 이 선언에서 사라졌다 — 이 축이 막으려던 바로 그 상태다.',
        )
