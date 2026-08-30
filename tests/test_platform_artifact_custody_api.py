"""플롯 보관 현황 중앙 표면 (plot-custody-web-and-chamber P2).

**오라클 독립성**: 어댑터의 SQL 을 테스트가 다시 쓰지 않는다. 프로덕션 SQL 상수를
그대로 실 SQLite 에 태우고(``central_pg_sqlite_shim``), 픽스처 DDL 은 중앙 스키마
JSON SSOT 에서 **파생**한다. 손으로 베낀 DDL 은 프로덕션이 컬럼을 하나 더해도 조용히
옛 모양을 계속 테스트한다.

이 파일이 지키는 성질:

1. **latest-wins 는 관측 시각으로 판정한다** — 늦게 도착한 낡은 관측이 새 판정을
   덮지 않는다. 그리고 밀려남은 **영수증에 실린다**(조용한 성공 금지).
2. **밀려난 스냅샷의 findings 는 건드리지 않는다** — 지우면 최신 판정의 상세를 잃는다.
3. **프로젝트 귀속은 자연키 조인이다** — ``project_id`` 가 나중에 채워지면 과거
   스냅샷이 **백필 없이** 그 프로젝트에 나타난다.
4. **귀속되지 않은 스냅샷은 개수로 보고된다** — 조용히 빠지지 않는다.
5. **상세 조회는 프로젝트 경계를 넘지 않는다** — 남의 프로젝트 스냅샷은 404.
6. **어휘 위반은 400 이다** — 503(중앙 장애)으로 둔갑하지 않는다.
7. **중앙은 판정하지 않는다** — 파일시스템에 접근하는 코드가 0이다.
"""
from __future__ import annotations

import ast
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support.central_pg_sqlite_shim import (  # noqa: E402
    QmarkConnection,
    create_tables_from_schema,
)

from fcc_test_platform.application.central_artifact_custody_read_adapter import (  # noqa: E402
    PostgresCentralArtifactCustodyReadAdapter,
)
from fcc_test_platform.application.central_artifact_custody_service import (  # noqa: E402
    ArtifactCustodyReportRejected,
    CentralArtifactCustodyService,
)
from fcc_test_platform.application.central_artifact_custody_write_adapter import (  # noqa: E402
    PostgresCentralArtifactCustodyWriteAdapter,
)
from domain.ports.output.central_artifact_custody_port import (  # noqa: E402
    ArtifactCustodyNotFoundError,
)
from fcc_test_contracts.common.sqlite_connection_factory import (  # noqa: E402
    SqliteConnectionFactory,
)


_PROVIDER = '11111111-1111-1111-1111-111111111111'
_PROJECT = '22222222-2222-2222-2222-222222222222'
_OTHER_PROJECT = '33333333-3333-3333-3333-333333333333'
_CHAMBER = 'chamber-a'


def _session(session_id='S1', *, status='missing', observed_at='2026-08-09T10:00:00Z',
             counts=None, findings=None, roots=('\\\\fs\\fcc',)):
    return {
        'provider_session_id': session_id,
        'status': status,
        'counts': counts or {'verified': 3, 'missing': 1, 'diverged': 0, 'unknown': 0},
        'observed_at': observed_at,
        'roots': list(roots),
        'findings': findings if findings is not None else [
            {'relative_path': 'a/b/plot.png', 'status': 'missing', 'reason': '보관소에 없음'},
        ],
    }


class _CentralFixture:
    """실 SQLite 중앙 — 스키마 JSON 에서 파생한 테이블."""

    def __init__(self, tmp_path: str) -> None:
        self.path = tmp_path
        conn = SqliteConnectionFactory(tmp_path).create()
        create_tables_from_schema(conn, [
            'artifact_custody_snapshots', 'artifact_custody_findings',
        ])
        # test_sessions 는 조인 상대다. 전체 스키마를 끌어오지 않고 이 테이블만 세운다.
        create_tables_from_schema(conn, ['test_sessions'])
        conn.close()

    def factory(self):
        return QmarkConnection(self.path)

    def add_session_row(self, provider_session_id, *, project_id, chamber_id=_CHAMBER):
        conn = SqliteConnectionFactory(self.path).create()
        conn.execute(
            'INSERT INTO test_sessions (id, provider_id, chamber_id, '
            'provider_session_id, project_id, status) VALUES (?,?,?,?,?,?)',
            (f'sess-{provider_session_id}', _PROVIDER, chamber_id,
             provider_session_id, project_id, 'completed'),
        )
        conn.commit()
        conn.close()

    def set_project(self, provider_session_id, project_id):
        conn = SqliteConnectionFactory(self.path).create()
        conn.execute(
            'UPDATE test_sessions SET project_id = ? WHERE provider_session_id = ?',
            (project_id, provider_session_id),
        )
        conn.commit()
        conn.close()

    def finding_count(self):
        conn = SqliteConnectionFactory(self.path).create()
        n = conn.execute('SELECT COUNT(*) FROM artifact_custody_findings').fetchone()[0]
        conn.close()
        return n


