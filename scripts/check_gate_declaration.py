#!/usr/bin/env python3
"""선언된 게이트 값과 «실제»를 대조한다.

■ 왜 이 검사가 있는가

2026-09-07 한 세션이 branch protection 을 켜면서 **네 번** 틀린 진술을 형제
세션 여섯에 방송했다. 네 번 다 「이 게이트가 무엇을 강제하는가」였다:

    · strict=false 의 근거   → guard 가 이미 한다  (거짓: advise || true, return 0)
    · 브랜치 삭제 금지        → 범위 누락 (main 에만 걸린다)
    · enforce_admins=false   → 사유가 성립 안 함 (승인 0이면 자기승인 문제가 없다)
    · CODEOWNERS = 게이트     → 거짓 (require_code_owner_reviews=false, 켤 수도 없다)

**강제를 «주장하는» 산문은 손으로 쓰면 반드시 낡거나 틀린다.** 그래서 값을
`.claude/contracts/branch-protection-declaration.json` 한 곳에 두고 대조한다.

■ 두 축이고, 하나만 «항상» 돈다

    오프라인 축  선언 자신의 정합 + 산문이 선언을 «인용»하는가       — 항상 돈다
    라이브 축    선언 == GitHub 실제 설정                            — 닿을 때만

⚠️ 라이브 축은 CI 에서 돌지 못한다. branch protection 읽기는 admin 권한을
요구하고 기본 `GITHUB_TOKEN` 에는 없다. 그래서 **닿지 못하면 「판정 불가」라고
시끄럽게 말하고** 통과로 세지 않는다 — 조용한 skip 은 초록과 같은 모양이다.

⚠️ 그리고 **몇 개를 대조했는지 센다.** 「집합이 비지 않았다」만 단언하면 크기 1의
«틀린» 집합이 통과한다(이 저장소가 mypy 절 이름에서 실제로 밟은 형태다).

■ 종료코드
    0  선언과 실제가 일치 (또는 라이브 축이 닿지 못했고 그 사실을 말했다)
    1  어긋났다 — 무엇이 어떻게인지 이름으로 말한다
    2  검사가 돌지 못했다 (선언 파일 부재 등)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

DECLARATION = Path('.claude/contracts/branch-protection-declaration.json')

#: 선언을 «인용»해야 하는 문서들. 여기 인용이 없으면 선언이 고아가 되고,
#: 고아가 된 선언은 아무도 안 고친다.
CITING_DOCS = (
    'intent/README.md',
    'intent/intent-driven-workflow/plan.md',
    '.claude/evaluations/2026-09-07-intent-driven-workflow-and-branch-protection.md',
)

UNMEASURED = '라이브 축: 판정 불가'


def load(root: Path) -> dict:
    return json.loads((root / DECLARATION).read_text(encoding='utf-8'))


def offline_problems(root: Path, decl: dict) -> tuple[list[str], int]:
    """선언 자신의 정합 + 산문의 인용. (문제들, 대조한 값 개수)."""
    out: list[str] = []
    values = decl.get('values') or {}
    why = decl.get('why_each_value') or {}
    if not values:
        out.append('선언에 값이 하나도 없다 — 빈 선언은 항상 통과한다')
    missing_why = sorted(set(values) - set(why))
    if missing_why:
        out.append(f'근거 없는 선언 값 {missing_why} — 값만 있고 왜인지 없으면 '
                   '다음 사람이 그것을 「단순화」하며 되돌린다')
    orphan_why = sorted(set(why) - set(values))
    if orphan_why:
        out.append(f'선언에 없는 값의 근거 {orphan_why} — 산문이 낡았다')
    for key in values:
        if '.' not in key:
            out.append(f'선언 키 {key!r} 가 `a.b` 모양이 아니다')

    for rel in CITING_DOCS:
        p = root / rel
        if not p.is_file():
            out.append(f'{rel} 이 없다 — 선언을 인용할 문서가 사라졌다')
        elif DECLARATION.name not in p.read_text(encoding='utf-8'):
            out.append(f'{rel} 이 {DECLARATION.name} 을 인용하지 않는다 — '
                       '선언이 고아가 된다')
    return out, len(values)


def _dig(blob: dict, dotted: str):
    cur = blob
    for part in dotted.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            return KeyError
        cur = cur[part]
    return cur


def compare_values(values: dict, live: dict) -> tuple[list[str], int]:
    """선언된 값들과 실제 blob 을 대조한다. (문제들, «대조한 개수»).

    ⚠️ 개수를 함께 돌려주는 이유: 「문제가 없다」만으로는 «아무것도 대조하지
    않은» 것과 구별되지 않는다. 이 저장소는 도구가 `checked 1 source file` 을
    성실히 보고하며 «틀린» 크기-1 집합으로 통과한 것을 이미 겪었다.
    """
    out: list[str] = []
    compared = 0
    for key, want in values.items():
        got = _dig(live, key)
        compared += 1
        if got is KeyError:
            out.append(f'{key}: 실제 설정에 그 키가 «없다» — 선언이 낡았거나 오타다')
        elif got != want:
            out.append(f'{key}: 선언 {want!r} · 실제 {got!r}')
    return out, compared


def live_problems(decl: dict) -> tuple[list[str] | None, int]:
    """선언 == GitHub 실제. 닿지 못하면 (None, 0)."""
    proc = subprocess.run(
        ['gh', 'api', f"repos/{decl['repo']}/branches/{decl['branch']}/protection"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        return None, 0
    try:
        live = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, 0
    return compare_values(decl['values'], live)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='선언된 게이트 값과 실제의 대조')
    ap.add_argument('--root', default='.')
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if not (root / DECLARATION).is_file():
        print(f'게이트 선언 검사: {DECLARATION} 가 없다 — 돌지 못했다.', file=sys.stderr)
        print('  ⚠️ 이것은 «통과가 아니다».', file=sys.stderr)
        return 2

    decl = load(root)
    off, off_n = offline_problems(root, decl)
    print(f'오프라인 축: 선언 값 {off_n}개를 대조했다.')
    live, live_n = live_problems(decl)
    if live is None:
        print(f'{UNMEASURED} — GitHub 에 닿지 못했다(권한 또는 네트워크).')
        print('  ⚠️ 이것은 「일치한다」가 아니라 「재지 못했다」이다.')
    else:
        print(f'라이브 축: 실제 설정과 {live_n}개를 대조했다.')

    problems = off + (live or [])
    if not problems:
        return 0
    print('\n선언과 실제가 어긋났다:\n', file=sys.stderr)
    for p in problems:
        print(f'  ✗ {p}', file=sys.stderr)
    print(f'\n  선언 SSOT: {DECLARATION}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
