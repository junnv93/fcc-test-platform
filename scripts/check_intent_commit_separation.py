#!/usr/bin/env python3
"""승인 아티팩트와 코드를 «한 커밋에» 섞는 것을 거절한다.

■ 무엇이 문제인가

`intent.md` 와 `spec.md` 는 **사람이 승인하는 대상**이다. 그것이 코드와 같은
커밋에 있으면, 그 승인이 코드까지 승인한 것이 된다 — 그리고 승인한 사람은
자기가 무엇을 승인했는지 모른다. `intent/README.md` 가 「PR 은 파일 하나만
담는다」라고 적는 이유가 그것이다.

■ `plan.md` 는 «예외»다

계획은 코드와 함께 간다. 그것이 설계다 — 리뷰어가 최종 diff 를 계획 표와
대조해야 하기 때문이다. 그래서 이 검사는 `plan.md` 를 코드로도 승인
아티팩트로도 세지 않는다.

■ 왜 「코드 경로 목록」을 적는가

반대로 하면(= 승인 아티팩트가 아닌 «모든 것»을 코드로 셈) 문서 오탈자 수정
하나가 spec 수정과 섞였다는 이유로 거절된다. 그것은 과발화이고, 과발화하는
게이트는 꺼진다. 그래서 **좁게** 센다.

■ ⚠️ 이것은 실수 방지층이다

`--no-verify` 한 번이면 사라진다. 진짜 강제는 리뷰어가 PR 을 열었을 때
「이 PR 이 파일 하나인가」를 보는 것이고, 그 축은 사람이 한다.

■ 종료코드
    0  섞이지 않았다 (또는 승인 아티팩트가 없다)
    1  섞였다 — 무엇과 무엇인지 이름으로 말한다
"""
from __future__ import annotations

import argparse
from pathlib import PurePosixPath
import re
import subprocess
import sys

#: 사람이 «승인»하는 아티팩트. plan.md 는 여기 없다 — 코드와 함께 가는 것이 설계다.
#: ⚠️ `_templates/` 를 제외한다 — 서식은 «규칙»이지 승인 대상이 아니다.
#:    빼지 않으면 서식을 고칠 때마다 코드와 분리해야 하고, 그것은 과발화다.
_APPROVAL_ARTIFACT = re.compile(r'^intent/(?!_templates/)[^/]+/(intent|spec)\.md$')

#: 코드로 세는 최상위 경로. 좁게 센다 — 넓히면 과발화하고, 과발화하면 꺼진다.
CODE_ROOTS = ('fcc_test_platform', 'scripts', 'tests', 'migrations',
              'infra', 'web', 'apps', 'packages', 'config')


def _staged(root: str) -> list[str]:
    proc = subprocess.run(
        ['git', '-C', root, 'diff', '--cached', '--name-only', '--diff-filter=ACMR'],
        capture_output=True, text=True)
    if proc.returncode != 0:
        return []
    return [p for p in proc.stdout.splitlines() if p.strip()]


def split(paths: list[str]) -> tuple[list[str], list[str]]:
    """(승인 아티팩트, 코드) 로 나눈다. 둘 다 아닌 것은 어느 쪽도 아니다."""
    artifacts = sorted(p for p in paths if _APPROVAL_ARTIFACT.match(p))
    code = sorted(p for p in paths
                  if PurePosixPath(p).parts and PurePosixPath(p).parts[0] in CODE_ROOTS)
    return artifacts, code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='승인 아티팩트와 코드의 혼합 커밋 거절')
    ap.add_argument('--root', default='.')
    ap.add_argument('--paths', nargs='*', default=None,
                    help='검사할 경로 (기본: staged)')
    args = ap.parse_args(argv)

    paths = args.paths if args.paths is not None else _staged(args.root)
    artifacts, code = split(paths)
    if not artifacts or not code:
        return 0

    print('intent 분리 게이트: 막았다 — 승인 아티팩트와 코드가 한 커밋에 있다.\n',
          file=sys.stderr)
    print('  승인 아티팩트 (사람이 승인하는 것):', file=sys.stderr)
    for p in artifacts:
        print(f'    · {p}', file=sys.stderr)
    print('\n  코드:', file=sys.stderr)
    for p in code[:6]:
        print(f'    · {p}', file=sys.stderr)
    if len(code) > 6:
        print(f'    · … 외 {len(code) - 6}건', file=sys.stderr)
    print('\n  섞이면 「의도 승인」이 코드까지 승인한 것이 되고,', file=sys.stderr)
    print('  승인한 사람은 자기가 무엇을 승인했는지 모른다.', file=sys.stderr)
    print('\n  커밋을 둘로 나눠라:', file=sys.stderr)
    print(f'    git commit -- {" ".join(artifacts)}', file=sys.stderr)
    print(f'    git commit -- {" ".join(code[:3])}{" …" if len(code) > 3 else ""}',
          file=sys.stderr)
    print('\n  ⚠️ plan.md 는 이 검사의 대상이 «아니다» — 코드와 함께 가는 것이 설계다.',
          file=sys.stderr)
    print('  우회: FCC_ALLOW_MIXED_INTENT_COMMIT=1 (그리고 왜 우회했는지 적어라)',
          file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