class CustodyCentralTestCase(unittest.TestCase):
    def setUp(self):
        import tempfile
        fd = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        fd.close()
        self.addCleanup(lambda: os.path.exists(fd.name) and os.unlink(fd.name))
        self.central = _CentralFixture(fd.name)
        self.service = CentralArtifactCustodyService(
            read_port=PostgresCentralArtifactCustodyReadAdapter(self.central.factory),
            write_port=PostgresCentralArtifactCustodyWriteAdapter(self.central.factory),
        )

    def store(self, *sessions):
        return self.service.store_report(
            chamber_id=_CHAMBER, provider_id=_PROVIDER, sessions=list(sessions),
        )


class TestLatestWinsIsJudgedOnObservationTime(CustodyCentralTestCase):
    def test_a_stale_observation_does_not_overwrite_a_newer_verdict(self):
        """재시도는 순서를 뒤집는다 — 3일 전 관측이 오늘을 덮으면 화면이 과거로 간다."""
        self.store(_session(observed_at='2026-08-09T10:00:00Z', status='verified',
                            counts={'verified': 4, 'missing': 0, 'diverged': 0, 'unknown': 0},
                            findings=[]))
        receipt = self.store(_session(observed_at='2026-08-01T10:00:00Z', status='missing'))

        self.assertEqual(receipt['superseded'], ['S1'])
        self.assertEqual(receipt['accepted'], [])

        self.central.add_session_row('S1', project_id=_PROJECT)
        view = self.service.get_project_custody(_PROJECT)
        self.assertEqual(view['status'], 'verified', '낡은 관측이 새 판정을 덮었다')

    def test_a_newer_observation_replaces_the_verdict(self):
        self.store(_session(observed_at='2026-08-01T10:00:00Z', status='missing'))
        receipt = self.store(_session(
            observed_at='2026-08-09T10:00:00Z', status='verified',
            counts={'verified': 4, 'missing': 0, 'diverged': 0, 'unknown': 0}, findings=[]))
        self.assertEqual(receipt['accepted'], ['S1'])
        self.assertEqual(receipt['superseded'], [])

        self.central.add_session_row('S1', project_id=_PROJECT)
        self.assertEqual(self.service.get_project_custody(_PROJECT)['status'], 'verified')

    def test_superseded_is_reported_not_silently_dropped(self):
        """조용히 성공으로 접으면 노드는 저장됐다고 믿고 중앙은 옛 판정을 갖는다."""
        self.store(_session(observed_at='2026-08-09T10:00:00Z'))
        receipt = self.store(_session(observed_at='2026-08-08T10:00:00Z'))
        self.assertIn('S1', receipt['superseded'])


class TestFindingsLifecycle(CustodyCentralTestCase):
    def test_superseded_report_leaves_the_newer_findings_alone(self):
        """밀려난 보고가 findings 를 지우면 카운터는 최신인데 목록이 빈다."""
        self.store(_session(observed_at='2026-08-09T10:00:00Z', findings=[
            {'relative_path': 'new/plot.png', 'status': 'missing'},
        ]))
        self.assertEqual(self.central.finding_count(), 1)
        self.store(_session(observed_at='2026-08-01T10:00:00Z', findings=[
            {'relative_path': 'old/plot.png', 'status': 'missing'},
        ]))
        self.assertEqual(self.central.finding_count(), 1, 'findings 가 밀려난 보고에 지워졌다')

        self.central.add_session_row('S1', project_id=_PROJECT)
        view = self.service.get_project_custody(_PROJECT)
        detail = self.service.get_snapshot(_PROJECT, view['sessions'][0]['snapshot_id'])
        self.assertEqual(detail['findings'][0]['relative_path'], 'new/plot.png')

    def test_accepted_report_replaces_findings_wholesale(self):
        """부분 병합이면 이미 옮긴 파일이 목록에 남아 시험원이 없는 일을 한다."""
        self.store(_session(observed_at='2026-08-01T10:00:00Z', findings=[
            {'relative_path': 'a.png', 'status': 'missing'},
            {'relative_path': 'b.png', 'status': 'missing'},
        ]))
        self.store(_session(observed_at='2026-08-09T10:00:00Z', findings=[
            {'relative_path': 'b.png', 'status': 'missing'},
        ]))
        self.central.add_session_row('S1', project_id=_PROJECT)
        view = self.service.get_project_custody(_PROJECT)
        detail = self.service.get_snapshot(_PROJECT, view['sessions'][0]['snapshot_id'])
        self.assertEqual([f['relative_path'] for f in detail['findings']], ['b.png'])


