"""판올림 봇 설정이 **이 저장소의 핀을 실제로 보는가** (2026-09-06).

`renovate.json` 이 있다는 것과 그 설정이 이 저장소의 핀을 집는다는 것은 **다른 명제**다.
정규식이 아무것도 매치하지 않아도 봇은 **조용히 초록으로 돈다** — 갱신 PR 이 0건인 것과
갱신할 것이 0건인 것이 같은 모습이 된다. 이 저장소가 이미 여러 번 이름 붙인 형태다.

⚠️ **왜 이 설정이 필요한가 — 실측**(renovate 42.99.0, `--platform=local --dry-run=lookup`):
기본 관리자는 이 트리에서 의존 **54개**를 추출하면서 **우리가 필요한 둘만** 건너뛴다.

    Dependency fcc-test-contracts has unsupported/unversioned value
      @ git+https://github.com/junnv93/fcc-test-contracts@v0.1.22 (versioning=pep440)
    Skipping fcc-test-contracts because no currentDigest or pinDigests

PEP 508 직접 URL 참조(`name @ git+…@tag`)는 pip/poetry 관리자의 versioning 축에 들어오지
않는다. 사용자 정의(정규식) 관리자를 두자 **`v0.1.22` → `v0.1.24` (patch)** 갱신이 실제로
만들어졌다 — 2026-09-06 에 실측된 바로 그 드리프트다.

## 이 검사가 재는 것

**「이 트리의 모든 git 핀이 적어도 하나의 customManager 정규식에 잡히는가.」**
핀이 새로 생기거나 형식이 바뀌면 봇이 그것을 못 보게 되는데, 그 사실은 봇 쪽에서
아무 신호도 내지 않는다. 그래서 이쪽에서 본다.

⚠️ **정직한 한계.** Renovate 의 정규식 엔진은 JavaScript 이고 이 검사는 Python `re` 로
평가한다. 이름 있는 그룹 문법만 옮기면(`(?<n>…)` → `(?P<n>…)`) 이 패턴 부류에서는 두
엔진의 판정이 같다 — 역참조·룩비하인드 같은 갈라지는 기능을 쓰지 않기 때문이다.
그 기능이 설정에 들어오는 날 이 검사는 **거짓 초록**이 될 수 있고, 아래
`test_no_pattern_uses_engine_specific_syntax` 가 그 날을 잡는다.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

_ROOT = Path(__file__).resolve().parent.parent
RENOVATE_CONFIG = _ROOT / 'renovate.json'
PINNED_FILES = (_ROOT / 'pyproject.toml', _ROOT / 'requirements-central.txt')

#: 이 트리에서 「git 핀」으로 치는 줄. 봇이 봐야 하는 대상의 정의다.
_GIT_PIN_LINE = re.compile(r'[A-Za-z0-9._-]+\s*@\s*git\+https://')

#: JS 의 이름 있는 그룹을 Python 문법으로. 두 엔진이 갈라지는 다른 기능은 아래에서 막는다.
_JS_NAMED_GROUP = re.compile(r'\(\?<([A-Za-z_][A-Za-z0-9_]*)>')

#: Python `re` 와 JS 정규식의 판정이 갈릴 수 있는 문법. 하나라도 쓰이면 이 검사의
#: 전제(두 엔진이 같은 답을 낸다)가 깨지므로 그 자리에서 멈춘다.
_ENGINE_SPECIFIC = (
    (r'(?<=', '룩비하인드'),
    (r'(?<!', '부정 룩비하인드'),
    (r'\\k<', '이름 역참조'),
    (r'\\p{', '유니코드 속성'),
)


def _custom_managers() -> list[dict]:
    return json.loads(RENOVATE_CONFIG.read_text(encoding='utf-8')).get('customManagers', [])


def _match_patterns() -> list[str]:
    """모든 customManager 의 matchStrings 를 Python `re` 문법으로 옮긴 것."""
    return [
        _JS_NAMED_GROUP.sub(r'(?P<\1>', pattern)
        for manager in _custom_managers()
        for pattern in manager.get('matchStrings', [])
    ]


def _git_pin_lines() -> list[tuple[str, str]]:
    """`(파일명, 줄)` — 이 트리가 선언한 git 핀 전부.

    ⚠️ 배포판 이름을 손으로 나열하지 않는다. 나열하면 핀이 하나 늘 때 이 검사가 조용히
    그것을 안 본다 — 「둘을 확인했다」와 「선언된 것을 전부 확인했다」는 다른 명제다.
    """
    lines: list[tuple[str, str]] = []
    for path in PINNED_FILES:
        for raw in path.read_text(encoding='utf-8').splitlines():
            stripped = raw.strip()
            if stripped.startswith('#'):
                continue
            if _GIT_PIN_LINE.search(stripped):
                lines.append((path.name, stripped))
    return lines


#: Renovate 가 아는 최상위 키 중 이 설정이 쓰는 것. **이 목록은 좁게 유지한다** —
#: 넓히려면 아래 검사가 말하는 대로 진짜 검증기를 돌려서 확인한 뒤에 더하라.
_ALLOWED_TOP_LEVEL = frozenset({
    '$schema', 'extends', 'description', 'customManagers', 'packageRules',
})


class TestTheConfigIsValidToRenovate(unittest.TestCase):
    """⚠️ **유효하지 않은 설정은 「설정이 없는 것」보다 나쁘다** — 저장소가 통째로 건너뛰어진다.

    실측 2026-09-06: 초판이 주석을 담으려고 `_doc` 이라는 임의 키를 최상위에 두었다.
    Renovate 는 그것을 **알 수 없는 옵션**으로 보고 설정 전체를 거부했고, 실행 결과가
    `result: "config-validation"` 으로 끝났다 — **핀은 하나도 조회되지 않았다.**
    그런데 이 파일의 다른 검사들은 **전부 초록이었다**: 정규식은 멀쩡히 핀을 집었기
    때문이다. 「정규식이 맞다」와 「봇이 이 설정을 받아들인다」는 다른 명제다.

    ⚠️ **정직한 한계.** 권위 있는 판정은 Renovate 자신의 검증기다:

        npx --yes --package renovate renovate-config-validator renovate.json

    그것은 renovate 패키지 전체를 요구하므로 이 저장소의 시험 의존에 두지 않았다.
    아래 검사는 **좁은 대리**다 — 알려진 키만 허용하므로 새 키를 더하면 (그것이
    정당하더라도) 빨개진다. **틀리는 방향이 안전하다**: 사람을 진짜 검증기로 보낸다.
    봇을 켜는 웨이브에서 위 명령을 CI 에 넣어라.
    """

    def test_no_unknown_top_level_key(self) -> None:
        config = json.loads(RENOVATE_CONFIG.read_text(encoding='utf-8'))
        unknown = sorted(set(config) - _ALLOWED_TOP_LEVEL)
        self.assertEqual(
            [], unknown,
            f'{RENOVATE_CONFIG.name} 의 최상위에 이 검사가 모르는 키가 있다: {unknown}\n'
            'Renovate 가 모르는 키 하나면 설정 **전체**가 거부되고 저장소가 통째로 '
            '건너뛰어진다(실측 2026-09-06, `_doc`). 진짜 검증기로 확인한 뒤 '
            '_ALLOWED_TOP_LEVEL 에 더하라:\n'
            '    npx --yes --package renovate renovate-config-validator renovate.json',
        )

    def test_comments_do_not_ride_on_an_invented_key(self) -> None:
        """설명은 `description` 에 담는다 — 그것이 Renovate 가 아는 자리다.

        `_` 로 시작하는 키는 «주석을 넣을 데가 없어서 만든 키»의 전형이고, 그것이
        2026-09-06 에 설정을 무효로 만든 형태다.
        """
        config = json.loads(RENOVATE_CONFIG.read_text(encoding='utf-8'))
        invented = sorted(k for k in config if k.startswith('_'))
        self.assertEqual(
            [], invented,
            f'임의로 만든 키가 있다: {invented} — 설명은 `description` 에 담아라. '
            'Renovate 는 모르는 키를 만나면 설정 전체를 거부한다',
        )


class TestThereIsSomethingToCheck(unittest.TestCase):
    """⚠️ 대상이나 규칙이 0개면 아래 단언은 아무것도 요구하지 않으면서 통과한다."""

    def test_the_config_exists_and_declares_custom_managers(self) -> None:
        self.assertTrue(RENOVATE_CONFIG.is_file(), f'{RENOVATE_CONFIG.name} 이 없다')
        self.assertTrue(
            _custom_managers(),
            f'{RENOVATE_CONFIG.name} 에 customManagers 가 없다 — 기본 관리자는 이 트리의 '
            'git 핀을 «unsupported/unversioned» 로 건너뛴다(실측). 이 검사가 공허하다',
        )

    def test_the_tree_actually_has_git_pins(self) -> None:
        self.assertTrue(
            _git_pin_lines(),
            '이 트리에서 git 핀을 하나도 찾지 못했다 — 대상 인식이 깨졌거나 핀이 '
            '사라졌다. 이 검사가 공허하다',
        )


class TestEveryGitPinIsSeenByTheBot(unittest.TestCase):
    """이 파일이 존재하는 이유."""

    def test_every_pin_matches_at_least_one_custom_manager(self) -> None:
        patterns = [re.compile(p) for p in _match_patterns()]
        unseen = [
            f'{name}: {line}'
            for name, line in _git_pin_lines()
            if not any(p.search(line) for p in patterns)
        ]
        self.assertEqual(
            [], unseen,
            '봇 설정이 보지 못하는 git 핀이 있다 — 그 핀은 새 판이 나와도 갱신 PR 이 '
            '오지 않고, 봇 쪽에서는 «갱신할 것이 없다»와 구별되지 않는다:\n  '
            + '\n  '.join(unseen)
            + f'\n{RENOVATE_CONFIG.name} 의 customManagers 에 규칙을 더하라.',
        )

    def test_every_custom_manager_matches_something(self) -> None:
        """반대 방향 — 아무것도 집지 않는 규칙은 낡은 규칙이다.

        핀의 이름이나 형식이 바뀌면 규칙은 남고 대상만 사라진다. 그 상태의 봇은
        조용히 아무 일도 하지 않는다.
        """
        lines = [line for _, line in _git_pin_lines()]
        idle = [
            pattern
            for pattern in _match_patterns()
            if not any(re.search(pattern, line) for line in lines)
        ]
        self.assertEqual(
            [], idle,
            '아무 핀에도 걸리지 않는 봇 규칙이 있다 — 규칙은 남았는데 대상이 바뀌었거나 '
            f'사라졌다:\n  ' + '\n  '.join(idle),
        )


class TestThePythonEvaluationIsFaithful(unittest.TestCase):
    """⚠️ 이 검사의 전제 — Python `re` 와 JS 정규식이 이 패턴들에 같은 답을 낸다."""

    def test_no_pattern_uses_engine_specific_syntax(self) -> None:
        offenders: list[str] = []
        for pattern in _match_patterns():
            for token, label in _ENGINE_SPECIFIC:
                if token in pattern:
                    offenders.append(f'{label}({token}): {pattern}')
        self.assertEqual(
            [], offenders,
            'Renovate 는 JavaScript 정규식으로, 이 검사는 Python `re` 로 평가한다. '
            '두 엔진의 판정이 갈릴 수 있는 문법이 쓰였으므로 이 검사는 더 이상 봇의 '
            f'실제 동작을 대변하지 않는다:\n  ' + '\n  '.join(offenders),
        )


class TestTheCheckWouldSeeAGap(unittest.TestCase):
    """⚠️ 비-공허성 — 합성 입력으로 이 축이 실제로 판정하는지 확인한다."""

    def test_an_unmatched_pin_is_reported(self) -> None:
        patterns = [re.compile(p) for p in _match_patterns()]
        rogue = 'fcc-test-newlane @ git+https://github.com/junnv93/x@v1.0.0'
        self.assertFalse(
            any(p.search(rogue) for p in patterns),
            '규칙에 없는 새 레인이 매치되면 이 축은 아무것도 재지 않는다',
        )

    def test_the_real_pins_do_match(self) -> None:
        patterns = [re.compile(p) for p in _match_patterns()]
        for sample in (
            'fcc-test-contracts @ git+https://github.com/junnv93/fcc-test-contracts@v0.1.22',
            'fcc-test-kernel @ git+https://github.com/junnv93/fcc-test-contracts'
            '@kernel-v0.5.0#subdirectory=packages/fcc-test-kernel',
        ):
            self.assertTrue(
                any(p.search(sample) for p in patterns),
                f'실제 핀 형태가 매치되지 않는다: {sample}',
            )

    def test_a_moving_ref_is_not_matched(self) -> None:
        """브랜치는 봇이 «올릴» 대상이 아니다 — 세 자리 태그만 집는다."""
        patterns = [re.compile(p) for p in _match_patterns()]
        self.assertFalse(
            any(p.search('fcc-test-contracts @ git+https://github.com/junnv93/x@main')
                for p in patterns))


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
