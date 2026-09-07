"""아키텍처 게이트의 봉인 — `mypy.ini` · `.importlinter` (설계서 §6.1~6.2 / S1·S2).

## 이 파일이 게이트에 «붙는» 방식

이 레인의 실질 게이트는 `githooks/pre-push` → `scripts/lane_check.py` → pytest 다
(`.github/workflows/checks.yml` 은 러너 미배정으로 휴면이고, 그 파일 자신이
「검사 정의를 여기 인라인하면 두 게이트가 갈라진다」고 적는다). 그래서 새 게이트는
워크플로 YAML 이 아니라 **테스트 한 파일**로 붙는다 — 그러면 pre-push 와 (러너가
돌아온 날의) CI 가 자동으로 같은 것을 본다.

## 왜 두 층인가 — 도구가 없어도 정책은 봉인된다

`TestTheGatesActuallyRun` 은 도구가 설치돼 있을 때만 돈다. 그 앞의 세 클래스는
**stdlib 만으로** 설정 자체를 검사하므로 어느 환경에서도 돈다.

⚠️ 이 분리가 없으면 게이트가 「도구 미설치」와 「위반 없음」을 같은 초록으로
보고한다 — 이 레포가 여러 번 값을 치른 형태다(`lane_check` 의 수집 0개 문제,
`checks.yml` 의 러너 미배정 문제가 같은 계열이다).

## baseline 은 이제 «공집합»이다 (2026-09-05 — 설계서 S3 착지)

세 계약 모두 예외 **0건**으로 KEPT 다. 그래서 정책 축이 「이 두 이름만 허용」에서
「어느 계약도 예외를 갖지 않는다」로 바뀌었다 — 바닥에 닿은 뒤에는 집합을 이름으로
세는 것보다 **부재를 요구**하는 쪽이 단순하면서 더 세다(허용 목록이 없으면 늘릴
목록도 없다).

  ① 그래프 축 — import-linter 의 `unmatched_ignore_imports_alerting = error`:
     누군가 다시 등재를 넣고 그 위반이 해소되면 게이트가 스스로 깨져 등재를
     지우도록 강제한다. 지금은 등재가 없어 «놀고» 있지만 미래를 위해 켜 둔다.
  ② 정책 축 — 이 파일의 `TestNoContractCarriesABaseline`: 세 계약 어디에도
     `ignore_imports` 가 없어야 한다. 새 위반을 조용히 등재해 초록을 만드는
     길을 막는다.

해소되기 전 두 등재가 무엇이었고 각각 어떻게 처분됐는지는 `.importlinter` 의
계약 3 주석이 **이름으로** 갖는다. 장부는 코드 옆에 두고 검사는 부재만 묻는다 —
검사가 장부를 겸하면 장부를 고치려고 검사를 무르는 날이 온다.
"""
from __future__ import annotations

import ast
import configparser
import json
import importlib.util
import os
import re
import subprocess
import sys
import unittest
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MYPY_INI = REPO_ROOT / 'mypy.ini'
IMPORTLINTER_INI = REPO_ROOT / '.importlinter'

#: strict 를 강제하는 범위. 저장소 전체가 아니라 domain/* 이다 — 도메인이 순수
#: (서드파티 의존 0)이라 타입이 가장 잘 서고, 거기서 얻는 규율이 가장 싸다.
#: strict 를 강제하는 절 — **집합이다.**
#:
#: ⚠️ 2026-09-06 까지 이것은 문자열 하나(`domain.*`)였다. 그 형태에서는 범위를 한 층
#:    넓히는 순간 아래 검사가 빨개지고, 그 red 는 「회귀」가 아니라 **「장부를 같이
#:    고치라」**는 신호다. 그런데 문자열 하나짜리 장부는 그 신호를 「고쳐야 할 검사」로
#:    보이게 만들어, 다음 사람이 범위를 넓히는 대신 검사를 되돌리도록 유도한다.
#:    집합으로 두면 층을 더하는 일이 **한 줄 추가**가 되고, 그 한 줄이 곧 선언이다.
#: 🔄 **2026-09-07 — 네 줄이 한 줄로 접혔다.** 층 넷은 각각 0건이었는데 **넷의
#:    합집합이 패키지가 아니었다**: 게이트가 절마다 «따로» 부르므로 최상위 모듈은
#:    어느 호출에서도 source file 이 되지 못했다(실측 139 대 215). 열거는 «다음
#:    파일이 조용히 빠지는 자리»이고, 이번이 그 구멍이 실현된 사례다.
#:    아래 `TestTheStrictScopeCoversThePackage` 가 그 되돌림을 막는다.
STRICT_SECTIONS = (
    'mypy-fcc_test_platform.*',
)

#: 이 절이 «접기 전»의 네 층. 지우지 않는다 — 아래 이빨 검사가 이것을 주입해
#: 「층 열거로는 패키지를 못 덮는다」를 매번 실증한다. 즉 이 상수는 기록이 아니라
#: **검사의 입력**이다.
LAYER_ONLY_SECTIONS_BEFORE_20260907 = (
    'mypy-fcc_test_platform.domain.*',
    'mypy-fcc_test_platform.infrastructure.*',
    'mypy-fcc_test_platform.application.*',
    'mypy-fcc_test_platform.api.*',
)
#: ⚠️ 2026-09-06 — `application.session.*`·`application.headless.*` 와 어댑터 계열
#:    24모듈의 «파생 + 열거» 두 덩어리가 여기서 한 줄로 접혔다. 그 구조가 존재한
#:    이유는 이름 규약이 아니라 **비용**이었고(층 전체 145건), 이번 웨이브가 마지막
#:    57건을 처분해 `application.*` 전량이 0건이 되면서 사유가 사라졌다.
#:
#: ⚠️ *"부분 와일드카드(`central_*`)는 0건 매치"* 라는 실측은 **여전히 참이다.**
#:    바뀐 것은 이제 부분이 아니라 **전부**를 선언한다는 것이다.
#:
#: ⚠️ 열거는 «다음 파일이 조용히 빠지는» 자리였다 — `central_read_adapter` 가 두
#:    웨이브 동안 밖에 있었고, 이유는 그 이름이 `central_<X>_read_adapter` 파생을
#:    따르지 않아서였다. 와일드카드는 그 구멍을 구성상 갖지 않는다.


#: 절 이름에서 **mypy 호출 인자**를 파생한다 — 두 번 적으면 갈라진다.
#:
#: ⚠️ 2026-09-06 까지 이 파생은 `section[:-len('.*')]` 로 **무조건** 끝 두 글자를
#:    잘랐다. 그 형태는 패키지 글롭만 받는다 — 모듈 절을 넣으면 이름이
#:    `…central_user_write_adapt` 로 잘린다. 다행히 그 실패는 조용하지 않다(실측:
#:    `mypy -p <없는 이름>` 은 exit 2 이고 「N source files」 줄도 없어서, 아래 두 검사가
#:    각각 잡는다). 그래도 장부가 «모듈을 받을 수 있어야» 다음 묶음을 자를 수 있으므로
#:    형태를 고친다: `.*` 로 끝나면 패키지(`-p`), 아니면 모듈(`-m`)이다.
STRICT_TARGETS = tuple(
    ('-p', section[len('mypy-'):-len('.*')]) if section.endswith('.*')
    else ('-m', section[len('mypy-'):])
    for section in STRICT_SECTIONS
)

