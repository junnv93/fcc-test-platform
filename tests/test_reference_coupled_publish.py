"""결합 그룹은 반쪽으로 게시될 수 없다 — 그리고 그 규칙은 화면이 아니라 경계에 산다.

## 무엇을 지키는가

`correction` 과 `switch_port_mapping` 은 **한 물리적 사실의 두 반쪽**이다. 어느 케이블이
신호를 나르는가(포트맵)와 그 케이블이 dB 로 얼마인가(보정)다. 한쪽만 게시하면 안테나 1의
신호 경로에 안테나 2의 경로 손실이 붙고, **측정은 정상 완료되고 verdict 도 나오며 숫자만
두 케이블 차이만큼 조용히 틀린다.** 다운스트림에서 탐지할 방법이 없다.

## 그런데 투영이 이미 그것을 막는다. 그러면 이 봉인은 무엇인가

`resolve_lookup_ownership` 은 부분 게시를 거부하고 **두 패밀리를 모두 워크북 소유로**
남긴다. 즉 **틀린 숫자가 장비에 도달하는 경로는 이미 닫혀 있다.** 이것은 안전 수정이
아니라 **막다른 골목 수정**이다:

시험원이 correction 만 게시한다 → 중앙이 **200 을 답한다** → 챔버는 다음 세션에
아무것도 바꾸지 않는다 → 유일한 흔적은 아무도 보지 않는 챔버 PC 로그의 ERROR 한 줄.
**성공을 답한 쪽과 실패를 아는 쪽이 다르다.**

## 왜 UI 가 아니라 서비스인가

API 가 경계다. 화면만 지키는 규칙은 그 화면 하나만 지킨다 — 그리고 이 웨이브가 유일한
클라이언트를 쓴다는 사실이 그 규칙을 **더 약하게** 만든다. 다음 클라이언트는 기억하지
못한다. 결합 어휘도 도메인 SSOT(`COUPLED_FAMILY_GROUPS`)에 있고 그것을 아는 계층 중
가장 바깥이 서비스다.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402

from fcc_test_platform.application.central_reference_service import (  # noqa: E402
    CentralReferenceService,
)
from fcc_test_kernel.domain.models.reference_catalog import CatalogFamily, RevisionState  # noqa: E402
from fcc_test_platform.domain.ports.output.central_reference_port import (  # noqa: E402
    ReferenceCoupledPublishError,
    ReferenceRevisionNotFoundError,
)
from _ast_string_finder import find_string_literals_anywhere  # noqa: E402
from fcc_test_kernel.domain.services.reference_ownership_policy import (  # noqa: E402
    COUPLED_FAMILY_GROUPS,
    projection_fields_for,
)

#: The provider these fixtures speak as. Every reference operation resolves
#: the provider since 2026-08-25, so the argument is no longer decorative —
#: it must match the ``provider_id`` on the rows the read fake returns.
_PROVIDER = 'fcc-unlicensed'


def _revision(revision_id: str, family: str, *, scope_id: str = 'room-1') -> dict:
    """A revision row shaped like the read adapter's REVISION_COLUMNS output."""
    return {
        'revision_id': revision_id,
        'provider_id': 'fcc-unlicensed',
        'family': family,
        'profile_id': 'default',
        'scope_kind': 'room',
        'scope_id': scope_id,
        'revision_number': 1,
        'state': RevisionState.CANDIDATE.value,
        'version': 1,
        'etag': 'a' * 64,
        'content_sha256': 'b' * 64,
        'source_snapshot_id': 'snap-1',
        'source_manifest_sha256': 'c' * 64,
        'official_manifest_sha256': None,
        'forked_from_revision_id': None,
        'created_by': 'tester@example.com',
        'created_at': '2026-08-08T00:00:00.000000Z',
        'updated_by': 'tester@example.com',
        'updated_at': '2026-08-08T00:00:00.000000Z',
        'approved_by': None,
        'approved_at': None,
        'approval_reason': None,
        'published_by': None,
        'published_at': None,
        'publish_reason': None,
        'retired_by': None,
        'retired_at': None,
        'retirement_reason': None,
        'entry_count': 2,
    }


