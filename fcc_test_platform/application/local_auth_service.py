"""Local login use-cases — the security-critical half of the identity axis.

Five operations: ``login`` · ``refresh`` · ``me`` · ``change_password`` · ``logout``.

## ⚠️ 사용자 열거 방지가 여기 산다 (계약 M-4)

*존재하지 않는 이메일* 과 *존재하는 이메일 + 틀린 비밀번호* 는 **구분 불가**해야 한다.
그것은 문구의 문제가 아니라 **세 축**의 문제다:

1. **오류 코드** — 둘 다 :data:`ErrorCode.AUTH_INVALID_CREDENTIALS`.
2. **본문** — 둘 다 같은 문장. *"등록되지 않은 이메일입니다"* 는 사내 이메일 명부를
   통째로 열거하게 해 준다.
3. **소요 시간** — 이것이 가장 자주 빠뜨리는 축이다. 존재하는 이메일은 bcrypt 왕복
   (cost 12 ≈ 250ms)을 지불하고 존재하지 않는 이메일은 즉시 돌아온다. 앞의 두 축이
   완벽해도 그 차이는 **네트워크 너머에서 측정 가능**하다. 그래서 사용자를 못 찾아도
   :meth:`BcryptPasswordHasher.verify_dummy` 로 **실제 해시 검증을 수행**한다.

## ⚠️ 계정 잠금도 구분 불가다 — EMS 와 갈라지는 지점

EMS 는 잠긴 계정에 별도 코드(``account_locked``)를 돌려준다. 여기서는 **돌려주지
않는다**. 그것이 깨끗한 열거 오라클이기 때문이다: 공격자가 아무 이메일에 다섯 번
실패시키고 여섯 번째 응답이 *"잠김"* 이면 그 계정은 **존재**하고, *"자격증명 불일치"*
면 존재하지 않는다. 비밀번호를 하나도 모르는 채로 명부를 얻는다.

대가는 UX 다 — 잠긴 사용자가 올바른 비밀번호를 넣어도 *"자격증명 불일치"* 를 본다.
15분 뒤 저절로 풀리고, 그 사이의 혼란은 명부 유출과 바꿀 수 있는 것이 아니다.
이 이탈은 ADR-0021 에 사유와 함께 등재된다.

## 왜 로그아웃이 두 겹인가

JWT 는 발급 후 취소할 수 없다. 그래서 로그아웃은:

* **``jti`` 폐기 목록** (in-memory, TTL) — 로그아웃이 **두 토큰 모두** 여기 올린다.
* **리프레시 회전** — 리프레시 토큰은 쓰는 순간 폐기되어 **단일 사용**이다.
* **``session_version`` 증가** — **비밀번호 변경**의 몫이다. 전 기기의 리프레시
  토큰을 한 번에 죽인다.

⚠️ **로그아웃은 ``session_version`` 을 올리지 않는다.** 올리면 한 탭의 로그아웃이
다른 컴퓨터의 시험을 중단시킨다. (이 문단은 한때 그 반대를 적고 있었고, 코드는
그때도 올리지 않았다 — 적대 평가가 그 모순을 찾았다.)

⚠️ **폐기 목록은 프로세스 로컬이다.** 중앙을 여러 프로세스로 띄우면 다른 프로세스가
그 ``jti`` 를 모른다 — 그 배포에서 액세스 토큰은 최대 TTL(15분)까지 살아 있고,
``session_version`` 만이 보장이다. Redis 를 들이지 않은 이유는 중앙에 Redis 가 없고
이 축을 위해 인프라를 하나 더 세우는 것이 시범 단계에 과하기 때문이다. **그 한계를
숨기지 않고 여기 적는다** — 장부 등재 대상이다.
"""
from __future__ import annotations

import json
import threading
import time as _time
import uuid
from collections import OrderedDict
from typing import Callable, Mapping, Optional

from fcc_test_contracts.common.access_policy import ApiPrincipal
from fcc_test_contracts.common.api_error_codes import ErrorCode
from fcc_test_contracts.common.logging_channel import get_logger
from fcc_test_contracts.common.local_identity import (
    CLAIM_SESSION_VERSION,
    DEFAULT_LOCAL_JWT_ACCESS_TTL_SECONDS,
    DEFAULT_LOCAL_JWT_REFRESH_TTL_SECONDS,
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
    LocalJwtConfig,
    LocalTokenError,
    issue_token,
    new_token_id,
    verify_token,
)
from fcc_test_contracts.common.operator_notice import (
    CHANNEL_LOCAL_AUTH,
    announce,
)
from fcc_test_platform.application.password_hasher import PasswordRejected
from fcc_test_contracts.common.identity import normalize_email
from fcc_test_contracts.common.login_throttle_policy import (
    SPRAYING_MAX_TRACKED_ACCOUNTS,
    SPRAYING_MAX_TRACKED_SOURCES,
    SPRAYING_WINDOW_SECONDS,
    fingerprint_digest,
    is_lock_active,
    spraying_suspected,
)
from fcc_test_platform.domain.services.password_policy import password_defects
from fcc_test_platform.domain.services.token_revocation_policy import (
    UNATTRIBUTED_CUSTODIAN,
    custodian_should_yield,
    custody_fair_share,
    steady_state_sessions_to_fill,
)


logger = get_logger('platform_api')


__all__ = [
    'BOOTSTRAP_ADMIN_ROLE_KEY',
    'BOOTSTRAP_GLOBAL_ROLE_KEY',
    'CUSTODY_EVICTION_PHRASE',
    'DEGENERATE_EVICTION_PHRASE',
    'InvalidCredentialsError',
    'LocalAccountNotFoundError',
    'RefreshRotationLimitedError',
    'LocalAuthService',
    'LoginSprayingDetector',
    'PasswordChangeRequiredError',
    'TokenRevocationList',
    'bootstrap_local_admin',
]


#: 기존 bootstrap 호환 역할. 프로젝트 역할은 ``project_memberships`` 로도
#: 부여되지만, 이 legacy 역할의 이름은 유지한다. 전역 권한은 아래 별도 role로
#: backfill하고 ``local_user_store``는 ``global_role_grants``만 해석한다.
#:
#: ⚠️ ``user_roles`` 는 **프로젝트 스코프가 아니다.** 그러므로 이 부여는 전역이고,
#: 그것이 부트스트랩에 정확히 필요한 것이다 — 프로젝트가 아직 하나도 없는 시스템에서
#: 프로젝트 스코프 권한만 받으면 그 관리자는 아무것도 할 수 없다(닭과 달걀).
BOOTSTRAP_ADMIN_ROLE_KEY = 'project_admin'

#: Bootstrap 계정에 함께 부여할 dedicated global role. This is intentionally
#: separate from the project-admin role catalog; project memberships cannot
#: manufacture the hard-delete permission.
BOOTSTRAP_GLOBAL_ROLE_KEY = 'system_admin'

#: 미인증 principal 의 subject — **리터럴이 아니라 그 클래스에서 파생**한다.
#: ``ApiPrincipal.anonymous()`` 의 subject 는 비어 있지 않으므로, 빈 문자열만 묻는
#: 가드는 프로덕션이 실제로 만드는 미인증 principal 을 통과시킨다(적대 평가 실측).
ANONYMOUS_PRINCIPAL_SUBJECT = ApiPrincipal.anonymous().subject



class InvalidCredentialsError(PermissionError):
    """자격증명이 맞지 않는다 — **또는 계정이 없다, 또는 잠겼다, 또는 비활성이다.**

    ⚠️ 네 경우가 한 예외인 것이 설계다. 갈라 놓으면 그 구분이 그대로 열거 오라클이
    된다(위 모듈 docstring). 호출부도 사유를 로그 이외 어디에도 싣지 않는다.
    """

    error_code = ErrorCode.AUTH_INVALID_CREDENTIALS


class PasswordChangeRequiredError(PermissionError):
    """비밀번호를 바꾸기 전에는 이 작업을 할 수 없다."""

    error_code = ErrorCode.AUTH_PASSWORD_CHANGE_REQUIRED


