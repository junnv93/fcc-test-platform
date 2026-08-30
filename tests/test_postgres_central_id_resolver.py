"""FE-P0c WIRE — PostgresCentralIdResolver production behaviour.

Covers completion conditions:
  (2) production resolver — deterministic uuid5 (session) + projects SELECT
      lookup (project) + cache + loud-fail.
  (5) resolver lookup cache hit/miss invariant (real behavioural measurement
      via project_db_query_count, not a substring check) + frozen-exe
      compat (no module-level psycopg import).
  (6) byte-identical verification through real data flow — a real SQLite
      connection executes the real lookup SQL (paramstyle-translated) against
      real seeded rows; the returned uuid is asserted byte-equal to the seed.
"""
from __future__ import annotations

import ast
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _REPO_ROOT / 'src'
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402
from fcc_test_contracts.common.sqlite_connection_factory import SqliteConnectionContext  # noqa: E402
from fcc_test_platform.central_id_resolver import (  # noqa: E402
    CentralIdResolutionError,
    CentralIdResolverPort,
)
from fcc_test_platform.postgres_central_id_resolver import (  # noqa: E402
    CENTRAL_SESSION_UUID_NAMESPACE,
    PROJECT_LOOKUP_SQL,
    PostgresCentralIdResolver,
)


#: Addressed by its *repository* name, resolved through the packager's own
#: relocation record. This file ships in the platform box (2026-08-15), where
#: the module is delivered as ``fcc_test_platform/postgres_central_id_resolver.py``
#: — a hard-coded ``src/...`` join answers correctly in the monorepo and names a
#: file that does not exist in the delivered tree. In the monorepo there is no
#: layout record and the result is byte-identical to the old join.
_RESOLVER_MODULE = resolve_repo_artifact(
    __file__, 'src/application/headless/postgres_central_id_resolver.py',
)


# ── Real SQLite fixture — exercises the production lookup SQL against real rows ──
#
# psycopg uses ``%s`` paramstyle; sqlite3 uses ``?``. This wrapper translates the
# paramstyle so the *exact production SQL constant* (PROJECT_LOOKUP_SQL) runs
# against a real DB with real WHERE matching + real fetchone — the only
# divergence from PostgreSQL is the placeholder token (a known cross-DBMS
# detail), so byte-identical assertions on the returned uuid are faithful.


from support.central_pg_sqlite_shim import QmarkConnection  # noqa: E402


def _seed_projects(db_path: str, rows: dict[str, str]) -> None:
    with SqliteConnectionContext(db_path) as conn:
        conn.execute('CREATE TABLE projects (id TEXT PRIMARY KEY, project_code TEXT UNIQUE)')
        conn.executemany(
            'INSERT INTO projects (id, project_code) VALUES (?, ?)',
            [(uuid_str, code) for code, uuid_str in rows.items()],
        )
        conn.commit()


