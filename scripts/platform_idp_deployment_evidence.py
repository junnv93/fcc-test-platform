#!/usr/bin/env python3
"""`fcc_test_platform.idp_deployment_evidence_cli` 의 진입점 — 로직은 그쪽이 갖는다.

⚠️ 이 파일이 **짧은 것이 요점**이다. `scripts/` 는 패키지가 아니라 휠이 나르지
못하므로 이 레인을 소비하는 레포마다 사본이 필요한데, 사본이 이만큼이면
**갈라질 것이 없다.** 알맹이가 바뀌면 휠이 나른다.

⚠️ `sys.path` 한 줄은 **설치 전에도 돌기 위한 것**이다. 이 레포 체크아웃에서
`python3 scripts/…` 로 직접 부르는 경로가 실재하고, 그때 패키지는 아직
site-packages 에 없을 수 있다.
"""
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fcc_test_platform.idp_deployment_evidence_cli import main  # noqa: E402

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
