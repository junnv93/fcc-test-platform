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

## baseline 이 한 방향으로만 줄어드는 것을 «둘»이 지킨다

  ① 그래프 축 — import-linter 의 `unmatched_ignore_imports_alerting = error`:
     위반이 해소되면 등재가 그래프에서 안 맞아 게이트가 깨진다. 고친 사람이
     등재를 지우도록 강제된다.
  ② 정책 축 — 이 파일의 `TestTheDbBaselineOnlyShrinks`: 등재 집합을 **이름으로**
     못박는다. 새 위반을 조용히 등재해 초록을 만드는 길을 막는다.

  ①만으로는 「등재를 늘려 초록으로 만들기」를 막지 못하고, ②만으로는 「해소됐는데
  등재가 남아 있기」를 막지 못한다. 개수가 아니라 이름 집합으로 재는 이유는
  `delivered_test_run_baseline.json` 과 같다 — 개수는 *고쳐진 것*과 *새로 깨진
  것*을 맞바꾼 것을 구분하지 못한다.
"""
from __future__ import annotations

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

#: BASELINE — 2026-09-05 실측(211 파일 / 919 의존)의 `app-no-db` 위반 전부.
#: 해소는 설계서 S3(DB 어댑터 이전). ⚠️ 이 집합은 **줄어들기만 한다.**
FROZEN_DB_BASELINE = frozenset({
    # ① 직접 — 모듈을 infrastructure/adapters/driven/ 으로 옮기면 사라진다.
    'fcc_test_platform.application.central_project_reference_adapter -> psycopg',
    # ② 간접 — runtime_config -> central_db_config -> psycopg 의 «진입 간선».
    #    루트 모듈이 설정과 드라이버를 함께 들고 있어 생긴다. 경유 모듈이
    #    application 밖이라 한 파일씩 보는 AST 가드로는 원리적으로 안 잡힌다.
    'fcc_test_platform.application.runtime_config -> fcc_test_platform.central_db_config',
})

_CONTRACT_PREFIX = 'importlinter:contract:'


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


class TestTheDbBaselineOnlyShrinks(unittest.TestCase):
    """정책 축 — 등재 집합을 «이름»으로 못박는다 (파일 docstring 참조)."""

    def test_the_baseline_is_exactly_the_two_measured_violations(self):
        cfg = _read(IMPORTLINTER_INI)
        declared = _as_set(cfg[f'{_CONTRACT_PREFIX}app-no-db']['ignore_imports'])
        self.assertEqual(
            set(FROZEN_DB_BASELINE), declared,
            '등재가 바뀌었다. 줄었다면 이 파일의 FROZEN_DB_BASELINE 에서도 지워라. '
            '늘었다면 그것은 새 위반을 등재해 초록을 만든 것이다 — SQL 은 '
            'infrastructure 에서만 나온다(설계서 S3).')

    def test_a_stale_entry_breaks_the_gate_rather_than_lingering(self):
        """그래프 축 — 해소된 등재가 남아 있으면 게이트가 깨져야 한다."""
        cfg = _read(IMPORTLINTER_INI)
        self.assertEqual(
            'error',
            cfg[f'{_CONTRACT_PREFIX}app-no-db']['unmatched_ignore_imports_alerting'],
            '이 값이 error 가 아니면 해소된 등재가 조용히 남아 baseline 이 '
            '한 방향으로 줄지 않는다')

    def test_the_other_two_contracts_carry_no_baseline(self):
        """레이어·순수성은 KEPT 다 — 여기 등재가 생기면 그것은 «후퇴»다."""
        cfg = _read(IMPORTLINTER_INI)
        for name in ('layers', 'purity'):
            with self.subTest(contract=name):
                self.assertNotIn(
                    'ignore_imports', cfg[f'{_CONTRACT_PREFIX}{name}'],
                    f'{name} 계약에 예외가 생겼다 — 이 둘은 실측상 위반 0건이다')


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
