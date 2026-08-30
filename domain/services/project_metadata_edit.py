"""Project 성적서 메타 부분 편집 정책 (W3 백엔드, 2026-07-28).

프로젝트(=모델) 진입층의 성적서 표지 메타 8필드는 지금까지 **생성 시에만** 쓸 수
있었다(``CreateProjectRequest``). FCC ID·applicant 는 프로젝트 개시 뒤에 확정되는
것이 정상 업무 흐름이라, 오타나 후행 확정을 되돌릴 경로가 없다는 것이 결함이었다.

본 모듈은 그 ``PATCH`` 경로의 **판단**만 소유한다 (영속은 어댑터):

1. **편집 가능 필드 SSOT** — :data:`EDITABLE_PROJECT_META_FIELDS` 8필드. 어댑터의
   ``SET`` 컬럼 목록, OpenAPI 스키마, 봉인 테스트가 전부 이 튜플에서 파생한다
   (컬럼명 인라인 리터럴 0).
2. **소속 테이블 분할** — 8필드 중 ``manufacturer`` 만 ``device_models`` 소속이고
   나머지 7개가 ``projects`` 소속이다(central_db_schema.v1.json SSOT). 두 테이블을
   건드리므로 어댑터는 **하나의 트랜잭션**에서 갱신해야 한다(부분 성공 금지).
3. **부분 갱신 semantics** — 요청에 **없는 키 = 무변경**, 명시적 ``null`` = **삭제**.
   ``COALESCE(EXCLUDED, existing)`` 머지를 채택하지 **않는다**: 그 선택은 sample
   inventory upsert 에서 이미 "PM 명시적 값 삭제 워크플로우 미지원" 부작용으로
   기록된 함정이다. 따라서 파서는 ``key in body`` 로 키 존재를 먼저 판정하고
   **존재하는 키만** 반환한다 — ``body.get(key)`` 로 읽으면 두 경우가 ``None`` 으로
   뭉개져 계약이 그 자리에서 깨진다.
4. **범위 밖 필드의 loud 거부** — ``status`` 는 ``complete_project`` /
   ``reopen_project`` 액션 서브리소스가 상태 전이 SSOT 이고, ``model_name`` /
   ``project_code`` 는 로컬 측정 DB 파일명까지 전파되는 정체성(ADR-0005 재키잉)이다.
   둘 다 조용히 무시하지 않고 ``ValueError`` (→ 400) 로 거부한다 — 조용한 무시는
   "보냈는데 반영 안 됨"이라는 더 나쁜 실패 모드다.

도메인 순수: stdlib 만 import (infrastructure / psycopg / openpyxl / pandas /
PySide6 무의존).
"""
from __future__ import annotations

from collections.abc import Mapping as _MappingABC
from types import MappingProxyType
from typing import Mapping, Optional


__all__ = [
    'DEVICE_MODEL_META_FIELDS',
    'EDITABLE_PROJECT_META_FIELDS',
    'IMMUTABLE_PROJECT_FIELDS',
    'PROJECT_META_FIELD_TABLES',
    'PROJECT_TABLE_META_FIELDS',
    'device_model_table_updates',
    'parse_project_metadata_update',
    'project_table_updates',
]


#: 편집 가능한 성적서 메타 8필드 (SSOT). ``CreateProjectRequest`` 의 optional 필드
#: 집합과 동일하다 — 생성 시 쓸 수 있는 것은 수정도 할 수 있어야 한다.
EDITABLE_PROJECT_META_FIELDS: tuple[str, ...] = (
    'customer',
    'manufacturer',
    'management_number',
    'fcc_grantee_code',
    'applicant_name',
    'applicant_address',
    'eut_description',
    'test_standard',
)

#: PATCH 로 절대 바뀌지 않는 필드 → loud ``ValueError``.
#: - ``status``: 상태 전이 SSOT 는 complete/reopen 액션 서브리소스. PATCH 에 넣으면
#:   전이 경로가 둘이 되어 그 결정이 무효화된다.
#: - ``model_name`` / ``project_code``: ADR-0017 D1 의 1:1 정체성 키. 편집은 메타
#:   수정이 아니라 재키잉(ADR-0005) 문제다.
IMMUTABLE_PROJECT_FIELDS: tuple[str, ...] = (
    'status',
    'model_name',
    'project_code',
)

_PROJECTS_TABLE = 'projects'
_DEVICE_MODELS_TABLE = 'device_models'