class _FakeRead:
    def __init__(self, revisions: dict[str, dict], entries=None) -> None:
        self.revisions = revisions
        self.entries = entries or {}

    def provider_exists(self, provider_id):
        """Registered by default — this fake models a provisioned deployment.

        Production refuses an unregistered provider on every reference
        operation (2026-08-25), so a fake without this method would let these
        tests pass a shape the real service rejects.
        """
        return True

    def read_revision(self, revision_id):
        return self.revisions.get(revision_id)

    def read_entries(self, revision_id):
        return self.entries.get(revision_id, [])

    def list_revisions(self, *a, **k):  # pragma: no cover — unused here
        return []

    def read_bundle_identity(self, *a, **k):  # pragma: no cover
        return []

    def read_bundle(self, *a, **k):  # pragma: no cover
        return []


class _FakeWrite:
    """Records which publish path was taken, and can fail the SECOND row.

    ``fail_second`` models the only failure that matters: the transaction that
    moved one row and then could not move the other. A real adapter rolls back;
    this fake asserts the SERVICE never asks for two separate publishes, which
    is the shape that would make a rollback impossible.
    """

    def __init__(self, *, fail_second: bool = False) -> None:
        self.single_calls: list[str] = []
        self.coupled_calls: list[tuple[str, str]] = []
        self._fail_second = fail_second

    def create_candidate(self, *a, **k):  # pragma: no cover — unused here
        raise AssertionError('not used')

    def publish(self, revision_id, **kwargs):
        self.single_calls.append(revision_id)
        return {'revision_id': revision_id, 'state': RevisionState.PUBLISHED.value}

    def publish_coupled(self, revision_id, coupled_revision_id, **kwargs):
        self.coupled_calls.append((revision_id, coupled_revision_id))
        if self._fail_second:
            raise RuntimeError('second UPDATE failed')
        return [
            {'revision_id': revision_id, 'state': RevisionState.PUBLISHED.value},
            {
                'revision_id': coupled_revision_id,
                'state': RevisionState.PUBLISHED.value,
            },
        ]


def _coupled_pair() -> tuple[CatalogFamily, CatalogFamily]:
    """The coupled pair, DERIVED from the domain SSOT rather than named here.

    Spelling ``correction`` and ``switch_port_mapping`` in this file would put
    the coupling vocabulary in a second place — the defect class the channel
    match-key SSOT exists for. If the group ever changes, this test follows.
    """
    group = COUPLED_FAMILY_GROUPS[0]
    members = sorted(group, key=lambda member: member.value)
    return members[0], members[1]


