"""``check_shared_kernel_closure.py`` 의 봉인 (2026-09-03).

**이 봉인이 지키는 성질은 「목록이 아니라 폐포」다.**

실측 2026-09-03: 한 세션이 공유 커널을 「15개」로 보고했는데 그것은 **그 세션이 복사한
집합**이었다. 폐포로 재니 53개였고, 방향도 양쪽이었다(중앙만 47 · provider 만 282).
손으로 센 수는 세는 사람의 우연을 담는다 — 그래서 이 검사는 세지 않고 **계산한다.**

⚠️ 이 봉인은 **provider 트리의 존재에 기대지 않는다.** 저장소 하나만 있는 체크아웃이
정상 상태이므로, 그 상태에서 red 를 내면 CI 와 새 클론이 전부 빨간불이 되고 그런 게이트는
삭제된다. 대신 임시 트리를 **구성해서** 판정 로직을 시험한다.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO_ROOT / 'scripts'
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_shared_kernel_closure as guard  # noqa: E402


class _TwoLanes:
    """중앙 레인과 provider 레인을 실제 파일로 만든다.

    폐포는 파일시스템과 import 문의 축이라, 흉내내면 그 축이 사라진다.
    """

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.central = root / 'central'
        self.provider_src = root / 'provider' / 'src'
        for base in (self.central, self.provider_src):
            (base / 'shared_pkg').mkdir(parents=True)
            (base / 'shared_pkg' / '__init__.py').write_text('', encoding='utf-8')
        # 중앙 씨앗
        (self.central / 'fcc_test_platform').mkdir()
        return self

    def __exit__(self, *a):
        self._tmp.cleanup()

    def write(self, base: Path, rel: str, body: str = ''):
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding='utf-8')
        return p


class TestTheClosureIsComputedNotListed(unittest.TestCase):
    def test_only_modules_both_lanes_reach_are_shared(self):
        with _TwoLanes() as t:
            for base in (t.central, t.provider_src):
                t.write(base, 'shared_pkg/both.py', 'X = 1')
                t.write(base, 'shared_pkg/only_central.py', 'X = 2')
                t.write(base, 'shared_pkg/only_provider.py', 'X = 3')
            t.write(t.central, 'fcc_test_platform/app.py',
                    'from shared_pkg.both import X\nfrom shared_pkg.only_central import X as Y')
            t.write(t.provider_src, 'runner.py',
                    'from shared_pkg.both import X\nfrom shared_pkg.only_provider import X as Z')
            obs = guard.measure(t.central, t.provider_src)
            self.assertEqual(obs['shared'], ['shared_pkg/both.py'])
            self.assertEqual(obs['central_only'], 1)
            self.assertEqual(obs['provider_only'], 1)

    def test_transitive_reach_counts(self):
        """직접 import 만 세면 폐포가 아니다."""
        with _TwoLanes() as t:
            for base in (t.central, t.provider_src):
                t.write(base, 'shared_pkg/leaf.py', 'X = 1')
                t.write(base, 'shared_pkg/mid.py', 'from shared_pkg.leaf import X')
            t.write(t.central, 'fcc_test_platform/app.py', 'from shared_pkg.mid import X')
            t.write(t.provider_src, 'runner.py', 'from shared_pkg.mid import X')
            obs = guard.measure(t.central, t.provider_src)
            self.assertIn('shared_pkg/leaf.py', obs['shared'])

    def test_shared_top_level_names_are_discovered_not_hardcoded(self):
        with _TwoLanes() as t:
            t.write(t.central, 'extra_pkg/__init__.py')
            t.write(t.provider_src, 'extra_pkg/__init__.py')
            tops = guard.discover_shared_tops(t.central, t.provider_src)
            self.assertIn('shared_pkg', tops)
            self.assertIn('extra_pkg', tops)


class TestSubmoduleImportsAreEdgesToo(unittest.TestCase):
    """``from <패키지> import <서브모듈>`` 도 간선이다.

    ⚠️ **실측 2026-09-03 — 이 팔이 없던 동안 게이트가 과소계수하고 있었다.**
    `application/central_contract/api_surfaces.py` 가 표면 9개를 이 형식으로
    부르는데, 워커는 `node.module`(= 패키지) 만 담았다. 그 패키지의
    `__init__.py` 는 **순수 docstring** 이라 거기서 탐색이 멈췄고,
    표면 9개와 `api_operation_factory` 가 폐포에 영영 들어오지 않았다.

    **축 맹점이다** — AST 축에서 「import 안 함」과 「서브모듈로 import 함」이
    같은 값이다. 그리고 틀리는 방향이 나쁜 쪽이다: 완료 오라클이 *「공유 폐포 0」*
    인데, 이 워커로는 **모듈이 아직 공유 중인데도 0** 이 나올 수 있다.

    ⚠️ 속성 import(`from m import SOME_CONST`)를 모듈로 오인하지 않는 것은
    `_resolve` 가 담당한다 — 파일이 없으면 해소되지 않는다. 그래서 판정은
    이름 모양이 아니라 **파일 존재**다.
    """

    def _both_lanes_reach(self, t, pkg_init_body):
        for base in (t.central, t.provider_src):
            t.write(base, 'shared_pkg/pkg/__init__.py', pkg_init_body)
            t.write(base, 'shared_pkg/pkg/leaf.py', 'X = 1')
            t.write(base, 'shared_pkg/facade.py',
                    'from shared_pkg.pkg import leaf')
        t.write(t.central, 'fcc_test_platform/app.py', 'import shared_pkg.facade')
        t.write(t.provider_src, 'runner.py', 'import shared_pkg.facade')
        return guard.measure(t.central, t.provider_src)

    def test_a_submodule_imported_from_its_package_is_in_the_closure(self):
        """패키지 `__init__` 이 비어 있어도 잎에 도달해야 한다.

        비어 있는 `__init__` 이 이 결함의 조건이었다 — 그것이 무언가를 재수출했다면
        그 import 문이 우연히 간선을 만들어 결함이 가려졌을 것이다.
        """
        with _TwoLanes() as t:
            obs = self._both_lanes_reach(t, '')
            self.assertIn('shared_pkg/pkg/leaf.py', obs['shared'])

    def test_an_attribute_import_is_not_mistaken_for_a_submodule(self):
        """⚠️ 반대 방향 — 고치면서 과잉계수하지 않았음을 잰다.

        이 팔이 없으면 「간선을 더 담아라」가 「아무 이름이나 담아라」로 퇴화하고,
        그때 폐포가 부풀어 완료 오라클이 영영 0 이 되지 않는다.
        """
        with _TwoLanes() as t:
            for base in (t.central, t.provider_src):
                t.write(base, 'shared_pkg/consts.py', 'NAME = 1')
                t.write(base, 'shared_pkg/user.py',
                        'from shared_pkg.consts import NAME')
            t.write(t.central, 'fcc_test_platform/app.py', 'import shared_pkg.user')
            t.write(t.provider_src, 'runner.py', 'import shared_pkg.user')
            obs = guard.measure(t.central, t.provider_src)
            self.assertIn('shared_pkg/consts.py', obs['shared'])
            self.assertNotIn('shared_pkg/consts/NAME.py', obs['shared'])
            self.assertEqual(
                [], [m for m in obs['shared'] if m.endswith('/NAME.py')],
                '속성 이름이 모듈로 해소됐다 — 과잉계수다',
            )


class TestVocabularyIsJudgedOnCodeNotProse(unittest.TestCase):
    """어휘는 타입이 지는 것이고 산문이 언급하는 것이 아니다.

    실측 2026-09-03: 이 구분 없이 세면 16, 있으면 9다.
    """

    def _shared_with(self, t, body):
        for base in (t.central, t.provider_src):
            t.write(base, 'shared_pkg/m.py', body)
        t.write(t.central, 'fcc_test_platform/app.py', 'from shared_pkg.m import X')
        t.write(t.provider_src, 'runner.py', 'from shared_pkg.m import X')
        return guard.measure(t.central, t.provider_src)

    def test_vocabulary_in_a_docstring_is_not_vocabulary(self):
        with _TwoLanes() as t:
            obs = self._shared_with(t, '"""WLAN 과 BLE 를 설명하는 산문."""\nX = 1')
            self.assertEqual(obs['provider_vocabulary'], [])

    def test_vocabulary_in_code_is_vocabulary(self):
        with _TwoLanes() as t:
            obs = self._shared_with(t, "X = ['WLAN', 'BLE']")
            self.assertEqual(obs['provider_vocabulary'], ['shared_pkg/m.py'])


class TestFirstPartyIsNotThirdParty(unittest.TestCase):
    """규칙이 제약하는 것은 서드파티이지 형제 레인이 아니다.

    합치면 4, 나누면 서드파티 0 — 두 숫자가 다른 결정을 지지한다.
    """

    def test_a_new_sibling_lane_is_recognised_without_editing_a_list(self):
        """⚠️ 목록판이 한 커밋 만에 낡았다 — `fcc_test_kernel` 이 생기자 서드파티로
        집계됐다. 판정은 이 계열의 접두사이지 손 목록이 아니다."""
        with _TwoLanes() as t:
            for base in (t.central, t.provider_src):
                t.write(base, 'shared_pkg/m.py', 'import fcc_test_brandnewlane')
            t.write(t.central, 'fcc_test_platform/app.py', 'import shared_pkg.m')
            t.write(t.provider_src, 'runner.py', 'import shared_pkg.m')
            obs = guard.measure(t.central, t.provider_src)
            self.assertEqual(obs['third_party_dependencies'], {})
            self.assertIn('shared_pkg/m.py', obs['sibling_lane_dependencies'])

    def test_sibling_lane_import_is_not_counted_as_third_party(self):
        with _TwoLanes() as t:
            for base in (t.central, t.provider_src):
                t.write(base, 'shared_pkg/m.py', 'from fcc_test_contracts.common import x')
            t.write(t.central, 'fcc_test_platform/app.py', 'from shared_pkg.m import x')
            t.write(t.provider_src, 'runner.py', 'from shared_pkg.m import x')
            obs = guard.measure(t.central, t.provider_src)
            self.assertEqual(obs['third_party_dependencies'], {})
            self.assertIn('shared_pkg/m.py', obs['sibling_lane_dependencies'])

    def test_a_real_third_party_is_counted(self):
        with _TwoLanes() as t:
            for base in (t.central, t.provider_src):
                t.write(base, 'shared_pkg/m.py', 'import openpyxl')
            # ⚠️ `from shared_pkg import m` 는 __init__.py 로 해소되어 m.py 가
            # 폐포에 안 들어온다. 모듈을 직접 지목해야 이 축이 시험된다.
            t.write(t.central, 'fcc_test_platform/app.py', 'import shared_pkg.m')
            t.write(t.provider_src, 'runner.py', 'import shared_pkg.m')
            obs = guard.measure(t.central, t.provider_src)
            self.assertEqual(obs['third_party_dependencies'], {'shared_pkg/m.py': ['openpyxl']})


class TestTheRatchetDirection(unittest.TestCase):
    """늘면 red, 줄면 green — 그러나 기준선을 자동으로 낮추지 않는다."""

    def _obs(self, shared):
        return {'shared': shared, 'central_only': 1, 'provider_only': 1,
                'provider_vocabulary': [], 'third_party_dependencies': {},
                'sibling_lane_dependencies': {}, 'shared_tops': ['shared_pkg']}

    def test_growth_fails_and_names_what_grew(self):
        code, report = guard.judge(self._obs(['a.py', 'b.py']), {'shared': ['a.py']})
        self.assertEqual(code, guard.EXIT_GREW)
        self.assertIn('b.py', report)

    def test_shrinking_passes_and_says_the_baseline_is_not_auto_lowered(self):
        code, report = guard.judge(self._obs(['a.py']), {'shared': ['a.py', 'b.py']})
        self.assertEqual(code, guard.EXIT_OK)
        self.assertIn('자동으로 낮추지 않는다', report)

    def test_no_baseline_is_undetermined_not_pass(self):
        code, report = guard.judge(self._obs(['a.py']), None)
        self.assertEqual(code, guard.EXIT_UNDETERMINED)
        self.assertIn('기준선이 없다', report)

    def test_empty_closure_is_undetermined_not_pass(self):
        code, report = guard.judge(self._obs([]), {'shared': []})
        self.assertEqual(code, guard.EXIT_UNDETERMINED)
        self.assertIn('「공유가 없다」가 아니라', report)


class TestTheTwoKindsOfZero(unittest.TestCase):
    """공유 0 에는 **두 상태**가 있고, 하나는 성공이다 (2026-09-03).

    ⚠️ 이 팔이 없던 동안 게이트는 **목표를 달성하면 답할 수 없었다.**
    완료 오라클이 *「공유 폐포 0」* 인데, 3단계가 그 0 을 만들자 공유 최상위
    이름 집합이 비어 `Undetermined` 가 났다 — **성공이 실패와 같은 모양**이었다.

    가르는 증거는 **두 트리를 실제로 읽었는가**다. 양쪽에 파이썬 파일이 있는데
    겹치는 최상위가 없다면 그것은 「못 찾았다」가 아니라 「이름을 다 놓았다」다.
    """

    def test_a_completed_zero_passes_and_says_so(self):
        with _TwoLanes() as t:
            # 겹치는 최상위가 없도록 각자 고유 이름만 둔다
            t.write(t.central, 'fcc_test_platform/app.py', 'X = 1')
            t.write(t.provider_src, 'runner.py', 'Y = 2')
            for base in (t.central, t.provider_src):
                for stale in (base / 'shared_pkg').rglob('*.py'):
                    stale.unlink()
            obs = guard.measure(t.central, t.provider_src)
            self.assertEqual([], obs['shared'])
            self.assertTrue(obs.get('both_trees_read'))
            code, report = guard.judge(obs, {'shared': ['x.py']})
            self.assertEqual(guard.EXIT_OK, code)
            self.assertIn('종료 상태', report)

    def test_a_zero_from_an_unreadable_tree_is_still_undetermined(self):
        """⚠️ 반대 방향 — 경로가 틀린 것을 「완료」로 읽으면 안 된다.

        이 팔이 없으면 위 정정이 *「0 이면 통과」* 로 퇴화하고, 그때 provider
        경로를 잘못 준 실행이 **완료를 선언한다.**
        """
        with _TwoLanes() as t:
            empty = pathlib.Path(t.central.parent) / 'nothing-here'
            empty.mkdir()
            with self.assertRaises(guard.Undetermined):
                guard.measure(t.central, empty)

    def test_judge_without_the_evidence_key_is_undetermined(self):
        """증거 키가 없는 관측은 통과시키지 않는다 — 옛 판과 섞여도 안전하다."""
        code, report = guard.judge(
            {'shared': [], 'central_only': 0, 'provider_only': 0,
             'provider_vocabulary': [], 'third_party_dependencies': {},
             'sibling_lane_dependencies': {}, 'shared_tops': []},
            {'shared': ['x.py']},
        )
        self.assertEqual(guard.EXIT_UNDETERMINED, code)
        self.assertIn('증거가 없다', report)


class TestTheBaselineIsPresentAndShaped(unittest.TestCase):
    """비-공허성 — 기준선이 없으면 이 축은 아무것도 붙잡지 않는다.

    ⚠️ **이 팔은 2026-09-03 에 정정됐다. 「목표에 도달하지 않는다」를 전제하고
    있었다.**

    원래 술어는 *「기준선의 공유 폐포가 비어 있지 않다」* 였고, 폐포가 0 이 아닌
    동안은 옳은 비-공허성이었다. 그런데 3단계가 **폐포를 0 으로 만들자 그것이
    red 가 됐다** — 종료 상태를 검사가 금지하고 있었던 것이다.

    붙잡아야 할 성질은 「기준선이 비어 있지 않다」가 아니라
    **「기준선이 회귀를 잡는다」**다. 빈 기준선도 그것을 한다 — 무엇이든 늘면
    늘어난 것이다. 축을 그리로 옮긴다.
    """

    def test_baseline_exists_and_has_the_shape_judge_reads(self):
        self.assertTrue(guard.BASELINE.is_file(), f'{guard.BASELINE} 가 없다')
        data = json.loads(guard.BASELINE.read_text(encoding='utf-8'))
        for key in ('shared', 'shared_tops'):
            with self.subTest(key=key):
                self.assertIn(key, data, f'judge 가 읽는 키 {key!r} 가 없다')
                self.assertIsInstance(data[key], list)

    def test_the_recorded_baseline_still_catches_growth(self):
        """⚠️ **진짜 비-공허성은 이것이다** — 기준선이 무엇을 붙잡는가.

        빈 기준선이든 아니든, 실제 파일에 기록된 그 값이 **늘어남을 잡아야**
        의미가 있다. 이 팔은 기준선이 0 이 된 뒤에도 계속 참이다.
        """
        data = json.loads(guard.BASELINE.read_text(encoding='utf-8'))
        grown = {
            'shared': sorted(set(data['shared']) | {'zz_regression_probe.py'}),
            'central_only': 1, 'provider_only': 1, 'provider_vocabulary': [],
            'third_party_dependencies': {}, 'sibling_lane_dependencies': {},
            'shared_tops': ['zz'], 'both_trees_read': True,
        }
        code, report = guard.judge(grown, data)
        self.assertEqual(guard.EXIT_GREW, code)
        self.assertIn('zz_regression_probe.py', report)


if __name__ == '__main__':  # pragma: no cover
    unittest.main(verbosity=2)
