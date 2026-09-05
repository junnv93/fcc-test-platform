"""아키텍처 게이트의 봉인 — `mypy.ini` · `.importlinter` (설계서 §6.1~6.2 / S1·S2).

## 이 파일이 게이트에 «붙는» 방식

이 레인의 실질 게이트는 `githooks/pre-push` → `scripts/lane_check.py` → pytest 다
(`.github/workflows/checks.yml` 은 러너 미배정으로 휴면이고, 그 파일 자신이
「검사 정의를 여기 인라인하면 두 게이트가 갈라진다」고 적는다). 그래서 새 게이트는
워크플로 YAML 이 아니라 **테스트 한 파일**로 붙는다 — 그러면 pre-push 와 (러너가
돌아온 날의) CI 가 자동으로 같은 것을 본다.

## 왜 두 층인가 — 도구가 없어도 정책은 봉인된다

`TestTheGatesActuallyRun` 은 도구가 설치돼 있을 때만 돈다. 그 앞의 세 클래스는
**stdlib 만으로** 설정 자체를 검사하므로 어느 환경에서도 돈다.

⚠️ 이 분리가 없으면 게이트가 「도구 미설치」와 「위반 없음」을 같은 초록으로
보고한다 — 이 레포가 여러 번 값을 치른 형태다(`lane_check` 의 수집 0개 문제,
`checks.yml` 의 러너 미배정 문제가 같은 계열이다).

## baseline 은 이제 «공집합»이다 (2026-09-05 — 설계서 S3 착지)

세 계약 모두 예외 **0건**으로 KEPT 다. 그래서 정책 축이 「이 두 이름만 허용」에서
「어느 계약도 예외를 갖지 않는다」로 바뀌었다 — 바닥에 닿은 뒤에는 집합을 이름으로
세는 것보다 **부재를 요구**하는 쪽이 단순하면서 더 세다(허용 목록이 없으면 늘릴
목록도 없다).

  ① 그래프 축 — import-linter 의 `unmatched_ignore_imports_alerting = error`:
     누군가 다시 등재를 넣고 그 위반이 해소되면 게이트가 스스로 깨져 등재를
     지우도록 강제한다. 지금은 등재가 없어 «놀고» 있지만 미래를 위해 켜 둔다.
  ② 정책 축 — 이 파일의 `TestNoContractCarriesABaseline`: 세 계약 어디에도
     `ignore_imports` 가 없어야 한다. 새 위반을 조용히 등재해 초록을 만드는
     길을 막는다.

해소되기 전 두 등재가 무엇이었고 각각 어떻게 처분됐는지는 `.importlinter` 의
계약 3 주석이 **이름으로** 갖는다. 장부는 코드 옆에 두고 검사는 부재만 묻는다 —
검사가 장부를 겸하면 장부를 고치려고 검사를 무르는 날이 온다.
"""
from __future__ import annotations

import ast
import configparser
import importlib.util
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MYPY_INI = REPO_ROOT / 'mypy.ini'
IMPORTLINTER_INI = REPO_ROOT / '.importlinter'

#: strict 를 강제하는 범위. 저장소 전체가 아니라 domain/* 이다 — 도메인이 순수
#: (서드파티 의존 0)이라 타입이 가장 잘 서고, 거기서 얻는 규율이 가장 싸다.
STRICT_SECTION = 'mypy-fcc_test_platform.domain.*'

#: 예외를 가져서는 안 되는 계약 — 즉 **전부**다. 2026-09-05 S3 착지로 마지막
#: 등재 2건(`app-no-db`)이 해소되면서 세 계약이 나란히 예외 0건이 됐다.
#: ⚠️ 여기서 이름을 빼는 것은 「그 계약에 예외를 허용한다」는 뜻이다. 그러지 마라.
CONTRACTS_THAT_CARRY_NO_BASELINE = ('layers', 'purity', 'app-no-db')

_CONTRACT_PREFIX = 'importlinter:contract:'

#: S3 가 드라이버 결박을 모아 둔 «유일한» 자리. 이 모듈의 docstring 이 frozen-exe
#: 안전(데스크톱 빌드가 PostgreSQL 드라이버를 0바이트 싣는다)을 약속하므로,
#: 그 약속을 여기서 기계가 지킨다. 약속만 있고 검사가 없으면 주석과 같은 효력이다.
DRIVER_ADAPTER = (
    REPO_ROOT / 'fcc_test_platform' / 'infrastructure' / 'adapters' / 'driven'
    / 'central_db_connection.py'
)

