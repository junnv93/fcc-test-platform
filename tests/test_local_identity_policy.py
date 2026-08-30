"""로컬 신원 + 비밀번호 정책 — 순수 도메인 봉인 (계약 T-2, M-1, M-6).

이 파일이 지키는 것 넷:

1. **이메일 정규화가 한 곳이다.** 저장과 조회가 다른 규칙을 쓰면 사용자가 자기 계정을
   못 찾는다.
2. **``LOCAL`` 과 ``LEGACY`` 는 다른 issuer 다.** 합치면 *"신원을 못 밝힌 옛 행"* 이
   로그인 조회에 도달한다.
3. **bcrypt 의 72바이트 천장이 문자가 아니라 바이트로 지켜진다.** 그리고 이 저장소에
   설치된 bcrypt 는 그것을 막아 주지 않는다(실측) — 정책이 유일한 방어다.
4. **SQL 리터럴과 Python 상수가 결박돼 있다.** SQL 은 상수를 import 할 수 없으므로
   그 결박은 테스트가 소유할 수밖에 없다.
"""
from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_REPO_ROOT / 'src'), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fcc_test_contracts.common.identity import (  # noqa: E402
    EMAIL_MAX_LENGTH,
    LOCAL_IDENTITY_ISSUER,
    is_local_issuer,
    local_identity_key,
    normalize_email,
)
from domain.services.password_policy import (  # noqa: E402
    BCRYPT_COST_ROUNDS,
    PASSWORD_HASH_INPUT_MAX_BYTES,
    PASSWORD_MIN_LENGTH,
    PasswordDefect,
    password_defects,
    password_hash_input,
)

_SCHEMA_PATH = _REPO_ROOT / 'docs' / 'platform' / 'central_db_schema.v1.json'
_MIGRATION_026 = (
    _REPO_ROOT / 'docs' / 'platform' / 'migrations' / '026_local_identity.sql'
)
_POLICY_SOURCE = (
    _REPO_ROOT / 'src' / 'domain' / 'services' / 'password_policy.py'
)


def _require_repo_artifact(case, path: Path) -> Path:
    """저장소 루트 산출물을 요구하거나, **사유와 함께** skip 한다.

    ⚠️ 이 축은 레인 추출 때문에 존재한다. 테스트의 레인 귀속은 **import 폐포에서
    파생**되므로, 이 파일이 platform 모듈을 import 하는 순간 파일 전체가
    ``fcc-test-platform`` 납품 상자로 따라 들어간다 — 그런데 ``docs/`` 는 따라가지
    않는다(memory: *"an import moves a test into a delivery box"*).

    상자 안에서 이 질문들은 **답할 수 없는 것이지 실패한 것이 아니다**: 생성된 DDL 이
    상수를 담는가, 계약 아티팩트가 401 을 선언하는가 — 전부 모노레포에 대한 질문이다.
    조용히 통과시키지 않고 무엇을 확인하지 못했는지 말한다.
    """
    if not path.exists():
        case.skipTest(
            f'NOT VERIFIED here: {path.name} is absent. This assertion is about the '
            'monorepo artifact, and a delivered lane box ships source without docs/. '
            'It runs in the repository lane.'
        )
    return path


def _require_composition(case):
    """합성 루트를 import 하거나 사유와 함께 skip 한다.

    ⚠️ 납품 상자는 자기 레인 모듈만 싣는다. 합성 루트는 여러 레인을 가로질러 배선하는
    자리이므로 상자 안에서 import 되지 않는 것이 **정상**이다 — 그 배선을 묻는 것은
    모노레포의 질문이다.
    """
    try:
        import fcc_test_platform.api_composition as composition
    except Exception as exc:  # noqa: BLE001
        case.skipTest(
            f'NOT VERIFIED here: platform_api_composition is not importable ({exc}). '
            'The composition root wires across lanes, so a single delivered lane box '
            'cannot stand it up. It runs in the repository lane.'
        )
    return composition


