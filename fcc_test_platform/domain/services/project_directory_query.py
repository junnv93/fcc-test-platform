"""프로젝트 디렉터리 조회 정책 — 검색 축 + keyset 전순서 (W3 백엔드, 2026-07-28).

``GET /platform/projects`` 는 지금까지 ``status`` 필터만 받고 **전량을 반환**했다.
프로젝트가 누적되면 O(N) 전송 + 클라이언트 필터가 되고, 현업이 1차 조회 키로 쓰는
**관리번호로 서버에 물어볼 방법이 아예 없었다**(계약 D2/D3).

본 모듈은 그 조회의 **판단**만 소유한다 (SQL 조립·영속은 어댑터):

1. **검색 축 SSOT** — :data:`PROJECT_SEARCH_COLUMNS`. 어댑터의 ``WHERE`` 절, 중앙
   스키마의 trigram 인덱스 이름, 봉인 테스트가 전부 이 튜플에서 파생한다
   (컬럼명 인라인 리터럴 0). ``management_number`` 가 반드시 포함된다.
   ``model_name`` 은 축에 없다 — ADR-0017 D1 이 ``project_code == model name`` 을
   못박았으므로 ``project_code`` 검색이 곧 모델명 검색이고, ``device_models`` 쪽
   컬럼을 축에 넣으면 조인 반대편에 인덱스를 하나 더 얹어야 한다(중복 비용).

   **2026-09-04 — 세 번째 축이 ``customer`` 에서 ``applicant_name`` 으로 이동했다.**
   두 컬럼은 성적서 표지에서 같은 주체(의뢰 주체)를 가리켰고, 그래서 같은 회사가
   두 칸으로 갈라져 있었다. 축이 ``customer`` 였으므로 **신청자 칸에만 적힌 회사는
   검색되지 않았다** — 검색이 데이터의 절반만 보고 있었던 셈이다. 폐기 결정
   (``project_metadata_edit.RETIRED_PROJECT_META_FIELDS``)에 따라 값이
   ``applicant_name`` 으로 합류했으므로 축도 그쪽으로 옮긴다. 인덱스 이름이 이
   튜플에서 파생되므로(§1) 마이그레이션 032 가 같은 규칙으로 새 인덱스를 만들고
   옛 인덱스를 지운다.
2. **전순서 keyset** — :data:`PROJECT_DIRECTORY_ORDER_COLUMNS` = ``(created_at, id)``.
   ``created_at`` 단독은 **전순서가 아니다**(같은 초에 만들어진 두 프로젝트가 동률).
   동률이 있으면 페이지 경계에서 행이 겹치거나 통째로 건너뛰어진다 — 그래서 유일
   컬럼 ``id`` 를 tie-breaker 로 반드시 동반한다. 단, 이 tie-breaker 는 **페이지
   경계가 있는 질의에만** 붙는다: 경계가 없는 pre-W3 무제한 읽기는 옛 정렬
   :data:`PROJECT_DIRECTORY_LEGACY_ORDER_COLUMNS` 를 그대로 쓴다(계약 S11).
   :func:`directory_order_columns` 가 그 선택의 단일 소유점이다.
3. **LIKE 패턴 이스케이프** — 사용자가 친 ``%``/``_`` 는 **와일드카드가 아니라 글자**다.
   이스케이프하지 않으면 ``50%`` 검색이 전체 매칭이 되고 ``A_1`` 이 ``A11`` 을 잡는다.
   :func:`search_like_pattern` 이 ``\\``/``%``/``_`` 를 :data:`SEARCH_LIKE_ESCAPE_CHAR`
   로 이스케이프한 뒤 부분일치 패턴으로 감싼다. 실측 확인: 명시적 ``ESCAPE`` 절이
   있어도 PostgreSQL trigram GIN 인덱스는 그대로 사용된다(계약 M6 증거 N).
4. **대소문자 무관은 ``LOWER()`` 양변으로** — ``ILIKE`` 는 PostgreSQL 전용이라 SQLite
   봉인 shim 에서 실행할 수 없고, 맨 ``LIKE`` 는 두 엔진의 대소문자 규약이 정반대다
   (PG=구분, SQLite=ASCII 무관). 양변을 ``LOWER`` 로 내리면 두 엔진에서 **같은 의미**가
   되고, PostgreSQL 은 ``LOWER(col)`` 표현식 인덱스로 그대로 커버한다.

도메인 순수: stdlib 만 import (infrastructure / psycopg / openpyxl / pandas /
PySide6 무의존). 페이지 크기 상한은 application 계층의 pagination SSOT 소관이라
여기서 다시 선언하지 않는다.
"""
from __future__ import annotations

from typing import Optional


__all__ = [
    'PROJECT_DIRECTORY_CURSOR_FIELDS',
    'PROJECT_DIRECTORY_LEGACY_ORDER_COLUMNS',
    'PROJECT_DIRECTORY_ORDER_COLUMNS',
    'PROJECT_SEARCH_COLUMNS',
    'SEARCH_LIKE_ESCAPE_CHAR',
    'directory_order_columns',
    'normalize_search_term',
    'search_like_pattern',
]


