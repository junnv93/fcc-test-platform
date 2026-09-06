"""핀이 여러 곳에 적히는데 서로 같은가, 그리고 실제 설치와 같은가 (2026-09-06).

이 상자는 공유 레인을 **소비**한다. 그 핀이 **두 파일**에 적힌다:

    pyproject.toml            이 패키지가 실제로 의존하는 것 (설치가 이것을 푼다)
    requirements-central.txt  중앙 PC 배포 목록

⚠️ **실측 2026-09-06: 두 파일이 서로 같은지 보는 검사가 0개였다.** 그날 값은 우연히
같았지만, 「오늘 같다」와 「어긋나면 멈춘다」는 다른 명제다. 한 파일만 고치고 끝내면
`pip install .` 로 세운 환경과 배포 목록으로 세운 환경이 **서로 다른 코드**를 싣고,
그 차이는 두 환경에서 같은 시험이 다른 답을 낼 때에야 드러난다.

⚠️ **그리고 「선언 ↔ 실제 설치」가 이 세션에서 두 번 사람을 속였다.** 트리는
`v0.1.22` 를 선언하는데 작업용 환경에는 `0.1.21` 이 깔려 있었고, 그 상태로 전량을 돌리자
무관한 검사 둘이 「새로 깨진 것」으로 보였다. 실제로 깨진 것은 없었다 — 낡은 설치본이
그렇게 말했을 뿐이다. 형제 레인이 같은 결함을 2026-08-31 에 이미 겪었고
(`선언 v0.1.4 · 설치 0.1.3`), 그때 만든 검사가 이 파일의 본보기다
(모노레포 `tests/test_lane_pin_matches_installed.py`).

⚠️ **틀리는 방향이 나쁘다.** 낡은 설치본에서 재면 「저쪽 변경이 반영됐다」와
「반영되지 않았다」가 **같은 초록**이 된다.

## 왜 「두 집합이 같다」가 아닌가

`fcc-test-platform` 은 배포 목록에만 있고 `pyproject.toml` 에는 없다 — 패키지는 자기
자신에 의존하지 않으므로 **그 비대칭이 옳다.** 그래서 명제는 「집합이 같다」가 아니라
**「pyproject 가 선언한 것은 전부 배포 목록에도 같은 태그로 있어야 한다」** 이다.
배포 목록의 추가 항목은 허용하되, **빠지는 것은 허용하지 않는다.**
"""
from __future__ import annotations

from importlib import metadata
from pathlib import Path
import re
import unittest

_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = _ROOT / 'pyproject.toml'
REQUIREMENTS_CENTRAL = _ROOT / 'requirements-central.txt'

#: `<배포판> @ git+<url>@<태그>` — 태그는 두 계열이 있다:
#:   `v0.1.22`         계약 패키지
#:   `kernel-v0.5.0`   커널 패키지 (같은 저장소의 하위 디렉터리)
#: 그래서 이름 접두는 선택이고, `#subdirectory=…` 조각이 뒤에 붙을 수 있다.
#: ⚠️ 세 자리 semver 를 요구한다 — 브랜치나 두 자리는 매치되지 않아야
#: `test_every_git_dependency_is_a_pinned_tag` 가 그것을 잡는다.
_PIN = re.compile(
    r'(?P<dist>[A-Za-z0-9._-]+)\s*@\s*git\+\S+?@'
    r'(?:[A-Za-z][A-Za-z0-9._-]*-)?v(?P<tag>\d+\.\d+\.\d+)'
    r'(?:#\S*)?$'
)

FIX_HINT = (
    '두 파일의 태그를 같게 맞추고, 작업 환경도 그 태그로 다시 설치하라:\n'
    '    python3 -m pip install --no-deps --force-reinstall '
    '"<배포판> @ git+https://github.com/junnv93/fcc-test-contracts@<태그>"'
)


def _pins_in(path: Path) -> dict[str, str]:
    """그 파일이 선언한 `배포판 → 태그의 semver`.

    ⚠️ **레인 이름을 손으로 나열하지 않는다.** 나열하면 레인이 하나 늘 때 이 검사가
    조용히 그것을 안 본다 — 「둘을 확인했다」와 「선언된 것을 전부 확인했다」는 다른
    명제다. 본보기(모노레포 `test_lane_pin_matches_installed.py`)의 성질을 그대로 쓴다.
    """
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.split('#', 1)[0] if not _looks_like_url_fragment(raw) else raw
        line = line.strip().strip(',').strip('"').strip("'").strip()
        match = _PIN.search(line)
        if match:
            pins[match.group('dist')] = match.group('tag')
    return pins