class TestEmailNormalisationIsOneRule(unittest.TestCase):

    def test_lowercases_and_strips(self):
        self.assertEqual(normalize_email('  Kim.MJ@Samsung.COM '), 'kim.mj@samsung.com')

    def test_is_idempotent(self):
        """정규화의 정규화는 자기 자신 — 저장값을 다시 정규화해도 안 움직인다.

        이것이 참이라야 ``lower(email) == email == subject`` 가 **구성으로** 성립한다.
        """
        once = normalize_email('  Kim.MJ@Samsung.COM ')
        self.assertEqual(normalize_email(once), once)

    def test_is_total_for_non_strings(self):
        """총함수 — HTTP 본문에서 온 값이 인증 표면을 500 으로 만들지 않는다."""
        for value in (None, 123, [], {}, object(), b'a@x.com'):
            with self.subTest(value=type(value).__name__):
                self.assertEqual(normalize_email(value), '')

    def test_none_does_not_become_a_real_local_part(self):
        """``str(None)`` 로 강제하면 ``'none'`` 이라는 **실재 가능한** 로컬파트가 된다."""
        self.assertNotEqual(normalize_email(None), 'none')


class TestLocalIdentityKey(unittest.TestCase):

    def test_key_is_sentinel_issuer_plus_normalised_email(self):
        self.assertEqual(
            local_identity_key('  Kim.MJ@Samsung.COM '),
            (LOCAL_IDENTITY_ISSUER, 'kim.mj@samsung.com'),
        )

    def test_case_variants_collapse_to_one_key(self):
        """대소문자만 다른 이메일이 같은 행을 가리킨다 — EMS ``lower(email)`` 과 같은 규칙."""
        self.assertEqual(
            local_identity_key('A@X.com'), local_identity_key('a@x.COM'),
        )

    def test_empty_email_is_refused_loudly(self):
        """총함수를 고르면 ``(local, '')`` 이라는 실재하는 행 키가 만들어진다.

        그 행은 아무도 로그인할 수 없는 사용자가 되고, 두 번째 빈 이메일 등록을 유일
        인덱스가 거부해 원인이 엉뚱한 곳에서 나타난다.
        """
        for value in ('', '   ', None, 123):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    local_identity_key(value)

    def test_over_long_email_is_refused(self):
        too_long = ('a' * EMAIL_MAX_LENGTH) + '@x.com'
        with self.assertRaises(ValueError):
            local_identity_key(too_long)

    def test_max_length_email_is_accepted(self):
        """경계가 거부가 아니라 수용 쪽이다 — off-by-one 을 양방향으로 못박는다."""
        at_limit = ('a' * (EMAIL_MAX_LENGTH - len('@x.com'))) + '@x.com'
        self.assertEqual(len(at_limit), EMAIL_MAX_LENGTH)
        issuer, subject = local_identity_key(at_limit)
        self.assertEqual(issuer, LOCAL_IDENTITY_ISSUER)
        self.assertEqual(len(subject), EMAIL_MAX_LENGTH)


class TestLocalIssuerIsNotLegacyIssuer(unittest.TestCase):
    """⚠️ 합치면 *"신원을 못 밝힌 옛 행"* 이 로그인 조회에 도달한다."""

    def test_the_two_sentinels_differ(self):
        from fcc_test_contracts.common.identity import LEGACY_IDENTITY_ISSUER
        self.assertNotEqual(LOCAL_IDENTITY_ISSUER, LEGACY_IDENTITY_ISSUER)

    def test_is_local_issuer_rejects_the_legacy_sentinel(self):
        from fcc_test_contracts.common.identity import LEGACY_IDENTITY_ISSUER
        self.assertFalse(is_local_issuer(LEGACY_IDENTITY_ISSUER))

    def test_is_local_issuer_rejects_a_real_oidc_issuer(self):
        self.assertFalse(is_local_issuer('https://login.microsoftonline.com/tid/v2.0'))

    def test_is_local_issuer_is_total(self):
        for value in (None, 123, [], object()):
            with self.subTest(value=type(value).__name__):
                self.assertFalse(is_local_issuer(value))

    def test_application_layer_reexports_the_same_object(self):
        """사본이 아니라 **같은 값**이다 — 둘이 갈라지면 로그인은 되는데 권한이 없다."""
        from fcc_test_contracts.common import identity as app_identity
        self.assertEqual(app_identity.LOCAL_IDENTITY_ISSUER, LOCAL_IDENTITY_ISSUER)
        self.assertIn('LOCAL_IDENTITY_ISSUER', app_identity.__all__)


