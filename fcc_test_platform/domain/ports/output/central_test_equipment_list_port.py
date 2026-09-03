"""Output ports — 성적서 §6 장비목록 중앙 read + write (2026-08-07).

플랫폼 장비목록 표면(``GET/POST /platform/projects/{id}/equipment-lists`` +
``GET/PUT/POST …/{equipment_list_id}[/items|/confirm]``)이 읽고 쓰는 중앙
``test_equipment_lists`` / ``test_equipment_list_items`` 행의 경계다.

Two narrow ports (``central_report_port`` 의 read/write 분리 미러):

- ``CentralTestEquipmentListReadPort`` — 한 프로젝트의 목록과 한 목록의 항목을 읽는다.
- ``CentralTestEquipmentListWritePort`` — 목록 생성 / 항목 전량 교체 / 확정.

**자연키가 둘이라는 사실이 write 계약에 드러난다.** 목록의 유일성은 부분 unique
인덱스 **두 개**로 나뉜다 — 성적서에 붙기 전에는 ``(project_id, test_item_key)``,
붙은 뒤에는 ``(test_report_id, test_item_key)``. 같은 모델에 성적서를 여러 판
내면서 장비가 달라질 수 있기 때문이다. 그래서 ``create_list`` 의 중복 판정은
"어느 자연키를 위반했는가"가 아니라 **행이 삽입되지 않았다**(→ ``None``)는 하나의
사실로 표현되고, 어느 술어를 쓸지는 어댑터가 ``test_report_id`` 유무로 고른다.

예외 3분류(각각 404 / 409 / 503 으로 매핑된다):

- ``EquipmentListNotFoundError`` — 없는 목록, 또는 **다른 프로젝트의 목록**
  (존재를 알려주지 않는다).
- ``EquipmentListConflictError`` — 자연키 중복, 확정본 편집, 확정 불가 상태.
- ``CentralTestEquipmentListError`` — 인프라 실패. 조용한 빈 목록으로 강등하지
  않는다(빈 §6 표는 성적서 생성이 hard-fail 로 거부하므로, 백엔드 장애를 "장비
  없음"으로 렌더하면 그 실패가 엉뚱한 곳에서 터진다).

dependency-free: stdlib typing 만 (infrastructure / psycopg / fastapi /
sqlalchemy / pandas / openpyxl / PySide6 import 0).
"""
from __future__ import annotations

from typing import Mapping, Optional, Protocol, Sequence, runtime_checkable


__all__ = [
    'CentralTestEquipmentListError',
    'EquipmentListNotFoundError',
    'EquipmentListConflictError',
    'CentralTestEquipmentListReadPort',
    'CentralTestEquipmentListWritePort',
]


class CentralTestEquipmentListError(RuntimeError):
    """중앙 장비목록 read/write 가 인프라 수준에서 실패했다 — loud(→ 503).

    빈 리스트로 강등하지 않는다: 그러면 백엔드 장애가 "이 프로젝트는 장비를 아직
    안 골랐다"로 보이고, 시험원이 이미 고른 목록 위에 빈 목록을 다시 만들게 된다.
    """


class EquipmentListNotFoundError(LookupError):
    """대상 목록이 없다(→ 404).

    경로 프로젝트에 속하지 않은 목록도 여기로 온다 — 다른 프로젝트의 행이
    존재한다는 사실 자체를 알려주지 않기 위해서다(403 이 아니라 404).
    """


class EquipmentListConflictError(RuntimeError):
    """상태 충돌(→ 409).

    세 경우가 모두 이 타입이다: 자연키 중복 생성, 확정본 항목 편집,
    확정할 수 없는 목록(이미 확정 / 항목 0개)의 확정 시도.
    ``ValueError``(400 잘못된 입력) 및 :class:`CentralTestEquipmentListError`
    (503 백엔드 장애)와 구분되어 플랫폼 에러표가 conflict 로 매핑한다.
    """


