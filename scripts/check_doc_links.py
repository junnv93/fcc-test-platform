#!/usr/bin/env python3
"""문서의 상대 링크가 실제 파일을 가리키는가 — **경로로 판정한다** (2026-09-03).

`check_import_name_ownership.py` 의 형제다. 그것이 *같은 import 이름을 두 곳이 주장할 때
누가 이기는가* 를 묻는다면, 이것은 *문서가 가리키는 곳에 실제로 무엇이 있는가* 를 묻는다.

**왜 이 축이 필요한가 — 내가 직접 만든 결함으로 배웠다 (2026-09-03).**
런북 5종을 이 레인으로 이관하면서 링크 무결성을 즉석 셸로 확인했다::

    grep -hoE "[a-z-]*central-pc[a-z-]*\\.md" docs/operations/*.md | while read x; do
        [ -f "docs/operations/$x" ] && echo OK || echo 없음
    done

**그 검사는 경로를 버리고 파일명만 본다.** 그래서 다음 링크가

    ../../../equipment_management_system/docs/operations/central-pc-reboot-ops-guide.md

맨 이름 ``central-pc-reboot-ops-guide.md`` 로 축약됐고, 로컬 디렉터리에 없으니
**「깨진 링크」로 보고**됐다. 나는 그것을 *"`fcc-` 접두사 누락"* 으로 읽고 이름을 고쳤다.
그 순간 링크는 **로컬에 실재하는 파일**을 가리키게 되어 검사가 **초록**이 됐다 —
즉 **내가 깨뜨렸기 때문에 검사가 통과했다.** 실제 대상은 다른 저장소에 있었고
(``equipment_management_system/docs/operations/central-pc-reboot-ops-guide.md``, 실재),
멀쩡하던 상호 참조가 그때 깨졌다. 형제 세션이 잡아 주지 않았으면 그대로 남았다.

교과서적인 축 맹점이다(``.claude/rules/check-axis-blindness.md``):
**검사의 축이 「파일명」인데, 그 축에서 「저장소 밖 참조」와 「로컬 참조」가 같은 값을 갖는다.**

그래서 이 검사는 **경로를 버리지 않는다.** 그리고 세 상태를 구분한다:

- **로컬 링크** — 저장소 안을 가리킨다. 판정할 수 있다 → PASS / 위반.
- **저장소 밖 링크** — 다른 저장소를 가리킨다. 그 저장소가 이 머신에 있을 수도, 없을 수도
  있다. **판정 불가**다. ⚠️ 「없음」으로 답하면 이번 결함이 그대로 재발한다 —
  없다고 보고된 링크는 고치고 싶어지고, 고치면 조용히 로컬을 가리키게 된다.
- **외부 URL · 앵커** — 판정 대상이 아니다.

종료 코드: 0 일치 · 1 깨진 로컬 링크 · 2 판정 불가.

사용법::

    python3 scripts/check_doc_links.py            # docs/ 전체
    python3 scripts/check_doc_links.py docs/operations
"""
from __future__ import annotations

import argparse
import dataclasses
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator

EXIT_OK = 0
EXIT_BROKEN = 1
EXIT_UNDETERMINED = 2

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: ``[텍스트](대상)`` — 대상에서 앵커/제목을 뗀다.
_LINK_RE = re.compile(r'\[[^\]]*\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)')
#: 판정 대상이 아닌 것 — 스킴이 붙었거나 순수 앵커.
_SKIP_RE = re.compile(r'^(?:[a-z][a-z0-9+.-]*:|#|//)', re.IGNORECASE)


@dataclasses.dataclass(frozen=True)
class LinkVerdict:
    source: str
    target: str
    resolved: str | None
    #: 'ok' · 'broken' · 'outside_repo' · 'skipped'
    state: str

    @property
    def is_broken(self) -> bool:
        return self.state == 'broken'

    @property
    def is_undetermined(self) -> bool:
        return self.state == 'outside_repo'


def iter_links(path: Path) -> Iterator[str]:
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return
    for match in _LINK_RE.finditer(text):
        yield match.group(1)


