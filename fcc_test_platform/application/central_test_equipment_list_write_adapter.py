"""Central PostgreSQL 장비목록 write 어댑터 (2026-08-07).

``PostgresCentralTestEquipmentListWriteAdapter`` 가
``CentralTestEquipmentListWritePort`` 를 구현한다.

**INSERT 가 두 문장인 이유 — 이 모듈에서 가장 중요한 사실.**
목록의 자연키는 부분 unique 인덱스 **두 개**로 나뉜다
(``015_test_equipment_lists.sql``):

    ux_test_equipment_lists_project_item  (project_id, test_item_key)
        WHERE test_report_id IS NULL
    ux_test_equipment_lists_report_item   (test_report_id, test_item_key)
        WHERE test_report_id IS NOT NULL

PostgreSQL 의 ``ON CONFLICT`` 는 conflict target 이 **술어까지 일치**해야 부분
인덱스를 추론한다. 술어 없이 ``ON CONFLICT ("project_id","test_item_key")`` 만
쓰면 "there is no unique or exclusion constraint matching the ON CONFLICT
specification" 으로 **런타임에 실패**한다 — 스키마를 읽지 않으면 드러나지 않는
함정이라 여기 명시한다. 그래서 ``test_report_id`` 유무로 INSERT 문을 고른다.

**조건부 UPDATE 를 먼저 하는 이유.** 확정본 보호를 "SELECT status → 판정 →
DELETE/INSERT" 로 하면 그 사이가 TOCTOU 창이다. 대신 ``status='draft'`` 를 WHERE
에 넣은 UPDATE 를 **먼저** 실행하고, 0행이면 그때 진단 SELECT 한 번으로 404(없음)
와 409(확정본)를 가른다. 정상 경로의 왕복은 늘지 않고 경합 창은 사라진다.

**드라이버 오류 메시지 문자열 파싱 영구 금지.** 이 테이블엔 FK 가 둘(project,
test_report)이라 SQLSTATE 23503 만으로 어느 부모인지 특정할 수 없고, 제약 *이름*
매칭은 DDL 명명 규약에 의존한다.

설계(``PostgresCentralReportWriteAdapter`` 미러): 주입 ``connection_factory`` /
``%s`` paramstyle / loud-fail / 단일 트랜잭션(성공 시 commit, 실패 시 rollback).
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional, Sequence

from domain.ports.output.central_test_equipment_list_port import (
    CentralTestEquipmentListError,
    EquipmentListConflictError,
    EquipmentListNotFoundError,
)
from domain.ports.output.platform_database_port import DbConnection
from fcc_test_kernel.domain.services.test_equipment_list_policy import (
    ITEM_PERSISTED_FIELDS,
    ListStatus,
    confirm_refusal_reason,
)


__all__ = [
    'LIST_INSERT_COLUMNS',
    'ITEM_INSERT_COLUMNS',
    'INSERT_LIST_UNATTACHED_SQL',
    'INSERT_LIST_ATTACHED_SQL',
    'INSERT_ITEM_SQL',
    'DELETE_ITEMS_SQL',
    'TOUCH_DRAFT_LIST_SQL',
    'CONFIRM_DRAFT_LIST_SQL',
    'ATTACH_DRAFT_LIST_SQL',
    'DIAGNOSE_LIST_SQL',
    'PostgresCentralTestEquipmentListWriteAdapter',
]


LIST_INSERT_COLUMNS: tuple[str, ...] = (
    'id',
    'project_id',
    'test_report_id',
    'test_item_key',
    'test_item_name',
    'status',
    'source_profile_key',
    'source_revision_key',
    'source_pulled_at',
    'created_at',
    'updated_at',
)

#: 항목 INSERT 컬럼 — 도메인의 영속 필드 목록에서 파생한다(양쪽에 손으로 나열
#: 하면 필드 추가 시 한쪽만 늘어난다). ``sort_order`` 는 도메인이 부여한 값이고
#: ``id``/``list_id``/``created_at`` 은 어댑터가 채운다.
ITEM_INSERT_COLUMNS: tuple[str, ...] = (
    ('id', 'list_id') + ITEM_PERSISTED_FIELDS + ('sort_order', 'created_at')
)


def _insert_sql(table: str, columns: tuple[str, ...], conflict: str = '') -> str:
    column_sql = ', '.join(f'"{column}"' for column in columns)
    placeholders = ', '.join(['%s'] * len(columns))
    return (
        f'INSERT INTO "{table}" ({column_sql}) VALUES ({placeholders})'
        f'{conflict} RETURNING "id"'
    )


#: 성적서에 아직 붙지 않은 목록 — (project_id, test_item_key) 부분 인덱스.
INSERT_LIST_UNATTACHED_SQL = _insert_sql(
    'test_equipment_lists',
    LIST_INSERT_COLUMNS,
    ' ON CONFLICT ("project_id", "test_item_key") '
    'WHERE "test_report_id" IS NULL DO NOTHING',
)

#: 성적서 판에 붙은 목록 — (test_report_id, test_item_key) 부분 인덱스.
INSERT_LIST_ATTACHED_SQL = _insert_sql(
    'test_equipment_lists',
    LIST_INSERT_COLUMNS,
    ' ON CONFLICT ("test_report_id", "test_item_key") '
    'WHERE "test_report_id" IS NOT NULL DO NOTHING',
)

INSERT_ITEM_SQL = _insert_sql('test_equipment_list_items', ITEM_INSERT_COLUMNS)

DELETE_ITEMS_SQL = 'DELETE FROM "test_equipment_list_items" WHERE "list_id" = %s'

#: 초안일 때만 updated_at 을 밀어 올린다. 0행 = 없거나 확정본.
TOUCH_DRAFT_LIST_SQL = (
    'UPDATE "test_equipment_lists" SET "updated_at" = %s '
    'WHERE "id" = %s AND "status" = %s RETURNING "id"'
)

#: 초안이고 **항목이 하나라도 있을 때만** 확정한다.
#: 0행 = 없거나 / 이미 확정본이거나 / 비어 있다.
#:
#: 항목 존재 조건이 같은 문장 안에 있는 이유(2026-08-08): 서비스가
#: ``len(list_items())`` 를 읽고 ``confirm_refusal_reason`` 으로 판정한 뒤 UPDATE
#: 하면 그 사이에 다른 세션이 항목을 전부 지울 수 있다. 그러면 **빈 목록이
#: 확정되고**, 실패는 한참 뒤 성적서 생성 단계에서 엉뚱한 곳에서 난다
#: (``report_generation_service`` 의 hard-fail). 상태 축은 처음부터 원자적이었고
#: 항목 수 축만 아니었다 — "check-then-set 창 없음"은 상태에 대해서만 참이었다.
CONFIRM_DRAFT_LIST_SQL = (
    'UPDATE "test_equipment_lists" SET "status" = %s, "confirmed_at" = %s, '
    '"updated_at" = %s WHERE "id" = %s AND "status" = %s '
    'AND EXISTS (SELECT 1 FROM "test_equipment_list_items" WHERE "list_id" = %s) '
    'RETURNING "id"'
)

#: 초안이고 아직 어느 판에도 붙지 않았을 때만 성적서에 붙인다.
#: 0행 = 없거나 / 확정본이거나 / 이미 다른 판에 붙어 있다.
ATTACH_DRAFT_LIST_SQL = (
    'UPDATE "test_equipment_lists" SET "test_report_id" = %s, "updated_at" = %s '
    'WHERE "id" = %s AND "status" = %s AND "test_report_id" IS NULL RETURNING "id"'
)

#: 조건부 UPDATE 가 0행일 때 원인을 가르는 단 하나의 진단 조회.
#:
#: 두 번째 열은 항목 존재 여부다 — 확정 UPDATE 가 항목 존재를 조건에 포함하게
#: 되면서 0행의 원인이 하나 늘었고, 그 원인을 알려면 이 조회가 답할 수 있어야
#: 한다. **왕복은 여전히 1회**다(상관 서브쿼리로 합류).
DIAGNOSE_LIST_SQL = (
    'SELECT "l"."status", EXISTS (SELECT 1 FROM "test_equipment_list_items" "i" '
    'WHERE "i"."list_id" = "l"."id") FROM "test_equipment_lists" "l" '
    'WHERE "l"."id" = %s'
)


class PostgresCentralTestEquipmentListWriteAdapter:
    """``CentralTestEquipmentListWritePort`` — 부분 인덱스 2종 대응 write."""

    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def create_list(self, list_record: Mapping) -> Optional[dict]:
        statement = (
            INSERT_LIST_ATTACHED_SQL
            if list_record.get('test_report_id')
            else INSERT_LIST_UNATTACHED_SQL
        )
        values = tuple(list_record.get(column) for column in LIST_INSERT_COLUMNS)

        def _txn(cursor) -> Optional[dict]:
            cursor.execute(statement, values)
            if not list(cursor.fetchall()):
                # 자연키 중복 — 삽입 없음. 서비스가 409 로 옮긴다.
                return None
            return {'list_id': list_record.get('id')}

        return self._in_transaction(_txn)

    def replace_items(
        self, equipment_list_id: str, items: Sequence[Mapping], *, updated_at: str
    ) -> dict:
        rows = [
            tuple(
                [item.get('id'), equipment_list_id]
                + [item.get(field) for field in ITEM_PERSISTED_FIELDS]
                + [item.get('sort_order'), updated_at]
            )
            for item in items
        ]

        def _txn(cursor) -> dict:
            # 조건부 UPDATE 를 먼저 — 초안이 아니면 아무것도 지우지 않는다.
            cursor.execute(
                TOUCH_DRAFT_LIST_SQL,
                (updated_at, equipment_list_id, ListStatus.DRAFT.value),
            )
            if not list(cursor.fetchall()):
                raise _diagnose(cursor, equipment_list_id, action='replace items')
            cursor.execute(DELETE_ITEMS_SQL, (equipment_list_id,))
            for row in rows:
                cursor.execute(INSERT_ITEM_SQL, row)
            return {'item_count': len(rows)}

        return self._in_transaction(_txn)

    def attach_to_report(
        self, equipment_list_id: str, *, test_report_id: str, updated_at: str
    ) -> dict:
        def _txn(cursor) -> dict:
            cursor.execute(
                ATTACH_DRAFT_LIST_SQL,
                (test_report_id, updated_at, equipment_list_id, ListStatus.DRAFT.value),
            )
            if not list(cursor.fetchall()):
                raise _diagnose(cursor, equipment_list_id, action='attach to a report')
            return {'list_id': equipment_list_id, 'test_report_id': test_report_id}

        return self._in_transaction(_txn)

    def confirm_list(self, equipment_list_id: str, *, confirmed_at: str) -> dict:
        def _txn(cursor) -> dict:
            cursor.execute(
                CONFIRM_DRAFT_LIST_SQL,
                (
                    ListStatus.CONFIRMED.value,
                    confirmed_at,
                    confirmed_at,
                    equipment_list_id,
                    ListStatus.DRAFT.value,
                    equipment_list_id,
                ),
            )
            if not list(cursor.fetchall()):
                raise _diagnose(cursor, equipment_list_id, action='confirm')
            return {
                'list_id': equipment_list_id,
                'status': ListStatus.CONFIRMED.value,
                'confirmed_at': confirmed_at,
            }

        return self._in_transaction(_txn)

    def _in_transaction(self, body: Callable[[object], object]):
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001
            raise CentralTestEquipmentListError(
                f'central equipment list write connection failed: {exc}'
            ) from exc
        try:
            cursor = connection.cursor()
            try:
                result = body(cursor)
            finally:
                cursor.close()
            connection.commit()
            return result
        except (
            CentralTestEquipmentListError,
            EquipmentListNotFoundError,
            EquipmentListConflictError,
        ):
            # 도메인이 이미 분류한 실패 — 인프라 오류로 감싸면 404/409 가 503 이 된다.
            _safe_rollback(connection)
            raise
        except Exception as exc:  # noqa: BLE001
            _safe_rollback(connection)
            raise CentralTestEquipmentListError(
                f'central equipment list write failed: {exc}'
            ) from exc
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()


def _diagnose(cursor, equipment_list_id: str, *, action: str) -> Exception:
    """조건부 UPDATE 가 0행일 때 404 인지 409 인지 가른다(왕복 1회).

    반환값을 caller 가 ``raise`` 한다 — 여기서 raise 하면 트랜잭션 헬퍼의
    except 분기가 이 함수의 프레임을 원인으로 잡아 메시지가 흐려진다.
    """
    cursor.execute(DIAGNOSE_LIST_SQL, (equipment_list_id,))
    rows = list(cursor.fetchall())
    if not rows:
        return EquipmentListNotFoundError(
            f'equipment list not found: {equipment_list_id}'
        )
    status, has_items = rows[0][0], bool(rows[0][1])
    if action == 'confirm':
        # **우선순위를 여기서 재발명하지 않는다.** 초판은 항목 존재를 먼저 봤는데,
        # 도메인 SSOT `confirm_refusal_reason` 은 상태를 먼저 본다. 그 차이가 실제
        # 상태 하나에서 갈린다 — 확정본이면서 항목이 0개인 목록(레거시 데이터)을
        # `empty_list` 로 보고하면, 시험원이 항목을 채워 재시도해도 여전히 확정본이라
        # 같은 "비어 있음"이 반복된다. 규칙이 두 곳에 있으면 그중 하나는 틀린다.
        refusal = confirm_refusal_reason(status, 1 if has_items else 0)
        if refusal is not None:
            return EquipmentListConflictError(
                f'cannot confirm: equipment list {equipment_list_id} is {refusal}'
            )
    return EquipmentListConflictError(
        f'cannot {action}: equipment list {equipment_list_id} is {status}'
    )


def _safe_rollback(connection) -> None:
    rollback = getattr(connection, 'rollback', None)
    if callable(rollback):
        try:
            rollback()
        except Exception:  # noqa: BLE001 — never mask the original error
            pass
