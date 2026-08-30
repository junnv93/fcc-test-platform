"""bcrypt password hashing — the infrastructure half of the password axis.

``domain/services/password_policy.py`` answers *"is this password acceptable"* and
owns the numbers; this module answers *"turn it into a stored hash"* and owns the
library. The split is the domain-purity rule (계약 M-6): the policy must not import
``bcrypt``.

## 왜 EMS 와 같은 알고리즘·비용인가

EMS 가 ``bcrypt`` cost 12 이고 컬럼명이 ``password_hash`` 다. 셋(알고리즘·비용·컬럼명)이
모두 같아야 나중에 EMS ``users`` 를 옮길 때 **해시를 그대로 실어 나를 수 있다** —
사용자 전원 비밀번호 재설정 없이. 운영자가 말한 이관 축이 그것이다.

## ⚠️ 이 저장소의 bcrypt 는 72바이트 천장을 지켜 주지 않는다

실측(bcrypt 3.2.2, 2026-08-21)::

    bcrypt.hashpw(b'a' * 73, salt)   # 예외 없이 통과 — 뒤가 조용히 버려진다
    bcrypt.hashpw(b'abc\\x00def', salt)
    # ValueError: password may not contain NUL bytes

bcrypt 4.x 는 73바이트에 ``ValueError`` 를 던진다. 즉 **라이브러리 버전에 따라 조용한
절단과 시끄러운 거부가 갈린다**. 정책이 두 경우 모두를 앞에서 거부하므로 이 모듈의
동작은 버전에 무관하고, 라이브러리 업그레이드가 동작을 바꾸지 않는다.

그러므로 :meth:`BcryptPasswordHasher.hash` 는 **정책을 통과하지 않은 값을 받으면
거부한다**. "호출자가 먼저 검증했겠지"는 검증이 아니다.
"""
from __future__ import annotations

from typing import Optional

from domain.services.password_policy import (
    BCRYPT_COST_ROUNDS,
    password_defects,
    password_hash_input,
)


__all__ = [
    'BcryptPasswordHasher',
    'PasswordRejected',
]


class PasswordRejected(ValueError):
    """정책을 통과하지 못한 비밀번호로 해시를 만들려 했다.

    ``PROBLEM_PARAM_FIELDS`` 를 선언해 problem+json 이 *어느 필드*가 문제인지 말할 수
    있게 한다. **결함 목록 자체는 싣지 않는다** — 어느 규칙을 어겼는지는 사용자에게
    유용하지만 그것을 ``params`` 에 넣으려면 PII 허용목록을 넓혀야 하고, 대신 응답
    본문의 ``detail`` 이 사람이 읽는 문장으로 전달한다.
    """

    PROBLEM_PARAM_FIELDS = ('field',)

    def __init__(self, message: str, *, field: str = 'password') -> None:
        super().__init__(message)
        self.field = field


