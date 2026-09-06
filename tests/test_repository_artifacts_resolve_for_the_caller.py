"""저장소 산출물은 «다루는 곳» 기준으로 해소돼야 한다 (2026-09-06).

⚠️ **이 봉인이 존재하는 이유는 「어떤 게이트도 그것을 못 잡았다」이다.** 다섯 모듈이
``resolve_repo_artifact(__file__, …)`` 로 산출물을 찾았고, 그 자리는

  · import 되고            (진입점 봉인 통과)
  · `--help` 가 돌고        (껍데기 봉인 통과)
  · 이 레인 안에서는 정답이며 (`__file__` 의 조상이 곧 이 저장소이므로)
  · **설치된 소비 레인에서만** `/docs/platform/...` 로 떨어진다.

즉 이 레인의 전량 시험은 이 결함 위에서 «전부 초록»이었다. 그것이 이 파일이 재는 것을
`__file__` 이 아니라 **cwd** 로 갈라 놓은 이유다 — 소비 조건을 여기서 재현한다.
"""
from __future__ import annotations

import ast
import os
import pathlib
import tempfile
import unittest

from fcc_test_platform.repository_anchor import repository_anchor

_PACKAGE = pathlib.Path(__file__).resolve().parents[1] / 'fcc_test_platform'


class TestNoModuleAnchorsRepositoryArtifactsOnItself(unittest.TestCase):
    """``resolve_repo_artifact(__file__, …)`` 는 설치되면 자기 site-packages 를 가리킨다."""

    def test_no_call_passes_dunder_file_directly(self):
        offenders: list[str] = []
        scanned = 0
        for path in sorted(_PACKAGE.rglob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', '')
                if name != 'resolve_repo_artifact' or not node.args:
                    continue
                scanned += 1
                first = node.args[0]
                if isinstance(first, ast.Name) and first.id == '__file__':
                    offenders.append(f'{path.name}:{node.lineno}')
        self.assertGreater(
            scanned, 0,
            '이 저장소에 `resolve_repo_artifact` 호출이 하나도 없다 — 그렇다면 이 검사는 '
            '공집합을 훑고 «참이지만 아무것도 재지 않는 참»이 된다. 함수 이름이 바뀌었다면 '
            '이 검사도 새 이름을 가리키게 고쳐라.',
        )
        self.assertEqual(
            [], offenders,
            '저장소 산출물을 `__file__` 기준으로 찾는다 — 설치되면 site-packages 에서 걸어 '
            f'`/docs/...` 로 떨어진다. `repository_anchor(__file__)` 을 써라: {offenders}',
        )


class TestTheAnchorFollowsTheCallerNotTheModule(unittest.TestCase):
    """헬퍼 자체의 이빨 — 「다루는 곳」이 바뀌면 답도 바뀌어야 한다."""

    def test_a_tree_with_a_pyproject_becomes_the_anchor(self):
        with tempfile.TemporaryDirectory() as raw:
            target = pathlib.Path(raw) / 'somewhere-else'
            target.mkdir()
            (target / 'pyproject.toml').write_text('', encoding='utf-8')
            previous = os.getcwd()
            try:
                os.chdir(target)
                self.assertEqual(repository_anchor(__file__), target / 'pyproject.toml')
            finally:
                os.chdir(previous)

    def test_outside_any_tree_it_falls_back_to_the_module(self):
        """저장소 밖이면 옛 동작으로 돌아간다 — 이 변경은 «틀렸던 자리만» 움직인다."""
        with tempfile.TemporaryDirectory() as raw:
            bare = pathlib.Path(raw).resolve()
            if any((c / 'pyproject.toml').is_file() for c in (bare, *bare.parents)):
                self.skipTest('임시 디렉터리의 조상이 pyproject.toml 을 갖는다 — 이 축을 못 잰다')
            previous = os.getcwd()
            try:
                os.chdir(bare)
                self.assertEqual(repository_anchor(__file__), pathlib.Path(__file__))
            finally:
                os.chdir(previous)


if __name__ == '__main__':
    unittest.main()
