"""승인 아티팩트와 코드의 «혼합 커밋» 거절(`scripts/check_intent_commit_separation.py`).

`intent.md`·`spec.md` 는 사람이 승인하는 대상이다. 코드와 한 커밋에 있으면
「의도 승인」이 코드까지 승인한 것이 되고, 승인한 사람은 자기가 무엇을
승인했는지 모른다.

■ 과발화도 결함이다

넓게 세면 문서 오탈자 하나가 spec 수정과 섞였다는 이유로 거절된다.
과발화하는 게이트는 꺼지고, 꺼진 게이트는 0층이다. 그래서 이 파일은
**거절해야 할 것**과 **거절하면 안 되는 것**을 같은 수만큼 단언한다.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import check_intent_commit_separation as sep  # noqa: E402


class TestItRefusesAMixedCommit(unittest.TestCase):

    def test_a_spec_with_code_is_refused(self) -> None:
        self.assertEqual(1, sep.main(['--paths', 'intent/a/spec.md', 'scripts/x.py']))

    def test_an_intent_with_code_is_refused(self) -> None:
        self.assertEqual(1, sep.main(['--paths', 'intent/a/intent.md',
                                      'fcc_test_platform/api/x.py']))

    def test_it_names_both_sides(self) -> None:
        artifacts, code = sep.split(['intent/a/spec.md', 'scripts/x.py', 'docs/z.md'])
        self.assertEqual(['intent/a/spec.md'], artifacts)
        self.assertEqual(['scripts/x.py'], code)


class TestItDoesNotOverFire(unittest.TestCase):
    """거절하면 «안 되는» 것들. 과발화는 게이트를 끄게 만든다."""

    def test_artifacts_alone_pass(self) -> None:
        self.assertEqual(0, sep.main(['--paths', 'intent/a/intent.md', 'intent/a/spec.md']))

    def test_code_alone_passes(self) -> None:
        self.assertEqual(0, sep.main(['--paths', 'scripts/x.py', 'tests/y.py']))

    def test_a_plan_with_code_passes(self) -> None:
        """`plan.md` 는 코드와 함께 가는 것이 «설계»다.

        리뷰어가 최종 diff 를 계획 표와 대조해야 하기 때문이다.
        이 케이스가 빠지면 모든 feature 커밋이 거절된다.
        """
        self.assertEqual(0, sep.main(['--paths', 'intent/a/plan.md', 'scripts/x.py']))

    def test_an_artifact_with_prose_passes(self) -> None:
        """문서는 코드가 아니다 — 좁게 센다."""
        self.assertEqual(0, sep.main(['--paths', 'intent/a/spec.md', 'docs/x.md',
                                      'README.md']))

    def test_the_readme_of_the_intent_folder_is_not_an_approval_artifact(self) -> None:
        """`intent/README.md` 는 «규칙»이지 승인 대상이 아니다."""
        artifacts, _ = sep.split(['intent/README.md', 'intent/_templates/spec.md'])
        self.assertEqual([], artifacts,
                         '규칙 문서와 서식을 승인 아티팩트로 셌다 — '
                         '그것들을 고칠 때마다 코드와 분리해야 해진다')


class TestTheHookCallsIt(unittest.TestCase):
    """검사를 만든 것과 그것이 «불리는» 것은 다른 명제다.

    이 저장소는 「아무 훅·스크립트·CI 도 부르지 않는 검사」를 이미 겪었다.
    """

    def test_pre_commit_invokes_the_separation_gate(self) -> None:
        hook = (ROOT / 'githooks' / 'pre-commit').read_text(encoding='utf-8')
        self.assertIn('check_intent_commit_separation.py', hook,
                      'pre-commit 이 이 게이트를 부르지 않는다 — '
                      '사람이 기억해서 타이핑해야 하는 게이트는 게이트가 아니다')

    def test_the_two_older_axes_survive(self) -> None:
        """축을 «덧붙였지» 덮어쓰지 않았다."""
        hook = (ROOT / 'githooks' / 'pre-commit').read_text(encoding='utf-8')
        for older in ('work_claim_branch_guard.py', 'supervisor_preflight.py'):
            self.assertIn(older, hook, f'기존 축 {older} 가 사라졌다')

    def test_the_bypass_is_loud_and_named(self) -> None:
        hook = (ROOT / 'githooks' / 'pre-commit').read_text(encoding='utf-8')
        self.assertIn('FCC_ALLOW_MIXED_INTENT_COMMIT', hook)


if __name__ == '__main__':
    unittest.main()