class TestProjectAttributionIsANaturalKeyJoin(CustodyCentralTestCase):
    def test_a_snapshot_appears_once_its_session_gets_a_project(self):
        """조인이므로 **백필 없이** 따라온다 — FK 였다면 아무도 재작성하지 않았을 것이다."""
        self.store(_session())
        self.central.add_session_row('S1', project_id=None)
        self.assertEqual(self.service.get_project_custody(_PROJECT)['session_count'], 0)

        self.central.set_project('S1', _PROJECT)
        view = self.service.get_project_custody(_PROJECT)
        self.assertEqual(view['session_count'], 1, 'project_id 해소가 반영되지 않았다')

    def test_unattributed_snapshots_are_counted_not_silently_dropped(self):
        self.store(_session('S1'), _session('S2'))
        self.central.add_session_row('S1', project_id=_PROJECT)
        view = self.service.get_project_custody(_PROJECT)
        self.assertEqual(view['session_count'], 1)
        self.assertGreaterEqual(
            view['unresolved_session_count'], 1,
            '귀속되지 않은 스냅샷이 조용히 빠졌다 — "이상 없음"과 "볼 수 없음"이 같아 보인다',
        )

    def test_the_join_uses_the_chamber_axis_too(self):
        """로컬 세션 id 는 노드마다 1부터다 — chamber 를 빼면 판정이 남의 세션에 붙는다."""
        self.service.store_report(
            chamber_id='chamber-b', provider_id=_PROVIDER, sessions=[_session('S1')],
        )
        self.central.add_session_row('S1', project_id=_PROJECT, chamber_id=_CHAMBER)
        self.assertEqual(
            self.service.get_project_custody(_PROJECT)['session_count'], 0,
            'chamber-b 의 판정이 chamber-a 의 세션에 붙었다',
        )

    def test_missing_snapshot_count_is_distinct_and_rolls_status_to_unknown(self):
        """4 verified snapshots + 5 project sessions means one missing report."""
        for index in range(1, 5):
            self.store(_session(
                f'S{index}',
                status='verified',
                counts={'verified': 4, 'missing': 0, 'diverged': 0, 'unknown': 0},
                findings=[],
                observed_at=f'2026-08-09T10:0{index}:00Z',
            ))
            self.central.add_session_row(f'S{index}', project_id=_PROJECT)
        self.central.add_session_row('S5', project_id=_PROJECT)

        view = self.service.get_project_custody(_PROJECT)

        self.assertEqual(view['session_count'], 4)
        self.assertEqual(view['missing_snapshot_session_count'], 1)
        self.assertEqual(view['unresolved_session_count'], 0)
        self.assertEqual(view['status'], 'unknown')

    def test_missing_snapshot_count_is_project_scoped(self):
        self.central.add_session_row('other-project-session', project_id=_OTHER_PROJECT)
        view = self.service.get_project_custody(_PROJECT)
        self.assertEqual(view['missing_snapshot_session_count'], 0)

    def test_existing_missing_status_precedes_missing_snapshot_unknown(self):
        self.store(_session('S1'))
        self.central.add_session_row('S1', project_id=_PROJECT)
        self.central.add_session_row('S2', project_id=_PROJECT)
        view = self.service.get_project_custody(_PROJECT)
        self.assertEqual(view['missing_snapshot_session_count'], 1)
        self.assertEqual(view['status'], 'missing')


