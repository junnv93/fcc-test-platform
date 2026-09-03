"""Central PostgreSQL 장비목록 read 어댑터 (2026-08-07).

``PostgresCentralTestEquipmentListReadAdapter`` 가
``CentralTestEquipmentListReadPort`` 를 중앙 ``test_equipment_lists`` /
``test_equipment_list_items`` 테이블에 대해 구현한다.

설계(``PostgresCentralReportReadAdapter`` 미러):

- **주입 ``connection_factory``**(``() -> DbConnection``) — psycopg 는 합성
  루트가 lazy 로 만든다. 이 모듈은 어떤 PostgreSQL 드라이버도 import 하지 않는다.
- **read-only**: ``SELECT`` 만.
- **``%s`` paramstyle**(psycopg).
- **loud-fail**: 연결/질의 실패는 ``CentralTestEquipmentListError``.
- 목록은 ``created_at DESC, id ASC``, 항목은
  ``sort_order ASC, id ASC`` — 후자는 ``idx_test_equipment_list_items_list_order``
  를 그대로 탄다. ``id`` 를 tie-breaker 로 둔 이유는 같은 ``sort_order`` 가
  들어오는 상황(다른 세션의 동시 교체 등)에서도 출력이 결정적이어야 하기 때문이다.

``item_count`` 는 목록 조회에서 상관 서브쿼리로 함께 센다. 화면이 목록마다 항목
수를 보여주는데, 이것을 N+1 왕복으로 세면 목록 화면이 목록 개수만큼 질의한다.
"""
from __future__ import annotations

from typing import Callable, Optional

from domain.ports.output.central_test_equipment_list_port import (
    CentralTestEquipmentListError,
)
from domain.ports.output.platform_database_port import DbConnection
from fcc_test_kernel.domain.services.test_equipment_list_policy import ITEM_PERSISTED_FIELDS


__all__ = [
    'LIST_COLUMNS',
    'ITEM_COLUMNS',
    'LIST_LISTS_SQL',
    'READ_LIST_SQL',
    'LIST_ITEMS_SQL',
    'PostgresCentralTestEquipmentListReadAdapter',
]


#: 목록 SELECT 의 출력 컬럼(= SELECT alias). 순서가 row→dict 매핑을 정한다.
LIST_COLUMNS: tuple[str, ...] = (
    'list_id',
    'project_id',
    'test_report_id',
    'test_item_key',
    'test_item_name',
    'status',
    'source_profile_key',
    'source_revision_key',
    'source_pulled_at',
    'confirmed_at',
    'created_at',
    'updated_at',
    'item_count',
)

#: 항목 SELECT 의 출력 컬럼. **도메인의 영속 필드 목록에서 파생한다** — 여기에
#: 열 이름을 손으로 나열하면 §6 표의 열 순서 사본이 하나 더 생기고, 도메인이
#: 필드를 추가해도 이 SELECT 만 조용히 뒤처진다.
ITEM_COLUMNS: tuple[str, ...] = ('item_id',) + ITEM_PERSISTED_FIELDS + ('sort_order',)

_LIST_SELECT = (
    'SELECT "l"."id" AS "list_id", "l"."project_id", "l"."test_report_id", '
    '"l"."test_item_key", "l"."test_item_name", "l"."status", '
    '"l"."source_profile_key", "l"."source_revision_key", "l"."source_pulled_at", '
    '"l"."confirmed_at", "l"."created_at", "l"."updated_at", '
    '(SELECT COUNT(*) FROM "test_equipment_list_items" i '
    'WHERE "i"."list_id" = "l"."id") AS "item_count" '
    'FROM "test_equipment_lists" l '
)

LIST_LISTS_SQL = (
    _LIST_SELECT
    + 'WHERE "l"."project_id" = %s ORDER BY "l"."created_at" DESC, "l"."id" ASC'
)

READ_LIST_SQL = _LIST_SELECT + 'WHERE "l"."id" = %s'

def _item_select_list() -> str:
    """``ITEM_COLUMNS`` 에서 SELECT 절을 만든다 — 컬럼 목록과 SQL 이 갈라질 수 없다."""
    return ', '.join(
        '"i"."id" AS "item_id"' if column == 'item_id' else f'"i"."{column}"'
        for column in ITEM_COLUMNS
    )


LIST_ITEMS_SQL = (
    f'SELECT {_item_select_list()} '
    'FROM "test_equipment_list_items" i WHERE "i"."list_id" = %s '
    'ORDER BY "i"."sort_order" ASC, "i"."id" ASC'
)


class PostgresCentralTestEquipmentListReadAdapter:
    """``CentralTestEquipmentListReadPort`` over a central PG connection factory."""

    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def list_lists(self, project_id: str) -> list[dict]:
        return self._query(LIST_LISTS_SQL, LIST_COLUMNS, (project_id,))

    def read_list(self, equipment_list_id: str) -> Optional[dict]:
        rows = self._query(READ_LIST_SQL, LIST_COLUMNS, (equipment_list_id,))
        return rows[0] if rows else None

    def list_items(self, equipment_list_id: str) -> list[dict]:
        return self._query(LIST_ITEMS_SQL, ITEM_COLUMNS, (equipment_list_id,))

    def _query(self, statement: str, columns: tuple[str, ...], params: tuple) -> list[dict]:
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001 — wrap as loud domain error
            raise CentralTestEquipmentListError(
                f'central equipment list read connection failed: {exc}'
            ) from exc
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(statement, params)
                rows = list(cursor.fetchall())
            finally:
                cursor.close()
        except Exception as exc:  # noqa: BLE001
            raise CentralTestEquipmentListError(
                f'central equipment list read query failed: {exc}'
            ) from exc
        finally:
            close: Optional[Callable] = getattr(connection, 'close', None)
            if callable(close):
                close()
        return [dict(zip(columns, row)) for row in rows]
