"""``check_import_name_ownership.py`` 의 봉인 (2026-09-03).

⚠️ **이 봉인은 이 머신의 상태에 기대지 않는다.** 그것이 요점이다 — 검사 자체가
*「지금 이 인터프리터에서 누가 이기는가」* 를 묻는 도구이므로, 그 도구를 시험하면서
같은 인터프리터의 우연한 상태를 오라클로 쓰면 **도구와 오라클이 같은 값을 공유**한다.
그러면 판정이 도는 것이 아니라 반사된다.

그래서 관측(`find_spec` · 배포판 루트)을 **주입**하고 두 축을 각각 구성한다:

  축 A(가려짐)  — 선언한 배포판 밖에서 해소되면 위반
  축 B(공유)    — 둘이 주장하고 하나가 __init__.py 를 실으면 **관측**(종료 코드 불변)

그리고 이 파일이 실제로 재현하는 것은 2026-09-03 실측 형상이다:
챔버 조건에서 ``domain``/``application`` 이 provider 저장소 ``src/`` 에서 해소되고,
중앙 조건에서 같은 이름이 설치된 휠에서 해소된다. **같은 저장소가 두 기계에서 다른
답을 낸다** — 그 차이를 보는 것이 이 검사가 존재하는 유일한 이유다.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO_ROOT / 'scripts'
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_import_name_ownership as guard  # noqa: E402


def _spec(location: str | None):
    """``find_spec`` 이 낼 법한 최소 객체. 패키지면 검색 경로를 갖는다."""
    if location is None:
        return None
    spec = importlib.util.spec_from_loader('x', loader=None)
    spec.submodule_search_locations = [location]
    return spec


def _fixed(mapping):
    def find_spec(name):
        return _spec(mapping.get(name))
    return find_spec


class TestTheShadowingAxis(unittest.TestCase):
    """축 A — 선언한 배포판 밖에서 해소되면 위반이다."""

    def test_resolution_inside_the_declaring_distribution_is_ok(self):
        v = guard.judge_name(
            'domain', ['fcc-test-platform'],
            {'fcc-test-platform': Path('/site-packages')},
            find_spec=_fixed({'domain': '/site-packages/domain'}),
        )
        self.assertEqual(v.state, 'ok')
        self.assertFalse(v.is_violation)

    def test_resolution_outside_it_is_a_violation(self):
        """2026-09-03 챔버 조건의 재현 — provider 트리가 휠을 가린다."""
        v = guard.judge_name(
            'domain', ['fcc-test-platform'],
            {'fcc-test-platform': Path('/site-packages')},
            find_spec=_fixed({'domain': '/repo/src/domain'}),
        )
        self.assertEqual(v.state, 'shadowed')
        self.assertTrue(v.is_violation)
        # 진단이 **어디서** 해소됐는지 이름으로 대야 한다 — 그것이 없으면
        # 운영자가 무엇을 치울지 알 수 없다.
        self.assertIn('/repo/src/domain', v.detail)

    def test_unresolvable_is_undetermined_not_ok(self):
        v = guard.judge_name(
            'ghost', ['some-dist'], {'some-dist': Path('/site-packages')},
            find_spec=_fixed({}),
        )
        self.assertEqual(v.state, 'unresolvable')
        self.assertTrue(v.is_undetermined)
        self.assertFalse(v.is_violation)


class TestTheSharedNamespaceAxisIsAnObservation(unittest.TestCase):
    """축 B — 관측이지 위반이 아니다. 그 한계가 모듈에 이름으로 적혀 있어야 한다."""

    def test_two_distributions_shipping_init_is_reported_not_failed(self):
        root = Path('/site-packages')
        v = guard.judge_name(
            'PySide6', ['PySide6', 'PySide6_Addons'],
            {'PySide6': root, 'PySide6_Addons': root},
            find_spec=_fixed({'PySide6': '/site-packages/PySide6'}),
        )
        # __init__.py 유무는 디스크를 보므로 이 경로에서는 없다 → 그냥 ok 다.
        # 중요한 것은 **위반이 아니라는 것**이다.
        self.assertFalse(v.is_violation)

    def test_the_limitation_is_named_in_the_module(self):
        """오탐 사유를 적어 두지 않으면 다음 세션이 이것을 방어층으로 믿는다."""
        text = (_SCRIPTS / 'check_import_name_ownership.py').read_text(encoding='utf-8')
        self.assertIn('NAMESPACE_AXIS_LIMITATION', text)
        self.assertIn('종료 코드를 바꾸지 않는다', text)


class TestNonVacuity(unittest.TestCase):
    """비-공허성 팔 — 이것들이 없으면 초록이 근거가 되지 못한다."""

    def test_zero_names_is_undetermined_not_pass(self):
        code, report = guard.render([])
        self.assertEqual(code, guard.EXIT_UNDETERMINED)
        self.assertIn('판정 불가', report)
        self.assertIn('「위반 없음」이 아니다', report)

    def test_the_report_always_says_how_many_it_judged(self):
        """「초록」만 찍으면 아무것도 안 본 초록과 구분되지 않는다."""
        ok = guard.NameVerdict('a', ('d',), '/site-packages/a', '/site-packages', 'ok')
        code, report = guard.render([ok])
        self.assertEqual(code, guard.EXIT_OK)
        self.assertIn('판정 1건', report)

    def test_non_import_names_are_excluded_by_definition_not_by_list(self):
        """실측: packages_distributions() 가 __pycache__ · PySide6/QtCore 를 냈다."""
        self.assertFalse(guard._is_import_name('__pycache__'))
        self.assertFalse(guard._is_import_name('PySide6/QtCore'))
        self.assertTrue(guard._is_import_name('domain'))
        self.assertTrue(guard._is_import_name('fcc_test_platform'))


class TestExitCodesKeepTheThreeStatesApart(unittest.TestCase):
    """0 · 1 · 2 는 서로 다른 사실이다. 접으면 판정이 거짓이 된다."""

    def test_violation_is_one(self):
        bad = guard.NameVerdict('domain', ('d',), '/repo/src/domain', '/sp', 'shadowed', 'x')
        self.assertEqual(guard.render([bad])[0], guard.EXIT_VIOLATION)

    def test_undetermined_is_two_not_zero(self):
        unk = guard.NameVerdict('x', ('d',), None, None, 'unresolvable', 'x')
        code, report = guard.render([unk])
        self.assertEqual(code, guard.EXIT_UNDETERMINED)
        self.assertIn('판정 불가는 통과가 아니다', report)

    def test_an_observation_alone_does_not_change_the_exit_code(self):
        obs = guard.NameVerdict('PySide6', ('a', 'b'), '/sp/PySide6', '/sp',
                                'namespace_shared', '셋이 주장한다')
        code, report = guard.render([obs])
        self.assertEqual(code, guard.EXIT_OK)
        self.assertIn('관측', report)


if __name__ == '__main__':  # pragma: no cover
    unittest.main(verbosity=2)