class TestSnapshotDetailRespectsTheProjectBoundary(CustodyCentralTestCase):
    def test_another_projects_snapshot_is_not_found(self):
        self.store(_session())
        self.central.add_session_row('S1', project_id=_PROJECT)
        snapshot_id = self.service.get_project_custody(_PROJECT)['sessions'][0]['snapshot_id']
        with self.assertRaises(ArtifactCustodyNotFoundError):
            self.service.get_snapshot(_OTHER_PROJECT, snapshot_id)


class TestVocabularyIsValidatedAtTheBoundary(CustodyCentralTestCase):
    """미검증이면 DB CHECK 위반이 드라이버 예외가 되고 503 으로 나간다 —
    클라이언트 잘못이 중앙 장애로 둔갑한다(Platform Boundary Honesty)."""

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(ArtifactCustodyReportRejected):
            self.store(_session(status='probably-fine'))

    def test_verified_is_not_an_actionable_finding_status(self):
        with self.assertRaises(ArtifactCustodyReportRejected):
            self.store(_session(findings=[
                {'relative_path': 'a.png', 'status': 'verified'},
            ]))

    def test_unparseable_observed_at_is_rejected(self):
        """latest-wins 비교가 그 값으로 도는데 파싱 불가면 비교가 성립하지 않는다."""
        with self.assertRaises(ArtifactCustodyReportRejected):
            self.store(_session(observed_at='어제쯤'))

    def test_a_finding_without_an_address_is_rejected(self):
        with self.assertRaises(ArtifactCustodyReportRejected):
            self.store(_session(findings=[{'status': 'missing'}]))

    def test_unknown_count_keys_are_rejected(self):
        with self.assertRaises(ArtifactCustodyReportRejected):
            self.store(_session(counts={'verified': 1, 'sortof': 2}))


class TestCountValuesAreValidatedNotJustTheirKeys(CustodyCentralTestCase):
    """독립 리뷰(2026-08-09) 지적 — 키만 보고 값을 안 보면 숫자가 조용히 틀린다."""

    def test_a_negative_count_cannot_cancel_a_real_one(self):
        """음수 하나가 다른 세션의 진짜 개수를 상쇄해 프로젝트 배지를 뒤집는다 —
        이 저장소의 서명 결함 부류(파이프라인은 완주하고 숫자만 틀림)."""
        with self.assertRaises(ArtifactCustodyReportRejected):
            self.store(_session('S2', status='verified', findings=[], counts={
                'verified': 5, 'missing': -3, 'diverged': 0, 'unknown': 0,
            }))

    def test_a_non_integer_count_is_rejected_at_the_boundary(self):
        """미검증이면 TypeError 가 500 으로 나간다 — 클라이언트 잘못이 서버 장애로."""
        with self.assertRaises(ArtifactCustodyReportRejected):
            self.store(_session(counts={'verified': {'a': 1}, 'missing': 1,
                                        'diverged': 0, 'unknown': 0}))

    def test_an_out_of_range_count_is_rejected_at_the_boundary(self):
        """PG integer 범위를 넘으면 DB 가 거절해 503(중앙 장애)으로 둔갑한다."""
        with self.assertRaises(ArtifactCustodyReportRejected):
            self.store(_session(counts={'verified': 99_999_999_999, 'missing': 1,
                                        'diverged': 0, 'unknown': 0}))

    def test_a_boolean_is_not_an_integer_here(self):
        with self.assertRaises(ArtifactCustodyReportRejected):
            self.store(_session(counts={'verified': True, 'missing': 1,
                                        'diverged': 0, 'unknown': 0}))


class TestDeclaredStatusMustAgreeWithTheCounts(CustodyCentralTestCase):
    """한 행이 초록 배지와 '발행 차단'을 동시에 보여주면 안 된다."""

    def test_a_row_cannot_be_both_verified_and_blocking(self):
        with self.assertRaises(ArtifactCustodyReportRejected):
            self.store(_session(status='verified', counts={
                'verified': 1, 'missing': 1, 'diverged': 0, 'unknown': 0,
            }))

    def test_an_agreeing_report_is_accepted(self):
        """비-공허성 — 정상 조합은 그대로 통과한다."""
        receipt = self.store(_session(status='missing', counts={
            'verified': 3, 'missing': 1, 'diverged': 0, 'unknown': 0,
        }))
        self.assertEqual(receipt['accepted'], ['S1'])


