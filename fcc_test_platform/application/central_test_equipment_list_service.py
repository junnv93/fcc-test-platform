"""Platform 장비목록 서비스 (2026-08-07).

``CentralTestEquipmentListService`` 는 플랫폼 driving 어댑터
(``PlatformApiAdapter``)와 중앙 장비목록 read/write 포트 + 프로젝트 read 포트
사이의 애플리케이션 경계다. 성적서 §6 표면의 로직을 소유한다:

1. **list_lists** — 프로젝트의 목록을 최신순으로 + 고를 수 있는 시험항목 어휘.
2. **get_list** — 목록 + 항목 + 두 표의 열 명세(``tables``). 열 순서는 도메인
   정책에서 파생하며 클라이언트가 재조립하지 않는다.
3. **create_list** — ``test_item_key`` 어휘 검증 + 프로젝트 존재 확인(→ 404) +
   자연키 race-safe 삽입(중복 → 409).
4. **replace_items** — 항목 전량 교체. ``sort_order`` 는 도메인이 배열 위치에서
   부여하고, 확정본이면 409.
5. **confirm_list** — 도메인 ``confirm_refusal_reason`` 판정 후 확정.

**타 프로젝트 목록은 404 다.** 경로 프로젝트에 속하지 않은 ``equipment_list_id``
는 존재를 알려주지 않는다(403 이면 "그 id 는 있다"를 흘린다).

**시험항목 어휘를 경계에서 집행한다.** ``test_item_key`` 는 FCC 성적서 한 편에
대응하는 닫힌 어휘이고(:class:`~domain.services.test_equipment_list_policy.TestItemKey`
— DTS/BLE/BT/UNII), 어휘 밖 값은 ``ValueError`` → 400 이다. 축을 정하는 주체는
EMS 가 아니라 **성적서를 발행하는 FCC** 다 — 어떤 측정이 어떤 성적서로 가는지는
이미 ``ReportTech`` / ``tech_partitioner`` / 템플릿 4파일이 알고 있다. EMS 표기
(``'DFS, UNII'``)를 축으로 삼으면 항목 하나가 성적서 여러 편에 걸쳐, ②단계에서
"이 성적서의 장비목록"을 집을 수 없다.

**어휘를 응답으로 실어 보낸다.** ``list_lists`` 가 목록 배열이 아니라 봉투를
돌려주며 ``test_items`` 를 함께 싣는다. ``get_list`` 가 두 표의 열 순서를
``tables`` 로 내려보내는 것과 같은 결정이다 — 프론트가 어휘 배열을 자기 쪽에
적으면 같은 어휘가 TS/Python 두 곳으로 쪼개진다.

dependency-free of infrastructure / FastAPI / SQL — 도메인 포트 + 도메인 서비스
+ stdlib ``uuid`` / ``datetime`` 만.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from fcc_test_platform.application.central_project_service import ProjectNotFoundError
from fcc_test_kernel.application.central_contract.envelope_helpers import optional_text, require_uuid, text
from fcc_test_platform.domain.ports.output.central_project_port import CentralProjectReadPort
from fcc_test_platform.domain.ports.output.central_report_port import CentralReportReadPort
from fcc_test_platform.domain.ports.output.central_test_equipment_list_port import (
    CentralTestEquipmentListReadPort,
    CentralTestEquipmentListWritePort,
    EquipmentListConflictError,
    EquipmentListNotFoundError,
)
from fcc_test_kernel.domain.services.test_equipment_list_policy import (
    TEST_ITEM_KEYS,
    ItemType,
    ListStatus,
    assign_sort_order,
    confirm_refusal_reason,
    is_frozen,
    normalize_test_item_key,
    table_specs,
)


__all__ = ['CentralTestEquipmentListService']


class CentralTestEquipmentListService:
    def __init__(
        self,
        read_port: CentralTestEquipmentListReadPort,
        write_port: CentralTestEquipmentListWritePort,
        project_read_port: CentralProjectReadPort,
        report_read_port: CentralReportReadPort,
        *,
        clock: Optional[Callable[[], str]] = None,
        id_factory: Optional[Callable[[], str]] = None,
    ) -> None:
        self._read = read_port
        self._write = write_port
        self._project_read = project_read_port
        self._report_read = report_read_port
        self._clock = clock or _utcnow_iso
        self._id_factory = id_factory or _uuid4_str

    # ── read ─────────────────────────────────────────────────────────────────

    def list_lists(self, project_id: str) -> dict:
        """프로젝트의 장비목록(최신순) + 고를 수 있는 시험항목 어휘를 반환한다.

        ``test_items`` 는 도메인 :data:`TEST_ITEM_KEYS` 를 그대로 실어 보낸다 —
        생성 폼이 서버가 준 어휘로 선택지를 그리고 자기 쪽에 배열을 적지 않게
        하기 위해서다. 순서는 성적서 파일 순서(E6~E9)이고 도메인이 소유한다.

        ``ValueError``(잘못된 id) / ``ProjectNotFoundError``(없는 프로젝트).
        """
        pid = require_uuid(project_id, 'project_id')
        self._require_project(pid)
        return {
            'lists': [_list_envelope(row) for row in self._read.list_lists(pid)],
            'test_items': list(TEST_ITEM_KEYS),
        }

    def get_list(self, project_id: str, equipment_list_id: str) -> dict:
        """목록 + 항목 + 두 표의 열 명세를 반환한다."""
        pid = require_uuid(project_id, 'project_id')
        lid = require_uuid(equipment_list_id, 'equipment_list_id')
        row = self._require_list_in_project(pid, lid)
        items = [_item_envelope(item) for item in self._read.list_items(lid)]
        return {
            'list': _list_envelope(row),
            'items': items,
            'tables': table_specs(),
        }

    # ── write ────────────────────────────────────────────────────────────────

    def create_list(
        self,
        project_id: str,
        *,
        test_item_key: str,
        test_item_name: Optional[str] = None,
        test_report_id: Optional[str] = None,
        source_profile_key: Optional[str] = None,
        source_revision_key: Optional[str] = None,
        source_pulled_at: Optional[str] = None,
    ) -> dict:
        """목록 하나를 만든다. 상태는 서버가 ``draft`` 로 정한다.

        같은 자연키가 이미 있으면 ``EquipmentListConflictError``(→ 409).
        """
        pid = require_uuid(project_id, 'project_id')
        self._require_project(pid)
        key = _require_test_item_key(test_item_key)
        report_id = self._require_report_in_project(pid, test_report_id)

        now = self._clock()
        record = {
            'id': self._id_factory(),
            'project_id': pid,
            'test_report_id': report_id,
            'test_item_key': key,
            'test_item_name': optional_text(test_item_name),
            'status': ListStatus.DRAFT.value,
            'source_profile_key': optional_text(source_profile_key),
            'source_revision_key': optional_text(source_revision_key),
            'source_pulled_at': optional_text(source_pulled_at),
            'created_at': now,
            'updated_at': now,
        }
        created = self._write.create_list(record)
        if created is None:
            raise EquipmentListConflictError(
                f'equipment list already exists for test_item_key {key!r}'
            )
        return _list_envelope(
            {**record, 'list_id': created.get('list_id') or record['id'], 'item_count': 0}
        )

    def replace_items(
        self, project_id: str, equipment_list_id: str, items: Sequence[dict]
    ) -> dict:
        """항목을 전량 교체한다. 확정본이면 409."""
        pid = require_uuid(project_id, 'project_id')
        lid = require_uuid(equipment_list_id, 'equipment_list_id')
        row = self._require_list_in_project(pid, lid)
        if is_frozen(row.get('status')):
            raise EquipmentListConflictError(
                f'equipment list {lid} is confirmed and cannot be edited'
            )

        ordered = assign_sort_order(_validated_items(items or []))
        for entry in ordered:
            entry['id'] = self._id_factory()
        outcome = self._write.replace_items(lid, ordered, updated_at=self._clock())
        return {
            'list_id': lid,
            'item_count': int(outcome.get('item_count', len(ordered))),
        }

    def attach_to_report(
        self, project_id: str, equipment_list_id: str, *, test_report_id: str
    ) -> dict:
        """초안 목록을 이 프로젝트의 성적서 판에 붙인다.

        시험원은 성적서 행이 생기기 전(시험 ~90% 시점)부터 목록을 만들 수 있으므로,
        나중에 붙이는 경로가 없으면 그 목록은 영영 성적서에 연결되지 않는다.
        확정본은 붙일 수 없고(스냅샷이 이미 굳었다), 이미 다른 판에 붙은 목록도
        옮길 수 없다(판마다 별도 행이 원칙이다) — 둘 다 409.
        """
        pid = require_uuid(project_id, 'project_id')
        lid = require_uuid(equipment_list_id, 'equipment_list_id')
        self._require_list_in_project(pid, lid)
        rid = self._require_report_in_project(pid, test_report_id)
        if rid is None:
            raise ValueError('test_report_id is required')
        outcome = self._write.attach_to_report(
            lid, test_report_id=rid, updated_at=self._clock()
        )
        return {
            'list_id': lid,
            'test_report_id': outcome.get('test_report_id', rid),
        }

    def confirm_list(self, project_id: str, equipment_list_id: str) -> dict:
        """목록을 확정한다(동결). 이미 확정이거나 항목이 0개면 409."""
        pid = require_uuid(project_id, 'project_id')
        lid = require_uuid(equipment_list_id, 'equipment_list_id')
        row = self._require_list_in_project(pid, lid)
        item_count = len(self._read.list_items(lid))
        refusal = confirm_refusal_reason(row.get('status'), item_count)
        if refusal is not None:
            raise EquipmentListConflictError(
                f'equipment list {lid} cannot be confirmed: {refusal}'
            )
        outcome = self._write.confirm_list(lid, confirmed_at=self._clock())
        return {
            'list_id': lid,
            'status': outcome.get('status', ListStatus.CONFIRMED.value),
            'confirmed_at': outcome.get('confirmed_at'),
        }

    # ── helpers ──────────────────────────────────────────────────────────────

    def _require_project(self, project_id: str) -> dict:
        detail = self._project_read.read_project_detail(project_id)
        if detail is None:
            raise ProjectNotFoundError(f'unknown project_id {project_id!r}')
        return detail

    def _require_report_in_project(
        self, project_id: str, test_report_id: Optional[str]
    ) -> Optional[str]:
        """성적서 id 가 **이 프로젝트의 것인지** 확인한다.

        확인하지 않으면 세 가지가 한꺼번에 깨진다: (a) 프로젝트 A 가 B 의
        성적서에 목록을 붙일 수 있고, (b) 그 행이
        ``ux_test_equipment_lists_report_item`` 자연키를 **선점해 B 의 정당한
        생성을 409 로 만들며**(교차 테넌트 거부), (c) 아예 없는 id 는 FK 위반이
        되어 클라이언트 잘못이 503 으로 보고된다.

        ``_require_project`` 가 바로 옆에 있는데 성적서 축에만 빠져 있었다.
        """
        if not test_report_id:
            return None
        rid = require_uuid(test_report_id, 'test_report_id')
        for row in self._report_read.list_reports(project_id):
            if str(row.get('report_id') or '') == rid:
                return rid
        # 다른 프로젝트의 성적서도 404 다 — 존재를 알려주지 않는다.
        raise EquipmentListNotFoundError(f'test report not found in project: {rid}')

    def _require_list_in_project(self, project_id: str, equipment_list_id: str) -> dict:
        """목록을 읽고 경로 프로젝트 소속임을 확인한다.

        다른 프로젝트의 목록도 **404** 다 — 403 이면 그 id 가 존재한다는 사실을
        흘린다.
        """
        row = self._read.read_list(equipment_list_id)
        if row is None or str(row.get('project_id') or '') != project_id:
            raise EquipmentListNotFoundError(
                f'equipment list not found: {equipment_list_id}'
            )
        return row


def _validated_items(items: Sequence[dict]) -> list[dict]:
    """항목의 ``item_type`` 어휘를 경계에서 검증한다(→ 400).

    검증하지 않으면 오타/결측이 그대로 INSERT 로 흘러가 NOT NULL 또는
    ``ck_test_equipment_list_items_item_type`` CHECK 위반이 되고, 어댑터의 generic
    except 가 그것을 ``CentralTestEquipmentListError`` 로 감싸 **클라이언트 잘못이
    503(서버 장애)으로 보고된다** — CLAUDE.md ``Platform Boundary Honesty SSOT`` 의
    "클라이언트 오류를 서버 장애로 보고하지 않는다" 축 위반이다.

    알 수 없는 키는 여기서 거르지 않는다 — 스키마의 ``additionalProperties: false``
    가 먼저 거절하고, :func:`assign_sort_order` 가 화이트리스트로 한 번 더 막는다.
    """
    allowed = {member.value for member in ItemType}
    validated: list[dict] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f'items[{index}] must be an object')
        item_type = item.get('item_type')
        if item_type not in allowed:
            raise ValueError(
                f'items[{index}].item_type must be one of {sorted(allowed)}, '
                f'got {item_type!r}'
            )
        validated.append(item)
    return validated


def _require_test_item_key(value: object) -> str:
    """시험항목 키를 도메인 어휘로 정규화·검증한다(어휘 밖이면 → 400).

    판정은 전부 도메인 :func:`normalize_test_item_key` 가 한다 — 여기서 값 목록을
    다시 적으면 어휘 정의 사이트가 둘이 된다. 이 함수는 전송 계층의 느슨한 타입
    (``None`` / 숫자 / 공백)을 문자열로 좁히는 얇은 어댑터일 뿐이다.

    검증하지 않으면 오타가 그대로 INSERT 되어 자연키 unique 인덱스에 **성적서
    어느 편에도 대응하지 않는 행**이 남고, ②단계가 그 축으로 장비 표를 집을 때
    조용히 빈 결과를 준다.
    """
    return normalize_test_item_key(text(value))


def _list_envelope(row: dict) -> dict:
    return {
        'list_id': str(row.get('list_id') or row.get('id') or ''),
        'project_id': optional_text(row.get('project_id')),
        'test_report_id': optional_text(row.get('test_report_id')),
        'test_item_key': optional_text(row.get('test_item_key')),
        'test_item_name': optional_text(row.get('test_item_name')),
        'status': optional_text(row.get('status')),
        'source_profile_key': optional_text(row.get('source_profile_key')),
        'source_revision_key': optional_text(row.get('source_revision_key')),
        'source_pulled_at': optional_text(row.get('source_pulled_at')),
        'confirmed_at': optional_text(row.get('confirmed_at')),
        'created_at': optional_text(row.get('created_at')),
        'updated_at': optional_text(row.get('updated_at')),
        'item_count': int(row.get('item_count') or 0),
    }


def _item_envelope(row: dict) -> dict:
    return {
        'item_id': str(row.get('item_id') or row.get('id') or ''),
        'item_type': optional_text(row.get('item_type')),
        'section_name': optional_text(row.get('section_name')),
        'sort_order': int(row.get('sort_order') or 0),
        'description': optional_text(row.get('description')),
        'manufacturer': optional_text(row.get('manufacturer')),
        'model_name': optional_text(row.get('model_name')),
        'serial_number': optional_text(row.get('serial_number')),
        'calibration_due_date': optional_text(row.get('calibration_due_date')),
        'software_version': optional_text(row.get('software_version')),
        'remarks': optional_text(row.get('remarks')),
    }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid4_str() -> str:
    return str(uuid.uuid4())