#: 서버측 검색이 훑는 컬럼 (SSOT). 전부 ``projects`` 테이블 소속이라 검색 인덱스가
#: 한 테이블에 모인다. ``management_number`` = 현업 1차 조회 키(계약 D3).
PROJECT_SEARCH_COLUMNS: tuple[str, ...] = (
    'management_number',
    'project_code',
    'applicant_name',
)

#: keyset 정렬 키 = ``projects`` 물리 컬럼, **전순서**(유일 컬럼 ``id`` 동반).
#: 디렉터리는 최신순이므로 두 키 모두 DESC 로 읽고, 커서 비교는 행-값 비교
#: ``(created_at, id) < (%s, %s)`` 한 번으로 표현된다(컬럼별 OR 분해 불필요).
PROJECT_DIRECTORY_ORDER_COLUMNS: tuple[str, ...] = ('created_at', 'id')

#: pre-W3 정렬 키 — ``created_at`` 단독. 전순서가 아니지만(동률 시 순서는 엔진 소관)
#: **페이지 경계가 없는** 질의에는 tie-breaker 가 필요 없고, 붙이면 기존 클라이언트가
#: 보던 무제한 응답의 행 순서가 바뀐다(계약 S11 backward compatibility).
#: 전순서의 **접두**라는 성질이 핵심이다 — 그래서 두 정렬은 서로 다른 순서가 아니라
#: "동률 구간만 더 좁힌 것"이고, ``created_at`` 이 다른 두 행의 상대 순서는 두 축에서
#: 항상 같다. 봉인 테스트가 이 접두 관계를 지킨다.
PROJECT_DIRECTORY_LEGACY_ORDER_COLUMNS: tuple[str, ...] = ('created_at',)

#: 위 정렬 키에 **위치로 대응**하는 read-row 키. 어댑터가 ``id`` 를 ``project_id``
#: 별칭으로 투영하므로 이름이 한 칸 다르다 — 커서를 만드는 쪽(서비스)은 행 dict 를
#: 보고 있으므로 이 튜플을 쓴다. 두 튜플의 arity·정렬 대응은 봉인 테스트가 지킨다.
PROJECT_DIRECTORY_CURSOR_FIELDS: tuple[str, ...] = ('created_at', 'project_id')

#: LIKE 메타문자 이스케이프 문자. PostgreSQL 의 기본값과 같지만 **명시적으로 ESCAPE
#: 절에 싣는다** — SQLite 는 ESCAPE 절이 없으면 이스케이프 문자 자체가 없어서
#: ``\%`` 가 "역슬래시 + 모든 것"으로 읽힌다(두 엔진 의미 분기).
SEARCH_LIKE_ESCAPE_CHAR = '\\'

_LIKE_METACHARACTERS: tuple[str, ...] = ('%', '_')


def directory_order_columns(*, paginated: bool) -> tuple[str, ...]:
    """정렬 키를 고른다 — **페이지 경계 유무**가 유일한 판단 기준이다.

    ``paginated=True`` (``LIMIT`` 또는 커서가 있는 질의) 면 전순서
    :data:`PROJECT_DIRECTORY_ORDER_COLUMNS` — 동률 tie-breaker 없이는 페이지 경계에서
    행이 중복되거나 누락된다.

    ``paginated=False`` (한 번에 전량을 돌려주는 pre-W3 읽기) 면 옛 정렬
    :data:`PROJECT_DIRECTORY_LEGACY_ORDER_COLUMNS`. 경계가 없으므로 tie-breaker 가
    **정확성에 기여하는 바가 없고**, 붙이면 응답 행 순서만 기존과 달라진다(계약 S11).
    """
    return (
        PROJECT_DIRECTORY_ORDER_COLUMNS if paginated
        else PROJECT_DIRECTORY_LEGACY_ORDER_COLUMNS
    )


def normalize_search_term(term: object) -> Optional[str]:
    """검색어를 정규화한다. 공백뿐이거나 부재면 ``None`` (= 필터 없음).

    빈 문자열을 400 으로 거부하지 않는 이유: UI 가 검색창을 비우면 ``?q=`` 가 그대로
    실려 온다. 그것은 "검색 안 함"이지 잘못된 요청이 아니다 — 거부하면 검색창을
    지울 때마다 에러가 뜬다.
    """
    if term is None:
        return None
    cleaned = str(term).strip()
    return cleaned or None


def search_like_pattern(term: str) -> str:
    """검색어를 대소문자 무관 부분일치 ``LIKE`` 패턴으로 변환한다.

    ``LOWER(column) LIKE <pattern> ESCAPE '\\'`` 의 우변으로 쓰인다 — 좌변이 이미
    ``LOWER`` 이므로 패턴도 소문자로 내린다. 이스케이프는 **역슬래시 먼저**:
    나중에 하면 방금 삽입한 이스케이프 문자를 다시 이스케이프해 ``\\%`` 가
    ``\\\\%`` 로 뭉개진다.
    """
    lowered = str(term).strip().lower()
    escaped = lowered.replace(
        SEARCH_LIKE_ESCAPE_CHAR, SEARCH_LIKE_ESCAPE_CHAR * 2,
    )
    for meta in _LIKE_METACHARACTERS:
        escaped = escaped.replace(meta, SEARCH_LIKE_ESCAPE_CHAR + meta)
    return f'%{escaped}%'
