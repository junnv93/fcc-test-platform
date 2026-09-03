"""폐기 목록 보관 분할 봉인 (계약 `refresh-rotation-insertion-binding`).

⚠️ **이 파일의 단언은 결과의 *출처*를 묻는다.** 직전 웨이브의 최대 손실은 *"429 가
났다"* 로 단언한 봉인이 결함 코드에서도 통과한 것이었다. 여기서 *"replay 가 401 이다"*
는 **불충분하다** — `session_version` 불일치·잠금·만료·서명 오류 어느 쪽으로도 401 이
난다. 그래서 단언은 **피해자의 ``jti`` 가 여전히 폐기 목록에 있는가**이고, 실 라우트
단언은 다른 401 사유를 하나씩 **배제**한 뒤에만 의미를 갖는다.

⚠️ **그리고 반사실이 먼저 온다.** ``TestTheAttackReproducesUnderTheOldRule`` 이 고치기
전 규칙(전역 가장-먼저-만료)에서 피해자 항목이 **실제로 축출된다**를 실행으로 보인다.
그 단언이 없으면 이 파일 전체가 고쳐지지 않은 코드에서도 통과할 수 있다.
"""
from __future__ import annotations

import ast
import inspect
import io
import logging
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_REPO_ROOT / 'src'), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fcc_test_contracts.common.access_policy import ApiAccessPolicy  # noqa: E402
from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402
from fcc_test_contracts.common.local_identity import (  # noqa: E402
    DEFAULT_LOCAL_JWT_ACCESS_TTL_SECONDS,
    DEFAULT_LOCAL_JWT_REFRESH_TTL_SECONDS,
    LOCAL_JWT_MIN_SECRET_BYTES,
    TOKEN_TYPE_REFRESH,
    LocalJwtConfig,
    verify_token,
)
import fcc_test_platform.application.local_auth_service as _local_auth_service  # noqa: E402
import fcc_test_platform.domain.services.token_revocation_policy as _token_revocation_policy  # noqa: E402
from fcc_test_kernel.application.central_contract.api_contracts import PLATFORM_API_OPERATIONS  # noqa: E402
from fcc_test_platform.application.local_auth_service import (  # noqa: E402
    CUSTODY_EVICTION_PHRASE,
    DEGENERATE_EVICTION_PHRASE,
    REVOCATION_LIST_MAX_ENTRIES,
    LocalAuthService,
    TokenRevocationList,
)
from fcc_test_platform.application.password_hasher import BcryptPasswordHasher  # noqa: E402
from fcc_test_contracts.common.login_throttle_policy import (  # noqa: E402
    fingerprint_digest,
    fold_failed_attempts,
    lock_expires_at,
)
from fcc_test_platform.domain.services.token_revocation_policy import (  # noqa: E402
    UNATTRIBUTED_CUSTODIAN,
    custodian_should_yield,
    custody_fair_share,
    steady_state_entries_per_session,
)

_SECRET = 'k' * LOCAL_JWT_MIN_SECRET_BYTES
_JWT = LocalJwtConfig(
    secret=_SECRET, issuer='https://fcc.internal/auth', audience='fcc-web',
)
_NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
_TEST_COST = 4
_PASSWORD = 'correcthorse'
def _source_of(module) -> Path:
    """이 모듈의 소스 파일 — **import 로 찾는다, 경로를 조립하지 않는다.**

    ⚠️ 첫 판은 ``_REPO_ROOT / 'src' / …`` 를 적었고, 그 순간 다섯 봉인이 **납품 상자
    안에서 red** 가 됐다(적대 평가 2R F5): 상자는 `src/` 라는 디렉터리를 싣지 않는다.
    이 저장소가 *"a data path is not an import"* 라고 이름 붙인 형태 그대로이고, 추출
    축의 ratchet 은 감소 전용이라 그것은 게이트를 깨뜨린다.

    모듈이 import 되는 곳이면 어디서든 자기 소스를 안다.
    """
    path = inspect.getsourcefile(module)
    assert path, f'{module.__name__} 의 소스를 찾을 수 없다'
    return Path(path)


_SERVICE_SOURCE = _source_of(_local_auth_service)
_POLICY_SOURCE = _source_of(_token_revocation_policy)


# ─────────────────────────────────────────────────────────────────────────────
# harness
# ─────────────────────────────────────────────────────────────────────────────

class _FakeStore:
    """``PostgresLocalUserStore`` 대역. 형제 ``test_credential_throttle`` 과 같은 형상."""

    def __init__(self, users=None) -> None:
        self.users = {u['subject']: dict(u) for u in (users or [])}
        self.permissions: dict = {}

    def find_by_email(self, email):
        row = self.users.get(str(email or '').strip().lower())
        return dict(row) if row else None

    def global_permissions(self, user_id):
        return tuple(self.permissions.get(user_id, ()))

    def record_successful_login(self, user_id, *, now):
        for row in self.users.values():
            if row['id'] == user_id:
                row['failed_login_attempts'] = 0
                row['locked_until'] = None

    def record_failed_login(self, user_id, *, now):
        for row in self.users.values():
            if row['id'] != user_id:
                continue
            attempts = fold_failed_attempts(
                row.get('failed_login_attempts'), row.get('last_failed_at'), now,
            )
            row['failed_login_attempts'] = attempts
            row['last_failed_at'] = now
            locked = lock_expires_at(attempts, now)
            if locked is not None:
                row['locked_until'] = locked
            return {'failed_login_attempts': attempts,
                    'locked_until': row.get('locked_until')}
        return {}

    def update_password(self, user_id, *, password_hash, now):
        for row in self.users.values():
            if row['id'] == user_id:
                row['password_hash'] = password_hash
                row['session_version'] = int(row.get('session_version') or 0) + 1
                row['force_password_change'] = False
                row['password_changed_at'] = now
                return row['session_version']
        raise AssertionError('no such user')


def _user(**overrides) -> dict:
    base = {
        'id': 'user-1', 'subject': 'tester@x.com', 'display_name': 'Tester',
        'email': 'tester@x.com', 'enabled': True, 'password_hash': '',
        'force_password_change': False, 'password_changed_at': _NOW,
        'session_version': 1, 'failed_login_attempts': 0, 'locked_until': None,
    }
    base.update(overrides)
    return base


def _store(*subjects, password=_PASSWORD):
    hasher = BcryptPasswordHasher(cost_rounds=_TEST_COST)
    digest = hasher.hash(password)
    return _FakeStore([
        _user(id=f'user-{i}', subject=s, email=s, password_hash=digest)
        for i, s in enumerate(subjects or ('tester@x.com',), start=1)
    ])


def _service(store, revocations):
    return LocalAuthService(
        store=store,
        hasher=BcryptPasswordHasher(cost_rounds=_TEST_COST),
        jwt_config=_JWT,
        clock=lambda: _NOW,
        revocation_list=revocations,
        epoch_clock=lambda: int(time.time()),
    )


def _custodian_of(subject: str) -> str:
    """봉인이 기대하는 보관자 키. 프로덕션과 **같은 함수**로 만든다."""
    return fingerprint_digest(_SECRET, subject)


def _evict_globally(entries, max_entries):
    """고치기 전 ``_evict_locked`` — 전역 가장-먼저-만료. 반사실 전용.

    ⚠️ 손으로 재작성한 것이 아니라 **그때 코드의 동작**이다: 만료분을 지우고, 초과분만큼
    만료 시각 오름차순으로 버린다.
    """
    live = dict(entries)
    overflow = len(live) - max_entries
    if overflow <= 0:
        return live
    doomed = sorted(live.items(), key=lambda item: item[1])[:overflow]
    for key, _exp in doomed:
        live.pop(key, None)
    return live


# ─────────────────────────────────────────────────────────────────────────────
# M-1 — 반사실: 고치기 전 규칙에서 공격이 실제로 성립한다
# ─────────────────────────────────────────────────────────────────────────────

class TestTheAttackReproducesUnderTheOldRule(unittest.TestCase):
    """⚠️ 이 클래스가 없으면 아래 전부가 **고쳐지지 않은 코드에서도** 통과한다."""

    def test_the_global_rule_discards_the_logout_and_keeps_the_rotations(self):
        max_entries = 50
        victim_logout_exp = 1_000.0
        entries = {'victim-logout': victim_logout_exp}
        # 회전은 매번 **새 7일짜리**를 넣는다 — 로그아웃 항목보다 항상 나중에 만료된다.
        for i in range(max_entries * 3):
            entries[f'atk-{i}'] = victim_logout_exp + 1_000.0 + i

        survivors = _evict_globally(entries, max_entries)

        self.assertNotIn(
            'victim-logout', survivors,
            '반사실이 성립하지 않으면 이 파일의 나머지는 아무것도 증명하지 않는다',
        )
        self.assertEqual(
            len(survivors), max_entries,
            '옛 규칙도 상한 자체는 지켰다 — 결함은 누가 희생되는가였다',
        )

    def test_the_victim_is_first_precisely_because_rotation_expires_later(self):
        """희생 순서가 **만료 시각의 함수**임을 보인다 — 우연이 아니라 기전이다."""
        max_entries = 2
        later = _evict_globally(
            {'victim': 10.0, 'a': 20.0, 'b': 30.0}, max_entries,
        )
        self.assertNotIn('victim', later)
        # 부호를 뒤집으면 피해자가 산다 — 즉 축출 규칙이 원인이라는 것의 대조군.
        earlier = _evict_globally(
            {'victim': 40.0, 'a': 20.0, 'b': 30.0}, max_entries,
        )
        self.assertIn('victim', earlier)


# ─────────────────────────────────────────────────────────────────────────────
# M-2 — 한 보관자의 삽입이 다른 보관자를 밀어낼 수 없다
# ─────────────────────────────────────────────────────────────────────────────

