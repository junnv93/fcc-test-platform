"""Real-PostgreSQL migration convergence proof for the cross-session schema.

The test is environment-gated: absent disposable DSNs produce a skip, never a
synthetic PASS. The live lane supplies two different empty databases through
the existing FCC_CENTRAL_DB_URL and FCC_CENTRAL_DB_UPGRADE_URL contract.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / 'src', ROOT / 'scripts'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from support.ambient_env import ambient_config_env  # noqa: E402


FRESH_DSN = ambient_config_env('FCC_CENTRAL_DB_URL').strip()
UPGRADE_DSN = ambient_config_env('FCC_CENTRAL_DB_UPGRADE_URL').strip()

pytestmark = pytest.mark.skipif(
    not FRESH_DSN or not UPGRADE_DSN,
    reason=(
        'FCC_CENTRAL_DB_URL and FCC_CENTRAL_DB_UPGRADE_URL are required for the '
        'disposable PostgreSQL cross-session proof; no synthetic PASS is emitted'
    ),
)


@pytest.fixture(scope='module')
def live_proof():
    from fcc_test_platform.central_db_live_proof_cli import (
        DEFAULT_REGISTRY_PATH,
        _default_provider_code,
        run_live_proof,
    )

    return run_live_proof(
        FRESH_DSN,
        upgrade_dsn=UPGRADE_DSN,
        proof_seed='pytest-cross-session-schema',
        provider_code=_default_provider_code(Path(DEFAULT_REGISTRY_PATH)),
        include_report=False,
    )


def _connect(dsn):
    import psycopg

    return psycopg.connect(dsn)


def _index_names(dsn):
    with _connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = 'measurement_attempts'"
        )
        return {str(row[0]) for row in cursor.fetchall()}


def _ledger(dsn):
    with _connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT version, checksum FROM "schema_migrations" ORDER BY version')
        return dict(cursor.fetchall())


def test_fresh_and_pre_030_upgrade_lanes_converge(live_proof):
    assert live_proof['verdict'] == 'PASS'
    assert live_proof['lanes']['fresh']['stages']['migration']['valid'] is True
    assert live_proof['lanes']['upgrade']['stages']['migration']['valid'] is True
    for lane in ('fresh', 'upgrade'):
        runner = live_proof['lanes'][lane]['stages']['migration']['runner']
        assert runner['candidate_migrate']['exit_code'] == 0


def test_legacy_and_provider_scoped_conflict_indexes_exist_in_both_lanes(live_proof):
    expected = {
        'ux_measurement_attempts_session_condition_attempt',
        'idx_measurement_attempts_project_condition_hash',
        'ux_measurement_attempts_provider_session_condition_attempt',
        'idx_measurement_attempts_project_provider_condition_hash',
    }
    for dsn in (FRESH_DSN, UPGRADE_DSN):
        assert expected <= _index_names(dsn)


def test_migration_rerun_is_a_noop_and_ledger_checksums_are_current(live_proof):
    from fcc_test_platform.db_migrate_cli import checksum_sql, discover_migrations, migrate

    expected = {
        version: checksum_sql(path.read_text(encoding='utf-8'))
        for version, path in discover_migrations()
    }
    for dsn in (FRESH_DSN, UPGRADE_DSN):
        rerun = migrate(
            dsn=dsn,
            applied_by='pytest-cross-session-schema-rerun',
        )
        assert rerun['applied'] == []
        assert _ledger(dsn) == expected


def test_live_proof_does_not_expose_database_credentials(live_proof):
    serialized = repr(live_proof)
    for secret in (FRESH_DSN, UPGRADE_DSN):
        assert secret not in serialized
    assert 'dsn_target' in serialized
