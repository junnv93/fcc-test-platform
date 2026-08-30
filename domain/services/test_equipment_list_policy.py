"""성적서 §6 장비목록 정책 (pure domain, stdlib only) — 2026-08-07.

FCC 성적서의 ``6. TEST AND MEASUREMENT EQUIPMENT`` 절은 표 **두 개**로 이루어진다:

    Test Equipment List
    Description | Manufacturer | Model | S/N | Cal Due

    UL Software
    Description | Manufacturer | Model | Version

EMS(사내 장비관리시스템)가 팀 × 시험항목 × 챔버별 **표준 장비리스트**의 SSOT 이고,
FCC 는 성적서 작성 시점에 시험원이 고른 **실사용본**을 기록·확정한다. 이 모듈은 그
기록에 필요한 규칙 — 어휘, 두 표의 열 순서, 정렬, 확정 가능성, 동결 — 의 단일
출처다. 인프라(중앙 DB / OpenAPI / 웹 화면 / 이후의 DOCX patcher)는 전부 여기서
파생하고, 자기 사본을 만들지 않는다.

**왜 열 순서가 도메인에 있는가.** 같은 순서를 백엔드 어댑터의 INSERT 목록,
OpenAPI item 스키마, 웹 편집기의 표 헤더, 그리고 (③단계에서) DOCX patcher 가 모두
필요로 한다. 사본을 넷 두면 성적서 표의 열 순서가 조용히 갈라진다 — 그리고 그
드리프트는 제출된 성적서에서만 드러난다. 그래서 순서는 여기서 한 번 선언되고
상세 응답이 ``tables[]`` 로 실어 내려보내며, 프론트는 그것을 **읽기만** 한다
(CLAUDE.md ``Derived-Value No-Client-Recompute SSOT``).

값 어휘(``ItemType`` / ``ListStatus``)는 세 곳이 일치해야 한다:

1. 이 모듈의 enum,
2. ``docs/platform/central_db_schema.v1.json`` 의 ``allowed_values``,
3. ``docs/platform/migrations/015_test_equipment_lists.sql`` 의 CHECK 제약.

셋의 동일성은 ``tests/test_test_equipment_list_policy.py`` 가 봉인한다.

**시험항목 축은 EMS 가 아니라 FCC 성적서가 정한다** (:class:`TestItemKey`).

dependency-free: stdlib 만 사용 (infrastructure / application / reporting /
psycopg / fastapi / sqlalchemy / pandas / openpyxl / PySide6 import 0).

"""
from __future__ import annotations

from enum import Enum
from typing import Iterable, Mapping, Optional


__all__ = [
    'ItemType',
    'ListStatus',
    'TestItemKey',
    'TEST_ITEM_KEYS',
    'normalize_test_item_key',
    'EQUIPMENT_TABLE_COLUMNS',
    'SOFTWARE_TABLE_COLUMNS',
    'TABLE_COLUMNS',
    'ITEM_PERSISTED_FIELDS',
    'CONFIRM_REFUSAL_ALREADY_CONFIRMED',
    'CONFIRM_REFUSAL_EMPTY_LIST',
    'assign_sort_order',
    'confirm_refusal_reason',
    'is_frozen',
    'table_specs',
]


class ItemType(str, Enum):
    """장비목록 행의 종류 — 성적서 §6 의 두 표에 각각 대응한다."""

    EQUIPMENT = 'equipment'
    TEST_SOFTWARE = 'test_software'


class ListStatus(str, Enum):
    """목록의 생애.

    ``draft`` 는 시험원이 계속 고칠 수 있는 상태다. ``confirmed`` 는 성적서에
    실린 스냅샷이며 **동결**된다 — 확정 후 값이 바뀌면 이미 발행한 성적서를
    재생성했을 때 표가 달라진다.
    """

    DRAFT = 'draft'
    CONFIRMED = 'confirmed'