_DRIVER_ROOTS = frozenset({'psycopg', 'psycopg2', 'asyncpg'})


def _driver_import_lines(tree: ast.AST) -> tuple[list[int], list[int]]:
    """(모듈 최상위, 전체) 드라이버 import 의 줄 번호.

    «전체»는 ``ast.walk`` 라 함수 안 지연 import 까지 센다 — frozen-exe 판정에서
    중요한 것이 정확히 그 구분이기 때문이다.
    """
    def is_driver(node: ast.AST) -> bool:
        if isinstance(node, ast.Import):
            return any(a.name.split('.')[0] in _DRIVER_ROOTS for a in node.names)
        if isinstance(node, ast.ImportFrom):
            return bool(node.module) and node.module.split('.')[0] in _DRIVER_ROOTS
        return False

    top = [n.lineno for n in tree.body if is_driver(n)]
    every = [n.lineno for n in ast.walk(tree) if is_driver(n)]
    return top, every


class TestTheDriverBindingStaysLazy(unittest.TestCase):
    """frozen-exe — 드라이버는 «한 파일의 함수 안»에서만 묶인다 (설계서 S3).

    ⚠️ **팔이 둘인 이유.** 「모듈 최상위에 psycopg 가 없다」만 물으면, 누군가 그
    결박을 통째로 지웠을 때도 초록이다 — 참이지만 아무것도 재지 않는 참이 된다.
    이 저장소는 그 형태를 이미 안다: 경로를 하드코딩한 검사가 이관 후 껍데기를
    읽으면 단언이 전부 통과하면서 아무것도 안 지킨다. 그래서 둘째 팔이
    **결박이 실제로 거기 있는지**를 함께 묻는다.

    왜 여기가 아니라 `api_composition` 이 아닌가: 2026-09-05 이전에는 그쪽에
    결박이 있었고 `test_platform_equipment_list_api` 가 그 자리를 지켰다. S3 가
    결박을 이 파일로 옮겼으므로 **지키는 자리도 함께 옮겨야** 한다. 옮기지 않으면
    옛 검사는 자기 파일에 대해서는 여전히 참이면서 성질을 놓친다.
    """

    def test_the_driver_is_bound_lazily_and_the_binding_is_actually_there(self):
        self.assertTrue(
            DRIVER_ADAPTER.is_file(),
            f'{DRIVER_ADAPTER} 가 없다 — S3 의 드라이버 결박 자리가 사라졌다')
        tree = ast.parse(DRIVER_ADAPTER.read_text(encoding='utf-8'))
        top, every = _driver_import_lines(tree)

        self.assertEqual(
            [], top,
            f'{DRIVER_ADAPTER.name} 의 모듈 최상위에 드라이버 import 가 생겼다(줄 {top}). '
            'import 하는 것만으로 psycopg 가 딸려 오면 데스크톱 빌드가 드라이버를 싣는다.')

        # ⚠️ 안티-공허 팔 — 대상의 «부재»로 초록이 되는 길을 막는다.
        self.assertTrue(
            every,
            f'{DRIVER_ADAPTER.name} 에 드라이버 import 가 하나도 없다. 위 팔이 '
            '「최상위에 없다」로 통과했지만 그것은 결박이 사라졌다는 뜻일 수 있다 — '
            '결박을 옮겼다면 이 검사도 새 자리를 가리키게 고쳐라.')


