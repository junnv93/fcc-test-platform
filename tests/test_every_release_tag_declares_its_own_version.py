"""발행된 태그의 트리가 **그 태그의 판번호를 스스로 말하는가** (2026-09-06).

소비 레인은 이 상자를 `@vX.Y.Z` 태그로 핀한다. 그러면 설치된 배포판이 스스로를 무엇이라
말하는지는 **그 태그 시점 트리의 `pyproject.toml:version`** 이 정한다. 둘이 어긋나면
소비 레인 쪽에서 「선언 vX · 설치 Y」로 빨개지고, **원인은 여기 있는데 빨강은 저기서
난다.**

⚠️ **실측 2026-09-06 — 실제로 일어났다.** `v0.1.10`(07:49, 커밋 `9aed9cc`)이 **판올림
커밋 없이** 붙어 그 트리의 `version` 이 여전히 `0.1.9` 였다:

    v0.1.8  → 0.1.8   ✓
    v0.1.9  → 0.1.9   ✓
    v0.1.10 → 0.1.9   ✗

형제 세션이 태그를 냈고, **다른 세션이 격리 venv 에 실제로 설치해서야** 잡혔다. 이 상자
안에서는 아무것도 그것을 보지 않았다 — 태그를 붙이는 일이 사람 손이고, 붙인 뒤 확인하는
장치가 없었기 때문이다.

★ **태그는 옮기지 않는다.** `v0.1.10` 은 그대로 두고 다음 판이 그 자리를 대신한다
(형제 레인이 `0.1.19` 를 같은 방식으로 처리한 선례). 그래서 아래 `_KNOWN_BROKEN` 에
**이유와 함께** 적는다 — 지우는 것이 아니라 기록하는 것이 이 저장소의 방식이다.

## ⚠️ 이 검사가 조용히 공허해지는 자리

CI 의 `actions/checkout` 은 기본이 **얕은 클론이라 태그를 받아 오지 않는다.** 그러면 이
검사는 「태그 0개」를 보고 **아무것도 요구하지 않으면서 통과**한다 — 검사가 있다는 사실이
지켜진다는 뜻이 되지 않는다. 그래서 태그가 안 보이면 **건너뛰지 않고 멈춘다**
(`test_tags_are_visible`). 워크플로에 `fetch-depth: 0` 이 함께 들어가는 이유다.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import unittest

_ROOT = Path(__file__).resolve().parent.parent

#: 판번호 태그의 모양. 다른 계열(예: 형제 레인의 `kernel-v…`)은 이 상자의 태그가 아니다.
_RELEASE_TAG = re.compile(r'^v(?P<version>\d+\.\d+\.\d+)$')

_VERSION_LINE = re.compile(r'^version\s*=\s*"(?P<version>[^"]+)"', re.M)

#: 이미 붙었고 **옮기지 않기로 한** 태그. 이름과 이유를 함께 적는다.
#:
#: ⚠️ 이 표는 «봐주기»가 아니라 **기록**이다. 지우면 다음 사람이 그 태그를 정상으로 보고
#: 핀했다가 소비 레인에서 빨강을 만난다. 새 항목을 더할 때는 **왜 고치지 않는지**를 함께
#: 적어라 — 태그는 옮기지 않는다는 규칙 때문이라면 그 대체 판번호도 적어라.
_KNOWN_BROKEN: dict[str, str] = {
    'v0.1.10': (
        '2026-09-06 07:49, 커밋 9aed9cc — 판올림 커밋 없이 태그만 붙어 트리가 0.1.9 를 '
        '말한다. 태그는 옮기지 않으므로 이 번호는 건너뛰고 다음 판이 대신한다(PR #90). '
        '소비 레인은 이 번호를 pin 하지 마라.'
    ),
}


def _git(*args: str) -> str:
    return subprocess.run(
        ['git', *args], cwd=_ROOT, capture_output=True, text=True, check=True,
    ).stdout


def _release_tags() -> list[str]:
    out = _git('tag', '--list', 'v*')
    return [t for t in out.split() if _RELEASE_TAG.match(t)]


def _declared_version_at(tag: str) -> str | None:
    try:
        blob = _git('show', f'{tag}:pyproject.toml')
    except subprocess.CalledProcessError:
        return None
    match = _VERSION_LINE.search(blob)
    return match.group('version') if match else None


class TestTagsAreVisible(unittest.TestCase):
    """⚠️ 태그가 안 보이면 아래 단언은 아무것도 요구하지 않으면서 통과한다."""

    def test_the_working_tree_is_a_git_repository(self) -> None:
        try:
            _git('rev-parse', '--git-dir')
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            self.fail(f'git 저장소가 아니거나 git 을 쓸 수 없다: {exc}')

    def test_release_tags_are_present(self) -> None:
        tags = _release_tags()
        self.assertTrue(
            tags,
            '`v*` 판번호 태그가 하나도 보이지 않는다 — 거의 항상 **얕은 클론**이다. '
            'CI 의 `actions/checkout` 은 기본으로 태그를 받아 오지 않는다. 이 상태로 두면 '
            '이 검사는 「태그 0개」를 대조하며 영원히 초록이다.\n'
            '고치기: 워크플로의 checkout 에 `with: {fetch-depth: 0}` 을 더하라.',
        )


class TestEveryTagDeclaresItsOwnVersion(unittest.TestCase):
    """이 파일이 존재하는 이유."""

    def test_the_tagged_tree_says_the_tagged_number(self) -> None:
        mismatched: list[str] = []
        unreadable: list[str] = []
        for tag in sorted(_release_tags()):
            if tag in _KNOWN_BROKEN:
                continue
            expected = _RELEASE_TAG.match(tag).group('version')  # type: ignore[union-attr]
            declared = _declared_version_at(tag)
            if declared is None:
                unreadable.append(f'{tag}: 그 시점 pyproject.toml 을 읽지 못했다')
            elif declared != expected:
                mismatched.append(f'{tag}: 트리가 {declared} 를 말한다')
        self.assertEqual(
            [], mismatched,
            '태그의 트리가 그 태그의 판번호를 말하지 않는다 — 소비 레인이 이 번호로 pin 하면 '
            '설치된 배포판이 다른 번호를 말하고, 빨강은 **저쪽에서** 난다:\n  '
            + '\n  '.join(mismatched)
            + '\n태그는 옮기지 마라. 다음 판번호로 대체하고 이 파일의 _KNOWN_BROKEN 에 '
              '이유와 함께 적어라.',
        )
        self.assertEqual([], unreadable, '\n  '.join(unreadable))


class TestTheExceptionTableStaysHonest(unittest.TestCase):
    """⚠️ 기록은 낡는다 — 표가 실재하지 않는 태그를 가리키면 그것도 결함이다."""

    def test_every_recorded_exception_names_a_real_tag(self) -> None:
        tags = set(_release_tags())
        ghosts = sorted(t for t in _KNOWN_BROKEN if t not in tags)
        self.assertEqual(
            [], ghosts,
            f'_KNOWN_BROKEN 이 실재하지 않는 태그를 가리킨다: {ghosts} — 표가 낡았다',
        )

    def test_every_recorded_exception_is_actually_broken(self) -> None:
        """봐주기가 «필요 없어진» 항목은 지워야 한다.

        고쳐진 뒤에도 남아 있으면 그 태그는 앞으로 **검사되지 않는다** — 표가 조용히
        구멍이 된다.
        """
        still_needed: list[str] = []
        for tag in sorted(_KNOWN_BROKEN):
            if tag not in set(_release_tags()):
                continue  # 위 검사가 잡는다
            expected = _RELEASE_TAG.match(tag).group('version')  # type: ignore[union-attr]
            if _declared_version_at(tag) == expected:
                still_needed.append(tag)
        self.assertEqual(
            [], still_needed,
            f'_KNOWN_BROKEN 에 있는데 실제로는 어긋나지 않는 태그가 있다: {still_needed} — '
            '지워라. 남겨 두면 그 태그가 앞으로 검사되지 않는다',
        )

    def test_every_recorded_exception_carries_a_reason(self) -> None:
        thin = sorted(t for t, why in _KNOWN_BROKEN.items() if len(why.strip()) < 40)
        self.assertEqual(
            [], thin,
            f'이유가 없거나 너무 짧은 예외 항목: {thin} — «왜 고치지 않는지»를 적어라',
        )


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