class TestTheSqlLiteralIsBoundToTheConstant(unittest.TestCase):
    """SQL 은 Python 상수를 import 할 수 없다 — 그 결박은 여기 산다 (계약 M-1).

    ⚠️ 이것은 *철자를 묻는 봉인* 이 아니다. 묻는 것은 **두 축이 같은 값을 말하는가**
    이고, 어느 한쪽이 움직이면 red 다. 상수를 바꾸면 인덱스가 옛 issuer 를 계속
    걸러내어 **새 로컬 사용자에게 이메일 유일성이 사라진다** — 조용히.
    """

    def _users_email_index(self) -> dict:
        schema = json.loads(
            _require_repo_artifact(self, _SCHEMA_PATH).read_text(encoding='utf-8')
        )
        for index in schema['tables']['users']['indexes']:
            if index['name'] == 'ux_users_email_lower':
                return index
        raise AssertionError(
            'ux_users_email_lower is gone from the schema SSOT — local email '
            'uniqueness is no longer enforced by the database'
        )

    def test_schema_index_predicate_names_the_local_issuer_constant(self):
        where = self._users_email_index()['where']
        self.assertIn(
            LOCAL_IDENTITY_ISSUER, where,
            f'the partial index predicate {where!r} does not name '
            f'{LOCAL_IDENTITY_ISSUER!r}. Either the constant moved or the index '
            'now scopes on something else; both silently change who is unique.',
        )

    def test_migration_predicate_names_the_local_issuer_constant(self):
        sql = _require_repo_artifact(self, _MIGRATION_026).read_text(encoding='utf-8')
        self.assertIn(LOCAL_IDENTITY_ISSUER, sql)

    def test_the_index_is_unique(self):
        self.assertTrue(self._users_email_index().get('unique'))

    def test_the_index_stays_partial_on_email_presence(self):
        """⚠️ 이 조건이 빠지면 **이메일 없는 기존 행 두 개가 서로 충돌**한다."""
        where = self._users_email_index()['where']
        self.assertIn('IS NOT NULL', where)
        self.assertIn("<> ''", where)

    def test_the_generated_ddl_carries_the_same_predicate(self):
        """스키마 JSON 이 아니라 **실제로 적용되는 DDL** 을 읽는다.

        생성물을 읽지 않으면 생성기가 그 절을 떨어뜨려도 이 파일은 초록이다
        (memory: *"a completeness gate must read the consumer's mirror"*).
        """
        ddl = _require_repo_artifact(
            self,
            _REPO_ROOT / 'docs' / 'platform' / 'migrations'
            / '001_initial_central_db.sql',
        ).read_text(encoding='utf-8')
        line = [ln for ln in ddl.splitlines() if 'ux_users_email_lower' in ln]
        self.assertEqual(len(line), 1, 'expected exactly one index statement')
        self.assertIn(LOCAL_IDENTITY_ISSUER, line[0])
        self.assertIn('UNIQUE', line[0])


class TestPasswordByteCeiling(unittest.TestCase):
    """bcrypt 의 72바이트 천장 — 문자가 아니라 바이트.

    ⚠️ 실측(bcrypt 3.2.2, 2026-08-21): ``hashpw(b'a' * 73, salt)`` 가 **예외 없이
    통과**한다. bcrypt 4.x 는 같은 입력에 ``ValueError`` 를 던진다. 즉 라이브러리가
    이 경계를 지켜 준다는 가정은 버전에 따라 거짓이고, 그래서 정책이 지킨다.
    """

    def test_exactly_at_the_ceiling_is_accepted(self):
        at_limit = 'a' * PASSWORD_HASH_INPUT_MAX_BYTES
        self.assertEqual(len(password_hash_input(at_limit)), 72)
        self.assertNotIn(PasswordDefect.TOO_LONG_FOR_HASH, password_defects(at_limit))

    def test_one_byte_over_is_refused(self):
        over = 'a' * (PASSWORD_HASH_INPUT_MAX_BYTES + 1)
        self.assertIn(PasswordDefect.TOO_LONG_FOR_HASH, password_defects(over))

    def test_the_ceiling_is_bytes_not_characters_for_hangul(self):
        """⚠️ 이 시스템의 사용자는 한글을 친다 — 72바이트는 **한글 24자**다.

        글자 수를 세는 검사는 이 경계를 그냥 지나치고, 25자를 저장하면 bcrypt 가
        24자에서 자른다. 사용자는 존재하지 않는 보안을 가졌다고 믿는다.
        """
        twenty_four = '가' * 24
        twenty_five = '가' * 25
        self.assertEqual(len(password_hash_input(twenty_four)), 72)
        self.assertEqual(len(password_hash_input(twenty_five)), 75)
        self.assertNotIn(
            PasswordDefect.TOO_LONG_FOR_HASH, password_defects(twenty_four),
        )
        self.assertIn(
            PasswordDefect.TOO_LONG_FOR_HASH, password_defects(twenty_five),
        )
        # 그리고 25자는 **문자 수로는** 최소 길이를 넉넉히 넘는다 — 즉 글자 수만 세는
        # 정책에서는 이 값이 완벽히 유효해 보인다.
        self.assertGreater(len(twenty_five), PASSWORD_MIN_LENGTH)

    def test_a_long_passphrase_and_its_truncation_are_told_apart(self):
        """앞 72바이트가 같고 뒤가 다른 두 비밀번호 — 둘 다 거부되어야 한다.

        이것이 천장이 존재하는 이유 그 자체다. 허용하면 두 값이 **같은 해시**를 낳아
        서로의 비밀번호로 로그인된다.
        """
        base = 'a' * PASSWORD_HASH_INPUT_MAX_BYTES
        for suffix in ('X', 'Y'):
            with self.subTest(suffix=suffix):
                self.assertIn(
                    PasswordDefect.TOO_LONG_FOR_HASH,
                    password_defects(base + suffix),
                )


