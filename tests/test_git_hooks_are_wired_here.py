"""이 체크아웃에 차단형 git 훅이 **실제로 걸려 있는가** (2026-08-31).

⚠️ 모노레포 `tests/test_git_hooks_are_wired_here.py` 에서 왔다. 이 레포에서는
**더 중요하다** — GitHub Actions 가 결제 계층에서 러너를 배정받지 못하는 동안
(`.github/workflows/checks.yml` 이 그 사실을 자기 머리말에 적는다) `githooks/pre-push`
가 **오늘 실제로 막는 유일한 게이트**이고, 그것은 clone 마다 opt-in 이다.

실측된 피해 (2026-08-31): 이 clone 이 미설정이라 push 때 아무것도 돌지 않았고,
그 사이 `scripts/lane_check.py` 의 선언 드리프트(선언에 없는 실패 4 · 낡은 선언 4)가
`origin/main` 에 **이미 쌓여 있었는데 아무도 못 봤다.**

⚠️ **훅은 자기가 안 걸렸다는 사실을 말할 수 없다** — 안 도니까. 그래서 그 사실은
훅 밖에서 물어야 하고, 여기가 그 자리다.

⚠️ 갓 클론한 트리에서 이 검사는 **빨간불이다.** 오탐이 아니라 정확한 관측이다 —
그 트리에는 차단형 게이트가 하나도 없다. 실패 문구가 고치는 명령을 이름으로 댄다.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import unittest

project_root = Path(__file__).resolve().parent.parent

#: 추적되는 훅 디렉터리. `README.md` §설치가 안내하는 값과 같다.
TRACKED_HOOKS_DIR = 'githooks'
INSTALL_COMMAND = 'git config core.hooksPath githooks'


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ['git', *args], cwd=str(project_root),
        capture_output=True, text=True, check=False,
    )


def _tracked_hooks() -> list[str]:
    """추적된 훅 이름 — **손으로 나열하지 않는다.**

    나열하면 훅이 하나 늘 때 이 검사가 조용히 그것을 안 본다. 「세 개를 확인했다」와
    「있는 것을 전부 확인했다」는 다른 명제다.
    """
    listing = _git('ls-files', '--', TRACKED_HOOKS_DIR)
    return sorted(
        Path(line).name for line in listing.stdout.split() if line.strip()
    )


class TestTheBlockingHooksAreWiredInThisCheckout(unittest.TestCase):

    def test_there_is_something_to_wire(self) -> None:
        """⚠️ 훅이 0개면 아래 단언들은 아무것도 요구하지 않으면서 통과한다."""
        hooks = _tracked_hooks()
        self.assertTrue(
            hooks,
            f'{TRACKED_HOOKS_DIR} 에 추적된 훅이 없다 — 이 검사가 공허하다',
        )

    def test_core_hooks_path_points_at_the_tracked_directory(self) -> None:
        configured = _git('config', 'core.hooksPath').stdout.strip()
        self.assertTrue(
            configured,
            '`core.hooksPath` 가 **미설정**이다 — 이 체크아웃에서 차단형 훅이 하나도 '
            '돌지 않는다. 자평 형식 위반·미청구 브랜치 커밋·낡은 머지 base 가 전부 '
            f'그냥 나간다.\n고치기: {INSTALL_COMMAND}',
        )
        resolved = Path(configured)
        if not resolved.is_absolute():
            resolved = project_root / resolved
        self.assertEqual(
            resolved.resolve(), (project_root / TRACKED_HOOKS_DIR).resolve(),
            f'`core.hooksPath` 가 {configured!r} 를 가리킨다 — 추적된 '
            f'{TRACKED_HOOKS_DIR} 가 아니다. ⚠️ 「설정돼 있다」와 「우리 훅이 돈다」는 '
            f'다른 축이다: 빈 디렉터리를 가리켜도 설정은 돼 있다.\n'
            f'고치기: {INSTALL_COMMAND}',
        )

    def test_every_tracked_hook_is_present_and_executable(self) -> None:
        """⚠️ 실행 비트가 없으면 git 은 그 훅을 **조용히 건너뛴다.**

        경로가 맞는 것과 세 훅이 다 도는 것은 다른 축이다.
        """
        hooks_dir = project_root / TRACKED_HOOKS_DIR
        broken: list[str] = []
        for name in _tracked_hooks():
            path = hooks_dir / name
            if not path.is_file():
                broken.append(f'{name}: 파일이 없다')
            elif not os.access(path, os.X_OK):
                broken.append(f'{name}: 실행 비트가 없다 — git 이 조용히 건너뛴다')
        self.assertEqual(
            [], broken,
            '추적된 훅이 실행되지 않는 상태다: ' + '; '.join(broken),
        )


class TestTheCheckWouldSeeAnUnwiredCheckout(unittest.TestCase):
    """⚠️ 비-공허성 — 위 검사가 *늘* 통과하는 것은 아닌지 합성 트리로 확인한다.

    이 검사 자체가 「설정돼 있다」에만 반응하고 「엉뚱한 곳을 가리킨다」를 놓치면,
    다음 사람이 훅 디렉터리를 옮긴 날 조용히 초록이 된다.
    """

    def _probe(self, tmp: Path, value: str | None) -> Path:
        subprocess.run(['git', 'init', '-q', str(tmp)], check=True)
        if value is not None:
            subprocess.run(
                ['git', 'config', 'core.hooksPath', value],
                cwd=str(tmp), check=True,
            )
        got = subprocess.run(
            ['git', 'config', 'core.hooksPath'], cwd=str(tmp),
            capture_output=True, text=True, check=False,
        ).stdout.strip()
        return Path(got) if got else Path('')

    def test_an_unset_checkout_reads_as_empty(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            got = self._probe(Path(raw) / 'unset', None)
            self.assertEqual(
                Path(''), got,
                '미설정 트리가 빈 값으로 읽히지 않으면 위 단언은 그 상태를 못 본다',
            )

    def test_a_checkout_pointed_elsewhere_does_not_match_the_tracked_dir(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / 'elsewhere'
            got = self._probe(root, 'some/other/hooks')
            self.assertNotEqual(
                (project_root / TRACKED_HOOKS_DIR).resolve(),
                (root / got).resolve(),
                '엉뚱한 디렉터리를 가리켜도 통과한다면 이 축은 «설정 여부»만 보는 것이다',
            )


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
