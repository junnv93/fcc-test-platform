"""The origin's storage boundary: loud on outage, typed on conflict, one cut.

WHAT THIS PROTECTS
------------------
These adapters are where the central reference catalog meets PostgreSQL. Three
properties matter enough to assert rather than assume:

1. **An outage never borrows the meaning of an empty result.** "This room
   publishes nothing" and "the database is unreachable" must not look the same to
   a chamber, because the first is a known gap and the second would have a
   chamber measure with no correction data while believing it was correct.

2. **A lost publish race is a conflict, not a fault** — and the answer is reached
   by a conditional statement plus one diagnostic query, never by reading a
   driver's error text. Parsing constraint messages binds behaviour to a DDL
   naming convention, and this table carries several unique indexes so the error
   class alone cannot say which one lost.

3. **A delivery bundle is ONE cut.** Revisions and their entries are fetched with
   two statements, not one query per revision: N queries would let a publish land
   between them and a chamber would apply half of a coupled family group, pairing
   a signal path with another path's loss.

The fakes here record every statement, so the tests can assert the SHAPE of the
access — how many round trips, in what order — and not merely the return value.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402

from fcc_test_platform.application.central_reference_read_adapter import (  # noqa: E402
    ENTRY_COLUMNS,
    REVISION_COLUMNS,
    PostgresCentralReferenceReadAdapter,
)
from fcc_test_platform.application.central_reference_write_adapter import (  # noqa: E402
    PostgresCentralReferenceWriteAdapter,
)
from domain.models.reference_catalog import (  # noqa: E402
    RevisionProvenanceKind,
    RevisionState,
)
from domain.ports.output.central_reference_port import (  # noqa: E402
    CentralReferenceError,
    CentralReferenceReadPort,
    CentralReferenceWritePort,
    ReferenceProviderNotFoundError,
    ReferencePublishConflictError,
    ReferenceRevisionNotFoundError,
)


class _FakeCursor:
    def __init__(self, connection):
        self._connection = connection
        self._rows: list = []

    def execute(self, statement, params=()):
        self._connection.statements.append((statement, tuple(params)))
        self._rows = self._connection.next_result()

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        self._connection.cursors_closed += 1


class _FakeConnection:
    """Records statements and hands back scripted result sets, in order."""

    def __init__(self, results=None, fail_on=None):
        self.statements: list = []
        self._results = list(results or [])
        self._fail_on = fail_on
        self.cursors_closed = 0
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def next_result(self):
        if self._fail_on is not None and len(self.statements) == self._fail_on:
            raise RuntimeError('connection reset by peer')
        return self._results.pop(0) if self._results else []

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _revision_row(revision_id='rev-1', family='correction', scope='room-1',
                  state=None, entry_count=2, provenance_kind=None):
    values = {
        'revision_id': revision_id,
        'provider_id': 'fcc-unlicensed-conducted',
        'family': family,
        'profile_id': 'default',
        'scope_kind': 'room',
        'scope_id': scope,
        'revision_number': 1,
        'state': state or RevisionState.PUBLISHED.value,
        'version': 1,
        'etag': 'a' * 64,
        'content_sha256': 'b' * 64,
        'source_snapshot_id': 'snap-1',
        'source_manifest_sha256': 'c' * 64,
        'official_manifest_sha256': None,
        'forked_from_revision_id': None,
        'provenance_kind': (
            provenance_kind or RevisionProvenanceKind.WORKBOOK.value
        ),
        'created_by': 'operator',
        'created_at': '2026-08-07T00:00:00Z',
        'updated_by': 'operator',
        'updated_at': '2026-08-07T00:00:00Z',
        'approved_by': None,
        'approved_at': None,
        'approval_reason': None,
        'published_by': 'operator',
        'published_at': '2026-08-07T00:00:00Z',
        'publish_reason': None,
        'retired_by': None,
        'retired_at': None,
        'retirement_reason': None,
        'entry_count': entry_count,
    }
    return tuple(values[column] for column in REVISION_COLUMNS)


def _entry_row(revision_id='rev-1', order=0):
    values = {
        'revision_id': revision_id,
        'entry_order': order,
        'reference_id': f'ref-{order}',
        'identity_key': f'key-{order}',
        'payload_json': {'correction_index': 1},
        'test_condition_ids_json': [],
        'effective_from': None,
        'effective_to': None,
        'source_sheet_name': 'Correction1',
        'source_row_number': 12 + order,
        'content_sha256': 'd' * 64,
    }
    return tuple(values[column] for column in ENTRY_COLUMNS)


class TestAdaptersSatisfyTheirPorts(unittest.TestCase):
    def test_read_adapter_is_a_read_port(self) -> None:
        adapter = PostgresCentralReferenceReadAdapter(lambda: _FakeConnection())
        self.assertIsInstance(adapter, CentralReferenceReadPort)

    def test_write_adapter_is_a_write_port(self) -> None:
        adapter = PostgresCentralReferenceWriteAdapter(lambda: _FakeConnection())
        self.assertIsInstance(adapter, CentralReferenceWritePort)

    def test_a_non_callable_factory_is_refused_at_construction(self) -> None:
        for cls in (
            PostgresCentralReferenceReadAdapter,
            PostgresCentralReferenceWriteAdapter,
        ):
            with self.subTest(cls=cls.__name__):
                with self.assertRaises(ValueError):
                    cls('not-callable')


class TestAnOutageIsNeverAnEmptyResult(unittest.TestCase):
    """The distinction a chamber's correctness depends on."""

    def test_a_failed_connection_raises(self) -> None:
        def factory():
            raise RuntimeError('no route to host')

        adapter = PostgresCentralReferenceReadAdapter(factory)
        with self.assertRaises(CentralReferenceError):
            adapter.list_revisions('fcc-unlicensed-conducted')

    def test_a_failed_query_raises(self) -> None:
        # fail_on counts statements already recorded, and execute() records
        # before it fetches — so 1 is the first query, not 0.
        connection = _FakeConnection(fail_on=1)
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)
        with self.assertRaises(CentralReferenceError):
            adapter.list_revisions('fcc-unlicensed-conducted')
        self.assertTrue(connection.closed, 'the connection must still be released')

    def test_a_genuinely_empty_catalog_returns_empty(self) -> None:
        """Non-vacuity: the failures above must be distinguishable from this."""
        adapter = PostgresCentralReferenceReadAdapter(
            lambda: _FakeConnection(results=[[]])
        )
        self.assertEqual(adapter.list_revisions('fcc-unlicensed-conducted'), [])