def _looks_like_url_fragment(raw: str) -> bool:
    """`#subdirectory=…` 은 주석이 아니라 URL 의 일부다.

    ⚠️ 이 구분이 없으면 커널 핀이 `#` 에서 잘려 정규식에 걸리지 않고, 검사는
    **커널을 못 본 채로 초록**이 된다 — 조용히 부분집합만 보는 형태다.
    """
    return '@ git+' in raw and '#subdirectory=' in raw


class TestThereIsSomethingToCheck(unittest.TestCase):
    """⚠️ 파싱이 깨져 0개가 되면 아래 단언들은 아무것도 요구하지 않으면서 통과한다.

    「검사가 안 돌았다」와 「위반이 없다」가 같은 초록이 되는 자리를 먼저 막는다.
    """

    def test_pyproject_declares_git_pins(self) -> None:
        pins = _pins_in(PYPROJECT)
        self.assertTrue(
            pins,
            f'{PYPROJECT.name} 에서 git 핀을 하나도 파싱하지 못했다 — 정규식이 선언 '
            '형식과 어긋났거나 선언이 사라졌다. 이 검사가 공허하다',
        )

    def test_the_deployment_list_declares_git_pins(self) -> None:
        pins = _pins_in(REQUIREMENTS_CENTRAL)
        self.assertTrue(
            pins,
            f'{REQUIREMENTS_CENTRAL.name} 에서 git 핀을 하나도 파싱하지 못했다 — '
            '이 검사가 공허하다',
        )

    def test_both_tag_series_are_seen(self) -> None:
        """⚠️ 두 계열(`v…` / `kernel-v…`)이 **둘 다** 파싱돼야 한다.

        커널 핀은 `#subdirectory=` 조각을 달고 있어서, 그 조각을 주석으로 잘라 버리는
        파서는 **커널만 조용히 빠뜨린 채** 초록이 된다. 계열 이름을 여기 적지 않고
        「배포판이 둘 이상 보인다」로 표현한다 — 이름을 적으면 이 검사가 그 이름에 묶인다.
        """
        self.assertGreaterEqual(
            len(_pins_in(PYPROJECT)), 2,
            f'{PYPROJECT.name} 에서 배포판이 하나만 보인다 — `#subdirectory=` 를 단 핀이 '
            '파서에서 잘려 나갔을 수 있다',
        )


class TestTheTwoDeclarationsAgree(unittest.TestCase):
    """이 파일이 존재하는 이유 — 두 파일이 같은 것을 말하는가."""

    def test_every_dependency_pin_is_carried_by_the_deployment_list(self) -> None:
        """`pyproject.toml` 이 선언한 것은 전부 배포 목록에도 **같은 태그로** 있어야 한다.

        ⚠️ 방향이 있다. 배포 목록의 **추가** 항목은 허용한다 — `fcc-test-platform` 은
        이 상자 자신이라 `pyproject.toml` 에 없는 것이 옳다. 그러나 **빠지는 것**은
        허용하지 않는다: 빠지면 중앙 PC 가 그 레인 없이 서거나 다른 판으로 선다.
        """
        declared = _pins_in(PYPROJECT)
        deployed = _pins_in(REQUIREMENTS_CENTRAL)
        offenders: list[str] = []
        for dist, tag in sorted(declared.items()):
            if dist not in deployed:
                offenders.append(
                    f'{dist}: {PYPROJECT.name} 은 v{tag} 를 선언하는데 '
                    f'{REQUIREMENTS_CENTRAL.name} 에 **없다**'
                )
            elif deployed[dist] != tag:
                offenders.append(
                    f'{dist}: {PYPROJECT.name} v{tag} · '
                    f'{REQUIREMENTS_CENTRAL.name} v{deployed[dist]}'
                )
        self.assertEqual(
            [], offenders,
            '핀이 두 파일에서 서로 다른 것을 말한다 — `pip install .` 로 세운 환경과 '
            '배포 목록으로 세운 중앙 PC 가 서로 다른 코드를 싣게 되고, 그 차이는 두 '
            '환경에서 같은 시험이 다른 답을 낼 때에야 드러난다:\n  '
            + '\n  '.join(offenders) + f'\n{FIX_HINT}',
        )


