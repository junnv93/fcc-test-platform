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
import contextlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from fcc_test_platform.repository_anchor import BOX_MARKERS, repository_anchor

def _declared_package_files() -> list[pathlib.Path]:
    """선언된 패키지 아래의 모든 파이썬 파일 — 루트마다 «비지 않았음»을 함께 요구한다."""
    files: list[pathlib.Path] = []
    for root in _package_roots():
        found = sorted(root.rglob('*.py'))
        if not found:
            raise AssertionError(
                f'선언된 패키지 {root.name} 아래에 파이썬 파일이 없다 — 루트 하나가 통째로 '
                '비어도 «합계»만 보는 검사는 초록으로 지나간다(형제 세션 -91 실측: '
                '로컬 59 + platform 28 = 108 > 100 이라 커널 루트가 없어도 통과했다). '
                '루트별로 묻는다.'
            )
        files.extend(found)
    return files


def _package_roots() -> list[pathlib.Path]:
    """이 배포판이 «싣는다고 선언한» 최상위 패키지들.

    ⚠️ **루트를 적어 두지 않는다.** 형제 세션(-91)이 같은 날 같은 형태를 잡았다 —
    Port 인구조사가 「어떤 Port 가 있나」는 디렉터리 순회로 파생하면서 **「Port 가 어디
    사나」는 상수로 적어 두었고**, 그 디렉터리가 이사한 날 조사가 통째로 조용해졌다.
    같은 결함의 두 축을 한쪽만 고쳐 둔 자리다.

    여기서는 답이 이미 `[tool.setuptools.packages.find].include` 에 «선언»돼 있다 —
    휠이 무엇을 싣는지 정하는 바로 그 값이다. 그것을 읽으므로, 실리는 이름이 늘거나
    줄면 이 검사의 시야도 함께 움직인다.
    """
    import tomllib

    root = pathlib.Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
    patterns = config['tool']['setuptools']['packages']['find']['include']
    # ⚠️ `__init__.py` 를 요구하지 **않는다.** 이 저장소의 `fcc_test_platform/` 에는
    #    그 파일이 없다(네임스페이스 패키지). 「패키지 = __init__.py 가 있는 것」은
    #    가정이고, 그 가정이 여기서 거짓이라 첫 판은 루트를 0개로 셌다 — 루트를 적어
    #    두는 것을 고치면서 그 «판정 방법»을 또 가정한 것이다. 실리는 것은 setuptools 가
    #    정하므로 디렉터리인지만 묻는다.
    roots = [
        candidate
        for pattern in patterns
        for candidate in sorted(root.glob(pattern.rstrip('*')))
        if candidate.is_dir()
    ]
    if not roots:
        raise AssertionError(
            f'`packages.find.include` = {patterns!r} 가 이 트리에서 아무 패키지도 '
            '가리키지 않는다 — 이 검사가 공집합을 훑게 된다.'
        )
    return roots


class TestNoModuleAnchorsRepositoryArtifactsOnItself(unittest.TestCase):
    """``resolve_repo_artifact(__file__, …)`` 는 설치되면 자기 site-packages 를 가리킨다."""

    def test_no_call_passes_dunder_file_directly(self):
        offenders: list[str] = []
        scanned = 0
        for path in _declared_package_files():
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
            if _holds_a_box_marker(bare):
                self.skipTest(f'임시 디렉터리의 조상이 {BOX_MARKERS} 중 하나를 갖는다 — 이 축을 못 잰다')
            previous = os.getcwd()
            try:
                os.chdir(bare)
                self.assertEqual(repository_anchor(__file__), pathlib.Path(__file__))
            finally:
                os.chdir(previous)


# ─────────────────────────────────────────────────────────────────────────────
# 저장소 «밖» 축 — 위의 검사들이 구조적으로 못 보는 자리
# ─────────────────────────────────────────────────────────────────────────────

_PROBE = r"""
import importlib, json, os, pkgutil, sys
import fcc_test_platform
root = [p for p in fcc_test_platform.__path__ if os.path.isdir(p)][0]
out = {}
for info in sorted(pkgutil.iter_modules([root]), key=lambda i: i.name):
    name = 'fcc_test_platform.' + info.name
    try:
        importlib.import_module(name)
        out[info.name] = 'ok'
    except RuntimeError:
        out[info.name] = 'refused'
    except BaseException as exc:      # 설치 축(ImportError 등) — 이 봉인의 축이 아니다
        out[info.name] = 'other:' + type(exc).__name__
print(json.dumps(out))
"""