class TestCoupledGroupPublishIsAtomicAtTheServiceBoundary(unittest.TestCase):

    def setUp(self) -> None:
        self.first, self.second = _coupled_pair()

    def test_the_domain_declares_exactly_one_coupled_pair(self) -> None:
        """Non-vacuity — the derivation found a real pair."""
        self.assertEqual(1, len(COUPLED_FAMILY_GROUPS))
        self.assertEqual(2, len(COUPLED_FAMILY_GROUPS[0]))

    def test_publishing_half_a_coupled_group_is_refused(self) -> None:
        read = _FakeRead({'rev-a': _revision('rev-a', self.first.value)})
        write = _FakeWrite()
        service = CentralReferenceService(read, write)

        with self.assertRaises(ReferenceCoupledPublishError) as caught:
            service.publish(_PROVIDER, 'rev-a', published_by='tester')

        self.assertEqual([], write.single_calls, 'a half publish reached the store')
        self.assertEqual([], write.coupled_calls)

    def test_the_refusal_names_the_sibling_family(self) -> None:
        """A refusal a tester cannot act on is a refusal that gets worked around."""
        read = _FakeRead({'rev-a': _revision('rev-a', self.first.value)})
        service = CentralReferenceService(read, _FakeWrite())
        with self.assertRaises(ReferenceCoupledPublishError) as caught:
            service.publish(_PROVIDER, 'rev-a', published_by='tester')
        self.assertIn(self.second.value, str(caught.exception))

    def test_publishing_both_halves_takes_the_single_transaction_path(self) -> None:
        read = _FakeRead({
            'rev-a': _revision('rev-a', self.first.value),
            'rev-b': _revision('rev-b', self.second.value),
        })
        write = _FakeWrite()
        service = CentralReferenceService(read, write)

        result = service.publish(
            _PROVIDER, 'rev-a', published_by='tester', coupled_revision_id='rev-b',
        )

        self.assertEqual([('rev-a', 'rev-b')], write.coupled_calls)
        self.assertEqual(
            [], write.single_calls,
            'the coupled path must NOT fall back to two independent publishes — '
            'two calls are two commit boundaries, which is the state the coupling '
            'exists to forbid.',
        )
        self.assertEqual(RevisionState.PUBLISHED.value, result['state'])

    def test_a_failed_second_half_does_not_report_success(self) -> None:
        read = _FakeRead({
            'rev-a': _revision('rev-a', self.first.value),
            'rev-b': _revision('rev-b', self.second.value),
        })
        write = _FakeWrite(fail_second=True)
        service = CentralReferenceService(read, write)
        with self.assertRaises(RuntimeError):
            service.publish(
                _PROVIDER, 'rev-a', published_by='tester', coupled_revision_id='rev-b',
            )

    def test_the_sibling_must_be_the_right_family(self) -> None:
        read = _FakeRead({
            'rev-a': _revision('rev-a', self.first.value),
            'rev-b': _revision('rev-b', CatalogFamily.FREQUENCY_TABLE.value),
        })
        service = CentralReferenceService(read, _FakeWrite())
        with self.assertRaises(ReferenceCoupledPublishError):
            service.publish(
                _PROVIDER, 'rev-a', published_by='tester', coupled_revision_id='rev-b',
            )

    def test_the_sibling_must_target_the_same_scope(self) -> None:
        """Two rooms' halves are the same defect wearing the coupling's clothes."""
        read = _FakeRead({
            'rev-a': _revision('rev-a', self.first.value, scope_id='room-1'),
            'rev-b': _revision('rev-b', self.second.value, scope_id='room-2'),
        })
        service = CentralReferenceService(read, _FakeWrite())
        with self.assertRaises(ReferenceCoupledPublishError) as caught:
            service.publish(
                _PROVIDER, 'rev-a', published_by='tester', coupled_revision_id='rev-b',
            )
        self.assertIn('room-1', str(caught.exception))
        self.assertIn('room-2', str(caught.exception))

    def test_an_uncoupled_family_publishes_alone(self) -> None:
        read = _FakeRead({
            'rev-c': _revision('rev-c', CatalogFamily.FREQUENCY_TABLE.value),
        })
        write = _FakeWrite()
        service = CentralReferenceService(read, write)
        service.publish(_PROVIDER, 'rev-c', published_by='tester')
        self.assertEqual(['rev-c'], write.single_calls)
        self.assertEqual([], write.coupled_calls)

    def test_an_uncoupled_family_refuses_a_coupled_id(self) -> None:
        """Silently ignoring it would let a client believe it published a pair."""
        read = _FakeRead({
            'rev-c': _revision('rev-c', CatalogFamily.FREQUENCY_TABLE.value),
            'rev-d': _revision('rev-d', CatalogFamily.ANT_GAIN.value),
        })
        write = _FakeWrite()
        service = CentralReferenceService(read, write)
        with self.assertRaises(ReferenceCoupledPublishError):
            service.publish(
                _PROVIDER, 'rev-c', published_by='tester', coupled_revision_id='rev-d',
            )
        self.assertEqual([], write.single_calls)

    def test_an_unknown_revision_is_not_found_rather_than_a_shrug(self) -> None:
        service = CentralReferenceService(_FakeRead({}), _FakeWrite())
        with self.assertRaises(ReferenceRevisionNotFoundError):
            service.publish(_PROVIDER, 'nope', published_by='tester')


