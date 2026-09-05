"""이 상자가 나르는 OpenAPI 사본이 **발행 레인의 발행본과 같은가**.

⚠️ **이 상자는 그 문서의 생산자가 아니다.** `fcc-test-contracts` 가 SSOT(`api_contracts`
의 표)와 변환기(`openapi_schema_builder`)를 가지고 자기 문서를 발행한다. 여기 있는 둘은
그 발행본의 **배포 사본**이다 — 하나는 테스트가, 하나는 프론트엔드 npm 패키지가 나른다.

## 이 축이 없던 동안 무슨 일이 있었나

실측 2026-09-04: 이 문서의 사본 **다섯**(모노레포 1 · 계약 2 · 이 상자 2)이 **byte
동일하게** 낡아 있었다. 계약은 `v0.1.17` 인데 내용은 `v0.1.12` 시절이었다.

⚠️ **사본이 서로 어긋난 것이 아니다 — 다섯이 완전히 같았다.** 그러므로 *사본 사이의
일치*를 보는 검사였다면 **끝까지 초록**이었을 것이다. 어긋난 것은 **생산자와 SSOT** 이고,
그것을 보려면 축이 사본 밖을 가리켜야 한다.

그래서 이 검사는 **의존 레인의 발행본**과 비교한다. 여기서 문서를 다시 만들지 않는다 —
그러면 생산자가 둘이 되고, 둘이 갈라지는 날 어느 쪽이 옳은지 말해 줄 것이 없다.

## 기존 봉인과의 관계 — 겹치지 않는다

`test_headless_snapshot_contract_conformance` 는 **in-process 스키마 표 ↔ 발행 아티팩트**
의 `nullable_partition` 을 본다. 그것은 nullability **축 하나**이고, 그래서 2026-09-04 에
제거된 `Col.*` enum 같은 변화를 **보지 못했다**. 이 축은 문서 **전체**를 보되 비교 대상이
다르다(의존 레인의 발행본). 둘은 서로를 대신하지 못한다.
"""
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'scripts'))

from fcc_test_contracts.common.tree_artifacts import (  # noqa: E402
    DependencyTreeUnavailable,
)
import sync_published_openapi as sync  # noqa: E402


class TestTheCarriedCopiesMatchThePublishedDocument(unittest.TestCase):
    def test_the_publishing_lane_can_be_reached_at_all(self):
        """⚠️ 비-공허성 팔. **답이 알려진 대상으로 눈금을 먼저 맞춘다.**

        아래 비교는 「사본이 발행본과 같은가」를 묻는데, 발행본을 못 읽으면 그 질문이
        답을 못 얻는다. 그때 조용히 건너뛰면 「사본이 맞다」와 「비교를 못 했다」가 같은
        초록이 된다 — 이 축이 끝내려는 것과 같은 모양이다.

        *이 검사가 성공하면 이 팔이 red 가 되는가?* → 아니오. 발행본을 읽을 수 있는 한
        초록이다.
        """
        try:
            document = sync.published_document()
        except DependencyTreeUnavailable as exc:
            self.fail(
                f'발행 레인의 문서를 읽지 못했다 — 이 축은 지금 아무것도 판정하지 '
                f'못한다.\n  {exc}\n'
                '⚠️ 사본 문제가 아니라 설치 형상 문제다.'
            )
        self.assertTrue(document.strip(), '발행본이 비었다')

    def test_every_carried_copy_equals_the_published_document(self):
        published = sync.published_document()
        stale = [
            str(path) for path in sync.carried_paths()
            if not path.is_file() or path.read_text(encoding='utf-8') != published
        ]
        self.assertEqual(
            stale, [],
            '나르는 사본이 발행 레인의 발행본과 다르다:\n  ' + '\n  '.join(stale)
            + '\n\n  fix: python3 scripts/sync_published_openapi.py'
            + '\n\n⚠️ 사본을 손으로 고치지 마라. 이 상자는 그 문서의 생산자가 아니다 — '
              '발행 레인이 SSOT 에서 만든 것을 나를 뿐이다.',
        )

    def test_at_least_one_copy_is_declared(self):
        """목록이 비면 위 검사가 0회 돌고 초록이 된다."""
        self.assertTrue(sync.CARRIED_RELATIVE_PATHS)
        self.assertTrue(sync.carried_paths())


if __name__ == '__main__':
    unittest.main()
