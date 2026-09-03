"""``check_doc_links.py`` 의 봉인 (2026-09-03).

**이 봉인이 존재하는 이유는 내가 만든 결함이다.** 런북 5종을 이 레인으로 이관하면서
링크 무결성을 즉석 셸로 확인했는데, 그 검사가 **경로를 버리고 파일명만** 봤다. 그래서
다른 저장소를 가리키는 링크가 맨 이름으로 축약돼 「깨졌다」로 보고됐고, 나는 그것을
이름 오기로 읽고 **고쳤다**. 고치자 그 링크는 로컬에 실재하는 파일을 가리키게 되어
검사가 **초록**이 됐다 — 즉 **깨뜨렸기 때문에 통과했다.**

`.claude/rules/check-axis-blindness.md` 의 서식 그대로다:

    「프로세스가 안 보인다」 → 없는 것인가, 내 축이 못 보는 것인가?

여기서는 「링크가 깨졌다」 → **정말 깨진 것인가, 내 축(파일명)이 저장소 밖을 못 보는
것인가**였다. 그 축에서 두 상태가 같은 값을 갖는다.

그래서 이 봉인이 지키는 성질은 하나다:

> **저장소 밖 링크는 「깨짐」이 아니라 「판정 불가」다.**

그리고 그 구분은 **경로로만** 할 수 있다. 파일명은 그 축을 갖지 않는다.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO_ROOT / 'scripts'
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_doc_links as guard  # noqa: E402


class _Tree:
    """실제 파일로 판정한다 — 링크 해소는 파일시스템 축이라 흉내내면 축이 사라진다."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve() / 'repo'
        (self.root / 'docs' / 'operations').mkdir(parents=True)
        # 저장소 **밖**의 형제 저장소
        (self.root.parent / 'other_repo' / 'docs').mkdir(parents=True)
        (self.root.parent / 'other_repo' / 'docs' / 'guide.md').write_text('x', encoding='utf-8')
        return self

    def __exit__(self, *a):
        self._tmp.cleanup()

    def write(self, rel: str, body: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding='utf-8')
        return p


class TestTheAxisIsThePathNotTheFilename(unittest.TestCase):
    """이 봉인의 본체 — 2026-09-03 결함의 재현 방지."""

    def test_a_link_that_leaves_the_repo_is_undetermined_not_broken(self):
        with _Tree() as t:
            src = t.write('docs/operations/a.md',
                          '[g](../../../other_repo/docs/guide.md)')
            v = guard.judge_link(src, '../../../other_repo/docs/guide.md',
                                 repo_root=t.root)
            self.assertEqual(v.state, 'outside_repo')
            self.assertFalse(v.is_broken)
            self.assertTrue(v.is_undetermined)

    def test_the_same_filename_inside_the_repo_is_judged(self):
        """파일명이 같아도 **경로가 다르면 다른 판정**이어야 한다.

        이것이 없으면 봉인이 「이름」 축으로 되돌아간 것을 알아채지 못한다.
        """
        with _Tree() as t:
            src = t.write('docs/operations/a.md', 'x')
            broken = guard.judge_link(src, './guide.md', repo_root=t.root)
            self.assertEqual(broken.state, 'broken')
            t.write('docs/operations/guide.md', 'y')
            ok = guard.judge_link(src, './guide.md', repo_root=t.root)
            self.assertEqual(ok.state, 'ok')

    def test_renaming_an_outside_link_to_a_local_file_does_not_turn_it_green(self):
        """**내가 실제로 한 실수.** 저장소 밖 링크를 로컬에 있는 이름으로 바꾸면
        옛 검사는 초록이 됐다. 이 검사는 그 둘을 다른 상태로 답해야 한다."""
        with _Tree() as t:
            src = t.write('docs/operations/a.md', 'x')
            t.write('docs/operations/fcc-guide.md', 'local')
            outside = guard.judge_link(src, '../../../other_repo/docs/guide.md',
                                       repo_root=t.root)
            renamed = guard.judge_link(src, './fcc-guide.md', repo_root=t.root)
            self.assertEqual(outside.state, 'outside_repo')
            self.assertEqual(renamed.state, 'ok')
            # 둘은 **다른 상태**다. 옛 검사에서는 둘 다 파일명으로 축약돼
            # 「없음」과 「있음」이라는 같은 축의 두 값이었다.
            self.assertNotEqual(outside.state, renamed.state)

    def test_absolute_paths_are_not_this_repo(self):
        with _Tree() as t:
            src = t.write('docs/operations/a.md', 'x')
            v = guard.judge_link(src, '/home/someone/other/doc.md', repo_root=t.root)
            self.assertEqual(v.state, 'outside_repo')


class TestWhatIsNotJudged(unittest.TestCase):
    def test_urls_and_anchors_are_skipped(self):
        with _Tree() as t:
            src = t.write('docs/operations/a.md', 'x')
            for target in ('https://example.com/x.md', 'mailto:a@b.c', '#section', ''):
                self.assertEqual(
                    guard.judge_link(src, target, repo_root=t.root).state, 'skipped',
                    target,
                )


class TestNonVacuity(unittest.TestCase):
    def test_zero_judged_links_is_undetermined_not_pass(self):
        code, report = guard.render([])
        self.assertEqual(code, guard.EXIT_UNDETERMINED)
        self.assertIn('「깨진 링크 없음」이 아니다', report)

    def test_only_skipped_links_is_also_undetermined(self):
        skipped = [guard.LinkVerdict('a.md', 'https://x', None, 'skipped')]
        self.assertEqual(guard.render(skipped)[0], guard.EXIT_UNDETERMINED)

    def test_the_report_says_how_many_it_judged(self):
        ok = guard.LinkVerdict('a.md', './b.md', '/r/b.md', 'ok')
        code, report = guard.render([ok])
        self.assertEqual(code, guard.EXIT_OK)
        self.assertIn('판정 1건', report)

    def test_a_broken_local_link_fails(self):
        bad = guard.LinkVerdict('a.md', './b.md', '/r/b.md', 'broken')
        code, report = guard.render([bad])
        self.assertEqual(code, guard.EXIT_BROKEN)
        self.assertIn('깨짐', report)

    def test_outside_links_alone_do_not_fail_but_are_named(self):
        out = guard.LinkVerdict('a.md', '../../o/x.md', '/o/x.md', 'outside_repo')
        code, report = guard.render([out])
        self.assertEqual(code, guard.EXIT_OK)
        self.assertIn('판정하지 않는다', report)


class TestThisRepoHasNoBrokenLocalDocLinks(unittest.TestCase):
    """실제 저장소에 대해 돈다 — 봉인이 대상을 갖는지도 함께 확인한다."""

    def test_docs_tree(self):
        verdicts = guard.judge_tree([_REPO_ROOT / 'docs'], repo_root=_REPO_ROOT)
        judged = [v for v in verdicts if v.state != 'skipped']
        self.assertTrue(judged, 'docs/ 에서 판정할 상대 링크가 0건이다 — 봉인이 공허하다')
        broken = [f'{v.source} → {v.target}' for v in judged if v.is_broken]
        self.assertFalse(broken, f'깨진 로컬 링크: {broken}')


if __name__ == '__main__':  # pragma: no cover
    unittest.main(verbosity=2)