class TestRevisionDetailCarriesTheServerOwnedColumnOrder(unittest.TestCase):
    """열 순서는 서버가 준다 — 클라이언트가 payload 키에서 파생하면 안 된다.

    payload 는 열린 매핑이고 null 필드는 생략될 수 있으므로, 엔트리마다 열 집합이
    달라져 표가 출렁인다. TS 에 6 패밀리 × N 컬럼을 다시 적는 대안은 같은 순서를 두
    언어로 쪼개고, 그 드리프트는 **시험원이 게시한 뒤에야** 드러난다.
    """

    def test_payload_columns_equal_the_domain_contract_for_every_family(self) -> None:
        checked = 0
        for family in CatalogFamily:
            try:
                expected = list(projection_fields_for(family))
            except KeyError:
                # 런타임 테이블이 없는 카탈로그 전용 패밀리 — 상세도 성립하지 않는다.
                continue
            read = _FakeRead({'rev': _revision('rev', family.value)})
            service = CentralReferenceService(read, _FakeWrite())
            with self.subTest(family=family.value):
                self.assertEqual(
                    expected, service.read_revision(_PROVIDER, 'rev')['payload_columns'],
                )
            checked += 1
        self.assertGreaterEqual(checked, 5, 'non-vacuity — no family was checked')

    def test_the_detail_carries_the_revision_row_not_only_entries(self) -> None:
        """검토 없이 게시하는 화면은 CANDIDATE 로 멈춘 설계를 배신한다."""
        read = _FakeRead(
            {'rev': _revision('rev', CatalogFamily.FREQUENCY_TABLE.value)},
            entries={'rev': []},
        )
        detail = CentralReferenceService(read, _FakeWrite()).read_revision(_PROVIDER, 'rev')
        self.assertEqual('rev', detail['revision']['revision_id'])
        self.assertEqual(RevisionState.CANDIDATE.value, detail['revision']['state'])
        self.assertIn('entries', detail)

    def test_an_unknown_revision_detail_is_not_found(self) -> None:
        service = CentralReferenceService(_FakeRead({}), _FakeWrite())
        with self.assertRaises(ReferenceRevisionNotFoundError):
            service.read_revision(_PROVIDER, 'nope')


class TestTheCouplingVocabularyHasOneDefinitionSite(unittest.TestCase):
    """짝 어휘가 플랫폼/프론트에 복제되지 않았다."""

    _SCANNED = (
        resolve_repo_artifact(__file__, 'src/application/platform'),
        resolve_repo_artifact(__file__, 'src/infrastructure/adapters/driving/api'),
    )

    def test_no_module_names_the_pair_as_literals(self) -> None:
        """실제 문자열 리터럴만 본다 — 주석은 대상이 아니다.

        첫 판은 소스 텍스트를 그대로 검색해 **규칙을 설명하는 주석**을 위반으로
        읽었다. 왜 짝을 적으면 안 되는지 적어두는 행위가 그 규칙 위반으로 판정되는
        가드는 사람들이 설명을 지우게 만들고, 그러면 다음 사람은 이유를 모른 채
        같은 리터럴을 적는다. 그래서 AST 로 코드가 **값으로 쓰는** 문자열만 본다
        (helper SSOT 위임 — dict/list/f-string/concat 우회 4종까지 커버).
        """
        first, second = _coupled_pair()
        pair = frozenset({first.value, second.value})
        offenders = []
        for root in self._SCANNED:
            for path in root.rglob('*.py'):
                tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
                found = {
                    literal
                    for _line, literal in find_string_literals_anywhere(tree, pair)
                }
                if found == pair:
                    offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(
            [], offenders,
            f'{offenders} spell the coupled pair as literals; the pair is '
            f'declared once in COUPLED_FAMILY_GROUPS and must be derived.',
        )

    def test_the_scan_flags_a_synthetic_offender(self) -> None:
        """비-공허성 — 실제로 짝을 값으로 적으면 잡힌다."""
        first, second = _coupled_pair()
        pair = frozenset({first.value, second.value})
        offender = ast.parse(
            f"COUPLED = ['{first.value}', '{second.value}']\n"
        )
        found = {
            literal for _line, literal in find_string_literals_anywhere(offender, pair)
        }
        self.assertEqual(pair, found)

    def test_the_scan_actually_reads_files(self) -> None:
        """Non-vacuity — the roots exist and contain modules."""
        total = sum(len(list(root.rglob('*.py'))) for root in self._SCANNED)
        self.assertGreater(total, 10)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()


