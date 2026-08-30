"""측정 세션의 **선언된 출처** — 어느 경로로 왔고, 어느 워크북을 썼나.

PC 단위 모드 배타 판정(운영자 2026-08-16)은 *"프로젝트는 시작한 모드로 끝난다"* 를
**운영 규칙**으로 둔다. 이 저장소가 세 번 적은 문장이 그 옆에 있다 — *아무것도 강제하지
않는 규칙은 건너뛸 수 있는 규칙이다.* 그런데 오늘은 규칙이 지켜졌는지 **볼 수조차 없다**:
중앙 ``test_sessions`` 에 경로/모드 칸이 0개이고, 세션에 워크북 식별자가 0건이다.

이 모듈은 그 두 사실에 **이름**을 준다. 새 기전이 아니라 **있는 사실을 적는 일**이다 —
업로드 핸들은 이미 내용 지문 파생이므로(``workbook_upload_policy.mint_workbook_handle``)
핸들만 기록하면 *"두 챔버가 같은 계획을 썼는가"* 가 조회 한 번으로 답해진다. 새 비교
로직도 새 해싱도 필요 없다.

**게이트가 아니다.** 프로젝트가 두 모드에 걸치는 것을 막는 코드는 이 축에 없다. 목적은
관측이고, 위반이 *일어난 뒤에* 조사 가능해지는 것이 이득 전부다. 막으려면 프로젝트-챔버
배정에 게이트를 걸어야 하고 그것은 시범 단계에 과하다(운영자 판정 2026-08-16).

**어휘는 provider 중립이다.** 답하는 문장은 하나뿐이다:

    이 측정이 **웹 세션으로** 왔나.

*"GUI 였나"* 는 provider 어휘다 — FCC 의 GUI · KC 의 MPTool · mmWave 의 자기 프로그램은
서로 다른 물건이고, 그것을 공통 enum 으로 만들면 provider 가 늘 때마다 중앙 마이그레이션이
필요해진다(``CLAUDE.md`` §Chamber Equipment Config SSOT 가 이미 이름 붙인 형태).
웹 세션을 받지 않은 PC 가 **무엇으로** 측정했는지는 provider 의 일이고 중앙은 알지 않는다.

**추론이 아니라 선언이다.** 오늘 간접 신호가 몇 개 있다 — 업로드 경로 문자열, 러너 클래스,
``chamber_id`` 유무. 전부 **우연한 부작용**이라 설정 하나로 사라진다. 그래서 값은 합성
루트가 선언하고, 클라이언트는 보낼 수 없다(요청 스키마에 없다). *어느 합성 루트가 이
세션을 만들었는가* 가 곧 값이다 — ``RevisionProvenanceKind`` 가 *어느 operation 이
돌았는가* 를 값으로 삼는 것과 같은 규율.

**미선언은 "모름"이지 "로컬"이 아니다.** 기본값으로 토큰을 넣지 않는다 — 넣으면 origin 을
선언하지 않은 웹 경로가 "로컬 프로그램"이라고 거짓말하고, 그 거짓말은 조사할 때 진실과
구분되지 않는다. 이 저장소가 지문 축에서 이미 내린 결론이다(*NULL 은 "모름"이지 "동일"이
아니다*).

⚠️ **``(str, Enum)`` 을 쓰지 않는다.** Python 3.11+ 에서 그 조합의 ``str(member)`` 는 값이
아니라 ``'SessionOrigin.WEB_SESSION'`` 을 돌려준다. 이 저장소는 그 함정에 이미 값을 치렀다
(``AntennaIdentifier`` — ALL1+ALL2 매칭이 영구 불일치가 되어 result_sum 이 영원히 None).
저장·비교는 **언제나 ``.value``** 이고, 그 사실을 ``tests/test_session_origin_axis.py`` 가
실행으로 단언한다.

순수 — stdlib only.
"""
from __future__ import annotations

from enum import Enum
from typing import Mapping, Optional

from domain.services.workbook_upload_policy import is_valid_workbook_handle


__all__ = [
    'SESSION_ORIGIN_VALUES',
    'SESSION_ORIGIN_PAYLOAD_KEY',
    'SessionOrigin',
    'WORKBOOK_HANDLE_PAYLOAD_KEY',
    'normalize_workbook_handle',
    'parse_session_origin',
    'session_origin_value',
    'session_provenance_columns',
]


class SessionOrigin(Enum):
    """이 측정이 웹 세션으로 왔나 — 그 한 문장에만 답하는 2-토큰 어휘.

    ``LOCAL_PROGRAM`` 은 *"웹 세션이 아니었다"* 이고 **무엇이었는지는 말하지 않는다**.
    그것이 provider 중립의 내용 전부다.
    """

    #: 세션 API(``POST /session/start``)를 통해 시작됐다.
    WEB_SESSION = 'WEB_SESSION'
    #: 세션 API 를 통하지 않았다 — provider 의 로컬 프로그램이 시작했다.
    LOCAL_PROGRAM = 'LOCAL_PROGRAM'