class TestPasswordDefectsAreTotalAndComplete(unittest.TestCase):

    def test_minimum_length_boundary(self):
        self.assertIn(
            PasswordDefect.TOO_SHORT,
            password_defects('a' * (PASSWORD_MIN_LENGTH - 1)),
        )
        self.assertNotIn(
            PasswordDefect.TOO_SHORT, password_defects('a' * PASSWORD_MIN_LENGTH),
        )

    def test_edge_whitespace_is_a_defect(self):
        """앞뒤 공백은 복사·붙여넣기 한 번은 살아남고 다음 번엔 못 살아남는다."""
        for value in (' abcdefghij', 'abcdefghij ', '\tabcdefghij\n'):
            with self.subTest(value=repr(value)):
                self.assertIn(
                    PasswordDefect.WHITESPACE_EDGES, password_defects(value),
                )

    def test_interior_whitespace_is_allowed(self):
        """⚠️ 문장형 패스프레이즈를 막지 않는다 — NIST 는 그것을 권장한다."""
        self.assertEqual(password_defects('correct horse battery'), ())

    def test_nul_byte_is_a_defect(self):
        """⚠️ 이론이 아니다. bcrypt 3.2.2 는 NUL 에 ``ValueError`` 를 던지고,
        NUL 은 ``json.loads('"\\u0000"')`` 으로 **도달 가능**하다 — 이 결함이 없으면
        클라이언트가 보낸 한 글자가 인증 표면을 500 으로 만든다."""
        self.assertIn(PasswordDefect.NULL_BYTE, password_defects('abcde\x00fghij'))

    def test_the_nul_path_is_actually_reachable_from_json(self):
        """도달 가능성 자체를 실행으로 못박는다 — 없으면 위 테스트는 가정 위에 선다."""
        decoded = json.loads('"abcde\\u0000fghij"')
        self.assertIn('\x00', decoded)
        self.assertIn(PasswordDefect.NULL_BYTE, password_defects(decoded))

    def test_every_defect_is_reachable(self):
        """어휘에 도달 불가능한 멤버가 없다 — 있으면 그것은 어휘가 아니라 장식이다."""
        witnesses = {
            PasswordDefect.TOO_SHORT: 'a',
            PasswordDefect.TOO_LONG_FOR_HASH: 'a' * 200,
            PasswordDefect.WHITESPACE_EDGES: ' abcdefghij',
            PasswordDefect.NULL_BYTE: 'abcdefghij\x00',
        }
        self.assertEqual(
            set(witnesses), set(PasswordDefect),
            'PasswordDefect gained a member with no witness — add one or delete it',
        )
        for defect, witness in witnesses.items():
            with self.subTest(defect=defect.value):
                self.assertIn(defect, password_defects(witness))

    def test_all_defects_are_returned_not_just_the_first(self):
        """⚠️ 첫 결함만 돌려주면 사용자가 거절을 반복하며 규칙을 하나씩 발견한다.

        그 UX 는 사용자를 *규칙을 만족하는 가장 짧은 비밀번호*로 몰아간다.
        """
        defects = password_defects(' a\x00 ')
        self.assertIn(PasswordDefect.TOO_SHORT, defects)
        self.assertIn(PasswordDefect.WHITESPACE_EDGES, defects)
        self.assertIn(PasswordDefect.NULL_BYTE, defects)
        self.assertGreaterEqual(len(defects), 3)

    def test_a_good_password_has_no_defects(self):
        self.assertEqual(password_defects('correcthorse'), ())

    def test_no_complexity_rule_is_enforced(self):
        """운영자 판정 2026-08-21 — EMS/NIST 정합. 복잡도 규칙 **0개**.

        NIST SP 800-63B: *"Verifiers SHALL NOT impose other composition rules
        (e.g., requiring mixtures of different character types) for passwords."*
        EMS 도 같은 결론을 코드에 적었다(``복잡도 규칙 없음(NIST 정렬)``).

        ⚠️ 이 단언이 red 가 되면 그것은 *버그가 고쳐졌다*가 아니라 **운영자 판정이
        코드에서 되돌려졌다**는 뜻이다. 되돌리려면 ADR-0021 을 먼저 고쳐라.
        """
        for password in ('alllowercase', 'ALLUPPERCASE', '123456789012', '////////////'):
            with self.subTest(password=password):
                self.assertEqual(
                    password_defects(password), (),
                    f'{password!r} was refused — a composition rule crept back in',
                )

    def test_is_total_for_non_strings(self):
        for value in (None, 123, [], {}, b'abcdefghij', object()):
            with self.subTest(value=type(value).__name__):
                defects = password_defects(value)
                self.assertIsInstance(defects, tuple)
                self.assertIn(PasswordDefect.TOO_SHORT, defects)