class TestOneCustodianCannotEvictAnother(unittest.TestCase):

    def _flooded(self, *, max_entries=50, rotations=None):
        rotations = max_entries * 10 if rotations is None else rotations
        revocations = TokenRevocationList(
            clock=lambda: 0.0, max_entries=max_entries,
        )
        revocations.revoke(
            'victim-logout', expires_at=1_000.0, custodian=_custodian_of('victim'),
        )
        for i in range(rotations):
            revocations.revoke(
                f'atk-{i}', expires_at=100_000.0 + i,
                custodian=_custodian_of('attacker'),
            )
        return revocations

    def test_the_victims_logout_entry_survives_the_flood(self):
        revocations = self._flooded()
        self.assertTrue(
            revocations.is_revoked('victim-logout'),
            '보관 분할이 성립하면 공격자의 회전은 자기 항목만 밀어낸다',
        )

    def test_the_ceiling_still_holds(self):
        """공격을 닫으면서 메모리 봉투를 잃지 않았다 — 둘 다여야 한다.

        ⚠️ 상한만 묻는 단언은 **빈 목록에서도 참**이다. 아래쪽도 함께 묶는다.
        """
        revocations = self._flooded(max_entries=50)
        self.assertLessEqual(len(revocations), 50)
        self.assertEqual(
            len(revocations), 50,
            '상한에 닿은 뒤에는 목록이 가득 차 있어야 한다 — 비어도 위 단언은 통과한다',
        )

    def test_the_attacker_stops_growing_and_pays_for_every_further_insertion(self):
        """⚠️ 이 테스트는 **두 번 이름을 고쳤고 두 번 다 이유가 있다.**

        첫 판(*"공격자가 자기 몫에 고정된다"*)의 단언은 어떤 구현에서도 참이었다
        (적대 평가 R1a F7): ``assertLessEqual(len - 1, max(share, 49))`` 는 우변이 항상
        49 이상이고 좌변은 상한 불변식이 이미 보장한다.

        두 번째 판은 보유량이 **공정몫**에 고정되는지 물었고 그것은 **거짓**이다 —
        축출은 상한에서만 돌므로 다른 보관자가 비워 둔 자리는 공격자가 차지한다(실측:
        50 중 49). 그것은 결함이 아니다. 이 설계가 지키는 문장은 *"공격자가 작아진다"*
        가 아니라 **"공격자가 남의 것으로 자라지 못한다"** 이고, 그것을 묻는다.
        """
        revocations = self._flooded(max_entries=50)
        attacker = _custodian_of('attacker')
        settled = revocations.held_by(attacker)
        self.assertGreater(settled, 0, '비공허성 — 공격자가 실제로 들고 있는지 먼저')

        for i in range(200):
            revocations.revoke(f'more-{i}', expires_at=200_000.0 + i,
                               custodian=attacker, churn=True)
        self.assertEqual(
            revocations.held_by(attacker), settled,
            '상한에 닿은 뒤에는 공격자의 보유량이 더 자라면 안 된다',
        )
        self.assertTrue(
            revocations.is_revoked('victim-logout'),
            '그 200회 동안 피해자 항목이 살아 있어야 한다',
        )
        self.assertEqual(revocations.tracked_custodians, 2)

    def test_bystander_activity_after_the_flood_does_not_release_the_victim(self):
        """⚠️ **이 봉인의 부재가 이 웨이브의 첫 판을 통과시켰다** (적대 평가 R1b HIGH-1).

        모든 M-2 봉인이 flood 를 하고 **거기서 멈췄다.** 첫 판의 결함은 정확히 그 다음
        줄에 있었다 — 희생자를 *삽입한 보관자*로만 골랐으므로, flood 가 목록을 상한에
        붙여 놓은 뒤 **무관한 제3자의 평범한 로그아웃 한 번**이 폴백을 타고 피해자
        항목을 버렸다. 공격자는 그 제3자일 필요조차 없다.

        그러므로 이 테스트는 flood **이후**를 묻는다.
        """
        revocations = self._flooded(max_entries=40)
        self.assertTrue(revocations.is_revoked('victim-logout'))
        for round_no in range(8):
            for who in range(3):
                revocations.revoke(
                    f'bystander-{round_no}-{who}', expires_at=2_000.0 + round_no,
                    custodian=_custodian_of(f'bystander-{who}'),
                )
            self.assertTrue(
                revocations.is_revoked('victim-logout'),
                f'무관한 제3자의 조작 {round_no} 회차가 피해자 항목을 버렸다',
            )

    def test_a_light_custodian_is_never_the_victim(self):
        """이 클래스가 지키는 문장 전부 — **몫 이내는 희생되지 않는다.**

        위 테스트가 특정 시나리오를 묻는다면 이것은 그 성질을 직접 묻는다: 무거운
        보관자가 압박을 만드는 동안, 가벼운 보관자들의 보유량은 **한 건도** 줄지 않는다.
        """
        ceiling, lights = 120, 5
        share = custody_fair_share(ceiling, lights + 1)
        revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=ceiling)
        light = [f'light-{i}' for i in range(lights)]
        for name in light:
            for j in range(3):
                revocations.revoke(f'{name}-{j}', expires_at=1_000.0 + j,
                                   custodian=_custodian_of(name))
        for i in range(2_000):
            revocations.revoke(f'heavy-{i}', expires_at=100_000.0 + i,
                               custodian=_custodian_of('heavy'), churn=True)
            if i % 500 == 0:
                for name in light:
                    revocations.revoke(
                        f'{name}-extra-{i}', expires_at=1_500.0 + i,
                        custodian=_custodian_of(name),
                    )
        # ⚠️ 전제를 **먼저 단언한다** — light 가 자기 몫을 넘었다면 그들이 자기 항목을
        # 내놓는 것은 정확한 동작이고, 그 상태에서의 아래 단언은 결함을 묻지 않는다.
        # (첫 판이 light 를 3+8=11 로 밀어 몫 10 을 넘겼다.)
        #
        # ⚠️ 그리고 **그 전제 단언 자체가 공허했다**(적대 평가 2R F6): `4 <= share` 는
        # 상수 비교라 어떤 구현에서도 참이고, 반복 변수 ``name`` 을 본문이 쓰지도 않아
        # 같은 식을 다섯 번 계산했다. 전제는 **실제 보유량**을 읽어야 한다.
        for name in light:
            held = revocations.held_by(_custodian_of(name))
            self.assertGreater(held, 0, f'{name} 이 아무것도 안 들고 있다')
            self.assertLessEqual(
                held, share,
                f'{name} 이 자기 몫({share})을 넘었다 — 그러면 아래 단언은 '
                f'결함이 아니라 정확한 동작을 반증하려 든다 (보유 {held})',
            )
        for name in light:
            for j in range(3):
                self.assertTrue(
                    revocations.is_revoked(f'{name}-{j}'),
                    f'{name} 은 자기 몫 이내인데 항목을 잃었다',
                )

    def test_an_ordinary_fill_at_deployment_scale_keeps_a_logout(self):
        """⚠️ 적대 평가 R1b HIGH-2 — **공격자 없이** 로그아웃이 사라지던 경로.

        ADR 이 파생한 용량 사실(세션당 672 항목, 상한 ÷ 672 ≈ 29 세션)은 그 자체로
        결함이 아니다. 결함은 그 지점에서 **누가** 희생되는가였다. 서른 개의 평범한
        세션이 목록을 채우는 동안, 한 시험원의 로그아웃 항목은 살아 있어야 한다.
        """
        revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=2_000)
        revocations.revoke('alice-logout', expires_at=1_000.0,
                           custodian=_custodian_of('alice'))
        for session in range(31):
            for i in range(672):
                revocations.revoke(
                    f's{session}-{i}', expires_at=50_000.0 + i,
                    custodian=_custodian_of(f'tester-{session}'),
                )
        self.assertGreater(len(revocations), 0)
        self.assertTrue(
            revocations.is_revoked('alice-logout'),
            '평범한 배포 규모의 채움이 로그아웃을 되돌렸다',
        )

    def test_a_deliberate_revocation_outlives_the_custodians_own_churn(self):
        """⚠️ **어느 끝을 버리는가**가 로그아웃의 생사를 정한다 (적대 평가 R1a F3).

        첫 판은 보관자의 *가장 오래된* 항목을 버렸고, 그것은 구조상 그 사람의
        **로그아웃 항목**이다(로그아웃이 한 번 들어가고 회전이 그 뒤에 쌓인다). 즉
        수정이라 부른 분기가 로그아웃을 표적으로 삼았다 — 다른 기기에서 계속 일하는
        것만으로 자기 로그아웃이 풀렸다. 평가자가 반대 정책을 심고 **46/46 green** 을
        보였다: 그 줄을 묻는 봉인이 하나도 없었다.

        ⚠️ 그리고 단순히 *가장 새로운* 것으로 뒤집는 것도 답이 아니다 — 그러면 방금
        넣은 로그아웃 항목이 즉시 사라져 가장 활동적인 사용자의 로그아웃이 no-op 이
        된다. 그래서 판정은 끝이 아니라 **가치**다.
        """
        revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=20)
        revocations.revoke('deliberate', expires_at=1_000.0,
                           custodian=_custodian_of('busy'))
        for i in range(500):
            revocations.revoke(f'churn-{i}', expires_at=100_000.0 + i,
                               custodian=_custodian_of('busy'), churn=True)
        self.assertTrue(
            revocations.is_revoked('deliberate'),
            '자기 회전이 자기 로그아웃을 버렸다 — 이 웨이브가 고친 결함의 자기 버전',
        )

    def test_a_deliberate_revocation_inserted_last_is_not_forfeited_first(self):
        """가장 활동적인 사용자가 **지금** 로그아웃해도 그것은 남아야 한다."""
        revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=20)
        for i in range(500):
            revocations.revoke(f'churn-{i}', expires_at=100_000.0 + i,
                               custodian=_custodian_of('busy'), churn=True)
        revocations.revoke('logout-now', expires_at=1_000.0,
                           custodian=_custodian_of('busy'))
        for i in range(500, 700):
            revocations.revoke(f'churn-{i}', expires_at=100_000.0 + i,
                               custodian=_custodian_of('busy'), churn=True)
        self.assertTrue(
            revocations.is_revoked('logout-now'),
            '방금 넣은 로그아웃 항목이 즉시 희생되면 로그아웃이 no-op 이다',
        )

    def test_churn_is_forfeited_oldest_first(self):
        """남은 보호 기간이 가장 짧은 것부터 — 버릴 수밖에 없다면 손실이 가장 작은 것."""
        revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=5)
        for i in range(5):
            revocations.revoke(f'c-{i}', expires_at=100_000.0 + i,
                               custodian=_custodian_of('busy'), churn=True)
        revocations.revoke('c-new', expires_at=100_100.0,
                           custodian=_custodian_of('busy'), churn=True)
        self.assertFalse(revocations.is_revoked('c-0'))
        self.assertTrue(revocations.is_revoked('c-4'))
        self.assertTrue(revocations.is_revoked('c-new'))

    def test_rotation_is_the_only_thing_marked_as_churn(self):
        """⚠️ 표시를 붙이는 자리가 하나여야 한다 — 로그아웃이 churn 이 되면 위 셋이 뒤집힌다."""
        revocations = _RecordingRevocationList()
        store = _store('tester@x.com')
        service = _service(store, revocations)
        pair = service.login(email='tester@x.com', password=_PASSWORD)

        revocations.churn_flags.clear()
        service.refresh(refresh_token=pair['refresh_token'])
        self.assertEqual(revocations.churn_flags, [True])

        revocations.churn_flags.clear()
        fresh = service.login(email='tester@x.com', password=_PASSWORD)
        service.logout(
            None,
            access_token=fresh['access_token'],
            refresh_token=fresh['refresh_token'],
        )
        self.assertEqual(revocations.churn_flags, [False, False])

    def test_a_third_custodian_is_untouched_too(self):
        """둘만의 성질이 아니라는 것 — 보관자가 셋이어도 같다."""
        revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=30)
        revocations.revoke('a-logout', expires_at=1_000.0,
                           custodian=_custodian_of('alice'))
        revocations.revoke('b-logout', expires_at=1_100.0,
                           custodian=_custodian_of('bob'))
        for i in range(600):
            revocations.revoke(f'atk-{i}', expires_at=100_000.0 + i,
                               custodian=_custodian_of('attacker'))
        self.assertTrue(revocations.is_revoked('a-logout'))
        self.assertTrue(revocations.is_revoked('b-logout'))

    def test_the_same_jti_twice_does_not_inflate_the_custodian(self):
        """멱등 삽입이 몫을 부풀리지 않는다 — 회계가 세 구조에서 함께 움직인다."""
        revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=10)
        for _ in range(50):
            revocations.revoke('same', expires_at=1_000.0,
                               custodian=_custodian_of('alice'))
        self.assertEqual(len(revocations), 1)
        self.assertEqual(revocations.tracked_custodians, 1)

    def test_moving_a_jti_between_custodians_leaves_no_ghost(self):
        """같은 ``jti`` 가 다른 보관자로 재삽입되면 옛 보관자에 잔여가 없어야 한다."""
        revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=10)
        revocations.revoke('x', expires_at=1_000.0, custodian=_custodian_of('a'))
        revocations.revoke('x', expires_at=1_000.0, custodian=_custodian_of('b'))
        self.assertEqual(revocations.tracked_custodians, 1)
        self.assertEqual(len(revocations), 1)

    def test_expiry_releases_the_custodian_slot(self):
        clock = {'t': 0.0}
        revocations = TokenRevocationList(
            clock=lambda: clock['t'], max_entries=10, prune_interval_seconds=0.0,
        )
        revocations.revoke('x', expires_at=5.0, custodian=_custodian_of('a'))
        self.assertEqual(revocations.tracked_custodians, 1)
        clock['t'] = 10.0
        self.assertFalse(revocations.is_revoked('x'))
        self.assertEqual(
            revocations.tracked_custodians, 0,
            '만료 항목이 보관자를 붙잡고 있으면 공정몫 분모가 영원히 부풀어 있다',
        )

    def test_the_periodic_prune_also_releases_the_custodian_slot(self):
        """⚠️ 만료 항목을 지우는 경로는 **둘**이다 — 조회(``is_revoked``)와 주기 정리.

        위 테스트는 조회 경로만 지난다. 변이 배터리가 그 공백을 찾았다: 주기 정리가
        보관자 장부를 놓아주지 않아도 위 단언은 통과한다. 두 경로가 같은 사실을
        유지해야 하므로 각각 묻는다 — 정리 경로가 새면 분모가 조용히 부풀고, 부푼
        분모는 공정몫을 1 로 밀어 **모두를 영구 초과**로 만든다.
        """
        clock = {'t': 0.0}
        revocations = TokenRevocationList(
            clock=lambda: clock['t'], max_entries=100,
            prune_interval_seconds=10.0,
        )
        revocations.revoke('gone', expires_at=5.0, custodian=_custodian_of('a'))
        self.assertEqual(revocations.tracked_custodians, 1)

        # 창을 넘겨 다음 삽입이 정리를 촉발하게 한다. 새 항목은 **다른** 보관자다.
        clock['t'] = 100.0
        revocations.revoke('live', expires_at=1e9, custodian=_custodian_of('b'))
        self.assertEqual(
            revocations.tracked_custodians, 1,
            '주기 정리가 만료 보관자를 놓아주지 않으면 분모가 영원히 부푼다',
        )
        self.assertTrue(revocations.is_revoked('live'))


