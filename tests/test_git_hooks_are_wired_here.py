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

#: git 이 **저장소 위치**를 결정할 때 `cwd` 를 이기는 환경변수들.
#:
#: ⚠️ 전부 지우지 않는다 — `GIT_AUTHOR_*` 나 `GIT_CONFIG_*` 는 위치와 무관하고,
#: 넓게 지우면 이 헬퍼가 「위치를 격리한다」가 아니라 「git 을 다르게 만든다」가 된다.
#: 지우는 것은 *어느 저장소인가* 를 바꾸는 것뿐이다.
GIT_REPO_LOCATION_ENV = (
    'GIT_DIR',
    'GIT_WORK_TREE',
    'GIT_COMMON_DIR',
    'GIT_INDEX_FILE',
    'GIT_OBJECT_DIRECTORY',
    'GIT_NAMESPACE',
)


def _git_env_without_repo_location() -> dict[str, str]:
    """부모가 가리키는 저장소를 물려받지 않는 환경.

    훅 안에서 돌 때만 달라지고, 훅 밖에서는 그 변수들이 애초에 없어 **무변경**이다.
    """
    env = dict(os.environ)
    for name in GIT_REPO_LOCATION_ENV:
        env.pop(name, None)
    return env



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
        """⚠️ ``cwd`` 는 격리가 아니다 — 환경이 아무 말도 안 할 때만 격리다.

        git 의 저장소 위치 결정은 **환경변수가 `cwd` 를 이긴다.** 그리고 git 은
        훅을 실행할 때 `GIT_DIR` 을 물려준다 — 곧 이 검사가 **실제로 의미를 갖는
        유일한 문맥**에서 아래 두 명령이 임시 트리가 아니라 **진짜 저장소**를
        향한다는 뜻이다.

        실측된 피해 (2026-08-31, 링크드 워크트리 형상):

            GIT_DIR=<repo>/.git/worktrees/<name>
            BEFORE: bare=false hooksPath=githooks
            AFTER : bare=true  hooksPath=some/other/hooks

        즉 **push 한 번이 pre-push 를 영구히 껐다.** 첫 push 는 훅이 돌아 막히고,
        그 훅이 자기를 무장해제하므로 두 번째 push 는 그냥 통과한다 —
        「번갈아 막힌다」는 증상이 그것이고, 장부는 그것을 **다른 세션의 소행**으로
        오래 기록하고 있었다(정정됨). ⚠️ 그 상태에서는 아무 검사도 돌지 않으므로
        **이 사실을 말해 주는 것이 없다.**
        """
        env = _git_env_without_repo_location()
        subprocess.run(['git', 'init', '-q', str(tmp)], check=True, env=env)
        if value is not None:
            subprocess.run(
                ['git', 'config', 'core.hooksPath', value],
                cwd=str(tmp), check=True, env=env,
            )
        got = subprocess.run(
            ['git', 'config', 'core.hooksPath'], cwd=str(tmp),
            capture_output=True, text=True, check=False, env=env,
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


class TestTheProbeDoesNotDisarmTheRepositoryItChecks(unittest.TestCase):
    """⚠️ 이 클래스가 없으면 위 수리는 되돌려져도 아무것도 red 가 되지 않는다.

    검사가 **자기가 지키려는 것을 끄는** 형태였고, 그 사실을 말해 줄 수 있는 것은
    구조적으로 없었다 — 꺼진 뒤에는 아무 검사도 돌지 않기 때문이다. 그러므로 여기서는
    훅 문맥을 **실제로 만들어** 묻는다: `GIT_DIR` 을 세운 채 프로브를 돌리고, 지목된
    저장소의 설정이 그대로인가.
    """

    # ⚠️ 이 클래스의 모든 git 호출이 환경을 격리한다 — 봉인 자신이 같은 결함을 갖지
    #    않기 위해서다. 실측 2026-08-31: 처음 판은 `_victim` 만 격리를 빠뜨렸고, 훅
    #    안에서는 부모의 `GIT_DIR` 이 **이미** 서 있으므로 피해자를 만들려던 `git init`
    #    이 진짜 저장소의 `core.bare` 를 뒤집었다. ⚠️ **격리 단위 실행으로는 잡히지
    #    않는다** — 그 문맥엔 주변 `GIT_DIR` 이 없어 같은 코드가 정상 동작한다.
    #    훅을 통한 실제 push 가 잡았다.
    def _git(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ['git', *args],
            cwd=None if cwd is None else str(cwd),
            capture_output=True, text=True, check=False,
            env=_git_env_without_repo_location(),
        )

    def _victim(self, root: Path) -> Path:
        """링크드 워크트리를 가진 저장소 — 피해가 실측된 바로 그 형상."""
        repo = root / 'victim'
        self.assertEqual(0, self._git('init', '-q', str(repo)).returncode)
        hooks = repo / TRACKED_HOOKS_DIR
        hooks.mkdir()
        (hooks / 'pre-push').write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        for args in (
            ('config', 'core.hooksPath', TRACKED_HOOKS_DIR),
            ('config', 'user.email', 'probe@example.invalid'),
            ('config', 'user.name', 'probe'),
            ('add', '-A'),
            ('commit', '-qm', 'init'),
            ('worktree', 'add', '-q', str(root / 'wt'), '-b', 'wt'),
        ):
            result = self._git(*args, cwd=repo)
            self.assertEqual(0, result.returncode, f'{args}: {result.stderr}')
        return repo

    def _config(self, repo: Path, key: str) -> str:
        return self._git('config', key, cwd=repo).stdout.strip()

    def test_running_under_a_hooks_git_dir_leaves_the_repository_alone(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = self._victim(root)
            before = (
                self._config(repo, 'core.hooksPath'),
                self._config(repo, 'core.bare'),
            )
            self.assertEqual((TRACKED_HOOKS_DIR, 'false'), before)

            # git 이 훅에 물려주는 그대로. 워크트리 형상이 `core.bare` 까지 뒤집던 자리다.
            # ⚠️ 원래 값을 **복원**한다 — 훅 안에서는 부모가 이미 하나 세워 뒀고,
            #    지워 버리면 이 검사가 뒤따르는 검사들의 문맥을 바꾼다.
            inherited = os.environ.get('GIT_DIR')
            os.environ['GIT_DIR'] = str(repo / '.git' / 'worktrees' / 'wt')
            try:
                probe = TestTheCheckWouldSeeAnUnwiredCheckout(
                    'test_an_unset_checkout_reads_as_empty',
                )
                probe._probe(root / 'elsewhere', 'some/other/hooks')
            finally:
                if inherited is None:
                    os.environ.pop('GIT_DIR', None)
                else:
                    os.environ['GIT_DIR'] = inherited

            after = (
                self._config(repo, 'core.hooksPath'),
                self._config(repo, 'core.bare'),
            )
            self.assertEqual(
                before, after,
                '프로브가 자기가 검사하는 저장소의 설정을 바꿨다 — '
                'push 한 번이 pre-push 를 영구히 끈다',
            )

    def test_the_isolation_is_a_no_op_outside_a_hook(self) -> None:
        """⚠️ 격리가 훅 밖에서 환경을 바꾸면 그것은 다른 결함이다."""
        for name in GIT_REPO_LOCATION_ENV:
            os.environ.pop(name, None)
        self.assertEqual(dict(os.environ), _git_env_without_repo_location())

    def test_the_stripped_set_leaves_identity_alone(self) -> None:
        """지우는 것은 *어느 저장소인가* 뿐이다 — git 을 다르게 만들지 않는다."""
        for name in ('GIT_AUTHOR_NAME', 'GIT_CONFIG_GLOBAL', 'GIT_TERMINAL_PROMPT'):
            self.assertNotIn(name, GIT_REPO_LOCATION_ENV)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