class TestWhoMayWriteReferenceData(unittest.TestCase):
    """시험원은 되고 뷰어는 안 된다 — 그리고 방 축은 멤버십으로 열리지 않는다.

    ``authorize`` 는 토큰 ∪ 프로젝트 멤버십의 합집합이다. 2026-08-08 이전에는 참조
    operation 만 ``project_id`` 없이 그것을 불러 **멤버십 절반이 죽어 있었다** — 즉
    ``rbac_role_grants`` 에 토큰을 넣어도 멤버십에서 파생된 권한에는 아무 효과가
    없었고, 그것은 권한 그래프에 대한 거짓 진술이다.

    비대칭은 의도이고 그것이 요점이다. 프로젝트 X 의 멤버라는 사실은 *그 프로젝트의*
    주파수표를 만질 근거는 되지만 **1방의 케이블 손실**을 바꿀 근거는 아니다 — 방은
    프로젝트보다 오래 존속하고 한 프로젝트가 두 방에 걸친다.
    """

    def _adapter(self, principal, *, read=None, write=None, rbac=None):
        from fcc_test_contracts.common.access_policy import ApiAccessPolicy
        from fcc_test_kernel.application.central_contract.api_contracts import PLATFORM_API_OPERATIONS
        from fcc_test_platform.api.platform_routes import (
            PlatformApiAdapter,
        )

        service = CentralReferenceService(
            read or _FakeRead({}), write or _FakeWrite(),
        )
        return PlatformApiAdapter(
            None,
            access_policy=ApiAccessPolicy(PLATFORM_API_OPERATIONS),
            principal=principal,
            reference_service=service,
            rbac_read_service=rbac,
        )

    @staticmethod
    def _principal(subject, permissions):
        from fcc_test_contracts.common.access_policy import ApiPrincipal

        return ApiPrincipal.from_permissions(subject, permissions)

    def test_the_write_token_authorises_authoring(self) -> None:
        write = _FakeWrite()
        read = _FakeRead({})
        adapter = self._adapter(
            self._principal('tester', ['platform:reference-write']),
            read=read, write=write,
        )
        # 인가를 통과하면 서비스가 요청 검증으로 넘어간다. 여기서는 인가만 본다.
        with self.assertRaises(Exception) as caught:
            adapter.create_reference_revision('fcc-unlicensed', {})
        self.assertNotIn('forbidden', str(caught.exception).lower())

    def test_a_read_only_principal_cannot_author(self) -> None:
        from fcc_test_platform.api.platform_routes import (
            PlatformAuthorizationError,
        )
        adapter = self._adapter(self._principal('viewer', ['platform:read']))
        with self.assertRaises(PlatformAuthorizationError):
            adapter.create_reference_revision('fcc-unlicensed', {
                'family': CatalogFamily.FREQUENCY_TABLE.value, 'scope_id': 'proj-1',
            })

    def test_an_unauthorised_publish_does_not_reveal_existence(self) -> None:
        """존재하는 리비전과 없는 리비전의 거부가 구별 불가여야 한다."""
        from fcc_test_platform.api.platform_routes import (
            PlatformAuthorizationError,
        )
        read = _FakeRead({
            'rev-real': _revision('rev-real', CatalogFamily.CORRECTION.value),
        })
        adapter = self._adapter(self._principal('viewer', ['platform:read']), read=read)

        errors = []
        for revision_id in ('rev-real', 'rev-does-not-exist'):
            with self.assertRaises(PlatformAuthorizationError) as caught:
                adapter.publish_reference_revision(
                    'fcc-unlicensed', revision_id, {},
                )
            errors.append(str(caught.exception))
        self.assertEqual(
            errors[0], errors[1],
            'the refusal differs between an existing and a missing revision — '
            'that difference is an existence oracle.',
        )

    def test_a_room_scoped_family_is_not_opened_by_project_membership(self) -> None:
        """방 축은 명시적으로 부여된 토큰을 요구한다."""
        from fcc_test_platform.api.platform_routes import (
            PlatformAuthorizationError,
        )

        class _GrantEverything:
            """멤버십이 무엇이든 준다고 답하는 RBAC — 그래도 방 축은 안 열린다."""

            def user_enabled(self, *a, **k):
                return True

            def permissions_for(self, *a, **k):
                return ['platform:reference-write']

            def effective_permissions(self, *a, **k):
                return ['platform:reference-write']

        room_family, _ = _coupled_pair()
        adapter = self._adapter(
            self._principal('member', ['platform:read']),
            rbac=_GrantEverything(),
        )
        with self.assertRaises(PlatformAuthorizationError):
            adapter.create_reference_revision('fcc-unlicensed', {
                'family': room_family.value, 'scope_id': 'room-1',
            })

    def test_a_project_scoped_family_IS_opened_by_project_membership(self) -> None:
        """비대칭의 나머지 반쪽 — 없으면 위 테스트는 "멤버십이 아무것도 안 한다"만 증명한다.

        프로젝트 X 의 멤버가 *그 프로젝트의* 분석기 설정 후보를 만드는 것은 정당하다.
        이 케이스가 통과해야 앞의 방-축 거부가 "멤버십 경로 자체가 죽었다"가 아니라
        "축에 따라 다르게 답한다"는 뜻이 된다.
        """
        recorded: list[str] = []

        class _ProjectMember:
            def user_enabled(self, *a, **k):
                return True

            def effective_permissions(self, project_id, subject, **k):
                recorded.append(project_id)
                return ['platform:reference-write']

        adapter = self._adapter(
            self._principal('member', ['platform:read']),
            rbac=_ProjectMember(),
        )
        with self.assertRaises(Exception) as caught:
            adapter.create_reference_revision('fcc-unlicensed', {
                'family': CatalogFamily.ANALYZER_SETTINGS.value,
                'scope_id': 'proj-1',
            })
        self.assertNotIn('forbidden', str(caught.exception).lower())
        self.assertEqual(
            ['proj-1'], recorded,
            'the membership path was never consulted with the project scope — '
            'the grant would be inert for membership-derived permissions.',
        )