class TestListingFiltersAndPagesOnTheIndexAxis(unittest.TestCase):
    def test_facets_become_bound_parameters(self) -> None:
        connection = _FakeConnection(results=[[_revision_row()]])
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)
        adapter.list_revisions(
            'prov', family='correction', scope_kind='room', scope_id='room-1',
            state=RevisionState.PUBLISHED.value, limit=10,
        )
        statement, params = connection.statements[0]
        self.assertEqual(
            params,
            ('prov', 'correction', 'room', 'room-1',
             RevisionState.PUBLISHED.value, 10),
            'facets must be bound, never interpolated',
        )
        self.assertNotIn("'correction'", statement)

    def test_the_cursor_is_a_row_value_comparison(self) -> None:
        """An OR-chain can disagree with the ORDER BY; a row value cannot."""
        connection = _FakeConnection(results=[[]])
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)
        adapter.list_revisions('prov', cursor_values=('correction', 'default', 'room-1', 1, 'rev-1'))
        statement, _params = connection.statements[0]
        self.assertIn(') > (%s, %s, %s, %s, %s)', statement)

    def test_the_order_matches_the_identity_index(self) -> None:
        connection = _FakeConnection(results=[[]])
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)
        adapter.list_revisions('prov')
        statement, _params = connection.statements[0]
        self.assertIn(
            'ORDER BY "r"."family", "r"."profile_id", "r"."scope_id", '
            '"r"."revision_number", "r"."id"',
            statement,
        )

    def test_omitted_facets_add_no_clause(self) -> None:
        connection = _FakeConnection(results=[[]])
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)
        adapter.list_revisions('prov')
        _statement, params = connection.statements[0]
        self.assertEqual(params, ('prov',))