# ─────────────────────────────────────────────────────────────────────────────
# M-2c — 회전은 **원자적**이다 (적대 평가 2R F1)
# ─────────────────────────────────────────────────────────────────────────────

class TestRotationIsAtomic(unittest.TestCase):
    """⚠️ **이 축이 보관 분할보다 앞에 있다.**

    첫 판의 ``refresh`` 는 ``is_revoked`` 로 묻고, 사용자 행을 읽는 **DB 왕복**을 하고,
    그 뒤에 ``revoke`` 했다. 그 왕복이 GIL 을 놓으므로 같은 캡처 토큰을 든 동시 요청이
    모두 확인을 통과했다 — 적대 평가 2R 이 실 라우트로 **12개 중 4개가 200**, 그리고
    피해자가 로그아웃한 **뒤에도** 넷 다 계속 세션을 찍어내는 것을 실측했다.

    즉 공격자는 축출이 **필요조차 없었다**. 이 파일의 나머지가 지키는 문장(*"몫 이내는
    희생되지 않는다"*)은 그 앞의 전제(*"회전은 단일 사용"*)가 참일 때만 의미가 있다.
    """

    def test_only_one_of_many_concurrent_claims_is_granted(self):
        revocations = TokenRevocationList()
        granted: list = []
        barrier = threading.Barrier(24)

        def racer():
            barrier.wait()
            ok = revocations.claim(
                'contested', expires_at=1e12,
                custodian=_custodian_of('victim'), churn=True,
            )
            # 이 sleep 이 결함의 창을 모형한다 — 첫 판은 여기서 DB 를 읽었다.
            time.sleep(0.004)
            if ok:
                granted.append(1)

        threads = [threading.Thread(target=racer) for _ in range(24)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(
            len(granted), 1,
            '동시 요청 여럿이 같은 토큰을 쓰면 하나의 캡처가 여러 갈래가 된다',
        )

    def test_the_refresh_path_claims_before_it_reads_the_user(self):
        """⚠️ **순서가 전부다.** 읽기 뒤에 claim 하면 창이 그대로 남는다.

        저장소 조회를 계측해, 그 조회가 일어나는 시점에 토큰이 **이미** 폐기 목록에
        들어가 있는지 묻는다 — 소스 검사가 아니라 실행 순서다.
        """
        revocations = TokenRevocationList()
        store = _store('tester@x.com')
        service = _service(store, revocations)
        pair = service.login(email='tester@x.com', password=_PASSWORD)
        jti = verify_token(
            _JWT, pair['refresh_token'], expected_type=TOKEN_TYPE_REFRESH,
        )['jti']

        seen: list = []
        original = store.find_by_email

        def watching(email):
            seen.append(revocations.is_revoked(jti))
            return original(email)

        store.find_by_email = watching
        service.refresh(refresh_token=pair['refresh_token'])
        self.assertTrue(seen, '비공허성 — 저장소 조회가 실제로 일어났다')
        self.assertTrue(
            all(seen),
            '사용자를 읽는 동안 토큰이 아직 열려 있으면 그 창이 곧 결함이다',
        )

    def test_a_rejected_attempt_gives_the_token_back(self):
        """⚠️ 잠금은 15분이고 소실은 영구다 — 그 둘을 맞바꿀 수 없다."""
        revocations = TokenRevocationList()
        store = _store('tester@x.com')
        service = _service(store, revocations)
        pair = service.login(email='tester@x.com', password=_PASSWORD)
        jti = verify_token(
            _JWT, pair['refresh_token'], expected_type=TOKEN_TYPE_REFRESH,
        )['jti']

        store.users['tester@x.com']['locked_until'] = _NOW + timedelta(minutes=15)
        with self.assertRaises(Exception):
            service.refresh(refresh_token=pair['refresh_token'])
        self.assertFalse(
            revocations.is_revoked(jti),
            '거부된 시도가 토큰을 소모하면 잠긴 사용자의 세션이 영구히 끝난다',
        )

        # 잠금이 풀리면 같은 토큰이 다시 통한다 — 그것이 이 되돌림의 요지다.
        store.users['tester@x.com']['locked_until'] = None
        self.assertIn(
            'access_token', service.refresh(refresh_token=pair['refresh_token']),
        )

    def test_a_spent_token_is_refused_the_second_time(self):
        revocations = TokenRevocationList()
        service = _service(_store('tester@x.com'), revocations)
        pair = service.login(email='tester@x.com', password=_PASSWORD)
        service.refresh(refresh_token=pair['refresh_token'])
        with self.assertRaises(Exception):
            service.refresh(refresh_token=pair['refresh_token'])

    def test_claim_refuses_a_token_with_no_identifier(self):
        """식별자가 없으면 재사용을 판정할 수 없다 — 통과는 무제한 재사용이다."""
        revocations = TokenRevocationList()
        for empty in (None, '', '   '):
            self.assertFalse(
                revocations.claim(
                    empty, expires_at=1e9, custodian=_custodian_of('a'),
                ),
            )


# ─────────────────────────────────────────────────────────────────────────────
# M-2d — 가치는 무게보다 앞선다 (적대 평가 2R F2)
# ─────────────────────────────────────────────────────────────────────────────

class TestValueOrderingOutranksWeightOrdering(unittest.TestCase):
    """⚠️ 첫 판은 희생자를 **먼저 무게로** 골랐고, 그것이 순서를 뒤집었다.

    ``_kept`` 만 가진 무거운 보관자는 정확히 *여러 기기에서 로그아웃한 사용자*다. 적대
    평가 2R 실측: 그 사람의 로그아웃 40건 중 **39건이 사라지는 동안** 가벼운 보관자의
    회전 churn 은 살아남았다 — 그 39건은 각각 다시 세션을 찍어내는 리프레시 토큰이고,
    이 파일이 지키려던 바로 그것이다.
    """

    @staticmethod
    def _crowded():
        revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=100)
        for i in range(40):
            revocations.revoke(f'victim-logout-{i}', expires_at=1e9,
                               custodian=_custodian_of('victim'))
        for i in range(5):
            revocations.revoke(f'mover-churn-{i}', expires_at=1e9,
                               custodian=_custodian_of('mover'), churn=True)
        for u in range(48):
            revocations.revoke(f'other-{u}', expires_at=1e9,
                               custodian=_custodian_of(f'user-{u}'), churn=True)
        for i in range(300):
            revocations.revoke(f'flood-{i}', expires_at=1e9 + i,
                               custodian=_custodian_of(f'user-{i % 48}'),
                               churn=True)
        return revocations

    def test_a_heavy_kept_only_custodian_keeps_every_logout_entry(self):
        revocations = self._crowded()
        survived = sum(
            revocations.is_revoked(f'victim-logout-{i}') for i in range(40)
        )
        self.assertEqual(
            survived, 40,
            '무게가 가치를 이기면 로그아웃한 사용자가 자기 로그아웃을 잃는다',
        )

    def test_the_price_is_paid_in_churn_somewhere(self):
        """비-공허성 — 압박이 실재했고 누군가는 냈다. 다만 churn 으로 냈다."""
        revocations = self._crowded()
        self.assertLessEqual(len(revocations), 100)
        churn_left = sum(
            revocations.is_revoked(f'mover-churn-{i}') for i in range(5)
        )
        self.assertLess(
            churn_left, 5, '아무것도 축출되지 않았다면 이 테스트는 공허하다',
        )

    def test_deliberate_entries_are_untouched_while_any_churn_remains(self):
        """전역 성질로 직접 묻는다 — 특정 시나리오가 아니라."""
        revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=30)
        for i in range(10):
            revocations.revoke(f'kept-{i}', expires_at=1e9,
                               custodian=_custodian_of('sombody'))
        for i in range(400):
            revocations.revoke(f'churn-{i}', expires_at=1e9 + i,
                               custodian=_custodian_of(f'other-{i % 3}'),
                               churn=True)
        for i in range(10):
            self.assertTrue(
                revocations.is_revoked(f'kept-{i}'),
                'churn 이 남아 있는 한 의도적 폐기는 건드리지 않는다',
            )