class TestItemKey(str, Enum):
    """장비목록이 귀속되는 **시험항목** — FCC 성적서 한 편에 대응한다.

    **왜 이 어휘인가.** 성적서는 기술별로 파일이 갈린다(E6~E9). 그 구분은 이미
    ``reporting.domain.models.report_bundle.ReportTech`` 가 갖고 있고,
    ``tech_partitioner`` 가 측정 행을 그 4개로 나누며,
    ``templates/fcc/{dts,ble,bt,unii}_template.docx`` 가 각각 한 편이다. 즉
    **어떤 측정이 어떤 성적서를 발행하는지는 FCC 가 아는 사실**이다.

    EMS(사내 장비관리시스템)는 프로젝트/성적서 도메인 지식이 없어 표준 표기로
    ``'DFS, UNII'`` 처럼 적는다. 그것을 그대로 축으로 삼으면 항목 하나가 성적서
    여러 편에 걸치고, ②단계에서 "이 성적서의 장비목록"을 집을 수 없다. **어휘는
    소비하는 쪽이 아니라 발행하는 쪽이 정한다.**

    **``ReportTech`` 를 직접 import 하지 않는 이유.** ``src/domain/`` →
    ``src/reporting/`` import 는 이 저장소에 선례가 0건이고(반대 방향은 다수),
    여기서 만들면 계층 방향이 뒤집힌다. 대신 값·순서의 동일성을 parity 테스트로
    잠근다 — ``ItemType``/``ListStatus`` 가 중앙 스키마 ``allowed_values`` 및 015
    CHECK 와 맺는 관계와 같은 형태다(``tests/test_test_equipment_list_policy.py``).

    **DB CHECK 를 두지 않는 이유.** ``item_type``/``status`` 와 달리 이 값 집합은
    provider 가 늘면 확장된다(mmWave·UWB 는 각각 다른 성적서다). CHECK 로 굳히면
    확장마다 중앙 마이그레이션이 필요하고, 중앙 스키마는 provider 공통 표면이라
    그 비용이 크다. 그래서 컬럼은 ``text`` 로 두고 검증은 **애플리케이션 경계**가
    한다. 어휘를 늘릴 때 고칠 곳은 이 enum 하나이며, 스키마 enum·프론트 선택지·
    parity 테스트는 전부 여기서 파생한다.
    """

    DTS = 'DTS'    # E6 — 2.4GHz WLAN
    BLE = 'BLE'    # E7 — Bluetooth Low Energy
    BT = 'BT'      # E8 — Bluetooth Classic
    UNII = 'UNII'  # E9 — 5/6GHz WLAN


#: 시험항목 어휘 — **선언 순서가 성적서 파일 순서(E6~E9)** 다. 프론트 선택지와
#: OpenAPI enum 이 이 순서를 그대로 쓴다.
TEST_ITEM_KEYS: tuple[str, ...] = tuple(member.value for member in TestItemKey)


def normalize_test_item_key(value: object) -> str:
    """시험항목 키를 canonical 형태로 만든다. 어휘 밖이면 ``ValueError``.

    ``strip().upper()`` 로 정규화한 뒤 검증한다. 관용을 두는 이유는 입력을 넓게
    받으려는 것이 아니라 **같은 시험항목이 두 행이 되는 것을 막기 위해서**다 —
    자연키 unique 인덱스는 정확한 텍스트 비교이므로, ``'bt'`` 와 ``'BT'`` 가 둘 다
    통과하면 한 프로젝트에 같은 시험항목의 목록이 둘 생긴다. 어휘가 대문자 4개로
    닫혀 있어 대소문자 접기에 모호성이 없다.

    **읽기 경로는 소급 검증하지 않는다.** 이미 저장된 행은 있는 그대로 돌려준다 —
    이 함수는 쓰기 경계의 게이트이지 데이터 마이그레이션이 아니다.
    """
    token = str(value or '').strip().upper()
    if not token:
        raise ValueError('test_item_key must not be blank')
    if token not in TEST_ITEM_KEYS:
        raise ValueError(
            f'test_item_key must be one of {list(TEST_ITEM_KEYS)}, got {value!r}'
        )
    return token


#: 장비표(Test Equipment List)의 열 순서. 성적서 양식 B:F 그대로다.
EQUIPMENT_TABLE_COLUMNS: tuple[str, ...] = (
    'description',
    'manufacturer',
    'model_name',
    'serial_number',
    'calibration_due_date',
)

#: 시험용 소프트웨어표(UL Software)의 열 순서. 마지막 열이 S/N·Cal Due 가 아니라
#: Version 하나다 — 두 표를 같은 열 목록으로 그리면 안 되는 이유.
SOFTWARE_TABLE_COLUMNS: tuple[str, ...] = (
    'description',
    'manufacturer',
    'model_name',
    'software_version',
)

#: item_type → 그 표의 열 순서. 렌더러(웹·DOCX)가 소비하는 유일한 출처.
TABLE_COLUMNS: Mapping[ItemType, tuple[str, ...]] = {
    ItemType.EQUIPMENT: EQUIPMENT_TABLE_COLUMNS,
    ItemType.TEST_SOFTWARE: SOFTWARE_TABLE_COLUMNS,
}

