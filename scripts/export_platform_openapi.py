"""이 상자가 **저작하는** platform OpenAPI 문서를 발행(또는 검증)한다.

⚠️ **이 진입점이 없어서 봉인이 없는 스크립트를 가리켰다.** 실측 2026-09-04:
`test_platform_chamber_api_p2` · `test_platform_claim_write_fe_p3` ·
`test_platform_asyncapi_schema` 가 드리프트를 정확히 잡고도 고치는 방법으로
`python scripts/export_session_api_schemas.py` 를 안내했는데, **그 스크립트는 이 상자에
없다**(모노레포 소유). 즉 게이트는 red 를 내는데 **운영자가 따라갈 수 있는 지시가
아니었다.**

⚠️ **여기서 문서를 다시 만드는 것이 맞다** — `headless-api.openapi.json` 과 다르다.
그쪽은 계약 레인이 저작하고 이 상자는 나르기만 하므로 `sync_published_openapi.py` 가
발행본을 **복사**한다. 이쪽은 조립기(`fcc_test_platform.application.api_schema.
build_platform_openapi_schema`)가 **이 상자 소유**다. 저작과 운반을 같은 스크립트로
묶으면 어느 문서에 대해 이 상자가 권위인지가 흐려진다.

Usage::

    python3 scripts/export_platform_openapi.py            # 다시 쓴다
    python3 scripts/export_platform_openapi.py --check    # 드리프트만 보고, 쓰지 않는다
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402
from fcc_test_platform.application.api_schema import (  # noqa: E402
    build_platform_openapi_schema,
)

#: ⚠️ 직렬화 규약은 모노레포 `export_session_api_schemas.py` 와 **같아야 한다**
#: (`indent=2, sort_keys=True, ensure_ascii=False` + 끝 개행). 다르면 두 생산자가 같은
#: 문서를 두 바이트로 쓰고, 서식 차이가 드리프트로 보인다 — 실측이 아니라.
PUBLISHED_RELATIVE_PATHS = (
    'docs/api/platform-api.openapi.json',
    'packages/api-artifacts/artifacts/platform-api.openapi.json',
)


def canonical_document() -> str:
    return json.dumps(
        build_platform_openapi_schema(None), indent=2, sort_keys=True, ensure_ascii=False,
    ) + '\n'


def published_paths() -> list[Path]:
    return [resolve_repo_artifact(__file__, rel) for rel in PUBLISHED_RELATIVE_PATHS]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    check_only = '--check' in args

    canonical = canonical_document()
    drifted: list[str] = []
    written: list[str] = []

    for path in published_paths():
        current = path.read_text(encoding='utf-8') if path.is_file() else None
        if current == canonical:
            continue
        if check_only:
            drifted.append(str(path))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical, encoding='utf-8')
        written.append(str(path))

    if check_only and drifted:
        print(json.dumps({
            'drifted': drifted,
            'fix': 'python3 scripts/export_platform_openapi.py',
        }, indent=2, ensure_ascii=False))
        return 1
    if written:
        print(json.dumps({'written': written}, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
