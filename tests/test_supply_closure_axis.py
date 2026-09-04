"""이 상자가 **자기가 필요로 하는 것을 선언하는가** (공급 폐포, 2026-09-04).

이 저장소는 같은 계급의 결함을 반복해 값으로 치렀고, 그 역사가 이미 두 군데에 적혀
있다 — 그런데 **그것을 재는 검사는 0건이었다.**

  * `pyproject.toml` `[project.optional-dependencies]` 주석:
    *"`jsonschema` 와 `PyJWT[crypto]` 는 검사가 **실제로 임포트** 하는데 선언에 없었다.
    이 머신에는 이미 깔려 있어 로컬은 초록이었고, 갓 클론한 CI 러너에서만 드러났다."*
  * `pyproject.toml` `[tool.setuptools.package-data]` 주석:
    *"`domain/models/reference_catalog.py` 는 모듈 레벨에서 `decision_catalogue.json` 을
    읽으므로, 그것이 빠진 배포물은 설치도 되고 휠도 만들어지지만 **import 하는 순간
    죽는다**. … 상자에는 실려 있고 **휠에만 없었다**."*

두 문장은 **같은 결함의 두 얼굴**이다: 「코드가 실제로 필요로 하는 것」과 「배포 선언이
말하는 것」이 손으로 동기화된다. 손 동기화는 반드시 낡는다.

실측 2026-09-04 — 그 계급이 두 번 더 나왔다:

  * `tests/test_central_docker_compose.py` 가 `yaml` 을 가드 없이 import 하는데
    `PyYAML` 이 어느 extra 에도 없다 → 중앙 저장소 CI 가 **24건 빨간 채로 굳어 있었다**
    (run 33858657216). 그 소음 속에서 새 회귀를 가려내려면 사람이 main 의 실패 집합과
    손으로 대조해야 했다.
  * PM/RF 엑셀 서식이 패키지 밖(`tests/fixtures/`)에 있어 휠에 실리지 않았다 →
    컨테이너에서 내보내기가 **구조적으로** 불가능했다. 개발 트리에서는 전부 초록이었다.

⭐ **그래서 이 파일은 선언을 손으로 세지 않는다.** 두 집합을 **파생**해서 대조한다.
목록을 적어 두는 순간 그 목록이 다음에 낡을 자리가 되기 때문이다.

  ① 의존성 폐포 — 코드가 가드 없이 import 하는 서드파티 이름 ⊆ 선언이 제공하는 배포판
  ② 패키지 자원 폐포 — 패키지 디렉터리 안의 비-.py 파일 ⊆ 실제로 빌드한 휠의 내용물

이름 매핑(`yaml` → `PyYAML`)은 **하드코딩하지 않는다.** 표준 라이브러리의
``importlib.metadata.packages_distributions()`` 가 그 매핑의 SSOT 다
(https://docs.python.org/3/library/importlib.metadata.html).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from importlib.metadata import packages_distributions
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = PROJECT_ROOT / 'pyproject.toml'

#: 스캔에서 제외할 디렉터리 이름. 파이썬 소스가 아니거나(node_modules) 원본의 사본이라
#: 두 번 세게 되는 것들(build · *.egg-info · __pycache__)이다. 이름 목록이 아니라
#: **성질**로 골랐다 — 새 디렉터리가 생겨도 이 성질이 아니면 스캔 대상이다.
_NOT_SOURCE = {'__pycache__', 'node_modules', 'build', 'dist', '.git', '.venv'}


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding='utf-8'))


def _normalize(name: str) -> str:
    """PEP 503 정규화 — `PyYAML` 과 `pyyaml` 과 `Py_YAML` 은 같은 배포판이다."""
    return re.sub(r'[-_.]+', '-', name).lower()


def _requirement_name(spec: str) -> str:
    """`"PyJWT[crypto]>=2.8.0"` → `pyjwt`, git URL 형태도 이름만 떼어낸다."""
    head = spec.split('@', 1)[0].strip()          # PEP 508 direct reference
    head = re.split(r'[<>=!~\[;\s]', head, 1)[0]  # 버전·extra·환경표지 제거
    return _normalize(head)


def _declared_distributions() -> set[str]:
    """이 상자가 **선언한** 배포판 전부 (런타임 + 모든 extra).

    extra 이름을 손으로 적지 않는다 — `[project.optional-dependencies]` 전체를 훑는다.
    새 extra 가 생겨도 이 함수는 그대로 맞다.
    """
    project = _pyproject()['project']
    specs = list(project.get('dependencies') or [])
    for extra_specs in (project.get('optional-dependencies') or {}).values():
        specs.extend(extra_specs)
    return {_requirement_name(spec) for spec in specs}


def _shipped_package_roots() -> list[Path]:
    """`packages.find` 의 include 패턴에서 파생한, 이 상자가 싣는 최상위 패키지들."""
    patterns = (
        _pyproject()['tool']['setuptools']['packages']['find'].get('include') or []
    )
    roots: list[Path] = []
    for pattern in patterns:
        stem = pattern.rstrip('*')
        candidate = PROJECT_ROOT / stem
        if candidate.is_dir():
            roots.append(candidate)
    return roots


def _python_scan_roots() -> list[Path]:
    """이 상자의 파이썬 전부 — 실리는 패키지 + 검사 + 스크립트.

    ⚠️ `scripts/` 는 배포되지 않지만 **돈다**(마이그레이션 러너 · DDL 생성기 · 게이트).
    거기서 쓰는 서드파티가 선언에 없으면 갓 클론한 러너에서 죽는다.
    """
    roots = list(_shipped_package_roots())
    for name in ('tests', 'scripts'):
        candidate = PROJECT_ROOT / name
        if candidate.is_dir():
            roots.append(candidate)
    return roots


def _importable_children(directory: Path) -> set[str]:
    names: set[str] = set()
    for entry in directory.iterdir():
        if entry.name.startswith('.') or entry.name in _NOT_SOURCE:
            continue
        if entry.is_dir() and any(entry.glob('*.py')):
            names.add(entry.name)
        elif entry.is_file() and entry.suffix == '.py':
            names.add(entry.stem)
    return names


def _first_party_names() -> set[str]:
    """이 트리가 스스로 제공하는 import 이름 (파생).

    저장소 루트뿐 아니라 **각 스캔 루트의 직계 자식**까지 센다. `scripts/` 와 `tests/` 는
    패키지가 아니라 디렉터리이고, 그 안의 모듈들은 `sys.path` 에 그 디렉터리를 얹은 뒤
    형제 이름으로 서로를 부른다(`import platform_db_migrate`). 저장소 루트만 훑으면 그
    형제들이 전부 「설치되지 않은 서드파티」로 오독된다 — 실측 2026-09-04, 오탐 9건.

    ⚠️ 이름이 서드파티와 겹치면 이 트리 것이 이긴다. 그것이 실제 import 해소 순서다.
    """
    names = _importable_children(PROJECT_ROOT)
    for root in _python_scan_roots():
        # 루트 자신도 import 이름이다 — `fcc_test_platform` 처럼 패키지 디렉터리가
        # 그대로 최상위 이름인 경우다. 자식만 세면 그 이름이 서드파티로 오독된다.
        if any(root.glob('*.py')):
            names.add(root.name)
        names |= _importable_children(root)
    return names


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in _python_scan_roots():
        for path in root.rglob('*.py'):
            if any(part in _NOT_SOURCE or part.endswith('.egg-info')
                   for part in path.relative_to(PROJECT_ROOT).parts):
                continue
            files.append(path)
    return sorted(files)


@dataclass(frozen=True)
class ImportSite:
    module: str
    path: Path
    line: int

    @property
    def where(self) -> str:
        return f'{self.path.relative_to(PROJECT_ROOT)}:{self.line}'


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    """이 except 절이 import 실패를 삼키는가.

    가드된 import 는 **선택적 의존성 선언**이다 — 없으면 그 경로를 포기한다는 뜻이므로
    폐포 검사의 대상이 아니다. 가드가 없으면 그것은 **필수**이고 선언돼 있어야 한다.
    """
    node = handler.type
    if node is None:                       # bare except
        return True
    candidates = node.elts if isinstance(node, ast.Tuple) else [node]
    for candidate in candidates:
        name = getattr(candidate, 'id', None) or getattr(candidate, 'attr', None)
        if name in ('ImportError', 'ModuleNotFoundError', 'Exception', 'BaseException'):
            return True
    return False


def _unguarded_imports(path: Path) -> list[ImportSite]:
    """한 파일에서 **가드 없이** 이름을 요구하는 import 전부."""
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):  # pragma: no cover — 파서 방어
        return []

    sites: list[ImportSite] = []

    def visit(node: ast.AST, guarded: bool) -> None:
        if isinstance(node, ast.Try):
            catching = [h for h in node.handlers if _catches_import_error(h)]
            body_guarded = guarded or bool(catching)
            for child in node.body:
                visit(child, body_guarded)
            # ⚠️ import 실패를 잡는 핸들러의 **본문도 가드된 것**이다 — 그것이
            # 폴백 분기이기 때문이다(`except ModuleNotFoundError: import psycopg2`).
            # 이것을 무가드로 세면 폴백 대안까지 필수 의존성으로 요구하게 되고,
            # 그러면 「둘 중 하나만 있으면 된다」는 선언을 표현할 수 없다.
            for handler in node.handlers:
                for child in handler.body:
                    visit(child, guarded or _catches_import_error(handler))
            for group in (node.orelse, node.finalbody):
                for child in group:
                    visit(child, guarded)
            return
        if isinstance(node, ast.Import) and not guarded:
            for alias in node.names:
                sites.append(ImportSite(alias.name.split('.')[0], path, node.lineno))
        elif isinstance(node, ast.ImportFrom) and not guarded:
            # 상대 import(`from . import x`)는 이 상자 자신이다.
            if node.level == 0 and node.module:
                sites.append(ImportSite(node.module.split('.')[0], path, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, guarded)

    for child in ast.iter_child_nodes(tree):
        visit(child, False)
    return sites


class TestEveryUnguardedImportIsDeclared(unittest.TestCase):
    """① 의존성 폐포 — 코드가 요구하는 것이 선언에 있는가.

    ⚠️ **이 시험은 두 가지 형태로 빨개지고 둘 다 옳다.**

      * 개발 머신(전부 깔려 있음): 이름은 import 되는데 그것을 제공하는 배포판이
        **선언에 없다** → 「로컬만 초록」이 되기 전에 여기서 멈춘다.
      * 갓 클론한 러너(선언한 것만 깔림): 이름이 **아예 import 되지 않는다** →
        24건이 흩어져 나는 대신 이 시험 하나가 이름을 대고 멈춘다.

    두 형태 모두 사람이 목록을 손보지 않아도 성립한다.
    """

    @classmethod
    def setUpClass(cls):
        # ⚠️ AST 스캔은 이 트리에서 390 파일 · 3,300여 import · 약 1.5초다. 시험마다
        # 다시 돌리면 그 값을 시험 수만큼 낸다 — 게이트가 느리면 사람이 게이트를
        # 건너뛰기 시작하고, 그러면 게이트가 있으나 마나가 된다. 클래스당 한 번만 돈다.
        cls.declared = _declared_distributions()
        cls.first_party = _first_party_names()
        cls.provided = packages_distributions()
        cls.sites = cls._scan_third_party_sites(cls.first_party)
        cls.classified = cls._classify_sites(cls.sites, cls.declared, cls.provided)

    @staticmethod
    def _scan_third_party_sites(first_party: set[str]) -> list[ImportSite]:
        sites: list[ImportSite] = []
        for path in _iter_python_files():
            for site in _unguarded_imports(path):
                if site.module in sys.stdlib_module_names or site.module in first_party:
                    continue
                sites.append(site)
        return sites

    def _third_party_sites(self) -> list[ImportSite]:
        return self.sites

    def test_the_scan_is_not_vacuous(self):
        """빈 스캔이 통과로 읽히지 않게 한다 — 이 게이트가 스스로 꺼지는 것을 막는다."""
        files = _iter_python_files()
        self.assertGreater(len(files), 100, f'스캔한 파이썬 파일이 {len(files)}개뿐이다')
        self.assertGreater(
            len(self._third_party_sites()), 0,
            '서드파티 import 를 하나도 못 찾았다 — 스캐너가 고장났다는 뜻이다',
        )

    def _classify(self) -> tuple[dict, dict]:
        return self.classified

    @staticmethod
    def _classify_sites(sites, declared, provided) -> tuple[dict, dict]:
        """두 결함 계급을 가른다 — 고치는 사람도, 고치는 법도 다르기 때문이다."""
        unresolvable: dict[str, list[str]] = {}
        undeclared: dict[str, tuple[list[str], list[str]]] = {}
        for site in sites:
            providers = provided.get(site.module)
            if providers:
                # 설치돼 있으면 정확한 매핑이 있다 — 그것으로 판정한다.
                if not {_normalize(name) for name in providers} & declared:
                    undeclared.setdefault(
                        site.module, (providers, []))[1].append(site.where)
                continue
            # 설치돼 있지 않다. 이 검사가 도는 환경은 `[test]` 만 깔린 러너일 수 있고,
            # 다른 extra 로 선언된 도구(브라우저 QA 등)는 여기 없는 것이 정상이다.
            # 그때는 이름 매칭이 유일하게 남은 증거다 — 정확한 매핑은 배포판을 설치해야만
            # 알 수 있기 때문이다(`packages_distributions()` 는 설치본을 훑는다).
            # ⚠️ 이름이 배포판과 다른 경우(PyYAML→yaml)는 이 우회를 못 타지만, 그런 것은
            # `[test]` 에 선언돼 러너에 설치되므로 위 정확한 경로로 판정된다.
            if _normalize(site.module) in declared:
                continue
            unresolvable.setdefault(site.module, []).append(site.where)
        return unresolvable, undeclared

    def test_every_unguarded_third_party_import_resolves_to_a_declared_distribution(self):
        """계급 A — 이름은 해소되는데 **선언에 없다.**

        개발 머신에서만 초록인 상태다. 우연히 깔려 있는 배포판에 기대고 있고, 갓 클론한
        러너에서 무너진다. 실측: jsonschema · PyJWT(2026-08-31) · PyYAML(2026-09-04).
        """
        _, undeclared = self._classify()
        report = [
            f'  · {module!r} — {providers} 가 제공하는데 pyproject 선언에 없다 '
            f'(우연히 깔려 있을 뿐이다). 부르는 곳: '
            f'{", ".join(sorted(set(wheres))[:3])}'
            for module, (providers, wheres) in sorted(undeclared.items())
        ]
        self.assertEqual(
            report, [],
            '이 상자가 자족적이지 않다 — 코드가 요구하는데 선언하지 않은 것이 있다:\n'
            + '\n'.join(report)
            + '\n\n고치는 법: pyproject.toml 의 [project.dependencies](소스가 쓰면) 또는 '
            '[project.optional-dependencies] 의 적절한 extra(그 도구만 쓰면) 에 그 배포판을 '
            '선언하라. ⚠️ import 에 try/except 가드를 다는 것으로 통과시키지 마라 — 그것은 '
            '그 경로의 검사를 조용히 끄는 것이고, 이 저장소가 lane_check 에서 이미 거부한 '
            '형태다.',
        )

    #: 계급 B 의 **원장** — 오늘 이 상자가 해소하지 못하는 이름과, 그 사유.
    #:
    #: ⚠️ 이것은 예외 목록이 아니다. `lane_check` 이 쓰는 것과 같은 형태의 원장이고,
    #: 아래 시험이 **정확한 일치**를 요구한다:
    #:
    #:   * 새 이름이 늘면 red — 누군가 이 상자에 없는 코드를 새로 불렀다.
    #:   * 원장의 이름이 해소되면 **그것도 red** — 선언이 낡았다는 사실이고,
    #:     `lane_check` 의 표현대로 *"그것도 소식이다"*.
    #:
    #: 예외 목록은 한 방향으로만 자라서 조용히 낡는다. 원장은 양방향이라 낡을 수 없다.
    #:
    #: ── 오늘의 항목 (2026-09-05 판정) ──────────────────────────────────────────
    #: `scripts/platform_extraction_runner.py` 가 `check_extraction_import_boundaries`
    #: 와 `prepare_headless_extraction_package` 를 부른다. 둘 다 **계약 레인의
    #: `scripts/` 에 살고 이 상자에는 없다** — 모노레포 분리 때 호출자만 남았다.
    #: 그래서 그 러너는 여기서 import 조차 되지 않는다(실측 2026-09-04).
    #:
    #: ⚠️ 그런데 `fcc_test_platform/cutover_workflow_hints.py` 가 운영자에게 그 명령을
    #: 안내한다. 그 힌트 바로 위 주석이 이미 그 위험을 이름 붙여 놨다 — *"힌트가 더는
    #: 존재하지 않는 명령을 설명하게 되는 경로이고, 운영자가 그것과 처음 만나는 자리다."*
    #:
    #: 해소는 이 레인 혼자 할 수 없다: 러너를 내리면 `extraction_package` 증거 축의
    #: 생산자가 사라지고(스키마·검증기·힌트는 이 상자에 있다), 협력자를 들이면 계약
    #: 레인의 「scripts 는 배포하지 않는다」 결정을 뒤집는다. 운영자 판정 2026-09-05:
    #: **이번 배송은 보고까지**, 해소는 다음 작업.
    _KNOWN_UNRESOLVABLE = {
        'check_extraction_import_boundaries',
        'prepare_headless_extraction_package',
    }

    def test_unresolvable_imports_match_the_recorded_ledger_exactly(self):
        """계급 B — 이름이 **어디에서도 해소되지 않는다.**

        선언 문제가 아니다. 이 상자가 자기 안에 없는 코드를 부른다는 뜻이고, 대개 모노레포
        분리 때 협력자만 다른 레인으로 가고 호출자가 남은 자리다. 계급 A 와 섞어 보고하면
        「의존성 하나 더 선언하면 되겠지」로 오독된다 — 그래서 시험을 나눠 둔다.
        """
        unresolvable, _ = self._classify()
        observed = set(unresolvable)
        appeared = sorted(observed - self._KNOWN_UNRESOLVABLE)
        resolved = sorted(self._KNOWN_UNRESOLVABLE - observed)

        detail = []
        for module in appeared:
            wheres = sorted(set(unresolvable[module]))[:3]
            detail.append(
                f'  [새로 생김] {module!r} — 이 상자 안에도 없고 설치된 어떤 배포판도 '
                f'제공하지 않는다. 부르는 곳: {", ".join(wheres)}'
            )
        for module in resolved:
            detail.append(
                f'  [해소됨] {module!r} — 원장에 남아 있는데 이제 해소된다. '
                f'원장에서 지워라.'
            )

        self.assertEqual(
            detail, [],
            '해소 불가 import 원장이 관측과 어긋난다:\n' + '\n'.join(detail)
            + '\n\n[새로 생김] 의 고치는 법은 선언이 아니다. 그 코드의 협력자가 어느 레인에 '
            '사는지 보고, (가) 이 상자가 그 레인을 통해 닿게 하거나 (나) 그 코드가 이 상자의 '
            '것이 아니면 옮겨라. 그 판정을 내리기 전까지 원장에 넣는 것은 **사유와 날짜를 '
            '함께 적을 때만** 정당하다.',
        )

    def test_a_guarded_import_is_treated_as_optional(self):
        """가드 판정 자체를 봉인한다 — 판정이 무너지면 위 시험이 의미를 잃는다."""
        source = (
            'try:\n'
            '    import nonexistent_optional_pkg\n'
            'except ImportError:\n'
            '    nonexistent_optional_pkg = None\n'
            'import nonexistent_required_pkg\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / 'probe.py'
            probe.write_text(source, encoding='utf-8')
            found = {site.module for site in _unguarded_imports(probe)}
        self.assertIn('nonexistent_required_pkg', found)
        self.assertNotIn('nonexistent_optional_pkg', found)


class TestEveryPackageResourceShipsInTheWheel(unittest.TestCase):
    """② 패키지 자원 폐포 — 코드 옆에 있는 비-.py 가 휠에도 있는가.

    ⚠️ **실제로 휠을 빌드해서 잰다.** 선언(`package-data` 글롭)을 읽어서 재면 그 선언이
    틀렸을 때 검사도 같이 틀린다 — `decision_catalogue.json` 과 엑셀 서식이 정확히 그렇게
    빠졌다. 재는 대상은 선언이 아니라 **산출물**이어야 한다.

    ⚠️ 빌드는 트리 안에서 하지 않는다. non-editable 설치는 `build/` 를 남기고
    `lane_check` 는 그 트리에서 **판정을 거부한다**(`CONFOUNDING_ARTIFACTS`).
    검사가 자기가 재는 트리를 오염시키면 그 측정은 자기 자신의 부작용을 잰다.
    """

    #: 소스에 있지만 휠에 없어도 되는 것 — 파이썬 자신이 만드는 부산물뿐이다.
    _NOT_A_RESOURCE = {'.pyc', '.pyo', '.pyd', '.so'}

    @classmethod
    def setUpClass(cls):
        cls.roots = _shipped_package_roots()
        cls.wheel = cls._build_wheel()

    @classmethod
    def _build_wheel(cls) -> zipfile.ZipFile:
        cls._workspace = tempfile.TemporaryDirectory(prefix='fcc-supply-closure-')
        workspace = Path(cls._workspace.name)
        staging = workspace / 'src'
        staging.mkdir()

        # 빌드 백엔드가 요구하는 최소 트리만 사본으로 옮긴다. 저장소 전체를 복사하면
        # apps/web/node_modules 까지 딸려와 이 시험이 분 단위가 된다.
        for name in ('pyproject.toml', 'README.md'):
            source = PROJECT_ROOT / name
            if source.is_file():
                shutil.copy2(source, staging / name)
        for root in _shipped_package_roots():
            shutil.copytree(
                root, staging / root.name,
                ignore=shutil.ignore_patterns(*_NOT_SOURCE, '*.pyc'),
            )

        output = workspace / 'wheel'
        base = [sys.executable, '-m', 'pip', 'wheel', '--no-deps', '-w', str(output)]
        # 격리 없는 빌드를 먼저 시도한다: 네트워크를 타지 않아 빠르고, 러너가 오프라인
        # 이어도 성립한다. setuptools 가 없는 환경에서만 표준 격리 빌드로 되돌아간다.
        attempts = ([*base, '--no-build-isolation', str(staging)], [*base, str(staging)])
        failures = []
        for command in attempts:
            completed = subprocess.run(command, capture_output=True, text=True)
            if completed.returncode == 0:
                break
            failures.append(completed.stderr[-800:])
        else:  # pragma: no cover — 빌드 자체가 불가능한 환경
            raise unittest.SkipTest(
                '휠을 빌드하지 못해 이 축을 잴 수 없다 (통과가 아니다):\n'
                + '\n---\n'.join(failures)
            )

        wheels = sorted(output.glob('*.whl'))
        assert wheels, 'pip 이 0 을 돌려줬는데 휠이 없다'
        return zipfile.ZipFile(wheels[-1])

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, 'wheel', None) is not None:
            cls.wheel.close()
        if getattr(cls, '_workspace', None) is not None:
            cls._workspace.cleanup()

    def _source_resources(self) -> set[str]:
        """패키지 디렉터리 안의 비-.py 파일 전부 (패키지 루트 기준 상대 경로)."""
        resources: set[str] = set()
        for root in self.roots:
            for path in root.rglob('*'):
                if not path.is_file():
                    continue
                parts = path.relative_to(PROJECT_ROOT).parts
                if any(part in _NOT_SOURCE for part in parts):
                    continue
                if path.suffix == '.py' or path.suffix in self._NOT_A_RESOURCE:
                    continue
                resources.add(str(path.relative_to(PROJECT_ROOT)).replace('\\', '/'))
        return resources

    def test_the_scan_is_not_vacuous(self):
        resources = self._source_resources()
        self.assertGreater(
            len(resources), 0,
            '패키지 안에서 비-.py 자원을 하나도 못 찾았다 — 스캐너가 고장났다',
        )

    def test_every_non_python_file_beside_the_code_is_in_the_wheel(self):
        shipped = {name for name in self.wheel.namelist() if not name.endswith('/')}
        missing = sorted(self._source_resources() - shipped)
        self.assertEqual(
            missing, [],
            '코드 옆에 있는데 휠에 실리지 않은 파일이 있다 — 설치본으로 도는 컨테이너에서만 '
            f'죽는 형태다:\n' + '\n'.join(f'  · {name}' for name in missing)
            + '\n\n고치는 법: pyproject.toml 의 [tool.setuptools.package-data] 에 그 패턴을 '
            '선언하라. ⚠️ 반대로 그 파일이 애초에 패키지 안에 있으면 안 되는 것이라면 '
            '패키지 밖으로 옮겨라 — 둘 중 하나이지, 그냥 두는 선택지는 없다.',
        )

    def test_the_wheel_carries_the_python_it_claims(self):
        """휠이 비었는데 자원만 맞는 상태를 통과로 읽지 않는다."""
        modules = [name for name in self.wheel.namelist() if name.endswith('.py')]
        self.assertGreater(len(modules), 50, f'휠에 .py 가 {len(modules)}개뿐이다')


if __name__ == '__main__':
    unittest.main()
