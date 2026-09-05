"""껍데기가 «실제로 도는가» — 알맹이를 옮긴 뒤에도 (2026-09-05).

**이 축은 형제 봉인들이 구조적으로 볼 수 없는 것을 본다.** 2026-09-05 웨이브에서
17개 껍데기를 만든 뒤 전부 `--help` 로 불러 보니 **2건이 죽어 있었다.** 그런데 그때
수집 에러 0 · 이름 해소 0 · 미해소 내부 import 0 · `lane_check` 「선언 0 / 관측 0 ✅」
였다. 이유는 하나다 — **어떤 시험도 껍데기를 부르지 않았다.**

| 죽어 있던 것 | 왜 |
|---|---|
| `scripts/_keycloak_chamber_admin.py` | 알맹이에 `main()` 이 없다 — CLI 가 아니라 라이브러리인데 껍데기 템플릿이 `from … import main` 을 했다 |
| `scripts/platform_provider_identity_live_proof.py` | 알맹이가 «모듈 최상위»에서 실행된다 — `DSN = sys.argv[1]` 이 import 시점에 돈다 |

즉 「알맹이에 `main()` 이 있고 import 는 부수효과가 없다」는 **껍데기 템플릿의
가정**이고, 그 가정을 아무도 검사하지 않았다. 여기서 검사한다.

⚠️ **하위 프로세스로 «실제로 부른다».** import 만으로는 부족하다 — 옛 형태에서
`provider_identity_live_proof` 는 import 하는 것만으로 라이브 DB 에 붙으려 했고,
그것이 바로 이 검사가 잡아야 할 상태다. 그리고 운영자가 실제로 하는 일이
`python3 scripts/…` 이므로 그 경로를 그대로 밟는다.

⚠️ **`--help` 를 쓰는 이유**: 부작용 없이 「진입점이 해소되고 인자 파서가 선다」를
묻는 가장 싼 질문이다. 이 도구들은 라이브 DB·Keycloak·브라우저를 요구하므로 실제
동작을 부를 수는 없다. 그 한계를 알고 쓴다 — 이 검사는 **「배선이 됐나」**를 묻지
「올바로 동작하나」를 묻지 않는다.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO_ROOT / 'scripts'
#: 알맹이가 패키지로 간 껍데기 = 자기 배포판에서 무언가를 가져오는 `scripts/` 파일.
#: 목록을 손으로 적지 않는다 — 손목록은 다음 이관에서 조용히 낡는다.
_PACKAGE = 'fcc_test_platform'


def _delegating_shells() -> list[Path]:
    found = []
    for path in sorted(_SCRIPTS.glob('*.py')):
        if path.name == '__init__.py':
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:  # pragma: no cover — 레인에 없다
            continue
        imports_package = any(
            isinstance(n, ast.ImportFrom) and n.module and n.module.startswith(_PACKAGE + '.')
            for n in tree.body
        )
        #: 껍데기는 짧다. 알맹이를 그대로 가진 스크립트까지 부르면 이 검사가
        #: 라이브 자원을 요구하는 도구를 통째로 깨우게 된다.
        if imports_package and len(path.read_text(encoding='utf-8').splitlines()) <= 40:
            found.append(path)
    return found


class TestEveryShellStillRuns(unittest.TestCase):
    """진탐 축 — 위임 껍데기 전부가 `--help` 에 답한다."""

    def test_every_delegating_shell_answers_help(self):
        shells = _delegating_shells()
        # 비-공허성 — 대상을 하나도 안 찾고 초록이 되는 것을 막는다.
        self.assertGreater(len(shells), 10, '위임 껍데기를 거의 못 찾았다 — 축이 꺼졌다')

        broken: list[str] = []
        for shell in shells:
            with self.subTest(shell=shell.name):
                completed = subprocess.run(
                    [sys.executable, str(shell), '--help'],
                    capture_output=True, text=True, timeout=60, cwd=str(_REPO_ROOT),
                )
                if completed.returncode != 0:
                    tail = (completed.stderr or completed.stdout).strip().splitlines()
                    broken.append(f'{shell.name}: rc={completed.returncode} {tail[-1] if tail else ""}')

        self.assertEqual(
            broken, [],
            '이 껍데기들이 도는 데 실패한다 — 알맹이를 옮기며 배선이 끊겼을 수 있다. '
            '알맹이에 `main()` 이 있는지, 그리고 그 모듈이 «import 만으로» 실행되지 '
            '않는지 보라:\n' + '\n'.join(broken),
        )


class TestTheGutsDoNotRunOnImport(unittest.TestCase):
    """알맹이가 import 시점에 실행되면 이 상자를 설치한 누구든 그것을 밟는다."""

    def test_no_packaged_cli_executes_at_module_level(self):
        offenders: list[str] = []
        scanned = 0
        for path in sorted((_REPO_ROOT / _PACKAGE).glob('*_cli.py')):
            scanned += 1
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in tree.body:
                if isinstance(node, ast.If) and '__name__' in ast.dump(node.test):
                    continue
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                    offenders.append(f'{path.name}:{node.lineno} {ast.unparse(node.value)[:60]}')
                if isinstance(node, ast.Assign) and 'argv' in ast.unparse(node.value):
                    offenders.append(f'{path.name}:{node.lineno} {ast.unparse(node)[:60]}')

        self.assertGreater(scanned, 10, '검사 대상 CLI 모듈이 갑자기 줄었다')
        self.assertEqual(
            offenders, [],
            '이 자리들은 «import 하는 것만으로» 돈다 — 휠이 이 모듈을 나르므로 이 상자를 '
            '설치한 누구든 그 이름을 import 하면 밟는다. 본문을 `main()` 안으로 넣어라:\n'
            + '\n'.join(offenders),
        )


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
