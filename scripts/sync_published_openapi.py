"""이 상자가 나르는 OpenAPI 사본을 **발행 레인의 발행본**과 맞춘다.

⚠️ **이 상자는 그 문서의 생산자가 아니다.** `fcc-test-contracts` 가 SSOT 와 변환기를
가지고 자기 문서를 발행하며(`scripts/export_headless_openapi.py`, 2026-09-04 이사),
여기 있는 둘은 그 발행본의 **배포 사본**이다 — 하나는 테스트가 읽고 하나는 프론트엔드
npm 패키지가 나른다.

⚠️ **사본을 손으로 고치지 마라.** 실측 2026-09-04: 이 문서의 사본 다섯이 byte 동일하게
낡아 있었다(계약 `v0.1.17` 인데 내용은 `v0.1.12` 시절). 사본끼리는 완전히 같았으므로
**사본 사이의 일치를 보는 검사였다면 끝까지 초록**이었을 것이다. 어긋난 것은 생산자와
SSOT 였고, 그래서 이 스크립트는 사본끼리 맞추지 않고 **의존 레인의 발행본에서** 가져온다.

Usage::

    python3 scripts/sync_published_openapi.py            # 발행본으로 맞춘다
    python3 scripts/sync_published_openapi.py --check    # 드리프트만 보고, 쓰지 않는다
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fcc_test_contracts.common.tree_artifacts import (  # noqa: E402
    resolve_dependency_artifact,
    resolve_repo_artifact,
)

#: 발행 레인이 그 문서를 부르는 이름. 레포 어휘로 부르고 해소는 그쪽 트리가 답한다.
PUBLISHED_SOURCE = 'docs/api/headless-api.openapi.json'

#: 이 상자가 그것을 나르는 자리. ⚠️ 둘 다 써야 한다 — 하나만 쓰면 그 둘이 갈라지고,
#: 그 갈라짐은 이 스크립트가 막으려는 것과 **같은 계급**이다.
CARRIED_RELATIVE_PATHS = (
    'docs/api/headless-api.openapi.json',
    'packages/api-artifacts/artifacts/headless-api.openapi.json',
)


def published_document() -> str:
    """발행 레인이 낸 문서. ⚠️ 여기서 다시 만들지 않는다 — 그러면 생산자가 둘이 된다."""
    return resolve_dependency_artifact(PUBLISHED_SOURCE).read_text(encoding='utf-8')


def carried_paths() -> list[Path]:
    return [resolve_repo_artifact(__file__, rel) for rel in CARRIED_RELATIVE_PATHS]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    check_only = '--check' in args

    published = published_document()
    drifted: list[str] = []
    written: list[str] = []

    for path in carried_paths():
        current = path.read_text(encoding='utf-8') if path.is_file() else None
        if current == published:
            continue
        if check_only:
            drifted.append(str(path))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(published, encoding='utf-8')
        written.append(str(path))

    if check_only and drifted:
        print(json.dumps({
            'drifted': drifted,
            'fix': 'python3 scripts/sync_published_openapi.py',
        }, indent=2, ensure_ascii=False))
        return 1
    if written:
        print(json.dumps({'written': written}, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
