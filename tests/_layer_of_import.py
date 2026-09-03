"""import 이 **어느 계층**을 부르는가 — 최상위 이름이 아니라 계층 절로 판정한다.

## 왜 최상위 이름으로는 안 되는가

순수성 가드의 명제는 *「도메인 정책이 인프라를 부르지 않는다」* 다. 그것을
최상위 import 이름으로 재면, 배포판 접두사가 붙는 순간 **판정이 조용히 사라진다**:

    from infrastructure.db import x                 최상위 = infrastructure   ✅ 잡힌다
    from fcc_test_kernel.infrastructure.db import x 최상위 = fcc_test_kernel  ❌ 안 잡힌다

⚠️ **실측 2026-09-03 — 이 저장소가 두 형태를 다 갖고 있었다.** 문자열 축
(`'from infrastructure' in source`)과 AST 최상위 축(`_imported_roots`) 둘 다
접두사에 눈이 멀었고, 커널 이관(1·2단계)이 진행되면서 **이관 당일에 조용히
약해지는** 자리였다.

첫 처방은 금지어 목록에 접두사판을 **더하는** 것이었다:

    'from infrastructure', 'from fcc_test_kernel.infrastructure', …

그것은 임시방편이다 — **접두사가 또 늘면 같은 자리에서 또 낡는다.** 그리고
「목록을 손으로 늘린다」는 이 계열이 반복해서 값을 치른 형태다.

## 판정

**first-party 배포판 접두사를 벗기고, 남은 첫 절을 계층으로 읽는다.**

    infrastructure.db.x                      → infrastructure
    fcc_test_kernel.infrastructure.db.x      → infrastructure
    fcc_test_platform.application.svc        → application
    fcc_test_contracts.common.x              → common
    psycopg                                  → psycopg      (접두사 없음, 그대로)

⚠️ 접두사 **목록**이 아니라 **접두사 패턴**이다 — `check_shared_kernel_closure.py`
가 같은 이유로 목록판을 버렸다(한 커밋 만에 `fcc_test_kernel` 이 생겨 낡았다).
"""
from __future__ import annotations

import ast
from pathlib import Path

#: 이 계열이 내는 배포판의 접두사. 목록이 아니라 패턴이다 —
#: `fcc_test_platform` · `fcc_test_contracts` · `fcc_test_kernel` 과
#: **앞으로 생길 형제 레인**이 전부 여기 걸린다.
FIRST_PARTY_PREFIX = 'fcc_test_'


def layer_of(module: str) -> str:
    """dotted 모듈 이름이 가리키는 계층 절.

    접두사가 벗겨진 뒤 남는 것이 없으면(배포판 자체를 import) 그 이름을 돌려준다 —
    삼키면 「배포판을 통째로 부른다」가 「아무것도 안 부른다」와 같은 값이 된다.
    """
    parts = [segment for segment in module.split('.') if segment]
    if not parts:
        return ''
    if parts[0].startswith(FIRST_PARTY_PREFIX) and len(parts) > 1:
        return parts[1]
    return parts[0]


def imported_layers(source: 'str | Path') -> set[str]:
    """모듈 원본이 부르는 계층 이름 전부.

    ⚠️ `from <패키지> import <서브모듈>` 도 센다 — 그 형태에서 서브모듈 이름을
    버리면 간선을 놓친다(실측 2026-09-03: 폐포 게이트가 정확히 그것으로 11개를
    놓쳤다). 여기서는 계층만 필요하므로 패키지 절이면 충분하지만, 같은 함정을
    기록해 둔다.
    """
    text = source.read_text(encoding='utf-8') if isinstance(source, Path) else source
    layers: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            layers.update(layer_of(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            layers.add(layer_of(node.module))
    return layers - {''}