def _probe(cwd: pathlib.Path) -> dict[str, str]:
    """별도 프로세스에서 최상위 모듈을 전부 import 하고 결과를 이름별로 돌려준다.

    ⚠️ 서브프로세스여야 한다 — 모듈 «수준» 에서 죽는 것을 재는데, 같은 프로세스에서는
    앞선 import 가 ``sys.modules`` 에 남아 두 번째 측정이 첫 번째의 답을 되돌려 준다.
    """
    result = subprocess.run(
        [sys.executable, '-c', _PROBE], cwd=cwd, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f'probe 자체가 실패했다 (cwd={cwd}):\n{result.stderr[-2000:]}')
    return json.loads(result.stdout)


def _holds_a_box_marker(where: pathlib.Path) -> bool:
    """``where`` 나 그 조상이 상자 표식을 갖는가 — **목록은 선언에서 파생한다.**

    ⚠️ 하드코딩하면 표식이 늘어난 날 이 검사가 **조용히 틀린다.** 실측 2026-09-06:
    `repository_anchor` 의 첫 판은 ``pyproject.toml`` 하나만 인정했는데, 컨테이너
    이미지가 그 파일을 의도적으로 지우는 바람에 마이그레이션 러너가 죽었고
    ``.extraction-layout.json`` 이 두 번째 표식으로 추가됐다(main `f7f01d2`).
    그때 이 파일은 여전히 ``pyproject.toml`` 만 묻고 있었다 — 즉 「저장소 밖」이라고
    부른 자리가 실제로는 «상자 안»일 수 있었고, 그 오판은 red 가 아니라 **조용한
    거짓 측정**으로 나타난다. 세 번째 표식이 생겨도 여기는 안 고쳐도 되게 둔다.
    """
    return any(
        (candidate / marker).is_file()
        for candidate in (where, *where.parents)
        for marker in BOX_MARKERS
    )


def _bare_directory(stack: contextlib.ExitStack) -> pathlib.Path | None:
    """어떤 조상도 상자 표식을 갖지 않는 임시 디렉터리, 없으면 ``None``."""
    raw = stack.enter_context(tempfile.TemporaryDirectory())
    bare = pathlib.Path(raw).resolve()
    return None if _holds_a_box_marker(bare) else bare


