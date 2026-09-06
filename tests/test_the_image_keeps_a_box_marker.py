"""컨테이너 이미지가 **상자 표식을 하나라도 들고 있는가** (2026-09-06).

## Why — 실제로 죽은 자리

`central-migrate` 잡이 이미지 안에서 이렇게 죽었다:

    {"ok": false,
     "error": "마이그레이션 디렉터리가 없다: /docs/platform/migrations
               이것은 «적용할 것이 없다» 가 아니라 «찾지 못했다» 다."}

두 사실이 만나서 생긴 결함이다:

1. `infra/central/Dockerfile.api` 는 설치 뒤 소스 표식을 **의도적으로 지운다** —
   `rm -rf … /app/pyproject.toml /app/README.md …`. 남기면 `/app` 이 cwd 라
   휠보다 먼저 import 되어 **휠을 조용히 가린다.** 그 삭제는 옳다.
2. `fcc_test_platform.repository_anchor` 의 첫 판은 상자 표식으로
   `pyproject.toml` **하나만** 인정했다.

그래서 앵커가 `site-packages` 로 되돌아갔고, 조상 걷기가 최외곽까지 올라가
`/docs/platform/migrations` 를 답했다.

⚠️ **이미지는 표식을 가지고 있었다.** 같은 Dockerfile 이 바로 그 자리를 메우려고
`.extraction-layout.json` 을 싣고, 주석이 그 이유를 적는다 — *"상자 표식을 함께
싣는다 … 그래서 부르는 쪽이 경로를 몰라도 된다."* 빠진 것은 표식이 아니라
**표식을 세는 쪽의 목록**이었다.

## What — 왜 파이썬 축이 이것을 못 봤나

`tests/test_dockerfile_copy_paths.py` 가 이미 이름 붙인 형태다: import 폐포·설치된
배포판·pytest 셋 다 *"이미지 안에서 무엇이 살아남는가"* 라는 축을 갖지 않는다.
체크아웃에는 `pyproject.toml` 이 있으므로 **모든 로컬 검사가 초록**이고, 결함은
`docker compose up` 에서만 나타난다.

그 파일은 「COPY 원본이 실재하는가」를 본다. 이 파일은 **그 다음 질문**을 본다:
「복사된 것 중 무엇이 `rm` 뒤에도 살아남는가, 그리고 그중에 상자 표식이 있는가.」

## 한계

`rm -rf` 를 정규식으로 읽는다. 셸의 전개(변수·글롭·`find -delete`)는 보지 못하므로
이 검사가 세는 삭제 집합은 **하한**이다. 그래도 오늘의 결함은 잡는다 — 그 삭제는
경로가 리터럴로 적혀 있다. ⚠️ 삭제가 리터럴이 아닌 형태로 바뀌면 이 검사는 조용히
약해진다. `test_the_delete_list_is_still_literal` 이 그 날을 잡는다.
"""
from __future__ import annotations

from pathlib import Path
import re
import tempfile
import os
import unittest

from fcc_test_platform.repository_anchor import BOX_MARKERS, repository_anchor

_REPO_ROOT = Path(__file__).resolve().parents[1]
_API_DOCKERFILE = _REPO_ROOT / 'infra' / 'central' / 'Dockerfile.api'

#: `COPY <src>… <dst>` — 마지막 인자가 목적지.
_COPY_RE = re.compile(r'^\s*COPY\s+(?P<rest>.+?)\s*$', re.IGNORECASE)
#: `rm -rf /app/foo /app/bar \` — 이어지는 줄까지 한 덩어리로 읽는다.
_RM_RE = re.compile(r'\brm\s+-rf?\b(?P<targets>[^&|;\n]*)')


def _logical_lines(text: str) -> list[str]:
    """행 이어쓰기(`\\`)를 편다. 삭제 목록이 여러 줄에 걸쳐 있다."""
    out, buf = [], ''
    for raw in text.splitlines():
        stripped = raw.rstrip()
        if stripped.endswith('\\'):
            buf += stripped[:-1] + ' '
            continue
        out.append(buf + stripped)
        buf = ''
    if buf:
        out.append(buf)
    return out


def _copied_root_files(dockerfile: Path) -> set[str]:
    """`COPY … ./` 로 **이미지 루트에** 놓이는 파일 이름들."""
    names: set[str] = set()
    for line in _logical_lines(dockerfile.read_text(encoding='utf-8')):
        m = _COPY_RE.match(line)
        if not m:
            continue
        parts = [p for p in m.group('rest').split() if not p.startswith('--')]
        if len(parts) < 2:
            continue
        sources, dest = parts[:-1], parts[-1]
        if dest not in ('./', '.', '/app/', '/app'):
            continue
        for src in sources:
            if not src.endswith('/'):
                names.add(Path(src).name)
    return names


def _deleted_root_files(dockerfile: Path) -> set[str]:
    """`rm -rf` 가 **이미지 루트에서** 지우는 파일 이름들."""
    names: set[str] = set()
    for line in _logical_lines(dockerfile.read_text(encoding='utf-8')):
        for m in _RM_RE.finditer(line):
            for target in m.group('targets').split():
                if target.startswith('-'):
                    continue
                p = target.rstrip('/')
                if p.startswith('/app/') and '*' not in p:
                    rest = p[len('/app/'):]
                    if '/' not in rest:
                        names.add(rest)
    return names


