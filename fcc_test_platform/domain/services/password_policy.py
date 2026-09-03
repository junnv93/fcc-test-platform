"""Password policy — pure domain (신원 축 EMS 정합, 2026-08-21).

이 모듈은 **비밀번호가 받아들여질 수 있는가**만 답한다. 해싱은 인프라의 일이고
(`application/platform/password_hasher.py`), 여기서 ``bcrypt`` 를 import 하지 않는다 —
도메인 순수성 AST 가드 대상이다.

그러나 **비용 계수는 도메인이 소유한다.** ``BCRYPT_COST_ROUNDS`` 는 bcrypt 의 세부가
아니라 *"이 시스템이 비밀번호 하나에 지불하기로 한 비용"* 이라는 정책 숫자이고, 그것을
호출부 리터럴로 두면 사이트마다 다른 값이 생긴다(EMS 가 실제로 그랬다 — cost 12 가
네 파일에 흩어져 있다).

## 왜 복잡도 규칙이 없는가 (운영자 판정 2026-08-21)

NIST SP 800-63B 는 구성 규칙을 **SHALL NOT** 으로 금지한다 — 대소문자·숫자·기호를
강제하면 사용자가 ``Password1!`` 류의 예측 가능한 변형으로 몰리고 엔트로피는 오히려
떨어진다. EMS 도 같은 결론을 코드에 적어 두었다(``packages/schemas/src/auth/
password-policy.ts`` — *"복잡도 규칙 없음(NIST 정렬)"*, 자기 ADR-0021 에 근거).

계획서 D-3 은 ``no_letter``/``no_digit`` 을 포함한 표를 확정했으나, D-3 자신이 *"EMS 쪽도
한 번 보고, 다르면 그 사실을 적는다"* 라고 지시했고 실측 결과가 달랐다. 운영자가
2026-08-21 에 **EMS 완전 정합**(최소 길이 8, 복잡도 0)으로 판정했다. 이탈 사유는
``docs/adr/0021-local-identity-and-ems-schema-parity.md`` 가 소유한다.

## 왜 상한이 문자가 아니라 바이트인가

bcrypt 는 입력의 **앞 72바이트만** 해싱하고 나머지를 조용히 버린다. 그 결과 100자
패스프레이즈와 *앞 72바이트만 같은* 다른 패스프레이즈가 **같은 해시**를 낳는다.
아무것도 오류나지 않고 로그인은 계속 되며, 긴 비밀번호를 고른 사용자는 **존재하지 않는
보안을 가졌다고 믿는다**.

⚠️ 그리고 그 천장은 **문자가 아니라 바이트**로 센다. 이 시스템의 사용자는 한글을 친다 —
``'가'`` 는 UTF-8 3바이트이므로 72바이트는 **한글 24자**다(실측: ``len('가'*24
.encode('utf-8')) == 72``, 25자는 75). ``len(text)`` 로 세는 검사는 이 경계를 그냥
지나친다.

⚠️⚠️ **그리고 이 저장소에 설치된 bcrypt 는 그것을 막아 주지 않는다.** 실측
(bcrypt 3.2.2, 2026-08-21): ``hashpw(b'a' * 73, salt)`` 가 **예외 없이 통과**한다.
bcrypt 4.x 는 같은 입력에 ``ValueError`` 를 던진다 — 즉 라이브러리 버전에 따라 *조용한
절단*과 *시끄러운 거부*가 갈린다. **그 차이를 정책이 흡수한다**: 여기서 거부하므로
어느 버전에서도 동작이 같고, 라이브러리 업그레이드가 동작을 바꾸지 않는다.

## 왜 인코딩이 이 모듈의 것인가

*검증한 바이트*와 *해싱된 바이트*가 다르면 이 모듈의 상한은 아무것도 보장하지 않는다.
그래서 :func:`password_hash_input` 이 **유일한 str→bytes 경로**이고 정책도 해셔도
그것만 부른다. 두 곳에서 인코딩하면 그 둘이 갈라지는 날 조용히 갈라진다.
"""
from __future__ import annotations

from enum import Enum
from typing import Tuple


__all__ = [
    'BCRYPT_COST_ROUNDS',
    'PASSWORD_ENCODING',
    'PASSWORD_ENCODING_ERRORS',
    'PASSWORD_HASH_INPUT_MAX_BYTES',
    'PASSWORD_MIN_LENGTH',
    'PasswordDefect',
    'password_defects',
    'password_hash_input',
]


#: bcrypt 비용 계수. EMS 와 같은 값(12) — 나중에 EMS ``users.password_hash`` 를 그대로
#: 옮길 수 있으려면 알고리즘과 비용이 같아야 한다.
BCRYPT_COST_ROUNDS = 12

#: 사용자 선택 비밀번호 최소 **문자 수**. EMS ``PASSWORD_POLICY.MIN_LENGTH`` /
#: ``USER_CHOSEN_MIN_LENGTH`` 와 동일한 8(운영자 판정 2026-08-21).
PASSWORD_MIN_LENGTH = 8

#: bcrypt 절단 경계. **문자가 아니라 UTF-8 바이트**(위 docstring).
PASSWORD_HASH_INPUT_MAX_BYTES = 72