@runtime_checkable
class CentralTestEquipmentListReadPort(Protocol):
    """한 프로젝트의 장비목록을 중앙 DB 에서 읽는다."""

    def list_lists(self, project_id: str) -> list[dict]:
        """프로젝트의 목록 행을 최신순으로 반환한다. 컬럼:
        ``{list_id, project_id, test_report_id, test_item_key, test_item_name,
        status, source_profile_key, source_revision_key, source_pulled_at,
        confirmed_at, created_at, updated_at, item_count}``.
        빈 리스트 ⇒ 아직 목록이 없다(백엔드 실패는
        :class:`CentralTestEquipmentListError` 로 raise 한다)."""
        ...

    def read_list(self, equipment_list_id: str) -> Optional[dict]:
        """목록 한 건을 반환하고, 없으면 ``None``.

        ``project_id`` 를 포함해 반환한다 — 서비스가 경로 프로젝트와 대조해
        타 프로젝트 행을 404 로 막는 데 쓴다."""
        ...

    def list_items(self, equipment_list_id: str) -> list[dict]:
        """목록의 항목을 ``sort_order`` 오름차순으로 반환한다. 컬럼:
        ``{item_id, item_type, section_name, sort_order, description,
        manufacturer, model_name, serial_number, calibration_due_date,
        software_version, remarks}``."""
        ...


@runtime_checkable
class CentralTestEquipmentListWritePort(Protocol):
    """장비목록 생성 / 항목 전량 교체 / 확정."""

    def create_list(self, list_record: Mapping) -> Optional[dict]:
        """``test_equipment_lists`` 행 하나를 삽입하고 ``{list_id}`` 를 반환한다.

        같은 자연키의 행이 이미 있으면 ``None`` — 서비스가
        :class:`EquipmentListConflictError` 로 옮긴다. 어느 자연키인지는
        ``test_report_id`` 유무가 정한다(모듈 docstring 참조).
        연결/질의 실패는 :class:`CentralTestEquipmentListError`."""
        ...

    def replace_items(
        self, equipment_list_id: str, items: Sequence[Mapping], *, updated_at: str
    ) -> dict:
        """목록의 항목을 **한 트랜잭션에서** 통째로 교체하고 ``{item_count}`` 반환.

        ``items`` 는 이미 ``sort_order`` 가 부여된 상태로 온다(도메인
        ``assign_sort_order``). 부분 성공은 없다 — 삭제만 되고 삽입이 실패하면
        시험원의 목록이 사라진다.

        확정본은 거부한다: 어댑터는 ``status='draft'`` 조건부 UPDATE 를 **먼저**
        수행하고 0행이면 진단 조회 한 번으로 404(없음)와 409(확정본)를 가른다.
        존재 여부를 먼저 SELECT 하고 나서 UPDATE 하면 그 사이가 TOCTOU 창이 된다."""
        ...

    def attach_to_report(
        self, equipment_list_id: str, *, test_report_id: str, updated_at: str
    ) -> dict:
        """초안 목록을 성적서 판에 붙이고 ``{list_id, test_report_id}`` 반환.

        이 메서드가 없으면 "성적서 행이 생기기 전부터 목록을 만든다"는 설계가
        **API 로 도달 불가**해진다 — 붙지 않은 목록이 영영 성적서 판에 연결되지
        않고, 두 번째 부분 unique 인덱스와 "판마다 장비가 다르다"는 근거 전체가
        쓰이지 못한다.

        ``status='draft'`` + ``test_report_id IS NULL`` 조건부 UPDATE 한 문장이다
        (확정본 재부착 금지 + 이미 붙은 목록의 판 이동 금지). 0행이면 진단 조회로
        404/409 를 가른다. 대상 판에 같은 시험항목 목록이 이미 있으면 두 번째
        부분 unique 인덱스가 거절하고, 그것은 conflict 로 표면화된다."""
        ...

    def confirm_list(self, equipment_list_id: str, *, confirmed_at: str) -> dict:
        """목록을 확정하고 ``{list_id, status, confirmed_at}`` 반환.

        ``status='draft'`` 조건부 UPDATE 한 문장이다. 0행이면 진단 조회로
        404/409 를 판정한다. **드라이버 오류 메시지 문자열 파싱 금지** — 제약이
        여럿이라 SQLSTATE 만으로 원인을 특정할 수 없고, 제약 *이름* 매칭은 DDL
        명명 규약에 의존한다."""
        ...
