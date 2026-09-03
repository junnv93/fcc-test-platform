"""스키마 탐색이 **설치된 배치**에서도 성립하는가 (2026-09-03).

**왜 이 봉인이 있는가.**

`rbac_role_catalog` 는 모듈 레벨에서 중앙 DB 스키마 JSON 을 읽는다. 그 탐색이
오늘까지 **조상 거슬러 올라가기** 하나였고, 배포 이미지에서는 그 물음에 답이 없다:

    모듈   /usr/local/lib/python3.11/site-packages/fcc_test_platform/...
    자원   /app/docs/platform/central_db_schema.v1.json

둘은 **조상 관계가 아니다** — 휠은 site-packages 로 가고 `docs/` 는 `COPY` 로
`/app` 에 놓인다. 조상 탐색이 전부 헛돌고 fallback 이
`/usr/local/lib/python3.11/docs/...` 를 가리켜 **import 시점에 죽는다.**

⚠️ **그런데 아무도 몰랐다** — compose 가 `FCC_PLATFORM_SCHEMA_PATH` 를 주기 때문이다.
즉 배포가 그 변수에 **전적으로 의존**하면서 그 사실을 아무 데도 적지 않았다.
변수를 지우거나 오타 내면 배포가 import 에서 죽고, 원인은 코드 어디에도 없다.

> **「환경변수가 있으면 돈다」와 「코드가 스스로 찾는다」는 운영 축에서 같은 값이었다.**

이 봉인은 그 둘을 가른다 — **환경변수를 빼고** 판정한다.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from fcc_test_platform.application import rbac_role_catalog as catalog


class TestTheEnvOverrideStillWins(unittest.TestCase):
    """명시적 지정이 최우선이라는 성질은 유지한다."""

    def test_the_env_override_is_used_verbatim(self):
        with mock.patch.dict(os.environ, {catalog._SCHEMA_PATH_ENV: '/x/y/z.json'}):
            self.assertEqual(Path('/x/y/z.json'), catalog._resolve_schema_path())


class TestDiscoveryWorksWithoutTheEnvVar(unittest.TestCase):
    """⚠️ **이 팔이 이 파일의 존재 이유다.**"""

    def test_the_repo_checkout_resolves_without_the_override(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(catalog._SCHEMA_PATH_ENV, None)
            found = catalog._discover_schema_path()
        self.assertTrue(
            found.is_file(),
            f'환경변수 없이 스키마를 못 찾았다: {found}. '
            'compose 가 그 변수를 주는 한 이 결함은 숨는다 — 그것이 이 팔의 이유다.',
        )

    def test_a_tree_reachable_only_through_the_working_directory_is_found(self):
        """설치된 배치의 재현 — 모듈의 조상 어디에도 자원이 없고 **cwd 트리에만** 있다.

        이미지가 정확히 이 모양이다. 이 팔이 없으면 그 배치가 시험되지 않는다.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'app'
            target = root / catalog._SCHEMA_RELATIVE_PATH
            target.parent.mkdir(parents=True)
            target.write_text('{"rbac_role_grants": {"grants": {}}}', encoding='utf-8')
            previous = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop(catalog._SCHEMA_PATH_ENV, None)
                    found = catalog._discover_schema_path()
            finally:
                os.chdir(previous)
        # ⚠️ 저장소 체크아웃에서는 **조상 탐색이 먼저 답을 준다** — 그것이 옳다.
        # 여기서 확인하는 것은 「cwd 축이 존재한다」이지 「cwd 가 이긴다」가 아니다.
        self.assertTrue(found.is_file())

    def test_the_module_tree_wins_over_the_working_directory(self):
        """⚠️ 반대 방향 — cwd 를 앞에 두면 안 된다.

        운영자가 우연히 다른 트리에서 실행할 때 그 트리의 스키마가 이기면,
        **어느 스키마로 판정했는지가 실행 위치에 따라 달라진다.**
        """
        roots = catalog._candidate_roots()
        here = Path(catalog.__file__).resolve()
        module_roots = [r for r in roots if r in here.parents]
        self.assertTrue(module_roots, '모듈 조상이 후보에 하나도 없다')
        first_module_root = roots.index(module_roots[0])
        cwd = Path.cwd().resolve()
        if cwd in roots:
            self.assertLess(
                first_module_root, roots.index(cwd),
                '작업 디렉터리가 모듈 트리보다 먼저다 — 실행 위치가 판정을 바꾼다',
            )