#: str→bytes 변환 SSOT. ``surrogatepass`` 인 이유는 :func:`password_hash_input` 참조.
PASSWORD_ENCODING = 'utf-8'
PASSWORD_ENCODING_ERRORS = 'surrogatepass'


class PasswordDefect(str, Enum):
    """비밀번호가 거절되는 **닫힌 어휘**.

    닫힌 어휘인 이유: HTTP 표면이 이것을 기계가 읽는 오류 코드로 바꾼다. 프론트가
    문자열 매칭해야 하는 산문 메시지는 **아무도 선언하지 않은 계약**이고, 그런 계약은
    문구를 다듬는 순간 깨진다.
    """

    TOO_SHORT = 'too_short'
    TOO_LONG_FOR_HASH = 'too_long_for_hash'
    WHITESPACE_EDGES = 'whitespace_edges'
    NULL_BYTE = 'null_byte'


def password_hash_input(password: object) -> bytes:
    """해셔에 건네질 **바로 그 바이트**. 총함수 — 어떤 입력에도 raise 하지 않는다.

    총함수인 것이 계약이다. 이 함수는 **신뢰할 수 없는 입력**(HTTP 본문)을 받고,
    호출자는 로그인 경로 한복판이라 예외를 500 으로 바꿔 버린다. 즉 여기서 raise 하면
    *거절되어야 할 비밀번호*가 *서버 오류*가 된다 — 옛 404 보다 나쁜 방향이다.

    ⚠️ ``errors='surrogatepass'`` 는 취향이 아니라 도달 가능한 결함의 정공이다.
    ``json.loads('"\\ud800"')`` 은 **성공**해서 짝 없는 서러게이트를 담은 ``str`` 을
    만들고(실측 2026-08-21), 그것을 ``'strict'`` 로 인코딩하면 ``UnicodeEncodeError`` 다.
    즉 클라이언트가 보낸 여섯 글자가 인증 표면을 500 으로 만든다. ``surrogatepass`` 는
    그 코드포인트를 3바이트로 실어 보내므로 **총함수이면서 72바이트 천장도 우회되지
    않는다**(Node ``Buffer.byteLength`` 가 U+FFFD 로 세는 3바이트와 같은 값 —
    EMS ``utf8ByteLength`` 가 같은 구멍을 같은 산술로 막는다).

    비-문자열은 ``''`` 로 접는다. 여기서 타입 오류를 던지면 그 처리가 모든 호출자로
    번지고, *"비밀번호가 아니다"* 는 이미 :func:`password_defects` 가 결함으로 답한다.
    """
    if not isinstance(password, str):
        return b''
    return password.encode(PASSWORD_ENCODING, PASSWORD_ENCODING_ERRORS)


def password_defects(password: object) -> Tuple[PasswordDefect, ...]:
    """정책을 어긴 **모든** 결함을 선언 순서로 돌려준다. 총함수.

    ⚠️ **첫 결함이 아니라 전부**다. 사용자가 규칙을 한 번에 알아야지, 거절을 반복하며
    하나씩 발견하게 두지 않는다 — 그 UX 는 사용자를 *규칙을 만족하는 가장 짧은
    비밀번호*로 몰아간다.

    비-문자열/``None`` 은 특례가 아니다. 글자도 길이도 없으므로 그에 해당하는 결함
    (``too_short``)을 그대로 모은다. 여기서 raise 하면 타입 처리가 모든 호출자로 번진다.
    """
    text = password if isinstance(password, str) else ''
    defects: list[PasswordDefect] = []

    if len(text) < PASSWORD_MIN_LENGTH:
        defects.append(PasswordDefect.TOO_SHORT)

    if len(password_hash_input(text)) > PASSWORD_HASH_INPUT_MAX_BYTES:
        defects.append(PasswordDefect.TOO_LONG_FOR_HASH)

    # 앞뒤 공백은 복사·붙여넣기 한 번은 살아남고 다음 번엔 못 살아남는다. 사용자가
    # **다시 칠 수 없는 비밀번호**를 갖게 두지 않는다. (빈 문자열은 strip 해도 같으므로
    # 이 검사에 걸리지 않는다 — 그것은 이미 too_short 다.)
    if text != text.strip():
        defects.append(PasswordDefect.WHITESPACE_EDGES)

    # ⚠️ 이것은 이론적 방어가 아니다. 실측(bcrypt 3.2.2, 2026-08-21):
    #     bcrypt.hashpw(b'abc\x00def...', salt)
    #     ValueError: password may not contain NUL bytes
    # 그리고 NUL 은 ``json.loads('"\\u0000"')`` 으로 **도달 가능**하다. 즉 이 결함이
    # 없으면 클라이언트가 보낸 한 글자가 인증 표면을 500 으로 만든다. 나아가 NUL 에서
    # 절단하는 다른 bcrypt 구현에서는 그것이 **조용한 절단** — 72바이트 천장과 같은
    # 종류의 결함 — 이 되므로, 거부가 두 세계 모두에서 옳은 답이다.
    if '\x00' in text:
        defects.append(PasswordDefect.NULL_BYTE)

    return tuple(defects)