class TestTheDetailCarriesTheCouplingVocabulary(unittest.TestCase):
    """짝 어휘도 서버가 준다 — 클라이언트가 적지도, 오류 문장에서 파싱하지도 않는다.

    열 순서(`payload_columns`)와 **같은 규칙**이다. 결합 사실은 도메인 SSOT
    ``COUPLED_FAMILY_GROUPS`` 에 있고, 프론트가 짝 이름을 적으면 어휘가 두 곳이 된다.
    거부 메시지에서 형제 이름을 뽑아 쓰는 것도 같은 결함의 다른 얼굴이다 — 사람이
    읽으라고 쓴 문장을 기계가 파싱하는 결합이 생기고, 문구를 다듬는 순간 깨진다.
    """

    def _detail(self, family: str) -> dict:
        read = _FakeRead({'rev': _revision('rev', family)}, entries={'rev': []})
        return CentralReferenceService(read, _FakeWrite()).read_revision(_PROVIDER, 'rev')

    def test_a_coupled_family_names_its_sibling(self) -> None:
        first, second = _coupled_pair()
        self.assertEqual(second.value, self._detail(first.value)['coupled_with'])
        self.assertEqual(first.value, self._detail(second.value)['coupled_with'])

    def test_an_uncoupled_family_says_null_rather_than_omitting_the_key(self) -> None:
        """키 자체를 빼면 클라이언트가 '아직 안 왔다'와 '짝이 없다'를 구별 못 한다."""
        detail = self._detail(CatalogFamily.FREQUENCY_TABLE.value)
        self.assertIn('coupled_with', detail)
        self.assertIsNone(detail['coupled_with'])

    def test_the_vocabulary_matches_the_domain_ssot(self) -> None:
        """파생임을 단언한다 — 리터럴이면 이 대조가 의미를 갖지 않는다."""
        first, second = _coupled_pair()
        self.assertEqual(
            {first.value, second.value},
            {member.value for member in COUPLED_FAMILY_GROUPS[0]},
        )