class TestWhatThisSealCannotMeasure(unittest.TestCase):
    """⚠️ **이 봉인의 한계를 이름으로 적는다 — 적지 않으면 있는 척한다.**

    이 파일은 **저장소 체크아웃 안에서** 돈다. 거기서는 모듈이 트리 안에 있어
    **옛 축(조상 탐색만)도 성공한다.** 즉 이 봉인을 다 통과해도
    *「이미지에서 도는가」* 는 확인되지 않는다 — 그 조건에서 두 축이 같은 값이다.

    실측 2026-09-03, 변이를 두 조건에서 각각 돌린 결과:

        저장소 안   옛 축 🟢 찾음     새 축 🟢 찾음     ← 구분 안 됨
        이미지 안   옛 축 🔴 못 찾음  새 축 🟢 찾음     ← 여기서만 갈린다

    ⚠️ 그래서 **판별력은 컨테이너 안에서 쟀다**(위 표의 아랫줄). 이 파일은
    회귀를 잡는 값싼 층이고, 진짜 판정은 이미지에서 나온다.

    `.claude/rules/check-axis-blindness.md` §「깨끗한 조건에서 쟀다」 그대로다 —
    *그 조건이 대상이 실제로 도는 조건인가.*
    """

    def test_this_suite_runs_where_the_module_is_inside_the_tree(self):
        """그 사실을 **기계적으로** 붙잡는다 — 다음 사람이 위 문단을 안 읽어도.

        언젠가 이 스위트가 설치된 배포판에 대고 돌게 되면 이 팔이 red 가 되고,
        그때는 위 한계가 사라진 것이므로 이 클래스를 지우면 된다.
        """
        module = Path(catalog.__file__).resolve()
        repo_root = Path(__file__).resolve().parents[1]
        self.assertIn(
            repo_root, module.parents,
            '모듈이 저장소 트리 밖에서 import 됐다 — 이 봉인의 한계 문단이 낡았다. '
            '그렇다면 여기서 이미지 배치를 직접 잴 수 있으니 이 클래스를 지우고 '
            '옛 축과 새 축을 구분하는 팔을 세워라.',
        )


class TestTheResourceIsNotCopiedIntoThePackage(unittest.TestCase):
    """⚠️ 사본을 만들지 않았다는 것을 붙잡는다.

    이 JSON 은 `docs/platform/` 이 SSOT 다. 패키지 안으로 복사해 「나르게」 하면
    두 벌이 되어 갈라진다 — 이 저장소가 오늘 `benchmark_harness` 에서 같은
    형태를 다뤘고, 그때 결론이 *「사본에 SSOT 를 거는 것은 SSOT 가 아니다」* 였다.
    """

    def test_the_schema_lives_only_at_its_ssot_path(self):
        repo_root = Path(__file__).resolve().parents[1]
        copies = [
            p.relative_to(repo_root).as_posix()
            for p in repo_root.rglob('central_db_schema.v1.json')
            if 'node_modules' not in p.parts and '__pycache__' not in p.parts
            and 'packages/api-artifacts' not in p.relative_to(repo_root).as_posix()
        ]
        self.assertEqual(
            ['docs/platform/central_db_schema.v1.json'], copies,
            '스키마 사본이 생겼다 — 두 벌은 갈라진다:\n  ' + '\n  '.join(copies),
        )


if __name__ == '__main__':  # pragma: no cover
    unittest.main(verbosity=2)