class TestEveryGitDependencyIsAPinnedTag(unittest.TestCase):
    """⚠️ 움직이는 ref 는 사후에 「어느 코드가 돌았나」를 답할 수 없게 만든다.

    `requirements-central.txt` 의 주석이 *"Pinned to a TAG, never a branch"* 를 산문으로
    적고 있으나, **산문은 다음 사람이 브랜치를 적는 것을 막지 못한다.**
    """

    def test_no_moving_ref_is_declared(self) -> None:
        offenders: list[str] = []
        for path in (PYPROJECT, REQUIREMENTS_CENTRAL):
            for raw in path.read_text(encoding='utf-8').splitlines():
                line = raw.split('#', 1)[0] if not _looks_like_url_fragment(raw) else raw
                line = line.strip().strip(',').strip('"').strip("'").strip()
                if '@ git+' not in line:
                    continue
                if not _PIN.search(line):
                    offenders.append(f'{path.name}: {line}')
        self.assertEqual(
            [], offenders,
            'git 의존이 세 자리 태그로 고정되지 않았다 — 움직이는 ref 는 재현 불가능한 '
            f'실행을 만든다:\n  ' + '\n  '.join(offenders),
        )


class TestTheDeclaredPinEqualsWhatIsInstalled(unittest.TestCase):
    """선언한 판과 **이 환경에 실제로 깔린 판**이 같은가.

    ⚠️ 이 축이 이 세션에서 두 번 사람을 속였다. 트리는 `v0.1.22` 를 선언하는데 작업용
    환경에는 `0.1.21` 이 깔려 있었고, 그 상태의 전량 실행에서 무관한 검사 둘이 「새로
    깨진 것」으로 보였다. **실제로 깨진 것은 없었다.** 그 두 검사는 트리의 파일이 아니라
    **설치된 패키지의 발행본**을 읽기 때문이다.

    빨강의 값이 여기서는 정직하다 — 「무엇이 어긋났는지」를 이름과 두 숫자로 말한다.
    """

    def test_the_installed_version_equals_the_declared_tag(self) -> None:
        drifted: list[str] = []
        for dist, tag in sorted(_pins_in(PYPROJECT).items()):
            try:
                installed = metadata.version(dist)
            except metadata.PackageNotFoundError:
                drifted.append(f'{dist}: 선언 v{tag} · **설치되지 않음**')
                continue
            if installed != tag:
                drifted.append(f'{dist}: 선언 v{tag} · 설치 {installed}')
        self.assertEqual(
            [], drifted,
            '선언한 핀과 이 환경에 설치된 배포판이 어긋난다 — 이 상태로 잰 결과는 '
            '트리가 아니라 낡은 설치본을 잰 것이고, 무관한 검사가 「새로 깨진 것」으로 '
            f'보인다:\n  ' + '\n  '.join(drifted) + f'\n{FIX_HINT}',
        )


class TestTheCheckWouldSeeADrift(unittest.TestCase):
    """⚠️ 비-공허성 — 위 검사들이 *늘* 통과하는 것은 아닌지 합성 입력으로 확인한다.

    실제 파일이 오늘 우연히 일치한다는 사실은 「어긋나면 잡는다」를 증명하지 않는다.
    """

    def test_a_disagreeing_pair_is_reported(self) -> None:
        declared = {'fcc-test-contracts': '0.1.22'}
        deployed = {'fcc-test-contracts': '0.1.21'}
        offenders = [d for d, t in declared.items() if deployed.get(d) != t]
        self.assertTrue(offenders, '어긋난 쌍이 보고되지 않으면 이 축은 아무것도 재지 않는다')

    def test_a_missing_entry_is_reported(self) -> None:
        declared = {'fcc-test-kernel': '0.5.0'}
        deployed: dict[str, str] = {}
        offenders = [d for d in declared if d not in deployed]
        self.assertTrue(offenders, '빠진 항목이 보고되지 않으면 이 축은 아무것도 재지 않는다')

    def test_a_moving_ref_does_not_parse_as_a_pin(self) -> None:
        self.assertIsNone(_PIN.search('fcc-test-contracts @ git+https://x/y@main'))
        self.assertIsNone(_PIN.search('fcc-test-contracts @ git+https://x/y@v1.2'))
        self.assertIsNotNone(_PIN.search('fcc-test-contracts @ git+https://x/y@v1.2.3'))

    def test_a_prefixed_tag_with_a_subdirectory_fragment_parses(self) -> None:
        """커널 핀의 실제 형태 — 이것이 안 잡히면 검사가 커널을 조용히 빠뜨린다."""
        match = _PIN.search(
            'fcc-test-kernel @ git+https://x/y@kernel-v0.5.0'
            '#subdirectory=packages/fcc-test-kernel'
        )
        self.assertIsNotNone(match)
        assert match is not None  # 타입 검사기용 좁히기
        self.assertEqual('fcc-test-kernel', match.group('dist'))
        self.assertEqual('0.5.0', match.group('tag'))


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
