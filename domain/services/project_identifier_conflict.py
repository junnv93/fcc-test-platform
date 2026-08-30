"""프로젝트 정체성 키 UNIQUE 충돌 분류 정책 (W3 백엔드, 2026-07-28).

``projects`` 에는 사람이 발번하는 UNIQUE 키가 둘 있다 — ``project_code``(= 모델명,
ADR-0017 D1 정체성)와 ``management_number``(PM 관리번호, 성적서 번호의 근간).
지금까지 이 둘의 중복은 드라이버 예외 → ``CentralProjectError`` → **503** 으로
표면화됐다. 즉 프론트가 "이미 쓰는 관리번호"(사람이 고칠 수 있는 입력 실수)와
"백엔드 장애"(사람이 못 고침)를 **구분할 수 없었다**(계약 D4).

본 모듈은 그 분류의 **판단**만 소유한다:

1. **제약 이름 SSOT** — :data:`PROJECT_UNIQUE_CONSTRAINTS` 는 중앙 스키마
   (``docs/platform/central_db_schema.v1.json`` 의 ``projects.indexes``) 의 UNIQUE
   인덱스 이름 ↔ 충돌 필드 매핑이다. 두 자산이 어긋나면 충돌이 다시 조용히 503 이
   되므로 봉인 테스트가 스키마 JSON 과 대조한다.
2. **드라이버 무의존 탐지** — 어댑터는 psycopg 를 import 하지 않는다(주입된
   ``connection_factory`` 규약). 그래서 예외 **타입**이 아니라 표준 SQLSTATE
   (``23505``) + 예외 문자열의 제약 토큰으로 판정한다. psycopg3 는 ``.sqlstate``,
   psycopg2 는 ``.pgcode`` 로 같은 값을 노출하고, 봉인 shim 의 ``sqlite3`` 는
   SQLSTATE 가 없는 대신 메시지에 ``projects.management_number`` 를 싣는다 — 두
   토큰 형태를 모두 받는 이유다.
3. **모르면 조용히 삼키지 않는다** — 어느 필드인지 판정되지 않으면 ``None`` 을 돌려
   호출자가 기존 ``CentralProjectError``(503) 경로를 그대로 타게 한다. 정체불명의
   무결성 오류를 409 "관리번호 중복"으로 둔갑시키는 것이 더 나쁜 실패 모드다.

도메인 순수: stdlib 만 import.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Optional


__all__ = [
    'PROJECT_CONFLICT_RESOURCE',
    'PROJECT_UNIQUE_CONSTRAINTS',
    'UNIQUE_VIOLATION_SQLSTATE',
    'classify_project_unique_violation',
]


#: SQL 표준 ``unique_violation`` SQLSTATE (PostgreSQL 23505).
UNIQUE_VIOLATION_SQLSTATE = '23505'

#: RFC 9457 ``params.resource`` 로 실리는 **리소스 종류**(사용자가 넣은 식별자 값이
#: 아니다 — ``PROBLEM_PARAM_ALLOWLIST`` 의 PII 가드 규약).
PROJECT_CONFLICT_RESOURCE = 'project'

_PROJECTS_TABLE = 'projects'

#: UNIQUE 인덱스 이름 → 충돌 필드. 중앙 스키마 ``projects.indexes`` 의 unique 항목과
#: 1:1 (봉인 테스트가 대조).
PROJECT_UNIQUE_CONSTRAINTS: Mapping[str, str] = MappingProxyType({
    'ux_projects_management_number': 'management_number',
    'ux_projects_project_code': 'project_code',
})

#: 드라이버가 SQLSTATE 를 노출하지 않을 때(sqlite3) 쓰는 메시지 지문. 소문자 비교.
_UNIQUE_MESSAGE_MARKERS: tuple[str, ...] = (
    'unique constraint',
    'unique violation',
    'duplicate key',
)


def classify_project_unique_violation(
    *, sqlstate: object, message: object,
) -> Optional[str]:
    """UNIQUE 위반이면 충돌한 ``projects`` 필드명을, 아니면 ``None`` 을 돌려준다.

    Args:
        sqlstate: 드라이버가 노출한 SQLSTATE (psycopg ``.sqlstate`` / ``.pgcode``).
            ``None`` 이면 메시지 지문으로 폴백한다(sqlite3 경로).
        message: 예외 문자열. 제약 이름(``ux_projects_management_number``) 또는
            ``projects.<column>`` 토큰을 담는다.

    Returns:
        :data:`PROJECT_UNIQUE_CONSTRAINTS` 의 값 중 하나, 또는 ``None``
        (UNIQUE 위반이 아니거나 어느 키인지 판정 불가 — 호출자는 기존 503 경로 유지).
    """
    text = '' if message is None else str(message).lower()
    if not _is_unique_violation(sqlstate, text):
        return None
    for constraint, field in PROJECT_UNIQUE_CONSTRAINTS.items():
        if constraint.lower() in text:
            return field
        # sqlite3: "UNIQUE constraint failed: projects.management_number"
        if f'{_PROJECTS_TABLE}.{field}'.lower() in text:
            return field
    return None


def _is_unique_violation(sqlstate: object, lowered_message: str) -> bool:
    code = '' if sqlstate is None else str(sqlstate).strip()
    if code:
        # SQLSTATE 를 노출하는 드라이버에서는 그것이 권위다 — 다른 코드가 왔다면
        # 메시지에 'unique' 가 섞여 있어도 UNIQUE 위반이 아니다.
        return code == UNIQUE_VIOLATION_SQLSTATE
    return any(marker in lowered_message for marker in _UNIQUE_MESSAGE_MARKERS)