#: 필드 → 소속 테이블. central_db_schema.v1.json 의 컬럼 배치와 1:1
#: (``manufacturer`` 만 device_models, 나머지 7개는 projects).
PROJECT_META_FIELD_TABLES: Mapping[str, str] = MappingProxyType({
    'customer': _PROJECTS_TABLE,
    'manufacturer': _DEVICE_MODELS_TABLE,
    'management_number': _PROJECTS_TABLE,
    'fcc_grantee_code': _PROJECTS_TABLE,
    'applicant_name': _PROJECTS_TABLE,
    'applicant_address': _PROJECTS_TABLE,
    'eut_description': _PROJECTS_TABLE,
    'test_standard': _PROJECTS_TABLE,
})

#: 테이블별 필드 튜플 (선언 순서 보존 — 어댑터 SET 절 순서가 결정적이어야
#: SQL 상수를 import 시점에 사전 생성할 수 있다).
PROJECT_TABLE_META_FIELDS: tuple[str, ...] = tuple(
    field for field in EDITABLE_PROJECT_META_FIELDS
    if PROJECT_META_FIELD_TABLES[field] == _PROJECTS_TABLE
)
DEVICE_MODEL_META_FIELDS: tuple[str, ...] = tuple(
    field for field in EDITABLE_PROJECT_META_FIELDS
    if PROJECT_META_FIELD_TABLES[field] == _DEVICE_MODELS_TABLE
)


def parse_project_metadata_update(
    body: Optional[Mapping],
) -> dict[str, Optional[str]]:
    """요청 본문을 (필드 → 값-or-``None``) 부분 갱신 맵으로 정규화한다.

    반환 dict 는 **본문에 실제로 존재한 키만** 담는다 — 부재 키는 아예 나타나지
    않으므로 호출자가 "무변경"과 "``null`` 삭제"를 구분할 수 있다. 값은 create
    경로(``_opt_text``)와 같은 규약으로 정규화한다: 문자열은 trim, 빈 문자열/공백
    문자열은 ``None``(삭제) — 두 경로가 다르면 "생성 땐 되는데 수정 땐 안 되는"
    비대칭이 생긴다.

    Raises:
        ValueError: 본문이 매핑이 아님 / ``status``·``model_name``·``project_code``
            동봉 / 미지 키 / 편집 가능 키가 하나도 없음. 전부 loud (→ 400) —
            조용한 무시나 조용한 no-op 은 만들지 않는다.
    """
    if body is None:
        raise ValueError('project metadata update body is required')
    if not isinstance(body, _MappingABC):
        raise ValueError(
            f'project metadata update body must be an object, got '
            f'{type(body).__name__}'
        )

    immutable = [key for key in IMMUTABLE_PROJECT_FIELDS if key in body]
    if immutable:
        raise ValueError(
            f'{immutable} cannot be changed via project metadata update — '
            f'status transitions use the complete/reopen action sub-resources and '
            f'model_name/project_code is the project identity (re-keying, not a '
            f'metadata edit)'
        )

    allowed = set(EDITABLE_PROJECT_META_FIELDS)
    unknown = sorted(key for key in body if key not in allowed)
    if unknown:
        raise ValueError(
            f'unknown project metadata field(s) {unknown} — editable fields are '
            f'{list(EDITABLE_PROJECT_META_FIELDS)}'
        )

    updates: dict[str, Optional[str]] = {}
    for field in EDITABLE_PROJECT_META_FIELDS:
        if field not in body:
            continue  # 키 부재 = 무변경 (SET 절에 나타나지 않는다)
        updates[field] = _optional_text(body[field])

    if not updates:
        raise ValueError(
            f'project metadata update requires at least one of '
            f'{list(EDITABLE_PROJECT_META_FIELDS)}'
        )
    return updates


def project_table_updates(
    updates: Mapping[str, Optional[str]],
) -> dict[str, Optional[str]]:
    """``projects`` 테이블 소속 갱신분만 (선언 순서로) 투영."""
    return _project_subset(updates, PROJECT_TABLE_META_FIELDS)


def device_model_table_updates(
    updates: Mapping[str, Optional[str]],
) -> dict[str, Optional[str]]:
    """``device_models`` 테이블 소속 갱신분만 (선언 순서로) 투영."""
    return _project_subset(updates, DEVICE_MODEL_META_FIELDS)


def _project_subset(
    updates: Mapping[str, Optional[str]], fields: tuple[str, ...],
) -> dict[str, Optional[str]]:
    return {field: updates[field] for field in fields if field in updates}


def _optional_text(value: object) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None