class TestToolsThatRequireARepositoryRefuseLoudlyOutsideOne(unittest.TestCase):
    """이 패키지가 「저장소 밖」에 어떻게 답하는지를 **이름 집합으로** 고정한다.

    ⚠️ 위의 검사들은 pytest 가 저장소 «안»에서 돌기 때문에 이 축을 못 본다 —
    관측자가 관측 대상 안에 있다. 그래서 여기만 서브프로세스로 cwd 를 옮긴다.

    ⚠️ **개수가 아니라 이름 집합이다.** 하나가 조용해지고 하나가 새로 거부하면 개수는 같다.

    ⚠️ 그리고 이 봉인은 「거부하지 마라」가 아니라 **「거부가 조용히 바뀌지 마라」**를
    요구한다. ``_repository_root`` 의 거부는 옳다 — 틀린 뿌리 위에서 파일을 세면
    「대상이 없다」와 「경로가 맞다」가 구별되지 않는다. 문제는 그 정책이 이 패키지 안에
    **여러 벌** 있고(``repository_anchor`` 는 조용히 물러서고 ``_repository_root`` 사본
    넷은 거부한다) 어느 쪽이 도는지가 모듈마다 다르다는 것이다. 그 지도를 여기 고정한다.
    """

    #: 저장소 «안»에서는 import 되는데 «밖»에서는 RuntimeError 로 거부하는 모듈.
    #:
    #: 실측 2026-09-06, platform ``84f2955`` · **선언된 핀만으로 만든 venv**
    #: (``fcc-test-contracts@v0.1.22`` · ``fcc-test-kernel@kernel-v0.5.0``):
    #: 안 77/77 · 밖 69/77. 늘거나 줄면 이 줄을 사유와 함께 고쳐라.
    DECLARED_REFUSERS = frozenset({
        'bench_project_result_selection_cli',
        'central_db_live_proof_cli',
        'cross_session_result_selection_evidence_cli',
        'cutover_live_workflow_cli',
        'db_migration_collect_cli',
        'db_migration_runner_cli',
        'export_central_db_ddl_cli',
        'extraction_runner_cli',
    })

    #: ⚠️ 「안에서 import 되지 않아 이 축을 잴 수 없는」 모듈 — «이름으로» 선언한다.
    #:
    #: 선언대로 설치하면 **비어 있다**(77/77). 비었다고 검사를 빼지 마라 — 이 축은
    #: «설치 축»이고, 비어 있음 자체가 「이 환경이 선언대로다」라는 진술이다.
    #:
    #: ⚠️ 이 집합이 차면 red 인데, 그 red 의 뜻은 「트리가 깨졌다」가 아니라
    #: **「이 환경이 선언과 다르다」**일 수 있다. 실측 2026-09-06 — 소비 레인의 공용
    #: venv 에서 이 축을 재면 셋이 찬다(``central_db_live_proof_cli`` ·
    #: ``extraction_runner_cli`` · ``api_composition``). 그 venv 에는
    #: ``fcc-test-kernel 0.3.0`` · ``fcc-test-contracts 0.1.12`` 가 있었다. 그것을
    #: 「배포판이 자기 핀보다 앞선 형제를 요구한다」로 읽으면 틀린다 — 태그를 열어 보면
    #: 그 이름들이 **핀 안에 있다**. 실패 메시지가 이 갈림을 먼저 묻는 이유다.
    DECLARED_UNMEASURABLE: frozenset[str] = frozenset()

    @classmethod
    def setUpClass(cls):
        with contextlib.ExitStack() as stack:
            bare = _bare_directory(stack)
            if bare is None:
                raise unittest.SkipTest(
                    f'임시 디렉터리의 조상이 {BOX_MARKERS} 중 하나를 갖는다 — 이 축을 못 잰다'
                )
            cls.outside = _probe(bare)
        cls.inside = _probe(pathlib.Path(__file__).resolve().parents[1])

    def _refusers(self) -> set[str]:
        """«안»에서 import 되는 것만 본다 — 설치 축의 실패를 이 축으로 세지 않는다."""
        return {
            name for name, verdict in self.outside.items()
            if verdict == 'refused' and self.inside.get(name) == 'ok'
        }

    def test_the_set_of_modules_that_refuse_outside_a_repository_is_declared(self):
        observed = self._refusers()
        self.assertEqual(
            self.DECLARED_REFUSERS, observed,
            '저장소 밖에서 거부하는 모듈 집합이 선언과 다르다.\n'
            f'  새로 거부  : {sorted(observed - self.DECLARED_REFUSERS)}\n'
            f'  조용해짐   : {sorted(self.DECLARED_REFUSERS - observed)}\n'
            '⚠️ 「조용해짐」도 소식이다 — 큰 거부가 조용한 fallback 으로 바뀌면 그 모듈은 '
            '틀린 뿌리 위에서 파일을 세고 「대상이 없다」로 답한다.',
        )

    def test_no_module_newly_fails_to_import_inside_the_repository(self):
        """⚠️ 못 재는 것을 «이름으로» 고정한다 — 말뭉치가 줄어도 조용해지지 않게."""
        unmeasurable = {
            name: verdict for name, verdict in self.inside.items()
            if verdict.startswith('other:')
        }
        surprises = {k: v for k, v in unmeasurable.items() if k not in self.DECLARED_UNMEASURABLE}
        self.assertEqual(
            {}, surprises,
            '저장소 «안»에서 import 되지 않는 모듈이 선언에 없다 — 이 축을 못 재게 됐다.\n'
            f'  새로 못 잼 : {surprises}\n'
            '⚠️ 이것은 「초록」도 「빨강」도 아닌 «잴 수 없음» 이다. **먼저 설치 축을 물어라** — '
            '`pip list` 로 fcc-test-contracts/kernel 이 이 배포판의 선언과 같은지 본다. 다르면 '
            '트리가 아니라 환경이 원인이고, 선언대로 세운 venv 에서 다시 재라. 같은데도 죽으면 '
            '트리 축이니 고치고, 고칠 수 없는 사유라면 DECLARED_UNMEASURABLE 에 이름을 추가하라.',
        )

    #: ⚠️ 저장소 «밖»에서 **RuntimeError 가 아닌 것으로** 죽는 모듈 — 이름으로 선언한다.
    #:
    #: 위 두 집합만으로는 이 자리가 **조용하다**: 이 모듈은 «안»에서 import 되므로
    #: UNMEASURABLE 이 아니고, 밖에서 거부가 아니라 다른 예외로 죽으므로 REFUSERS 도
    #: 아니다. 형제 세션 ``fcc-delivery-final-91`` 이 다른 rig 로 재다가 찾았다 —
    #: 내 rig 에서는 «성공»하고 그쪽에서는 실패하는데, 봉인은 양쪽 다 초록이었다.
    #:
    #: ``api_composition`` → ``rbac_role_catalog._discover_schema_path`` 가
    #: ``docs/platform/central_db_schema.v1.json`` 을 «모듈의 조상» 다음 «cwd 의 조상»
    #: 순으로 찾고, 못 찾으면 import 시점에 시끄럽게 죽는다(의도다 — 빈 카탈로그로 모든
    #: authz 를 조용히 403 내는 것보다 낫다). 그래서 답이 **rig 에 달려 있다**:
    #:
    #:     editable 설치 (CI: ``pip install -e '.[test]'``)  → 모듈 조상에 소스 트리 → 성공
    #:     비-editable + 트리 «밖» venv                       → 조상에 아무것도 없음 → 실패
    #:     배포 이미지                                        → ``FCC_PLATFORM_SCHEMA_PATH`` 가 답한다
    #:
    #: ⚠️ 그래서 «포함»으로 묻는다. 등호로 묻으면 rig 를 바꾼 사람이 무관한 red 를 본다.
    #: 그리고 이 이름이 여기 적혀 있다는 것 자체가 **「이 축은 rig 에 의존한다」는 진술**이다 —
    #: 빼면 다음 사람이 자기 rig 의 초록을 전체의 답으로 읽는다.
    DECLARED_OUTSIDE_OTHER_FAILURES = frozenset({
        'api_composition',
    })

    def test_outside_failures_that_are_not_the_deliberate_refusal_are_declared(self):
        """⚠️ 「거부」도 「못 잼」도 아닌 세 번째 모양 — 두 집합 사이로 빠지는 자리."""
        others = {
            name: verdict for name, verdict in self.outside.items()
            if verdict.startswith('other:') and self.inside.get(name) == 'ok'
        }
        surprises = {
            k: v for k, v in others.items() if k not in self.DECLARED_OUTSIDE_OTHER_FAILURES
        }
        self.assertEqual(
            {}, surprises,
            '저장소 밖에서 «의도된 거부가 아닌» 예외로 죽는 모듈이 선언에 없다.\n'
            f'  새로 발견 : {surprises}\n'
            '⚠️ RuntimeError 는 "저장소 안에서 실행하라"는 의도된 거부다. 다른 예외는 그것이 '
            '아니다 — 자원을 못 찾았거나 rig 에 의존한다는 뜻이고, 어느 쪽인지 갈라야 한다.',
        )

    def test_the_probe_is_not_vacuous_inside_the_repository(self):
        """이빨 ① — «안»에서는 아무도 거부하지 않아야 한다. 그래야 위 집합이 밖의 성질이다."""
        refused_inside = sorted(n for n, v in self.inside.items() if v == 'refused')
        self.assertEqual(
            [], refused_inside,
            f'저장소 «안»에서도 거부한다 — 측정이 cwd 축을 재고 있지 않다: {refused_inside}',
        )

    def test_the_probe_actually_imported_something(self):
        """이빨 ② — 전부 죽으면 위 검사들이 공허하게 초록이 된다."""
        ok_inside = [n for n, v in self.inside.items() if v == 'ok']
        self.assertGreater(
            len(ok_inside), len(self.DECLARED_REFUSERS),
            f'«안»에서 import 된 모듈이 {len(ok_inside)}개뿐이다 — 설치가 깨졌다면 이 봉인의 '
            '답은 「초록」이 아니라 「잴 수 없음」이다.',
        )

    def test_some_modules_survive_outside_the_repository(self):
        """이빨 ③ — 밖에서 «전부» 죽으면 이름 집합이 자명해져 아무것도 안 묻는다."""
        survived = sorted(
            n for n, v in self.outside.items() if v == 'ok' and self.inside.get(n) == 'ok'
        )
        self.assertNotEqual(
            [], survived,
            '저장소 밖에서 살아남는 모듈이 하나도 없다 — 그러면 이 축은 「전부 거부」라는 '
            '한 문장이고 집합을 고정할 이유가 없다.',
        )


if __name__ == '__main__':
    unittest.main()
