"""dataclass 에 **선언된** 기본값을 읽는다 — 센티널이 새어 나가지 않게.

두 런타임 설정(`application/session/runtime_config.py` ·
`application/headless/runtime_config.py`)은 환경변수가 비었을 때 dataclass 필드의
기본값으로 되돌아간다. 그 되돌림이 이렇게 쓰여 있었다::

    app_title=(
        read_text(env, _ENV['app_title'])
        or defaults['app_title'].default          # ← cls.__dataclass_fields__
    )

⚠️ **``Field.default`` 는 「기본값」이 아니다.** 필드에 기본값이 선언돼 있지 않으면 그
자리에는 값이 아니라 ``dataclasses.MISSING`` **센티널 객체**가 들어 있고, 그것은 예외
없이 ``str`` 필드로 그대로 흘러간다. 실측(최소 재현, 2026-09-06)::

    defaults['first'].default  →  <dataclasses._MISSING_TYPE object at 0x…>
    _Probe(first=그 값)         →  생성 성공. 예외 0건. 타입은 _MISSING_TYPE.

그러면 앱 제목이 ``<dataclasses._MISSING_TYPE object at 0x7f…>`` 가 되고, 화면과
로그에 그 문자열이 그대로 뜬다. mypy 는 이 자리를 8건의
``Any | Literal[_MISSING_TYPE.MISSING]`` 로 짚고 있었다.

── 오늘 왜 안 터졌나, 그리고 그 안전이 왜 «우연»인가 ───────────────────────────
실측 2026-09-06: 이 패턴으로 읽는 필드는 16개이고 **그중 기본값이 없는 필드는 0개**다.
그래서 오늘 센티널은 흐르지 않는다. 그러나 그 안전은 **두 목록이 우연히 포개진 것**이고
아무도 그 포개짐을 검사하지 않는다:

  * ``HeadlessApiConfig.db_path`` 는 **이미 기본값이 없는 필드**다. 맨 앞에 있어서
    dataclass 정의는 통과한다 — 즉 「기본값 없는 필드」가 이 저장소에 실재한다.
  * 기본값이 있는 필드에서 그것을 지우면? 그 필드가 기본값 있는 필드 «뒤»에 있으면
    파이썬이 ``TypeError: non-default argument … follows default argument`` 로 클래스
    정의를 거절한다 — 오늘의 방어는 **필드 순서**에 얹혀 있다. 순서가 바뀌거나 새 필드가
    앞쪽에 들어오는 날 그 방어는 조용히 사라진다.

── 그래서 이 함수가 하는 일 ────────────────────────────────────────────────────
1. 센티널이면 **그 자리에서** 이름을 가진 오류로 죽는다. 설정 로딩은 프로세스 시작
   지점이라, 여기서 죽는 것이 화면에 센티널이 뜨는 것보다 언제나 낫다.
2. 선언된 타입과 다르면 그것도 죽는다. ``Field.default`` 는 mypy 가
   ``Any`` 로밖에 볼 수 없어서, 그 검사를 **런타임으로 옮기지 않으면 아무도 안 본다.**
3. 호출부가 기대 타입을 적으므로 반환 타입이 ``Any`` 가 아니다 — 그것이 이 자리에서
   mypy 를 되살리는 유일한 방법이다.
"""
from __future__ import annotations

from dataclasses import Field, MISSING
from typing import Any, Mapping, TypeVar

__all__ = ['declared_default']

_T = TypeVar('_T')


def declared_default(
    fields: Mapping[str, 'Field[Any]'], name: str, expected: type[_T],
) -> _T:
    """``fields[name]`` 에 **선언된** 기본값. 없거나 타입이 다르면 죽는다.

    ``fields`` 는 보통 ``cls.__dataclass_fields__`` 다.
    """
    try:
        field = fields[name]
    except KeyError:
        raise KeyError(
            f'{name!r} is not a declared field; '
            f'known fields: {sorted(fields)}'
        ) from None
    value = field.default
    if value is MISSING:
        raise ValueError(
            f'field {name!r} declares no default, so its fallback would be the '
            f'dataclasses.MISSING sentinel — give it a default or stop falling '
            f'back to one'
        )
    if not isinstance(value, expected):
        raise TypeError(
            f'field {name!r} declares a {type(value).__name__} default '
            f'({value!r}) but {expected.__name__} was expected here'
        )
    return value