class TestTheBundleIsOneCut(unittest.TestCase):
    def test_two_statements_regardless_of_revision_count(self) -> None:
        """N revisions must not mean N queries — a publish could land between."""
        revisions = [_revision_row(f'rev-{i}') for i in range(5)]
        entries = [_entry_row(f'rev-{i}', 0) for i in range(5)]
        connection = _FakeConnection(results=[revisions, entries])
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)

        bundle = adapter.read_bundle('prov', scope_ids=['room-1', 'proj-1'])

        self.assertEqual(len(bundle), 5)
        self.assertEqual(
            len(connection.statements), 2,
            f'expected 2 statements, got {len(connection.statements)}',
        )

    def test_entries_are_attached_to_their_revision(self) -> None:
        connection = _FakeConnection(results=[
            [_revision_row('rev-a'), _revision_row('rev-b')],
            [_entry_row('rev-a', 0), _entry_row('rev-a', 1), _entry_row('rev-b', 0)],
        ])
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)
        bundle = adapter.read_bundle('prov', scope_ids=['room-1'])
        by_id = {revision['revision_id']: revision for revision in bundle}
        self.assertEqual(len(by_id['rev-a']['entries']), 2)
        self.assertEqual(len(by_id['rev-b']['entries']), 1)

    def test_only_published_revisions_are_delivered(self) -> None:
        connection = _FakeConnection(results=[[], []])
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)
        adapter.read_bundle('prov', scope_ids=['room-1'])
        _statement, params = connection.statements[0]
        self.assertIn(RevisionState.PUBLISHED.value, params)

    def test_no_scopes_means_no_query_at_all(self) -> None:
        """An unscoped bundle would hand a chamber every room's cabling."""
        connection = _FakeConnection()
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)
        self.assertEqual(adapter.read_bundle('prov', scope_ids=['', '  ']), [])
        self.assertEqual(connection.statements, [])

    def test_no_revisions_means_no_entry_query(self) -> None:
        connection = _FakeConnection(results=[[]])
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)
        self.assertEqual(adapter.read_bundle('prov', scope_ids=['room-1']), [])
        self.assertEqual(len(connection.statements), 1)


class TestTheCutIsIdentifiedWithoutBeingDownloaded(unittest.TestCase):
    """The tag has to cover the whole cut even when a page covers part of it.

    So the identity read is its own cheap statement: two columns, no payloads,
    no LIMIT. Deriving the tag from a page instead would give each page its own
    tag, and the node's mid-walk-change check would fire on every normal walk.
    """

    def test_the_identity_read_carries_no_payload_and_no_limit(self) -> None:
        connection = _FakeConnection(results=[[('rev-a', 'e' * 64)]])
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)

        rows = adapter.read_bundle_identity('prov', scope_ids=['room-1'])

        statement, _params = connection.statements[0]
        self.assertEqual(rows, [{'revision_id': 'rev-a', 'etag': 'e' * 64}])
        self.assertEqual(len(connection.statements), 1)
        self.assertNotIn('payload_json', statement)
        self.assertNotIn('LIMIT', statement)

    def test_the_identity_read_is_scoped_and_published_only(self) -> None:
        connection = _FakeConnection(results=[[]])
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)
        adapter.read_bundle_identity('prov', scope_ids=['room-1', 'proj-1'])
        _statement, params = connection.statements[0]
        self.assertIn(RevisionState.PUBLISHED.value, params)
        self.assertIn('room-1', params)
        self.assertIn('proj-1', params)

    def test_no_scopes_means_no_identity_query(self) -> None:
        connection = _FakeConnection()
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)
        self.assertEqual(adapter.read_bundle_identity('prov', scope_ids=['']), [])
        self.assertEqual(connection.statements, [])


