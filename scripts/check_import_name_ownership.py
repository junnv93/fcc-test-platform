#!/usr/bin/env python3
"""설치된 배포판이 선언한 최상위 import 이름이 **실제로 그 배포판에서 해소되는가** (2026-09-03).

`check_auth_mode_pairing.py` · `check_central_provider_id_pairing.py` 의 형제다.
그 둘이 *운영자가 env 에 무엇을 적었나* 를 묻는다면, 이것은 다른 축을 묻는다:

    같은 import 이름을 두 곳이 주장할 때, 지금 이 인터프리터에서 누가 이기는가.

**왜 이 축이 필요한가 — 실측 2026-09-03.** 두 레인이 최상위 이름 **넷**을 동시에
주장하고 있다: ``domain`` · ``application`` · ``infrastructure`` · ``logger_config``.
그리고 해소 결과가 **기계마다 다르다** — 챔버 PC 에서는 provider 저장소의 ``src/`` 가
넷 다 이기고, 중앙 PC 에서는 설치된 휠이 넷 다 이긴다.

그 상태에서 실제로 갈라졌다. ``domain/`` 의 양쪽 공통 87파일 중 **4개가 내용이 다르고**
둘은 기능 차이였다(``measurement_history`` 의 측정대상 축 · ``sample_inventory_policy``
의 ``snapshot_measurement_identity``). **같은 import 이름으로 서로 다른 코드가 돌았고,
그것을 보고하는 검사가 0건이었다.** import 는 성공하고, 테스트도 통과하고, 어느 쪽도
오류를 내지 않는다 — 그저 다른 코드가 돈다.

⚠️ **이것은 「사본이 갈라진다」의 기계적 형태다.** 사본 축은 *파일이 두 벌인가* 를 묻고,
이 축은 *지금 어느 벌이 도는가* 를 묻는다. 후자를 보지 않으면 전자를 고쳐도
「고쳤다」를 증명할 수 없다.

두 축을 묻는다. 둘 다 **파생**이다 — 배포판 목록도 이름 목록도 하드코딩하지 않는다.

**축 A — 가려짐(shadowing).** 설치된 배포판이 선언한 이름이 그 배포판의 설치 위치
**밖**에서 해소되면, 경로에 놓인 무언가가 배포판을 가리고 있다. 공식 지침
(`packaging.python.org` / *src layout vs flat layout*)이 이름 붙인 형태다 —
*"인터프리터가 현재 디렉터리를 import 경로 첫 항목에 넣는다. 로컬 패키지가 설치된
패키지와 이름이 같으면 로컬이 쓰인다."*

**축 B — namespace 공유(관측).** 두 배포판이 같은 이름을 주장하는 것 자체는 결함이
아니다 — PEP 420 namespace 패키지가 바로 그것을 위해 있다. 공식 지침이 위험 조건을
명시한다: *"그 namespace 를 쓰는 **모든** 배포판이 ``__init__.py`` 를 생략해야 한다.
하나라도 그러지 않으면 namespace 논리가 실패하고 다른 하위 패키지들이 import 불가가
된다."*

⚠️ **NAMESPACE_AXIS_LIMITATION — 이 축은 관측이지 위반이 아니다.** 실측 2026-09-03:
챔버 venv 에서 이 조건에 걸리는 것이 ``PySide6`` 였다(``PySide6`` ·
``PySide6_Essentials`` · ``PySide6_Addons`` 셋이 한 디렉터리를 함께 쓴다). 그것은
상류가 **함께 설치되도록 설계한** 형태이고 실제로 깨지지 않는다. 그런데 메타데이터만
보고 *「함께 설계된 가족」* 과 *「우연한 충돌」* 을 구분할 방법이 없다.

구분할 수 없는 것을 빨간불로 만들면 **오탐을 내는 게이트**가 되고, 오탐을 내는 게이트는
삭제된다 — 이 저장소가 세 번 낸 결론이다. 그래서 이 축은 **보고만 하고 종료 코드를
바꾸지 않는다.** 이 문장이 사라지면 다음 세션이 이것을 방어층으로 믿는다.

종료 코드: 0 일치 · 1 위반 · 2 판정 불가.
**2 를 0 으로도 1 로도 접지 않는다** — 「읽을 수 없다」와 「틀렸다」는 다른 사실이다.
(형제 스크립트들이 이 규율을 이미 갖는다. 그 둘은 2026-09-03 에 exit 1 로 죽어
「검사가 죽음」이 「값이 틀림」과 같은 답으로 보이던 것을 고쳤다.)

사용법::

    python3 scripts/check_import_name_ownership.py
    python3 scripts/check_import_name_ownership.py --json

⚠️ **이 검사는 「지금 이 인터프리터」에 대해서만 참이다.** 그것이 요점이다 — 같은
저장소가 두 기계에서 다른 답을 내는 것이 이 축이 존재하는 이유이므로, **양쪽에서
따로 돌려야 한다.**
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Mapping

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_UNDETERMINED = 2

#: 이 검사가 판정 대상에서 빼는 이름. **레인 이름이 아니라 구조적 사유**만 넣는다.
#: 표준 라이브러리/인터프리터가 소유하는 이름은 배포판 선언과 무관하게 해소된다.
_STDLIB_TOP_LEVEL = frozenset(sys.stdlib_module_names)


def _is_import_name(name: str) -> bool:
    """``packages_distributions()`` 가 내는 것이 전부 import 이름은 아니다.

    실측 2026-09-03(챔버 venv): 그 API 가 ``__pycache__`` 와 ``PySide6/QtCore`` 를
    함께 냈다 — 배포판 ``RECORD`` 에 그런 항목이 있으면 그대로 나온다. 둘 다
    **import 문에 쓸 수 없는 문자열**이라 판정 대상이 아니다.

    ⚠️ 이것은 면제 목록이 아니라 **정의**다. 이름을 하나씩 빼기 시작하면 다음 오탐이
    또 목록에 들어가고, 목록이 된 게이트는 꺼진다. 판정항은 *「import 문에 쓸 수 있는
    식별자인가」* 하나다.
    """
    return name.isidentifier() and not name.startswith('__')


class Undetermined(Exception):
    """판정할 수 없다 (→ exit 2)."""


@dataclasses.dataclass(frozen=True)
class NameVerdict:
    """최상위 이름 하나에 대한 판정."""

    name: str
    declared_by: tuple[str, ...]
    resolved_at: str | None
    owner_root: str | None
    #: 'ok' · 'shadowed' · 'namespace_shared' · 'editable' · 'unresolvable'
    state: str
    detail: str = ''

    @property
    def is_violation(self) -> bool:
        return self.state == 'shadowed'

    @property
    def is_undetermined(self) -> bool:
        return self.state in ('editable', 'unresolvable')

    @property
    def is_observation(self) -> bool:
        return self.state == 'namespace_shared'


def _packages_distributions() -> Mapping[str, list[str]]:
    """``최상위 이름 -> [배포판…]``. 표준 라이브러리가 이 질문에 답한다."""
    try:
        from importlib.metadata import packages_distributions
    except ImportError as exc:  # pragma: no cover — Python 3.10+ 에는 있다
        raise Undetermined(
            f'importlib.metadata.packages_distributions 를 쓸 수 없다 — {exc}'
        ) from exc
    try:
        return packages_distributions()
    except Exception as exc:  # noqa: BLE001
        raise Undetermined(f'설치된 배포판을 열거하지 못했다 — {exc}') from exc


def _distribution_roots(names: Iterable[str]) -> dict[str, Path]:
    """``배포판 이름 -> 설치 루트``. 해소 위치를 그 안인지로 판정하기 위한 것."""
    from importlib.metadata import distribution

    roots: dict[str, Path] = {}
    for dist_name in names:
        if dist_name in roots:
            continue
        try:
            dist = distribution(dist_name)
            root = Path(str(dist.locate_file(''))).resolve()
        except Exception:  # noqa: BLE001 — 하나 못 읽어도 나머지는 판정한다
            continue
        roots[dist_name] = root
    return roots


def _is_editable(dist_name: str) -> bool:
    """editable 설치는 파일이 설치 루트 밖에 있는 것이 **정상**이다.

    그것을 가려짐으로 부르면 개발 환경 전체가 빨간불이 되고, 그런 검사는 삭제된다.
    """
    from importlib.metadata import distribution

    try:
        raw = distribution(dist_name).read_text('direct_url.json')
    except Exception:  # noqa: BLE001
        return False
    if not raw:
        return False
    try:
        return bool((json.loads(raw).get('dir_info') or {}).get('editable'))
    except (TypeError, ValueError):
        return False


def _ships_init_py(name: str, root: Path) -> bool:
    """그 배포판이 이 이름에 대해 ``__init__.py`` 를 싣는가 (PEP 420 판정항)."""
    return (root / name / '__init__.py').is_file()


def judge_name(
    name: str,
    dists: list[str],
    roots: Mapping[str, Path],
    *,
    find_spec=importlib.util.find_spec,
) -> NameVerdict:
    """이름 하나를 판정한다. **관측(해소 위치)은 주입 가능하다** — 봉인이 실제
    인터프리터 상태에 기대지 않고 두 축을 각각 시험할 수 있어야 한다."""
    owners = tuple(sorted(set(dists)))
    known = [d for d in owners if d in roots]

    # ── 축 B: namespace 무결성 ────────────────────────────────────────────
    # 둘 이상이 주장하는 것 자체는 정상(PEP 420). 하나라도 __init__.py 를 실으면
    # 공식 지침이 말한 대로 namespace 논리가 깨진다.
    shared_note = ''
    if len(known) > 1:
        shipping = [d for d in known if _ships_init_py(name, roots[d])]
        if shipping:
            shared_note = (
                f'배포판 {len(known)}개가 이 이름을 주장하고 {shipping} 가 '
                f'__init__.py 를 싣는다'
            )

    try:
        spec = find_spec(name)
    except Exception as exc:  # noqa: BLE001 — 부모 패키지 실행 실패 등
        return NameVerdict(name, owners, None, None, 'unresolvable', f'{type(exc).__name__}: {exc}')
    if spec is None:
        return NameVerdict(name, owners, None, None, 'unresolvable', '해소되지 않는다')

    locations = list(spec.submodule_search_locations or [])
    resolved = locations[0] if locations else (spec.origin or '')
    if not resolved or resolved == 'built-in':
        return NameVerdict(name, owners, resolved or None, None, 'unresolvable', '위치가 없다')
    resolved_path = Path(resolved).resolve()

    # ── 축 A: 가려짐 ──────────────────────────────────────────────────────
    for dist_name in known:
        root = roots[dist_name]
        if root == resolved_path or root in resolved_path.parents:
            if shared_note:
                return NameVerdict(
                    name, owners, str(resolved_path), str(root),
                    'namespace_shared', shared_note,
                )
            return NameVerdict(name, owners, str(resolved_path), str(root), 'ok')

    editable = [d for d in owners if _is_editable(d)]
    if editable:
        return NameVerdict(
            name, owners, str(resolved_path), None, 'editable',
            f'{editable} 가 editable 설치라 설치 루트 밖에서 해소되는 것이 정상이다',
        )
    return NameVerdict(
        name, owners, str(resolved_path), str(roots[known[0]]) if known else None,
        'shadowed',
        f'{owners} 가 선언한 이름인데 그 배포판 밖({resolved_path})에서 해소된다 — '
        '경로에 놓인 것이 배포판을 가리고 있다',
    )


def judge_all(
    packages: Mapping[str, list[str]],
    roots: Mapping[str, Path],
    *,
    find_spec=importlib.util.find_spec,
) -> list[NameVerdict]:
    """판정 대상 전량. 표준 라이브러리 이름은 뺀다(배포판 선언과 무관하게 해소된다)."""
    return [
        judge_name(name, dists, roots, find_spec=find_spec)
        for name, dists in sorted(packages.items())
        if name not in _STDLIB_TOP_LEVEL and _is_import_name(name)
    ]


def render(verdicts: list[NameVerdict]) -> tuple[int, str]:
    """판정 → (종료 코드, 사람이 읽는 보고)."""
    # ⚠️ 비-공허성 ① — 판정 대상이 0건이면 이 검사는 아무것도 묻지 않은 것이다.
    if not verdicts:
        return EXIT_UNDETERMINED, (
            'import 이름 소유권: 판정 불가 — 설치된 배포판이 선언한 최상위 이름이 '
            '0건이다.\n'
            '  이것은 「위반 없음」이 아니다. 이 인터프리터에 배포판이 설치돼 있는지 '
            '확인하라.'
        )

    violations = [v for v in verdicts if v.is_violation]
    undetermined = [v for v in verdicts if v.is_undetermined]
    observations = [v for v in verdicts if v.is_observation]
    ok = [v for v in verdicts if v.state == 'ok']

    lines: list[str] = []
    for v in violations:
        lines.append(f'  위반     {v.name}  — {v.detail}')
    for v in undetermined:
        lines.append(f'  판정불가 {v.name}  — {v.detail}')
    for v in observations:
        lines.append(f'  관측     {v.name}  — {v.detail} (종료 코드를 바꾸지 않는다)')

    # ⚠️ 비-공허성 ② — 몇 개를 실제로 관측했는지 매번 말한다. 「초록」만 찍으면
    # 아무것도 안 본 초록과 구분되지 않는다.
    tally = (
        f'import 이름 소유권: 판정 {len(verdicts)}건 '
        f'(일치 {len(ok)} · 위반 {len(violations)} · 판정 불가 {len(undetermined)}'
        f' · 관측 {len(observations)})'
    )

    if violations:
        body = '\n'.join([tally, *lines, '',
                          '  같은 import 이름을 두 곳이 주장하면 어느 쪽이 도는지는 '
                          'sys.path 순서가 정한다.',
                          '  import 는 성공하고 테스트도 통과한다 — 그저 다른 코드가 돈다.'])
        return EXIT_VIOLATION, body
    if undetermined:
        return EXIT_UNDETERMINED, '\n'.join([tally, *lines, '',
                                             '  판정 불가는 통과가 아니다.'])
    return EXIT_OK, '\n'.join([tally, *lines]) if lines else tally


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--json', action='store_true', help='판정을 JSON 으로 낸다')
    args = parser.parse_args(argv)

    packages = _packages_distributions()
    roots = _distribution_roots(
        name for dists in packages.values() for name in dists
    )
    verdicts = judge_all(packages, roots)
    code, report = render(verdicts)

    if args.json:
        print(json.dumps({
            'exit_code': code,
            'interpreter': sys.executable,
            'cwd': os.getcwd(),
            'verdicts': [dataclasses.asdict(v) for v in verdicts],
        }, ensure_ascii=False, indent=2))
        return code

    print(report, file=sys.stdout if code == EXIT_OK else sys.stderr)
    return code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Undetermined as exc:
        print(f'import 이름 소유권: 판정 불가 — {exc}', file=sys.stderr)
        raise SystemExit(EXIT_UNDETERMINED) from exc
