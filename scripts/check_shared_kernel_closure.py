#!/usr/bin/env python3
"""두 레인이 **함께 도달하는** 모듈 폐포를 잰다 — 목록이 아니라 폐포다 (2026-09-03).

`check_import_name_ownership.py` 의 형제다. 그것이 *지금 어느 벌이 도는가* 를 묻는다면,
이것은 그 앞의 질문을 묻는다:

    두 레인이 실제로 **함께** 쓰는 것이 무엇인가.

**왜 이 축이 필요한가.** 공유 코드의 소유권을 옮기려면 먼저 «무엇이 공유인가» 를 알아야
하는데, 그 답을 **손으로 세면 세는 사람의 우연이 들어간다.** 실측 2026-09-03: 한 세션이
「15개」라고 보고했는데 그것은 그 세션이 복사한 집합이었고, 폐포로 재니 **53개**였다.
방향도 양쪽이었다 — 중앙만 도달 47 · provider 만 282 · **양쪽 53**.

이 저장소 계열이 이미 같은 결론을 냈다(`repository-split.md` §Shared Kernel Delivery,
2026-08-12): *"명백해 보이는 수리(계층을 레인으로 승격해 통째 납품)는 기각됐다 —
platform 이 실제로 부르는 것은 58뿐이고 나머지 157은 전부 측정 판단이다. …
**정공은 목록이 아니라 폐포다.**"* 그 기전은 레포 분할과 함께 퇴역했지만 **질문은 남았고**,
파일을 남의 저장소로 복사하기 시작하면 그 축이 즉시 되살아난다고 그 규칙이 적었다.
2026-09-03 에 정확히 그렇게 됐다.

**무엇을 판정하나.** 폐포의 크기와 구성을 기준선과 대조한다(ratchet). 기준선이 없으면
쓰고, 있으면 **늘어난 것만** 이름으로 보고한다 — 줄어드는 것은 이관이 진행된 것이므로
정상이다.

⚠️ **줄었다고 자동으로 기준선을 낮추지 않는다.** 관측값으로 덮으면 「이관했다」와
「폐포 계산이 깨졌다」가 같은 초록이 된다(레인 기준선과 같은 규율). `--write-baseline`
은 사람이 친다.

⚠️ **provider 트리가 없으면 판정 불가다.** 이 검사는 저장소 둘을 필요로 하고, 하나가
없는 체크아웃이 정상 상태다(CI · 새 클론). 「없으니 0개」로 답하면 폐포가 사라진 것과
provider 가 없는 것이 같은 값이 된다.

종료 코드: 0 기준선과 일치(또는 축소) · 1 폐포가 늘었다 · 2 판정 불가.

사용법::

    python3 scripts/check_shared_kernel_closure.py --provider-root /path/to/provider
    python3 scripts/check_shared_kernel_closure.py --provider-root … --write-baseline
    python3 scripts/check_shared_kernel_closure.py --provider-root … --json
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Mapping

EXIT_OK = 0
EXIT_GREW = 1
EXIT_UNDETERMINED = 2

_REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = _REPO_ROOT / 'docs' / 'platform' / 'shared_kernel_closure.baseline.json'

#: 두 레인이 같은 이름으로 주장하는 최상위 패키지. **파생이다** — 이 저장소가 배포하는
#: 것과 provider 트리가 갖는 것의 교집합에서 나온다(아래 ``discover_shared_tops``).
_CENTRAL_SEED_PACKAGE = 'fcc_test_platform'

#: provider 어휘 표지. ⚠️ **docstring 은 세지 않는다** — 어휘는 타입이 지는 것이고
#: 산문이 언급하는 것이 아니다. 실측 2026-09-03: 이 구분 없이 세면 16, 있으면 9다.
_PROVIDER_VOCAB = re.compile(
    r'\b(BT|BLE|DTS|UNII|WLAN|antenna|modulation|EIRP|dBm|spectrum|analyzer|'
    r'GPIB|VISA|appium|MPTool|QRCT)\b'
)


class Undetermined(Exception):
    """판정할 수 없다 (→ exit 2)."""


# ── 공통: import 폐포 ──────────────────────────────────────────────────────────

def _imports(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
    return out


def _resolve(root: Path, module: str) -> Path | None:
    flat = root / (module.replace('.', '/') + '.py')
    pkg = root / module.replace('.', '/') / '__init__.py'
    return flat if flat.is_file() else (pkg if pkg.is_file() else None)


def _closure(root: Path, seeds: Iterable[Path], tops: frozenset[str]) -> set[str]:
    """``seeds`` 에서 출발해 ``tops`` 아래로 도달하는 모듈의 **추이 폐포**.

    ⚠️ ``__init__`` 체인이 폐포에 들어온다 — 패키지를 지나가려면 그 파일이 실린다.
    (`repository-split.md` 가 이름 붙인 네 실패 모드 중 첫째다.)
    """
    seen: set[Path] = set()
    reached: set[str] = set()
    queue = list(seeds)
    while queue:
        path = queue.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            source = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        for module in _imports(source):
            if module.split('.')[0] not in tops:
                continue
            target = _resolve(root, module)
            if target is None:
                continue
            reached.add(target.relative_to(root).as_posix())
            queue.append(target)
    return reached


def discover_shared_tops(central_root: Path, provider_src: Path) -> frozenset[str]:
    """두 트리가 **같은 이름으로** 갖는 최상위 import 이름. 손 목록이 아니다."""
    def tops(root: Path) -> set[str]:
        out = set()
        for child in root.iterdir():
            if child.is_dir() and not child.name.startswith(('.', '__')) and any(child.rglob('*.py')):
                out.add(child.name)
            elif child.suffix == '.py':
                out.add(child.stem)
        return out
    return frozenset(tops(central_root) & tops(provider_src))


def measure(central_root: Path, provider_src: Path) -> dict:
    """두 레인의 폐포와 그 교집합. **관측만 한다** — 판정은 호출자가 한다."""
    tops = discover_shared_tops(central_root, provider_src)
    if not tops:
        raise Undetermined(
            f'두 트리가 공유하는 최상위 이름이 0개다 ({central_root} · {provider_src}) — '
            '경로가 맞는지 확인하라. 0개는 「공유가 없다」가 아니라 「못 찾았다」일 수 있다.'
        )

    central_seed_root = central_root / _CENTRAL_SEED_PACKAGE
    if not central_seed_root.is_dir():
        raise Undetermined(f'{_CENTRAL_SEED_PACKAGE}/ 를 찾지 못했다: {central_seed_root}')
    central_seeds = [p for p in central_seed_root.rglob('*.py') if '__pycache__' not in str(p)]

    provider_seeds = [
        p for p in provider_src.rglob('*.py')
        if '__pycache__' not in str(p)
        and p.relative_to(provider_src).parts[0] not in tops
    ]
    if not provider_seeds:
        raise Undetermined(f'provider 씨앗이 0개다: {provider_src}')

    central = _closure(central_root, central_seeds, tops)
    provider = _closure(provider_src, provider_seeds, tops)
    shared = sorted(central & provider)

    #: 이 계열이 소유하는 배포판 이름. **third-party 와 구분한다** — 규칙이 제약하는
    #: 것은 서드파티이지 형제 레인이 아니고, 둘을 한 숫자로 합치면 「제약에 걸린다」와
    #: 「같은 계열을 쓴다」가 같은 값이 된다(실측 2026-09-03: 합치면 4, 나누면 0).
    FIRST_PARTY = frozenset({'fcc_test_contracts', 'fcc_test_platform'})
    vocab = []
    external = {}
    lane_deps = {}
    for rel in shared:
        path = central_root / rel
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                  ast.AsyncFunctionDef))
                    and ast.get_docstring(node) is not None):
                node.body[0].value.value = ''
        ast.fix_missing_locations(tree)
        if _PROVIDER_VOCAB.search(ast.unparse(tree)):
            vocab.append(rel)
        for module in _imports(path.read_text(encoding='utf-8')):
            top = module.split('.')[0]
            if top in sys.stdlib_module_names or top in tops:
                continue
            bucket = lane_deps if top in FIRST_PARTY else external
            bucket[rel] = sorted(set(bucket.get(rel, [])) | {top})

    return {
        'shared_tops': sorted(tops),
        'central_only': len(central - provider),
        'provider_only': len(provider - central),
        'shared': shared,
        'provider_vocabulary': sorted(vocab),
        'third_party_dependencies': external,
        'sibling_lane_dependencies': lane_deps,
    }


def judge(observed: Mapping, baseline: Mapping | None) -> tuple[int, str]:
    shared = list(observed['shared'])
    # ⚠️ 비-공허성 — 공유가 0개면 이 검사는 아무것도 묻지 않은 것이다.
    if not shared:
        return EXIT_UNDETERMINED, (
            '공유 커널 폐포: 판정 불가 — 두 레인이 함께 도달하는 모듈이 0개다.\n'
            '  이것은 「공유가 없다」가 아니라 폐포 계산이 대상을 못 찾았다는 뜻일 수 있다.'
        )

    head = (
        f'공유 커널 폐포: {len(shared)}개 '
        f'(중앙만 {observed["central_only"]} · provider만 {observed["provider_only"]}) · '
        f'provider 어휘 {len(observed["provider_vocabulary"])} · '
        f'서드파티 의존 {len(observed["third_party_dependencies"])} · '
        f'형제 레인 의존 {len(observed["sibling_lane_dependencies"])}'
    )
    if baseline is None:
        return EXIT_UNDETERMINED, (
            head + '\n  기준선이 없다 — --write-baseline 으로 기록하라.\n'
            '  기준선 없는 관측은 「늘었다」를 판정할 수 없다.'
        )

    grew = sorted(set(shared) - set(baseline['shared']))
    shrank = sorted(set(baseline['shared']) - set(shared))
    lines = [head]
    if shrank:
        lines.append(f'  줄어든 것 {len(shrank)}개 — 이관이 진행됐다면 정상이다.')
        for rel in shrank[:6]:
            lines.append(f'      - {rel}')
        lines.append('  ⚠️ 기준선은 자동으로 낮추지 않는다 (--write-baseline 은 사람이 친다).')
    if grew:
        lines.append(f'  **늘어난 것 {len(grew)}개** — 공유 표면이 커졌다:')
        for rel in grew:
            lines.append(f'      + {rel}')
        lines.append('  공유가 늘면 두 레인이 같은 이름을 주장하는 표면도 함께 커진다.')
        return EXIT_GREW, '\n'.join(lines)
    return EXIT_OK, '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--provider-root', type=Path, required=True,
                        help='provider 저장소 루트 (그 안의 src/ 를 읽는다)')
    parser.add_argument('--baseline', type=Path, default=BASELINE)
    parser.add_argument('--write-baseline', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    provider_src = args.provider_root / 'src'
    if not provider_src.is_dir():
        print(
            f'공유 커널 폐포: 판정 불가 — provider 트리를 찾지 못했다: {provider_src}\n'
            '  이것은 「공유가 없다」가 아니다. 저장소 하나만 있는 체크아웃은 정상 상태이고,\n'
            '  그 상태에서 이 축은 답할 수 없다.',
            file=sys.stderr,
        )
        return EXIT_UNDETERMINED

    observed = measure(_REPO_ROOT, provider_src)

    if args.write_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps({k: observed[k] for k in
                        ('shared_tops', 'shared', 'provider_vocabulary')},
                       ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'기준선 기록: {args.baseline} ({len(observed["shared"])}개)')
        return EXIT_OK

    baseline = None
    if args.baseline.is_file():
        try:
            baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
        except ValueError as exc:
            print(f'공유 커널 폐포: 판정 불가 — 기준선을 읽지 못했다: {exc}', file=sys.stderr)
            return EXIT_UNDETERMINED

    code, report = judge(observed, baseline)
    if args.json:
        print(json.dumps({'exit_code': code, **observed}, ensure_ascii=False, indent=2))
        return code
    print(report, file=sys.stdout if code == EXIT_OK else sys.stderr)
    return code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Undetermined as exc:
        print(f'공유 커널 폐포: 판정 불가 — {exc}', file=sys.stderr)
        raise SystemExit(EXIT_UNDETERMINED) from exc
