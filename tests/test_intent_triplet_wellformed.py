"""삼종세트 검사(`scripts/check_intent_triplet.py`)의 봉인.

■ 두 축이다

    1. 이 저장소의 «실물» 삼종세트가 온전한가        (회귀 축)
    2. 깨진 것을 주입하면 «실제로 거부하는가»         (이빨 축)

2번이 없으면 「아무것도 안 하는 검사」와 구별되지 않는다. 이 저장소는
「검사는 있는데 아무것도 검사하지 않는」 상태를 여러 번 겪었다.
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import check_intent_triplet as triplet  # noqa: E402

_INTENT = ('# Intent: x\n\nAuthor: a\nDate: 2026-09-07\n'
           'Status: {status}\nSlug: {slug}\n')
_SPEC = ('# Spec: x\n\nAuthor: a\nDate: 2026-09-07\n'
         'Status: draft\nSlug: {slug}\n')
_PLAN = ('# Plan: x\n\nEngineer: a\nDate: 2026-09-07\n'
         'Status: draft\nSlug: {slug}\n')


def _fixture(tmp: Path, *, slug: str = 'a-slug', intent_status: str = 'accepted',
             with_spec: bool = False, with_plan: bool = False,
             spec_slug: str | None = None, stray: str | None = None,
             skip_templates: bool = False) -> Path:
    base = tmp / 'intent'
    if not skip_templates:
        (base / '_templates').mkdir(parents=True, exist_ok=True)
        for k in ('intent', 'spec', 'plan'):
            (base / '_templates' / f'{k}.md').write_text('tpl\n', encoding='utf-8')
    d = base / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / 'intent.md').write_text(_INTENT.format(status=intent_status, slug=slug),
                                 encoding='utf-8')
    if with_spec:
        (d / 'spec.md').write_text(_SPEC.format(slug=spec_slug or slug), encoding='utf-8')
    if with_plan:
        (d / 'plan.md').write_text(_PLAN.format(slug=slug), encoding='utf-8')
    if stray:
        (d / stray).write_text('x\n', encoding='utf-8')
    return tmp


class TestTheRealTripletsAreWellFormed(unittest.TestCase):
    """회귀 축 — 이 저장소가 실제로 들고 있는 것."""

    def test_every_intent_folder_in_this_repository_is_well_formed(self) -> None:
        found = triplet.problems(ROOT)
        self.assertEqual([], found,
                         '이 저장소의 삼종세트가 온전하지 않다:\n  ' +
                         '\n  '.join(found))


class TestTheCheckerHasTeeth(unittest.TestCase):
    """이빨 축 — 깨진 것을 넣으면 «이름으로» 거부해야 한다."""

    def _problems(self, **kw) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            return triplet.problems(_fixture(Path(td), **kw))

    def test_a_spec_under_a_draft_intent_is_refused(self) -> None:
        """주입 — 승인 안 된 의도에서 설계로 내려가는 것.

        이 흐름의 «요지»가 이것이다. 이 케이스가 없으면 나머지는 서식 검사일 뿐이다.
        """
        found = self._problems(intent_status='draft', with_spec=True)
        self.assertTrue(any('승인 없이 설계로 내려갔다' in p for p in found),
                        f'draft 의도 아래 spec 을 허용했다: {found}')

    def test_the_same_pair_passes_once_the_intent_is_accepted(self) -> None:
        """대조 — 축 하나(Status)만 바꾸면 통과해야 한다."""
        self.assertEqual([], self._problems(intent_status='accepted', with_spec=True))

    def test_a_plan_without_a_spec_is_refused(self) -> None:
        found = self._problems(with_spec=False, with_plan=True)
        self.assertTrue(any('plan.md 가 spec.md 없이' in p for p in found), found)

    def test_a_slug_mismatch_is_refused(self) -> None:
        """머리말 Slug 가 폴더와 다르면 브랜치 이름이 갈라진다."""
        found = self._problems(with_spec=True, spec_slug='other-slug')
        self.assertTrue(any('폴더 이름' in p for p in found), found)

    def test_an_uppercase_slug_is_refused(self) -> None:
        found = self._problems(slug='Bad_Slug')
        self.assertTrue(any('슬러그가' in p for p in found), found)

    def test_a_stray_markdown_in_the_folder_is_refused(self) -> None:
        """스펙이 둘 필요하면 의도를 둘로 나눠야 한다."""
        found = self._problems(with_spec=True, stray='spec-2.md')
        self.assertTrue(any('삼종세트 밖의' in p for p in found), found)

    def test_missing_templates_are_refused(self) -> None:
        """서식이 사라지면 동료가 복사할 것이 없어진다."""
        found = self._problems(skip_templates=True)
        self.assertTrue(any('서식이 없다' in p for p in found), found)

    def test_an_unknown_status_word_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            p = root / 'intent' / 'a-slug' / 'intent.md'
            p.write_text(p.read_text(encoding='utf-8').replace(
                'Status: accepted', 'Status: 승인함'), encoding='utf-8')
            found = triplet.problems(root)
        self.assertTrue(any('정의된 어휘가 아니다' in p for p in found), found)

    def test_a_missing_header_field_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td))
            p = root / 'intent' / 'a-slug' / 'intent.md'
            p.write_text(p.read_text(encoding='utf-8').replace('Author: a\n', ''),
                         encoding='utf-8')
            found = triplet.problems(root)
        self.assertTrue(any('`Author:` 이 없다' in p for p in found), found)

    def test_plan_is_asked_for_engineer_not_author(self) -> None:
        """서식과 검사가 «같은 필드 이름»을 봐야 한다.

        이 검사의 첫 판은 plan.md 에도 `Author:` 를 요구해 실물을 빨갛게 했다.
        서식은 `Engineer:` 를 쓴다 — 갈라지면 갈라진 쪽을 읽은 사람이 틀린다.
        """
        self.assertEqual(('Engineer', 'Date', 'Status', 'Slug'),
                         triplet.REQUIRED_FIELDS['plan'])
        with tempfile.TemporaryDirectory() as td:
            root = _fixture(Path(td), with_spec=True, with_plan=True)
            self.assertEqual([], triplet.problems(root))


class TestTheCheckerSaysWhenItCannotRun(unittest.TestCase):
    def test_a_missing_intent_directory_returns_two_not_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(2, triplet.main(['--root', td]))


class TestTheCheckerDeclaresWhatItCannotSee(unittest.TestCase):
    """검사가 «못 하는 것»이 문서에 남아 있어야 한다.

    이 문단이 지워지면 다음 사람이 초록을 「좋은 의도」로 읽는다.
    """

    def test_the_module_says_it_cannot_judge_content_quality(self) -> None:
        doc = (ROOT / 'scripts' / 'check_intent_triplet.py').read_text(encoding='utf-8')
        self.assertIn('내용의 품질을 못 본다', doc,
                      '검사가 «못 하는 것»의 선언이 사라졌다')


if __name__ == '__main__':
    unittest.main()