#: 저장·전송에 쓰이는 토큰 집합. 중앙 스키마 ``allowed_values`` 와 마이그레이션 CHECK 가
#: 이 집합과 3자 parity 를 이룬다 — 손으로 세 번 적으면 갈라지는 쪽은 언제나 사본이다.
SESSION_ORIGIN_VALUES: tuple[str, ...] = tuple(member.value for member in SessionOrigin)

#: outbox payload 안의 키. 중앙 sync 어댑터가 같은 이름으로 읽는다.
#: (중앙에는 로컬 DB 가 없으므로 사실은 조회되지 않고 실려 온다 — artifact 축과 동형.)
SESSION_ORIGIN_PAYLOAD_KEY = 'session_origin'
WORKBOOK_HANDLE_PAYLOAD_KEY = 'workbook_handle'


def session_origin_value(origin: Optional[SessionOrigin]) -> str:
    """저장·전송용 토큰 ('' = 미선언).

    ``str(origin)`` 을 쓰지 않는 것이 요점이다 — 위 ⚠️ 참조.
    """
    if origin is None:
        return ''
    if not isinstance(origin, SessionOrigin):
        # 다른 enum 이나 문자열을 받았다면 그것은 결함이다. 그러나 이 축은 관측이므로
        # 측정을 죽이지 않는다 — 미선언으로 degrade 하고 호출자가 loud 하게 남긴다.
        return ''
    return origin.value


def parse_session_origin(raw: object) -> Optional[SessionOrigin]:
    """저장된 토큰 → enum. **total** — 어떤 입력에도 raise 하지 않는다.

    이 함수는 이미 기록된 행과 이미 전송된 봉투를 읽는다. 거기 무엇이 들어 있든
    *읽는 쪽이 죽는 것*은 답이 아니다 — 모르는 토큰과 빈 칸은 둘 다 ``None``(모름)이다.

    ⚠️ 쓰는 쪽은 다르다. 기록되는 값은 언제나 이 모듈의 enum 에서 나오고, 중앙 CHECK 가
    그 집합 밖의 값을 거절한다. 관용은 **읽기 전용**이다.
    """
    if isinstance(raw, SessionOrigin):
        return raw
    if not isinstance(raw, str):
        return None
    token = raw.strip()
    if not token:
        return None
    for member in SessionOrigin:
        if member.value == token:
            return member
    return None


def normalize_workbook_handle(raw: object) -> Optional[str]:
    """기록할 워크북 핸들 ('' / 문법 위반 / 미전달 → ``None``).

    핸들은 **우리 저장소가 민팅한 토큰**이다(sha256 소문자 64자). 그러므로 문법을 만족
    하지 않는 값이 여기 도달하면 그것은 호출자 잘못이 아니라 **결함**이다 — 그래서
    조용히 통과시키지 않는다.

    ⚠️ 그렇다고 raise 하지도 않는다. 이 함수는 측정이 도는 중에 세션 행을 쓰는 자리에서
    불린다. 진단 하나가 측정을 죽이면 이 축은 관측이 아니라 게이트가 되고, 그것은 계획서
    §3 이 이름으로 금지한 것이다. 호출자가 ``None`` 을 받고 **이름을 대며 경고**한다 —
    거부는 이미 경계(``WorkbookUploadStorePort.resolve``)가 typed 404 로 하고 있다.
    """
    if not isinstance(raw, str):
        return None
    token = raw.strip()
    if not token:
        return None
    return token if is_valid_workbook_handle(token) else None


def session_provenance_columns(
    *,
    session_origin: Optional[SessionOrigin] = None,
    workbook_handle: object = None,
) -> Mapping[str, str]:
    """선언된 두 사실 → 저장 컬럼 값 (미선언 키는 **없다**).

    비어 있는 값을 키로 싣지 않는 것이 형제 ``map_test_session_record`` 와 같은 규율이다:
    미선언 세션이 오늘과 **byte-identical** 한 행을 남겨야, 이 축이 켜진 것과 그 세션이
    로컬이었던 것이 구분된다.
    """
    columns: dict[str, str] = {}
    origin_token = session_origin_value(session_origin)
    if origin_token:
        columns[SESSION_ORIGIN_PAYLOAD_KEY] = origin_token
    handle = normalize_workbook_handle(workbook_handle)
    if handle:
        columns[WORKBOOK_HANDLE_PAYLOAD_KEY] = handle
    return columns
