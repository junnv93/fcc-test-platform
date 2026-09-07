"""방아쇠 판정기(`scripts/human_judgment_triggers.py`)의 봉인.

■ 왜 «양방향» 인가

발화만 단언하면 「항상 거부하는 판정기」가 통과한다.
미발화만 단언하면 「항상 통과하는 판정기」가 통과한다.
**둘 다 없으면 아무것도 안 하는 판정기와 구별되지 않는다.**

그래서 방아쇠마다 **깨진 것과 정상인 것을 한 쌍씩** 넣는다. 그리고 각 쌍은
**한 축만 다르다** — 두 축이 함께 움직이면 어느 것이 판정을 바꿨는지 모른다.

■ 왜 진짜 git 저장소를 만드는가

판정기는 `git diff`·`git show` 로 입력을 모은다. 그것을 가짜로 바꾸면
**가짜가 죽은 계약을 보존한다** — 이 저장소가 이미 기록한 형태다. 시험은
시그니처를 흉내내는 대신 진짜 저장소를 만들어 판정을 그쪽에 위임한다.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import human_judgment_triggers as judge  # noqa: E402


def _git(root: Path, *args: str) -> None:
    subprocess.run(['git', '-C', str(root), *args], check=True,
                   capture_output=True, text=True)


class _Repo:
    """방아쇠를 재기 위한 최소 저장소. base 커밋 하나 + head 커밋 하나."""

    def __init__(self, tmp: Path) -> None:
        self.root = tmp
        _git(tmp, 'init', '-q', '-b', 'main')
        _git(tmp, 'config', 'user.email', 't@t')
        _git(tmp, 'config', 'user.name', 't')
        # 훅이 이 임시 저장소에서 돌면 안 된다 — 이 시험은 판정기만 잰다.
        _git(tmp, 'config', 'core.hooksPath', str(tmp / '.no-hooks'))
        (tmp / 'pyproject.toml').write_text('[project]\nname="x"\n', encoding='utf-8')

    def write(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')

    def commit(self, message: str) -> str:
        _git(self.root, 'add', '-A')
        _git(self.root, 'commit', '-q', '-m', message, '--no-verify')
        out = subprocess.run(['git', '-C', str(self.root), 'rev-parse', 'HEAD'],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()

    def baseline(self, names: list[str]) -> None:
        self.write('delivered_test_run_baseline.json',
                   json.dumps({'lane': 'x', 'baseline': names}, ensure_ascii=False))

    def verdict(self) -> judge.Verdict:
        return judge.evaluate(self.root, self.base, 'HEAD')


def _make(tmp: Path, *, baseline_before: list[str] | None = None) -> _Repo:
    repo = _Repo(tmp)
    repo.baseline(baseline_before or [])
    repo.write('intent/README.md', 'rules\n')
    repo.base = repo.commit('base')
    return repo


_SPEC_HEAD = '# Spec: x\n\n## 7. 열린 질문\n\n'
_PLAN_HEAD = '# Plan: x\n\n## 1. 바뀌는 파일\n\n'


class TestT2DebtRegistrationNeedsANamedDecision(unittest.TestCase):
    """T2 — 기준선에 실패 «이름이 추가»되면 부채 등재다."""

    def _run(self, attestation: str) -> judge.Verdict:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            repo = _make(Path(td), baseline_before=['tests/a.py::x'])
            repo.baseline(['tests/a.py::x', 'tests/b.py::y'])   # ← 이름 «추가»
            repo.write('intent/s/spec.md',
                       _SPEC_HEAD + f'**Debt-Accepted-By**: {attestation}\n')
            repo.commit('head')
            return repo.verdict()

    def test_an_added_baseline_name_without_an_attestation_fires(self) -> None:
        """주입 ① — 이름을 추가하고 어테스테이션이 없으면 «거부»."""
        names = [t.name for t in self._run('(없음)').blocking]
        self.assertIn('T2', names,
                      '기준선에 이름을 추가했는데 T2 가 발화하지 않았다 — '
                      '부채가 조용히 등재된다')

    def test_a_named_attestation_clears_it(self) -> None:
        """주입 ② — 같은 추가에 이름과 사유를 달면 «통과». 축 하나만 다르다."""
        names = [t.name for t in self._run('홍길동 — 상류 수리 대기').blocking]
        self.assertNotIn('T2', names,
                         '이름 붙은 부채 등재를 T2 가 막았다 — 과발화다')


class TestT3UnansweredQuestionsBlock(unittest.TestCase):
    """T3 — 답 없는 열린 질문이 남으면 설계가 확정될 수 없다."""

    def _run(self, question_block: str) -> judge.Verdict:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            repo = _make(Path(td))
            repo.write('intent/s/spec.md', _SPEC_HEAD + question_block)
            repo.commit('head')
            return repo.verdict()

    def test_a_blank_answer_slot_fires(self) -> None:
        """주입 ③ — `→ 답( ):` 이 비어 있으면 «거부»."""
        v = self._run('1. 이것은?\n   → 답( ): \n')
        self.assertIn('T3', [t.name for t in v.blocking],
                      '답 없는 질문을 T3 가 못 봤다')

    def test_a_named_answer_clears_it(self) -> None:
        """주입 ④ — 이름과 내용이 있으면 «통과»."""
        v = self._run('1. 이것은?\n   → 답(홍길동): 그대로 간다\n')
        self.assertNotIn('T3', [t.name for t in v.blocking],
                         '답이 달린 질문을 T3 가 막았다 — 과발화다')

    def test_a_name_without_content_is_not_an_answer(self) -> None:
        """주입 ⑤ — 이름만 있고 «내용이 없으면» 답이 아니다.

        17항목 자가점검의 「사유 축」과 같은 판정이다: 상태 뒤에 아무것도 없는
        행은 어테스테이션이 아니라 단어다. 이 케이스가 없으면
        `→ 답(홍길동):` 만 적어도 통과하고, 그것이 이 방아쇠의 가장 싼 우회다.
        """
        v = self._run('1. 이것은?\n   → 답(홍길동):\n')
        self.assertIn('T3', [t.name for t in v.blocking],
                      '이름만 있고 내용이 없는 것을 답으로 셌다 — 우회가 3초다')


class TestT4UnplannedFilesBlock(unittest.TestCase):
    """T4 — 계획 표에 없는 파일이 diff 에 있으면 범위가 조용히 넓어진 것이다."""

    def _run(self, *, plan_rows: str, extra: str | None,
             attestation: str = '(없음)') -> judge.Verdict:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            repo = _make(Path(td))
            repo.write('intent/s/plan.md',
                       _PLAN_HEAD + plan_rows + f'\n**Scope-Extended-By**: {attestation}\n')
            repo.write('src/planned.py', 'x = 1\n')
            if extra:
                repo.write(extra, 'y = 2\n')
            repo.commit('head')
            return repo.verdict()

    _ROW = '| 1 | `src/planned.py` | 무엇을 | 왜 | R1 |\n'

    def test_a_file_outside_the_plan_table_fires(self) -> None:
        """주입 ⑥ — 표에 없는 파일이 있으면 «거부»."""
        v = self._run(plan_rows=self._ROW, extra='src/sneaked.py')
        self.assertIn('T4', [t.name for t in v.blocking],
                      '계획에 없는 파일을 T4 가 못 봤다 — 범위가 조용히 넓어진다')

    def test_the_same_change_passes_once_the_table_names_it(self) -> None:
        """주입 ⑦ — 그 파일을 표에 «추가»하면 통과. 축 하나만 다르다."""
        rows = self._ROW + '| 2 | `src/sneaked.py` | 무엇을 | 왜 | R2 |\n'
        v = self._run(plan_rows=rows, extra='src/sneaked.py')
        self.assertNotIn('T4', [t.name for t in v.blocking],
                         '표에 적힌 파일을 T4 가 막았다 — 과발화다')

    def test_a_named_scope_extension_also_clears_it(self) -> None:
        """주입 ⑧ — 표 대신 이름 붙은 확대 선언으로도 통과."""
        v = self._run(plan_rows=self._ROW, extra='src/sneaked.py',
                      attestation='홍길동 — 같은 결함의 다른 자리')
        self.assertNotIn('T4', [t.name for t in v.blocking],
                         '이름 붙은 범위 확대를 T4 가 막았다 — 과발화다')

    def test_the_triplet_itself_is_exempt(self) -> None:
        """계획서가 «자기 자신»을 첫 행으로 갖게 하면 안 된다."""
        v = self._run(plan_rows=self._ROW, extra='intent/s/intent.md')
        self.assertNotIn('T4', [t.name for t in v.blocking],
                         '삼종세트 자신이 T4 를 발화시켰다')


class TestThePlanTableExtractorSeesEveryShapeOfPath(unittest.TestCase):
    """판정기가 「적을 수 없는 것」을 요구하면 T4 는 장식이 된다.

    첫 판은 「`/` 가 있거나 `.md`/`.py` 로 끝나는 것」만 경로로 셌고, 그래서
    루트의 점파일(`.gitignore`)은 표에 적어도 계속 T4 를 발화시켰다.
    사람은 그럴 때 어테스테이션으로 도망간다.
    """

    def _planned(self, row: str) -> set[str]:
        return judge._planned_paths(_PLAN_HEAD + row)

    def test_a_nested_path(self) -> None:
        self.assertIn('scripts/x.py', self._planned('| 1 | `scripts/x.py` | a | b |\n'))

    def test_a_root_dotfile(self) -> None:
        self.assertIn('.gitignore', self._planned('| 1 | `.gitignore` | a | b |\n'),
                      '루트 점파일을 계획에 적을 수 없다 — T4 가 영원히 발화한다')

    def test_a_root_file_with_an_extension(self) -> None:
        self.assertIn('CODEOWNERS.md',
                      self._planned('| 1 | `CODEOWNERS.md` | a | b |\n'))

    def test_prose_outside_the_table_is_not_counted(self) -> None:
        """선언은 «표»이지 산문이 아니다."""
        self.assertEqual(set(), judge._planned_paths('본문에서 `scripts/y.py` 를 언급한다\n'))


class TestT1IsReportedButDoesNotBlock(unittest.TestCase):
    """T1 — 오늘 «게이트가 아니다». 그 사실이 판정기 안에 있어야 한다."""

    def test_a_codeowned_path_is_advisory_not_blocking(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            repo = _make(Path(td))
            repo.write('CODEOWNERS', '/migrations/   @lead\n')
            repo.write('migrations/001.sql', 'select 1;\n')
            repo.write('intent/s/plan.md',
                       _PLAN_HEAD + '| 1 | `migrations/` | x | y | R1 |\n'
                       '| 2 | `CODEOWNERS` | x | y | R1 |\n')
            repo.commit('head')
            v = repo.verdict()
            self.assertIn('T1', [t.name for t in v.advisory],
                          'CODEOWNERS 경로를 건드렸는데 T1 을 보고조차 안 했다')
            self.assertNotIn('T1', [t.name for t in v.blocking],
                             'T1 이 막았다 — 오늘 그것은 강제되지 않는다. '
                             '막으면 「보장처럼 생긴 것」이 하나 더 생긴다')

    def test_the_owned_set_comes_from_codeowners_not_from_this_file(self) -> None:
        """T1 경로는 «파생»이다. 판정기 안에 경로 리터럴이 있으면 갈라진다."""
        source = (ROOT / 'scripts' / 'human_judgment_triggers.py').read_text(encoding='utf-8')
        body = source.split('"""', 2)[-1]          # 모듈 docstring 밖만 본다
        for literal in ('migrations/', 'infra/', 'githooks/', 'rbac.py'):
            self.assertNotIn(f"'{literal}", body,
                             f'판정기가 T1 경로 {literal!r} 를 하드코딩했다 — '
                             'CODEOWNERS 와 갈라진다')