class TestPasswordEncodingIsTotal(unittest.TestCase):
    """``json.loads`` 는 짝 없는 서러게이트를 통과시킨다 — 실측 2026-08-21."""

    def test_a_lone_surrogate_survives_json_and_does_not_raise(self):
        decoded = json.loads('"\\ud800abcdefghij"')
        self.assertIn('\ud800', decoded)
        # strict 인코딩이었다면 여기서 UnicodeEncodeError → 500 이다.
        with self.assertRaises(UnicodeEncodeError):
            decoded.encode('utf-8')
        # 정책 경로는 raise 하지 않는다.
        self.assertIsInstance(password_hash_input(decoded), bytes)
        self.assertIsInstance(password_defects(decoded), tuple)

    def test_a_lone_surrogate_costs_three_bytes(self):
        """Node ``Buffer.byteLength`` 가 U+FFFD 로 세는 값과 같다 — 천장 우회 불가."""
        self.assertEqual(len(password_hash_input('\ud800')), 3)

    def test_a_surrogate_padded_password_cannot_bypass_the_ceiling(self):
        """⚠️ 서러게이트를 0바이트로 세면 이것이 통과한다."""
        payload = '\ud800' * 25          # 25 × 3 = 75 바이트
        self.assertIn(PasswordDefect.TOO_LONG_FOR_HASH, password_defects(payload))

    def test_encoding_is_total_for_non_strings(self):
        for value in (None, 123, [], object()):
            with self.subTest(value=type(value).__name__):
                self.assertEqual(password_hash_input(value), b'')

    def test_validated_bytes_are_the_hashed_bytes(self):
        """정책이 센 바이트와 해셔가 받을 바이트가 **같은 함수**에서 나온다.

        두 곳에서 인코딩하면 그 둘이 갈라지는 날 *검증한 비밀번호 ≠ 저장된 비밀번호*가
        된다 — EMS 가 이름 붙인 silent truncation 의 다른 얼굴이다.
        """
        source = _require_repo_artifact(
            self,
            _REPO_ROOT / 'src' / 'application' / 'platform' / 'password_hasher.py',
        ).read_text(encoding='utf-8')
        self.assertIn(
            'password_hash_input', source,
            'the hasher does not delegate to the policy encoding SSOT',
        )
        # 소스 검사만으로는 *철자를 묻는 봉인*이라, 실제 왕복으로 확인한다: 정책이
        # 세는 바이트로 만든 해시가 원문으로 검증된다.
        from fcc_test_platform.application.password_hasher import BcryptPasswordHasher
        hasher = BcryptPasswordHasher(cost_rounds=4)   # 테스트 비용 (정책 값 아님)
        password = '가' * 24                            # 정확히 72바이트
        stored = hasher.hash(password)
        self.assertTrue(hasher.verify(password, stored))
        # 그리고 다른 인코딩으로 만든 바이트로는 검증되지 않는다 — 두 경로가 갈라지면
        # 바로 이 단언이 red 다.
        self.assertFalse(hasher.verify(password.encode('utf-16'), stored))


