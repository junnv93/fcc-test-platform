"""`githooks/pre-push` 가 «ref 삭제»와 «트리를 미는 push»를 구별하는가.

■ 왜 이 시험이 있는가

2026-09-07 이전 이 훅은 stdin 을 전혀 읽지 않아 둘을 구별하지 못했다. 삭제에는
검사할 트리가 없는데 전량 시험이 돌았고, 인터프리터에 pytest 가 없으면
「pytest 가 시작조차 못했다」로 막혔다 — **부재와 회귀가 같은 빨강**이었다.

그리고 더 나쁜 것: 정당한 경로(머지 뒤 브랜치 정리)에 `FCC_SKIP_LANE_CHECK=1`
을 강제하면 그 습관이 **트리를 미는 push 로 옮겨간다.**

■ ⚠️ 다섯째 주입이 이 파일의 요점이다

이 수리의 유일한 «조용한» 실패 경로는 `while` 을 파이프로 바꾸는 것이다.
그러면 `while` 이 서브셸에서 돌아 `_saw_a_ref` 가 소실되고, 삭제 건너뛰기가
멈추면서 게이트가 다시 삭제 push 를 막는다. **fail-safe 방향이라 아무도
눈치채지 못한다.** 그 형태를 여기서 주입해 확인한다.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / 'githooks' / 'pre-push'
ZERO = '0' * 40
_SKIPPED = 'ref 삭제 push 다'


def _run(stdin: str, *, hook: Path | None = None) -> tuple[int, str]:
    """훅을 돌리고 (종료코드, stderr+stdout) 를 돌려준다.

    ⚠️ `FCC_SKIP_LANE_CHECK=1` 을 준다 — 이 시험이 재는 것은 «축 0 의 판정»이지
       레인 검사가 아니다. 두 축이 함께 움직이면 어느 것이 답했는지 모른다.
    """
    proc = subprocess.run(
        ['bash', str(hook or HOOK), 'origin', 'https://example.invalid/x'],
        input=stdin, capture_output=True, text=True,
        env={'PATH': '/usr/bin:/bin', 'FCC_SKIP_LANE_CHECK': '1', 'HOME': '/tmp'},
        cwd=str(ROOT))
    return proc.returncode, proc.stdout + proc.stderr


class TestTheDeletionAxis(unittest.TestCase):

    def test_a_pure_deletion_is_skipped_and_says_so(self) -> None:
        """주입 ① — 삭제만 있으면 건너뛴다. 그리고 «조용히» 건너뛰지 않는다."""
        rc, out = _run(f'(delete) {ZERO} refs/heads/x {ZERO}\n')
        self.assertEqual(0, rc, out)
        self.assertIn(_SKIPPED, out,
                      '삭제를 건너뛰면서 아무 말도 하지 않았다 — '
                      '조용한 통과는 초록과 같은 모양이다')

    def test_a_normal_push_is_not_skipped(self) -> None:
        """주입 ② — 트리를 미는 push 는 축 0 을 통과해 축 2 로 내려간다."""
        rc, out = _run(f'refs/heads/x abc123def456 refs/heads/x {ZERO}\n')
        self.assertNotIn(_SKIPPED, out,
                         '일반 push 를 삭제로 읽었다 — 게이트가 통째로 사라진다')

    def test_a_deletion_mixed_with_a_real_push_is_not_skipped(self) -> None:
        """주입 ③ — 삭제 한 줄을 끼워 넣는 우회가 막혀야 한다."""
        rc, out = _run(f'(delete) {ZERO} refs/heads/a {ZERO}\n'
                       f'refs/heads/b abc123 refs/heads/b {ZERO}\n')
        self.assertNotIn(_SKIPPED, out,
                         '삭제 한 줄로 게이트를 껐다')

    def test_an_empty_stdin_is_not_treated_as_a_deletion(self) -> None:
        """주입 ④ — ref 를 하나도 «못 봤으면» 건너뛰지 않는다.

        훅을 손으로 부르는 경우가 그것이고, 건너뛰면 그 실패 모양이 초록이라
        자기 자신을 드러내지 못한다.
        """
        rc, out = _run('')
        self.assertNotIn(_SKIPPED, out,
                         '빈 stdin 을 삭제로 읽었다 — 손으로 부른 모든 push 가 통과한다')


class TestTheOnlySilentFailurePathIsSealed(unittest.TestCase):
    """⚠️ 주입 ⑤ — 파이프로 바꾸면 이 수리가 «조용히» 무효가 된다."""

    def test_a_pipe_instead_of_a_here_string_breaks_the_skip(self) -> None:
        import tempfile
        source = HOOK.read_text(encoding='utf-8')
        self.assertIn('done <<EOF', source,
                      'here-string(또는 here-doc)이 사라졌다 — '
                      '파이프로 바뀌면 서브셸이 변수를 먹는다')

        broken = source.replace(
            'while read -r _lref _lsha _rref _rsha; do',
            'echo "$_refs" | while read -r _lref _lsha _rref _rsha; do'
        ).replace('done <<EOF\n$_refs\nEOF\n', 'done\n')
        self.assertNotEqual(source, broken, '주입이 착지하지 않았다')

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'pre-push-piped'
            p.write_text(broken, encoding='utf-8')
            rc, out = _run(f'(delete) {ZERO} refs/heads/x {ZERO}\n', hook=p)

        self.assertNotIn(
            _SKIPPED, out,
            '파이프 형태인데도 삭제를 건너뛰었다 — 이 시험이 재는 축이 '
            '실재하지 않거나 주입이 대상을 못 맞혔다. 어느 쪽이든 이 봉인은 공허하다')


class TestChildrenDoNotInheritTheConsumedStdin(unittest.TestCase):
    """자식이 stdin 을 읽으면 ref 줄이 사라진다 — 부류 전체에 면역을 준다."""

    def test_every_child_invocation_redirects_stdin(self) -> None:
        source = HOOK.read_text(encoding='utf-8')
        for needle in ('merge_readiness_guard.py" advise </dev/null',
                       'lane_check.py" --root "$root" </dev/null'):
            self.assertIn(needle, source,
                          f'자식 호출에 `</dev/null` 이 없다: {needle!r} — '
                          '그 스크립트가 언젠가 stdin 을 읽으면 삭제 판정이 조용히 깨진다')


if __name__ == '__main__':
    unittest.main()