class BcryptPasswordHasher:
    """``bcrypt`` 로 비밀번호를 해싱/검증한다.

    ⚠️ ``bcrypt`` 는 **지연 import** 다. 이 모듈은 platform-api 합성 루트에서 도달
    가능하고, 데스크톱 frozen exe 는 platform 라우트를 ``--nofollow-import-to`` 로
    제외한다 — 모듈 레벨 import 를 두면 정적 분석이 그 배제를 넘어 bcrypt 를 끌고
    들어올 수 있다. 형제 ``psycopg`` 가 같은 이유로 같은 형태다
    (``platform_api_composition._build_central_connection_factory``).
    """

    def __init__(self, *, cost_rounds: int = BCRYPT_COST_ROUNDS) -> None:
        self._cost_rounds = int(cost_rounds)
        self._dummy_hash: Optional[bytes] = None

    def warm(self) -> None:
        """Pay the dummy hash's one-off construction cost now, not on a login.

        ⚠️ **Otherwise the first unknown-email login after any process start pays
        TWO cost-12 rounds** — the generation plus the comparison — while a
        known-email failure pays one. Measured by adversarial review: **295.2 ms
        vs 153.9 ms, a 1.92× gap**. That is one clean enumeration bit per restart,
        surviving all three axes the login path equalises. Composition calls this.
        """
        self._dummy_credential_hash()

    def hash(self, password: object) -> str:
        """정책을 통과한 비밀번호의 저장용 해시.

        정책 위반은 :class:`PasswordRejected` — 여기서 거부하지 않으면 위반이 bcrypt
        의 ``ValueError``(NUL) 또는 **조용한 절단**(>72바이트)이 되어, 전자는 500 이고
        후자는 아무 증상 없이 잘못된 보안이 된다.
        """
        defects = password_defects(password)
        if defects:
            raise PasswordRejected(
                'password does not satisfy the policy: '
                + ', '.join(defect.value for defect in defects)
            )
        bcrypt = self._bcrypt()
        salt = bcrypt.gensalt(rounds=self._cost_rounds)
        return bcrypt.hashpw(password_hash_input(password), salt).decode('ascii')

    def verify(self, password: object, stored_hash: object) -> bool:
        """비밀번호가 저장된 해시와 맞는가. **총함수 — 절대 raise 하지 않는다.**

        총함수인 것이 보안 계약이다. 로그인 실패 경로가 예외를 던지면 그 예외는 500 이
        되고, *"비밀번호가 틀렸다"* 와 *"이 계정의 해시가 손상됐다"* 가 **다른 응답**이
        되어 사용자 열거 오라클이 열린다(계약 M-4). 손상된 해시·빈 해시·잘못된 salt 는
        전부 "맞지 않는다"로 접힌다.

        ⚠️ ``stored_hash`` 가 비어 있는 경우도 여기서 ``False`` 다 — 비밀번호가 설정된
        적 없는 OIDC 행(``password_hash IS NULL``)이 빈 비밀번호로 로그인되지 않는다.
        """
        if not isinstance(stored_hash, (str, bytes)) or not stored_hash:
            return False
        encoded_hash = (
            stored_hash.encode('ascii', 'ignore')
            if isinstance(stored_hash, str) else stored_hash
        )
        candidate = password_hash_input(password)
        try:
            return bool(self._bcrypt().checkpw(candidate, encoded_hash))
        except Exception:  # noqa: BLE001 — 위 docstring: 어떤 실패도 "맞지 않는다"
            return False

    def verify_dummy(self, password: object) -> None:
        """사용자를 못 찾았을 때 **실제로 지불하는** bcrypt 비용 (계약 M-4).

        ⚠️ 이것이 없으면 응답 시간이 신원을 누설한다: 존재하는 이메일은 bcrypt 왕복
        (cost 12 ≈ 250ms)을 지불하고 존재하지 않는 이메일은 즉시 돌아온다. 상태코드와
        본문이 아무리 같아도 그 차이는 **네트워크 너머에서 측정 가능**하고, 그것만으로
        사내 이메일 명부를 통째로 열거할 수 있다.

        더미 해시는 :data:`BCRYPT_COST_ROUNDS` 에서 **파생**되어 지연 생성된다.
        비용을 상수로 박아 두면 rounds 를 올린 날 더미만 싸져서 오라클이 되돌아온다.
        """
        self.verify(password, self._dummy_credential_hash())

    def _dummy_credential_hash(self) -> bytes:
        """프로세스 수명 동안 한 번만 만든다 (cost 12 생성은 그 자체로 비싸다)."""
        if self._dummy_hash is None:
            bcrypt = self._bcrypt()
            self._dummy_hash = bcrypt.hashpw(
                b'dummy-login-credential',
                bcrypt.gensalt(rounds=self._cost_rounds),
            )
        return self._dummy_hash

    @staticmethod
    def _bcrypt():
        import bcrypt  # lazy — 위 클래스 docstring 참조
        return bcrypt