class TestTheBundlePagesOnTheSameAxisAsTheListing(unittest.TestCase):
    def test_a_limit_reaches_the_statement(self) -> None:
        connection = _FakeConnection(results=[[_revision_row('rev-a')], []])
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)
        adapter.read_bundle('prov', scope_ids=['room-1'], limit=2)
        statement, params = connection.statements[0]
        self.assertIn('LIMIT %s', statement)
        self.assertEqual(params[-1], 2)

    def test_a_cursor_becomes_a_row_value_seek_on_the_keyset_axis(self) -> None:
        connection = _FakeConnection(results=[[], []])
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)
        adapter.read_bundle(
            'prov', scope_ids=['room-1'],
            cursor_values=('correction', 'default', 'room-1', '1', 'rev-a'),
        )
        statement, params = connection.statements[0]
        self.assertIn(
            '("r"."family", "r"."profile_id", "r"."scope_id", '
            '"r"."revision_number", "r"."id") > (%s, %s, %s, %s, %s)',
            statement,
            'an OR-chain could disagree with the ORDER BY about where the page '
            'ends; a row-value comparison cannot',
        )
        self.assertIn('rev-a', params)

    def test_paging_does_not_add_a_third_statement(self) -> None:
        connection = _FakeConnection(results=[[_revision_row('rev-a')], []])
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)
        adapter.read_bundle('prov', scope_ids=['room-1'], limit=1)
        self.assertEqual(
            len(connection.statements), 2,
            'a page is still one revision statement plus one entry statement',
        )

    def test_entries_are_keyed_on_this_pages_revisions_only(self) -> None:
        connection = _FakeConnection(results=[
            [_revision_row('rev-a')],
            [_entry_row('rev-a', 0)],
        ])
        adapter = PostgresCentralReferenceReadAdapter(lambda: connection)
        adapter.read_bundle('prov', scope_ids=['room-1'], limit=1)
        _statement, params = connection.statements[1]
        self.assertEqual(
            params, ('rev-a',),
            'a page must not carry entries for a revision it did not return',
        )


class TestCandidateCreation(unittest.TestCase):
    def test_revision_and_entries_land_in_one_transaction(self) -> None:
        connection = _FakeConnection(results=[[('rev-new', 3)], [], []])
        adapter = PostgresCentralReferenceWriteAdapter(lambda: connection)

        result = adapter.create_candidate(
            'prov',
            revision=_candidate_revision(),
            entries=[_candidate_entry(0), _candidate_entry(1)],
        )

        self.assertEqual(result, {'revision_id': 'rev-new', 'revision_number': 3})
        self.assertTrue(connection.committed)
        self.assertEqual(len(connection.statements), 3, 'one insert plus two entries')

    def test_the_state_is_always_candidate(self) -> None:
        """Publishing is a human review step; an importer cannot skip it."""
        connection = _FakeConnection(results=[[('rev-new', 1)], []])
        adapter = PostgresCentralReferenceWriteAdapter(lambda: connection)
        adapter.create_candidate(
            'prov', revision=_candidate_revision(), entries=[_candidate_entry(0)],
        )
        _statement, params = connection.statements[0]
        self.assertIn(RevisionState.CANDIDATE.value, params)
        self.assertNotIn(RevisionState.PUBLISHED.value, params)

    def test_the_revision_number_is_assigned_by_the_database(self) -> None:
        """Two importers racing on one identity must not both claim a number."""
        connection = _FakeConnection(results=[[('rev-new', 1)], []])
        adapter = PostgresCentralReferenceWriteAdapter(lambda: connection)
        adapter.create_candidate(
            'prov', revision=_candidate_revision(), entries=[_candidate_entry(0)],
        )
        statement, _params = connection.statements[0]
        self.assertIn('MAX("r2"."revision_number") + 1', statement)

    def test_an_unknown_provider_is_a_client_error(self) -> None:
        connection = _FakeConnection(results=[[]])
        adapter = PostgresCentralReferenceWriteAdapter(lambda: connection)
        with self.assertRaises(ReferenceProviderNotFoundError):
            adapter.create_candidate(
                'nope', revision=_candidate_revision(), entries=[],
            )
        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)

    def test_payload_is_serialised_deterministically(self) -> None:
        """A key-order change would alter the stored bytes and every diff."""
        connection = _FakeConnection(results=[[('rev-new', 1)], []])
        adapter = PostgresCentralReferenceWriteAdapter(lambda: connection)
        entry = _candidate_entry(0)
        entry['payload'] = {'z': 1, 'a': 2}
        adapter.create_candidate(
            'prov', revision=_candidate_revision(), entries=[entry],
        )
        _statement, params = connection.statements[1]
        self.assertEqual(json.loads(params[4]), {'a': 2, 'z': 1})
        self.assertEqual(params[4], '{"a": 2, "z": 1}')


