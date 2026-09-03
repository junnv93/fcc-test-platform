"""`tests/_layer_of_import.py` 의 봉인 (2026-09-03).

**이 봉인이 지키는 성질은 「판정이 접두사를 통과한다」다.**

⚠️ 이 헬퍼가 생긴 이유가 곧 이 봉인의 red 조건이다 — 커널 이관이 모듈에
`fcc_test_kernel.` 접두사를 붙이면, 최상위 이름으로 재던 순수성 가드가
**이관 당일에 조용히 통과**한다.
"""
from __future__ import annotations

import unittest

from tests._layer_of_import import FIRST_PARTY_PREFIX, imported_layers, layer_of


class TestTheLayerSurvivesAPrefix(unittest.TestCase):
    def test_the_bare_and_prefixed_forms_give_the_same_layer(self):
        """⚠️ 이 팔이 이 파일의 존재 이유다."""
        self.assertEqual(
            layer_of('infrastructure.db.x'),
            layer_of('fcc_test_kernel.infrastructure.db.x'),
        )
        self.assertEqual('infrastructure', layer_of('infrastructure.db.x'))

    def test_a_new_sibling_lane_is_recognised_without_editing_a_list(self):
        """목록판이 아니라 접두사판이다 — 형제 레인이 늘어도 손대지 않는다.

        `check_shared_kernel_closure.py` 가 같은 이유로 목록판을 버렸다:
        `fcc_test_kernel` 이 생기자 하루 만에 낡았다.
        """
        self.assertEqual(
            'infrastructure',
            layer_of(f'{FIRST_PARTY_PREFIX}brandnewlane.infrastructure.x'),
        )

    def test_a_third_party_name_is_not_stripped(self):
        """접두사가 없는 이름은 그대로다 — 벗기면 서드파티 판정이 무너진다."""
        for name in ('psycopg', 'psycopg2.extras', 'PySide6.QtCore', 'pandas'):
            with self.subTest(name=name):
                self.assertEqual(name.split('.')[0], layer_of(name))

    def test_the_distribution_itself_is_not_swallowed(self):
        """⚠️ 배포판을 통째로 import 하면 그 이름이 남아야 한다.

        빈 문자열을 돌려주면 「배포판을 통째로 부른다」와 「아무것도 안 부른다」가
        같은 값이 된다.
        """
        self.assertEqual('fcc_test_kernel', layer_of('fcc_test_kernel'))


class TestImportedLayersReadsBothImportForms(unittest.TestCase):
    def test_plain_import_and_from_import_are_both_seen(self):
        source = (
            'import infrastructure.db\n'
            'from fcc_test_kernel.infrastructure.logging import x\n'
            'from fcc_test_platform.application import svc\n'
            'import psycopg\n'
        )
        self.assertEqual(
            {'infrastructure', 'application', 'psycopg'},
            imported_layers(source),
        )

    def test_a_relative_import_is_not_mistaken_for_a_layer(self):
        """`from . import x` 는 계층 이름을 담지 않는다 — 담으면 오탐이다."""
        self.assertEqual(set(), imported_layers('from . import sibling\n'))

    def test_a_module_with_no_imports_yields_nothing(self):
        """⚠️ 비-공허성의 반대 방향 — 빈 집합이 「순수하다」의 정답이다.

        그러므로 이것을 쓰는 검사는 **대상이 실제로 import 를 갖는지** 따로
        확인해야 한다. 이 헬퍼는 그것을 대신 답해 주지 않는다.
        """
        self.assertEqual(set(), imported_layers('X = 1\n'))


if __name__ == '__main__':  # pragma: no cover
    unittest.main(verbosity=2)