#: 클라이언트가 보낼 수 있고 어댑터가 영속하는 항목 필드.
#:
#: ``sort_order`` 는 **여기 없다** — 서버가 제출 배열의 위치에서 부여한다
#: (:func:`assign_sort_order`). 클라이언트가 보내게 두면 파생값 재조립이 되고,
#: 순서와 배열이 어긋난 요청을 어느 쪽이 옳은지 판정해야 한다.
#: ``remarks`` 는 화면 전용 운영 메모이며 §6 표에는 출력되지 않는다.
ITEM_PERSISTED_FIELDS: tuple[str, ...] = (
    'item_type',
    'section_name',
    'description',
    'manufacturer',
    'model_name',
    'serial_number',
    'calibration_due_date',
    'software_version',
    'remarks',
)

#: 확정 거부 사유 토큰. 호출자가 문자열을 비교하지 않도록 명명 상수로 노출한다.
CONFIRM_REFUSAL_ALREADY_CONFIRMED: str = 'already_confirmed'
CONFIRM_REFUSAL_EMPTY_LIST: str = 'empty_list'


def assign_sort_order(items: Iterable[Mapping]) -> list[dict]:
    """제출 배열의 위치를 ``sort_order`` 로 부여한 새 dict 목록을 반환한다.

    순서는 **위치가 곧 정의**다. 그래서 items 쓰기는 전량 교체(PUT) 하나이고,
    행 단위 추가/삭제 API 를 두지 않는다 — 두면 "3번과 5번을 바꿔라"라는 두 번째
    계약이 반드시 따라온다.

    입력 매핑은 변형하지 않으며, :data:`ITEM_PERSISTED_FIELDS` 에 있는 키만
    복사한다(계약 밖 키는 조용히 버리는 것이 아니라 애초에 스키마의
    ``additionalProperties: false`` 가 먼저 거절한다 — 여기는 두 번째 방어선).
    """
    result: list[dict] = []
    for index, item in enumerate(items):
        row = {field: item.get(field) for field in ITEM_PERSISTED_FIELDS}
        row['sort_order'] = index
        result.append(row)
    return result


def is_frozen(status: object) -> bool:
    """확정된 목록인가 — 확정본은 편집할 수 없다(→ 409).

    ``status`` 는 DB/전송 경로에서 평문 문자열로 오므로 enum 과 문자열을 모두
    받는다. 알 수 없는 값은 **동결로 보지 않는다** — 모르는 상태를 동결로 읽으면
    시험원이 자기 초안을 영영 못 고치게 된다(그 반대는 확정본을 한 번 덧쓰는
    것이고, 그것은 어댑터의 조건부 UPDATE 가 다시 막는다).
    """
    return _status_token(status) == ListStatus.CONFIRMED.value


def confirm_refusal_reason(status: object, item_count: int) -> Optional[str]:
    """확정을 거부할 이유를 반환한다. 확정 가능하면 ``None``.

    - 이미 확정됨 → :data:`CONFIRM_REFUSAL_ALREADY_CONFIRMED`
      (재확정은 ``confirmed_at`` 을 갱신해 스냅샷 시점을 흐린다)
    - 항목이 0개 → :data:`CONFIRM_REFUSAL_EMPTY_LIST`.
      빈 §6 표는 이미 ``report_generation_service`` 가 hard-fail 로 거부하는
      상태이므로, 빈 목록을 확정하게 두면 성적서 생성 단계까지 가서야 실패한다.
    """
    if is_frozen(status):
        return CONFIRM_REFUSAL_ALREADY_CONFIRMED
    if item_count <= 0:
        return CONFIRM_REFUSAL_EMPTY_LIST
    return None


def table_specs() -> list[dict]:
    """상세 응답이 실어 보내는 표 명세 — ``[{item_type, columns}, …]``.

    선언 순서(장비 → 소프트웨어)가 성적서 §6 의 표 순서다. 소비자(웹 편집기,
    ③단계 DOCX patcher)는 이 목록을 순회해 표를 그리고, 열 이름을 자기 쪽에
    다시 적지 않는다.
    """
    return [
        {'item_type': item_type.value, 'columns': list(columns)}
        for item_type, columns in TABLE_COLUMNS.items()
    ]


def _status_token(status: object) -> str:
    """enum 이든 평문이든 하나의 비교 가능한 토큰으로 만든다.

    ``ListStatus`` 는 ``(str, Enum)`` 이지만 Python 3.11+ 에서 ``str(member)`` 는
    값이 아니라 ``'ListStatus.CONFIRMED'`` 를 돌려준다 — 그 함정으로 antenna sum
    매칭이 한 번 깨진 전례가 있다(``db_utils._match_text`` 참조). 그래서
    ``.value`` 를 먼저 본다.
    """
    if isinstance(status, Enum):
        return str(status.value)
    return str(status or '')