class TestPublishDecidesWithoutReadingDriverText(unittest.TestCase):
    def test_a_successful_publish_commits(self) -> None:
        # 첫 결과는 supersede UPDATE(반환값 없음), 그 다음이 publish UPDATE.
        connection = _FakeConnection(results=[[], [('rev-1',)]])
        adapter = PostgresCentralReferenceWriteAdapter(lambda: connection)
        result = adapter.publish(
            'rev-1', published_by='operator', published_at='2026-08-07T00:00:00Z',
        )
        self.assertEqual(result['state'], RevisionState.PUBLISHED.value)
        self.assertTrue(connection.committed)

    def test_both_preconditions_live_in_the_where_clause(self) -> None:
        connection = _FakeConnection(results=[[], [('rev-1',)]])
        adapter = PostgresCentralReferenceWriteAdapter(lambda: connection)
        adapter.publish(
            'rev-1', published_by='operator', published_at='2026-08-07T00:00:00Z',
        )
        statement, _params = connection.statements[1]
        self.assertIn('AND "state" = %s', statement)
        self.assertIn('NOT EXISTS', statement)

    def test_an_unknown_revision_is_not_found(self) -> None:
        connection = _FakeConnection(results=[[], [], []])
        adapter = PostgresCentralReferenceWriteAdapter(lambda: connection)
        with self.assertRaises(ReferenceRevisionNotFoundError):
            adapter.publish(
                'ghost', published_by='op', published_at='2026-08-07T00:00:00Z',
            )

    def test_a_taken_slot_is_a_conflict(self) -> None:
        connection = _FakeConnection(results=[
            [], [], [(RevisionState.CANDIDATE.value, True)],
        ])
        adapter = PostgresCentralReferenceWriteAdapter(lambda: connection)
        with self.assertRaises(ReferencePublishConflictError) as caught:
            adapter.publish(
                'rev-1', published_by='op', published_at='2026-08-07T00:00:00Z',
            )
        self.assertIn('took the published slot', str(caught.exception))

    def test_a_non_candidate_is_a_conflict_with_its_own_reason(self) -> None:
        connection = _FakeConnection(results=[
            [], [], [(RevisionState.RETIRED.value, False)],
        ])
        adapter = PostgresCentralReferenceWriteAdapter(lambda: connection)
        with self.assertRaises(ReferencePublishConflictError) as caught:
            adapter.publish(
                'rev-1', published_by='op', published_at='2026-08-07T00:00:00Z',
            )
        self.assertIn(RevisionState.RETIRED.value, str(caught.exception))

    def test_no_driver_message_is_parsed(self) -> None:
        """CLAUDE.md forbids it: constraint text binds behaviour to DDL naming.

        The check reads EXECUTABLE string constants via AST rather than scanning
        the file. Prose is allowed to name the index — the module docstring
        explains that it is the backstop — and a text scan would either forbid
        that explanation or be silenced by deleting it.
        """
        import ast

        source = (
            resolve_repo_artifact(__file__, 'src/application/platform/central_reference_write_adapter.py')
        ).read_text(encoding='utf-8')
        literals = _executable_string_literals(ast.parse(source))
        for smell in ('ux_reference_revisions_published', 'pgcode', 'sqlstate'):
            with self.subTest(smell=smell):
                offenders = [text for text in literals if smell in text.lower()]
                self.assertEqual(
                    offenders, [],
                    f'{smell!r} appears in an executable string; deciding a lost '
                    'race from driver text binds behaviour to DDL naming',
                )

        code = '\n'.join(
            line for line in source.splitlines()
            if not line.lstrip().startswith('#')
        )
        self.assertNotIn(
            'str(exc)', code,
            'inspecting an exception message to classify it is the same defect',
        )

    def test_the_literal_scan_can_actually_fail(self) -> None:
        """Non-vacuity: the AST scan must see executable strings at all."""
        import ast

        source = (
            resolve_repo_artifact(__file__, 'src/application/platform/central_reference_write_adapter.py')
        ).read_text(encoding='utf-8')
        literals = _executable_string_literals(ast.parse(source))
        self.assertTrue(
            any('reference_revisions' in text for text in literals),
            'the scan found no SQL at all, so the assertions above are vacuous',
        )