# ─────────────────────────────────────────────────────────────────────────────
# M-3 — 실 HTTP 라우트 end-to-end, 그리고 401 의 **사유**
# ─────────────────────────────────────────────────────────────────────────────

class TestTheReplayStaysDeadThroughTheRealRoutes(unittest.TestCase):
    """``create_platform_app`` — ASGI 진입점이 실제로 부르는 그 함수."""

    def setUp(self) -> None:
        try:
            from fastapi.testclient import TestClient  # noqa: F401
        except ImportError:
            self.skipTest('fastapi not installed in this shard')

    def _client(self, revocations, store):
        from fastapi.testclient import TestClient
        from fcc_test_contracts.common.principal_resolver import LocalJwtPrincipalResolver
        from fcc_test_platform.application.central_read_adapter import (
            PostgresCentralReadAdapter,
        )
        from fcc_test_platform.application.central_read_service import CentralReadService
        from fcc_test_platform.api.platform_routes import (
            PlatformApiAdapter,
            create_platform_app,
        )

        adapter = PlatformApiAdapter(
            CentralReadService(PostgresCentralReadAdapter(lambda: None)),
            access_policy=ApiAccessPolicy(PLATFORM_API_OPERATIONS),
            local_auth_service=_service(store, revocations),
        )
        return TestClient(
            create_platform_app(
                adapter,
                principal_resolver=LocalJwtPrincipalResolver(
                    _JWT, revocation_list=revocations,
                ),
                credential_secret=_SECRET,
            ),
            raise_server_exceptions=False,
        )

    def test_a_rotation_flood_does_not_resurrect_a_logged_out_refresh_token(self):
        max_entries = 40
        revocations = TokenRevocationList(max_entries=max_entries)
        store = _store('victim@x.com', 'attacker@x.com')
        client = self._client(revocations, store)

        victim = client.post('/platform/auth/login', json={
            'email': 'victim@x.com', 'password': _PASSWORD}).json()
        attacker = client.post('/platform/auth/login', json={
            'email': 'attacker@x.com', 'password': _PASSWORD}).json()

        captured = victim['refresh_token']
        victim_jti = verify_token(
            _JWT, captured, expected_type=TOKEN_TYPE_REFRESH,
        )['jti']

        client.post('/platform/auth/logout', json={
            'refresh_token': captured},
            headers={'Authorization': f'Bearer {victim["access_token"]}'})
        self.assertEqual(
            client.post('/platform/auth/refresh',
                        json={'refresh_token': captured}).status_code, 401,
            '로그아웃 직후에는 replay 가 거부돼야 한다 — 출발점이 성립하는지 먼저',
        )
        self.assertTrue(revocations.is_revoked(victim_jti))

        # 공격자가 **자기** 토큰을 상한의 몇 배로 회전시킨다.
        token = attacker['refresh_token']
        for _ in range(max_entries * 4):
            rotated = client.post(
                '/platform/auth/refresh', json={'refresh_token': token},
            )
            self.assertEqual(rotated.status_code, 200)
            token = rotated.json()['refresh_token']

        # ⚠️ 판정은 상태코드가 아니라 **목록 내용**이다.
        self.assertTrue(
            revocations.is_revoked(victim_jti),
            '피해자의 jti 가 여전히 폐기 목록에 있어야 한다',
        )
        replay = client.post(
            '/platform/auth/refresh', json={'refresh_token': captured},
        )
        self.assertEqual(replay.status_code, 401)

        # ⚠️ 그리고 그 401 이 **폐기 목록 때문**임을 다른 사유를 배제해 보인다:
        claims = verify_token(_JWT, captured, expected_type=TOKEN_TYPE_REFRESH)
        row = store.find_by_email('victim@x.com')
        self.assertEqual(
            int(claims['sv']), int(row['session_version']),
            'session_version 이 갈라져 있으면 401 은 이 봉인이 묻는 사유가 아니다',
        )
        self.assertIsNone(row['locked_until'], '잠금도 401 을 낸다 — 배제한다')
        self.assertGreater(
            float(claims['exp']), time.time(), '만료도 401 을 낸다 — 배제한다',
        )

    def test_the_attackers_own_rotation_keeps_working(self):
        """공격을 닫으면서 정상 회전을 깨지 않았다 — 거부는 0건이어야 한다."""
        revocations = TokenRevocationList(max_entries=20)
        client = self._client(revocations, _store('tester@x.com'))
        pair = client.post('/platform/auth/login', json={
            'email': 'tester@x.com', 'password': _PASSWORD}).json()
        token = pair['refresh_token']
        for _ in range(100):
            got = client.post(
                '/platform/auth/refresh', json={'refresh_token': token},
            )
            self.assertEqual(got.status_code, 200)
            token = got.json()['refresh_token']


# ─────────────────────────────────────────────────────────────────────────────
# M-4 — 보관자는 **검증된** sub 에서만 온다 (도착값 기록)
# ─────────────────────────────────────────────────────────────────────────────