class TestUnresolvedCountIsScopedToTheProject(CustodyCentralTestCase):
    """전역 개수를 프로젝트 숫자로 렌더하면 이 필드가 지키려던 구분이 무너진다."""

    def test_another_providers_unattributed_snapshots_are_not_counted(self):
        self.store(_session('S1'))
        self.central.add_session_row('S1', project_id=_PROJECT)
        before = self.service.get_project_custody(_PROJECT)['unresolved_session_count']

        # 다른 provider 가 귀속되지 않은 스냅샷을 잔뜩 보고한다.
        other = CentralArtifactCustodyService(
            read_port=PostgresCentralArtifactCustodyReadAdapter(self.central.factory),
            write_port=PostgresCentralArtifactCustodyWriteAdapter(self.central.factory),
        )
        other.store_report(
            chamber_id='chamber-z', provider_id='99999999-9999-9999-9999-999999999999',
            sessions=[_session(f'X{i}') for i in range(7)],
        )
        after = self.service.get_project_custody(_PROJECT)['unresolved_session_count']
        self.assertEqual(
            after, before,
            '다른 provider 의 미귀속 스냅샷이 이 프로젝트 숫자로 새어 들어왔다 — '
            '시험원이 해소할 수 없는 건에 대해 영구히 안내를 보게 된다',
        )

    def test_our_own_unattributed_snapshot_is_still_counted(self):
        """비-공허성 — 좁히기가 정당한 값까지 지우지 않는다."""
        self.store(_session('S1'), _session('S2'))
        self.central.add_session_row('S1', project_id=_PROJECT)
        self.assertGreaterEqual(
            self.service.get_project_custody(_PROJECT)['unresolved_session_count'], 1,
        )