class TestTheParserSeesThisDockerfile(unittest.TestCase):
    """읽지 못한 것을 초록으로 세지 않는다."""

    def test_the_dockerfile_is_present(self) -> None:
        self.assertTrue(_API_DOCKERFILE.is_file(), f'{_API_DOCKERFILE} 가 없다')

    def test_copied_and_deleted_sets_are_both_non_empty(self) -> None:
        self.assertTrue(_copied_root_files(_API_DOCKERFILE), 'COPY … ./ 를 하나도 못 읽었다')
        self.assertTrue(_deleted_root_files(_API_DOCKERFILE), 'rm -rf /app/… 를 하나도 못 읽었다')

    def test_the_delete_list_is_still_literal(self) -> None:
        """삭제가 리터럴 경로여야 이 검사가 세는 집합이 뜻을 갖는다."""
        deleted = _deleted_root_files(_API_DOCKERFILE)
        self.assertIn(
            'pyproject.toml', deleted,
            'Dockerfile 이 pyproject.toml 을 리터럴로 지우지 않는다. 형태가 바뀌었다면 '
            '이 검사의 삭제 집합은 더 이상 하한이 아니라 «빈 것»일 수 있다 — 파서를 넓혀라.',
        )


class TestTheImageCanStillFindItsBox(unittest.TestCase):
    """⚠️ 이 검사의 핵심."""

    def test_at_least_one_box_marker_survives_in_the_image(self) -> None:
        copied = _copied_root_files(_API_DOCKERFILE)
        deleted = _deleted_root_files(_API_DOCKERFILE)
        surviving = copied - deleted
        kept_markers = surviving & set(BOX_MARKERS)
        self.assertTrue(
            kept_markers,
            '이미지 루트에 상자 표식이 하나도 남지 않는다.\n'
            f'  COPY 로 놓인 것: {sorted(copied)}\n'
            f'  rm 으로 지운 것: {sorted(deleted)}\n'
            f'  살아남은 것:     {sorted(surviving)}\n'
            f'  인정하는 표식:   {sorted(BOX_MARKERS)}\n'
            '표식이 없으면 repository_anchor 가 site-packages 로 되돌아가고, '
            '조상 걷기가 최외곽까지 올라가 /docs/platform/migrations 를 답한다. '
            'central-migrate 가 그 자리에서 죽는다(2026-09-06 실측).',
        )


class TestTheAnchorAcceptsABoxWithoutPyproject(unittest.TestCase):
    """행동 축 — 이미지의 배치를 흉내 내어 직접 물어본다."""

    def test_a_tree_with_only_the_layout_record_answers_as_the_box(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            box = Path(tmp).resolve() / 'app'
            (box / 'migrations').mkdir(parents=True)
            (box / '.extraction-layout.json').write_text('{"paths": {}}', encoding='utf-8')
            cwd = os.getcwd()
            try:
                os.chdir(box)
                anchor = repository_anchor('/usr/lib/python3/site-packages/pkg/mod.py')
            finally:
                os.chdir(cwd)
            self.assertEqual(
                anchor.parent, box,
                f'상자 표식만 있는 트리를 상자로 보지 못했다 — 앵커가 {anchor} 로 갔다. '
                '이것이 컨테이너 이미지의 배치다(pyproject.toml 은 설치 뒤 지워진다).',
            )

    def test_a_checkout_still_answers_through_pyproject(self) -> None:
        """이 변경은 «틀렸던 자리만» 움직여야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve() / 'repo'
            repo.mkdir(parents=True)
            (repo / 'pyproject.toml').write_text('[project]\n', encoding='utf-8')
            cwd = os.getcwd()
            try:
                os.chdir(repo)
                anchor = repository_anchor('/usr/lib/python3/site-packages/pkg/mod.py')
            finally:
                os.chdir(cwd)
            self.assertEqual(anchor, repo / 'pyproject.toml')

    def test_the_nearest_tree_wins_over_a_farther_one(self) -> None:
        """⚠️ 표식을 바깥 고리로 돌리면 먼 조상의 pyproject 가 가까운 상자를 이긴다."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = Path(tmp).resolve() / 'outer'
            inner = outer / 'box'
            inner.mkdir(parents=True)
            (outer / 'pyproject.toml').write_text('[project]\n', encoding='utf-8')
            (inner / '.extraction-layout.json').write_text('{"paths": {}}', encoding='utf-8')
            cwd = os.getcwd()
            try:
                os.chdir(inner)
                anchor = repository_anchor('/usr/lib/python3/site-packages/pkg/mod.py')
            finally:
                os.chdir(cwd)
            self.assertEqual(
                anchor.parent, inner,
                '가까운 상자가 아니라 먼 조상이 이겼다 — 후보 디렉터리가 «바깥» 고리여야 한다',
            )

    def test_outside_any_tree_it_still_falls_back_to_the_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare = Path(tmp).resolve() / 'bare'
            bare.mkdir(parents=True)
            cwd = os.getcwd()
            try:
                os.chdir(bare)
                anchor = repository_anchor('/nowhere/pkg/mod.py')
            finally:
                os.chdir(cwd)
            self.assertEqual(anchor, Path('/nowhere/pkg/mod.py'))


class TestTheCheckWouldSeeTheGap(unittest.TestCase):
    """봉인이 오늘 초록인 것과 이빨이 있는 것은 다른 명제다."""

    def test_a_marker_set_without_the_layout_record_would_fail(self) -> None:
        copied = _copied_root_files(_API_DOCKERFILE)
        deleted = _deleted_root_files(_API_DOCKERFILE)
        surviving = copied - deleted
        self.assertFalse(
            surviving & {'pyproject.toml'},
            '이 검사의 전제가 깨졌다: pyproject.toml 이 이미지에 살아남는다면 '
            '이 봉인은 결함이 있던 판에서도 초록이었을 것이다',
        )


if __name__ == '__main__':
    unittest.main()
