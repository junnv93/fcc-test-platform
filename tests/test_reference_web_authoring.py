"""웹에서의 참조값 저작 (2026-08-09) — fork + 후보 엔트리 편집.

참조 데이터 소유권 이전은 **읽기·검토·게시**까지 와 있었고 **저작**에서 멈춰 있었다.
후보를 만드는 유일한 방법이 운영자 CLI 였으므로, 시험원이 재배선 후 케이블 손실을
다시 재도 운영자를 기다려야 했고 그 동안 워크북이 계속 권위였다.

이 파일이 잠그는 것은 두 operation 의 **거부**들이다. 성공 경로는 한 번 보면
알지만, 이 표면에서 값을 치르는 것은 거부하지 않았을 때다:

* 모르는 행을 조용히 건너뛰면 화면은 "저장됨"이라고 말하고 시험원의 숫자는
  아무 데도 가지 않는다.
* payload 모양이 다르면 측정 경로가 읽는 테이블에 망가진 행이 투영된다.
* 식별 필드가 움직이면 따로 저장된 `identity_key` 가 없는 행을 가리킨다.
* 낡은 etag 를 통과시키면 두 시험원 중 한 명의 숫자가 조용히 사라진다.

DB 는 중앙 스키마 SSOT 에서 파생한 실 SQLite 다(컬럼 목록을 여기 적으면 그것이
두 번째 스키마가 된다). 서비스·write 어댑터·read 어댑터를 전부 실물로 통과시키므로,
"조각은 다 되는데 연결이 안 된" 형태가 green 으로 남을 수 없다.
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / 'src'
for _path in (str(_REPO_ROOT), str(_SRC)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402
from tests._moved_module_source import moved_module_source  # noqa: E402
from tests._layer_of_import import imported_layers  # noqa: E402


def _repo_source(rel: str) -> str:
    """Source text of a repository-relative module, wherever this tree put it.

    These are AST/text audits of production modules, so they have to *find* the
    file. This suite ships in the delivered platform box, where the packager
    relocates ``src/application/platform/x.py`` to ``fcc_test_platform/x.py`` —
    a hard-coded ``_SRC / …`` join names a file that is not there. In the
    monorepo there is no layout record and the answer is the same join as before.
    """
    return resolve_repo_artifact(__file__, rel).read_text(encoding='utf-8')


def _kernel_source(dotted: str) -> str:
    """공유 커널로 간 모듈의 원본 — **경로가 아니라 모듈에게 묻는다.**

    ⚠️ 이 저장소가 이미 한 번 지불한 판정이다(`tests/_moved_module_source.py`):
    경로를 하드코딩한 검사는 *트리*에 대해 단언하지 검사하려는 *코드*에 대해
    단언하지 않는다 — 레인이 갈라진 뒤 그 둘은 같은 것이기를 그만두었다.
    """
    return moved_module_source(dotted).read_text(encoding='utf-8')

from fcc_test_platform.application.central_reference_read_adapter import (  # noqa: E402
    PostgresCentralReferenceReadAdapter,
)
from fcc_test_platform.application.central_reference_service import (  # noqa: E402
    CentralReferenceService,
)
from fcc_test_platform.application.central_reference_write_adapter import (  # noqa: E402
    PostgresCentralReferenceWriteAdapter,
)
from fcc_test_kernel.domain.models.reference_catalog import (  # noqa: E402
    CatalogFamily,
    RevisionProvenanceKind,
    RevisionState,
)
from domain.ports.output.central_reference_port import (  # noqa: E402
    ReferencePublishConflictError,
    ReferenceRevisionNotFoundError,
    ReferenceStateConflictError,
)
from fcc_test_kernel.domain.services.reference_entry_edit_policy import (  # noqa: E402
    EntryEdit,
    ReferenceEntryEditError,
    ReferenceEntryPayloadValueError,
    apply_entry_edits,
)
from domain.services.reference_hashing import (  # noqa: E402
    build_reference_entry_hash,
)
from domain.services.reference_row_edit_policy import (  # noqa: E402
    ReferenceRowEditError,
)
from fcc_test_kernel.domain.services.reference_ownership_policy import (  # noqa: E402
    identity_fields_for,
    identity_key_for,
    projection_fields_for,
    projection_value_kinds_for,
)
from fcc_test_kernel.domain.services.reference_scope_policy import ReferenceScopeError  # noqa: E402
from tests.support.central_pg_sqlite_shim import QmarkConnection  # noqa: E402

_SCHEMA = _REPO_ROOT / 'docs' / 'platform' / 'central_db_schema.v1.json'
_PROVIDER = 'fcc-unlicensed-conducted'
_FAMILY = CatalogFamily.CORRECTION


def _sqlite_type(declared: str) -> str:
    return {
        'uuid': 'TEXT', 'text': 'TEXT', 'integer': 'INTEGER',
        'timestamp': 'TEXT', 'boolean': 'INTEGER', 'json': 'TEXT',
    }.get(declared, 'TEXT')


#: The central DDL owns id and the timestamps (migration 011), so the adapters'
#: INSERTs deliberately omit them. A shim that dropped the DEFAULTs would leave
#: `id` NULL and the RETURNING clause would hand back nothing — which looks like
#: a broken adapter and is really a broken fixture.
_SQLITE_DEFAULTS = {
    'gen_random_uuid()': "(lower(hex(randomblob(16))))",
    'now()': 'CURRENT_TIMESTAMP',
}


def _create_table_sql(table: str, columns: dict) -> str:
    parts = []
    for name, spec in columns.items():
        clause = f'"{name}" {_sqlite_type(spec.get("type", "text"))}'
        default = spec.get('default')
        if default is not None:
            clause += f' DEFAULT {_SQLITE_DEFAULTS.get(default, default)}'
        parts.append(clause)
    return f'CREATE TABLE "{table}" ({", ".join(parts)})'


def _payload(family: CatalogFamily, row: int, *, value: float = 1.5) -> dict:
    """A payload whose key set IS the family's runtime row, by construction.

    Built from ``projection_fields_for`` rather than from a hand-written dict:
    a literal column list here would be a second copy of the contract, and the
    test would keep passing while the real one moved.
    """
    identity = set(identity_fields_for(family)[0])
    fields = projection_fields_for(family)
    kinds = projection_value_kinds_for(family)
    return {
        field: (
            row if field in identity and kind == 'number'
            else f'{field}-{row}' if field in identity
            else value if kind == 'number'
            else f'{field}-{row}'
        )
        for field, kind in zip(fields, kinds)
    }


def _value_field(family: CatalogFamily) -> str:
    """A payload field that is NOT part of the identity — i.e. one a tester edits."""
    identity = set(identity_fields_for(family)[0])
    return next(f for f in projection_fields_for(family) if f not in identity)


class _AuthoringConnection(QmarkConnection):
    """``QmarkConnection`` that answers ``now()``.

    The central DDL stamps ``updated_at`` with ``now()`` (migration 011 moved
    that ownership to the DB deliberately). SQLite has no such function, so the
    shim registers one rather than letting the production statement be rewritten
    for the test — a statement edited to suit the harness is a statement the
    harness is no longer checking.
    """

    def __init__(self, db_path: str) -> None:
        super().__init__(db_path)
        self._conn.create_function(
            'now', 0,
            lambda: datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        )


class _CentralFixture:
    """A real central DB with one published correction revision."""

    def __init__(self) -> None:
        schema = json.loads(_SCHEMA.read_text(encoding='utf-8'))
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / 'central.sqlite3')
        connection = _AuthoringConnection(self.db_path)
        try:
            cursor = connection.cursor()
            for table in ('providers', 'reference_revisions', 'reference_entries'):
                cursor.execute(
                    _create_table_sql(table, schema['tables'][table]['columns'])
                )
            cursor.execute(
                'INSERT INTO "providers" ("id", "provider_id") VALUES (?, ?)',
                (uuid.uuid4().hex, _PROVIDER),
            )
            cursor.close()
            connection.commit()
        finally:
            connection.close()

        factory = lambda: _AuthoringConnection(self.db_path)  # noqa: E731
        self.read = PostgresCentralReferenceReadAdapter(factory)
        self.write = PostgresCentralReferenceWriteAdapter(factory)
        self.service = CentralReferenceService(
            self.read, self.write, bundle_provider_id=_PROVIDER,
        )

    def register_provider(self, provider_id: str) -> str:
        """Register a second provider centrally.

        Cross-provider isolation needs the OTHER provider to exist: since
        2026-08-25 every reference operation resolves the provider first, so an
        unregistered id is refused before ownership is ever considered — and a
        test that named a fictional provider would assert isolation while
        actually exercising the registration refusal.
        """
        connection = _AuthoringConnection(self.db_path)
        try:
            cursor = connection.cursor()
            cursor.execute(
                'INSERT INTO "providers" ("id", "provider_id") VALUES (?, ?)',
                (uuid.uuid4().hex, provider_id),
            )
            cursor.close()
            connection.commit()
        finally:
            connection.close()
        return provider_id

    def cleanup(self) -> None:
        self._tmp.cleanup()

    def _create_candidate(self, family: CatalogFamily, rows: int = 3) -> str:
        created = self.service.create_candidate(
            _PROVIDER,
            request={
                'family': family.value,
                'profile_id': 'default',
                'scope_kind': 'room',
                'scope_id': 'room-1',
                'source_snapshot_id': 'snap-1',
                'source_manifest_sha256': 'c' * 64,
                'entries': [
                    {
                        'reference_id': f'ref-{i}',
                        'identity_key': f'{family.value}|row={i}',
                        'payload': _payload(family, i, value=1.5 + i),
                        'test_condition_ids': [],
                        'effective_from': None,
                        'effective_to': None,
                        'source_sheet_name': 'sheet',
                        'source_row_number': 10 + i,
                        'content_sha256': f'{i:064d}',
                    }
                    for i in range(rows)
                ],
            },
            created_by='importer',
        )
        return created['revision_id']

    def seed_published(self) -> str:
        """Publish the coupled pair and return the CORRECTION revision id.

        Both halves, because the publish gate refuses a half-published coupled
        group — and refusing it is correct: a correction curve published without
        its switch-port map pairs one antenna's signal path with another's path
        loss. Seeding only one half would have made this fixture argue with the
        invariant instead of exercising it.
        """
        correction_id = self._create_candidate(_FAMILY)
        sibling_id = self._create_candidate(CatalogFamily.SWITCH_PORT_MAPPING)
        self.service.publish(
            _PROVIDER,
            correction_id,
            published_by='tester',
            coupled_revision_id=sibling_id,
        )
        return correction_id


class _CentralTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _CentralFixture()
        self.addCleanup(self.fixture.cleanup)
        self.service = self.fixture.service
        self.published_id = self.fixture.seed_published()


class TestForkMakesTheTesterTheAuthor(_CentralTestCase):
    def test_a_fork_is_a_candidate_that_names_its_parent(self) -> None:
        detail = self.service.fork_published(
            _PROVIDER, self.published_id, forked_by='tester',
        )
        revision = detail['revision']
        self.assertEqual(revision['state'], RevisionState.CANDIDATE.value)
        self.assertEqual(
            revision['forked_from_revision_id'], self.published_id,
        )
        self.assertEqual(revision['created_by'], 'tester')
        self.assertNotEqual(revision['revision_id'], self.published_id)

    def test_the_database_assigns_the_next_revision_number(self) -> None:
        parent = self.service.read_revision(_PROVIDER, self.published_id)['revision']
        child = self.service.fork_published(
            _PROVIDER, self.published_id, forked_by='tester',
        )['revision']
        self.assertEqual(
            child['revision_number'], parent['revision_number'] + 1,
        )

    def test_entries_are_copied_verbatim(self) -> None:
        parent = self.service.read_revision(_PROVIDER, self.published_id)
        child = self.service.fork_published(
            _PROVIDER, self.published_id, forked_by='tester',
        )

        def fingerprint(detail):
            return [
                (e['reference_id'], e['identity_key'], e['payload'],
                 e['content_sha256'])
                for e in detail['entries']
            ]

        self.assertEqual(fingerprint(child), fingerprint(parent))

    def test_an_untouched_copy_still_has_the_workbooks_values(self) -> None:
        """상속이지 FORK_EDIT 스탬프가 아니다 — 없는 사실을 주장하지 않는다."""
        child = self.service.fork_published(
            _PROVIDER, self.published_id, forked_by='tester',
        )['revision']
        self.assertEqual(
            child['provenance_kind'], RevisionProvenanceKind.WORKBOOK.value,
        )

    def test_only_a_published_revision_may_be_forked(self) -> None:
        candidate_id = self.service.fork_published(
            _PROVIDER, self.published_id, forked_by='tester',
        )['revision']['revision_id']
        with self.assertRaises(ReferenceStateConflictError):
            self.service.fork_published(
                _PROVIDER, candidate_id, forked_by='tester',
            )

    def test_a_retired_revision_may_not_be_forked_either(self) -> None:
        """RETIRED 도 PUBLISHED 가 아니다 — 판정이 상태 일반적임을 실증한다.

        `state != PUBLISHED` 라는 코드 한 줄이 두 상태를 다 덮는다는 것은 읽으면
        보이지만, 읽어서 보이는 것과 실행해서 보이는 것은 다르다. 승계가 실제로
        RETIRED 를 만들어내므로 이 상태는 이제 도달 가능하다.
        """
        candidate_id = self.service.fork_published(
            _PROVIDER, self.published_id, forked_by='tester',
        )['revision']['revision_id']
        sibling_id = self.fixture._create_candidate(  # noqa: SLF001
            CatalogFamily.SWITCH_PORT_MAPPING
        )
        # 게시가 부모를 승계 → 부모가 RETIRED 가 된다.
        self.service.publish(
            _PROVIDER,
            candidate_id, published_by='tester', coupled_revision_id=sibling_id,
        )
        retired = self.service.read_revision(_PROVIDER, self.published_id)['revision']
        self.assertEqual(retired['state'], RevisionState.RETIRED.value)

        with self.assertRaises(ReferenceStateConflictError):
            self.service.fork_published(
                _PROVIDER, self.published_id, forked_by='tester',
            )

    def test_a_retired_revision_may_not_be_edited_either(self) -> None:
        candidate_id = self.service.fork_published(
            _PROVIDER, self.published_id, forked_by='tester',
        )['revision']['revision_id']
        sibling_id = self.fixture._create_candidate(  # noqa: SLF001
            CatalogFamily.SWITCH_PORT_MAPPING
        )
        parent_etag = self.service.read_revision(_PROVIDER,
            self.published_id
        )['revision']['etag']
        self.service.publish(
            _PROVIDER,
            candidate_id, published_by='tester', coupled_revision_id=sibling_id,
        )
        with self.assertRaises(ReferenceStateConflictError):
            self.service.update_candidate_entries(
                _PROVIDER, self.published_id,
                request={
                    'expected_etag': parent_etag,
                    'edits': [{
                        'reference_id': 'ref-0',
                        'payload': _payload(_FAMILY, 0, value=9.75),
                    }],
                },
                updated_by='tester',
            )

    def test_the_state_refusal_is_not_the_publish_slot_refusal(self) -> None:
        """같은 409 라도 사실이 다르다 — 접으면 문장을 파싱해야 구분된다."""
        self.assertFalse(
            issubclass(ReferenceStateConflictError, ReferencePublishConflictError)
        )
        self.assertFalse(
            issubclass(ReferencePublishConflictError, ReferenceStateConflictError)
        )

    def test_another_providers_revision_is_answered_as_absent(self) -> None:
        other = self.fixture.register_provider('some-other-provider')
        with self.assertRaises(ReferenceRevisionNotFoundError):
            self.service.fork_published(
                other, self.published_id, forked_by='tester',
            )

    def test_fork_adds_no_new_insert_statement(self) -> None:
        """기존 `_REVISION_INSERT` 재사용 — 두 번째 INSERT 는 두 번째 진실이다."""
        source = _repo_source(
            'src/application/platform/central_reference_write_adapter.py'
        )
        self.assertEqual(
            source.count('INSERT INTO "reference_revisions"'), 1,
        )
        self.assertEqual(
            source.count('INSERT INTO "reference_entries"'), 1,
        )


class TestPublishingSupersedesRatherThanDeadEnding(_CentralTestCase):
    """게시가 승계하지 않으면 이 표면 전체가 마지막 걸음에서 막힌다.

    부분 unique 인덱스는 정체성당 PUBLISHED 를 **하나만** 허용하는데, 중앙에는
    RETIRE operation 이 없다. 그래서 승계 이전의 두 번째 게시는 영원히 409 였고,
    거부 메시지는 *"retire it first"* 라며 **존재하지 않는 operation** 을 안내했다.
    fork → 편집 → 게시에서 마지막 화살표가 아무 데도 가지 않았다는 뜻이다.

    별도 retire 호출이 답이 아닌 이유가 이 클래스의 두 번째 테스트다: 두 요청
    사이에 그 정체성은 게시본이 **0** 이 되고, `resolve_lookup_ownership` 은 바로
    그 공집합을 "이 패밀리는 워크북 소유"로 읽는다 — 그 틈에 부팅한 챔버는
    시험원이 새 값이 살아 있다고 믿는 동안 워크북 숫자로 측정한다.
    """

    def _fork_edit_publish(self):
        detail = self.service.fork_published(
            _PROVIDER, self.published_id, forked_by='tester',
        )
        candidate_id = detail['revision']['revision_id']
        self.service.update_candidate_entries(
            _PROVIDER, candidate_id,
            request={
                'expected_etag': detail['revision']['etag'],
                'edits': [{
                    'reference_id': 'ref-0',
                    'payload': _payload(_FAMILY, 0, value=42.0),
                }],
            },
            updated_by='tester',
        )
        sibling_id = self.fixture._create_candidate(  # noqa: SLF001
            CatalogFamily.SWITCH_PORT_MAPPING
        )
        self.service.publish(
            _PROVIDER,
            candidate_id, published_by='tester', coupled_revision_id=sibling_id,
        )
        return candidate_id

    def test_the_tester_can_finish_the_loop(self) -> None:
        candidate_id = self._fork_edit_publish()
        published = self.service.read_revision(_PROVIDER, candidate_id)['revision']
        self.assertEqual(published['state'], RevisionState.PUBLISHED.value)

    def test_exactly_one_revision_is_published_at_every_moment(self) -> None:
        candidate_id = self._fork_edit_publish()
        rows, _ = self.service.list_revisions(
            _PROVIDER, family=_FAMILY.value,
            state=RevisionState.PUBLISHED.value,
        )
        self.assertEqual(
            [row['revision_id'] for row in rows], [candidate_id],
            'the identity must never hold two published revisions, and never '
            'zero — an empty published set is read as "workbook-owned"',
        )

    def test_the_superseded_parent_says_what_replaced_it(self) -> None:
        candidate_id = self._fork_edit_publish()
        parent = self.service.read_revision(_PROVIDER, self.published_id)['revision']
        self.assertEqual(parent['state'], RevisionState.RETIRED.value)
        self.assertIn(candidate_id, parent['retirement_reason'])
        self.assertEqual(parent['retired_by'], 'tester')

    def test_the_published_values_are_the_edited_ones(self) -> None:
        candidate_id = self._fork_edit_publish()
        entries = self.service.read_revision(_PROVIDER, candidate_id)['entries']
        edited = next(e for e in entries if e['reference_id'] == 'ref-0')
        self.assertEqual(edited['payload'][_value_field(_FAMILY)], 42.0)

    def test_supersession_does_not_reach_another_provider(self) -> None:
        """같은 family·profile·scope 를 쓰는 **다른 provider** 는 건드리지 않는다.

        봉인 공백으로 발견됐다(2026-08-09 적대적 리뷰): `_SUPERSEDE_UPDATE` 의
        `provider_id` 상관관계를 지워도 165개 테스트가 전부 green 이었다. 방 축은
        `test_supersession_does_not_reach_another_room` 이 덮고 있었지만 provider
        축은 아무도 보지 않았고, 그 회귀는 **다른 제품군의 게시본을 조용히 은퇴**
        시킨다 — 그 provider 의 챔버는 다음 세션에 워크북으로 떨어진다.

        provider 는 같은 정체성 문자열을 자연스럽게 공유한다(모든 provider 가
        `correction`/`default`/방 이름을 쓴다). 그래서 이것은 억지 시나리오가 아니라
        기본 형상이다.
        """
        other_provider = 'fcc-mmwave-conducted'
        connection = _AuthoringConnection(self.fixture.db_path)
        try:
            cursor = connection.cursor()
            cursor.execute(
                'INSERT INTO "providers" ("id", "provider_id") VALUES (?, ?)',
                (uuid.uuid4().hex, other_provider),
            )
            cursor.close()
            connection.commit()
        finally:
            connection.close()

        # 같은 family/profile/scope 를 쓰는 두 번째 provider 의 게시본.
        saved = _PROVIDER
        try:
            globals()['_PROVIDER'] = other_provider
            foreign = self.fixture._create_candidate(_FAMILY)  # noqa: SLF001
            foreign_sibling = self.fixture._create_candidate(  # noqa: SLF001
                CatalogFamily.SWITCH_PORT_MAPPING
            )
            self.service.publish(
                other_provider, foreign,
                published_by='other-provider-tester',
                coupled_revision_id=foreign_sibling,
            )
        finally:
            globals()['_PROVIDER'] = saved

        self._fork_edit_publish()

        untouched = self.service.read_revision(
            other_provider, foreign,
        )['revision']
        self.assertEqual(
            untouched['state'], RevisionState.PUBLISHED.value,
            'a publish under one provider retired another provider\'s '
            'published revision — that provider\'s chambers would fall back '
            'to the workbook on their next session',
        )

    def test_supersession_does_not_reach_another_room(self) -> None:
        """승계 대상은 **같은 정체성**뿐이다 — 아니면 옆 방이 워크북으로 떨어진다."""
        other = self.fixture._create_candidate(_FAMILY)  # noqa: SLF001
        other_sibling = self.fixture._create_candidate(  # noqa: SLF001
            CatalogFamily.SWITCH_PORT_MAPPING
        )
        # 같은 scope 에서는 게시 슬롯이 하나뿐이므로, 다른 방을 만들어야 한다.
        connection = _AuthoringConnection(self.fixture.db_path)
        try:
            cursor = connection.cursor()
            for revision_id in (other, other_sibling):
                cursor.execute(
                    'UPDATE "reference_revisions" SET "scope_id" = ? '
                    'WHERE "id" = ?',
                    ('room-2', revision_id),
                )
            cursor.close()
            connection.commit()
        finally:
            connection.close()
        self.service.publish(
            _PROVIDER,
            other, published_by='other-tester',
            coupled_revision_id=other_sibling,
        )

        self._fork_edit_publish()

        untouched = self.service.read_revision(_PROVIDER, other)['revision']
        self.assertEqual(untouched['state'], RevisionState.PUBLISHED.value)


class TestEditingACandidate(_CentralTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.detail = self.service.fork_published(
            _PROVIDER, self.published_id, forked_by='tester',
        )
        self.candidate_id = self.detail['revision']['revision_id']
        self.etag = self.detail['revision']['etag']

    def _edit(self, reference_id, payload, *, etag=None, revision_id=None):
        return self.service.update_candidate_entries(
            _PROVIDER,
            revision_id or self.candidate_id,
            request={
                'expected_etag': etag or self.etag,
                'edits': [{'reference_id': reference_id, 'payload': payload}],
            },
            updated_by='tester',
        )

    def test_a_value_change_lands_and_only_that_row_moves(self) -> None:
        before = {e['reference_id']: e['payload'] for e in self.detail['entries']}
        changed = _payload(_FAMILY, 0, value=9.75)
        after = self._edit('ref-0', changed)
        payloads = {e['reference_id']: e['payload'] for e in after['entries']}
        self.assertEqual(payloads['ref-0']['correction_db'], 9.75)
        self.assertEqual(payloads['ref-1'], before['ref-1'])
        self.assertEqual(payloads['ref-2'], before['ref-2'])

    def test_the_server_recomputes_the_entry_digest(self) -> None:
        changed = _payload(_FAMILY, 0, value=9.75)
        after = self._edit('ref-0', changed)
        entry = next(e for e in after['entries'] if e['reference_id'] == 'ref-0')
        self.assertEqual(
            entry['content_sha256'],
            build_reference_entry_hash({
                'identity_key': entry['identity_key'],
                'payload': changed,
                'test_condition_ids': [],
                'effective_from': None,
                'effective_to': None,
            }),
        )

    def test_an_edit_promotes_provenance(self) -> None:
        after = self._edit('ref-0', _payload(_FAMILY, 0, value=9.75))
        self.assertEqual(
            after['revision']['provenance_kind'],
            RevisionProvenanceKind.FORK_EDIT.value,
        )

    def test_the_lattice_is_monotone_across_a_second_fork(self) -> None:
        edited = self._edit('ref-0', _payload(_FAMILY, 0, value=9.75))
        # 결합 그룹이라 형제도 함께 게시해야 한다 — fork 는 요청한 것만 만들고,
        # 강제 지점은 게시다(그것이 이 설계의 의도이므로 여기서도 그대로 따른다).
        sibling_id = self.fixture._create_candidate(  # noqa: SLF001
            CatalogFamily.SWITCH_PORT_MAPPING
        )
        self.service.publish(
            _PROVIDER,
            self.candidate_id,
            published_by='tester',
            coupled_revision_id=sibling_id,
        )
        grandchild = self.service.fork_published(
            _PROVIDER, self.candidate_id, forked_by='tester',
        )['revision']
        self.assertEqual(
            edited['revision']['provenance_kind'],
            RevisionProvenanceKind.FORK_EDIT.value,
        )
        self.assertEqual(
            grandchild['provenance_kind'],
            RevisionProvenanceKind.FORK_EDIT.value,
            'a fork of an edited edition did not get its values from the '
            'workbook either',
        )

    def test_resubmitting_identical_values_writes_nothing(self) -> None:
        """`FORK_EDIT` 는 "누가 저장을 눌렀다"가 아니라 "사람의 숫자가 들어 있다"다."""
        same = next(
            e['payload'] for e in self.detail['entries']
            if e['reference_id'] == 'ref-0'
        )
        after = self._edit('ref-0', dict(same))
        self.assertEqual(
            after['revision']['provenance_kind'],
            RevisionProvenanceKind.WORKBOOK.value,
        )
        self.assertEqual(after['revision']['version'],
                         self.detail['revision']['version'])

    def test_the_concurrency_token_is_the_etag_not_the_version(self) -> None:
        """ADR-0019 는 blanket version-CAS 를 기각했고, 이 경로는 그것이 필요 없다.

        version 은 누가 매 write 마다 올려주기를 **기억해야 하는** 카운터다. etag 는
        내용에서 **파생**되므로 잊을 수가 없다 — 값이 바뀌면 태그가 구성상 바뀐다.
        그래서 편집은 etag 로 CAS 하고 version 은 게시가 그러듯 건드리지 않는다.
        이 단언이 없으면 다음 사람이 "편집은 내용 변경이니 version 을 올려야 한다"는
        그럴듯한 이유로 절반짜리 CAS 를 다시 넣는다(이 웨이브가 실제로 그랬다).
        """
        before = self.detail['revision']['version']
        after = self._edit('ref-0', _payload(_FAMILY, 0, value=9.75))
        self.assertNotEqual(
            after['revision']['etag'], self.detail['revision']['etag'],
            'the content changed, so the derived tag must have moved',
        )
        self.assertEqual(
            after['revision']['version'], before,
            'version is a replica-schema obligation, not a concurrency token',
        )

    def test_a_stale_etag_is_refused(self) -> None:
        self._edit('ref-0', _payload(_FAMILY, 0, value=9.75))
        with self.assertRaises(ReferencePublishConflictError):
            self._edit('ref-1', _payload(_FAMILY, 1, value=8.25))

    def test_the_second_writer_does_not_lose_the_first_number(self) -> None:
        first = self._edit('ref-0', _payload(_FAMILY, 0, value=9.75))
        with self.assertRaises(ReferencePublishConflictError):
            self._edit('ref-1', _payload(_FAMILY, 1, value=8.25))
        current = self.service.read_revision(_PROVIDER, self.candidate_id)
        payloads = {e['reference_id']: e['payload'] for e in current['entries']}
        self.assertEqual(payloads['ref-0']['correction_db'], 9.75)
        self.assertEqual(
            current['revision']['etag'], first['revision']['etag'],
        )

    def test_a_published_revision_is_immutable(self) -> None:
        with self.assertRaises(ReferenceStateConflictError):
            self._edit(
                'ref-0', _payload(_FAMILY, 0, value=9.75),
                etag=self.service.read_revision(_PROVIDER,
                    self.published_id
                )['revision']['etag'],
                revision_id=self.published_id,
            )

    def test_an_unknown_revision_is_absent(self) -> None:
        with self.assertRaises(ReferenceRevisionNotFoundError):
            self._edit(
                'ref-0', _payload(_FAMILY, 0, value=9.75),
                revision_id=uuid.uuid4().hex,
            )

    def test_a_missing_expected_etag_is_refused(self) -> None:
        with self.assertRaises(ReferenceEntryEditError):
            self.service.update_candidate_entries(
                _PROVIDER, self.candidate_id,
                request={'edits': [
                    {'reference_id': 'ref-0',
                     'payload': _payload(_FAMILY, 0, value=9.75)},
                ]},
                updated_by='tester',
            )

    def test_the_detail_carries_the_identity_columns(self) -> None:
        """프론트가 `IDENTITY_FIELD_CONTRACT` 를 재선언하지 않도록 서버가 준다."""
        self.assertEqual(
            self.detail['identity_columns'],
            list(identity_fields_for(_FAMILY)[0]),
        )


class TestTheEditPolicyRefusesWhatIsNotAnEdit(unittest.TestCase):
    """순수 도메인 — DB 없이 거부 규칙 자체를 본다."""

    def setUp(self) -> None:
        self.entries = [
            {'reference_id': 'ref-0',
             'payload': _payload(_FAMILY, 0)},
            {'reference_id': 'ref-1',
             'payload': _payload(_FAMILY, 1, value=2.5)},
        ]

    def test_an_unknown_reference_id_is_refused_by_name(self) -> None:
        with self.assertRaises(ReferenceEntryEditError) as caught:
            apply_entry_edits(_FAMILY, self.entries, [
                EntryEdit('ref-9', _payload(_FAMILY, 0)),
            ])
        self.assertIn('ref-9', str(caught.exception))

    def test_a_duplicate_reference_id_is_refused(self) -> None:
        with self.assertRaises(ReferenceEntryEditError):
            apply_entry_edits(_FAMILY, self.entries, [
                EntryEdit('ref-0', _payload(_FAMILY, 0)),
                EntryEdit('ref-0', _payload(_FAMILY, 0, value=2.5)),
            ])

    def test_a_missing_payload_column_is_refused_by_name(self) -> None:
        payload = _payload(_FAMILY, 0)
        dropped = sorted(payload)[0]
        payload.pop(dropped)
        with self.assertRaises(ReferenceEntryEditError) as caught:
            apply_entry_edits(_FAMILY, self.entries, [
                EntryEdit('ref-0', payload),
            ])
        self.assertIn(dropped, str(caught.exception))

    def test_an_unexpected_payload_column_is_refused_by_name(self) -> None:
        payload = _payload(_FAMILY, 0)
        payload['not_a_column'] = 1
        with self.assertRaises(ReferenceEntryEditError) as caught:
            apply_entry_edits(_FAMILY, self.entries, [
                EntryEdit('ref-0', payload),
            ])
        self.assertIn('not_a_column', str(caught.exception))

    def test_moving_an_identity_field_is_refused_by_name(self) -> None:
        identity_field = identity_fields_for(_FAMILY)[0][0]
        payload = _payload(_FAMILY, 0)
        payload[identity_field] = 'moved'
        with self.assertRaises(ReferenceEntryEditError) as caught:
            apply_entry_edits(_FAMILY, self.entries, [
                EntryEdit('ref-0', payload),
            ])
        self.assertIn(identity_field, str(caught.exception))

    def test_text_in_a_number_column_is_refused_by_name(self) -> None:
        """숫자 칸에 문자열이 들어가는 것은 '다른 값'이 아니라 **다른 종류**다.

        적대적 리뷰에서 실증된 결함이다. 화면의 `coerceLikeOriginal` 은 사용자가
        친 문자열을 숫자로 되돌리려 시도하고, **실패하면 원문 문자열을 그대로**
        보낸다. `'1,5'`(쉼표 소수 구분자 — 흔한 입력)는 `Number()` 가 `NaN` 이라
        문자열로 통과했고, 서버에도 투영에도 타입 검사가 **0**이라 correction dB
        `1.5`(float)가 `'1,5'`(str)로 저장됐다. 게시되면 측정 경로가 읽는 런타임
        행에 숫자 대신 문자열이 앉는다 — 이 표면이 막으려던 *"숫자만 조용히
        틀린다"* 보다 나쁘다.

        경계가 최종 권위이므로 판정은 여기(도메인)에 있다. 화면이 읽지 못한 입력을
        원문으로 보내는 것 자체는 화면의 결함이지만, 서버가 그것을 받아들이는 것은
        서버의 결함이다.
        """
        payload = _payload(_FAMILY, 0)
        value_field = _value_field(_FAMILY)
        payload[value_field] = '1,5'
        with self.assertRaises(ReferenceEntryEditError) as caught:
            apply_entry_edits(_FAMILY, self.entries, [
                EntryEdit('ref-0', payload),
            ])
        self.assertIn(value_field, str(caught.exception))
        self.assertIn('number', str(caught.exception))

    def test_an_int_may_become_a_float(self) -> None:
        """거부가 과잉이 아님 — 1 → 1.5 는 시험원이 값을 정밀화한 것이다.

        JSON 은 int 와 float 를 구분하지 않으므로, 둘을 나누면 와이어가 표현하지도
        못하는 차이를 이유로 정당한 편집을 막게 된다.
        """
        value_field = _value_field(_FAMILY)
        rows = [{'reference_id': 'ref-0',
                 'payload': {**_payload(_FAMILY, 0), value_field: 2}}]
        payload = {**_payload(_FAMILY, 0), value_field: 2.5}
        outcome = apply_entry_edits(_FAMILY, rows, [EntryEdit('ref-0', payload)])
        self.assertTrue(outcome.changed)
        self.assertEqual(outcome.payloads['ref-0'][value_field], 2.5)

    def test_filling_an_empty_cell_is_allowed(self) -> None:
        """빈 칸에는 지킬 종류가 없다 — 채우는 것을 막으면 안 된다."""
        value_field = _value_field(_FAMILY)
        rows = [{'reference_id': 'ref-0',
                 'payload': {**_payload(_FAMILY, 0), value_field: None}}]
        payload = {**_payload(_FAMILY, 0), value_field: 3.25}
        outcome = apply_entry_edits(_FAMILY, rows, [EntryEdit('ref-0', payload)])
        self.assertEqual(outcome.payloads['ref-0'][value_field], 3.25)

    def test_a_boolean_is_not_a_number(self) -> None:
        """`bool` 은 파이썬에서 `int` 다 — 접으면 True 가 숫자로 통과한다."""
        value_field = _value_field(_FAMILY)
        payload = {**_payload(_FAMILY, 0), value_field: True}
        with self.assertRaises(ReferenceEntryEditError):
            apply_entry_edits(_FAMILY, self.entries, [EntryEdit('ref-0', payload)])

    def test_a_non_identity_change_is_allowed(self) -> None:
        """거부가 과잉이 아님을 실증 — 아니면 위 다섯은 '전부 거부'와 구별되지 않는다."""
        identity = set(identity_fields_for(_FAMILY)[0])
        value_field = next(
            f for f in projection_fields_for(_FAMILY) if f not in identity
        )
        payload = _payload(_FAMILY, 0)
        payload[value_field] = 99.0
        outcome = apply_entry_edits(_FAMILY, self.entries, [
            EntryEdit('ref-0', payload),
        ])
        self.assertTrue(outcome.changed)
        self.assertEqual(outcome.payloads['ref-0'][value_field], 99.0)

    def test_a_numeric_identity_survives_a_json_round_trip(self) -> None:
        """`1` 과 `1.0` 은 같은 행이다 — 브라우저는 그 둘을 구분하지 못한다.

        적대적 리뷰가 실증했다. 옛 비교는 `str` 이라 `'1' != '1.0'` 이었고, 저장된
        float 식별값이 JS 왕복에서 int 로 돌아오는 **평범한 재저장**이 "식별 필드를
        바꾸려 한다"며 거부됐다. correction 의 식별 필드는 숫자이므로 이건 예외가
        아니라 기본 경로였다.
        """
        identity_field = identity_fields_for(_FAMILY)[0][0]
        value_field = _value_field(_FAMILY)
        rows = [{'reference_id': 'ref-0',
                 'payload': {**_payload(_FAMILY, 0), identity_field: 1.0}}]
        payload = {**_payload(_FAMILY, 0), identity_field: 1, value_field: 7.5}
        outcome = apply_entry_edits(_FAMILY, rows, [EntryEdit('ref-0', payload)])
        self.assertTrue(outcome.changed)

    def test_clearing_an_identity_field_is_still_a_move(self) -> None:
        """`None → ''` 은 옛 비교에서 **둘 다 `''`** 라 조용히 통과했다.

        식별 이동을 막는 것이 이 검사의 유일한 일인데, 그 한 가지 이동이 통과했다.
        """
        identity_field = identity_fields_for(_FAMILY)[0][0]
        rows = [{'reference_id': 'ref-0',
                 'payload': {**_payload(_FAMILY, 0), identity_field: None}}]
        payload = {**_payload(_FAMILY, 0), identity_field: ''}
        with self.assertRaises(ReferenceEntryEditError) as caught:
            apply_entry_edits(_FAMILY, rows, [EntryEdit('ref-0', payload)])
        self.assertIn(identity_field, str(caught.exception))

    def test_too_many_edits_in_one_request_are_refused(self) -> None:
        """한 요청이 손으로 고칠 수 있는 것보다 많은 행을 지목하면 거부한다."""
        from fcc_test_kernel.domain.services.reference_entry_edit_policy import (
            MAX_ENTRY_EDITS_PER_REQUEST,
        )

        edits = [
            EntryEdit(f'ref-{i}', _payload(_FAMILY, 0))
            for i in range(MAX_ENTRY_EDITS_PER_REQUEST + 1)
        ]
        with self.assertRaises(ReferenceEntryEditError) as caught:
            apply_entry_edits(_FAMILY, self.entries, edits)
        self.assertIn(str(MAX_ENTRY_EDITS_PER_REQUEST), str(caught.exception))

    def test_a_non_object_payload_is_refused_at_creation(self) -> None:
        """저장된 뒤에는 이 API 로 고칠 수 없는 유일한 모양이다.

        `create_candidate` 에 검사가 없어 리스트 payload 가 저장됐고, 그 뒤 **모든**
        편집이 — 그것을 고치려는 편집까지 — 읽다가 죽었다(적대적 리뷰 실증).
        """
        from fcc_test_kernel.domain.services.reference_entry_edit_policy import (
            validate_entry_payload_shape,
        )

        with self.assertRaises(ReferenceEntryEditError) as caught:
            validate_entry_payload_shape(_FAMILY, 'ref-0', ['not', 'a', 'dict'])
        self.assertIn('list', str(caught.exception))
        # 비-공허성 — 정상 payload 는 그대로 통과한다.
        payload = _payload(_FAMILY, 0)
        self.assertIs(
            validate_entry_payload_shape(_FAMILY, 'ref-0', payload), payload,
        )

    def test_an_empty_edit_list_is_refused(self) -> None:
        with self.assertRaises(ReferenceEntryEditError):
            apply_entry_edits(_FAMILY, self.entries, [])

    def test_identical_values_are_not_a_change(self) -> None:
        outcome = apply_entry_edits(_FAMILY, self.entries, [
            EntryEdit('ref-0', _payload(_FAMILY, 0)),
        ])
        self.assertFalse(outcome.changed)

    def test_the_policy_is_domain_pure(self) -> None:
        source = _kernel_source('fcc_test_kernel.domain.services.reference_entry_edit_policy')
        # ⚠️ **판정을 계층 축으로 올렸다** (2026-09-03).
        #
        # 여기 있던 것은 문자열 대조였고(`'from infrastructure' in source`), 이 모듈이
        # 커널로 가자 실제 import 가 `from fcc_test_kernel.infrastructure…` 가 되어
        # **그 문자열에 걸리지 않았다.** 첫 정정은 금지어에 접두사판을 *더하는*
        # 것이었는데, 그것은 **접두사가 또 늘면 같은 자리에서 또 낡는다.**
        #
        # ⚠️ 그리고 이 저장소의 다른 AST 가드(`_imported_roots`)도 같은 맹점을
        # 갖고 있었다 — **최상위 이름**을 뽑으면 접두사가 붙는 순간 최상위가
        # `fcc_test_kernel` 이 된다. 「AST 로 하면 된다」가 답이 아니었다.
        #
        # 판정 단위는 **계층 절**이다: `tests/_layer_of_import.py`.
        layers = imported_layers(source)
        self.assertTrue(
            layers, '이 모듈이 아무것도 import 하지 않는다 — 순수성 판정이 공허하다')
        for forbidden in ('infrastructure', 'pyvisa', 'PySide6', 'pandas'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, layers)


class TestTheStatusCodesActuallyReachAnHttpClient(unittest.TestCase):
    """선언된 404/409/503 이 **HTTP 응답**으로 나가는지를 왕복으로 본다.

    구조적 증거(에러표 ↔ `except` 튜플의 AST 파생 포함관계)는 "빠진 것이 없다"를
    증명하고, 이것은 "실제로 나간다"를 증명한다. 둘 다 필요하다 — 이 표면이 값을
    치른 방식이 정확히 *조각은 다 맞는데 왕복에서 다른 것이 나오는* 형태였다:
    표도, OpenAPI 도, 서비스를 호출하는 테스트도 전부 404 를 보는 동안 HTTP
    클라이언트만 500 을 받았다.
    """

    def setUp(self) -> None:
        try:
            from fastapi import FastAPI, HTTPException
            from fastapi.responses import JSONResponse
            from fastapi.testclient import TestClient
        except ImportError:  # pragma: no cover - fastapi is a test dependency
            self.skipTest('fastapi is not installed')
        from fcc_test_platform.api.platform_routes import (
            PlatformApiAdapter,
            create_platform_router,
        )
        from fcc_test_contracts.web.problem_response import (
            install_problem_details_handler,
        )

        self._raise: BaseException = ReferenceRevisionNotFoundError('nope')

        class _RaisingReferenceService:
            """모든 참조 read/write 가 이 케이스의 예외를 던진다."""

            def __init__(self, owner):
                self._owner = owner

            def __getattr__(self, _name):
                def _boom(*_args, **_kwargs):
                    raise self._owner._raise
                return _boom

        # 인가는 이 테스트의 대상이 아니다 — 예외가 HTTP 로 나가는 경로만 본다.
        adapter = PlatformApiAdapter(
            None,
            access_policy=None,
            reference_service=_RaisingReferenceService(self),
        )
        adapter.authorize = lambda *a, **k: None  # type: ignore[method-assign]
        # The framework objects are resolved here and handed over, the same
        # shape the three production create_*_app factories use: the installer
        # is owned by the dependency-free contracts lane and imports no web
        # framework (2026-08-15). Folding these three lines into a
        # tests/support/ helper was tried and reverted — a helper is
        # attributed to the lane of what it imports, so it followed the
        # installer into the contracts box and carried fastapi with it.
        app = FastAPI()
        install_problem_details_handler(
            app, http_exception=HTTPException, json_response=JSONResponse,
        )
        app.include_router(create_platform_router(adapter))
        self.client = TestClient(app, raise_server_exceptions=False)

    def _get_detail(self) -> int:
        return self.client.get(
            f'/platform/providers/{_PROVIDER}/reference-revisions/rev-1'
        ).status_code

    def test_an_unknown_revision_is_404_not_500(self) -> None:
        self._raise = ReferenceRevisionNotFoundError('nope')
        self.assertEqual(self._get_detail(), 404)

    def test_a_publish_slot_conflict_is_409_not_500(self) -> None:
        self._raise = ReferencePublishConflictError('taken')
        self.assertEqual(self._get_detail(), 409)

    def test_a_state_conflict_is_409_not_500(self) -> None:
        self._raise = ReferenceStateConflictError('not published')
        self.assertEqual(self._get_detail(), 409)

    def test_a_coupled_refusal_is_409_not_500(self) -> None:
        from domain.ports.output.central_reference_port import (
            ReferenceCoupledPublishError,
        )

        self._raise = ReferenceCoupledPublishError('half a pair')
        self.assertEqual(self._get_detail(), 409)

    def test_a_backend_outage_is_503_not_500(self) -> None:
        from domain.ports.output.central_reference_port import CentralReferenceError

        self._raise = CentralReferenceError('no route to host')
        self.assertEqual(self._get_detail(), 503)

    def test_an_unmapped_error_still_reaches_the_default(self) -> None:
        """비-공허성 — 위 다섯이 "무엇이든 그 코드"가 되어 통과한 것이 아니다."""
        self._raise = KeyError('not part of the reference taxonomy')
        self.assertEqual(self._get_detail(), 500)


class TestAuthorizationRunsBeforeTheServiceIsResolved(unittest.TestCase):
    """참조 표면을 배포하지 않은 노드가 그 사실을 무권한 호출자에게 말하지 않는다.

    2026-08-09 적대적 리뷰가 실증했다. 네 핸들러가 `_require_reference_service`
    를 인가보다 **먼저** 부르고 있어서, `reference_service` 가 배선되지 않은
    배포(문서화된 정상 상태)에서 **익명·무권한** 호출자가 403 이 아니라 500 을
    받았다. 그것만으로 *"여기는 참조 쓰기가 배포돼 있지 않다"* 를 알 수 있고,
    권한 없는 사람이 배포 형상을 알 일은 없다.

    데이터 접근 우회는 아니지만, 인가 실패가 리비전의 **존재**를 노출하지 않게
    만든 것과 같은 계열이다 — 거부는 거부에 대해서만 말해야 한다.
    """

    def setUp(self) -> None:
        try:
            from fastapi import FastAPI, HTTPException
            from fastapi.responses import JSONResponse
            from fastapi.testclient import TestClient
        except ImportError:  # pragma: no cover - fastapi is a test dependency
            self.skipTest('fastapi is not installed')
        from fcc_test_platform.api.platform_routes import (
            PlatformApiAdapter,
            create_platform_router,
        )
        from fcc_test_contracts.web.problem_response import (
            install_problem_details_handler,
        )

        # 참조 서비스를 **배선하지 않는다** — 문제의 배포 형상 그대로.
        adapter = PlatformApiAdapter(None, access_policy=None)

        def _deny(*_args, **_kwargs):
            from fcc_test_platform.api.platform_routes import (
                PlatformAuthorizationError,
            )
            raise PlatformAuthorizationError('missing_permission')

        adapter.authorize = _deny  # type: ignore[method-assign]
        # The framework objects are resolved here and handed over, the same
        # shape the three production create_*_app factories use: the installer
        # is owned by the dependency-free contracts lane and imports no web
        # framework (2026-08-15). Folding these three lines into a
        # tests/support/ helper was tried and reverted — a helper is
        # attributed to the lane of what it imports, so it followed the
        # installer into the contracts box and carried fastapi with it.
        app = FastAPI()
        install_problem_details_handler(
            app, http_exception=HTTPException, json_response=JSONResponse,
        )
        app.include_router(create_platform_router(adapter))
        self.client = TestClient(app, raise_server_exceptions=False)

    def _paths(self) -> list[tuple[str, str]]:
        base = f'/platform/providers/{_PROVIDER}/reference-revisions/rev-1'
        return [
            ('GET', base),
            ('POST', f'{base}/fork'),
            ('PUT', f'{base}/entries'),
            ('POST', f'{base}/publish'),
        ]

    def test_an_unwired_surface_answers_403_not_500(self) -> None:
        for method, path in self._paths():
            with self.subTest(route=f'{method} {path}'):
                response = self.client.request(method, path, json={})
                self.assertEqual(
                    response.status_code, 403,
                    'an unauthorized caller learned the deployment topology '
                    'from the status code',
                )

    def test_the_control_is_not_vacuous(self) -> None:
        """서비스가 **배선된** 경우에도 무권한이면 같은 403 이어야 한다."""
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient
        from fcc_test_platform.api.platform_routes import (
            PlatformApiAdapter,
            PlatformAuthorizationError,
            create_platform_router,
        )
        from fcc_test_contracts.web.problem_response import (
            install_problem_details_handler,
        )

        class _Boom:
            def __getattr__(self, name):
                def _explode(*_a, **_k):
                    raise AssertionError(
                        f'unauthorized caller reached the service ({name})'
                    )
                return _explode

        adapter = PlatformApiAdapter(
            None, access_policy=None, reference_service=_Boom(),
        )

        def _deny(*_args, **_kwargs):
            raise PlatformAuthorizationError('missing_permission')

        adapter.authorize = _deny  # type: ignore[method-assign]
        # The framework objects are resolved here and handed over, the same
        # shape the three production create_*_app factories use: the installer
        # is owned by the dependency-free contracts lane and imports no web
        # framework (2026-08-15). Folding these three lines into a
        # tests/support/ helper was tried and reverted — a helper is
        # attributed to the lane of what it imports, so it followed the
        # installer into the contracts box and carried fastapi with it.
        app = FastAPI()
        install_problem_details_handler(
            app, http_exception=HTTPException, json_response=JSONResponse,
        )
        app.include_router(create_platform_router(adapter))
        client = TestClient(app, raise_server_exceptions=False)
        for method, path in self._paths():
            with self.subTest(route=f'{method} {path}'):
                self.assertEqual(
                    client.request(method, path, json={}).status_code, 403,
                )


if __name__ == '__main__':
    unittest.main()


# ═══════════════════════════════════════════════════════════════════════════════
# Wave B (2026-08-11) — 처음부터 만들기(출처 축) + 행 추가·삭제(식별 축)
#
# 두 축은 **다른 것**이고 한 웨이브 안에서도 분리해 다룬다. B-1 은 *이 값들이 어디서
# 왔는가*를 답할 수 있게 하고, B-2 는 *어떤 행이 존재하는가*를 바꾼다.
# ═══════════════════════════════════════════════════════════════════════════════

def _authored_payload(family: CatalogFamily, row: int, *, value: float = 7.5) -> dict:
    return _payload(family, row, value=value)


class TestAuthoringFromScratch(_CentralTestCase):
    """B-1 — 워크북 없이 태어난 리비전이 그 사실을 정직하게 기록한다."""

    def _create(self, **overrides):
        request = {
            'family': _FAMILY.value,
            'profile_id': 'default',
            'scope_kind': 'room',
            'scope_id': 'room-9',
            'entries': [{'payload': _authored_payload(_FAMILY, 90)}],
        }
        request.update(overrides)
        return self.service.create_authored_candidate(
            _PROVIDER, request=request, created_by='tester',
        )

    def test_it_is_stamped_web_authored_and_carries_no_snapshot(self):
        created = self._create()
        detail = self.service.read_revision(_PROVIDER, created['revision_id'])
        revision = detail['revision']
        self.assertEqual(
            revision['provenance_kind'], RevisionProvenanceKind.WEB_AUTHORED.value,
        )
        self.assertIsNone(revision.get('source_snapshot_id'))
        self.assertIsNone(revision.get('source_manifest_sha256'))
        self.assertEqual(revision['state'], RevisionState.CANDIDATE.value)

    def test_the_server_mints_every_derived_value(self):
        """요청은 payload 만 나른다 — 정체성과 해시는 서버가 만든다."""
        payload = _authored_payload(_FAMILY, 91)
        detail = self.service.read_revision(_PROVIDER,
            self._create(entries=[{'payload': payload}])['revision_id'],
        )
        entry = detail['entries'][0]
        expected_key = identity_key_for(_FAMILY, payload)
        self.assertEqual(entry['identity_key'], expected_key)
        self.assertEqual(entry['reference_id'], expected_key)
        self.assertEqual(
            entry['content_sha256'],
            build_reference_entry_hash({
                'identity_key': expected_key,
                'payload': payload,
                'test_condition_ids': (),
                'effective_from': None,
                'effective_to': None,
            }),
        )

    def test_the_request_schema_offers_no_identity_or_provenance_field(self):
        """클라이언트가 보낼 수 있는 것이 곧 위조할 수 있는 것이다."""
        from fcc_test_kernel.application.central_contract.api_contracts import PLATFORM_API_SCHEMAS

        request_schema = PLATFORM_API_SCHEMAS[
            'CreateAuthoredReferenceRevisionRequest'
        ]
        self.assertEqual(request_schema['additionalProperties'], False)
        offered = set(request_schema['properties'])
        for forbidden in (
            'provenance_kind', 'source_snapshot_id', 'source_manifest_sha256',
        ):
            self.assertNotIn(forbidden, offered)
        entry_schema = PLATFORM_API_SCHEMAS['AuthoredReferenceEntry']
        self.assertEqual(set(entry_schema['properties']), {'payload'})
        self.assertEqual(entry_schema['additionalProperties'], False)

    def test_editing_a_web_authored_candidate_keeps_the_label(self):
        """격자가 여기서 멈춘다 — 그 값들은 워크북 편집에서 온 것이 아니다."""
        created = self._create()
        detail = self.service.read_revision(_PROVIDER, created['revision_id'])
        entry = detail['entries'][0]
        edited = dict(entry['payload'])
        edited[_value_field(_FAMILY)] = 42.0
        self.service.update_candidate_entries(
            _PROVIDER, created['revision_id'],
            request={
                'expected_etag': detail['revision']['etag'],
                'edits': [{
                    'reference_id': entry['reference_id'], 'payload': edited,
                }],
            },
            updated_by='tester',
        )
        after = self.service.read_revision(_PROVIDER, created['revision_id'])
        self.assertEqual(
            after['revision']['provenance_kind'],
            RevisionProvenanceKind.WEB_AUTHORED.value,
            '웹 저작본을 편집했다고 FORK_EDIT 로 재라벨하면 감사 칸이 거짓이 된다',
        )

    def test_an_empty_authored_revision_is_refused(self):
        with self.assertRaises(ReferenceRowEditError):
            self._create(entries=[])

    def test_a_wrong_scope_axis_is_refused(self):
        with self.assertRaises(ReferenceScopeError):
            self._create(scope_kind='project')

    def test_a_payload_that_is_not_the_runtime_row_is_refused(self):
        with self.assertRaises(ReferenceRowEditError):
            self._create(entries=[{'payload': {'not': 'a row'}}])

    def test_a_non_null_payload_value_kind_is_refused_and_names_the_field(self):
        payload = _authored_payload(_FAMILY, 93)
        field = _value_field(_FAMILY)
        payload[field] = '7.5'

        with self.assertRaises(ReferenceEntryPayloadValueError) as caught:
            self._create(entries=[{'payload': payload}])

        message = str(caught.exception)
        self.assertIn(field, message)
        self.assertIn("'text'", message)
        self.assertIn("'number'", message)

    def test_none_is_allowed_as_an_empty_payload_cell(self):
        payload = _authored_payload(_FAMILY, 94)
        field = _value_field(_FAMILY)
        payload[field] = None

        created = self._create(entries=[{'payload': payload}])
        stored = self.service.read_revision(_PROVIDER, created['revision_id'])['entries'][0]

        self.assertIsNone(stored['payload'][field])

    def test_a_workbook_created_candidate_rejects_wrong_non_null_value_kind(self):
        payload = _payload(_FAMILY, 95)
        field = _value_field(_FAMILY)
        payload[field] = '7.5'

        with self.assertRaises(ReferenceEntryPayloadValueError) as caught:
            self.service.create_candidate(
                _PROVIDER,
                request={
                    'family': _FAMILY.value,
                    'profile_id': 'default',
                    'scope_kind': 'room',
                    'scope_id': 'room-9',
                    'source_snapshot_id': 'snap-1',
                    'source_manifest_sha256': 'c' * 64,
                    'entries': [{
                        'reference_id': 'ref-kind',
                        'identity_key': 'not-used-before-validation',
                        'payload': payload,
                        'test_condition_ids': [],
                        'effective_from': None,
                        'effective_to': None,
                        'source_sheet_name': 'sheet',
                        'source_row_number': 10,
                        'content_sha256': 'd' * 64,
                    }],
                },
                created_by='importer',
            )

        self.assertIn(field, str(caught.exception))

    def test_two_rows_with_the_same_identity_are_refused(self):
        payload = _authored_payload(_FAMILY, 92)
        with self.assertRaises(ReferenceRowEditError):
            self._create(entries=[{'payload': payload}, {'payload': dict(payload)}])


class TestRowsCanBeAddedAndRemoved(_CentralTestCase):
    """B-2 — 값 편집 정책이 거부하던 바로 그 연산을, 자기 operation 으로."""

    def setUp(self) -> None:
        super().setUp()
        self.candidate_id = self.fixture._create_candidate(_FAMILY, rows=3)

    def _detail(self):
        return self.service.read_revision(_PROVIDER, self.candidate_id)

    def _rows(self, **request):
        detail = self._detail()
        payload = {'expected_etag': detail['revision']['etag']}
        payload.update(request)
        return self.service.update_candidate_rows(
            _PROVIDER, self.candidate_id, request=payload, updated_by='tester',
        )

    def test_an_added_row_lands_after_the_existing_ones(self):
        payload = _authored_payload(_FAMILY, 80)
        before = [entry['reference_id'] for entry in self._detail()['entries']]
        self._rows(additions=[{'payload': payload}])
        after = [entry['reference_id'] for entry in self._detail()['entries']]
        self.assertEqual(after[:len(before)], before, '기존 행의 순서는 그대로다')
        self.assertEqual(after[-1], identity_key_for(_FAMILY, payload))

    def test_a_removed_row_is_gone_and_the_gap_is_left_behind(self):
        detail = self._detail()
        victim = detail['entries'][1]['reference_id']
        self._rows(removals=[victim])
        remaining = [entry['reference_id'] for entry in self._detail()['entries']]
        self.assertNotIn(victim, remaining)
        self.assertEqual(len(remaining), 2)
        # 재번호하지 않는다 — 다음 추가는 여전히 최대값 다음에 붙는다.
        self._rows(additions=[{'payload': _authored_payload(_FAMILY, 81)}])
        self.assertEqual(len(self._detail()['entries']), 3)

    def test_a_row_can_be_replaced_by_one_with_the_same_identity(self):
        """식별 필드를 '옮기는' 일 — 추가+삭제로만 표현되고, 한 트랜잭션이다."""
        payload = _authored_payload(_FAMILY, 82)
        self._rows(additions=[{'payload': payload}])
        reference_id = identity_key_for(_FAMILY, payload)
        replacement = dict(payload)
        replacement[_value_field(_FAMILY)] = 99.0
        self._rows(
            removals=[reference_id], additions=[{'payload': replacement}],
        )
        entries = {e['reference_id']: e for e in self._detail()['entries']}
        self.assertEqual(
            entries[reference_id]['payload'][_value_field(_FAMILY)], 99.0,
        )

    def test_removing_a_row_this_revision_does_not_have_is_refused(self):
        with self.assertRaises(ReferenceRowEditError):
            self._rows(removals=['no-such-row'])

    def test_naming_the_same_row_twice_is_refused(self):
        victim = self._detail()['entries'][0]['reference_id']
        with self.assertRaises(ReferenceRowEditError):
            self._rows(removals=[victim, victim])

    def test_adding_a_row_that_collides_with_a_survivor_is_refused(self):
        payload = _authored_payload(_FAMILY, 83)
        self._rows(additions=[{'payload': payload}])
        with self.assertRaises(ReferenceRowEditError):
            self._rows(additions=[{'payload': dict(payload)}])

    def test_an_added_row_with_a_wrong_value_kind_is_refused(self):
        payload = _authored_payload(_FAMILY, 86)
        field = _value_field(_FAMILY)
        payload[field] = '42.0'

        with self.assertRaises(ReferenceEntryPayloadValueError) as caught:
            self._rows(additions=[{'payload': payload}])

        self.assertIn(field, str(caught.exception))

    def test_removing_every_row_is_refused(self):
        everything = [e['reference_id'] for e in self._detail()['entries']]
        with self.assertRaises(ReferenceRowEditError):
            self._rows(removals=everything)

    def test_an_empty_request_is_refused(self):
        with self.assertRaises(ReferenceRowEditError):
            self._rows()

    def test_a_stale_etag_is_refused(self):
        with self.assertRaises(ReferencePublishConflictError):
            self.service.update_candidate_rows(
                _PROVIDER, self.candidate_id,
                request={
                    'expected_etag': 'stale',
                    'additions': [{'payload': _authored_payload(_FAMILY, 84)}],
                },
                updated_by='tester',
            )

    def test_a_published_revision_is_refused(self):
        with self.assertRaises(ReferenceStateConflictError):
            self.service.update_candidate_rows(
                _PROVIDER, self.published_id,
                request={
                    'expected_etag': 'whatever',
                    'removals': ['ref-0'],
                },
                updated_by='tester',
            )

    def test_another_providers_revision_answers_like_an_absent_one(self):
        other = self.fixture.register_provider('someone-else')
        with self.assertRaises(ReferenceRevisionNotFoundError):
            self.service.update_candidate_rows(
                other, self.candidate_id,
                request={'expected_etag': 'x', 'removals': ['ref-0']},
                updated_by='tester',
            )

    def test_a_row_edit_promotes_a_workbook_candidate(self):
        self._rows(additions=[{'payload': _authored_payload(_FAMILY, 85)}])
        self.assertEqual(
            self._detail()['revision']['provenance_kind'],
            RevisionProvenanceKind.FORK_EDIT.value,
        )

    def test_the_value_edit_policy_was_not_loosened(self):
        """이 축이 열렸다고 해서 식별 필드 편집이 허용되지 않는다.

        두 결함이 서로를 가리지 않게 하는 것이 이 웨이브의 설계 근거였으므로,
        그 사실을 실행으로 고정한다.
        """
        family = _FAMILY
        payload = _payload(family, 1)
        moved = dict(payload)
        identity_field = identity_fields_for(family)[0][0]
        moved[identity_field] = 'moved'
        with self.assertRaises(ReferenceEntryEditError):
            apply_entry_edits(
                family,
                [{'reference_id': 'ref-1', 'payload': payload}],
                [EntryEdit(reference_id='ref-1', payload=moved)],
            )


class TestValueValidationLeavesTheEditAxisUntouched(unittest.TestCase):
    def test_apply_entry_edits_does_not_call_creation_value_validation(self):
        source = _kernel_source('fcc_test_kernel.domain.services.reference_entry_edit_policy')
        tree = ast.parse(source)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == 'apply_entry_edits'
        )
        function_source = ast.get_source_segment(source, function)
        self.assertIsNotNone(function_source)
        self.assertNotIn('validate_entry_payload_values', function_source)