class TestThePlatformPackageBoundaryHolds(unittest.TestCase):
    """These modules may not reach for hashing, infrastructure or a driver."""

    _MODULES = (
        'central_reference_read_adapter.py',
        'central_reference_write_adapter.py',
    )

    def test_no_forbidden_imports(self) -> None:
        import ast

        for name in self._MODULES:
            source = (resolve_repo_artifact(__file__, 'src/application/platform') / name).read_text(
                encoding='utf-8'
            )
            tree = ast.parse(source)
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.append(node.module or '')
            for forbidden in ('hashlib', 'psycopg', 'psycopg2', 'sqlite3'):
                with self.subTest(module=name, forbidden=forbidden):
                    self.assertNotIn(forbidden, imported)
            for module in imported:
                with self.subTest(module=name, imported=module):
                    self.assertFalse(
                        module.startswith('infrastructure'),
                        'the platform package must not import infrastructure',
                    )

    def test_state_tokens_derive_from_the_domain_enum(self) -> None:
        """Re-typing a token here is how three declarations drift apart."""
        for name in self._MODULES:
            source = (resolve_repo_artifact(__file__, 'src/application/platform') / name).read_text(
                encoding='utf-8'
            )
            code = '\n'.join(
                line for line in source.splitlines()
                if not line.lstrip().startswith('#')
            )
            for state in RevisionState:
                with self.subTest(module=name, state=state.value):
                    self.assertNotIn(
                        f"'{state.value}'", code,
                        f'{name} spells {state.value!r} instead of deriving it '
                        'from RevisionState',
                    )


def _executable_string_literals(tree) -> list[str]:
    """Every string constant that is NOT a docstring.

    Docstring nodes are excluded BY IDENTITY, not by comparing text:
    ``ast.get_docstring`` returns a cleaned, dedented copy, so a text comparison
    silently fails to match the node it came from and the docstring leaks into
    the scan — which is exactly what made an earlier version of this check
    reject its own explanatory prose.
    """
    import ast as _ast

    docstring_nodes = set()
    for node in _ast.walk(tree):
        if not isinstance(
            node, (_ast.Module, _ast.ClassDef, _ast.FunctionDef, _ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, 'body', None) or []
        if (
            body
            and isinstance(body[0], _ast.Expr)
            and isinstance(body[0].value, _ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstring_nodes.add(id(body[0].value))
    return [
        node.value for node in _ast.walk(tree)
        if isinstance(node, _ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstring_nodes
    ]


def _candidate_revision() -> dict:
    return {
        'family': 'correction',
        'profile_id': 'default',
        'scope_kind': 'room',
        'scope_id': 'room-1',
        'etag': 'a' * 64,
        'content_sha256': 'b' * 64,
        'source_snapshot_id': 'snap-1',
        'source_manifest_sha256': 'c' * 64,
        'provenance_kind': RevisionProvenanceKind.WORKBOOK.value,
        'created_by': 'operator',
    }


def _candidate_entry(order: int) -> dict:
    return {
        'reference_id': f'ref-{order}',
        'identity_key': f'key-{order}',
        'payload': {'correction_index': 1, 'frequency_hz': 2400.0, 'correction_db': 1.5},
        'content_sha256': 'd' * 64,
    }


if __name__ == '__main__':
    unittest.main()