class _CountingFactory:
    """Wraps the connection factory to count how many connections were opened
    (cross-check for the cache hit/miss measurement)."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self.open_count = 0

    def __call__(self) -> QmarkConnection:
        self.open_count += 1
        return QmarkConnection(self._db_path)


class _ResolverFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name

    def tearDown(self) -> None:
        Path(self.db_path).unlink(missing_ok=True)


class TestSessionUuidDeterministic(unittest.TestCase):
    def test_uuid5_byte_identical_to_independent_recompute(self):
        resolver = PostgresCentralIdResolver(
            provider_id='prov-1', connection_factory=lambda: None,  # type: ignore[arg-type]
        )
        resolved = resolver.resolve_session_uuid(11)
        # Independent recompute via the public namespace SSOT — byte-identical.
        expected = str(uuid.uuid5(CENTRAL_SESSION_UUID_NAMESPACE, 'prov-1:11'))
        self.assertEqual(resolved, expected)
        # It is a valid v5 uuid.
        self.assertEqual(uuid.UUID(resolved).version, 5)

    def test_deterministic_across_instances(self):
        a = PostgresCentralIdResolver(provider_id='p', connection_factory=lambda: None)  # type: ignore[arg-type]
        b = PostgresCentralIdResolver(provider_id='p', connection_factory=lambda: None)  # type: ignore[arg-type]
        self.assertEqual(a.resolve_session_uuid(7), b.resolve_session_uuid(7))

    def test_provider_scoped(self):
        a = PostgresCentralIdResolver(provider_id='prov-A', connection_factory=lambda: None)  # type: ignore[arg-type]
        b = PostgresCentralIdResolver(provider_id='prov-B', connection_factory=lambda: None)  # type: ignore[arg-type]
        self.assertNotEqual(a.resolve_session_uuid(1), b.resolve_session_uuid(1))

    def test_chamber_scoped_local_ids_are_distinct_and_stable(self):
        resolver = PostgresCentralIdResolver(
            provider_id='prov-A', connection_factory=lambda: None,  # type: ignore[arg-type]
        )
        chamber_a = resolver.resolve_session_uuid(1, chamber_id='chamber-a')
        chamber_b = resolver.resolve_session_uuid(1, chamber_id='chamber-b')
        self.assertNotEqual(chamber_a, chamber_b)
        self.assertEqual(
            chamber_a,
            resolver.resolve_session_uuid(1, chamber_id='chamber-a'),
        )
        # The migration sentinel preserves the pre-multichamber UUID space.
        self.assertEqual(resolver.resolve_session_uuid(1), resolver.resolve_session_uuid(1, chamber_id='__fcc_legacy__'))

    def test_distinct_local_ids_distinct_uuids(self):
        resolver = PostgresCentralIdResolver(provider_id='p', connection_factory=lambda: None)  # type: ignore[arg-type]
        self.assertNotEqual(resolver.resolve_session_uuid(1), resolver.resolve_session_uuid(2))

    def test_session_uuid_does_not_touch_db(self):
        factory = _CountingFactory(':memory:')
        resolver = PostgresCentralIdResolver(provider_id='p', connection_factory=factory)
        resolver.resolve_session_uuid(1)
        resolver.resolve_session_uuid(2)
        self.assertEqual(factory.open_count, 0, 'session uuid is pure compute — no DB')

    def test_invalid_local_session_id_loud_fail(self):
        resolver = PostgresCentralIdResolver(provider_id='p', connection_factory=lambda: None)  # type: ignore[arg-type]
        with self.assertRaises(CentralIdResolutionError):
            resolver.resolve_session_uuid('not-an-int')  # type: ignore[arg-type]

    def test_empty_provider_id_loud_fail(self):
        with self.assertRaises(ValueError):
            PostgresCentralIdResolver(provider_id='', connection_factory=lambda: None)  # type: ignore[arg-type]


class TestProjectUuidLookup(_ResolverFixture):
    def test_select_lookup_byte_identical(self):
        project_uuid = str(uuid.uuid4())
        _seed_projects(self.db_path, {'PRJ-1': project_uuid})
        factory = _CountingFactory(self.db_path)
        resolver = PostgresCentralIdResolver(provider_id='p', connection_factory=factory)

        resolved = resolver.resolve_project_uuid('PRJ-1')
        self.assertEqual(resolved, project_uuid)  # byte-identical to seeded row
        self.assertEqual(factory.open_count, 1)

    def test_none_and_empty_return_none_without_db(self):
        factory = _CountingFactory(self.db_path)
        resolver = PostgresCentralIdResolver(provider_id='p', connection_factory=factory)
        self.assertIsNone(resolver.resolve_project_uuid(None))
        self.assertIsNone(resolver.resolve_project_uuid(''))
        self.assertEqual(factory.open_count, 0)

    def test_cache_hit_avoids_second_query(self):
        _seed_projects(self.db_path, {'PRJ-1': str(uuid.uuid4())})
        factory = _CountingFactory(self.db_path)
        resolver = PostgresCentralIdResolver(provider_id='p', connection_factory=factory)

        first = resolver.resolve_project_uuid('PRJ-1')
        self.assertEqual(resolver.project_db_query_count, 1)  # miss → 1 DB query
        self.assertEqual(factory.open_count, 1)

        second = resolver.resolve_project_uuid('PRJ-1')
        self.assertEqual(second, first)
        self.assertEqual(resolver.project_db_query_count, 1)  # hit → no extra query
        self.assertEqual(factory.open_count, 1)  # hit → no extra connection

    def test_distinct_codes_each_query_once(self):
        _seed_projects(self.db_path, {'PRJ-1': str(uuid.uuid4()), 'PRJ-2': str(uuid.uuid4())})
        factory = _CountingFactory(self.db_path)
        resolver = PostgresCentralIdResolver(provider_id='p', connection_factory=factory)

        resolver.resolve_project_uuid('PRJ-1')
        resolver.resolve_project_uuid('PRJ-2')
        resolver.resolve_project_uuid('PRJ-1')  # cached
        self.assertEqual(resolver.project_db_query_count, 2)
        self.assertEqual(factory.open_count, 2)

    def test_unmapped_project_code_loud_fail(self):
        _seed_projects(self.db_path, {'PRJ-1': str(uuid.uuid4())})
        factory = _CountingFactory(self.db_path)
        resolver = PostgresCentralIdResolver(provider_id='p', connection_factory=factory)
        with self.assertRaises(CentralIdResolutionError):
            resolver.resolve_project_uuid('NOPE')

    def test_uses_production_lookup_sql_constant(self):
        # The fixture executes PROJECT_LOOKUP_SQL verbatim (paramstyle-translated).
        self.assertIn('projects', PROJECT_LOOKUP_SQL)
        self.assertIn('project_code', PROJECT_LOOKUP_SQL)
        self.assertIn('%s', PROJECT_LOOKUP_SQL)  # PostgreSQL paramstyle


class TestPortConformanceAndFrozenExe(unittest.TestCase):
    def test_satisfies_central_id_resolver_port(self):
        resolver = PostgresCentralIdResolver(provider_id='p', connection_factory=lambda: None)  # type: ignore[arg-type]
        self.assertIsInstance(resolver, CentralIdResolverPort)

    def test_no_module_level_postgres_driver_import(self):
        tree = ast.parse(_RESOLVER_MODULE.read_text(encoding='utf-8'))
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        offenders = {n for n in names if n.split('.')[0] in {'psycopg', 'psycopg2', 'asyncpg'}}
        self.assertEqual(
            offenders, set(),
            f'resolver must not import a PostgreSQL driver (uses injected '
            f'connection_factory): {offenders}',
        )


if __name__ == '__main__':
    unittest.main()
