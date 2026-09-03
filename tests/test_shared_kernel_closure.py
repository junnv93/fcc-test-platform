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


class TestTheBaselineIsPresentAndShaped(unittest.TestCase):
    """비-공허성 — 기준선이 없으면 이 축은 아무것도 붙잡지 않는다."""

    def test_baseline_exists_and_records_a_nonempty_closure(self):
        self.assertTrue(guard.BASELINE.is_file(), f'{guard.BASELINE} 가 없다')
        data = json.loads(guard.BASELINE.read_text(encoding='utf-8'))
        self.assertTrue(data['shared'], '기준선의 공유 폐포가 비어 있다 — 붙잡는 것이 없다')
        self.assertTrue(data['shared_tops'])


if __name__ == '__main__':  # pragma: no cover
    unittest.main(verbosity=2)
