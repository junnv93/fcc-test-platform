#!/usr/bin/env python3
"""이 상자의 **자기-정합성** 게이트 — 관측된 실패 이름 집합 == 선언된 기준선.

■ 왜 「전부 통과」가 아니라 「선언과 일치」인가

배송된 상자는 오늘 전부 통과하지 못한다. 모노레포에만 있는 경로를 단언하는
테스트가 남아 있기 때문이고, 그것은 분리가 끝나야 사라진다. 그래서 상자는
`EXTRACTED_FROM.md` 에서 *"N 개 노드가 실패합니다 — 당신이 깨뜨린 것이 아닙니다"*
라고 적고, 판정을 **개수가 아니라 이름 집합으로** 하라고 명시한다.

그 문장은 대조되지 않는 한 약속일 뿐이다. 이 스크립트가 그것을 실제로 대조한다.
그러면 세 가지가 한꺼번에 성립한다:

* 오늘의 알려진 실패는 **통과**로 읽힌다 (팀원을 헛되이 막지 않는다).
* 새 실패는 **즉시 red** 다 (선언에 없는 이름이 나타난다).
* 고쳐진 실패도 **red** 다 — 선언이 낡았다는 사실이고, 그것도 소식이다.

⚠️ 개수 비교가 아니라 이름 집합이다. 하나 고치고 하나 깨뜨리면 개수는 같다.

■ 기준선의 출처

상자 루트의 `delivered_test_run_baseline.json` 하나. 이 파일에는 레인 이름
리터럴이 없다 — 그래서 두 상자가 **바이트 동일한** 사본을 들 수 있다.

⚠️ 왜 매니페스트를 직접 읽지 않는가. 매니페스트
(`artifacts/headless_contract_extraction_manifest.v1.json`)는 **contracts 상자에만**
배송된다. 그 경로의 소유가 contracts 레인이라 platform 이 같은 파일을 주장하면
매니페스트 자신의 소유권 게이트를 위반한다. 그런데 platform 상자의
`EXTRACTED_FROM.md` 는 *"매니페스트의 이름 집합으로 판정하라"* 고 적는다 —
**그 상자에 없는 파일을 가리키는 지시**다(2026-08-30 실측). 레인 로컬 사본이
그 구멍을 메우고, 사본이 매니페스트와 갈라지지 않는 것은 모노레포의 파리티
게이트가 지킨다 (`tests/test_delivered_lane_check_axis.py`).

■ 종료코드
    0  일치 — 이 상자는 선언한 그대로다
    1  불일치 — 아래 두 목록이 무엇이 어긋났는지 이름으로 말한다
    2  게이트 자신이 돌지 못했다 (pytest 부재, 기준선 부재 등)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BASELINE_REL = Path('delivered_test_run_baseline.json')

# ⚠️ 판정을 오염시키는 것으로 **실측된** 산출물 디렉터리 (2026-08-30, A 세션).
#
# `pip install .` (non-editable) 은 상자 안에 `build/lib/` 를 만든다. 트리를
# 스캔하는 테스트 일부가 그 사본을 원본과 함께 세어 **선언에 없는 실패**를
# 만든다. 그리고 `.gitignore` 가 `build/` 를 덮으므로 `git status` 는 0줄이다 —
# 즉 트리는 깨끗해 보이는데 게이트는 빨갛고, 원인은 git 에 보이지 않는다.
#
# 그런 트리에서는 **판정하지 않는다**. 오염된 red 를 진짜 red 로 보고하면
# 팀원은 자기가 깨뜨렸다고 결론내고, 그것이 바로 이 게이트가 없애려는 오독이다.
CONFOUNDING_ARTIFACTS = ('build',)

# A 세션이 2026-08-30 에 실제로 돌린 명령이 SSOT 다. 게이트가 다른 명령을 돌면
# 봉인하는 것이 실측한 것이 아니게 된다.
#   -p no:randomly       순서 고정 (실패 집합이 순서에 흔들리지 않게)
#   -p no:cacheprovider  상자에 .pytest_cache 를 남기지 않는다
#   --continue-on-collection-errors
#                        수집 에러 하나가 나머지 전부를 가리지 않게. 이것이 없으면
#                        exit=2 · 수집 0개가 되어 「실패 0건」과 구분되지 않는다.
PYTEST_ARGS = (
    '-q',
    '-p', 'no:randomly',
    '-p', 'no:cacheprovider',
    '--tb=no',
    '-ra',
    '--continue-on-collection-errors',
)


def _fail(msg: str) -> 'int':
    print(f'lane-check: {msg}', file=sys.stderr)
    return 2


def read_baseline(root: Path) -> 'tuple[str, set[str]]':
    data = json.loads((root / BASELINE_REL).read_text(encoding='utf-8'))
    return data['lane'], set(data['baseline'])


def observe(root: Path) -> set[str]:
    """실제로 suite 를 돌려 실패 이름 집합을 얻는다."""
    scripts = root / 'scripts'
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(
        [str(root), str(scripts)] + ([env['PYTHONPATH']] if env.get('PYTHONPATH') else [])
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'failed.json'
        env['FCC_LANE_CHECK_OUT'] = str(out)
        subprocess.run(
            [sys.executable, '-m', 'pytest', *PYTEST_ARGS, '-p', 'lane_check_plugin'],
            cwd=root,
            env=env,
            check=False,
        )
        if not out.exists():
            raise RuntimeError(
                'pytest 가 실패 집합을 남기지 않았다 — 플러그인이 붙지 않았거나 '
                'pytest 가 시작조차 못했다. 위 출력을 보라.'
            )
        return set(json.loads(out.read_text(encoding='utf-8')))


def main(argv: 'list[str] | None' = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.', help='상자 루트 (기본: 현재 디렉터리)')
    parser.add_argument(
        '--write-baseline',
        action='store_true',
        help='관측값으로 매니페스트 기준선을 갱신한다. ⚠️ 게이트를 초록으로 '
             '만들려고 쓰지 마라 — 그것은 검사를 끄는 것이다. 실패가 실제로 '
             '해소됐을 때만.',
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    polluted = [d for d in CONFOUNDING_ARTIFACTS if (root / d).is_dir()]
    if polluted:
        return _fail(
            f'{", ".join(polluted)}/ 가 트리에 있다 — 이 트리의 실패 집합은 오염됐다.\n'
            f'          `pip install .`(non-editable)이 만든 사본을 테스트가 원본과 '
            f'함께 센다.\n'
            f'          ⚠️ .gitignore 가 덮으므로 `git status` 는 깨끗하다고 답한다.\n'
            f'          해소: rm -rf {" ".join(polluted)} 후 다시. 설치는 editable 로:\n'
            f'                pip install -e \'.[test,oidc]\''
        )

    try:
        lane, declared = read_baseline(root)
    except FileNotFoundError as exc:
        return _fail(f'상자 루트가 아니다 ({exc.filename} 없음): {root}')
    except (KeyError, json.JSONDecodeError) as exc:
        return _fail(f'기준선 파일을 읽지 못했다 ({BASELINE_REL}): {exc}')

    try:
        observed = observe(root)
    except RuntimeError as exc:
        return _fail(str(exc))

    unexpected = sorted(observed - declared)
    resolved = sorted(declared - observed)

    print()
    print(f'lane-check: {lane}')
    print(f'  선언된 실패 {len(declared)}개 / 관측된 실패 {len(observed)}개')

    if args.write_baseline:
        path = root / BASELINE_REL
        path.write_text(
            json.dumps(
                {'lane': lane, 'baseline': sorted(observed)},
                ensure_ascii=False,
                indent=1,
            )
            + '\n',
            encoding='utf-8',
        )
        print(f'  기준선을 관측값으로 갱신했다 ({len(observed)}개).')
        return 0

    if not unexpected and not resolved:
        print('  ✅ 일치 — 이 상자는 선언한 그대로다.')
        return 0

    if unexpected:
        print()
        print(f'  ❌ 선언에 없는 실패 {len(unexpected)}개 — 새로 깨진 것이다:')
        for nodeid in unexpected:
            print(f'      {nodeid}')
    if resolved:
        print()
        print(f'  ⚠️ 선언됐는데 실패하지 않은 것 {len(resolved)}개 — 선언이 낡았다:')
        for nodeid in resolved:
            print(f'      {nodeid}')
        print()
        print('     고쳐서 그런 것이면 --write-baseline 으로 선언을 줄여라.')
    print()
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
