"""세션 uuid 의 **target 스코프**가 두 구현에서 같은 답을 내는지 봉인한다.

## 왜 이 파일이 있나

`CentralIdResolverPort.resolve_session_uuid` 의 docstring 이 계약으로 선언한다 —

> ``target_identity`` scopes the uuid to one measurement target. It is required
> for the same reason ``chamber_id`` is: a chamber PC keeps one measurement
> database per target and each numbers its sessions from 1, so without it two
> devices measured on one chamber share a uuid.

즉 「한 chamber 에서 두 target 을 재면 세션 uuid 가 갈린다」는 **포트의 계약**이지
구현마다 달라도 되는 세부가 아니다. 그런데 2026-09-07 실측에서 두 구현이 그
질문에 다르게 답했고, **그 질문을 던지는 검사가 레포 어디에도 없었다**:

    도구 범위: grep -rn --exclude-dir=.git --exclude-dir=node_modules, 모든 파일형

        target_identity          소스 6파일 / 시험 **0파일**
        session_uuid_name        소스 2파일 / 시험 **0파일**
        measurement_target_key   소스 3파일 / 시험 **0파일**

chamber 축에는 봉인이 있었다 —
``test_postgres_central_id_resolver.py::test_chamber_scoped_local_ids_are_distinct_and_stable``.
**그 target 형제가 없었다.** 이 파일이 그 자리다.

## 이 파일이 «하지 않는» 것

실물의 기대값을 문자열로 적어 두고 대역과 비교하지 **않는다.** 값을 베끼면 실물이
바뀐 날 이 검사는 「과거를 검사한 초록」이 된다. 대신 **두 구현에 같은 질문을 던지고
성질을 비교**한다.

그리고 실물의 ``_session_cache`` 키 모양을 대역에 이식하지 **않는다.** 실측:
실물의 miss 경로와 hit 경로가 같은 값을 낸다 — 그 dict 는 관측 불가한 «캐시»이고,
대역의 ``_session_uuid`` 는 «등록부»다. 같은 이름의 dict 지만 역할의 종이 다르다.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
import unittest

from fcc_test_platform.central_id_resolver import (
    CentralIdResolutionError,
    InMemoryCentralIdResolver,
)
from fcc_test_platform.postgres_central_id_resolver import PostgresCentralIdResolver


CHAMBER = 'CH-1'
LOCAL_ID = 11
TARGET_A = 'MODEL-X|SAMPLE-1'
TARGET_B = 'MODEL-Y|SAMPLE-2'


def _connection_factory_that_must_not_be_called():
    """세션 경로는 DB 를 만지지 않는다 — 부르면 그것이 결함이다.

    ``test_postgres_central_id_resolver.py::test_session_uuid_does_not_touch_db``
    가 이미 그 축을 봉인하고 있다. 여기서 다시 세우는 이유는 이 파일이 실물을
    «직접» 부르기 때문이다: 조용히 연결을 열면 이 검사가 통합시험이 되고, 통합
    시험은 오프라인 시험실 PC 에서 다른 이유로 빨개진다.
    """
    raise AssertionError('resolve_session_uuid must not open a connection')


def _real() -> PostgresCentralIdResolver:
    return PostgresCentralIdResolver(
        provider_id='provider-uuid',
        connection_factory=_connection_factory_that_must_not_be_called,
    )


class TestBothImplementationsHonourTheTargetScope(unittest.TestCase):
    """R2 · R7 — 두 구현이 같은 성질을 만족한다."""

    def test_both_split_two_targets_on_one_chamber(self):
        # 대역에는 두 target 을 «등록»해 준다. 대역은 등록부라 계산하지 않는다 —
        # 실물처럼 uuid 를 «지어낼» 수 없고, 지어내게 만드는 것이 이 판의 목표도
        # 아니다. 물음은 「등록했을 때 대역이 그것을 가르는가」다.
        fake = InMemoryCentralIdResolver()
        fake.register_session(
            LOCAL_ID, 'uuid-for-A', chamber_id=CHAMBER, target_identity=TARGET_A,
        )
        fake.register_session(
            LOCAL_ID, 'uuid-for-B', chamber_id=CHAMBER, target_identity=TARGET_B,
        )
        real = _real()

        for label, resolver in (('fake', fake), ('real', real)):
            with self.subTest(implementation=label):
                first = resolver.resolve_session_uuid(
                    LOCAL_ID, chamber_id=CHAMBER, target_identity=TARGET_A,
                )
                second = resolver.resolve_session_uuid(
                    LOCAL_ID, chamber_id=CHAMBER, target_identity=TARGET_B,
                )
                # 값을 베끼지 않는다 — «성질»만 묻는다.
                self.assertNotEqual(
                    first, second,
                    f'{label}: 한 chamber 의 두 target 이 세션 uuid 를 공유한다 — '
                    f'포트가 그 충돌을 금지한다',
                )

    def test_both_are_stable_for_the_same_target(self):
        """갈라짐만 요구하면 「매번 새 uuid」도 통과한다. 안정성도 같이 묻는다."""
        fake = InMemoryCentralIdResolver()
        fake.register_session(
            LOCAL_ID, 'uuid-for-A', chamber_id=CHAMBER, target_identity=TARGET_A,
        )
        real = _real()

        for label, resolver in (('fake', fake), ('real', real)):
            with self.subTest(implementation=label):
                first = resolver.resolve_session_uuid(
                    LOCAL_ID, chamber_id=CHAMBER, target_identity=TARGET_A,
                )
                second = resolver.resolve_session_uuid(
                    LOCAL_ID, chamber_id=CHAMBER, target_identity=TARGET_A,
                )
                self.assertEqual(first, second, f'{label}: 같은 target 이 두 uuid 를 냈다')

    def test_the_real_resolver_never_opened_a_connection(self):
        """위 두 검사가 실물을 부르고도 조용히 통과하는 «경로»를 다시 확인한다."""
        real = _real()
        # 부르면 AssertionError 가 난다 — 안 나는 것이 이 검사의 판정이다.
        real.resolve_session_uuid(LOCAL_ID, chamber_id=CHAMBER, target_identity=TARGET_A)


class TestDeclaredScopeIsNotBypassed(unittest.TestCase):
    """R3 · R4 — 「선언하지 않은 무관심」과 「선언한 스코프를 무시함」을 가른다."""

    def test_declared_scope_is_not_silently_bypassed(self):
        """R3 — target 스코프를 «선언한» 등록부는 조용히 넘어가지 않는다.

        이것이 이 판이 닫는 결함의 핵심이다. 이 가드가 없으면 대역은 target B 를
        물었을 때 target-무관 항목을 돌려주고, 그것이 곧 포트가 금지하는 충돌이다.
        """
        resolver = InMemoryCentralIdResolver(
            session_uuid_by_local_id={LOCAL_ID: 'agnostic-uuid'},
        )
        resolver.register_session(
            LOCAL_ID, 'uuid-for-A', target_identity=TARGET_A,
        )

        # 등록한 target 은 그대로 온다.
        self.assertEqual(
            resolver.resolve_session_uuid(LOCAL_ID, target_identity=TARGET_A),
            'uuid-for-A',
        )
        # 등록하지 «않은» target 은 target-무관 항목으로 미끄러지지 않는다.
        with self.assertRaises(CentralIdResolutionError):
            resolver.resolve_session_uuid(LOCAL_ID, target_identity=TARGET_B)

    def test_undeclared_scope_still_serves_the_agnostic_entry(self):
        """R4 — 등록이 target 을 아예 말하지 않았으면 caller 의 선언을 존중한다.

        ``{11: 'AAA'}`` 는 「스코프 무관하게 11은 AAA」라는 **선언**이다. 그것을
        깨뜨리는 것은 결함 수리가 아니라 오탐이고, 오탐을 내는 게이트는 우회를
        가르친다. 오늘 초록인 시험 여덟이 정확히 이 세계에 산다.
        """
        resolver = InMemoryCentralIdResolver(
            session_uuid_by_local_id={LOCAL_ID: 'agnostic-uuid'},
        )
        self.assertEqual(
            resolver.resolve_session_uuid(LOCAL_ID, target_identity=TARGET_A),
            'agnostic-uuid',
        )
        self.assertEqual(
            resolver.resolve_session_uuid(LOCAL_ID, target_identity=TARGET_B),
            'agnostic-uuid',
        )

    def test_the_two_worlds_are_scoped_per_local_session(self):
        """한 세션의 선언이 «다른» 세션의 무관심을 오염시키지 않는다."""
        resolver = InMemoryCentralIdResolver(
            session_uuid_by_local_id={LOCAL_ID: 'agnostic-11', 22: 'agnostic-22'},
        )
        resolver.register_session(LOCAL_ID, 'uuid-for-A', target_identity=TARGET_A)

        # 11 은 스코프를 선언했다 → 미등록 target 은 loud.
        with self.assertRaises(CentralIdResolutionError):
            resolver.resolve_session_uuid(LOCAL_ID, target_identity=TARGET_B)
        # 22 는 선언하지 않았다 → 그대로 온다.
        self.assertEqual(
            resolver.resolve_session_uuid(22, target_identity=TARGET_B),
            'agnostic-22',
        )


def _key_arities_the_reader_reads() -> frozenset[int]:
    """``resolve_session_uuid`` 가 ``_session_uuid`` 를 «어떤 모양의 키로» 읽는가.

    **AST 로 판정한다 — 줄 단위 정규식이 아니다.** 이 레포는 「줄 단위 스캔은 여러
    줄 표현을 못 본다」를 하루에 네 번 밟은 기록이 있고, 그 실패는 「없다」와 「못
    봤다」의 출력이 같아서 조용하다. AST 는 줄바꿈을 보지 않는다.

    반환은 **arity 의 집합**이다: 스칼라 키는 1, 튜플 키는 그 길이.
    """
    source = textwrap.dedent(inspect.getsource(InMemoryCentralIdResolver.resolve_session_uuid))
    tree = ast.parse(source)

    def _is_session_dict(node: ast.AST) -> bool:
        return isinstance(node, ast.Attribute) and node.attr == '_session_uuid'

    arities: set[int] = set()
    for node in ast.walk(tree):
        key: ast.expr | None = None
        if isinstance(node, ast.Subscript) and _is_session_dict(node.value):
            key = node.slice
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'get'
            and _is_session_dict(node.func.value)
            and node.args
        ):
            key = node.args[0]
        if key is None:
            continue
        arities.add(len(key.elts) if isinstance(key, ast.Tuple) else 1)
    return frozenset(arities)


def _key_arities_the_writer_can_make() -> frozenset[int]:
    """``register_session`` 이 실제로 만들어 내는 키 모양.

    시그니처를 읽지 않고 **불러서 본다.** 선언은 거짓말할 수 있고, 이 레포는
    「타입 클라이언트는 거짓말하는 선언을 못 본다」를 기록해 두었다.
    """
    accepted = set(
        inspect.signature(InMemoryCentralIdResolver.register_session).parameters
    )
    arities: set[int] = set()
    for chamber, target in (
        ('', ''),
        (CHAMBER, ''),
        ('', TARGET_A),
        (CHAMBER, TARGET_A),
    ):
        # ⚠️ 시그니처가 받는 인자만 넘긴다. 무조건 넘기면 writer 에 그 인자가 «없을»
        #    때 TypeError 로 죽고, 그러면 이 함수는 「모양이 몇 종인가」를 재기도 전에
        #    끝난다 — 아래 등호는 실행되지 않은 채 red 가 되고, red 의 이유가
        #    「모양이 다르다」가 아니라 「이름이 없다」가 된다. 두 명제는 다르다.
        kwargs = {'chamber_id': chamber}
        if 'target_identity' in accepted:
            kwargs['target_identity'] = target
        elif target:
            continue  # 이 writer 는 target 스코프를 표현할 수단이 없다
        resolver = InMemoryCentralIdResolver()
        resolver.register_session(LOCAL_ID, 'some-uuid', **kwargs)
        for key in resolver._session_uuid:
            arities.add(len(key) if isinstance(key, tuple) else 1)
    return frozenset(arities)


class TestReaderAndWriterAgreeOnKeyShapes(unittest.TestCase):
    """R1 · R5 — reader 가 읽는 모양을 writer 가 전부 만들 수 있다."""

    def test_writer_can_make_every_key_shape_the_reader_reads(self):
        """등호다. 「writer 가 더 많이 만든다」도 「덜 만든다」도 결함이다.

        ⚠️ 두 집합 다 **파생**이다 — 어느 쪽도 상수로 적지 않았다. 상수로 적으면
        네 번째 모양이 생긴 날 이 검사가 조용해진다. 이 레포는 「집합은 선언에서
        파생하라」를 이미 기록했다.
        """
        reads = _key_arities_the_reader_reads()
        writes = _key_arities_the_writer_can_make()

        self.assertEqual(
            reads, writes,
            f'reader 가 읽는 키 모양 {sorted(reads)} 와 writer 가 만드는 키 모양 '
            f'{sorted(writes)} 가 다르다. reader 만 아는 모양은 «생성자 주입 없이는 '
            f'도달할 수 없는» 자리이고, 다음 사람이 writer 를 처음 부르는 날 '
            f'조용히 미끄러진다',
        )

    def test_the_derivation_is_not_vacuous(self):
        """파생기가 «아무것도 못 본» 채로 등호를 통과하는 것을 막는다.

        빈 집합 둘은 서로 같다 — 「집합이 빈다」는 공허 통과의 첫째 모양이고,
        이 레포는 그것을 이름으로 기록했다.
        """
        reads = _key_arities_the_reader_reads()
        self.assertGreaterEqual(
            len(reads), 2,
            'reader 에서 키 모양을 두 종류도 못 찾았다 — 파생기가 눈이 멀었거나 '
            'reader 가 바뀌었다. 어느 쪽이든 이 등호는 지금 아무것도 말하지 않는다',
        )
        self.assertIn(
            1, reads,
            'reader 는 스칼라 키(맨 int)를 읽는다 — 그것을 못 봤다면 파생기가 '
            'Subscript 를 놓치고 있다',
        )


if __name__ == '__main__':
    unittest.main()
