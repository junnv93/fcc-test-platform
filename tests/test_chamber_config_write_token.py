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

from fcc_test_kernel.application.central_contract.api_vocabulary import (  # noqa: E402
    PLATFORM_API_PERMISSION_DESCRIPTIONS,
)

# ⚠️ 2026-08-31 — 이 토큰은 **리터럴이 아니라 파생**이다. 같은 문자열이 모노레포
#    `tests/test_chamber_equipment_config_axis.py` 에도 필요하고, 리터럴 둘이면
#    한쪽만 바뀌어도 두 검사가 각자 통과하면서 값이 갈라진다 — 그리고 그 갈라짐은
#    두 레포 어디에서도 보이지 않는다. 출처는 **두 레포에 모두 있는**
#    `application/central_contract/api_vocabulary.py` 다.
#    ⚠️ 이름을 적어 고르는 것이 아니라 **선언된 서술 집합에 그 토큰이 있는가**로 고른다.
_CANDIDATES = tuple(
    token for token in PLATFORM_API_PERMISSION_DESCRIPTIONS
    if token.endswith(':chamber-config-write')
)
assert len(_CANDIDATES) == 1, (
    '챔버 속성 쓰기 토큰이 하나가 아니다 — 어휘가 갈라졌거나 이름이 바뀌었다: '
    f'{_CANDIDATES}'
)

#: `platform:equipment-write` 를 대체한 챔버 속성 쓰기 토큰 (migration 023).
WRITE_PERMISSION = _CANDIDATES[0]
#: 그 토큰이 대체한 것. 프론트엔드가 아직 옛 토큰으로 게이트하면 안 된다.
#: ⚠️ 이것은 **은퇴한** 값이라 어휘에 없다 — 없는 것을 파생시킬 수는 없으므로
#: 리터럴로 남는다. 그리고 그것이 옳다: 어휘에서 사라진 사실 자체가 요점이다.
RETIRED_PERMISSION = 'platform:equipment-write'


def _retired_token_is_really_gone() -> bool:
    """은퇴 토큰이 어휘에 **없다**는 것도 명제의 일부다."""
    return RETIRED_PERMISSION not in PLATFORM_API_PERMISSION_DESCRIPTIONS

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

    def test_the_retired_token_is_gone_from_the_vocabulary_too(self):
        """⚠️ 「프론트엔드가 안 쓴다」와 「어휘에서 은퇴했다」는 다른 축이다.

        어휘에 남아 있으면 다른 표면이 그것을 되살릴 수 있고, 그때 프론트만 보는
        검사는 조용하다.
        """
        self.assertTrue(
            _retired_token_is_really_gone(),
            f'{RETIRED_PERMISSION} 가 아직 권한 어휘에 있다 — migration 023 이 '
            '은퇴시킨 값이므로 어휘에서도 사라져야 한다',
        )

    def test_the_derivation_is_not_a_literal_in_disguise(self):
        """⚠️ 파생이 우연히 옛 리터럴과 같아서 통과하는 것은 아닌지 — 출처가
        실제로 그 값을 **선언**하는지 확인한다."""
        self.assertIn(
            WRITE_PERMISSION, PLATFORM_API_PERMISSION_DESCRIPTIONS,
            '파생한 토큰이 권한 어휘에 없다 — 파생이 깨졌다',
        )
        self.assertTrue(
            PLATFORM_API_PERMISSION_DESCRIPTIONS[WRITE_PERMISSION].strip(),
            '토큰은 있는데 서술이 비었다 — 어휘가 그 값을 설명하지 않는다',
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
