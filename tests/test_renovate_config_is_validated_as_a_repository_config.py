"""판올림 봇 설정이 **봇 자신의 검증기로** 읽히는가 (2026-09-06).

## Why

`tests/test_renovate_covers_every_lane_pin.py` 는 「이 트리의 핀이 customManager
정규식에 잡히는가」를 본다. 그 검사는 Renovate 의 정규식 의미를 **Python `re` 로
재구현**해서 보므로, 설정이 renovate 자신에게 **유효한가**는 묻지 못한다.

⚠️ 그 축이 왜 필요한가: Renovate 는 모르는 키가 하나라도 있으면 **설정 전체를
거부한다.** 그러면 봇은 붙어 있는데 아무 PR 도 만들지 않고, 그것은 「갱신할 것이
없다」와 화면에서 구별되지 않는다.

## What — 그리고 이 검사가 실제로 잡은 함정

⚠️ **검증기를 부르는 방법이 두 가지이고, 하나는 틀린 축이다.** 2026-09-06 실측
(renovate 42.99.0):

    $ renovate-config-validator renovate.json
     INFO: Validating renovate.json as global config     ← 전역 설정 축
    $ renovate-config-validator
     INFO: Validating renovate.json                      ← 저장소 설정 축

전역 설정과 저장소 설정은 **허용 키가 다르다.** 파일명을 인자로 주면 우리가 실제로
쓰는 축이 아닌 쪽으로 검증되고, 그 결과는 초록이어도 우리 질문에 답하지 않는다.
(오늘의 `renovate.json` 은 두 축 모두에서 통과한다 — 즉 **오늘은 증상이 없다.**
그래서 더더욱 봉인이 필요하다: 증상 없는 틀린 축은 스스로 드러나지 않는다.)

## 한계

이 검사는 **워크플로가 무엇을 부르는지**를 본다. 그 명령이 실제로 통과하는지는
CI 에서만 관측된다 — 여기서 `npx` 를 부르면 네트워크에 의존하는 검사가 되어
레인 게이트 전체가 바깥 사정에 흔들린다. 두 자리의 분담이 그래서 이렇다:

    이 파일   게이트가 **올바른 축으로 배선돼 있는가**   (오프라인·결정적)
    CI 잡     그 축으로 **실제로 통과하는가**             (네트워크·권위)
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest

import yaml

_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = _ROOT / '.github' / 'workflows'
RENOVATE_CONFIG = _ROOT / 'renovate.json'

#: 검증기를 부르는 줄. 이름이 나오면 그 줄이 이 게이트의 진입점이다.
_VALIDATOR = re.compile(r'renovate-config-validator\b')

#: `npx --package renovate@<판>` 의 판 부분.
_PINNED_PACKAGE = re.compile(r'--package[= ]\s*renovate@(?P<version>[0-9]+\.[0-9]+\.[0-9]+)\b')


def _run_lines() -> list[tuple[Path, str]]:
    """모든 워크플로의 `run:` 본문을 줄 단위로 편다.

    ⚠️ 대상을 손으로 나열하지 않는다 — 워크플로 **선언에서 파생**하므로 잡이
    다른 파일로 옮겨 가도 따라간다.
    """
    out: list[tuple[Path, str]] = []
    for path in sorted(WORKFLOW_DIR.glob('*.y*ml')):
        doc = yaml.safe_load(path.read_text(encoding='utf-8'))
        for job in (doc.get('jobs') or {}).values():
            for step in job.get('steps') or []:
                body = step.get('run')
                if not body:
                    continue
                for line in body.splitlines():
                    out.append((path, line.strip()))
    return out


def _validator_lines() -> list[tuple[Path, str]]:
    return [(p, line) for p, line in _run_lines() if _VALIDATOR.search(line)]


def _workflow_of(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def _triggers(doc: dict) -> dict:
    on = doc.get('on', doc.get(True))
    return on if isinstance(on, dict) else {}


class TestTheGateExists(unittest.TestCase):
    def test_the_config_this_gate_is_about_is_present(self) -> None:
        """대상이 없으면 아래 검사들이 전부 공허하다."""
        self.assertTrue(
            RENOVATE_CONFIG.is_file(),
            'renovate.json 이 없다. 설정이 사라졌는데 검증 잡만 남으면 그 잡은 '
            '아무것도 검증하지 않으면서 초록이 된다.',
        )

    def test_some_workflow_runs_the_real_validator(self) -> None:
        self.assertTrue(
            _validator_lines(),
            'renovate-config-validator 를 부르는 워크플로가 없다. 그러면 이 저장소의 '
            '봇 설정을 보는 눈은 파이썬 재구현 하나뿐이고, 그것은 스키마 오류를 못 본다.',
        )


class TestTheValidatorIsCalledOnTheRepositoryAxis(unittest.TestCase):
    """⚠️ 이 검사의 핵심. 파일명을 인자로 주면 **전역 설정 축**으로 검증된다."""

    def test_no_invocation_passes_a_config_filename(self) -> None:
        for path, line in _validator_lines():
            tail = line.split('renovate-config-validator', 1)[1].strip()
            offenders = [
                token for token in tail.split()
                if not token.startswith('-') and token not in {'&&', '||', ';', '|'}
            ]
            self.assertEqual(
                offenders, [],
                f'{path.name}: `renovate-config-validator` 에 인자 {offenders} 를 준다. '
                '파일명을 주면 검증기가 그 파일을 **전역 설정**으로 읽는다 — 저장소 '
                '설정과 허용 키가 다른 틀린 축이다(2026-09-06 실측: '
                "`INFO: Validating renovate.json as global config`). 인자 없이 불러라.",
            )


class TestTheGateFiresWhenTheConfigChanges(unittest.TestCase):
    """배선돼 있어도 **켜지지 않으면** 없는 것과 같다."""

    def test_the_workflow_is_triggered_by_changes_to_the_config(self) -> None:
        for path, _line in _validator_lines():
            triggers = _triggers(_workflow_of(path))
            self.assertTrue(triggers, f'{path.name}: 트리거를 읽지 못했다')
            watched = {
                pattern
                for event in ('push', 'pull_request')
                for pattern in ((triggers.get(event) or {}).get('paths') or [])
            }
            if not watched:
                continue  # paths 필터가 없으면 모든 변경에서 돈다 — 이 축에서 안전하다
            self.assertIn(
                'renovate.json', watched,
                f'{path.name}: paths 필터가 있는데 renovate.json 이 그 안에 없다. '
                '설정을 고쳐도 이 게이트가 돌지 않는다.',
            )


class TestTheValidatorVersionIsPinned(unittest.TestCase):
    """판을 흘리면 우리 커밋과 무관한 빨강이 생기고, 사람은 그런 게이트를 끈다."""

    def test_no_invocation_floats_the_renovate_version(self) -> None:
        for path, line in _validator_lines():
            if 'renovate' not in line:
                continue
            self.assertNotIn(
                'renovate@latest', line,
                f'{path.name}: renovate 판이 흐른다. 스키마가 바뀌는 날 이 게이트는 '
                '우리 커밋과 무관하게 빨개진다.',
            )
            self.assertIsNotNone(
                _PINNED_PACKAGE.search(line),
                f'{path.name}: renovate 판이 고정돼 있지 않다 ({line!r}). '
                '`--package renovate@<major.minor.patch>` 로 못박아라.',
            )


class TestTheCheckWouldSeeTheGap(unittest.TestCase):
    """봉인이 오늘 초록인 것과 이빨이 있는 것은 다른 명제다."""

    def _offenders(self, line: str) -> list[str]:
        tail = line.split('renovate-config-validator', 1)[1].strip()
        return [t for t in tail.split() if not t.startswith('-') and t not in {'&&', '||', ';', '|'}]

    def test_the_wrong_axis_form_is_recognised_as_an_offender(self) -> None:
        wrong = 'npx --yes --package renovate@42.99.0 renovate-config-validator renovate.json'
        self.assertEqual(self._offenders(wrong), ['renovate.json'])

    def test_the_shipped_form_has_no_offender(self) -> None:
        right = 'npx --yes --package renovate@42.99.0 renovate-config-validator'
        self.assertEqual(self._offenders(right), [])

    def test_a_floating_version_is_recognised(self) -> None:
        self.assertIsNone(
            _PINNED_PACKAGE.search('npx --yes --package renovate@latest renovate-config-validator')
        )


if __name__ == '__main__':
    unittest.main()