class TestCentralNeverJudges(unittest.TestCase):
    """중앙이 파일을 열면 이 축의 전제(원본이 권위, 판정은 노드)가 무너진다."""

    _MODULES = (
        'application/platform/central_artifact_custody_service.py',
        'application/platform/central_artifact_custody_read_adapter.py',
        'application/platform/central_artifact_custody_write_adapter.py',
    )
    _FORBIDDEN = {'os', 'pathlib', 'hashlib', 'shutil', 'glob'}

    def test_no_filesystem_access_in_the_central_custody_modules(self):
        src = Path(__file__).resolve().parents[1] / 'src'
        for relative in self._MODULES:
            with self.subTest(module=relative):
                tree = ast.parse((src / relative).read_text(encoding='utf-8'))
                imported = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported.update(a.name.split('.')[0] for a in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imported.add(node.module.split('.')[0])
                leaked = imported & self._FORBIDDEN
                self.assertFalse(
                    leaked,
                    f'{relative} 가 파일시스템 모듈을 import 한다: {sorted(leaked)} — '
                    '중앙은 원본 보관소를 열 수 없고 판정하지도 않는다',
                )


class TestTheNodeScopedOperationsAreBoundToTheirChamber(unittest.TestCase):
    """독립 리뷰(2026-08-09) 지적 — 두 노드-스코프 operation 의 바인딩에 봉인이 **없었다**.

    코드는 오늘 맞지만, 제거해도 전부 green 이었다. 한 방의 머신 토큰이 다른 방의 행을
    읽거나 다른 방 세션에 보관 판정을 심는 것을 막는 가드라, 리팩터 한 번에 조용히
    사라질 수 있었다.
    """

    def _adapter(self, *, chamber_id='chamber-a', **collaborators):
        from fcc_test_contracts.common.access_policy import ApiAccessPolicy, ApiPrincipal
        from application.central_contract.api_contracts import PLATFORM_API_OPERATIONS
        from fcc_test_platform.api.platform_routes import PlatformApiAdapter

        return PlatformApiAdapter(
            None,
            access_policy=ApiAccessPolicy(PLATFORM_API_OPERATIONS),
            principal=(
                ApiPrincipal.from_permissions(
                    'node-a', ['platform:chamber'], chamber_id=chamber_id,
                )
                if chamber_id
                else ApiPrincipal.from_permissions('node-a', ['platform:chamber'])
            ),
            **collaborators,
        )

    def test_a_report_for_another_chamber_is_refused_on_the_path_axis(self):
        from fcc_test_platform.api.platform_routes import (
            PlatformAuthorizationError,
        )

        adapter = self._adapter(artifact_custody_service=object())
        with self.assertRaises(PlatformAuthorizationError):
            adapter.push_artifact_custody_report(
                'chamber-b', {'chamber_id': 'chamber-b', 'provider_id': 'p', 'sessions': []},
            )

    def test_a_report_whose_envelope_names_another_chamber_is_refused(self):
        """경로만 묶고 봉투를 안 묶으면 한 챔버 토큰으로 다른 챔버 세션에 판정을 심는다."""
        from fcc_test_platform.api.platform_routes import (
            PlatformAuthorizationError,
        )

        adapter = self._adapter(artifact_custody_service=object())
        with self.assertRaises(PlatformAuthorizationError):
            adapter.push_artifact_custody_report(
                'chamber-a', {'chamber_id': 'chamber-b', 'provider_id': 'p', 'sessions': []},
            )

    def test_a_claimless_chamber_token_cannot_report(self):
        from fcc_test_platform.api.platform_routes import (
            PlatformAuthorizationError,
        )

        adapter = self._adapter(chamber_id='', artifact_custody_service=object())
        with self.assertRaises(PlatformAuthorizationError):
            adapter.push_artifact_custody_report(
                'chamber-a', {'chamber_id': 'chamber-a', 'provider_id': 'p', 'sessions': []},
            )

    def test_a_node_cannot_read_another_chambers_settings(self):
        from fcc_test_platform.api.platform_routes import (
            PlatformAuthorizationError,
        )

        adapter = self._adapter(chamber_write_service=object())
        with self.assertRaises(PlatformAuthorizationError):
            adapter.get_chamber_settings('chamber-b')

    def test_a_claimless_chamber_token_cannot_read_settings(self):
        from fcc_test_platform.api.platform_routes import (
            PlatformAuthorizationError,
        )

        adapter = self._adapter(chamber_id='', chamber_write_service=object())
        with self.assertRaises(PlatformAuthorizationError):
            adapter.get_chamber_settings('chamber-a')

    def test_the_bound_chamber_reaches_the_service(self):
        """비-공허성 — 정당한 토큰은 통과해서 서비스에 도달한다."""
        class _Service:
            @staticmethod
            def get_settings(chamber_id):
                return {'chamber_id': chamber_id, 'artifact_storage_root': r'\\fs\fcc'}

        adapter = self._adapter(chamber_write_service=_Service())
        self.assertEqual(
            adapter.get_chamber_settings('chamber-a')['artifact_storage_root'],
            r'\\fs\fcc',
        )


class TestRequestScopedProjectionIsDerived(unittest.TestCase):
    """``with_principal`` 이 협력자를 떨어뜨리면 **인증이 켜진 배포에서만** 사라진다.

    어댑터를 직접 만드는 테스트는 전부 green 이므로 아무것도 잡지 못한다 — 세션
    어댑터에서 ``allowed_storage_roots`` 가 정확히 그렇게 사라졌다.
    """

    def test_every_constructor_kwarg_is_projected(self):
        import inspect

        from fcc_test_platform.api.platform_routes import PlatformApiAdapter

        signature = inspect.signature(PlatformApiAdapter.__init__)
        kwargs = {
            name for name, param in signature.parameters.items()
            if name not in ('self',) and param.kind is not param.VAR_KEYWORD
        }
        adapter = PlatformApiAdapter(None)
        projected = set(adapter._request_scoped_state())
        missing = kwargs - projected
        self.assertFalse(
            missing,
            f'생성자 kwarg 가 요청 스코프 투영에서 빠졌다: {sorted(missing)} — '
            '인증이 켜진 배포에서만 조용히 사라진다',
        )

    def test_the_projection_actually_survives_with_principal(self):
        """파생 선언이 아니라 **결과**를 본다 — 투영 dict 만 보면 생성자가 그것을
        무시해도 green 이다."""
        from fcc_test_platform.api.platform_routes import PlatformApiAdapter

        sentinel = object()
        adapter = PlatformApiAdapter(None, artifact_custody_service=sentinel)
        self.assertIs(adapter.with_principal(None)._artifact_custody_service, sentinel)

    def test_provisioning_dedup_is_not_shared_across_requests(self):
        from fcc_test_platform.api.platform_routes import PlatformApiAdapter

        adapter = PlatformApiAdapter(None)
        first = adapter.with_principal(None)
        second = adapter.with_principal(None)
        self.assertIsNot(first._provisioning_dedup, second._provisioning_dedup)


if __name__ == '__main__':
    unittest.main()