class TestTheHasherRefusesAnAbsentHash(unittest.TestCase):
    """⚠️ ``verify`` 의 명시된 안전 속성에 단언이 없었다 (적대 평가 R2).

    ``stored_hash`` 가 비어 있을 때 ``True`` 를 반환하도록 변이시켜도 149개 신규
    테스트가 전부 통과했다. 오늘은 로그인이 그 앞에서 단락되어 도달 불가하지만
    ``change_password`` 는 그 가드 없이 ``verify`` 에 닿고, 무엇보다 **주장된 속성은
    단언돼야 한다** — 도달 불가는 오늘의 사실이지 계약이 아니다.
    """

    def setUp(self) -> None:
        from fcc_test_platform.application.password_hasher import BcryptPasswordHasher
        self.hasher = BcryptPasswordHasher(cost_rounds=4)

    def test_an_absent_hash_never_verifies(self):
        """비밀번호가 설정된 적 없는 OIDC 행이 빈 비밀번호로 로그인되지 않는다."""
        for stored in ('', None, b'', 0, [], {}):
            with self.subTest(stored=repr(stored)):
                self.assertFalse(self.hasher.verify('anything', stored))
                self.assertFalse(self.hasher.verify('', stored))

    def test_a_corrupt_hash_never_verifies_and_never_raises(self):
        """⚠️ raise 하면 500 이 되고, *"비밀번호가 틀렸다"* 와 *"해시가 손상됐다"* 가
        서로 다른 응답이 되어 사용자 열거 오라클이 열린다."""
        for stored in ('not-a-bcrypt-hash', '$2b$xx$broken', '$2b$04$' + 'z' * 53):
            with self.subTest(stored=stored[:20]):
                self.assertFalse(self.hasher.verify('anything', stored))

    def test_a_real_hash_still_verifies(self):
        """⚠️ 음성 단언만 있으면 *항상 False* 인 구현이 만점을 받는다."""
        stored = self.hasher.hash('correcthorse')
        self.assertTrue(self.hasher.verify('correcthorse', stored))
        self.assertFalse(self.hasher.verify('wronghorsexx', stored))


class TestPolicyStaysPureDomain(unittest.TestCase):
    """계약 M-6 — 정책은 순수하고 비용 계수는 상수 SSOT 다."""

    def test_policy_module_does_not_import_bcrypt(self):
        """해싱은 인프라다. 도메인이 bcrypt 를 import 하면 순수성 가드가 red 다."""
        tree = ast.parse(
            _require_repo_artifact(self, _POLICY_SOURCE).read_text(encoding='utf-8')
        )
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split('.')[0])
        forbidden = imported & {
            'bcrypt', 'infrastructure', 'application', 'pandas', 'openpyxl',
            'PySide6', 'pyvisa', 'psycopg', 'fastapi',
        }
        self.assertEqual(
            forbidden, set(),
            f'domain password policy imports {sorted(forbidden)}',
        )

    def test_cost_rounds_matches_ems(self):
        """EMS 가 cost 12 다. 다르면 이관한 해시의 검증 비용이 달라진다."""
        self.assertEqual(BCRYPT_COST_ROUNDS, 12)

    def test_no_call_site_hardcodes_the_cost(self):
        """비용 계수 리터럴이 호출부에 없다 — EMS 는 실제로 네 파일에 흩어져 있다."""
        offenders: list[str] = []
        for path in sorted((_REPO_ROOT / 'src').rglob('*.py')):
            if path == _POLICY_SOURCE:
                continue
            source = path.read_text(encoding='utf-8', errors='replace')
            if 'gensalt' not in source and 'bcrypt' not in source:
                continue
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, 'attr', None) != 'gensalt':
                    continue
                for keyword in node.keywords:
                    if keyword.arg == 'rounds' and isinstance(
                        keyword.value, ast.Constant
                    ):
                        offenders.append(f'{path.relative_to(_REPO_ROOT)}:{node.lineno}')
                for arg in node.args:
                    if isinstance(arg, ast.Constant):
                        offenders.append(f'{path.relative_to(_REPO_ROOT)}:{node.lineno}')
        self.assertEqual(
            offenders, [],
            f'bcrypt cost is a literal at {offenders} instead of '
            'domain.services.password_policy.BCRYPT_COST_ROUNDS',
        )


if __name__ == '__main__':
    unittest.main()