def judge_link(source: Path, target: str, *, repo_root: Path = _REPO_ROOT) -> LinkVerdict:
    """링크 하나를 판정한다. **경로를 버리지 않는다** — 그것이 이 검사의 전부다."""
    rel_source = str(source.relative_to(repo_root))
    if _SKIP_RE.match(target):
        return LinkVerdict(rel_source, target, None, 'skipped')

    bare = target.split('#', 1)[0]
    if not bare:
        return LinkVerdict(rel_source, target, None, 'skipped')

    if bare.startswith('/'):
        # 절대 경로는 이 저장소의 것이 아니다(운영자 머신의 배치를 가리킨다).
        return LinkVerdict(rel_source, target, bare, 'outside_repo')

    resolved = (source.parent / bare).resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError:
        # ⚠️ **저장소 밖이다. 「없음」이 아니라 「판정 불가」다.**
        # 이 한 줄이 2026-09-03 의 결함을 막는다 — 없다고 답하면 고치고 싶어지고,
        # 고치면 그 링크는 조용히 이 저장소 안을 가리키게 된다.
        return LinkVerdict(rel_source, target, str(resolved), 'outside_repo')

    state = 'ok' if resolved.exists() else 'broken'
    return LinkVerdict(rel_source, target, str(resolved), state)


def judge_tree(roots: Iterable[Path], *, repo_root: Path = _REPO_ROOT) -> list[LinkVerdict]:
    out: list[LinkVerdict] = []
    for root in roots:
        for path in sorted(root.rglob('*.md')):
            for target in iter_links(path):
                out.append(judge_link(path, target, repo_root=repo_root))
    return out


def render(verdicts: list[LinkVerdict]) -> tuple[int, str]:
    judged = [v for v in verdicts if v.state != 'skipped']
    # ⚠️ 비-공허성 — 판정 대상이 0건이면 이 검사는 아무것도 묻지 않은 것이다.
    if not judged:
        return EXIT_UNDETERMINED, (
            '문서 링크: 판정 불가 — 판정할 상대 링크가 0건이다.\n'
            '  이것은 「깨진 링크 없음」이 아니다. 대상 경로를 확인하라.'
        )

    broken = [v for v in judged if v.is_broken]
    outside = [v for v in judged if v.is_undetermined]
    ok = [v for v in judged if v.state == 'ok']

    lines = [
        f'문서 링크: 판정 {len(judged)}건 '
        f'(일치 {len(ok)} · 깨짐 {len(broken)} · 저장소 밖 {len(outside)})'
    ]
    for v in broken:
        lines.append(f'  깨짐       {v.source} → {v.target}')
    if outside:
        lines.append(
            f'  저장소 밖 {len(outside)}건 — **판정하지 않는다.** 다른 저장소가 이 '
            '머신에 있을 수도 없을 수도 있고, 「없음」으로 답하면 고치고 싶어진다.'
        )
        for v in outside[:6]:
            lines.append(f'             {v.source} → {v.target}')
        if len(outside) > 6:
            lines.append(f'             … 외 {len(outside) - 6}건')

    if broken:
        return EXIT_BROKEN, '\n'.join(lines)
    if outside:
        # 저장소 밖 링크의 존재만으로 red 를 내지 않는다 — 그것은 정상 상태다.
        # 다만 종합 판정에서 「전부 확인했다」고 말하지도 않는다.
        return EXIT_OK, '\n'.join(lines)
    return EXIT_OK, lines[0]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('roots', nargs='*', default=None,
                        help='검사할 디렉터리 (기본: docs)')
    args = parser.parse_args(argv)
    roots = [Path(r).resolve() for r in (args.roots or [_REPO_ROOT / 'docs'])]
    missing = [r for r in roots if not r.is_dir()]
    if missing:
        print(f'문서 링크: 판정 불가 — 없는 경로 {missing}', file=sys.stderr)
        return EXIT_UNDETERMINED

    code, report = render(judge_tree(roots))
    print(report, file=sys.stdout if code == EXIT_OK else sys.stderr)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