#: 사람이 읽는 이름(보고·subTest 라벨용).
STRICT_PACKAGES = tuple(name for _flag, name in STRICT_TARGETS)

#: 게이트가 **부르는** 대상 전체.
#:
#: ⚠️ `STRICT_SECTIONS` 와 «다른 질문»이다. 저것은 「어디가 strict 인가」를 말하고
#:    이것은 「어디를 «검사»하는가」를 말한다. 그 둘이 갈라져 있던 것이
#:    2026-09-07 웨이브가 연 결함의 원인이다 — 게이트가 절 이름에서 호출 인자를
#:    파생해 절마다 «따로» 부르므로, 목록에 없는 `fcc_test_platform/` 최상위 75
#:    모듈은 **호출조차 되지 않았다.** 빨강도 초록도 아닌 침묵이었고, 그 침묵
#:    아래 실제 타입 오류 45건이 16파일에 쌓여 있었다(실측 main@9565be6, CI 와
#:    같은 리그).
#:
#: ⚠️ **이 축을 `mypy.ini` 의 절로 표현하지 않는다.** `[mypy-fcc_test_platform.*]`
#:    를 넣으면 그것은 「전량 검사」가 아니라 「전량 strict」이고, 성질이 다른
#:    `no-untyped-def` 183건이 딸려 온다. 45건은 strict 옵션이 아니라 «기본»
#:    설정에서도 잡히는 실제 타입 오류였다 — 부족했던 것은 설정이 아니라 호출이다.
#:    그러니 답도 설정이 아니라 호출이어야 한다.
#:
#: ⚠️ 그리고 `[mypy-fcc_test_platform]`(점-별 없이)은 **no-op** 이다(PR #139 실측).
#:    그 절을 넣으면 게이트가 `-m fcc_test_platform` 을 부르긴 하면서
#:    `checked 1 source file` 을 성실히 보고한다 — 본 것은 `__init__.py` 하나다.
WHOLE_PACKAGE = 'fcc_test_platform'


def _expected_module_count(package: str) -> int:
    """mypy 가 ``-p <package>`` 에서 「N source files」로 «보고해야 할» 수.

    ⚠️ **디스크의 `.py` 수와 같지 않다.** mypy 는 ``__init__.py`` 가 없는 디렉터리를
    namespace 패키지로 잡아 모듈 하나로 «더» 센다. 실측 2026-09-07 — 이 트리에는
    그런 디렉터리가 셋이다(`fcc_test_platform` 자신 · `api` · `infrastructure/excel/
    templates`)::

        fcc_test_platform                 .py 212 + namespace 3 = 215   ✓ mypy 215
        fcc_test_platform.domain          .py  51 + namespace 0 =  51   ✓ mypy  51
        fcc_test_platform.infrastructure  .py  10 + namespace 1 =  11   ✓ mypy  11
        fcc_test_platform.application     .py  75 + namespace 0 =  75   ✓ mypy  75
        fcc_test_platform.api             .py   1 + namespace 1 =   2   ✓ mypy   2

    ⚠️ 이 함수가 **장부가 아니라 파생**인 것이 요점이다. 「215」를 상수로 박으면
    모듈이 하나 늘어난 날 초록이 빨개지고, 다음 사람은 «검사를 고치는» 쪽으로
    유도된다. 파생이면 양쪽이 같이 움직이고, 움직이지 «않는» 것 — 즉 게이트가
    조용히 좁아지는 것 — 만 red 가 된다. `.importlinter` 가 못박은 「등재를 다시
    늘리지 마라, 답은 등재가 아니라 코드다」와 같은 규율이다.

    ■ 이 식이 «무엇에» 기대는가 — 두 축을 갈라 재 뒀다 (2026-09-07)

    형제 세션이 취약 조건 하나를 제기했다: *"`infrastructure/excel/templates` 는
    `.py` 가 0개이고 `.xlsx` 만 있다. 누군가 그 디렉터리를 옮기면 조용히 1이
    빠진다."* **재 봤고, 그 결론은 성립하지 않는다.** 이 함수와 mypy 는 **같은
    파일시스템**을 읽으므로 양쪽이 함께 움직인다 (실측: 그 디렉터리를 트리 밖으로
    옮기고 → 파생 214 / mypy 214, 복원 → 둘 다 215). 「조용히 어긋난다」가 바로
    장부의 실패 모드이고, 파생은 구성상 그것을 갖지 않는다.

    ⚠️ **다만 그 관찰이 진짜 의존 축을 가리킨다.** 이 식은 트리의 성질이 아니라
    **mypy 의 동작** 하나에 기댄다 — 「`__init__.py` 없는 디렉터리는 그 안에
    `.py` 가 **하나도 없어도** 모듈 하나로 센다」. 그리고 `pyproject.toml` 은
    `mypy>=2.3.1` 로 **바닥만** 정하므로 그 동작이 바뀌면 이 등호가 깨진다.

    그래서 «그 축을» 쟀다 — 같은 트리, mypy 만 갈아 끼우며:

        mypy 1.15.0  →  215        mypy 2.0.0  →  215
        mypy 1.20.2  →  215        mypy 2.3.1  →  215

    1.x → 2.x 주버전 경계를 포함해 안정적이다. 그러니 이 등호가 언젠가 빨개지면
    **먼저 물을 것은 「트리가 움직였나」가 아니라 「mypy 가 계수 규칙을 바꿨나」**다.
    전자면 양쪽이 같이 움직여 red 가 나지 않는다.
    """
    root = REPO_ROOT / Path(*package.split('.'))
    modules = 0
    for path in root.rglob('*'):
        if '__pycache__' in path.parts:
            continue
        if path.is_file():
            if path.suffix == '.py':
                modules += 1
        elif path.is_dir() and not (path / '__init__.py').is_file():
            modules += 1
    if not (root / '__init__.py').is_file():
        modules += 1
    return modules

def _module_names(package: str) -> set[str]:
    """트리에서 **모듈 이름 전부**를 파생한다 — `_expected_module_count` 의 이름 판.

    두 함수가 같은 트리를 다르게 걸으면 갈라지므로, 규칙을 여기 한 번만 적는다.
    수와 이름이 같은 것에서 나온다는 것은 아래 첫 검사가 **등호로** 확인한다.
    """
    root = REPO_ROOT / Path(*package.split('.'))
    names = {package}
    for path in root.rglob('*'):
        if '__pycache__' in path.parts:
            continue
        rel = path.relative_to(root)
        if path.is_file() and path.suffix == '.py':
            parts = list(rel.with_suffix('').parts)
            if parts and parts[-1] == '__init__':
                parts = parts[:-1]
            names.add('.'.join([package, *parts]))
        elif path.is_dir() and not (path / '__init__.py').is_file():
            names.add('.'.join([package, *rel.parts]))
    return names


