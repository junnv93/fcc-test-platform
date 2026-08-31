"""챔버 속성 쓰기 토큰이 프론트엔드에 실재한다 (2026-08-31).

⚠️ 이 검사는 모노레포 `tests/test_chamber_equipment_config_axis.py` 에서 **절반만**
왔다. 그 명제는 «이 토큰을 프론트엔드와 IdP 가 함께 들고 있다» 인데, 분리 이후
두 절반이 **서로 다른 레포**에 산다:

  - IdP 절반(`infra/keycloak/*.json`) — 모노레포가 갖는다
  - 프론트엔드 절반(`apps/web/src/api/permissions.ts`) — 여기가 갖는다

한쪽에서만 검사하면 나머지 절반은 아무도 안 보고, 두 값이 갈라져도 조용하다.
그래서 각 레포가 **자기 쪽**을 지킨다.

⚠️ 토큰 문자열은 이 파일과 모노레포 양쪽에 **리터럴로** 적힌다. 공유 패키지에
그 상수가 없기 때문이고, 그것이 남은 부채다 — 계약 값은 계약 패키지가 소유해야 한다.
"""
from __future__ import annotations

from pathlib import Path
import unittest

#: `platform:equipment-write` 를 대체한 챔버 속성 쓰기 토큰 (migration 023).
WRITE_PERMISSION = 'platform:chamber-config-write'
#: 그 토큰이 대체한 것. 프론트엔드가 아직 옛 토큰으로 게이트하면 안 된다.
RETIRED_PERMISSION = 'platform:equipment-write'

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PERMISSIONS_TS = _REPO_ROOT / 'apps' / 'web' / 'src' / 'api' / 'permissions.ts'


class TestChamberConfigWriteTokenReachesTheFrontend(unittest.TestCase):

    def test_the_permissions_module_exists(self):
        """대상이 없으면 아래 단언들은 존재하지 않는 파일에 대해 참이 된다."""
        self.assertTrue(
            _PERMISSIONS_TS.is_file(),
            f'{_PERMISSIONS_TS} 가 없다 — 이 검사의 대상이 사라졌거나 이사했다',
        )

    def test_the_frontend_carries_the_token(self):
        source = _PERMISSIONS_TS.read_text(encoding='utf-8')
        self.assertIn(
            WRITE_PERMISSION, source,
            '프론트엔드가 챔버 속성 쓰기 토큰을 들고 있지 않다 — 화면이 그 권한을 '
            '요구하지 못하거나, 엉뚱한 토큰으로 게이트한다',
        )

    def test_the_retired_token_is_not_still_gating(self):
        """⚠️ 새 토큰이 있다는 사실은 옛 토큰이 없다는 뜻이 아니다.

        둘 다 있으면 화면은 여전히 옛 토큰으로 통과시킬 수 있고, 그 상태는
        「이관 완료」와 같은 모양이다.
        """
        source = _PERMISSIONS_TS.read_text(encoding='utf-8')
        self.assertNotIn(
            RETIRED_PERMISSION, source,
            f'{RETIRED_PERMISSION} 는 migration 023 에서 은퇴했다 — 프론트엔드가 '
            '아직 그것으로 게이트하면 권한 이관이 끝나지 않은 것이다',
        )


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
