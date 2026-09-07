"""문서가 «선언한» 게이트 값과 실제가 일치하는가.

■ 이 검사가 태어난 이유는 이 저장소의 «오늘» 데이터다

2026-09-07 한 세션이 branch protection 을 켜면서 **네 번** 틀린 진술을 형제
세션 여섯에 방송했고, 네 번 다 「이 게이트가 무엇을 강제하는가」였다.
**강제를 주장하는 산문은 손으로 쓰면 반드시 낡거나 틀린다.**

■ 두 축, 그리고 «판정 불가»는 통과가 아니다

라이브 축은 CI 에서 돌지 못한다 — branch protection 읽기는 admin 권한을
요구하고 기본 `GITHUB_TOKEN` 에는 없다. 그래서 닿지 못하면 **「판정 불가」라고
말하게** 하고, 그 «말하는 것»을 여기서 단언한다. 조용한 skip 은 초록과 같은
모양이라 그것 자체가 결함이다.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import check_gate_declaration as gate  # noqa: E402


class TestTheDeclarationItselfIsSound(unittest.TestCase):

    def setUp(self) -> None:
        self.decl = gate.load(ROOT)

    def test_every_declared_value_has_a_rationale(self) -> None:
        """값만 있고 근거가 없으면 다음 사람이 그것을 「단순화」하며 되돌린다."""
        self.assertEqual(sorted(self.decl['values']),
                         sorted(self.decl['why_each_value']))

    def test_the_declaration_is_not_empty(self) -> None:
        """빈 선언은 «항상» 통과한다 — 공집합형 봉인의 고전적 결함."""
        self.assertGreaterEqual(len(self.decl['values']), 5,
                                '선언이 너무 작다 — 무엇을 대조하는지 세어 봐라')

    def test_the_offline_axis_reports_how_many_it_compared(self) -> None:
        """「문제 없음」과 「아무것도 안 봤음」은 달라야 한다."""
        problems, compared = gate.offline_problems(ROOT, self.decl)
        self.assertEqual([], problems, problems)
        self.assertEqual(len(self.decl['values']), compared)


class TestTheProseCitesTheDeclaration(unittest.TestCase):
    """산문이 값을 «다시 적으면» 사본이 되고, 사본은 갈라진다."""

    def test_each_citing_document_names_the_declaration(self) -> None:
        for rel in gate.CITING_DOCS:
            with self.subTest(doc=rel):
                p = ROOT / rel
                self.assertTrue(p.is_file(), f'{rel} 이 없다')
                self.assertIn(gate.DECLARATION.name, p.read_text(encoding='utf-8'),
                              f'{rel} 이 선언을 인용하지 않는다 — 선언이 고아가 된다')


class TestTheComparisonHasTeeth(unittest.TestCase):
    """가짜 실제 설정을 주입해 «거부하는지» 본다."""

    def test_a_differing_value_is_caught(self) -> None:
        problems, n = gate.compare_values(
            {'enforce_admins.enabled': True},
            {'enforce_admins': {'enabled': False}})
        self.assertEqual(1, n)
        self.assertTrue(any('enforce_admins.enabled' in p for p in problems), problems)

    def test_a_matching_value_is_not_caught(self) -> None:
        problems, n = gate.compare_values(
            {'enforce_admins.enabled': True},
            {'enforce_admins': {'enabled': True}})
        self.assertEqual(([], 1), (problems, n))

    def test_a_key_absent_from_the_live_config_is_caught_not_skipped(self) -> None:
        """⚠️ 가장 중요한 케이스. 오타 난 키를 «조용히 건너뛰면» 선언이 공허해진다."""
        problems, n = gate.compare_values(
            {'enforce_admins.enbaled': True},          # ← 오타
            {'enforce_admins': {'enabled': True}})
        self.assertEqual(1, n, '오타 난 키를 대조 «횟수»에서 뺐다 — 공허해진다')
        self.assertTrue(any('없다' in p for p in problems),
                        f'없는 키를 통과시켰다: {problems}')

    def test_the_real_declaration_would_catch_a_flip(self) -> None:
        """실물 선언에 «반대 값»을 넣으면 전부 걸려야 한다."""
        decl = gate.load(ROOT)
        flipped = {k: (not v) if isinstance(v, bool) else v
                   for k, v in decl['values'].items()}
        live = {'required_pull_request_reviews':
                    {'required_approving_review_count': 0,
                     'require_code_owner_reviews': False},
                'required_status_checks': {'strict': False, 'contexts': ['lane-check']},
                'enforce_admins': {'enabled': True},
                'required_linear_history': {'enabled': False},
                'allow_force_pushes': {'enabled': False},
                'allow_deletions': {'enabled': False}}
        problems, n = gate.compare_values(flipped, live)
        bools = sum(1 for v in decl['values'].values() if isinstance(v, bool))
        self.assertEqual(bools, len(problems),
                         f'불리언 {bools}개를 뒤집었는데 {len(problems)}개만 걸렸다')


class TestUnmeasuredIsNotGreen(unittest.TestCase):

    def test_the_checker_announces_when_it_cannot_reach_github(self) -> None:
        """닿지 못한 것을 «말하는» 문자열이 있어야 한다."""
        src = (ROOT / 'scripts' / 'check_gate_declaration.py').read_text(encoding='utf-8')
        self.assertIn('판정 불가', src)
        self.assertIn('「일치한다」가 아니라 「재지 못했다」', src,
                      '판정 불가를 일치로 읽지 말라는 문장이 사라졌다')

    def test_a_missing_declaration_returns_two_not_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(2, gate.main(['--root', td]))


class TestTheOfflineAxisHasTeeth(unittest.TestCase):

    def _decl(self, **over) -> dict:
        d = {'repo': 'x/y', 'branch': 'main',
             'values': {'a.b': True}, 'why_each_value': {'a.b': '왜'}}
        d.update(over)
        return d

    def test_a_value_without_a_rationale_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel in gate.CITING_DOCS:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(gate.DECLARATION.name, encoding='utf-8')
            problems, _ = gate.offline_problems(
                root, self._decl(values={'a.b': True, 'c.d': 1}))
        self.assertTrue(any('근거 없는' in p for p in problems), problems)

    def test_an_empty_declaration_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            problems, n = gate.offline_problems(Path(td), self._decl(values={}))
        self.assertEqual(0, n)
        self.assertTrue(any('값이 하나도 없다' in p for p in problems), problems)


if __name__ == '__main__':
    unittest.main()