def _pattern_matches(pattern: str, module: str) -> bool:
    """mypy 의 per-module 절 이름이 모듈을 잡는가.

    ⚠️ **`*` 는 점으로 구분된 성분 «전체»만 대체한다.** 이 저장소가 여러 번 실측해
    적어 둔 사실이고, 이 함수가 그것을 기계로 옮긴 것이다 — `central_*` 같은 부분
    와일드카드는 **0건 매치**이며, 그 실패는 「위반 없음」과 출력이 같다.

    ⚠️ 그리고 `foo.*` 는 `foo` **자신도** 잡는다(mypy 문서). 그 규칙이 없으면 이
    검사가 네임스페이스 루트 하나를 「안 덮인다」고 잘못 말한다.
    """
    if pattern.endswith('.*'):
        prefix = pattern[:-len('.*')]
        return module == prefix or module.startswith(prefix + '.')
    want = pattern.split('.')
    got = module.split('.')
    return len(want) == len(got) and all(w in ('*', g) for w, g in zip(want, got, strict=True))


def _modules_not_covered(sections: tuple[str, ...], package: str) -> set[str]:
    """`sections` 가 «못 덮는» 모듈 이름들. 빈 집합이면 완전히 덮는다."""
    patterns = [s[len('mypy-'):] for s in sections]
    return {
        module for module in _module_names(package)
        if not any(_pattern_matches(pattern, module) for pattern in patterns)
    }


#: 예외를 가져서는 안 되는 계약 — 즉 **전부**다. 2026-09-05 S3 착지로 마지막
#: 등재 2건(`app-no-db`)이 해소되면서 세 계약이 나란히 예외 0건이 됐다.
#: ⚠️ 여기서 이름을 빼는 것은 「그 계약에 예외를 허용한다」는 뜻이다. 그러지 마라.
CONTRACTS_THAT_CARRY_NO_BASELINE = ('layers', 'purity', 'app-no-db')

_CONTRACT_PREFIX = 'importlinter:contract:'

#: S3 가 드라이버 결박을 모아 둔 «유일한» 자리. 이 모듈의 docstring 이 frozen-exe
#: 안전(데스크톱 빌드가 PostgreSQL 드라이버를 0바이트 싣는다)을 약속하므로,
#: 그 약속을 여기서 기계가 지킨다. 약속만 있고 검사가 없으면 주석과 같은 효력이다.
DRIVER_ADAPTER = (
    REPO_ROOT / 'fcc_test_platform' / 'infrastructure' / 'adapters' / 'driven'
    / 'central_db_connection.py'
)

_DRIVER_ROOTS = frozenset({'psycopg', 'psycopg2', 'asyncpg'})


def _driver_import_lines(tree: ast.AST) -> tuple[list[int], list[int]]:
    """(모듈 최상위, 전체) 드라이버 import 의 줄 번호.

    «전체»는 ``ast.walk`` 라 함수 안 지연 import 까지 센다 — frozen-exe 판정에서
    중요한 것이 정확히 그 구분이기 때문이다.
    """
    def is_driver(node: ast.AST) -> bool:
        if isinstance(node, ast.Import):
            return any(a.name.split('.')[0] in _DRIVER_ROOTS for a in node.names)
        if isinstance(node, ast.ImportFrom):
            return bool(node.module) and node.module.split('.')[0] in _DRIVER_ROOTS
        return False

    top = [n.lineno for n in tree.body if is_driver(n)]
    every = [n.lineno for n in ast.walk(tree) if is_driver(n)]
    return top, every


class TestTheDriverBindingStaysLazy(unittest.TestCase):
    """frozen-exe — 드라이버는 «한 파일의 함수 안»에서만 묶인다 (설계서 S3).

    ⚠️ **팔이 둘인 이유.** 「모듈 최상위에 psycopg 가 없다」만 물으면, 누군가 그
    결박을 통째로 지웠을 때도 초록이다 — 참이지만 아무것도 재지 않는 참이 된다.
    이 저장소는 그 형태를 이미 안다: 경로를 하드코딩한 검사가 이관 후 껍데기를
    읽으면 단언이 전부 통과하면서 아무것도 안 지킨다. 그래서 둘째 팔이
    **결박이 실제로 거기 있는지**를 함께 묻는다.

    왜 여기가 아니라 `api_composition` 이 아닌가: 2026-09-05 이전에는 그쪽에
    결박이 있었고 `test_platform_equipment_list_api` 가 그 자리를 지켰다. S3 가
    결박을 이 파일로 옮겼으므로 **지키는 자리도 함께 옮겨야** 한다. 옮기지 않으면
    옛 검사는 자기 파일에 대해서는 여전히 참이면서 성질을 놓친다.
    """

    def test_the_driver_is_bound_lazily_and_the_binding_is_actually_there(self):
        self.assertTrue(
            DRIVER_ADAPTER.is_file(),
            f'{DRIVER_ADAPTER} 가 없다 — S3 의 드라이버 결박 자리가 사라졌다')
        tree = ast.parse(DRIVER_ADAPTER.read_text(encoding='utf-8'))
        top, every = _driver_import_lines(tree)

        self.assertEqual(
            [], top,
            f'{DRIVER_ADAPTER.name} 의 모듈 최상위에 드라이버 import 가 생겼다(줄 {top}). '
            'import 하는 것만으로 psycopg 가 딸려 오면 데스크톱 빌드가 드라이버를 싣는다.')

        # ⚠️ 안티-공허 팔 — 대상의 «부재»로 초록이 되는 길을 막는다.
        self.assertTrue(
            every,
            f'{DRIVER_ADAPTER.name} 에 드라이버 import 가 하나도 없다. 위 팔이 '
            '「최상위에 없다」로 통과했지만 그것은 결박이 사라졌다는 뜻일 수 있다 — '
            '결박을 옮겼다면 이 검사도 새 자리를 가리키게 고쳐라.')