class _RecordingRevocationList(TokenRevocationList):
    """도착한 ``custodian`` 값을 기록한다.

    ⚠️ **키워드의 존재가 아니라 도착한 값을 본다.** ``custodian=`` 이 소스에 있다는
    AST 단언은 ``custodian=''`` 변이에서 살아남는다.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.seen: list = []
        self.churn_flags: list = []

    def revoke(self, token_id, *, expires_at, custodian, churn=False):
        self.seen.append(custodian)
        self.churn_flags.append(churn)
        super().revoke(
            token_id, expires_at=expires_at, custodian=custodian, churn=churn,
        )

    def claim(self, token_id, *, expires_at, custodian, churn=False):
        """⚠️ 회전은 ``revoke`` 가 아니라 **원자적 ``claim``** 으로 삽입한다.

        기록 대역이 그 경로를 보지 못하면 *"회전이 검증된 sub 에 귀속된다"* 는 단언이
        **조용히 공허해진다** — 실제로 이 파일이 그 상태였고, ``claim`` 도입 직후
        네 테스트가 red 로 그것을 알렸다.
        """
        granted = super().claim(
            token_id, expires_at=expires_at, custodian=custodian, churn=churn,
        )
        if granted:
            self.seen.append(custodian)
            self.churn_flags.append(churn)
        return granted


class TestTheCustodianComesFromTheVerifiedSubject(unittest.TestCase):

    def test_rotation_attributes_the_entry_to_the_verified_subject(self):
        revocations = _RecordingRevocationList()
        store = _store('tester@x.com')
        service = _service(store, revocations)
        pair = service.login(email='tester@x.com', password=_PASSWORD)
        revocations.seen.clear()
        service.refresh(refresh_token=pair['refresh_token'])
        self.assertEqual(revocations.seen, [_custodian_of('tester@x.com')])

    def test_logout_attributes_both_tokens_to_the_verified_subject(self):
        revocations = _RecordingRevocationList()
        store = _store('tester@x.com')
        service = _service(store, revocations)
        pair = service.login(email='tester@x.com', password=_PASSWORD)
        revocations.seen.clear()
        service.logout(
            None,
            access_token=pair['access_token'],
            refresh_token=pair['refresh_token'],
        )
        self.assertEqual(
            revocations.seen,
            [_custodian_of('tester@x.com'), _custodian_of('tester@x.com')],
        )

    def test_change_password_attributes_the_entry_to_the_verified_subject(self):
        revocations = _RecordingRevocationList()
        store = _store('tester@x.com')
        service = _service(store, revocations)
        pair = service.login(email='tester@x.com', password=_PASSWORD)
        revocations.seen.clear()

        class _P:
            subject = 'tester@x.com'
            issuer = _JWT.issuer

        service.change_password(
            _P(), current_password=_PASSWORD, new_password='Anotherpass9!',
            access_token=pair['access_token'],
        )
        self.assertEqual(revocations.seen, [_custodian_of('tester@x.com')])

    def test_the_three_insertion_sites_are_the_only_ones(self):
        """``_revoke`` 가 유일 통로임을 소스에서 확인한다 — 새 경로가 생기면 red."""
        tree = ast.parse(_SERVICE_SOURCE.read_text(encoding='utf-8'))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'revoke'
        ]
        self.assertEqual(
            len(calls), 1,
            'revoke 호출이 둘 이상이면 보관자 파생이 한 곳에 있지 않다',
        )
        self.assertTrue(
            any(kw.arg == 'custodian' for kw in calls[0].keywords),
        )

    def test_a_forged_body_cannot_choose_another_custodian(self):
        """공격자는 자기 서명 토큰의 sub 아래로만 삽입할 수 있다."""
        revocations = _RecordingRevocationList()
        store = _store('victim@x.com', 'attacker@x.com')
        service = _service(store, revocations)
        attacker = service.login(email='attacker@x.com', password=_PASSWORD)
        revocations.seen.clear()
        service.refresh(refresh_token=attacker['refresh_token'])
        self.assertEqual(revocations.seen, [_custodian_of('attacker@x.com')])
        self.assertNotIn(_custodian_of('victim@x.com'), revocations.seen)

    def test_the_logout_body_cannot_insert_under_another_subject(self):
        """⚠️ 적대 평가 R1a F8 — M-4 의 주장이 실제보다 넓었다.

        ``logout`` 은 ``refresh_token`` 을 **request body** 에서 받는다. 착지 시점에는
        그 토큰의 ``sub`` 를 인증된 신원과 대조하지 않았으므로, 남의 유효 리프레시
        토큰을 body 에 실으면 **그 사람의 보관자 아래로 삽입**할 수 있었다 — 그리고
        그 사람을 강제 로그아웃시킬 수 있었다. 보관 귀속과 인증된 신원이 갈라지는
        지점이고, M-4 의 전제가 바로 그 둘이 같다는 것이다.
        """
        revocations = _RecordingRevocationList()
        store = _store('victim@x.com', 'attacker@x.com')
        service = _service(store, revocations)
        victim = service.login(email='victim@x.com', password=_PASSWORD)
        attacker = service.login(email='attacker@x.com', password=_PASSWORD)

        class _AttackerPrincipal:
            subject = 'attacker@x.com'
            issuer = _JWT.issuer

        revocations.seen.clear()
        service.logout(
            _AttackerPrincipal(),
            access_token=attacker['access_token'],
            refresh_token=victim['refresh_token'],
        )
        self.assertNotIn(
            _custodian_of('victim@x.com'), revocations.seen,
            '공격자가 피해자 보관자 아래로 삽입할 수 있으면 분할의 전제가 깨진다',
        )
        self.assertEqual(revocations.seen, [_custodian_of('attacker@x.com')])
        victim_jti = verify_token(
            _JWT, victim['refresh_token'], expected_type=TOKEN_TYPE_REFRESH,
        )['jti']
        self.assertFalse(
            revocations.is_revoked(victim_jti),
            '남의 토큰으로 그 사람을 강제 로그아웃시킬 수 없어야 한다',
        )
        # 비공허성 — 피해자 세션이 실제로 살아 있다.
        self.assertIsNotNone(service.refresh(refresh_token=victim['refresh_token']))

    def test_an_unverifiable_token_inserts_nothing_at_all(self):
        revocations = _RecordingRevocationList()
        service = _service(_store('tester@x.com'), revocations)
        service.logout(None, access_token='not-a-jwt', refresh_token='also-not')
        self.assertEqual(revocations.seen, [])


# ─────────────────────────────────────────────────────────────────────────────
# 알려진 한계 — 갈라진 회전 체인은 로그아웃으로 끝나지 않는다 (적대 평가 R1a F6)
# ─────────────────────────────────────────────────────────────────────────────

class TestLogoutCannotEndAForkedRotationChain(unittest.TestCase):
    """⚠️ **이것은 봉인이 아니라 오늘의 한계를 실행으로 적은 것이다.**

    로그아웃은 *클라이언트가 제시한* ``jti`` 를 폐기하고 ``session_version`` 은 일부러
    올리지 않는다(형제 기기 보호). 공격자가 캡처한 리프레시 토큰을 **로그아웃 전에 한
    번 써서 체인을 갈라 두면**, 그 갈래의 ``jti`` 는 폐기 목록에 들어간 적이 없으므로
    로그아웃이 그것을 끝내지 못한다 — 상한도 축출도 관여하지 않는다. 회전마다 갱신되어
    사실상 영구적이다.

    ⚠️ **이 웨이브는 그것을 고치지 않았고, 고쳤다고 주장하지도 않는다.** 정공은
    리프레시 토큰 replay detection(OAuth 2.0 Security BCP §4.14.2)이고 토큰 claim +
    서버 상태가 필요한 별개 축이다. ⚠️ **순진한 판(폐기된 리프레시 제시 = 전 세션
    무효화)은 오탐이 비싸다** — 네트워크 재시도 한 번이나 로그아웃과 경합한 탭 하나가
    그 사람의 모든 기기를 끊는다. 유예 창이나 family 추적이 함께 필요하다.

    ⚠️ **그날이 오면 이 테스트가 red 로 알려준다.** 산문으로만 적으면 다음 세션이
    *"로그아웃이 세션을 끝낸다"* 를 조건 없이 믿는다.
    """

    def test_todays_logout_does_not_kill_a_chain_forked_before_it(self):
        revocations = TokenRevocationList()
        store = _store('victim@x.com')
        service = _service(store, revocations)
        victim = service.login(email='victim@x.com', password=_PASSWORD)

        # 공격자가 캡처한 토큰으로 체인을 한 번 갈라 둔다.
        forked = service.refresh(refresh_token=victim['refresh_token'])

        # 피해자가 자기 클라이언트가 들고 있는 토큰으로 로그아웃한다.
        class _P:
            subject = 'victim@x.com'
            issuer = _JWT.issuer

        service.logout(
            _P(),
            access_token=victim['access_token'],
            refresh_token=victim['refresh_token'],
        )

        self.assertGreater(
            len(revocations), 0,
            '비공허성 — 목록에 아무것도 없으면 이 시나리오는 성립하지 않는다',
        )
        self.assertLess(
            len(revocations), 10,
            '상한도 축출도 이 시나리오에 관여하지 않는다 (그래서 갈래가 산다)',
        )
        still_works = service.refresh(refresh_token=forked['refresh_token'])
        self.assertIn(
            'access_token', still_works,
            '⚠️ 이 단언이 실패하면 replay detection 축이 착지한 것이다 — '
            '장부 항목을 상환으로 옮기고 이 클래스를 봉인으로 바꿔라',
        )


# ─────────────────────────────────────────────────────────────────────────────
# M-4c — 비밀번호 변경 문의 잠금은 **조용하지 않다** (적대 평가 2R F2)
# ─────────────────────────────────────────────────────────────────────────────

class TestTheChangePasswordLockoutIsObservable(unittest.TestCase):
    """⚠️ 이 표면의 잠금은 **한 줄도 남기지 않았다.**

    ``change_password`` 가 ``record_failed_login`` 의 반환을 버려, 로그인 문이 내는
    잠금 경보가 여기서는 한 번도 뜨지 않았다. 그리고 스프레이 탐지기는 서로 다른 계정
    **다섯**을 요구하므로 단일 계정 공격을 구조적으로 볼 수 없다 — 즉 관측이 **0** 이었다.

    실해: 훔친 액세스 토큰을 쥔 공격자가 15분마다 5회 틀리는 것만으로 피해자를 영구히
    잠글 수 있고, 잠긴 동안에는 비밀번호 변경도 로그인도 불가능하며 ``session_version``
    을 올리는 유일한 코드가 그 비밀번호 변경이라 훔친 토큰은 계속 산다. 그 상태를 푸는
    HTTP 표면이 **하나도 없다**.

    ⚠️ 이 웨이브는 그 설계 판단(카운터 유지 — ADR-0021 D-8)을 뒤집지 않는다. **조용한
    것을 시끄럽게** 만들 뿐이고, 그 차이가 운영자가 개입할 수 있는지 없는지를 가른다.
    """

    def _lock_via_change_password(self):
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        log = logging.getLogger('test_automation.platform_api')
        log.addHandler(handler)
        try:
            revocations = TokenRevocationList()
            store = _store('victim@x.com')
            service = _service(store, revocations)

            class _P:
                subject = 'victim@x.com'
                issuer = _JWT.issuer

            for _ in range(6):
                try:
                    service.change_password(
                        _P(), current_password='wrong-guess',
                        new_password='Somethingelse9!',
                    )
                except Exception:
                    pass
            return store, [l for l in buf.getvalue().splitlines() if l.strip()]
        finally:
            log.removeHandler(handler)

    def test_the_lockout_is_reported(self):
        store, lines = self._lock_via_change_password()
        self.assertIsNotNone(
            store.users['victim@x.com']['locked_until'],
            '비공허성 — 잠금이 실제로 걸렸는지 먼저',
        )
        self.assertTrue(lines, '잠금이 한 줄도 남기지 않으면 아무도 개입할 수 없다')
        self.assertTrue(
            any('change-password door' in line for line in lines),
            '어느 문에서 잠겼는지 말하지 않으면 로그인 문과 구분할 수 없다',
        )

    def test_the_report_names_what_the_operator_cannot_do(self):
        """⚠️ *"잠겼다"* 만으로는 이것이 15분짜리 불편인지 지속 가능한 DoS 인지 모른다."""
        _store_after, lines = self._lock_via_change_password()
        joined = '\n'.join(lines)
        self.assertIn('neither log in nor change its password', joined)
        self.assertIn('no operation lifts the lock early', joined)


# ─────────────────────────────────────────────────────────────────────────────
# M-5 — 평문 없음, 다이제스트 정의는 하나
# ─────────────────────────────────────────────────────────────────────────────

class TestTheCustodianKeyCarriesNoPlaintext(unittest.TestCase):

    def test_no_subject_or_email_appears_in_the_lists_state(self):
        revocations = TokenRevocationList()
        service = _service(_store('tester@x.com'), revocations)
        pair = service.login(email='tester@x.com', password=_PASSWORD)
        service.logout(
            None, access_token=pair['access_token'],
            refresh_token=pair['refresh_token'],
        )
        blob = repr(revocations.__dict__)
        self.assertNotIn('tester@x.com', blob)
        self.assertNotIn('tester', blob)
        self.assertIn(
            _custodian_of('tester@x.com'), blob,
            '비공허성 — 보관자가 실제로 기록됐는지 먼저 확인한다',
        )

    def test_the_service_reuses_the_shared_digest_and_defines_no_second_one(self):
        tree = ast.parse(_SERVICE_SOURCE.read_text(encoding='utf-8'))
        defined = {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertNotIn('fingerprint_digest', defined)
        self.assertFalse(
            {n for n in defined if 'digest' in n and n != '_digest'},
            '익명화 규칙이 둘이면 갈라지고, 갈라진 곳이 우회로다',
        )

    def test_the_revocation_list_never_receives_the_signing_secret(self):
        """목록은 다이제스트를 만들지 않는다 — 시크릿을 받지 않는 것이 그 봉인이다.

        ⚠️ 판정은 **호출**이지 문자열 등장이 아니다. 클래스 docstring 이 왜 그런지를
        설명하며 그 이름을 적는데, 산문을 위반으로 읽는 가드는 사람들이 설명을 지우게
        만든다(이 저장소가 챔버 어휘 가드에서 이미 이름 붙인 형태).
        """
        import inspect
        params = set(inspect.signature(TokenRevocationList.__init__).parameters)
        self.assertNotIn('secret', params)

        tree = ast.parse(_SERVICE_SOURCE.read_text(encoding='utf-8'))
        cls = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == 'TokenRevocationList'
        )
        called = {
            node.func.id for node in ast.walk(cls)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn('fingerprint_digest', called)
        # 비공허성 — 이 클래스가 정말 무언가를 부르고 있는지(파서가 빈 집합을 보고
        # 통과한 것이 아닌지) 확인한다.
        self.assertIn('str', called)


# ─────────────────────────────────────────────────────────────────────────────
# M-6 — 새 상수 0, 몫은 파생
# ─────────────────────────────────────────────────────────────────────────────

class TestTheFairShareIsDerivedAndNotAConstant(unittest.TestCase):

    def test_the_policy_module_declares_no_numeric_budget_constant(self):
        tree = ast.parse(_POLICY_SOURCE.read_text(encoding='utf-8'))
        numeric = []
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (isinstance(target, ast.Name) and target.id.isupper()
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, (int, float))
                        and not isinstance(node.value.value, bool)):
                    numeric.append(target.id)
        self.assertEqual(
            numeric, [],
            '예산 상수를 새로 만들면 그것은 측정되지 않은 봉투다 (ADR-0021 D-11)',
        )

    def test_the_share_follows_the_ceiling_and_the_population(self):
        self.assertEqual(custody_fair_share(100, 4), 25)
        self.assertEqual(custody_fair_share(100, 1), 100)
        self.assertEqual(custody_fair_share(100, 1000), 1)

    def test_the_share_is_floored_at_one(self):
        self.assertEqual(custody_fair_share(1, 10_000), 1)

    def test_everyone_within_share_cannot_exceed_the_ceiling(self):
        """§자기 정합성 — 산술이 그 주장을 실제로 지지하는지 확인한다."""
        for ceiling in (10, 50, 20_000):
            for population in range(1, 40):
                share = custody_fair_share(ceiling, population)
                if share == 1 and ceiling // population == 0:
                    continue  # 하한이 발화한 구간 — 주장 범위 밖
                self.assertLessEqual(population * share, ceiling)

    def test_yield_is_strictly_more_than_the_share(self):
        self.assertFalse(custodian_should_yield(held=25, fair_share=25))
        self.assertTrue(custodian_should_yield(held=26, fair_share=25))

    def test_the_policy_is_total(self):
        class _Hostile:
            def __int__(self):
                raise RuntimeError('no')

        for bad in (None, '', 'x', -1, 0, object(), _Hostile(), True, [1]):
            self.assertIsInstance(custody_fair_share(bad, 3), int)
            self.assertIsInstance(custody_fair_share(100, bad), int)
            self.assertIsInstance(
                custodian_should_yield(held=bad, fair_share=bad), bool,
            )
            self.assertIsInstance(steady_state_entries_per_session(bad, bad), int)

    def test_a_bool_is_not_a_count(self):
        """``True`` 는 ``int`` 다 — 플래그를 세면 가장 좁은 몫이 조용히 나온다.

        ⚠️ **판별하는 경우를 골라야 한다.** 첫 판은 ``custody_fair_share(100, True)``
        만 물었는데 그것은 bool 을 받아들여도 같은 100 을 답한다(``100 // 1``) — 값이
        우연히 같아 봉인이 분기를 보지 못했고, 변이 배터리가 그것을 SURVIVED 로
        드러냈다. 직전 웨이브가 ``6 × 10 = 60 = min(60, 120)`` 에서 겪은 형태 그대로다.
        아래는 답이 **갈라지는** 경우다: ``fair_share=True`` 를 세면 몫이 1 이 되어
        2 를 들고 있는 보관자가 초과로 판정된다.
        """
        self.assertFalse(
            custodian_should_yield(held=2, fair_share=True),
            '플래그는 몫이 아니다 — 세면 몫 1 이 되어 정상 보관자가 초과가 된다',
        )
        # 대조군: 진짜 1 이면 같은 입력이 True 를 답한다(위 단언이 공허하지 않다).
        self.assertTrue(custodian_should_yield(held=2, fair_share=1))
        self.assertEqual(custody_fair_share(100, True), 100)
        self.assertFalse(custodian_should_yield(held=True, fair_share=True))


# ─────────────────────────────────────────────────────────────────────────────
# S-2 — 용량 산술이 코드에 파생으로 남는다
# ─────────────────────────────────────────────────────────────────────────────

class TestTheCapacityArithmeticIsDerivedNotRemembered(unittest.TestCase):

    def test_a_live_session_settles_at_the_ttl_ratio(self):
        self.assertEqual(
            steady_state_entries_per_session(
                DEFAULT_LOCAL_JWT_REFRESH_TTL_SECONDS,
                DEFAULT_LOCAL_JWT_ACCESS_TTL_SECONDS,
            ),
            672,
        )

    def test_the_ceiling_is_reached_by_about_thirty_ordinary_sessions(self):
        """⚠️ 이것은 결함이 아니라 **알려진 용량 사실**이다 — 장부가 소유한다."""
        per_session = steady_state_entries_per_session(
            DEFAULT_LOCAL_JWT_REFRESH_TTL_SECONDS,
            DEFAULT_LOCAL_JWT_ACCESS_TTL_SECONDS,
        )
        self.assertEqual(REVOCATION_LIST_MAX_ENTRIES // per_session, 29)

    def test_it_answers_zero_rather_than_inventing_a_size(self):
        self.assertEqual(steady_state_entries_per_session(0, 900), 0)
        self.assertEqual(steady_state_entries_per_session(900, 0), 0)


# ─────────────────────────────────────────────────────────────────────────────
# M-7 — 총함수, 미상 보관자는 전용 버킷
# ─────────────────────────────────────────────────────────────────────────────

class TestTheListIsTotalOverItsCustodian(unittest.TestCase):

    def test_hostile_custodians_do_not_raise(self):
        class _Hostile:
            def __str__(self):
                raise RuntimeError('boom')

        revocations = TokenRevocationList()
        for bad in (None, '', '   ', 0, object(), _Hostile(), [1, 2]):
            revocations.revoke('j' + str(id(bad)), expires_at=1e9, custodian=bad)
        self.assertGreater(len(revocations), 0)

    def test_unattributable_entries_get_their_own_bucket(self):
        revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=10)
        revocations.revoke('a', expires_at=1e9, custodian=_custodian_of('alice'))
        revocations.revoke('u', expires_at=1e9, custodian=None)
        self.assertEqual(revocations.tracked_custodians, 2)

    def test_an_unattributable_flood_cannot_evict_a_real_custodian(self):
        revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=20)
        revocations.revoke('a', expires_at=1_000.0,
                           custodian=_custodian_of('alice'))
        for i in range(400):
            revocations.revoke(f'u-{i}', expires_at=100_000.0 + i, custodian=None)
        self.assertTrue(revocations.is_revoked('a'))

    def test_a_missing_sub_is_not_digested_into_a_lookalike_custodian(self):
        """빈 값을 다이제스트하면 미상이 **실재하는 것처럼 보이는** 보관자가 된다."""
        revocations = _RecordingRevocationList()
        service = _service(_store('tester@x.com'), revocations)
        self.assertEqual(
            service._custodian_for({'jti': 'x'}), UNATTRIBUTED_CUSTODIAN,
        )
        self.assertNotEqual(
            service._custodian_for({'jti': 'x'}), fingerprint_digest(_SECRET, ''),
        )
        for weird in ({'sub': ''}, {'sub': '  '}, {'sub': 42}, {'sub': None}):
            self.assertEqual(
                service._custodian_for(weird), UNATTRIBUTED_CUSTODIAN,
            )


# ─────────────────────────────────────────────────────────────────────────────
# M-8 — 락 안 O(n) 훑기 0
# ─────────────────────────────────────────────────────────────────────────────

class TestEvictionDoesNotScanTheCustodianPopulation(unittest.TestCase):

    def test_the_class_never_sorts_or_scans_for_an_extremum(self):
        """⚠️ 첫 판은 이 검사가 ``sorted`` 를 **화이트리스트**했다 — 그리고 그 ``sorted``
        가 상한 20k 에서 삽입당 1.46ms 를 락 안에서 지불하고 있었다(적대 평가 R1a F5).
        지금은 클래스 전체에 하나도 없어야 한다.
        """
        tree = ast.parse(_SERVICE_SOURCE.read_text(encoding='utf-8'))
        cls = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and node.name == 'TokenRevocationList'
        )
        called = [
            node.func.id for node in ast.walk(cls)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        self.assertTrue(called, '비공허성 — 파서가 빈 집합을 보고 통과한 것이 아니다')
        for banned in ('sorted', 'min', 'sum'):
            self.assertNotIn(
                banned, called,
                f'{banned} 는 보관자 수 또는 항목 수에 비례한다 — 락 안에서 금지',
            )
        # ⚠️ ``max`` 는 **인자 하나 형태만** 금지한다 — ``max(iterable)`` 은 보관자 수에
        # 비례하고 ``max(a, b)`` 는 상수 시간이다. 이름만 금지하면 정당한 사용까지 막혀
        # 가드가 삭제되고, 그냥 허용하면 훑기가 이 이름으로 되돌아온다.
        #
        # ⚠️ **비-공허성 단언을 여기 붙이지 않는다.** 첫 판은 *"상수 시간 형태가 실제로
        # 쓰인다"* 를 함께 단언했는데, 그것은 **그 사용을 없애면 red** 가 되는 단언이다
        # — 실제로 ``crowded_population = max(...)`` 를 단순 대입으로 정리하자 이 검사가
        # 정직하게 red 를 냈다. 음성 단언에 비-공허성을 붙이려면 *금지된 형태를 심으면
        # red 인가*를 물어야지 *허용된 형태가 존재하는가*를 물으면 안 된다.
        scanning_max = [
            node for node in ast.walk(cls)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == 'max'
            and len(node.args) < 2
        ]
        self.assertEqual(
            scanning_max, [],
            'max(iterable) 은 보관자 수에 비례한다 — 락 안에서 금지',
        )

    def test_the_scan_guard_would_actually_catch_a_planted_scan(self):
        """음성 단언의 비-공허성 — **금지된 형태를 심으면 red 인가.**"""
        planted = ast.parse('class C:\n    def f(self):\n        return max(self._by_count)\n')
        cls = next(n for n in ast.walk(planted) if isinstance(n, ast.ClassDef))
        scanning = [
            node for node in ast.walk(cls)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == 'max'
            and len(node.args) < 2
        ]
        self.assertEqual(len(scanning), 1, '가드가 심은 훑기를 못 본다면 무의미하다')

    def test_per_insertion_cost_is_flat_as_the_ceiling_grows(self):
        """⚠️ 첫 판은 **삽입 횟수**를 바꾸고 상한을 200 에 고정했다 — 비용이 의존하는
        유일한 변수를 고정한 것이라 구조적으로 실패할 수 없었다(적대 평가 R1a F5).
        상한을 바꾸고, 보관자를 여럿 둬서 극값 탐색이 실제로 비싸지게 만든다.
        """
        def cost(ceiling):
            revocations = TokenRevocationList(
                clock=lambda: 0.0, max_entries=ceiling,
                prune_interval_seconds=1e9,
            )
            # 서로 다른 보유량을 가진 보관자 다수 — ``_by_count`` 키가 많아진다.
            owner, size = 0, 1
            planted = 0
            while planted < ceiling:
                for j in range(min(size, ceiling - planted)):
                    revocations.revoke(f'seed-{planted}-{j}',
                                       expires_at=1e9 + planted,
                                       custodian=_custodian_of(f'own-{owner}'),
                                       churn=True)
                    planted += 1
                owner += 1
                size += 1
            samples = 2_000
            start = time.perf_counter()
            for i in range(samples):
                revocations.revoke(f'x-{i}', expires_at=1e9 + i,
                                   custodian=_custodian_of('own-0'), churn=True)
            return (time.perf_counter() - start) / samples * 1e6

        small = cost(400)
        large = cost(20_000)
        self.assertGreater(small, 0.0)
        self.assertLess(
            large, max(small * 4.0, 30.0),
            f'상한이 50배 커질 때 삽입 비용이 따라 커진다 '
            f'({small:.2f}us -> {large:.2f}us) — 락 안에 훑기가 있다',
        )


class TestTheCountIndexAgreesWithTheQueues(unittest.TestCase):
    """⚠️ 색인이 큐와 갈라지면 **상한이 조용히 뚫린다.**

    ``_heaviest`` 가 실재하지 않는 보유량을 가리키면 희생자 조회가 ``None`` 을 답하고
    축출 루프가 그대로 빠져나온다 — 목록은 계속 자란다. 변이 배터리가 그 두 형태를
    SURVIVED 로 드러냈다(감소 추적 제거 · ``_forget_locked`` 재색인 제거): 단일 보관자
    flood 만으로는 보유량이 **줄지 않으므로** 그 결함이 발화하지 않았다.
    """

    @staticmethod
    def _mixed_workload(revocations, rounds=400):
        owners = [f'own-{i}' for i in range(7)]
        for r in range(rounds):
            for i, name in enumerate(owners):
                for j in range(i + 1):
                    revocations.revoke(
                        f'{name}-{r}-{j}', expires_at=1e9 + r,
                        custodian=_custodian_of(name), churn=(j % 2 == 0),
                    )
            if r % 5 == 0:
                revocations.revoke(
                    f'logout-{r}', expires_at=1e9,
                    custodian=_custodian_of(owners[r % len(owners)]),
                )

    def _assert_index_matches(self, revocations):
        expected: dict = {}
        for owner in set(revocations._churn) | set(revocations._kept):
            held = (len(revocations._churn.get(owner, ()))
                    + len(revocations._kept.get(owner, ())))
            expected.setdefault(held, set()).add(owner)
        actual = {
            count: set(owners)
            for count, owners in revocations._by_count.items() if owners
        }
        self.assertEqual(
            actual, expected,
            '보유량 색인이 큐와 갈라졌다 — 희생자 선택이 유령을 본다',
        )
        self.assertEqual(
            revocations._heaviest, max(expected, default=0),
            '_heaviest 가 실제 최대 보유량과 다르다',
        )

    def test_the_ceiling_holds_under_a_mixed_multi_custodian_workload(self):
        revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=200)
        self._mixed_workload(revocations)
        self.assertLessEqual(
            len(revocations), 200,
            '색인이 갈라지면 축출 루프가 빠져나오고 상한이 조용히 뚫린다',
        )
        self.assertGreater(len(revocations), 0)

    def test_the_index_matches_the_queues_after_that_workload(self):
        revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=200)
        self._mixed_workload(revocations)
        self.assertGreater(revocations.tracked_custodians, 1)
        self._assert_index_matches(revocations)

    def test_the_index_matches_after_expiry_releases_entries(self):
        clock = {'t': 0.0}
        revocations = TokenRevocationList(
            clock=lambda: clock['t'], max_entries=200,
            prune_interval_seconds=1.0,
        )
        for i in range(300):
            revocations.revoke(f'short-{i}', expires_at=5.0,
                               custodian=_custodian_of(f'own-{i % 4}'),
                               churn=True)
        for i in range(300):
            revocations.revoke(f'long-{i}', expires_at=1e9,
                               custodian=_custodian_of(f'own-{i % 4}'))
        clock['t'] = 100.0
        revocations.revoke('trigger', expires_at=1e9,
                           custodian=_custodian_of('own-0'))
        self._assert_index_matches(revocations)


# ─────────────────────────────────────────────────────────────────────────────
# M-9 — 상한에 닿지 않으면 답이 이전과 같다
# ─────────────────────────────────────────────────────────────────────────────

class TestBelowTheCeilingNothingChanged(unittest.TestCase):

    def test_revoked_and_expired_answers_are_unchanged(self):
        clock = {'t': 1000.0}
        revocations = TokenRevocationList(clock=lambda: clock['t'])
        revocations.revoke('jti-1', expires_at=1100.0,
                           custodian=_custodian_of('a'))
        self.assertTrue(revocations.is_revoked('jti-1'))
        self.assertFalse(revocations.is_revoked('never-seen'))
        self.assertFalse(revocations.is_revoked(''))
        self.assertFalse(revocations.is_revoked(None))
        clock['t'] = 1100.0
        self.assertFalse(revocations.is_revoked('jti-1'))

    def test_no_warning_is_emitted_below_the_ceiling(self):
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        log = logging.getLogger('test_automation.platform_api')
        log.addHandler(handler)
        try:
            revocations = TokenRevocationList(
                clock=lambda: 0.0, max_entries=1_000,
            )
            for i in range(500):
                revocations.revoke(f'k-{i}', expires_at=1e9,
                                   custodian=_custodian_of('a'))
        finally:
            log.removeHandler(handler)
        self.assertEqual(buf.getvalue().strip(), '')


# ─────────────────────────────────────────────────────────────────────────────
# M-10 — 경고가 분기를 이름으로 대고, 홍수가 되지 않는다
# ─────────────────────────────────────────────────────────────────────────────

class TestTheEvictionWarningNamesItsBranch(unittest.TestCase):

    def _capture(self, run):
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        log = logging.getLogger('test_automation.platform_api')
        log.addHandler(handler)
        try:
            run()
        finally:
            log.removeHandler(handler)
        return [line for line in buf.getvalue().splitlines() if line.strip()]

    @staticmethod
    def _degenerate_pressure():
        """**퇴화 구간**을 실제로 발화시킨다 — 상한보다 신원이 많은 구성.

        ⚠️ 이것은 용량 사건이 **아니다**. 첫 판은 이 분기를 *"capacity signal"* 이라
        불렀고 적대 평가가 그 문구를 반증했다: 단일 신원의 flood 직후에도 그 줄이 떠서
        운영자에게 *"공격이 아니다"* 라고 말했다. 이제 이 분기는 ``max_entries`` 가
        추적 중인 신원 수보다 작을 때만 도달한다 — 공정한 선택 자체가 존재하지 않는
        **구성 오류**이고, 프로덕션 상한 20,000 에서는 서로 다른 인증 신원 20,000개를
        요구한다.
        """
        revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=4)
        for i in range(9):
            revocations.revoke(
                f'k-{i}', expires_at=1_000.0 + i,
                custodian=_custodian_of(f'user-{i}'),
            )
        return revocations

    def test_degenerate_pressure_reads_as_misconfiguration_not_load(self):
        lines = self._capture(self._degenerate_pressure)
        self.assertTrue(lines, '비공허성 — 퇴화 분기가 실제로 발화했는지 먼저')
        self.assertIn('MISCONFIGURATION', lines[0])
        self.assertIn('NO fair choice available', lines[0])

    def test_a_single_identity_flood_never_reaches_the_degenerate_branch(self):
        """⚠️ 적대 평가가 반증한 바로 그 주장의 봉인.

        옛 판에서는 한 신원의 flood 가 폴백 분기를 발화시켜 *"단일 신원 flood 가
        아니다"* 라는 줄을 냈다. 이제 그 분기는 신원 수 > 상한 일 때만 도달하므로,
        flood 는 **자기 제한 줄만** 낸다.
        """
        def flood():
            revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=20)
            revocations.revoke('v', expires_at=1_000.0,
                               custodian=_custodian_of('victim'))
            for i in range(500):
                revocations.revoke(f'a-{i}', expires_at=100_000.0 + i,
                                   custodian=_custodian_of('attacker'))

        lines = self._capture(flood)
        self.assertTrue(lines, '비공허성 — 축출이 실제로 일어났는지 먼저')
        self.assertEqual(len(lines), 1)
        self.assertIn('No custodian within its share lost an entry', lines[0])
        self.assertNotIn('MISCONFIGURATION', lines[0])

    def test_one_axis_flooding_does_not_silence_the_other(self):
        """⚠️ 두 축이 창을 공유하면 나눈 의미가 없다 — 변이 배터리가 찾은 공백."""
        def both():
            revocations = TokenRevocationList(
                clock=lambda: 0.0, max_entries=4, prune_interval_seconds=60.0,
            )
            for i in range(9):                     # 퇴화 (신원 9 > 상한 4)
                revocations.revoke(f'k-{i}', expires_at=1_000.0 + i,
                                   custodian=_custodian_of(f'user-{i}'))
            for i in range(200):                   # 자기 몫 축출 (한 보관자)
                revocations.revoke(f'a-{i}', expires_at=100_000.0 + i,
                                   custodian=_custodian_of('attacker'))

        lines = self._capture(both)
        self.assertEqual(
            len(lines), 2,
            '한 축의 홍수가 형제 축의 첫 줄을 덮으면 안 된다',
        )
        self.assertIn('MISCONFIGURATION', lines[0])
        self.assertIn('No custodian within its share', lines[1])

    def test_the_two_branches_do_not_share_a_sentence(self):
        """⚠️ 판정은 **발화된 두 줄**이지 소스의 리터럴이 아니다."""
        def flood():
            revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=20)
            for i in range(300):
                revocations.revoke(f'a-{i}', expires_at=100_000.0 + i,
                                   custodian=_custodian_of('attacker'))

        custody_line = self._capture(flood)[0]
        degenerate_line = self._capture(self._degenerate_pressure)[0]
        self.assertNotEqual(custody_line, degenerate_line)
        self.assertIn('within its share', custody_line)
        self.assertNotIn('within its share', degenerate_line)
        self.assertIn('MISCONFIGURATION', degenerate_line)
        self.assertNotIn('MISCONFIGURATION', custody_line)

    def test_the_running_total_survives_an_unreported_tail(self):
        """⚠️ 창 집계는 마지막 버스트의 꼬리를 잃는다 — 누계가 그것을 복원한다.

        적대 평가 실측: 축출 50건에 *"1건"* 한 줄. 창 안 누적만 실으면 그 줄 이후의
        건수는 다음 축출이 올 때까지 보고되지 않고, 안 오면 영영 보고되지 않는다.
        그래서 **프로세스 시작 이후 누계**를 함께 싣는다 — 다음 줄이 진실을 복원한다.
        """
        clock = {'t': 0.0}
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        log = logging.getLogger('test_automation.platform_api')
        log.addHandler(handler)
        try:
            revocations = TokenRevocationList(
                clock=lambda: clock['t'], max_entries=20,
                prune_interval_seconds=60.0,
            )
            for i in range(60):
                revocations.revoke(f'a-{i}', expires_at=100_000.0 + i,
                                   custodian=_custodian_of('attacker'))
            clock['t'] = 1_000.0
            revocations.revoke('a-final', expires_at=100_000.0,
                               custodian=_custodian_of('attacker'))
        finally:
            log.removeHandler(handler)
        lines = [line for line in buf.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertIn('(1 since start)', lines[0])
        # 두 번째 줄의 누계가 실제로 일어난 축출 전량을 답한다.
        self.assertIn('(41 since start)', lines[1])

    def test_a_flood_does_not_become_a_log_flood(self):
        """⚠️ 사건당 한 줄은 그 자체로 증폭기다 — 실측 380 회전/초."""
        def run():
            revocations = TokenRevocationList(
                clock=lambda: 0.0, max_entries=20, prune_interval_seconds=60.0,
            )
            for i in range(5_000):
                revocations.revoke(f'a-{i}', expires_at=100_000.0 + i,
                                   custodian=_custodian_of('attacker'))

        lines = self._capture(run)
        self.assertEqual(len(lines), 1)

    def test_the_reported_count_accumulates_across_the_window(self):
        """창 안의 것만 세면 초당 380건과 하루 1건이 같은 줄을 낸다."""
        clock = {'t': 0.0}

        def run():
            revocations = TokenRevocationList(
                clock=lambda: clock['t'], max_entries=20,
                prune_interval_seconds=60.0,
            )
            for i in range(300):
                revocations.revoke(f'a-{i}', expires_at=100_000.0 + i,
                                   custodian=_custodian_of('attacker'))
            clock['t'] = 1_000.0
            revocations.revoke('a-final', expires_at=100_000.0,
                               custodian=_custodian_of('attacker'))

        lines = self._capture(run)
        self.assertEqual(len(lines), 2)
        self.assertIn('; 1 entries', lines[0])
        self.assertNotIn('; 1 entries', lines[1])

    def test_the_runbook_names_the_phrases_the_code_actually_emits(self):
        """⚠️ **런북이 코드보다 오래 살았다** — 평가자 둘이 각각 그것을 찾았다.

        이 웨이브가 문구를 다시 쓰면서 런북을 고치지 않아, 런북이 grep 하라고 적은 두
        문자열이 코드에 **0건**이었고 의미도 **반대**였다. 그 런북을 따른 운영자는 정상
        포화에 무고한 시험원의 비밀번호를 바꾸게 한다.

        그래서 판정은 산문 대조가 아니라 **같은 상수**다 — 개명이 셋을 함께 움직인다.
        """
        runbook_path = resolve_repo_artifact(
            __file__, 'docs/operations/central-pc-operational-validation-runbook.md',
        )
        if not runbook_path.is_file() and (_REPO_ROOT / '.extraction-layout.json').is_file():
            self.skipTest(
                'NOT VERIFIED here: monorepo operational runbook is not shipped '
                'in this delivered lane; repository execution remains required.'
            )
        runbook = runbook_path.read_text(encoding='utf-8')
        for phrase in (CUSTODY_EVICTION_PHRASE, DEGENERATE_EVICTION_PHRASE):
            self.assertIn(
                phrase, runbook,
                f'런북이 코드가 내지 않는 문구를 지목한다: {phrase!r}',
            )

        # 그리고 그 상수가 **실제로 발화된 줄에** 들어 있어야 한다 — 상수가 코드 어딘가에
        # 있다는 것만으로는 운영자가 grep 할 대상이 되지 않는다.
        custody = self._capture(lambda: self._flood_one_identity())[0]
        degenerate = self._capture(self._degenerate_pressure)[0]
        self.assertIn(CUSTODY_EVICTION_PHRASE, custody)
        self.assertIn(DEGENERATE_EVICTION_PHRASE, degenerate)
        self.assertNotIn(DEGENERATE_EVICTION_PHRASE, custody)
        self.assertNotIn(CUSTODY_EVICTION_PHRASE, degenerate)

    def test_the_custody_line_does_not_accuse_an_attacker(self):
        """⚠️ 공격자 없는 시험원 30명·연속 세션만으로도 이 줄이 뜬다 (적대 평가 2R F3).

        첫 판은 *"check for a replayed refresh token"* 으로 끝났고, 런북은 그 줄을 보면
        비밀번호 변경을 권고하라고 적었다 — 즉 정상 포화가 무고한 시험원에 대한 조치로
        이어졌다. 이 줄은 *"누가 냈는가"* 만 말하고, 정상 포화의 규모를 함께 실어야 한다.
        """
        line = self._capture(lambda: self._flood_one_identity())[0]
        self.assertIn('does NOT mean an attack', line)
        self.assertIn(
            'continuously-active sessions reach this ceiling', line,
            '정상 포화의 규모를 말하지 않으면 운영자는 이 줄을 공격으로 읽는다',
        )

    @staticmethod
    def _flood_one_identity():
        revocations = TokenRevocationList(clock=lambda: 0.0, max_entries=20)
        for i in range(300):
            revocations.revoke(f'a-{i}', expires_at=100_000.0 + i,
                               custodian=_custodian_of('attacker'), churn=True)

    def test_the_degenerate_line_reports_the_population_it_decided_on(self):
        """⚠️ 축출이 보관자를 비우면 인구가 줄어, 나중에 읽으면 자기 분기의 전제를
        만족하지 않는 숫자가 보고된다 (적대 평가 2R F3 실측: 신원 5에서 "4"를 보고)."""
        line = self._capture(self._degenerate_pressure)[0]
        self.assertIn('5 distinct identities are tracked', line)
        self.assertNotIn('4 distinct identities are tracked', line)

    def test_the_first_event_is_never_delayed(self):
        def run():
            revocations = TokenRevocationList(
                clock=lambda: 0.0, max_entries=2, prune_interval_seconds=1e9,
            )
            for i in range(5):
                revocations.revoke(f'a-{i}', expires_at=1e9 + i,
                                   custodian=_custodian_of('attacker'))

        self.assertEqual(len(self._capture(run)), 1)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