def _read(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read_string(path.read_text(encoding='utf-8'), source=str(path))
    return parser


def _as_set(raw: str) -> set[str]:
    return {line.strip() for line in raw.splitlines() if line.strip()}


def _sibling_lane() -> Path:
    return REPO_ROOT.parent / 'fcc-test-contracts'


def _tool_env() -> dict[str, str]:
    """이 레인은 혼자 돌지 않는다 — 형제 레인이 sys.path 에 있어야 한다.

    근거는 `EXTRACTED_FROM.md` §「이 상자가 혼자 도는가」다.
    """
    sib = _sibling_lane()
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(
        [str(REPO_ROOT), str(sib), str(sib / 'packages' / 'fcc-test-kernel')]
    )
    env['MYPYPATH'] = str(REPO_ROOT)
    return env


class TestTheMypyGateIsDeclared(unittest.TestCase):
    """S1 — `domain/*` 에 strict 가 «선언»돼 있는가."""

    def test_the_config_does_not_live_in_the_delivered_pyproject(self):
        """⚠️ 이 팔이 설정 파일이 따로 있는 이유다.

        루트 `pyproject.toml` 은 `.extraction-layout.json` 이 예약한 배송 경로다
        (`packaging/fcc-test-platform/pyproject.toml` → `pyproject.toml`). 거기에
        게이트 설정을 두면 배송이 그 파일을 이름으로 대며 거부한다.
        """
        self.assertTrue(MYPY_INI.is_file(), 'mypy.ini 가 없다 — 게이트가 선언되지 않았다')
        pyproject = (REPO_ROOT / 'pyproject.toml').read_text(encoding='utf-8')
        self.assertNotIn(
            '[tool.mypy]', pyproject,
            '배송이 관리하는 pyproject.toml 에 mypy 설정이 들어갔다 — 배송이 거부한다')

    def test_the_domain_is_strict_and_the_rest_is_not_yet(self):
        cfg = _read(MYPY_INI)
        self.assertIn(STRICT_SECTION, cfg.sections(),
                      f'{STRICT_SECTION} 절이 없다 — domain 에 strict 가 걸리지 않는다')
        self.assertTrue(
            cfg.getboolean(STRICT_SECTION, 'disallow_untyped_defs'),
            'domain/* 의 disallow_untyped_defs 가 켜져 있지 않다')
        self.assertFalse(
            cfg.getboolean('mypy', 'disallow_untyped_defs'),
            '저장소 전체 strict 는 아직 합의된 범위가 아니다 — 범위를 넓히려면 '
            '설계서를 먼저 고쳐라')


class TestTheImportLinterContractsAreDeclared(unittest.TestCase):
    """S2 — 세 계약이 «선언»돼 있는가."""

    def test_all_three_contracts_are_present(self):
        cfg = _read(IMPORTLINTER_INI)
        contracts = {
            s[len(_CONTRACT_PREFIX):]: cfg[s]
            for s in cfg.sections() if s.startswith(_CONTRACT_PREFIX)
        }
        self.assertEqual({'layers', 'purity', 'app-no-db'}, set(contracts))
        self.assertEqual('layers', contracts['layers']['type'])
        self.assertEqual('forbidden', contracts['purity']['type'])
        self.assertEqual('forbidden', contracts['app-no-db']['type'])

    def test_the_four_layers_are_ordered_top_down(self):
        cfg = _read(IMPORTLINTER_INI)
        self.assertEqual(
            [
                'fcc_test_platform.api',
                'fcc_test_platform.infrastructure',
                'fcc_test_platform.application',
                'fcc_test_platform.domain',
            ],
            [ln.strip() for ln in
             cfg[f'{_CONTRACT_PREFIX}layers']['layers'].splitlines() if ln.strip()],
            '레이어 순서가 바뀌면 계약이 «반대»를 검사한다 — 위반이 조용히 통과한다')

    def test_external_packages_are_included(self):
        """⚠️ 없으면 실행 «자체»가 거부된다 — forbidden 에 외부 패키지가 있기 때문이다."""
        cfg = _read(IMPORTLINTER_INI)
        self.assertTrue(cfg.getboolean('importlinter', 'include_external_packages'))


class TestNoContractCarriesABaseline(unittest.TestCase):
    """정책 축 — 세 계약 어디에도 예외가 없어야 한다 (파일 docstring 참조)."""

    def test_no_contract_carries_a_baseline(self):
        """⚠️ 이 팔이 「새 위반을 등재해 초록 만들기」를 막는 유일한 자리다.

        그래프 축(`unmatched_ignore_imports_alerting`)은 **해소된** 등재만 잡는다.
        새로 «추가된» 등재는 그래프와 완벽히 일치하므로 그쪽에서는 초록이다.
        """
        cfg = _read(IMPORTLINTER_INI)
        for name in CONTRACTS_THAT_CARRY_NO_BASELINE:
            with self.subTest(contract=name):
                section = cfg[f'{_CONTRACT_PREFIX}{name}']
                self.assertEqual(
                    set(), _as_set(section.get('ignore_imports', '')),
                    f'{name} 계약에 예외가 생겼다. 세 계약은 실측상 위반 0건이다 — '
                    f'새 위반이 났다면 답은 등재가 아니라 코드다. SQL 은 '
                    f'infrastructure 에서만 나온다(설계서 S3).')

    def test_a_stale_entry_breaks_the_gate_rather_than_lingering(self):
        """그래프 축 — 누군가 다시 등재를 넣더라도 해소되면 게이트가 깨진다."""
        cfg = _read(IMPORTLINTER_INI)
        self.assertEqual(
            'error',
            cfg[f'{_CONTRACT_PREFIX}app-no-db']['unmatched_ignore_imports_alerting'],
            '이 값이 error 가 아니면 해소된 등재가 조용히 남아 baseline 이 '
            '한 방향으로 줄지 않는다')


#: import-linter 의 진입점. ⚠️ `-m importlinter.cli` 가 **아니다** — 그 패키지에는
#: `__main__.py` 가 없어서 모듈을 import 만 하고 **아무 출력 없이 exit=0** 으로
#: 끝난다. 2026-09-05 에 이 자리에서 실제로 겪었다: 게이트가 초록이었는데 계약을
#: 하나도 검사하지 않고 있었다. 콘솔 스크립트가 부르는 것과 같은 것을 부른다
#: (`entry_points.txt`: lint-imports = importlinter.cli:lint_imports_command).
_LINT_IMPORTS_ENTRY = 'from importlinter.cli import lint_imports_command; lint_imports_command()'


class TestTheGatesActuallyRun(unittest.TestCase):
    """선언이 아니라 «실행». 도구가 없으면 skip 하되, 그 skip 이 보이게 한다.

    ⚠️ **종료코드만 보지 않는다.** 잘못된 진입점 · 수집 0개 · 러너 미배정은 전부
    「exit=0, 한 일 없음」으로 나타나고, 그것은 「위반 없음」과 구분되지 않는다.
    그래서 각 팔은 도구가 **일했다는 증거**(검사한 파일 수 · 분석한 의존 수)를
    출력에서 함께 확인한다. 이 레포가 같은 계열의 값을 이미 세 번 치렀다 —
    `lane_check` 의 `--continue-on-collection-errors`, `checks.yml` 의 러너 미배정,
    그리고 위의 `-m importlinter.cli`.
    """

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv, cwd=str(REPO_ROOT), env=_tool_env(),
            capture_output=True, text=True, timeout=900,
        )

    @unittest.skipIf(importlib.util.find_spec('mypy') is None,
                     'mypy 미설치 — 게이트를 돌리려면: pip install mypy')
    def test_the_domain_has_no_untyped_defs(self):
        done = self._run([sys.executable, '-m', 'mypy', '-p', 'fcc_test_platform.domain'])
        report = f'{done.stdout}\n{done.stderr}'
        # 증거 먼저 — 「한 파일도 안 봤다」가 「오류 없다」로 읽히지 않게.
        checked = re.search(r'(\d+) source files?', done.stdout)
        self.assertIsNotNone(
            checked, f'mypy 가 검사한 파일 수를 보고하지 않았다 — 돌지 않았다:\n{report}')
        self.assertGreater(
            int(checked.group(1)), 0, f'mypy 가 0개를 검사했다 — 게이트가 공허하다:\n{report}')
        self.assertEqual(
            0, done.returncode, f'domain/* strict 가 깨졌다 (설계서 S1):\n{report}')

    @unittest.skipIf(importlib.util.find_spec('importlinter') is None,
                     'import-linter 미설치 — 게이트를 돌리려면: pip install import-linter')
    def test_the_three_contracts_hold(self):
        if not _sibling_lane().is_dir():
            self.skipTest(f'형제 레인이 없다: {_sibling_lane()} (이 상자는 혼자 돌지 않는다)')
        done = self._run([sys.executable, '-c', _LINT_IMPORTS_ENTRY, '--no-cache'])
        report = f'{done.stdout}\n{done.stderr}'
        analyzed = re.search(r'Analyzed (\d+) files, (\d+) dependencies', done.stdout)
        self.assertIsNotNone(
            analyzed, f'import-linter 가 분석 규모를 보고하지 않았다 — 돌지 않았다:\n{report}')
        self.assertGreater(
            int(analyzed.group(2)), 0,
            f'의존 0건을 분석했다 — 그래프가 비었다면 어떤 계약도 깨질 수 없다:\n{report}')
        self.assertEqual(
            3, done.stdout.count(' KEPT'),
            f'세 계약이 모두 KEPT 로 보고되지 않았다 (설계서 S2):\n{report}')
        self.assertEqual(
            0, done.returncode, f'경계 계약이 깨졌다 (설계서 S2):\n{report}')


if __name__ == '__main__':
    unittest.main()