def _read(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read_string(path.read_text(encoding='utf-8'), source=str(path))
    return parser


def _as_set(raw: str) -> set[str]:
    return {line.strip() for line in raw.splitlines() if line.strip()}


def _sibling_lane() -> Path:
    return REPO_ROOT.parent / 'fcc-test-contracts'


class GateEvidence(UserWarning):
    """게이트 도구가 «일했다»는 증거를 CI 로그로 내보내는 통로.

    ⚠️ **경고가 아니다.** 그런데도 `warnings` 를 쓰는 이유가 있다 — 다른 통로가
    전부 막혀 있었다(실측 2026-09-05):

        print / stderr        pytest 가 통과한 시험의 출력을 인쇄하지 않는다
        -rA · -rP 로 전환     `lane_check.py` 의 `PYTEST_ARGS` 는 pre-push 와 CI 가
                              **공유하는 SSOT** 다. 거기를 건드리면 통과 3,000여 건의
                              출력이 함께 쏟아진다
        JUnit XML             이 레인은 생성하지 않는다

    warnings summary 는 **기본으로 인쇄되고 메시지 전문을 싣는다**(CI run
    33968476114 로그에서 확인). 그래서 이 한 줄만으로 증거가 pre-push 와 CI 양쪽
    로그에 남는다.

    ## 왜 이것이 필요한가

    아래 두 팔은 도구가 보고한 규모를 **단언**한다(`Analyzed … dependencies` 가
    없으면 「돌지 않았다」로 red). 그 단언은 러너 위에서 참이지만, **통과하면 숫자가
    아무 데도 남지 않는다.** 그래서 로그를 읽는 사람은 「돌았다」와 「빠졌다」를
    skip 줄의 부재로만 추론해야 했다 — 이 저장소가 반복해 밟은 「도구가 안 돌았다 =
    위반이 없다」와 정확히 같은 관측 문제다(설계서 §7 교훈 ③).

    단언은 **기계**를 위한 것이고, 이 통로는 **사람**을 위한 것이다. 둘 다 필요하다.
    """


def _publish_evidence(line: str) -> None:
    """도구가 보고한 규모를 로그에 남긴다 — 단언을 대신하지 «않는다»."""
    warnings.warn(line, GateEvidence, stacklevel=2)


def _contracts_are_reachable() -> bool:
    """형제 레인의 패키지에 «도달할 수 있는가».

    ⚠️ 이 질문에 「형제 «디렉터리»가 있는가」로 답하면 CI 에서 틀린다. 개발자
    체크아웃에서는 형제 트리가 곧 도달 경로라 둘이 우연히 같은 값이지만, CI 러너는
    자기 레포 하나만 체크아웃하고 형제 레인은 **pip 로** 받는다:

        CI 실측 2026-09-05 (run 33964514082):
          Collecting fcc-test-contracts @ git+...@v0.1.21 (from fcc-test-platform==0.1.9)
          Collecting fcc-test-kernel   @ git+...@kernel-v0.5.0
          REPO_ROOT.parent / 'fcc-test-contracts'  → 없음

    즉 필요한 것을 **가지고 있으면서** 디렉터리가 없다는 이유로 게이트가 빠지고
    있었다. 이 레인이 `fcc-test-contracts` 를 정식 의존으로 «선언»하므로
    (`[project].dependencies`, 아래 `TestTheSiblingLaneIsADeclaredDependency` 가
    봉인) 올바르게 설치된 환경이라면 임포트가 언제나 답이다.

    실측: 형제 디렉터리 없이 설치본만으로 `Analyzed 213 files, 927 dependencies`
    · 3 kept, 0 broken — `.importlinter` 가 기록한 수치와 같다.
    """
    return importlib.util.find_spec('fcc_test_contracts') is not None


def _tool_env() -> dict[str, str]:
    """이 레인은 혼자 돌지 않는다 — 형제 레인이 sys.path 에 있어야 한다.

    근거는 `EXTRACTED_FROM.md` §「이 상자가 혼자 도는가」다.
    """
    sib = _sibling_lane()
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(
        [str(REPO_ROOT), str(sib), str(sib / 'packages' / 'fcc-test-kernel')]
    )
    env['MYPYPATH'] = str(REPO_ROOT)
    return env


class TestTheMypyGateIsDeclared(unittest.TestCase):
    """S1 — `domain/*` 에 strict 가 «선언»돼 있는가."""

    def test_the_config_does_not_live_in_the_delivered_pyproject(self):
        """⚠️ 이 팔이 설정 파일이 따로 있는 이유다.

        루트 `pyproject.toml` 은 `.extraction-layout.json` 이 예약한 배송 경로다
        (`packaging/fcc-test-platform/pyproject.toml` → `pyproject.toml`). 거기에
        게이트 설정을 두면 배송이 그 파일을 이름으로 대며 거부한다.
        """
        self.assertTrue(MYPY_INI.is_file(), 'mypy.ini 가 없다 — 게이트가 선언되지 않았다')
        pyproject = (REPO_ROOT / 'pyproject.toml').read_text(encoding='utf-8')
        self.assertNotIn(
            '[tool.mypy]', pyproject,
            '배송이 관리하는 pyproject.toml 에 mypy 설정이 들어갔다 — 배송이 거부한다')

    def test_the_declared_layers_are_strict_and_the_rest_is_not_yet(self):
        cfg = _read(MYPY_INI)
        self.assertTrue(STRICT_SECTIONS, 'strict 절이 하나도 선언되지 않았다 (판정이 vacuous)')
        for section in STRICT_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(
                    section, cfg.sections(),
                    f'{section} 절이 없다 — 그 층에 strict 가 걸리지 않는다')
                self.assertTrue(
                    cfg.getboolean(section, 'disallow_untyped_defs'),
                    f'{section} 의 disallow_untyped_defs 가 켜져 있지 않다')
        self.assertFalse(
            cfg.getboolean('mypy', 'disallow_untyped_defs'),
            '저장소 전체 strict 는 아직 합의된 범위가 아니다 — 범위를 넓히려면 '
            '설계서를 먼저 고쳐라')


class TestTheStrictScopeCoversThePackage(unittest.TestCase):
    """🆕 2026-09-07 — **「네 층의 합집합이 패키지인가」를 묻는 자리.**

    ■ 왜 이 검사가 필요했나

    옆의 `TestTheGatesActuallyRun.test_the_strict_layers_have_no_untyped_defs` 는
    `STRICT_PACKAGES` 를 순회하며 **「각 층이 0인가」**만 묻는다. 그 형태의 사각지대는
    **목록에 없는 모듈**이고, 그것은 빨강도 초록도 아닌 **침묵**이다. 실측
    2026-09-07(main@f143f2d)::

        4회 호출이 넘긴 것   51 + 11 + 75 + 2 = 139 source files
        패키지 전체          215 source files

    76의 차이에 `no-untyped-def` 183건과 (앞선 웨이브가 처분한) 실제 타입 오류 45건이
    있었다. **검사는 성실히 돌았고 139개를 정말로 검사했고 정직하게 초록을 냈다.**
    말하지 않은 것은 76이 밖에 있다는 사실뿐이다 — 이 레포가 이름 붙인 *공허 통과의
    둘째 종류: 집합이 비는 것이 아니라 집합이 «틀린» 것.*

    ■ 왜 도구가 아니라 **트리**에 묻나

    이 클래스는 stdlib 만 쓴다. mypy 가 없는 기계에서도 돈다 — 그리고 그것이
    요점이다. 「도구가 안 돌았다」와 「범위가 좁아졌다」는 다른 사건인데, 도구에
    물으면 둘이 같은 skip 이 된다.
    """

    def test_the_two_derivations_agree(self):
        """⚠️ **안티-공허 팔이 먼저다.** 이름 집합이 비거나 수 파생과 어긋나면
        아래 커버리지 판정이 «자동으로» 통과한다 — 아무것도 안 덮어도 「안 덮인 것이
        없다」가 되기 때문이다. 그래서 이름 파생과 수 파생을 **등호로** 묶는다.
        """
        names = _module_names(WHOLE_PACKAGE)
        self.assertTrue(names, '모듈 이름을 하나도 파생하지 못했다 — 트리가 사라졌나')
        self.assertEqual(
            _expected_module_count(WHOLE_PACKAGE), len(names),
            '이 파일의 «수» 파생과 «이름» 파생이 다른 답을 냈다. 둘은 같은 트리를 '
            '같은 규칙으로 걸어야 한다 — 갈라지면 한쪽이 다른 쪽을 못 지킨다.')

    def test_the_declared_strict_scope_covers_every_module(self):
        """선언된 strict 범위가 패키지의 **모든 모듈**을 덮는가."""
        uncovered = _modules_not_covered(STRICT_SECTIONS, WHOLE_PACKAGE)
        shown = ', '.join(sorted(uncovered)[:8])
        self.assertEqual(
            set(), uncovered,
            f'strict 범위 밖에 모듈 {len(uncovered)}개가 있다 — {shown}\n'
            f'게이트는 절 이름에서 호출 인자를 파생해 절마다 «따로» 부른다. 목록에 '
            f'없는 모듈은 red 도 green 도 아닌 «침묵»이고, 그 침묵은 초록과 구별되지 '
            f'않는다. 범위를 좁혔다면 되돌리고, 정말로 좁혀야 한다면 그 사유를 '
            f'mypy.ini 와 이 검사에 «함께» 적어라.')

    def test_this_check_has_teeth(self):
        """**이빨 확인 — 주입이 여기 «상주»한다.**

        ⚠️ 위 팔은 오늘 통과한다. 그런데 「덮는다」와 「이 함수가 아무것도 못 본다」는
        출력이 같다. 그래서 **접기 전의 네 층 열거를 매번 주입**해, 그 형태가 실제로
        빨개지는지를 검사 자신이 확인한다. 한 번 손으로 주입하고 마는 것과 다르다 —
        매처가 나중에 망가지면 이 팔이 그날 말한다.

        실측 2026-09-07: 그 열거로는 76개 모듈이 밖에 남는다(215 − 139).
        """
        uncovered = _modules_not_covered(
            LAYER_ONLY_SECTIONS_BEFORE_20260907, WHOLE_PACKAGE)
        self.assertTrue(
            uncovered,
            '접기 전의 네 층 열거가 «패키지를 전부 덮는다»고 나왔다. 그럴 리 없다 — '
            '그 형태가 최상위 모듈을 놓치는 것이 2026-09-07 웨이브의 출발점이었다. '
            '이 검사의 매처가 망가졌거나, 최상위 모듈이 전부 사라진 것이다.')
        self.assertTrue(
            all('.' not in m[len(WHOLE_PACKAGE) + 1:] for m in uncovered if m != WHOLE_PACKAGE),
            f'놓친 것이 최상위 모듈만이 아니다: {sorted(uncovered)[:8]} — '
            f'네 층 열거가 층 «안»의 무언가도 못 덮는다면 진단이 달라진다.')
        _publish_evidence(
            f'strict 범위 봉인이 이빨을 보였다 — 층 열거는 {len(uncovered)}개를 '
            f'놓치고, 오늘의 범위는 0개를 놓친다')


class TestTheImportLinterContractsAreDeclared(unittest.TestCase):
    """S2 — 세 계약이 «선언»돼 있는가."""

    def test_all_three_contracts_are_present(self):
        cfg = _read(IMPORTLINTER_INI)
        contracts = {
            s[len(_CONTRACT_PREFIX):]: cfg[s]
            for s in cfg.sections() if s.startswith(_CONTRACT_PREFIX)
        }
        self.assertEqual({'layers', 'purity', 'app-no-db'}, set(contracts))
        self.assertEqual('layers', contracts['layers']['type'])
        self.assertEqual('forbidden', contracts['purity']['type'])
        self.assertEqual('forbidden', contracts['app-no-db']['type'])

    def test_the_four_layers_are_ordered_top_down(self):
        cfg = _read(IMPORTLINTER_INI)
        self.assertEqual(
            [
                'fcc_test_platform.api',
                'fcc_test_platform.infrastructure',
                'fcc_test_platform.application',
                'fcc_test_platform.domain',
            ],
            [ln.strip() for ln in
             cfg[f'{_CONTRACT_PREFIX}layers']['layers'].splitlines() if ln.strip()],
            '레이어 순서가 바뀌면 계약이 «반대»를 검사한다 — 위반이 조용히 통과한다')

    def test_external_packages_are_included(self):
        """⚠️ 없으면 실행 «자체»가 거부된다 — forbidden 에 외부 패키지가 있기 때문이다."""
        cfg = _read(IMPORTLINTER_INI)
        self.assertTrue(cfg.getboolean('importlinter', 'include_external_packages'))


class TestNoContractCarriesABaseline(unittest.TestCase):
    """정책 축 — 세 계약 어디에도 예외가 없어야 한다 (파일 docstring 참조)."""

    def test_no_contract_carries_a_baseline(self):
        """⚠️ 이 팔이 「새 위반을 등재해 초록 만들기」를 막는 유일한 자리다.

        그래프 축(`unmatched_ignore_imports_alerting`)은 **해소된** 등재만 잡는다.
        새로 «추가된» 등재는 그래프와 완벽히 일치하므로 그쪽에서는 초록이다.
        """
        cfg = _read(IMPORTLINTER_INI)
        for name in CONTRACTS_THAT_CARRY_NO_BASELINE:
            with self.subTest(contract=name):
                section = cfg[f'{_CONTRACT_PREFIX}{name}']
                self.assertEqual(
                    set(), _as_set(section.get('ignore_imports', '')),
                    f'{name} 계약에 예외가 생겼다. 세 계약은 실측상 위반 0건이다 — '
                    f'새 위반이 났다면 답은 등재가 아니라 코드다. SQL 은 '
                    f'infrastructure 에서만 나온다(설계서 S3).')

    def test_a_stale_entry_breaks_the_gate_rather_than_lingering(self):
        """그래프 축 — 누군가 다시 등재를 넣더라도 해소되면 게이트가 깨진다."""
        cfg = _read(IMPORTLINTER_INI)
        self.assertEqual(
            'error',
            cfg[f'{_CONTRACT_PREFIX}app-no-db']['unmatched_ignore_imports_alerting'],
            '이 값이 error 가 아니면 해소된 등재가 조용히 남아 baseline 이 '
            '한 방향으로 줄지 않는다')


#: import-linter 의 진입점. ⚠️ `-m importlinter.cli` 가 **아니다** — 그 패키지에는
#: `__main__.py` 가 없어서 모듈을 import 만 하고 **아무 출력 없이 exit=0** 으로
#: 끝난다. 2026-09-05 에 이 자리에서 실제로 겪었다: 게이트가 초록이었는데 계약을
#: 하나도 검사하지 않고 있었다. 콘솔 스크립트가 부르는 것과 같은 것을 부른다
#: (`entry_points.txt`: lint-imports = importlinter.cli:lint_imports_command).
_LINT_IMPORTS_ENTRY = 'from importlinter.cli import lint_imports_command; lint_imports_command()'


class TestTheGatesCanActuallyRunHere(unittest.TestCase):
    """⚠️ **skip 은 통과가 아니다** — 이 클래스가 그 차이를 지킨다 (2026-09-05).

    아래 `TestTheGatesActuallyRun` 의 두 팔은 도구가 없으면 `skipIf` 로 빠진다.
    그 설계는 옳다(도구 미설치가 남의 커밋을 막으면 안 된다). 그런데 **선언이 없으면
    그 skip 이 «영구»가 된다** — 갓 `pip install -e '.[test]'` 한 러너에서도 빠지고,
    아무도 계약이 KEPT 인지 보지 않는 채로 게이트가 초록을 낸다.

    실측 2026-09-05: `[test]` extra 에 두 도구가 «선언되어 있지 않아» 공유 체크아웃의
    `.venv` 와 시스템 `python3`(= `pre-push` 의 기본 인터프리터) 양쪽에서 두 팔이
    조용히 빠지고 있었다. 이 파일이 본문에서 경고하는 「도구가 안 돌았다 = 위반이
    없다」를 **이 파일 자신이** 겪고 있었다.

    그래서 이 팔은 도구의 «존재»가 아니라 **선언**을 본다 — 존재를 보면 도구가 깔린
    기계에서만 초록이 되어 같은 함정을 되풀이한다.
    """

    def test_the_test_extra_declares_both_gate_tools(self):
        import tomllib
        pyproject = REPO_ROOT / 'pyproject.toml'
        data = tomllib.loads(pyproject.read_text(encoding='utf-8'))
        extra = data['project']['optional-dependencies']['test']
        names = {re.split(r'[<>=!\[]', item, maxsplit=1)[0].strip().lower() for item in extra}
        for tool in ('mypy', 'import-linter'):
            with self.subTest(tool=tool):
                self.assertIn(
                    tool, names,
                    f"`[project.optional-dependencies].test` 에 {tool} 이 없다 — "
                    f"그러면 TestTheGatesActuallyRun 의 팔이 갓 설치한 러너에서도 "
                    f"영구히 skip 되고, 계약이 깨져도 아무도 보지 못한다.")


class TestTheSiblingLaneIsADeclaredDependency(unittest.TestCase):
    """⚠️ **선행조건이 「트리」를 보면 CI 에서 조용히 빠진다** (2026-09-05).

    `TestTheGatesActuallyRun.test_the_three_contracts_hold` 은 형제 레인이 필요한데,
    그 «필요»를 오랫동안 `REPO_ROOT.parent / 'fcc-test-contracts'` 디렉터리의 존재로
    확인했다. 개발자 체크아웃에서는 맞는 답이다 — 형제 트리가 실제 도달 경로다.

    **CI 에서는 틀린 답이다.** 러너는 자기 레포 하나만 체크아웃하므로 그 디렉터리가
    없고, 형제 레인은 `pip install -e '.[test]'` 가 git URL 로 받아 온다. 그래서
    게이트는 필요한 것을 **가진 채로** 「없다」며 빠졌고, 경계 계약 세 개가 CI 에서
    한 번도 검사되지 않았다. 84e0960 이 도구 선언을 고친 뒤에도 이 팔만 계속 빠져
    사유만 「미설치」에서 「형제 레인 없다」로 바뀐다 — 두 사유 모두 초록으로 보인다.

    이 봉인은 완화의 **근거**를 지킨다: 임포트로 답해도 되는 이유는 이 레인이 형제
    레인을 정식 의존으로 선언하기 때문이다. 누군가 그 선언을 지우면 임포트 기반
    선행조건은 근거를 잃고, 그날 이 팔이 이름을 대며 멈춘다.

    ⚠️ 도구의 «존재»가 아니라 **선언**을 본다 — `TestTheGatesCanActuallyRunHere` 와
    같은 이유다(존재를 보면 설치된 기계에서만 초록이 되어 같은 함정을 되풀이한다).
    """

    def test_the_contracts_lane_is_declared_as_a_dependency(self):
        import tomllib
        data = tomllib.loads((REPO_ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        names = {
            re.split(r'[<>=!\[@ ]', item, maxsplit=1)[0].strip().lower()
            for item in data['project']['dependencies']
        }
        self.assertIn(
            'fcc-test-contracts', names,
            "`[project].dependencies` 에 fcc-test-contracts 가 없다 — 그러면 "
            "`_contracts_are_reachable()` 이 참이라는 보장이 사라지고, 경계 계약 "
            "게이트가 형제 트리를 가진 기계에서만 돌게 된다(= CI 에서는 안 돈다).")


class TestTheGatesActuallyRun(unittest.TestCase):
    """선언이 아니라 «실행». 도구가 없으면 skip 하되, 그 skip 이 보이게 한다.

    ⚠️ **종료코드만 보지 않는다.** 잘못된 진입점 · 수집 0개 · 러너 미배정은 전부
    「exit=0, 한 일 없음」으로 나타나고, 그것은 「위반 없음」과 구분되지 않는다.
    그래서 각 팔은 도구가 **일했다는 증거**(검사한 파일 수 · 분석한 의존 수)를
    출력에서 함께 확인한다. 이 레포가 같은 계열의 값을 이미 세 번 치렀다 —
    `lane_check` 의 `--continue-on-collection-errors`, `checks.yml` 의 러너 미배정,
    그리고 위의 `-m importlinter.cli`.
    """

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv, cwd=str(REPO_ROOT), env=_tool_env(),
            capture_output=True, text=True, timeout=900,
        )

    @unittest.skipIf(importlib.util.find_spec('mypy') is None,
                     'mypy 미설치 — 게이트를 돌리려면: pip install mypy')
    def test_the_strict_layers_have_no_untyped_defs(self):
        """선언된 «모든» strict 층을 실제로 돌린다.

        ⚠️ 층 목록은 여기 다시 적지 않고 ``STRICT_PACKAGES`` 에서 파생한다. 손으로
        두 번 적으면 `mypy.ini` 에 층을 더하고 이 검사는 옛 층만 돌리는 날이 온다 —
        그때 게이트는 **초록인 채로 새 층을 안 본다.**
        """
        for flag, package in STRICT_TARGETS:
            with self.subTest(package=package):
                done = self._run([sys.executable, '-m', 'mypy', flag, package])
                report = f'{done.stdout}\n{done.stderr}'
                # 증거 먼저 — 「한 파일도 안 봤다」가 「오류 없다」로 읽히지 않게.
                checked = re.search(r'(\d+) source files?', done.stdout)
                self.assertIsNotNone(
                    checked,
                    f'{package}: mypy 가 검사한 파일 수를 보고하지 않았다 — 돌지 않았다:\n{report}')
                # ⚠️ 한때 이 단언은 `> 0` 이었다. 그것이 통과시키는 것 둘을 이 레포가
                #    이미 실측했다: (1) `[mypy-fcc_test_platform]` 같은 점-별 없는 절은
                #    `checked 1 source file` 을 «성실히» 보고한다 — 본 것은
                #    `__init__.py` 하나다(PR #139). (2) `py.typed` 유무로 오류가
                #    57 대 0 으로 갈릴 때도 **검사한 파일 수는 양쪽 75 로 같다**.
                #    즉 `> 0` 은 「돌았다」를 말할 뿐 「무엇을 봤다」를 말하지 못한다.
                #    옆의 import-linter 팔은 이미 `Analyzed N files, M dependencies` 로
                #    규모까지 본다 — 새 규율이 아니라 «이미 있는 규율의 구멍»이다.
                self.assertEqual(
                    _expected_module_count(package), int(checked.group(1)),
                    f'{package}: mypy 가 «다른 수»를 검사했다 — 트리에서 파생한 수와 '
                    f'맞지 않는다. 모듈이 늘/줄었으면 두 수가 «같이» 움직이므로 이 '
                    f'red 는 그것이 아니다: 게이트가 조용히 좁아졌거나, '
                    f'`__init__.py` 없는 디렉터리가 생겼거나 사라진 것이다.\n{report}')
                self.assertEqual(
                    0, done.returncode,
                    f'{package} strict 가 깨졌다 (설계서 S1):\n{report}')
                _publish_evidence(f'mypy 게이트가 돌았다 — {package}: {checked.group(0)}')

    @unittest.skipIf(importlib.util.find_spec('mypy') is None,
                     'mypy 미설치 — 게이트를 돌리려면: pip install mypy')
    def test_the_whole_package_is_type_checked(self):
        """**패키지 «전체»를 한 번 부른다** — 위 팔이 못 보는 자리가 여기다.

        위 `test_the_strict_layers_have_no_untyped_defs` 는 `mypy.ini` 의 절마다
        «따로» 부른다. 그 형태의 사각지대는 「절 목록에 없는 모듈」이고, 그것은
        빨강도 초록도 아닌 **침묵**이다 — 실측 2026-09-07(main@9565be6, CI 와 같은
        리그: contracts 0.1.26 / kernel-v0.5.4 / py.typed 있음 / fastapi 있음)::

            절마다 부르면    domain 0 · infrastructure 0 · application 0 · api 0
            패키지 전체면    45건 / 16파일   ← 전부 최상위

        ⚠️ **이 팔은 strict 를 요구하지 않는다.** 45건은 `disallow_untyped_defs` 와
        무관한 실제 타입 오류였고(`arg-type` 16 · `dict-item` 7 · `operator` 6 ·
        `index` 6 · `assignment` 5 · `attr-defined` 3 · 나머지 2), 부족했던 것은
        설정이 아니라 **호출**이었다. 그래서 답도 설정(`[mypy-…*]` 절 추가 =
        `no-untyped-def` 183건)이 아니라 호출이다. 층별 strict 확대는 별개의 질문이고
        `mypy.ini` 가 그 순서를 이미 적어 두었다.

        ⚠️ **장부를 두지 않는다.** 「지적 45건」 같은 스칼라를 적으면 검사를 «넓히는»
        개선이 회귀처럼 빨개진다. 대신 여집합을 박는다 — 「위반 == ∅」(returncode 0)
        과 「대상이 있는가」 등호(`_expected_module_count`). 참고 형태:
        `tests/test_operation_table_read_form_axis.py`.
        """
        done = self._run([sys.executable, '-m', 'mypy', '-p', WHOLE_PACKAGE])
        report = f'{done.stdout}\n{done.stderr}'
        checked = re.search(r'(\d+) source files?', done.stdout)
        self.assertIsNotNone(
            checked,
            f'{WHOLE_PACKAGE}: mypy 가 검사한 파일 수를 보고하지 않았다 — 돌지 '
            f'않았다:\n{report}')
        # 「대상이 있는가」 — 수가 아니라 «파생과의 등호»다. 아래 위반 단언이
        # 초록일 때 이 등호가 「그 초록이 무엇을 보고 난 초록인가」를 답한다.
        self.assertEqual(
            _expected_module_count(WHOLE_PACKAGE), int(checked.group(1)),
            f'{WHOLE_PACKAGE}: mypy 가 «다른 수»를 검사했다 — 트리에서 파생한 수와 '
            f'맞지 않는다. 이 팔이 좁아지면 최상위 모듈이 다시 침묵으로 돌아간다.'
            f'\n{report}')
        # 「위반 == ∅」
        self.assertEqual(
            0, done.returncode,
            f'{WHOLE_PACKAGE} 전량 타입 검사가 깨졌다 — 이 팔이 도입되기 «전»에는 '
            f'이 자리가 빨강도 초록도 아니었다(호출되지 않았다). baseline 을 늘리지 '
            f'말고 코드를 고쳐라:\n{report}')
        _publish_evidence(
            f'mypy 게이트가 돌았다 — {WHOLE_PACKAGE} 전량: {checked.group(0)}')

    @unittest.skipIf(importlib.util.find_spec('importlinter') is None,
                     'import-linter 미설치 — 게이트를 돌리려면: pip install import-linter')
    def test_the_three_contracts_hold(self):
        if not (_sibling_lane().is_dir() or _contracts_are_reachable()):
            self.skipTest(
                f'형제 레인에 도달할 수 없다 — 트리도 없고({_sibling_lane()}) '
                f'설치본도 없다. 이 상자는 혼자 돌지 않는다')
        done = self._run([sys.executable, '-c', _LINT_IMPORTS_ENTRY, '--no-cache'])
        report = f'{done.stdout}\n{done.stderr}'
        analyzed = re.search(r'Analyzed (\d+) files, (\d+) dependencies', done.stdout)
        self.assertIsNotNone(
            analyzed, f'import-linter 가 분석 규모를 보고하지 않았다 — 돌지 않았다:\n{report}')
        self.assertGreater(
            int(analyzed.group(2)), 0,
            f'의존 0건을 분석했다 — 그래프가 비었다면 어떤 계약도 깨질 수 없다:\n{report}')
        self.assertEqual(
            3, done.stdout.count(' KEPT'),
            f'세 계약이 모두 KEPT 로 보고되지 않았다 (설계서 S2):\n{report}')
        self.assertEqual(
            0, done.returncode, f'경계 계약이 깨졌다 (설계서 S2):\n{report}')
        _publish_evidence(
            f'import-linter 게이트가 돌았다 — {analyzed.group(0)} · '
            f'KEPT {done.stdout.count(" KEPT")}회')


#: 「이 이름이 게이트가 도는 환경에서 무엇으로 해소되는가」를 묻는 탐침.
#: ⚠️ 이 테스트 프로세스가 아니라 **하위 프로세스**에서, `_tool_env()` 와 같은
#:    `PYTHONPATH` 로 묻는다 — mypy 가 보는 것을 재야 하는데 그 둘이 다를 수 있기
#:    때문이다(`_tool_env()` 는 형제 «트리»를 설치본 앞에 세운다. 개발 체크아웃에는
#:    그 트리가 있고 CI 에는 없다).
_MODULE_PROBE = (
    'import importlib.util, json, pathlib, sys\n'
    'spec = importlib.util.find_spec(sys.argv[1])\n'
    'roots = [str(p) for p in (spec.submodule_search_locations or [])] if spec else []\n'
    'print(json.dumps({\n'
    '    "found": spec is not None,\n'
    '    "origin": (spec.origin if spec else None),\n'
    '    "roots": roots,\n'
    '    "markers": [str(pathlib.Path(r) / "py.typed") for r in roots\n'
    '                if (pathlib.Path(r) / "py.typed").is_file()],\n'
    '}))\n'
)


class TestTheRigCanMeasureWhatTheGateClaims(unittest.TestCase):
    """게이트가 «무엇으로» 재는가 — 도구의 **존재**가 아니라 **능력**이다.

    ⚠️ 위 `TestTheGatesCanActuallyRunHere` 와 층위가 다르다. 저 클래스는
    「도구가 «선언»돼 있는가」를 묻고(존재를 물으면 도구가 깔린 기계에서만 초록이
    되니까), 이 클래스는 「그 도구가 이 리그에서 «볼 수 있는가»」를 묻는다.
    핀 패리티가 「신원」을 묻는 것과도 다르다 — 같은 태그가 마커를 실을 수도 안
    실을 수도 있고, 그것은 태그 이름이 아니라 «배포판»의 성질이다.

    ⚠️ **못 보면 skip 이 아니라 red 다.** 도구/의존 부재를 초록으로 만들면 「안
    봤다」와 「위반 없다」가 같은 값이 된다 — 이 파일이 본문 곳곳에서 경고하는
    바로 그 형태다. 여기서 red 를 내는 것의 값은 「이 기계의 측정을 믿지 마라」를
    측정 «전»에 말해 준다는 것이다.

    실측 2026-09-07 — 이 축이 없으면 무엇이 조용해지는가:

      · **py.typed**: 같은 트리(main@9565be6)에서 커널·contracts 배포판의 마커
        파일만 지웠다 넣었다 하니 `mypy -p fcc_test_platform` 이 44 대 45 였다.
        그 +1 은 `api_composition.py` 의 실제 결함(사본 Protocol 이 SSOT 와
        갈라져 `fetchall() -> list` 대 `-> Sequence`)이었고, 마커가 없는 동안
        커널 타입이 `Any` 라서 **존재하는데 보이지 않았다.** 형제 세션 실측은
        더 크다 — `application` 층이 57 대 0, 그런데 **검사한 파일 수는 양쪽 75 로
        같다.** 즉 파일 수 등호로도 이 축은 못 잡는다.

      · **fastapi**: `mypy.ini` 의 `api` 절이 적어 둔 실측 — fastapi 없는 리그에서
        21건이던 것이 CI 와 같은 설치에서 60건이었다(`Request` 가 `Any` 를 벗으면서
        `_normalize_request_body_args` 의 파급이 비로소 보였다).

    두 경우 모두 게이트는 **돌았고, 초록이었고, 파일 수도 맞았다.**
    """

    def _probe(self, module_name: str) -> dict:
        done = subprocess.run(
            [sys.executable, '-c', _MODULE_PROBE, module_name],
            cwd=str(REPO_ROOT), env=_tool_env(),
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(
            0, done.returncode,
            f'{module_name} 탐침이 실행되지 못했다:\n{done.stdout}\n{done.stderr}')
        return json.loads(done.stdout)

    def test_fastapi_is_importable_where_the_gate_runs(self):
        """`api` 층 측정은 fastapi 가 «보이는» 리그에서만 참이다."""
        probe = self._probe('fastapi')
        self.assertTrue(
            probe['found'],
            'fastapi 를 이 리그에서 해소하지 못했다 — 그러면 `api` 층 측정이 '
            'CI 와 다른 답을 낸다(실측: 21건 대 60건). `[project].dependencies` 가 '
            '이미 fastapi 를 선언하므로 이것은 「선언 누락」이 아니라 「설치 누락」이다: '
            "`pip install -e '.[test]'` 로 리그를 다시 세워라. "
            '⚠️ 이 자리를 skip 으로 바꾸지 마라 — 그러면 「안 봤다」가 초록이 된다.')

    def test_the_kernel_the_gate_reads_ships_py_typed(self):
        """마커가 없으면 커널 타입이 `Any` 가 되고, 갈라짐이 조용해진다."""
        for name in ('fcc_test_kernel', 'fcc_test_contracts'):
            with self.subTest(distribution=name):
                probe = self._probe(name)
                self.assertTrue(
                    probe['found'],
                    f'{name} 을 이 리그에서 해소하지 못했다 — 형제 레인이 정식 '
                    f'의존인데 설치되지 않았다.')
                self.assertTrue(
                    probe['markers'],
                    # ⚠️ 「어디까지 봤나」를 함께 적는다. 부재 주장은 그것 없이는
                    #    다음 사람이 검증할 수 없다.
                    f'{name} 배포판에 `py.typed` 가 없다 — 그러면 이 레인이 그 '
                    f'패키지의 타입을 «전부 Any 로» 본다. 게이트는 돌고, 초록이고, '
                    f'파일 수까지 맞는 채로 갈라짐을 못 본다.\n'
                    f'  찾아본 자리: {probe["roots"]}\n'
                    f'  이 축이 red 라면 물을 것은 「검사가 틀렸나」가 아니라 '
                    f'「핀이 가리키는 태그가 마커를 싣는가」다 '
                    f'(kernel-v0.5.0 은 0개, v0.5.1 부터 1개).')
        _publish_evidence('리그 축: 커널·contracts 배포판이 py.typed 를 싣는다')


if __name__ == '__main__':
    unittest.main()