class TestTheJudgeRefusesRatherThanPassesWhenItCannotSee(unittest.TestCase):
    """판정기가 돌지 못한 것과 통과한 것은 달라야 한다."""

    def test_a_missing_codeowners_is_announced(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            repo = _make(Path(td))
            repo.write('src/a.py', 'x = 1\n')
            repo.commit('head')
            self.assertEqual([], judge.read_codeowners_paths(repo.root),
                             'CODEOWNERS 가 없는데 경로를 만들어 냈다')

    def test_a_bad_ref_returns_two_not_zero(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            repo = _make(Path(td))
            repo.write('src/a.py', 'x = 1\n')
            repo.commit('head')
            rc = judge.main(['--root', str(repo.root), '--base', 'no-such-ref'])
            self.assertEqual(2, rc,
                             '판정기가 돌지 못했는데 통과(0)나 위반(1)으로 답했다')


class TestCodeownersPatternsDoNotLeakAcrossSlashes(unittest.TestCase):
    """`fnmatch` 를 쓰면 `*` 가 `/` 를 넘어 T1 이 조용히 넓어진다."""

    def test_a_single_star_does_not_cross_a_slash(self) -> None:
        self.assertTrue(judge._matches_codeowner_pattern('intent/a/spec.md',
                                                         '/intent/*/spec.md'))
        self.assertFalse(judge._matches_codeowner_pattern('intent/a/b/spec.md',
                                                          '/intent/*/spec.md'),
                         '`*` 가 `/` 를 넘었다 — T1 이 선언보다 넓어진다')

    def test_a_pattern_shorter_than_the_path_does_not_match(self) -> None:
        """옛 코드는 zip 이 «조용히 잘라» 이 축을 마지막 줄에서만 답했다.

        `strict=True` 로 바꾸면서 길이 검사를 앞으로 올렸다. 동치인지를 여기서
        단언한다 — 아니면 「린터를 달래려고 동작을 바꿨다」가 된다.
        """
        self.assertFalse(judge._matches_codeowner_pattern('a/b/c.md', 'a/b'))
        self.assertFalse(judge._matches_codeowner_pattern('a/b', 'a/b/c.md'))
        self.assertTrue(judge._matches_codeowner_pattern('a/b', 'a/b'))

    def test_a_directory_pattern_covers_its_subtree(self) -> None:
        self.assertTrue(judge._matches_codeowner_pattern('migrations/001.sql',
                                                         '/migrations/'))
        self.assertFalse(judge._matches_codeowner_pattern('migrations_old/001.sql',
                                                          '/migrations/'),
                         '접두사만 같은 다른 디렉터리를 삼켰다')


if __name__ == '__main__':
    unittest.main()
