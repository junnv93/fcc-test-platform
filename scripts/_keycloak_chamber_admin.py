#!/usr/bin/env python3
"""`fcc_test_platform.keycloak_chamber_admin` 의 **재수출** — 로직은 그쪽이 갖는다.

⚠️ 이것은 진입점이 아니라 **shim** 이다. 형제들과 달리 이 모듈에는 `main()` 이
없다 — CLI 가 아니라 라이브러리이고, `platform_chamber_token_evidence` 가
`run_lifecycle_live` 를 가져다 쓴다. `from … import main` 을 하는 껍데기 템플릿을
그대로 쓰면 **`ImportError` 가 난다** (2026-09-05 실측 — 그리고 어떤 시험도
껍데기를 부르지 않아 게이트 전부가 초록이었다).

⚠️ 이 파일이 **짧은 것이 요점**이다. `scripts/` 는 패키지가 아니라 휠이 나르지
못하므로 이 레인을 소비하는 레포마다 사본이 필요한데, 사본이 이만큼이면
**갈라질 것이 없다.** 알맹이가 바뀌면 휠이 나른다.

⚠️ `sys.path` 한 줄은 **설치 전에도 돌기 위한 것**이다.
"""
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fcc_test_platform.keycloak_chamber_admin import *  # noqa: E402,F401,F403
from fcc_test_platform.keycloak_chamber_admin import (  # noqa: E402,F401
    build_live_evidence,
    provision_chambers_live,
    run_lifecycle_live,
)