class RefreshRotationLimitedError(RuntimeError):
    """이 subject 의 리프레시 회전이 창 예산을 넘었다 (2026-08-23).

    ⚠️ **이 실패는 비싼 방향이다** — refresh 429 는 *시험 도중 로그아웃*이다. 그래서
    예산은 정상 최악(모든 세션이 동시에 회전 + 각자 한 번 재시도)에서 파생하고
    (:mod:`domain.services.identity_budget_envelope`), 그 여유가 산문이 아니라
    ``burst_headroom_ratio`` 로 **측정 가능**하다.

    ⚠️ ``retry_after_seconds`` 를 싣는다 — 429 에 ``Retry-After`` 가 없으면
    클라이언트가 언제 다시 시도할지 몰라 즉시 재시도하고, 그것이 예산을 창 내내
    고정시킨다(RFC 9110 §10.2.3).
    """

    error_code = ErrorCode.RATE_LIMITED

    def __init__(
        self, message: str, *, retry_after_seconds: int = 1, rate_limit: int = 0,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = int(retry_after_seconds)
        #: 창의 예산 크기. 경계가 ``RateLimit-Limit`` 로 실어 미들웨어의 429 와 같은
        #: 모양을 만든다 — 형식이 둘로 갈리면 클라이언트가 한쪽만 이해한다.
        self.rate_limit = int(rate_limit)


class LocalAccountNotFoundError(LookupError):
    """관리자가 지목한 로컬 계정이 없다 (2026-08-23).

    ⚠️ **형제 ``InvalidCredentialsError`` 가 네 경우를 한 예외로 접는 것과 반대이고,
    그 반대가 의도다.** 그쪽은 **미인증** 표면이라 «없는 계정» 과 «틀린 비밀번호» 를
    가르면 그 구분이 그대로 계정 열거 오라클이 된다. 이쪽은 ``platform:admin`` 만
    통과하는 표면이고, 관리자는 *"내가 적은 주소에 계정이 없다"* 와 *"해제했다"* 를
    반드시 구분할 수 있어야 한다 — 구분하지 못하면 오타 한 글자가 «해제됐다» 로
    보이고 잠긴 시험원은 계속 못 들어온다.

    즉 열거 위험은 사라지는 것이 아니라 **이미 최상위 권한을 가진 주체에게로 좁혀지는
    것**이고, 그 주체는 사용자 목록을 볼 자격이 있다.
    """

    error_code = ErrorCode.NOT_FOUND


#: 폐기 목록이 기억하는 ``jti`` 의 상한.
#:
#: ⚠️ **TTL 만으로는 부족하다 — 리프레시 회전이 그것을 증명했다.** 회전은 매번 7일짜리
#: 항목을 넣으므로 만료 기반 정리가 **아무것도 회수하지 못한다**. 적대 평가 실측:
#: `/platform/auth/refresh` 는 ``public`` 이고 경로에 bcrypt 가 없어 **초당 380 회전**이
#: 가능하며, 캡처된 리프레시 토큰 하나면 된다. 즉 상한 없는 이 구조는 메모리 소진
#: 경로이자, 아래 잘라내기가 O(n) 이라 **요청마다 같은 락을 잡는 이차 비용**이었다.
#:
#: 형제 ``SPRAYING_MAX_TRACKED_ACCOUNTS`` 를 *"상한 없는 관측은 메모리 소진 경로다"* 라는
#: 이유로 넣어 놓고 그 옆에 더 나쁜 무상한 구조를 남겼던 것이다.
REVOCATION_LIST_MAX_ENTRIES = 20_000

#: 만료 정리를 실제로 수행하는 최소 간격(초). 매 호출 정리는 O(n) 을 요청마다 지불한다.
REVOCATION_PRUNE_INTERVAL_SECONDS = 60.0

#: 축출 경고 두 분기를 **구분하는 문구**. 운영자 런북이 이 값에 결박된다.
#:
#: ⚠️ **런북이 코드보다 오래 살았다** — 이 웨이브가 문구를 다시 쓰면서 런북을 고치지
#: 않아, 적대 평가 2라운드의 **두 평가자가 각각** 그것을 찾았다: 런북이 grep 하라고
#: 적은 두 문자열이 코드에 **0건**이었고, 게다가 의미가 **반대**였다(런북 *"공격이
#: 아니라 용량"* ↔ 코드 *"부하가 아니라 구성 오류"*). 그 런북을 따른 운영자는 정상
#: 포화에 무고한 시험원의 비밀번호를 바꾸게 한다.
#:
#: 상수로 올린 이유는 하나다 — **봉인이 런북과 코드를 같은 값에 묶을 수 있게** 하려고.
#: 산문으로만 두면 다음 개명에서 같은 드리프트가 반복된다.
CUSTODY_EVICTION_PHRASE = 'No custodian within its share lost an entry'
DEGENERATE_EVICTION_PHRASE = 'This is a MISCONFIGURATION'


def _custodian_key(custodian: object) -> str:
    """보관자 키를 총함수로 정규화한다. 읽을 수 없으면 **전용 미상 버킷**이다.

    ⚠️ 미상을 다른 보관자에 섞지 않는 이유는 :data:`UNATTRIBUTED_CUSTODIAN` 이 적는다 —
    섞으면 그 보관자의 몫을 희석하고, 아예 빼면 상한에서 면제된다.

    ⚠️ ``str()`` 이 터지는 객체도 받아야 한다. 이 함수는 로그아웃 경로에서 불리고,
    로그아웃은 실패할 수 없는 연산이다(``logout`` docstring).
    """
    try:
        text = str(custodian or '').strip()
    except Exception:  # noqa: BLE001 — 적대적 __str__ 이 로그아웃을 500 으로 만들 수 없다
        return UNATTRIBUTED_CUSTODIAN
    return text or UNATTRIBUTED_CUSTODIAN


class TokenRevocationList:
    """``jti`` → 만료시각. 프로세스 로컬, **상한과 amortized 정리**를 갖는다.

    ⚠️ **경계 조건이 보안이다** — 그리고 2026-08-22 까지 그 경계가 로그아웃을 되돌렸다.
    상한 초과 시 희생자를 **전역에서 가장 먼저 만료될 항목**으로 고르고 있었는데, 회전은
    매번 새 7일짜리를 넣으므로 **가장 오래된 로그아웃이 항상 첫 희생자**였다. 적대 평가가
    실 라우트로 재현했다: 공격자가 자기 토큰을 20,001회 회전시키자 피해자의 로그아웃이
    풀리고 캡처된 리프레시가 다시 200 을 받았다.

    ⚠️ **정공은 축출 순서가 아니라 삽입이다.** 남은 수명이 짧은 항목을 먼저 버리는 것은
    그 자체로는 옳다. 결함은 **삽입을 공격자가 통제**한다는 것이었다
    (``/platform/auth/refresh`` 는 ``public`` 이고 bcrypt 가 없다 — 실측 380 회전/초).

    그래서 항목은 **보관자(custodian)** 를 갖는다. 모든 삽입은 ``verify_token`` 을 이미
    통과한 claims 에서 오므로 그 ``sub`` 는 위조 불가이고, 자기 몫을 넘은 보관자는
    **자기 항목**을 내놓는다. 판정은 순수 도메인
    :mod:`domain.services.token_revocation_policy` 가 소유한다 — 새 상수는 없고 몫은
    상한과 보관자 수에서 파생된다.

    ⚠️ **보관자 키는 caller 가 만든 불투명 문자열이다.** 이 클래스는 그것이 무엇인지
    알지 않는다 — 시크릿도 받지 않고 다이제스트도 만들지 않는다. 익명화 SSOT 는
    :func:`fingerprint_digest` 한 곳에 남는다(형제 :class:`LoginSprayingDetector` 와
    같은 함수). 정규화기가 둘이면 갈라지고, 갈라진 곳이 우회로다.

    ⚠️ 그리고 **축출은 조용한 보안 저하다**: 축출된 토큰은 만료 전까지 다시 유효해진다.
    그래서 축출이 일어나면 시끄럽게 알린다 — 그리고 **어느 분기인지 이름을 댄다**.
    자기 몫 축출은 설계된 자기 제한이고, 전역 용량 압박은 진짜 용량 신호다. 한 문구면
    운영자가 그 둘을 구분할 수 없다.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = _time.time,
        max_entries: int = REVOCATION_LIST_MAX_ENTRIES,
        prune_interval_seconds: float = REVOCATION_PRUNE_INTERVAL_SECONDS,
        refresh_ttl_seconds: int = DEFAULT_LOCAL_JWT_REFRESH_TTL_SECONDS,
        access_ttl_seconds: int = DEFAULT_LOCAL_JWT_ACCESS_TTL_SECONDS,
    ) -> None:
        #: ⚠️ 경고 문구가 *"정상 포화는 이 정도 규모다"* 를 말하려면 두 TTL 이 필요하다.
        #: 기본값은 이 배포의 값이고, **주입 가능**한 이유는 그 수가 설정에 따라 달라지기
        #: 때문이다 — 상수로 박으면 TTL 을 바꾼 배포에서 그 줄이 조용히 거짓이 된다.
        self._jwt_refresh_ttl = refresh_ttl_seconds
        self._jwt_access_ttl = access_ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._revoked: dict[str, float] = {}
        #: ``jti`` → 보관자. ``_revoked`` 와 항상 같은 키 집합을 갖는다.
        self._custody: dict[str, str] = {}
        #: 보관자 → 그 보관자의 ``jti`` 삽입 순서. ⚠️ ``OrderedDict`` 인 이유는
        #: 형제 :class:`LoginSprayingDetector` 와 같다 — 희생자 선택을 **락 안에서
        #: O(1)** 로 끝내기 위해서다. 락 안의 O(n) 훑기는 이 저장소가 이미 두 번
        #: 지운 형태이고(D-9), 키가 공격자에게 열려 있는 구조에서 그것은 CPU·락 경합
        #: 증폭기가 된다.
        #: 보관자 → 회전 churn 항목(삽입 순). **먼저 버리는 쪽**이다.
        self._churn: "dict[str, OrderedDict[str, None]]" = {}
        #: 보관자 → 의도적 폐기 항목(로그아웃·비밀번호 변경, 삽입 순).
        #:
        #: ⚠️ **두 종류를 가르는 것이 F3 의 정공이다.** 한 큐만 두면 어느 끝을 버려도
        #: 지는 경우가 있다: *가장 오래된* 것을 버리면 그 보관자의 **로그아웃 항목**이
        #: 첫 희생자이고(로그아웃은 한 번 넣고 회전이 그 뒤에 쌓인다 — 적대 평가가
        #: 실측: 다른 기기에서 계속 일하는 것만으로 자기 로그아웃이 풀린다), *가장
        #: 새로운* 것을 버리면 **방금 넣은 로그아웃 항목**이 즉시 사라져 가장 활동적인
        #: 사용자의 로그아웃이 no-op 이 된다. 끝이 아니라 **가치**로 골라야 한다.
        #:
        #: 회전 churn 은 그 보관자 자신이 만든 것이고 버려도 그 사람 자신의 옛 토큰만
        #: 되살아난다. 의도적 폐기는 *"이 세션을 끝내겠다"* 는 사람의 결정이다.
        self._kept: "dict[str, OrderedDict[str, None]]" = {}
        #: 보유량 → 그만큼 들고 있는 보관자들. **가장 무거운 보관자를 O(1) 로 찾기
        #: 위한 색인**이고, 그것이 이 클래스에서 가장 중요한 자료구조다.
        #:
        #: ⚠️ **첫 판은 이 색인이 없었고, 그래서 희생자를 "방금 삽입한 보관자"로
        #: 골랐다 — 그것이 이 웨이브의 결함이었다.** 자기 정합성 산술은 *"**모든**
        #: 보관자가 몫 이하이면 총합이 상한을 넘을 수 없다"* 를 말하는데 분기 조건은
        #: **한** 보관자만 물었다. 그래서 flood 가 목록을 상한에 붙여 놓은 뒤
        #: **무관한 제3자의 평범한 로그아웃 한 번**이 전역 폴백을 타고 피해자 항목을
        #: 버렸다(적대 평가가 실 라우트로 재현: replay 200 + 폐기된 관리자 액세스
        #: 토큰 부활). 자기 몫을 넘은 쪽에서 골라야 하고, 그 쪽은 **삽입한 쪽이
        #: 아닐 수 있다**.
        #:
        #: 갱신은 LFU 캐시의 표준 구조와 같다 — 보유량은 한 번에 1씩만 움직이므로
        #: ``_heaviest`` 유지가 상수 시간이다(스캔 0).
        self._by_count: "dict[int, OrderedDict[str, None]]" = {}
        self._heaviest = 0
        #: 같은 구조의 **두 번째 색인**, 이번엔 회전 churn 보유량만 센다.
        #:
        #: ⚠️ **왜 둘이 필요한가** — 첫 판은 희생자를 *먼저 무게로* 고르고 그 안에서만
        #: churn 을 먼저 버렸다. 그러면 가치 순서가 무게 순서에 **종속**되어, `_kept`
        #: 만 가진 무거운 보관자(= 여러 기기에서 로그아웃한 사용자)가 **가벼운
        #: 보관자의 버려도 되는 churn 보다 먼저** 드레인된다. 적대 평가 2라운드 실측:
        #: 피해자의 로그아웃 항목 40건 중 **39건이 사라지는 동안** 다른 사람의 churn 은
        #: 살아남았다. 그 39건은 각각 다시 세션을 찍어내는 리프레시 토큰이다.
        #:
        #: 그래서 순서를 뒤집는다 — **어디에든 churn 이 남아 있는 한 의도적 폐기는
        #: 건드리지 않는다**(전역), 그리고 *그 안에서* 공정몫(가장 무거운 churn 보유자)이
        #: 낸다. 두 질문이 각자의 색인을 갖는다.
        self._by_churn: "dict[int, OrderedDict[str, None]]" = {}
        self._heaviest_churn = 0
        #: 항목을 하나라도 든 보관자 수. ⚠️ **증분으로 유지한다** — 첫 판은 축출 루프의
        #: 매 회차마다 ``len(set(self._churn) | set(self._kept))`` 를 다시 계산했고,
        #: 그것은 모든 인증 요청이 기다리는 락 안의 **보관자 수 비례 작업**이다. 이
        #: 클래스가 없앴다고 적은 바로 그 형태다(적대 평가 2라운드 F5).
        self._population = 0
        self._max_entries = int(max_entries)
        self._prune_interval = float(prune_interval_seconds)
        self._last_prune = 0.0
        #: 축출 경고는 **집계해서** 낸다. ⚠️ 사건당 한 줄은 그 자체로 취약점이다 —
        #: 이 목록을 채우는 것은 초당 수백 회전이고(실측 380/s), 사건당 경고는 그것을
        #: 그대로 로그 싱크 증폭기로 바꾼다. 더 나쁜 것은 그 홍수가 **형제 분기**(전역
        #: 용량 압박)의 한 줄을 덮는다는 점이다 — 두 분기를 나눠 놓고 하나가 다른 하나를
        #: 못 보이게 하면 나눈 의미가 없다. 창은 정리 간격과 같은 값을 쓴다: 두 축 모두
        #: *"이 목록이 얼마나 자주 자기 상태를 다시 봐야 하는가"* 라는 한 질문의 답이다.
        self._eviction_report: dict[str, float] = {}
        self._pending_evictions: dict[str, int] = {'custody': 0, 'degenerate': 0}
        #: 프로세스 시작 이후 누계. ⚠️ 창 집계는 **마지막 버스트의 꼬리를 잃는다**
        #: — 창이 닫힌 뒤 다음 축출이 없으면 그 건수는 보고되지 않는다(적대 평가
        #: 실측: 50건 축출에 '1건' 한 줄). 누계를 함께 실어 다음 줄이 진실을
        #: 복원하게 하고, 그 한계를 런북이 이름으로 적는다.
        self._total_evictions: dict[str, int] = {'custody': 0, 'degenerate': 0}

    def revoke(
        self, token_id: object, *, expires_at: float, custodian: object,
        churn: bool = False,
    ) -> None:
        """``token_id`` 를 폐기한다. ``custodian`` 은 **필수 키워드**다.

        ⚠️ 기본값을 주지 않는 것이 설계다. 기본값이 있으면 새 호출부가 보관자를 빠뜨려도
        조용히 미상 버킷으로 떨어지고, 그 버킷은 다른 보관자를 보호하지 못한다 — 즉
        결함이 **컴파일도 테스트도 통과한 채** 들어온다. 부르는 쪽이 답하게 만든다.

        ``churn=True`` 는 *"이것은 회전이 만든 부산물이다"* 를 뜻한다. 상한에서 먼저
        버려지는 쪽이고, 기본값이 ``False`` 인 것이 안전한 방향이다 — 표시하지 않은
        항목은 **의도적 폐기로 취급**되어 보호받는다. 반대로 기본을 ``True`` 로 두면
        새 호출부가 표시를 빠뜨렸을 때 그 항목이 조용히 가장 먼저 버려진다.
        """
        key = str(token_id or '').strip()
        if not key:
            return
        owner = _custodian_key(custodian)
        with self._lock:
            self._insert_locked(
                key, expires_at=expires_at, owner=owner, churn=churn,
            )

    def _insert_locked(
        self, key: str, *, expires_at: float, owner: str, churn: bool,
    ) -> None:
        """삽입의 **유일한 정의**. :meth:`revoke` 와 :meth:`claim` 이 함께 쓴다.

        ⚠️ 사본이 둘이면 갈라지고, 갈라진 쪽이 축출을 부르지 않는 쪽이 된다.
        """
        self._maybe_prune_locked()
        self._forget_locked(key)
        self._revoked[key] = float(expires_at)
        self._custody[key] = owner
        before = self._count_locked(owner)
        queue = self._churn if churn else self._kept
        queue.setdefault(owner, OrderedDict())[key] = None
        self._reindex_locked(owner, before, before + 1, churn_delta=1 if churn else 0)
        # ⚠️ 순수한 최적화다 — ``_evict_locked`` 의 ``while`` 이 같은 조건을 다시
        # 본다. 하중을 받는 것처럼 보이지 않도록 적어 둔다(변이 배터리가 이 줄을
        # 뒤집어도 동작이 같다는 것을 실행으로 확인했다).
        if len(self._revoked) > self._max_entries:
            self._evict_locked()

    def _is_revoked_locked(self, key: str) -> bool:
        expires_at = self._revoked.get(key)
        if expires_at is None:
            return False
        if self._clock() >= expires_at:
            self._forget_locked(key)
            return False
        return True

    def held_by(self, custodian: object) -> int:
        """이 보관자가 지금 들고 있는 항목 수 — 분할이 실제로 무엇을 하는지 묻는 창.

        ⚠️ ``tracked_custodians`` 만으로는 *"공격자가 자기 몫에 고정됐는가"* 를 물을 수
        없고, 그것을 묻지 못한 봉인이 첫 판에서 공허하게 통과했다.
        """
        owner = _custodian_key(custodian)
        with self._lock:
            return self._count_locked(owner)

    def _count_locked(self, owner: str) -> int:
        return (
            len(self._churn.get(owner, ()))
            + len(self._kept.get(owner, ()))
        )

    def claim(
        self, token_id: object, *, expires_at: float, custodian: object,
        churn: bool = False,
    ) -> bool:
        """*"이 토큰이 아직 안 쓰였다면, 지금 내가 쓴다."* — **한 원자적 동작.**

        이미 폐기돼 있으면 ``False`` 를 답하고 아무것도 바꾸지 않는다. 아니면 삽입하고
        ``True`` 를 답한다. 둘 다 **같은 락 안**에서 일어난다.

        ⚠️ **``is_revoked`` 로 묻고 나중에 ``revoke`` 하는 것은 회전에 대해 안전하지
        않다.** 그 둘 사이에 리프레시 경로는 사용자 행을 읽는 **DB 왕복**을 하고 그것이
        GIL 을 놓는다. 적대 평가 2라운드가 실 라우트로 실측했다: 같은 캡처 토큰으로
        **동시 요청 12개 중 4개가 200** 을 받아 **로그아웃으로 끝낼 수 없는 세션 4개**가
        됐다(피해자가 로그아웃한 **뒤에도** 넷 다 계속 새 토큰을 찍어냈다). 즉 회전이
        *"단일 사용"* 이라는 이 파일의 주장이 거짓이었고, 그것은 보관 분할이 지키려는
        문장보다 **앞에 있는** 전제다 — 공격자는 축출이 필요조차 없었다.

        ⚠️ 이 원자성은 **프로세스 안**에서만 참이다. 다중 워커 배포에서는 워커마다 락이
        따로이므로 워커 수만큼 갈래가 난다 — 형제 항목들과 같은 축(공유 저장소)이고
        장부가 소유한다. 그 한계를 여기 적는 이유는, 적지 않으면 다음 세션이 이 메서드를
        **분산 보장**으로 읽기 때문이다.
        """
        key = str(token_id or '').strip()
        if not key:
            # 식별자가 없으면 재사용을 판정할 수 없다 — 통과시키면 그 토큰은 무제한
            # 재사용 가능해진다. 거부가 유일하게 안전한 답이다.
            return False
        owner = _custodian_key(custodian)
        with self._lock:
            if self._is_revoked_locked(key):
                return False
            self._insert_locked(
                key, expires_at=expires_at, owner=owner, churn=churn,
            )
            return True

    def release(self, token_id: object) -> None:
        """:meth:`claim` 이 넣은 항목을 되돌린다 — **거부된 시도에만**.

        ⚠️ 왜 필요한가: claim 은 사용자 행을 읽기 **전**에 일어나야 원자적인데, 그 뒤의
        검사(계정 비활성 · ``session_version`` 불일치 · 잠금)가 거부하면 그 토큰은
        *쓰이지 않은* 것이다. 되돌리지 않으면 **잠긴 사용자의 세션이 15분이 아니라
        영구히 끝난다** — 잠금은 일시적인데 소실은 영구적이라 그 둘을 맞바꿀 수 없다.

        되돌리는 동안에도 락은 그 키를 붙잡고 있었으므로 동시 요청이 끼어들 수 없다.
        """
        key = str(token_id or '').strip()
        if not key:
            return
        with self._lock:
            self._forget_locked(key)

    def is_revoked(self, token_id: object) -> bool:
        key = str(token_id or '').strip()
        if not key:
            return False
        with self._lock:
            return self._is_revoked_locked(key)

    def __len__(self) -> int:
        with self._lock:
            return len(self._revoked)

    @property
    def tracked_custodians(self) -> int:
        """현재 항목을 하나라도 들고 있는 보관자 수 (공정몫 파생·봉투 단언용)."""
        with self._lock:
            return self._population

    def _forget_locked(self, key: str) -> None:
        """한 항목을 세 구조에서 **함께** 지운다.

        ⚠️ 세 구조가 갈라지면 공정몫이 실재하지 않는 항목을 세고, 그러면 자기 몫을 넘지
        않은 보관자가 자기 항목을 내놓거나 그 반대가 된다. 지우는 자리를 하나로 두는 것이
        그 드리프트를 구조적으로 막는다.
        """
        self._revoked.pop(key, None)
        owner = self._custody.pop(key, None)
        if owner is None:
            return
        before = self._count_locked(owner)
        churn_before = len(self._churn.get(owner, ()))
        for queue in (self._churn, self._kept):
            held = queue.get(owner)
            if held is None:
                continue
            held.pop(key, None)
            if not held:
                queue.pop(owner, None)
        after = self._count_locked(owner)
        churn_after = len(self._churn.get(owner, ()))
        if after != before:
            self._reindex_locked(
                owner, before, after, churn_delta=churn_after - churn_before,
            )

    def _maybe_prune_locked(self) -> None:
        """만료 정리는 **간격을 두고** 한다.

        ⚠️ 매 ``revoke`` 마다 전체를 훑는 것이 이차 비용의 근원이었다(실측: 80k 항목에서
        호출당 1.4ms 를 **락을 쥔 채** 지불 — 그 락은 모든 요청의 ``is_revoked`` 가
        기다리는 락이다). 정리를 늦게 하는 것의 유일한 대가는 이미 만료된 항목을 잠시 더
        들고 있는 것이고, 그것은 **아무 보안 의미가 없다**(만료된 토큰은 어차피 거부된다).
        """
        now = self._clock()
        if now - self._last_prune < self._prune_interval:
            return
        self._last_prune = now
        for key in [k for k, exp in self._revoked.items() if now >= exp]:
            self._forget_locked(key)

    def _reindex_locked(
        self, owner: str, before: int, after: int, *, churn_delta: int = 0,
    ) -> None:
        """두 보유량 색인과 보관자 수를 갱신한다. 상수 시간.

        ⚠️ 최댓값은 **감소할 때 한 칸씩만** 내려간다. 보유량이 한 번에 1씩만 움직이므로
        그것으로 충분하고, 그래서 이 갱신에 스캔이 없다 — LFU 캐시가 최소 빈도를 유지하는
        방식 그대로다. 훑기를 넣는 순간 이 클래스는 락 안의 O(n) 을 되찾고, 그것이 D-9 가
        두 번 지운 형태다.
        """
        if before == 0 and after > 0:
            self._population += 1
        elif before > 0 and after == 0:
            self._population -= 1
        if churn_delta:
            churn_before = len(self._churn.get(owner, ())) - churn_delta
            self._heaviest_churn = self._move_locked(
                self._by_churn, owner, churn_before,
                churn_before + churn_delta, self._heaviest_churn,
            )
        self._heaviest = self._move_locked(
            self._by_count, owner, before, after, self._heaviest,
        )

    @staticmethod
    def _move_locked(index, owner: str, before: int, after: int, top: int) -> int:
        """한 색인에서 ``owner`` 를 ``before`` 버킷에서 ``after`` 버킷으로 옮긴다.

        ⚠️ 두 색인이 이 함수를 **공유**한다. 사본을 두면 한쪽의 최댓값 유지만 고쳐지고
        다른 쪽이 유령을 가리키게 된다 — 그리고 그 결과는 조용한 상한 붕괴다.
        """
        if before == after:
            return top
        bucket = index.get(before)
        if bucket is not None:
            bucket.pop(owner, None)
            if not bucket:
                index.pop(before, None)
        if after > 0:
            index.setdefault(after, OrderedDict())[owner] = None
        if after > top:
            top = after
        while top > 0 and top not in index:
            top -= 1
        return top

    def _choose_victim_locked(self):
        """누가 무엇을 내놓는가 — ``(보관자, jti, 보유량, churn 이었나)``.

        **가치가 무게보다 앞선다.** 회전 churn 이 **어디에든** 남아 있으면 그 중에서
        고르고, 의도적 폐기(로그아웃·비밀번호 변경)는 그때 전혀 건드리지 않는다.

        ⚠️ 첫 판은 순서가 반대였다 — 먼저 무게로 보관자를 고르고 *그 안에서만* churn 을
        먼저 버렸다. 그러면 ``_kept`` 만 가진 무거운 보관자가 가벼운 보관자의 churn 보다
        먼저 드레인되고, 그 보관자는 정확히 *여러 기기에서 로그아웃한 사용자*다. 적대
        평가 2라운드 실측: 피해자 로그아웃 40건 중 **39건 소실**, 그 사이 남의 churn 은
        생존. 그 39건은 각각 다시 세션을 찍어내는 리프레시 토큰이다.

        각 축 안에서는 **가장 무거운 보관자**가 낸다(공정몫) — 그 근거는
        :meth:`_evict_locked` 가 갖는다. 그리고 그 보관자의 **가장 오래된** 항목을
        내놓는다: 남은 보호 기간이 가장 짧아 잃는 것이 가장 적다.

        두 조회 모두 상수 시간이다 — 색인이 최댓값을 유지한다.
        """
        for index, top, queues in (
            (self._by_churn, self._heaviest_churn, (self._churn,)),
            (self._by_count, self._heaviest, (self._churn, self._kept)),
        ):
            bucket = index.get(top)
            if not bucket:
                continue
            owner = next(iter(bucket))
            for queue in queues:
                held = queue.get(owner)
                if held:
                    return owner, next(iter(held)), self._count_locked(owner), (
                        queue is self._churn
                    )
        return None, None, 0, False

    def _evict_locked(self) -> None:
        """상한 초과 시 희생자를 고른다 — **자기 몫을 넘은 보관자가 낸다.**

        ⚠️ **삽입한 쪽이 아니라 가장 무거운 쪽이다.** 첫 판은 방금 삽입한 보관자만
        물었고, 적대 평가가 그 틈으로 공격을 그대로 재현했다: flood 가 목록을 상한에
        붙여 놓으면 **무관한 제3자의 평범한 로그아웃 한 번**이 폴백을 타고 피해자
        항목을 버렸다. 자기 정합성 산술이 말하는 것은 *"**모든** 보관자가 몫 이하이면
        총합이 상한을 넘을 수 없다"* 이고, 그 대우가 정확한 규칙이다 —
        **총합이 상한을 넘었다면 몫을 넘은 보관자가 반드시 존재한다.** 가장 무거운
        보관자가 그 중 하나이므로, 그를 고르는 것이 곧 정확한 선택이다.

        그러므로 **몫 이내의 보관자는 희생되지 않는다** — 단, 아래 퇴화 구간은 예외이고
        그 예외를 문장에 **함께** 적는다. 첫 판은 이 문장을 조건 없이 적었고 적대 평가가
        반례를 실행으로 냈다(신원 5 > 상한 4 에서 몫 1 을 든 보관자가 축출됐다). 조건
        없는 보장은 그것이 참이 아닌 구간에서 다음 사람을 속인다.

        ⚠️ 예외는 하나뿐이고 **퇴화 구간**이라 부른다: 보관자 수가 상한보다 많으면
        (``max_entries // n == 0``) 모두가 몫 1 을 들고도 총합이 상한을 넘는다. 그때는
        공정한 선택 자체가 존재하지 않는다 — 상한이 신원 수보다 작다는 뜻이고, 그것은
        용량 사건이 아니라 **구성 오류**다. 프로덕션 상한 20,000 에서는 서로 다른 인증
        신원 20,000개를 요구하므로 도달 불가에 가깝다.

        ⚠️ **``sorted`` 도 전수 훑기도 없다.** 첫 판은 만료 스캔과 ``sorted`` 를 둘 다
        락 안에서 매 호출 돌렸고, 적대 평가가 상한 80k 에서 호출당 **1.23ms**, 무관한
        사용자의 ``is_revoked`` p99 **133ms** 를 실측했다 — 위 ``_maybe_prune_locked``
        docstring 이 *제거했다고 적은 바로 그 비용*을 옆줄에서 되살린 것이다. 만료 정리는
        간격을 두고 하는 그 함수의 몫이고, 여기서는 하지 않는다.
        """
        evicted = {'custody': 0, 'degenerate': 0}
        crowded_population = 0
        # ⚠️ **``if``, not ``while`` — 그리고 그것은 취향이 아니라 정확성이다.**
        # ``_insert_locked`` 는 정확히 하나를 넣고 나서 이것을 부르므로 목록은 상한을
        # 최대 1 넘고 한 번의 축출이 그것을 되돌린다. 루프는 speculative generality 이고
        # 실비용이 있다: 세 보관 구조가 어긋나면 길이가 영영 줄지 않아 **모든 인증
        # 요청이 기다리는 락을 쥔 채 무한히 돈다**. 변이 배터리가 그 형태로 한 번
        # 매달렸고, 그 사실을 적은 뒤 나중 판이 ``while`` 을 되살렸다 — 적대 평가 2R 이
        # 그 되돌림을 찾았고 그때 내 자가점검은 *"약화한 단언 없음"* 이라고 적고 있었다.
        if len(self._revoked) > self._max_entries:
            share = custody_fair_share(self._max_entries, self._population)
            victim, forfeit, held_count, _was_churn = self._choose_victim_locked()
            if victim is not None and forfeit is not None:
                if custodian_should_yield(held=held_count, fair_share=share):
                    axis = 'custody'
                else:
                    axis = 'degenerate'
                    # ⚠️ 인구는 **판정 시점**의 값을 기억한다. 축출이 보관자를 비우면
                    # ``self._population`` 이 줄어들어, 나중에 읽으면 운영자에게
                    # *"N 신원, 상한 N"* 처럼 자기 분기의 전제를 만족하지 않는 숫자가
                    # 보고된다(적대 평가 2라운드 F3 실측).
                    crowded_population = self._population
                self._forget_locked(forfeit)
                evicted[axis] += 1
            # ⚠️ 색인과 큐가 갈라지면 희생자가 ``None`` 이고 이 호출은 아무것도 하지
            # 않는다 — 상한을 그 순간 지키지 못하지만 **매달리지는 않는다**. 그 정합은
            # ``TestTheCountIndexAgreesWithTheQueues`` 가 따로 지킨다.

        now = self._clock()
        if evicted['custody']:
            # ⚠️ 설계된 자기 제한이다 — 아래 퇴화 분기와 **다른 사건**이라 다른 문구를
            # 쓴다. 같은 문구면 운영자가 "누가 대가를 치렀나"를 구분할 수 없다.
            reported = self._due_eviction_count_locked(
                'custody', evicted['custody'], now,
            )
            if reported:
                # ⚠️ **이 줄은 공격을 주장하지 않는다** (적대 평가 2R F3). 첫 판은
                # *"check for a replayed refresh token"* 으로 끝났고, 평가자가 공격자
                # 없는 시험원 30명·연속 세션만으로 그 줄이 뜨는 것을 실측했다 — 그리고
                # 런북은 그 줄을 보면 비밀번호 변경을 권고하라고 적고 있었다. 즉 정상
                # 포화가 무고한 시험원에 대한 조치로 이어진다.
                #
                # 이 분기는 *"누가 냈는가"* 만 말한다. 공격과 포화를 가르는 것은 이
                # 문구가 아니라 **누적 속도**이고, 그래서 두 수를 함께 싣는다.
                logger.warning(
                    'token revocation list is full; %s entries were discarded '
                    'since the last report (%s since start) from custodians over '
                    'their fair share of the %s-entry ceiling. '
                    + CUSTODY_EVICTION_PHRASE + '. ⚠️ This alone does NOT mean an '
                    'attack: %s continuously-active sessions reach this ceiling on '
                    'their own (each settles at refresh_ttl/access_ttl entries). '
                    'A sustained high rate from few identities is what distinguishes '
                    'a replayed refresh token from ordinary saturation.',
                    reported, self._total_evictions['custody'], self._max_entries,
                    steady_state_sessions_to_fill(
                        self._max_entries,
                        self._jwt_refresh_ttl, self._jwt_access_ttl,
                    ),
                )
        if evicted['degenerate']:
            reported = self._due_eviction_count_locked(
                'degenerate', evicted['degenerate'], now,
            )
            if reported:
                logger.warning(
                    'token revocation list discarded %s entries (%s since start) '
                    'with NO fair choice available: %s distinct identities are '
                    'tracked but the ceiling is only %s, so every custodian is '
                    'already at its minimum share. '
                    + DEGENERATE_EVICTION_PHRASE + ', not load — logout cannot be '
                    'guaranteed for anyone until the ceiling exceeds the number '
                    'of identities.',
                    reported, self._total_evictions['degenerate'],
                    crowded_population, self._max_entries,
                )

    def _due_eviction_count_locked(
        self, axis: str, evicted: int, now: float,
    ) -> int:
        """이 축의 축출을 지금 보고해야 하면 **창 안 누적 건수**, 아니면 ``0``.

        ⚠️ **사건당 한 줄은 그 자체로 취약점이다.** 이 목록을 채우는 것은 초당 수백
        회전이고(실측 380/s), 사건당 경고는 그것을 로그 싱크 증폭기로 바꾼다. 더 나쁜
        것은 그 홍수가 **형제 축**의 한 줄을 덮는다는 점이다 — 두 축을 나눠 놓고 하나가
        다른 하나를 못 보이게 하면 나눈 의미가 없다. 그래서 창은 **축마다** 따로다.

        ⚠️ **첫 사건은 즉시 낸다.** 창을 먼저 채우고 알리면 짧은 사고가 통째로 조용하고,
        그러면 이 경고는 *"일어났다"* 가 아니라 *"오래 일어났다"* 만 답한다.

        ⚠️ 돌려주는 수는 **창 안 누적**이지 이번 호출의 수가 아니다. 이번 것만 실으면
        상한에 붙어 있는 목록이 영원히 *"1건 축출"* 만 보고하고, 운영자는 초당 380건과
        하루 1건을 구분할 수 없다.

        ⚠️ 판정은 **메시지를 만들지 않는다.** 두 축의 문구는 자리표시자 순서가 다르고,
        한 함수가 그것을 문자열에서 되유도하면 그 되유도가 다음 문구에서 조용히 틀린다.
        """
        self._total_evictions[axis] = (
            self._total_evictions.get(axis, 0) + int(evicted)
        )
        pending = self._pending_evictions.get(axis, 0) + int(evicted)
        last = self._eviction_report.get(axis)
        if last is not None and now - last < self._prune_interval:
            self._pending_evictions[axis] = pending
            return 0
        self._eviction_report[axis] = now
        self._pending_evictions[axis] = 0
        return pending


class LoginSprayingDetector:
    """한 출처가 **여러 계정**에 실패하는 패턴을 관측한다. 차단하지 않는다.

    ⚠️ 잠금과는 **다른 질문**에 답한다. 스프레이 공격은 계정마다 한두 번만 틀리므로
    잠금 임계값에 영원히 닿지 않는다 — 잠금만으로는 구조적으로 보이지 않는 공격이다.

    ⚠️ **아무것도 차단하지 않고 어떤 예외도 밖으로 내보내지 않는다.** 관측 계층이
    로그인을 실패시키면 그것은 관측이 아니라 장애다. EMS 도 같은 형태다.

    ⚠️ **원본 식별자를 보관하지 않는다.** 계정은 HMAC 다이제스트로만 세므로, 이 구조가
    통째로 유출돼도 누가 공격당했는지 나오지 않는다.
    """

    def __init__(
        self,
        *,
        secret: str,
        clock: Callable[[], float] = _time.time,
        on_suspected: Optional[Callable[[str, int], None]] = None,
    ) -> None:
        self._secret = secret or ''
        self._clock = clock
        self._on_suspected = on_suspected
        self._lock = threading.Lock()
        # ⚠️ ``OrderedDict`` — 버킷 **개수** 상한을 O(1) 로 집행하기 위해서다. 아래
        # ``_evict_locked`` 참조. 평범한 dict 이던 시절의 sweep 이 무엇이었는지도 거기 있다.
        self._buckets: "OrderedDict[str, tuple[float, set, bool]]" = OrderedDict()

    @property
    def tracked_sources(self) -> int:
        """현재 기억 중인 출처 버킷 수 (메모리 봉투 단언용)."""
        with self._lock:
            return len(self._buckets)

    def record_failure(self, fingerprint: object, identifier: object) -> None:
        try:
            self._record(fingerprint, identifier)
        except Exception:  # noqa: BLE001 — 위 docstring: 관측이 로그인을 깨지 않는다
            pass

    def _record(self, fingerprint: object, identifier: object) -> None:
        source = self._digest(fingerprint)
        account = self._digest(identifier)
        now = self._clock()
        with self._lock:
            started_at, accounts, already = self._buckets.get(
                source, (now, set(), False),
            )
            if now - started_at >= SPRAYING_WINDOW_SECONDS:
                started_at, accounts, already = now, set(), False
            # ⚠️ 상한을 넘으면 더 담지 않는다. 임계값을 이미 넘었다면 답은 바뀌지 않으므로
            # 계속 기억할 이유가 없다.
            if len(accounts) < SPRAYING_MAX_TRACKED_ACCOUNTS:
                accounts.add(account)
            suspected = spraying_suspected(len(accounts)) and not already
            self._buckets[source] = (started_at, accounts, already or suspected)
            self._buckets.move_to_end(source)
            self._evict_locked(protected=source)
        if suspected and self._on_suspected is not None:
            self._on_suspected(source, len(accounts))

    def _evict_locked(self, *, protected: str) -> None:
        """상한을 넘는 동안 **가장 오래 안 건드린** 버킷부터 버린다. O(1)/건.

        ⚠️ **여기 있던 sweep 을 되살리지 마라.** 이전 판은 매 기록마다 전 버킷을 훑어
        만료된 것을 지웠고(``_prune_locked``), 그것은 **락 안의 O(n)** 이다. 버킷 키가
        공격자에게 열려 있는 동안 그 훑기는 key-rotation flood 를 CPU·락 경합 증폭기로
        바꾼다 — 형제 :class:`FixedWindowRateLimiter` 가 정확히 같은 이유로 같은 sweep 을
        지웠고, 그 docstring 이 근거를 갖는다.

        LRU 순서가 그 sweep 을 **포함한다**: 만료된 창은 정의상 살아 있는 어느 창보다
        오래 안 건드려진 것이므로 먼저 쫓겨난다.

        ⚠️ 축출은 **관측을 잃을 뿐 잘못 발화하지 않는다**(이 계층은 아무것도 차단하지
        않으므로 잃는 것은 로그 한 줄의 기회다). 안전한 방향이다.
        """
        while len(self._buckets) > SPRAYING_MAX_TRACKED_SOURCES:
            victim, state = self._buckets.popitem(last=False)
            if victim == protected:
                self._buckets[victim] = state
                break

    def _digest(self, value: object) -> str:
        return fingerprint_digest(self._secret, value)


class LocalAuthService:
    """비밀번호 로그인 유스케이스."""

    def __init__(
        self,
        *,
        store,
        hasher,
        jwt_config: LocalJwtConfig,
        clock: Callable[[], object],
        revocation_list: Optional[TokenRevocationList] = None,
        spraying_detector: Optional[LoginSprayingDetector] = None,
        rotation_throttle: object = None,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
        epoch_clock: Callable[[], int] = lambda: int(_time.time()),
    ) -> None:
        self._store = store
        self._hasher = hasher
        self._jwt = jwt_config
        self._clock = clock
        self._revocations = revocation_list
        self._spraying = spraying_detector
        # ⚠️ Optional, and ``None`` means **no subject tier** — not a degraded
        # one. A deployment that does not wire it keeps today's behaviour
        # (unbounded rotations bounded only by the peer tier), which is exactly
        # what the kill-switch must mean for a budget on the sign-in path.
        self._rotation_throttle = rotation_throttle
        self._id_factory = id_factory
        self._epoch = epoch_clock

    # ── login ────────────────────────────────────────────────────────────────

    def login(
        self,
        *,
        email: object,
        password: object,
        source_fingerprint: object = '',
    ) -> dict:
        """자격증명을 토큰 쌍으로 바꾼다. 실패는 전부 :class:`InvalidCredentialsError`."""
        normalized = normalize_email(email)
        user = self._store.find_by_email(normalized) if normalized else None

        # ⚠️ 네 갈래가 **한 경로**로 접힌다. 각 갈래마다 다른 응답을 주면 그 차이가
        # 열거 오라클이다. 여기서 더미 해시를 실제로 검증하는 것이 타이밍 축이다.
        if user is None or not user.get('enabled') or not user.get('password_hash'):
            self._hasher.verify_dummy(password)
            self._pay_failed_login_cost()
            self._note_spraying(source_fingerprint, normalized)
            raise InvalidCredentialsError('invalid email or password')

        if is_lock_active(user.get('locked_until'), self._now_for_compare()):
            # 잠긴 계정도 같은 비용을 지불하고 같은 답을 받는다 (위 모듈 docstring).
            # 실제 실패를 기록하지 **않는** 것도 설계다 — 기록하면 잠금이 앞으로 밀려
            # 피해자 계정 영구 잠금이 된다. 대신 같은 왕복 비용만 지불한다.
            self._hasher.verify_dummy(password)
            self._pay_failed_login_cost()
            self._note_spraying(source_fingerprint, normalized)
            raise InvalidCredentialsError('invalid email or password')

        if not self._hasher.verify(password, user.get('password_hash')):
            outcome = self._store.record_failed_login(user['id'], now=self._clock())
            if outcome.get('locked_until'):
                # ⚠️ 잠금은 **경보 대상**이다 — 정당한 오타 다섯 번일 수도, 공격일 수도
                # 있고, 그 판단은 사람이 한다. 기록되지 않으면 판단할 기회 자체가 없다.
                logger.warning(
                    'local account locked after repeated failures: subject=%s '
                    'attempts=%s',
                    user.get('subject'), outcome.get('failed_login_attempts'),
                )
            self._note_spraying(source_fingerprint, normalized)
            raise InvalidCredentialsError('invalid email or password')

        self._store.record_successful_login(user['id'], now=self._clock())
        # ⚠️ 감사 흔적. 이 표면은 로그인·잠금·비밀번호 변경을 다루는데 착지 시점까지
        # **로그가 한 줄도 없었다**(적대 평가 R2). 흔적이 없으면 사고 조사 때 "누가
        # 언제 들어왔나"에 답할 방법이 없고, 그것은 이 축이 대체하려는 Keycloak 이
        # 기본으로 주던 것이다.
        #
        # ⚠️ 비밀번호는 물론이고 **토큰도 싣지 않는다**. subject 는 이미 이 사용자가
        # 소유한 식별자이므로 로그에 남는 것이 새로운 노출이 아니다.
        logger.info('local login succeeded: subject=%s', user.get('subject'))
        return self._issue_pair(user, session_version=user.get('session_version'))

    # ── refresh ──────────────────────────────────────────────────────────────

    def refresh(self, *, refresh_token: object) -> dict:
        """리프레시 토큰을 **새 토큰 쌍**으로 바꾼다.

        ⚠️ 사용자를 DB 에서 **다시 읽는다.** 리프레시 토큰은 권한을 싣지 않으므로
        (7일 동안 굳어 있을 값이다) 새 액세스 토큰의 권한은 지금의 DB 상태에서 나온다 —
        그래서 권한 회수가 최대 15분 안에 반영된다.
        """
        try:
            claims = verify_token(
                self._jwt, refresh_token, expected_type=TOKEN_TYPE_REFRESH,
            )
        except LocalTokenError as exc:
            raise InvalidCredentialsError('invalid refresh token') from exc

        # ⚠️ **ROTATION, AND IT MUST BE ATOMIC.** The presented refresh token is
        # retired the moment it is spent, so it is single-use. Without rotation it
        # stayed valid for its whole 7-day TTL and could be replayed without limit
        # — measured: three replays, three fresh sessions. On a plaintext LAN,
        # where capture is assumed, an unrotated refresh token is a 7-day skeleton
        # key. This is OAuth 2.1 / BCP for public clients, and it is what makes
        # logout able to end a session at all: revoking a token that can simply be
        # re-presented revokes nothing.
        #
        # ⚠️ **The claim happens BEFORE the database read, and that ordering is the
        # whole point** (adversarial review round 2). The first version asked
        # ``is_revoked`` here and revoked at the end, with ``find_by_email`` — a
        # round-trip that releases the GIL — in between. Twelve concurrent requests
        # carrying the *same* captured token all passed the check before any of them
        # reached the revoke: **four got 200**, and all four kept minting sessions
        # after the victim logged out. One capture became four logout-proof chains,
        # through a ``public`` route with no throttle. That falsified the "single
        # use" sentence above, and it sits *upstream* of everything the custody
        # partition guarantees — the attacker never needed eviction at all.
        #
        # ⚠️ ``churn=True`` — 회전이 만드는 항목은 상한에서 **먼저** 버려진다.
        # 로그아웃·비밀번호 변경은 사람이 *"이 세션을 끝내겠다"* 고 결정한 것이라
        # 그 표시를 달지 않는다.
        token_id = claims.get('jti')
        claimed = self._revocations is not None and self._revocations.claim(
            token_id,
            expires_at=float(claims.get('exp') or 0),
            custodian=self._custodian_for(claims),
            churn=True,
        )
        if self._revocations is not None and not claimed:
            raise InvalidCredentialsError('invalid refresh token')

        try:
            user = self._store.find_by_email(claims.get('sub'))
            if (user is None or not user.get('enabled')
                    or not user.get('password_hash')):
                raise InvalidCredentialsError('invalid refresh token')

            # ⚠️ session_version 대조 — 로그아웃/비밀번호 변경이 이미 발급된 리프레시
            # 토큰을 **즉시** 죽이는 유일한 기전이다. 서명은 여전히 유효하다.
            if int(claims.get(CLAIM_SESSION_VERSION) or 0) != int(
                user.get('session_version') or 0
            ):
                raise InvalidCredentialsError('invalid refresh token')

            if is_lock_active(user.get('locked_until'), self._now_for_compare()):
                raise InvalidCredentialsError('invalid refresh token')

            # ⚠️ **과금은 여기다 — 검증을 전부 통과한 뒤.** 이 줄 위의 네 검사
            # (서명·토큰 종류·``session_version``·잠금)를 통과해야 ``sub`` 가 *주장*이
            # 아니라 *사실*이 된다. 검증 **전**에 과금하면 아무나 위조 토큰에 남의
            # 주소를 적어 그 사람의 회전 예산을 소진시키고 **강제 로그아웃**시킬 수
            # 있다 — 위협모델 A-19 의 형태 그대로다.
            #
            # ⚠️ 그리고 이 예외는 위 ``except BaseException`` 이 잡아 **claim 을
            # 되돌린다**. 그것이 옳다: 거부된 회전은 그 토큰을 *쓰지 않은* 것이고,
            # 되돌리지 않으면 429 하나가 그 세션을 영구히 끝낸다(잠금은 일시적이고
            # 소실은 영구적이라 그 둘을 맞바꿀 수 없다 — 아래 주석과 같은 논거).
            if self._rotation_throttle is not None:
                denial = self._rotation_throttle.charge(claims.get('sub'))
                if denial is not None:
                    # ⚠️ **관측자 없는 스로틀은 조용한 사고다.** 이 tier 가 발화하면
                    # 시험원은 세션이 끊기거나 지연되는 것을 겪는데, 착지 직전까지
                    # 그 사실을 말하는 로그도 메트릭도 **0** 이었다(적대 평가 2R).
                    # 형제 스프레이 탐지기가 «관측자 없는 탐지기는 조용한 no-op» 이라는
                    # 같은 이유로 관측자를 요구한다. subject 는 이미 검증된 값이다.
                    #
                    # ⚠️ **그리고 «로그에 적었다» 는 «운영자가 볼 수 있다» 가 아니다**
                    # (적대 평가 3R 실측: 실 429 를 유발했는데 `docker logs` 에는
                    # uvicorn 의 일반 접근 줄 두 개뿐 — 어느 tier 인지도, 누구인지도,
                    # 위 문장도 없었다). 같은 웨이브의 형제 둘은 이미 통지 채널을
                    # 쓰는데 이 자리만 로거로 끝냈다. 채널은 하나다.
                    announce(
                        CHANNEL_LOCAL_AUTH,
                        f'refresh rotation budget exhausted: '
                        f'subject={claims.get("sub")} '
                        f'retry_after={denial.retry_after_seconds}s — this '
                        f'account has rotated more than the measured envelope '
                        f'allows in one window. If a tester is seeing this, the '
                        f'envelope is wrong; if not, someone holds a captured '
                        f'refresh token.',
                        log=logger,
                    )
                    raise RefreshRotationLimitedError(
                        'too many refresh rotations for this account',
                        retry_after_seconds=denial.retry_after_seconds,
                        rate_limit=denial.limit,
                    )
        except BaseException:
            # ⚠️ **거부된 시도는 그 토큰을 쓰지 않은 것이다.** 되돌리지 않으면 잠긴
            # 사용자의 세션이 15분이 아니라 **영구히** 끝난다 — 잠금은 일시적이고
            # 소실은 영구적이라 그 둘을 맞바꿀 수 없다. 저장소 오류로 여기 도달하는
            # 경우도 같다: 그것은 클라이언트의 잘못이 아니다.
            #
            # 되돌리는 동안 그 키는 계속 목록에 있었으므로 위 원자성은 유지된다.
            #
            # ⚠️ ``claimed`` 를 묻지 ``_revocations is not None`` 만 묻지 않는다 —
            # 목록이 없는 구성(폐기 목록 미배선)에서는 claim 이 일어나지 않았으므로
            # 되돌릴 것도 없고, 그 경우에 ``release`` 를 부르면 ``None`` 에 접근한다.
            if claimed:
                self._revocations.release(token_id)
            raise

        return self._issue_pair(user, session_version=user.get('session_version'))

    # ── me ───────────────────────────────────────────────────────────────────

    def me(self, principal) -> dict:
        user = self._require_local_user(principal)
        return self._profile(user)

    # ── change password ──────────────────────────────────────────────────────

    def change_password(
        self,
        principal,
        *,
        current_password: object,
        new_password: object,
        access_token: object = '',
        source_fingerprint: object = '',
    ) -> dict:
        """본인 비밀번호 변경.

        성공 시 무효화되는 것을 **정확히** 적는다 — 이전 docstring 은 *"모든 기존
        토큰이 무효가 된다"* 고 주장했고 그것은 **거짓이었다**(적대 평가 2026-08-21
        실측: 비밀번호 변경 뒤에도 옛 액세스 토큰이 ``platform:admin`` 을 그대로 행사).
        ``session_version`` 은 **리프레시 경로에서만** 대조되기 때문이다.

        실제 보장:

        * **모든 리프레시 토큰** — 즉시 무효(``session_version`` 증가).
        * **이 요청이 제시한 액세스 토큰** — 즉시 무효(``jti`` 폐기).
        * **다른 기기의 액세스 토큰** — 남은 수명(≤15분)까지 유효.

        ⚠️ 마지막 줄이 남는 한계이고 숨기지 않는다. 없애려면 요청마다 ``sv`` 를 DB 와
        대조해야 하고(권한 조회가 이미 그러듯), 그 판단은 장부 항목이다.

        ⚠️ 현재 비밀번호를 반드시 재확인한다. 액세스 토큰만으로 비밀번호를 바꿀 수
        있으면, 훔친 토큰 하나가 **계정 탈취**가 된다(공격자가 비밀번호를 바꿔 정당한
        사용자를 잠가 버린다).
        """
        user = self._require_local_user(principal)
        # ⚠️ **잠긴 계정은 이 표면에서도 추측을 계속할 수 없다** (2026-08-22, 형제 결함 ①).
        # 착지 시점의 이 함수는 실패 카운터를 *올리기만* 하고 ``is_lock_active`` 를 **묻지
        # 않았다**. 그래서 로그인이 잠긴 계정에 대해서도 여기서는 무제한으로 비밀번호를
        # 추측할 수 있었다 — 잠금이 한쪽 문에만 걸려 있었던 셈이다.
        if is_lock_active(user.get('locked_until'), self._now_for_compare()):
            self._note_spraying(source_fingerprint, user.get('subject'))
            raise InvalidCredentialsError('current password does not match')
        if not self._hasher.verify(current_password, user.get('password_hash')):
            # ⚠️ 실패 카운터를 함께 올린다 — 이 표면도 비밀번호 추측 표면이다.
            #
            # ⚠️ **이 줄을 지우고 싶어지는 이유와, 지우면 안 되는 이유** (적대 평가
            # 2026-08-21 R3): 훔친 액세스 토큰을 쥔 공격자가 일부러 5회 틀리면 정당한
            # 사용자가 15분간 로그인하지 못한다 — 계정 잠금 지렛대다. 그러나 카운터를
            # 떼면 그 공격자는 ``current_password`` 를 **무제한** 추측할 수 있고, 맞추면
            # 비밀번호를 바꿔 **계정을 통째로 가져간다**. 가용성 15분과 계정 탈취를
            # 맞바꾸는 것이므로 카운터는 **유지**한다.
            #
            # 지렛대는 제거가 아니라 **축소**한다: 위 잠금 확인이 잠긴 뒤의 추측을 끊고,
            # 계정축 스로틀(``credential_rule``)이 잠금에 닿기 전에 분당 6회로 자르며,
            # 아래 통지가 이 표면을 관측 사각지대에서 꺼낸다. 남은 지렛대는 장부 항목이다.
            outcome = self._store.record_failed_login(
                user['id'], now=self._clock(),
            )
            if outcome.get('locked_until'):
                # ⚠️ **이 문 하나가 조용했다** (적대 평가 2R F2). 이 함수는
                # ``record_failed_login`` 의 반환을 버렸고, 그래서 로그인 문이 내는
                # 잠금 경보(위 ``login`` 참조)가 여기서는 한 번도 뜨지 않았다.
                #
                # 실해는 관측 부재로 끝나지 않는다: 훔친 액세스 토큰을 쥔 공격자가
                # 15분마다 5회 틀리는 것만으로 피해자를 **영구히** 잠글 수 있고, 잠긴
                # 동안에는 비밀번호 변경도(잠금 확인이 막는다) 로그인도 불가능하며
                # ``session_version`` 을 올리는 유일한 코드가 그 비밀번호 변경이므로
                # 훔친 토큰은 계속 산다. 그 상태를 푸는 HTTP 표면이 **하나도 없다**.
                #
                # 이 웨이브는 그 설계 판단을 뒤집지 않는다(ADR-0021 D-8: 카운터를 떼면
                # 무제한 추측 = 계정 탈취). 대신 **조용한 것을 시끄럽게** 만든다 —
                # 스프레이 탐지기는 서로 다른 계정 5개를 요구하므로 단일 계정 공격을
                # 구조적으로 볼 수 없고, 그러면 남는 관측이 0 이었다.
                logger.warning(
                    'local account locked after repeated failures on the '
                    'change-password door: subject=%s attempts=%s. ⚠️ while '
                    'locked this account can neither log in nor change its '
                    'password, and no operation lifts the lock early — a holder '
                    'of a stolen access token can sustain this. Investigate '
                    'before it repeats.',
                    user.get('subject'), outcome.get('failed_login_attempts'),
                )
            # ⚠️ 착지 시점까지 이 표면은 스프레이 탐지기에 **통지하지 않았다**. 즉 훔친
            # 토큰들로 비밀번호를 흩뿌리는 공격이 관측에 한 줄도 남기지 않았다.
            self._note_spraying(source_fingerprint, user.get('subject'))
            raise InvalidCredentialsError('current password does not match')

        defects = password_defects(new_password)
        if defects:
            raise PasswordRejected(
                'new password does not satisfy the policy: '
                + ', '.join(defect.value for defect in defects)
            )
        # ⚠️ 같은 비밀번호로의 "변경"을 거부한다. 강제 변경 상태에서 그것을 허용하면
        # 강제 변경이 아무것도 강제하지 않는 클릭 한 번이 된다.
        if self._hasher.verify(new_password, user.get('password_hash')):
            raise PasswordRejected('new password must differ from the current one')

        new_version = self._store.update_password(
            user['id'],
            password_hash=self._hasher.hash(new_password),
            now=self._clock(),
        )
        logger.info(
            'local password changed: subject=%s session_version=%s',
            user.get('subject'), new_version,
        )
        # ⚠️ 제시된 액세스 토큰을 함께 폐기한다. 이것이 없으면 "비밀번호를 바꿨다"가
        # **지금 이 브라우저의** 토큰조차 끝내지 못한다 — 계정 탈취를 당한 사용자가
        # 취할 수 있는 조치가 15분 동안 아무 효과가 없다는 뜻이다.
        self._revoke_raw(
            access_token, TOKEN_TYPE_ACCESS,
            expected_subject=user.get('subject'),
        )
        refreshed = dict(user)
        refreshed['session_version'] = new_version
        refreshed['force_password_change'] = False
        # ⚠️ **``password_changed_at`` too, and forgetting it soft-bricked every
        # fresh deployment.** ``_issue_pair`` derives the forced-change flag from
        # BOTH fields (`force_password_change` OR `password_changed_at IS NULL`),
        # and `create_local_user` leaves the timestamp NULL. So the row read
        # *before* the update still carried NULL, the brand-new token still said
        # "you must change your password", and the bootstrap administrator landed
        # on a shell where every operation answered 403 — after doing exactly what
        # was demanded. Reproduced end-to-end by adversarial review; the only way
        # out was to sign out, sign in again, and invent a *third* password.
        #
        # The store just wrote this value; mirroring it here keeps the token we
        # hand back consistent with the row we just committed.
        refreshed['password_changed_at'] = self._clock()
        return self._issue_pair(refreshed, session_version=new_version)

    # ── logout ───────────────────────────────────────────────────────────────

    def logout(
        self,
        principal,
        *,
        access_token: object = '',
        refresh_token: object = '',
    ) -> dict:
        """이 세션의 **두 토큰 모두** 폐기한다.

        ⚠️ **리프레시 토큰을 함께 받는 것이 이 연산의 핵심이다.** 액세스 토큰만
        폐기하면 로그아웃은 아무것도 끝내지 않는다 — 캡처된 리프레시 토큰이 7일 동안
        새 액세스 토큰을 계속 찍어낸다(적대 평가 2026-08-21 실측: 로그아웃 뒤 refresh
        200, 새 액세스로 me 200). 평문 LAN 에서 캡처는 가정이므로 그 구멍은 이론이
        아니다.

        ⚠️ **``session_version`` 은 건드리지 않는다.** 올리면 이 사용자의 **다른 기기
        세션이 전부 끊긴다** — 한 탭에서 로그아웃했다고 다른 컴퓨터의 시험이 중단되는
        것은 로그아웃이 아니라 사고다. 전 세션 무효화는 비밀번호 변경의 몫이다.

        ⚠️ **항상 성공한다.** 토큰이 이미 만료됐거나 읽을 수 없어도 ``logged_out``
        이다. 로그아웃이 실패할 수 있으면 사용자는 *로그아웃했다고 믿는 채로 로그인
        상태로 남는* 상황에 놓이고, 그것이 이 연산에서 가능한 최악의 결과다.
        """
        # ⚠️ **제시된 토큰은 인증된 신원의 것이어야 한다** (적대 평가 2026-08-22).
        # 착지 시점의 이 함수는 body 의 ``refresh_token`` 을 그냥 폐기했으므로, 남의
        # 리프레시 토큰을 쥔 인증 사용자가 **그 사람을 강제 로그아웃**시킬 수 있었다.
        # 실해는 제한적이지만(그 토큰을 쥔 것이 이미 계정에 가깝다) 더 중요한 것은
        # 그것이 **보관 귀속과 인증된 신원을 갈라 놓는다**는 점이다 — M-4 의 전제가
        # 바로 그 둘이 같다는 것이고, 삽입 사이트에서 그 전제가 깨지면 분할이 지키는
        # 문장도 함께 약해진다.
        subject = getattr(principal, 'subject', None)
        access_retired = self._revoke_raw(
            access_token, TOKEN_TYPE_ACCESS, expected_subject=subject,
        )
        refresh_retired = self._revoke_raw(
            refresh_token, TOKEN_TYPE_REFRESH, expected_subject=subject,
        )
        if not refresh_retired:
            # ⚠️ Still a 200 — logout must not be failable (see the docstring).
            # But it must not be SILENT either: without the refresh token this
            # call left a credential alive that mints new sessions for a week,
            # and nothing else in the system would ever say so.
            logger.warning(
                'logout retired no refresh token (subject=%s): the session '
                'remains refreshable until the refresh token expires — the '
                'client must send refresh_token',
                getattr(principal, 'subject', None),
            )
        return {
            'logged_out': True,
            'access_token_revoked': access_retired,
            'refresh_token_revoked': refresh_retired,
        }

    # ── administrative unlock ────────────────────────────────────────────────

    def unlock_account(self, principal, *, subject: object) -> dict:
        """관리자가 ``subject`` 의 로그인 잠금을 푼다 (2026-08-23).

        운영자 판정(2026-08-22): 해제 주체는 **관리자 해제 + 자동 만료 둘 다**.
        자동 만료는 이미 동작하므로 이 함수가 나머지 절반이다.

        ⚠️ **왜 이 operation 이 필요한가.** 훔친 액세스 토큰을 쥔 공격자가 비밀번호
        변경 문에서 15분마다 5회 틀리는 것만으로 피해자를 **영구히** 잠글 수 있고,
        잠긴 동안에는 로그인도 비밀번호 변경도 불가능하다. 그 상태를 푸는 HTTP
        표면이 **하나도 없었다**(``change_password`` 의 경고 로그가 그 사실을
        이름으로 적고 있었다). ADR-0021 ``D-8`` 을 뒤집지 않고 — 카운터 제거는
        무제한 추측 = 계정 탈취다 — 출구만 만든다.

        ⚠️ **해제는 ``session_version`` 을 올린다.** 그 판단의 근거와 대가는
        ``local_user_store.UNLOCK_ACCOUNT_SQL`` 이 소유한다(요약: 올리지 않으면
        공격자가 **즉시 재잠금**한다). 남는 창도 그곳에 적혀 있다 — 공격자의 액세스
        토큰은 ≤15분 산다.

        ⚠️ **행위자는 인증된 principal 에서만 온다.** body 에서 받으면 감사 원장이
        *누가 풀었는가* 에 대해 요청자가 적은 값을 기록한다 — 형제 멤버십 축이 같은
        이유로 같은 규율을 갖는다(FE-P8 operator provenance).
        """
        actor = str(getattr(principal, 'subject', '') or '').strip()
        # ⚠️ **빈 문자열만 묻는 것으로는 부족했다** (적대 평가 실측 2026-08-23).
        # 프로덕션이 실제로 만드는 미인증 principal 은 ``ApiPrincipal.anonymous()``
        # 이고 그 ``subject`` 는 **비어 있지 않은 리터럴** ``'anonymous'`` 다. 즉 이
        # 가드는 *프로덕션이 만들지 않는 모양*(빈 subject)만 막고 있었고, 봉인도 그
        # 모양으로 물어 초록이었다 — 이 저장소의 «두 번째 자물쇠가 문서화돼 있고
        # 봉인돼 있고 존재하지 않는다» 형태 그대로다.
        #
        # 오늘은 ``authorize()`` 가 먼저 거부하므로 end-to-end 로는 도달 불가이지만,
        # 그 첫 자물쇠가 움직이는 날 이 자물쇠가 실제로 잠겨 있어야 한다.
        #
        # ⚠️ 센티넬은 **리터럴이 아니라 그 클래스에서 파생**한다 — 손으로 적으면
        # ``anonymous()`` 가 바뀌는 날 이 가드가 조용히 아무것도 막지 않게 된다.
        if not actor or actor == ANONYMOUS_PRINCIPAL_SUBJECT:
            # 인증된 행위자가 없으면 **거부한다** — 익명 행위자로 감사 행을 쓰느니
            # 아무것도 하지 않는 편이 낫다(형제 멤버십 축과 같은 판단).
            raise PermissionError(
                'unlock_local_account requires an authenticated actor for audit'
            )
        try:
            outcome = self._store.unlock_account(
                subject,
                now=self._clock(),
                audit_record={
                    'id': str(uuid.uuid4()),
                    'actor_subject': actor,
                    'occurred_at': self._clock(),
                    'created_at': self._clock(),
                    # ⚠️ **JSON 문자열이어야 한다 — dict 는 psycopg 가 `%s` 에
                    # 바인딩하지 못한다.** 착지 직전 판이 dict 를 넘겼고, 그 결과 이
                    # operation 은 실 PostgreSQL 에서 **항상 503** 이었다(감사 INSERT
                    # 가 터지고 같은 트랜잭션이라 해제까지 롤백된다). 76개 봉인이
                    # 전부 초록이었던 이유는 테스트가 쓰는 유일한 감사 writer 가
                    # 기록만 하는 대역이라 그 값을 커서에 **바인딩한 적이 없어서**다.
                    # 형제 다섯(claim · chamber · membership · rekey · project)이
                    # 전부 `json.dumps` 를 넘긴다 — 이 줄은 그 규약을 따른다.
                    'detail_json': json.dumps(
                        {'reason': 'administrative unlock'},
                        ensure_ascii=False, sort_keys=True,
                    ),
                },
            )
        except ValueError as exc:
            # ``local_identity_key`` 는 읽을 수 없는 식별자에 ValueError 를 낸다.
            # 그것은 «그런 계정이 없다» 와 관리자에게 같은 조치를 뜻한다.
            raise LocalAccountNotFoundError(
                'no local account for that identifier'
            ) from exc
        if not outcome:
            raise LocalAccountNotFoundError('no local account for that identifier')
        if not outcome.get('was_locked'):
            # 잠기지 않은 계정에 대한 해제는 **무-op** 이다 — 세션도 끊지 않고 감사도
            # 남기지 않는다. 200 으로 답하되 무엇이 일어났는지(=아무것도) 를 봉투가
            # 말한다. 404 로 답하면 «오타» 와 «이미 풀림» 이 한 답이 된다.
            logger.info(
                'local account unlock requested but no lock was held: target=%s '
                'actor=%s — nothing was changed and no session was ended.',
                outcome.get('subject'), actor,
            )
            return {
                'subject': outcome.get('subject'),
                'session_version': None,
                'was_locked': False,
                'sessions_invalidated': False,
            }
        # ⚠️ 로거만으로는 `docker logs` 에 닿지 않는다 — 사유와 그 한계는
        # ``operator_notice`` 가 소유한다. «이 계정의 모든 세션이 끊겼다» 는
        # 운영자가 그 자리에서 알아야 하는 사실이다.
        announce(
            CHANNEL_LOCAL_AUTH,
            f'account unlocked by administrator: '
            f'target={outcome.get("subject")} actor={actor} — every existing '
            f'session for that account has been invalidated.',
        )
        logger.warning(
            'local account unlocked by administrator: target=%s actor=%s '
            'session_version=%s — every existing session for that account has '
            'been invalidated. ⚠️ the attacker access token that caused the lock '
            'survives for its remaining lifetime (the configured access-token '
            'TTL, default 900s = 15 min), so re-locking is bounded by that TTL, '
            'not prevented.',
            outcome.get('subject'), actor, outcome.get('session_version'),
        )
        return {
            'subject': outcome.get('subject'),
            'session_version': int(outcome.get('session_version') or 0),
            'was_locked': True,
            'sessions_invalidated': True,
        }

    def _revoke_raw(
        self, token: object, expected_type: str, *,
        expected_subject: object = None,
    ) -> bool:
        """Best-effort revoke of a raw token string. Never raises.

        Returns whether anything was actually retired, so the caller can say so.
        ⚠️ Silence here is the failure mode the logout docstring names: a caller
        that gets 200 and keeps a live session has been told the opposite of what
        happened.

        ⚠️ ``expected_subject`` binds the presented token to the **authenticated**
        identity. ``None`` means *"there is no principal to bind to"* and skips the
        check — not *"any subject is acceptable"*. The distinction matters because
        the caller that has a principal must always pass it; a default of "skip"
        would let a new call site drop the binding silently, which is how the
        defect this closes got in.
        """
        if self._revocations is None or not token:
            return False
        try:
            claims = verify_token(self._jwt, token, expected_type=expected_type)
        except (LocalTokenError, ValueError):
            return False
        if expected_subject is not None:
            presented = claims.get('sub')
            if not isinstance(presented, str) or presented != str(expected_subject):
                # ⚠️ 거부하되 **터지지 않는다** — 로그아웃은 실패할 수 없는 연산이다.
                # 대신 시끄럽게 남긴다: 남의 토큰을 로그아웃에 실어 보내는 것은
                # 정상 클라이언트가 하지 않는 일이다.
                logger.warning(
                    'logout was handed a %s token belonging to a different '
                    'subject than the authenticated caller; it was NOT revoked',
                    expected_type,
                )
                return False
        self._revoke(claims)
        return True

    def _revoke(self, claims: Mapping, *, churn: bool = False) -> None:
        """폐기 목록에 넣는 **유일한 통로**. 보관자는 여기서 정해진다.

        ⚠️ ``claims`` 는 이미 :func:`verify_token` 을 통과한 것이다 — 세 호출자
        (``refresh`` 회전 · ``_revoke_raw`` → 로그아웃 · 비밀번호 변경) 전부 그렇다.
        그러므로 ``sub`` 는 **위조 불가**이고, 그 사실이 보관 분할을 성립시킨다: 피해자
        보관자 아래로 삽입하려면 피해자의 유효 서명 토큰이 필요하다.

        ⚠️ 보관자는 평문 ``sub`` 가 아니라 그 **다이제스트**다. 형제
        :class:`LoginSprayingDetector` 와 **같은 함수**를 쓴다 — 목록이 통째로 유출돼도
        누구의 세션인지 나오지 않고, 익명화 규칙이 한 곳에만 존재한다.
        """
        token_id = claims.get('jti')
        if self._revocations is not None and token_id:
            self._revocations.revoke(
                token_id,
                expires_at=float(claims.get('exp') or 0),
                custodian=self._custodian_for(claims),
                churn=churn,
            )

    def _custodian_for(self, claims: Mapping) -> str:
        """검증된 ``sub`` → 불투명 보관자 키. 총함수.

        ``sub`` 가 없거나 문자열이 아니면 :data:`UNATTRIBUTED_CUSTODIAN` 을 돌려준다 —
        **다이제스트를 만들지 않는다**. 빈 값을 다이제스트하면 모든 미상 항목이 하나의
        *진짜처럼 보이는* 보관자로 모여, 미상 버킷이 실재 보관자와 구분되지 않는다.
        """
        subject = claims.get('sub')
        if not isinstance(subject, str) or not subject.strip():
            return UNATTRIBUTED_CUSTODIAN
        # ⚠️ 로그인 경로와 **같은 정규화**를 쓴다. ``find_by_email`` 은 소문자로
        # 접는데 여기서 ``strip()`` 만 하면, 정규화되지 않은 ``subject`` 행이 하나라도
        # 생기는 날 한 신원이 두 보관자로 갈라진다 — 그리고 갈라진 둘은 각자 더 가벼워져
        # **더 잘 보호받는다**(공정몫 분모가 부풀고 아무도 자기 몫을 넘지 않는다).
        # 오늘은 무해하지만 정규화기가 둘이면 갈라지고, 갈라진 곳이 우회로다.
        normalized = normalize_email(subject)
        if not normalized:
            return UNATTRIBUTED_CUSTODIAN
        return fingerprint_digest(self._jwt.secret, normalized)

    # ── internals ────────────────────────────────────────────────────────────

    def _issue_pair(self, user: Mapping, *, session_version: object) -> dict:
        issued_at = self._epoch()
        version = int(session_version or 0)
        force_change = bool(
            user.get('force_password_change')
            or user.get('password_changed_at') is None
        )
        # ⚠️ 강제 변경 중에는 권한을 **싣지 않는다.** 정책 거부(계약 M-5)가 첫 방어이고
        # 이것이 두 번째다 — 어느 한쪽이 뚫려도 그 토큰으로 할 수 있는 일이 없다.
        if force_change:
            permissions = ()
        else:
            resolve_permissions = getattr(self._store, 'local_permissions', None)
            if callable(resolve_permissions):
                permissions = resolve_permissions(user.get('id'))
            else:
                # Compatibility for narrow test doubles and older embedded
                # stores; production PostgresLocalUserStore implements the
                # complete SSOT-backed resolver above.
                permissions = self._store.global_permissions(user.get('id'))
        subject = str(user.get('subject') or '')
        common = dict(
            subject=subject,
            issued_at=issued_at,
            session_version=version,
            force_password_change=force_change,
        )
        access_id = self._id_factory()
        access = issue_token(
            self._jwt, token_type=TOKEN_TYPE_ACCESS, token_id=access_id,
            permissions=permissions,
            display_name=str(user.get('display_name') or ''),
            email=str(user.get('email') or subject),
            **common,
        )
        refresh = issue_token(
            self._jwt, token_type=TOKEN_TYPE_REFRESH, token_id=new_token_id(),
            **common,
        )
        return {
            'access_token': access,
            'refresh_token': refresh,
            'token_type': 'Bearer',
            'expires_in': self._jwt.access_ttl_seconds,
            'force_password_change': force_change,
            'user': self._profile(user),
        }

    @staticmethod
    def _profile(user: Mapping) -> dict:
        """응답에 실리는 사용자 정보.

        ⚠️ ``password_hash`` 는 물론이고 ``failed_login_attempts``/``locked_until``
        도 싣지 않는다 — 후자는 자기 계정에 대해서도 공격 진행도를 알려준다.
        """
        return {
            'subject': str(user.get('subject') or ''),
            'email': str(user.get('email') or user.get('subject') or ''),
            'display_name': str(user.get('display_name') or ''),
            'enabled': bool(user.get('enabled')),
            'force_password_change': bool(
                user.get('force_password_change')
                or user.get('password_changed_at') is None
            ),
        }

    def _require_local_user(self, principal) -> dict:
        subject = str(getattr(principal, 'subject', '') or '').strip()
        if not subject or subject == 'anonymous':
            raise InvalidCredentialsError('authentication required')
        user = self._store.find_by_email(subject)
        if user is None or not user.get('enabled'):
            raise InvalidCredentialsError('authentication required')
        return user

    def _pay_failed_login_cost(self) -> None:
        """실패 분기가 **같은 DB 왕복**을 지불하게 한다 — 네 번째 타이밍 축.

        ⚠️ **적대 평가(2026-08-21)가 찾은 누출이다.** 오류 코드·본문·bcrypt 비용 세 축을
        맞춰 놓고도, 실제 실패 기록(``record_failed_login`` 의 원자적 ``UPDATE``)은
        *계정이 존재하고 활성이고 잠기지 않은* 분기에서**만** 돌았다. 나머지 세 분기는
        쓰기 없이 즉시 반환했고, 동기 쓰기 트랜잭션의 커밋 비용은 네트워크 너머에서
        측정 가능하다(실측 시뮬레이션 중앙값 +0.98ms). 즉 **틀린 비밀번호 한 번**으로
        그 이메일의 실재 여부를 가를 수 있었다 — M-4 가 막으려던 바로 그 열거다.

        같은 UPDATE 를 **아무 행에도 맞지 않는 임의 id** 로 실행해 비용만 지불한다.
        부작용은 0이고(0 rows), 지불하는 왕복은 같다.

        ⚠️ 총함수다. 이 평탄화가 실패해서 로그인 경로가 500 이 되면, 그것은 누출보다
        나쁜 결과다 — 게다가 그 500 자체가 새로운 오라클이 된다.
        """
        try:
            self._store.record_failed_login(self._id_factory(), now=self._clock())
        except Exception:  # noqa: BLE001 — 위 docstring 참조
            pass

    def _note_spraying(self, fingerprint: object, identifier: object) -> None:
        if self._spraying is not None:
            self._spraying.record_failure(fingerprint, identifier)

    def _now_for_compare(self):
        """잠금 비교용 '지금'. ``clock`` 이 무엇을 돌려주든 그것으로 비교한다."""
        return self._clock()


def bootstrap_local_admin(
    *,
    store,
    hasher,
    email: object,
    password: object,
    now,
    id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    role_key: str = BOOTSTRAP_ADMIN_ROLE_KEY,
) -> Optional[dict]:
    """최초 관리자를 **없을 때만** 만든다. 이미 있으면 ``None``.

    ⚠️ **부트스트랩이 백도어가 되면 안 된다.** 이미 로컬 사용자가 하나라도 있으면 이
    함수는 아무것도 하지 않는다 — 그렇지 않으면 환경변수를 바꾸고 재시작하는 것만으로
    **살아 있는 관리자의 비밀번호를 재설정**할 수 있고, 그것은 부트스트랩이 아니라
    권한 상승 경로다. 저장소 쪽 ``ON CONFLICT DO NOTHING`` 이 두 번째 방어다.

    ⚠️ **``force_password_change=True`` 로 만든다.** 초기 비밀번호는 환경변수에 적혀
    있고 배포 로그·셸 히스토리·compose 파일에 남는다. 첫 로그인에서 바꾸지 않으면 그
    값이 영구 자격증명이 되고, 아무도 바꾸지 않는다.

    ⚠️ 비밀번호는 정책을 통과해야 한다 — 해셔가 거부하면 :class:`PasswordRejected` 가
    그대로 올라간다. 부팅이 시끄럽게 실패하는 쪽이, 아무도 로그인할 수 없는 시스템이
    조용히 뜨는 쪽보다 낫다.
    """
    normalized = normalize_email(email)
    if not normalized or not password:
        return None
    if store.count_local_users() > 0:
        return None
    created = store.create_local_user(
        user_id=id_factory(),
        email=normalized,
        display_name=normalized,
        password_hash=hasher.hash(password),
        force_password_change=True,
        now=now,
    )
    if created is None:
        # Lost a race with another process that bootstrapped first. Not an error —
        # the whole point is that exactly one administrator gets created.
        return None
    store.grant_role(created['id'], role_key)
    # The historical project-admin grant remains for bootstrap compatibility,
    # but the dedicated global hard-delete capability must be seeded through a
    # separate store seam. Older test doubles may not expose that seam; their
    # project-role assertion remains valid while production receives the
    # global-role backfill.
    grant_global_role = getattr(store, 'grant_global_role', None)
    if callable(grant_global_role):
        grant_global_role(created['id'], BOOTSTRAP_GLOBAL_ROLE_KEY)
    # ⚠️ 부트스트랩 관리자 생성은 **한 배포에 한 번뿐인 사건**이고, 그것이 언제 누구를
    # 만들었는지는 나중에 반드시 물어보게 된다. 초기 비밀번호는 당연히 싣지 않는다.
    logger.warning(
        'bootstrap administrator created: subject=%s role=%s — this account must '
        'change its password on first login',
        created.get('subject'), role_key,
    )
    return created
