"""Repeatable end-to-end live proof for the central platform PostgreSQL store.

This orchestrator drives the *real* migration/ingestion modules against a live
PostgreSQL database (no FakeConnection, no fixture stand-ins) and emits a single
combined evidence bundle. It is the repeatable entrypoint behind the env-gated
integration test ``tests/integration/test_central_db_e2e_live.py``.

What it proves, in order:

1. migration       — applies docs/platform/migrations/001_initial_central_db.sql
                     and validates the collected evidence against the schema SSOT.
2. ingestion ×2    — ingests a representative measurement batch twice through the
                     real worker/writer and asserts replay idempotency (row counts
                     stable, exactly one is_latest=true attempt, coverage row present).
3. report axis     — seeds the representative FCC demo measurements through the same
                     production ingestion chain and publishes the handoff identities a
                     provider report proof needs (``report_axis.demo_measurement_seed``).
Stage 4 — report ingestion — is a SEPARATE invocation (``--report-reconstruction``):
it ingests provider-generated report output metadata, emitting one report_runs parent
before report_outputs and replaying that batch idempotently. It cannot be a stage of the
run above because that run refuses to start against anything but an EMPTY database, while
ingestion requires the database it just seeded.

Lane boundary (ADR-0020, ``governance.cross_lane_import_baseline``):
- Reconstructing an FCC report is ``fcc-unlicensed-headless`` work and lives in
  ``scripts/provider_report_reconstruction_live_proof.py``. The extracted
  ``fcc-test-platform`` repository has no ``application/reporting/`` at all, so a
  platform proof that imported it could never run there. This file therefore names
  no provider module; it publishes a seed handoff and consumes an evidence file.
- A bundle without report ingestion is a NORMAL platform-only proof, not a silent
  skip: the ``report_axis`` stage states that the stage is a separate invocation and
  names both commands that produce it. In an extracted platform repository the
  provider command does not exist, and running the proof alone is complete there.

Honest scope (see also docs/platform/central_db_live_proof_readiness.md):
- The provider proof reconstructs reports from central-DB measurements plus
  reference equipment/frequency/antenna data from canonical non-Excel resources.
  The resulting real DOCX/PDF metadata is what stage 4 here consumes; unsupported
  WLAN tech partitioning remains a reporting-domain item.

SSOT / no hardcoding:
- DB DSN comes from --dsn or the FCC_CENTRAL_DB_URL env var only.
- The provider business code defaults to the first entry of the provider
  registry SSOT (docs/api/headless_provider_registry.json), never a literal.
- Table order / idempotency keys come from platform_ingestion_plan SSOT.
- Identity UUIDs are derived deterministically from --proof-seed so repeated runs
  converge (idempotent provisioning + ingestion).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
# The script's own holding directory — a single ``.parent``, not a tree-depth
# question, since ``platform_db_migration_collect`` lives right beside this
# file regardless of how deep the tree that holds both of them is delivered.
_SCRIPTS_ROOT = Path(__file__).resolve().parent
for _path in (PROJECT_ROOT, SRC_ROOT, _SCRIPTS_ROOT):
    _text = str(_path)
    if _text not in sys.path:
        sys.path.insert(0, _text)

from fcc_test_contracts.common.tree_artifacts import discover_tree_artifact  # noqa: E402
from fcc_test_platform.db_migration_evidence import (  # noqa: E402
    EXPECTED_MIGRATION_ID,
    central_db_migration_evidence_errors,
)
from fcc_test_platform.ingestion_execution_evidence import (  # noqa: E402
    build_ingestion_execution_manifest,
    ingestion_execution_errors,
)
from fcc_test_platform.provider_ingestion import (  # noqa: E402
    REPORT_RUN_COMPLETED_STATUS,
    build_platform_ingestion_batch,
)
from fcc_test_platform.provider_ingestion_plan import build_platform_ingestion_plan  # noqa: E402
from fcc_test_platform.provider_ingestion_worker import (  # noqa: E402
    COVERAGE_REFRESH_SUCCEEDED,
    IngestionRetryPolicy,
    execute_platform_ingestion_plan,
)
from fcc_test_platform.postgres_ingestion_writer import PostgresIngestionWriter  # noqa: E402
from fcc_test_platform.provider_registry import load_provider_registry  # noqa: E402
from fcc_test_platform.application.central_sample_inventory_write_adapter import (  # noqa: E402
    PostgresCentralSampleInventoryWriteAdapter,
)
from fcc_test_platform.application.central_project_read_adapter import (  # noqa: E402
    PostgresCentralProjectReadAdapter,
)
from fcc_test_platform.application.central_report_read_adapter import (  # noqa: E402
    PostgresCentralReportReadAdapter,
)
from fcc_test_platform.application.central_report_service import CentralReportService  # noqa: E402
from fcc_test_platform.application.central_report_write_adapter import (  # noqa: E402
    PostgresCentralReportWriteAdapter,
)
from fcc_test_kernel.domain.models.sample_inventory import SNAPSHOT_SCHEMA_VERSION  # noqa: E402
from fcc_test_kernel.domain.models.session_provenance import SessionOrigin  # noqa: E402
from platform_db_migration_collect import collect_from_database  # noqa: E402

DEFAULT_SCHEMA_PATH = discover_tree_artifact(__file__, 'docs', 'platform', 'central_db_schema.v1.json')
DEFAULT_MIGRATION_PATH = discover_tree_artifact(
    __file__, 'docs', 'platform', 'migrations', '001_initial_central_db.sql',
)
DEFAULT_MIGRATION_029_PATH = discover_tree_artifact(
    __file__, 'docs', 'platform', 'migrations', '029_web_sample_inventory.sql',
)
DEFAULT_MIGRATIONS_DIR = discover_tree_artifact(__file__, 'docs', 'platform', 'migrations')
DEFAULT_REPORT_PARENT_MIGRATION_PATH = discover_tree_artifact(
    __file__, 'docs', 'platform', 'migrations', '012_report_run_ingestion_parent.sql',
)
DEFAULT_MIGRATION_RUNNER_PATH = discover_tree_artifact(__file__, 'scripts', 'platform_db_migrate.py')
DEFAULT_REGISTRY_PATH = discover_tree_artifact(__file__, 'docs', 'api', 'headless_provider_registry.json')
#: The representative FCC measurement dataset seeded through the production
#: ingestion chain. ``resources/`` is an out-of-scope root in the extraction
#: manifest (bundled assets follow the lane that ships them), so reading it is a
#: data dependency, not a lane crossing.
DEMO_DATASET_PATH = discover_tree_artifact(__file__, 'resources', 'fcc', 'demo_session.json')
ENV_DSN = 'FCC_CENTRAL_DB_URL'
ENV_UPGRADE_DSN = 'FCC_CENTRAL_DB_UPGRADE_URL'
#: The two isolated database lanes this proof drives. Also the key space of the
#: report-reconstruction handoff — a single flat evidence object would let the
#: upgrade lane ingest the fresh lane's outputs, and while the report_run_id
#: determinism check would catch that, it would not say why.
PROOF_LANES = ('fresh', 'upgrade')
#: The command that produces ``--report-reconstruction`` evidence. Named here so a
#: platform-only run says what is missing instead of silently omitting stage 4.
PROVIDER_REPORT_PROOF_COMMAND = 'scripts/provider_report_reconstruction_live_proof.py'
#: The flag that switches this script into report-output ingestion. It is a
#: SEPARATE invocation rather than a stage of the full proof because the full
#: proof requires an empty database (:func:`_empty_database_preflight`) while
#: this one requires the opposite — the database the full proof just seeded.
REPORT_INGESTION_FLAG = '--report-reconstruction'
BASELINE_MIGRATION_SHA = '0fe5d0dae86175d414bd0ff973e033ef58f3e1f6'
MIGRATION_029_VERSION = DEFAULT_MIGRATION_029_PATH.stem
PRE_029_LAST_VERSION = '028_test_plan_read_rbac'
_MIGRATION_FILE_PATTERN = re.compile(r'^(?P<number>\d{3})_.+\.sql$')

# Stable timestamp for deterministic, idempotent re-runs (avoids row churn on the
# provisioned identity graph). The proof asserts behaviour, not wall-clock.
_PROOF_TIMESTAMP = '2026-06-12T10:00:00+00:00'


class LiveProofError(RuntimeError):
    """Raised when a live-proof assertion fails (the proof did not hold)."""


def _connect(dsn: str):
    import psycopg

    return psycopg.connect(dsn)


def _connection_factory(dsn: str):
    def factory():
        return _connect(dsn)

    return factory


def _default_provider_code(registry_path: Path) -> str:
    registry = load_provider_registry(registry_path, PROJECT_ROOT)
    providers = list(registry.providers)
    if not providers:
        raise LiveProofError('provider registry SSOT has no providers')
    return providers[0].provider_id


def _ids(seed: str) -> dict:
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f'fcc-central-live-proof:{seed}')
    return {key: str(uuid.uuid5(namespace, key)) for key in (
        'provider', 'project', 'model', 'sample', 'session', 'result', 'attempt', 'report_run',
    )}


def _provision_identity_graph(dsn: str, ids: dict, provider_code: str, proof_seed: str) -> None:
    """Idempotently seed the FK prerequisite identity graph.

    Represents platform-owned provisioning. ``providers`` is a platform-global
    entity keyed by the natural business code ``provider_id`` (UNIQUE) — the
    proof RESOLVES the existing row rather than creating a per-run duplicate, so
    it composes with whatever provisioned the provider. ``projects/models/
    samples/sessions`` are proof-owned, keyed by deterministic UUIDs derived
    from --proof-seed (idempotent on re-run via ON CONFLICT (id) DO NOTHING).
    ``ids['provider']`` is rebound to the resolved provider id for downstream
    FK references.
    """
    ts = _PROOF_TIMESTAMP
    with _connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO providers(id,provider_id,product_line,contract_family,'
                'contract_version,base_url,capabilities_json,enabled,created_at,updated_at) '
                "VALUES(%s,%s,'unlicensed-conducted','fcc-conducted-headless','v1',"
                "'http://localhost','{}',true,%s,%s) ON CONFLICT (provider_id) DO NOTHING",
                (ids['provider'], provider_code, ts, ts),
            )
            cursor.execute('SELECT id FROM providers WHERE provider_id = %s', (provider_code,))
            ids['provider'] = str(cursor.fetchone()[0])
            # Proof-owned natural keys are seed-scoped so distinct proof seeds
            # (e.g. the CLI 'harness' run and the pytest 'pytest-e2e' run)
            # coexist without colliding on UNIQUE(project_code/sample_code/
            # provider_session_id), while a re-run of the same seed converges.
            cursor.execute(
                'INSERT INTO projects(id,project_code,name,created_at,updated_at) '
                "VALUES(%s,%s,'Central DB Live Proof',%s,%s) ON CONFLICT (id) DO NOTHING",
                (ids['project'], f'PROOF-PRJ-{proof_seed}', ts, ts),
            )
            cursor.execute(
                'INSERT INTO device_models(id,project_id,model_name,created_at,updated_at) '
                'VALUES(%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING',
                (ids['model'], ids['project'], f'PROOF-MODEL-{proof_seed}', ts, ts),
            )
            cursor.execute(
                'INSERT INTO samples(id,project_id,model_id,sample_code,created_at,updated_at) '
                'VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING',
                (ids['sample'], ids['project'], ids['model'], f'PROOF-S1-{proof_seed}', ts, ts),
            )
            cursor.execute(
                'INSERT INTO test_sessions(id,provider_id,project_id,sample_id,'
                'provider_session_id,status,created_at,updated_at) '
                "VALUES(%s,%s,%s,%s,%s,'active',%s,%s) ON CONFLICT (id) DO NOTHING",
                (ids['session'], ids['provider'], ids['project'], ids['sample'],
                 f'PROOF-SESSION-{proof_seed}', ts, ts),
            )
        connection.commit()


def _representative_batch(ids: dict, proof_seed: str) -> dict:
    condition_json = json.dumps({'channel': 0, 'bandwidth': '1MHz'}, sort_keys=True)
    result_json = json.dumps({'value': -3.5, 'unit': 'dBm'}, sort_keys=True)
    # Seed-scope the natural keys: provider_result_id participates in the
    # measurement_results idempotency key (provider_id, provider_result_id) and
    # provider_id is the SHARED resolved provider, so distinct seeds must use
    # distinct provider_result_id to avoid cross-seed collisions.
    condition_hash = f'PROOF-H1-{proof_seed}'
    provider_result_id = f'PROOF-R1-{proof_seed}'
    return {
        'measurement_results': [{
            'id': ids['result'], 'provider_id': ids['provider'], 'session_id': ids['session'],
            'project_id': ids['project'], 'provider_result_id': provider_result_id,
            'test_name': 'PSD', 'technology': 'BLE', 'condition_hash': condition_hash,
            'condition_json': condition_json, 'result_json': result_json,
            'verdict': 'PASS', 'operator': 'live-proof', 'measured_at': _PROOF_TIMESTAMP,
            'created_at': _PROOF_TIMESTAMP,
        }],
        'measurement_attempts': [{
            'id': ids['attempt'], 'provider_id': ids['provider'], 'session_id': ids['session'],
            'project_id': ids['project'], 'test_name': 'PSD', 'technology': 'BLE',
            'condition_hash': condition_hash, 'attempt_number': '1', 'is_latest': True,
            'status': 'completed', 'result_json': result_json, 'verdict': 'PASS',
            'operator': 'live-proof', 'measured_at': _PROOF_TIMESTAMP,
            'created_at': _PROOF_TIMESTAMP, '_fk_provider_result_id': provider_result_id,
        }],
    }


def _database_name(dsn: str) -> str:
    with _connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT current_database()')
        return str(cursor.fetchone()[0])


def _database_identity(dsn: str) -> dict:
    with _connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            'SELECT current_database(), '
            "COALESCE(inet_server_addr()::text, '<local-socket>'), "
            'COALESCE(inet_server_port(), 0)'
        )
        database_name, server, port = cursor.fetchone()
    return {
        'database_name': str(database_name),
        'server': str(server),
        'port': int(port),
        'dsn_target': _safe_dsn(dsn),
    }


def _empty_database_preflight(dsn: str) -> dict:
    with _connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_class AS c "
            "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')"
        )
        table_count = int(cursor.fetchone()[0])
        cursor.execute("SELECT to_regclass('public.schema_migrations')")
        ledger_table = cursor.fetchone()[0]
        cursor.execute("SELECT to_regclass('public.report_runs')")
        report_runs_table = cursor.fetchone()[0]
    result = {
        'public_table_count': table_count,
        'schema_migrations_present': ledger_table is not None,
        'report_runs_present': report_runs_table is not None,
    }
    if table_count or ledger_table is not None or report_runs_table is not None:
        raise LiveProofError(
            f'proof lane must start from an empty isolated database: {result}'
        )
    return result


def _migration_ledger_state(dsn: str) -> dict:
    with _connect(dsn) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT "version", "checksum", "applied_at", "applied_by" '
                    'FROM "schema_migrations" ORDER BY "version"'
                )
                rows = cursor.fetchall()
        except Exception:
            connection.rollback()
            return {
                'exists': False,
                'count': 0,
                'versions': [],
                'rows': [],
            }
    ledger_rows = [
        {
            'version': str(row[0]),
            'checksum': str(row[1]),
            'applied_at': row[2].isoformat() if row[2] is not None else '',
            'applied_by': str(row[3]) if row[3] is not None else '',
        }
        for row in rows
    ]
    return {
        'exists': True,
        'count': len(ledger_rows),
        'versions': [row['version'] for row in ledger_rows],
        'rows': ledger_rows,
    }


def _report_run_created_at_default(dsn: str) -> dict:
    with _connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            'SELECT column_default FROM information_schema.columns '
            "WHERE table_schema = 'public' AND table_name = 'report_runs' "
            "AND column_name = 'created_at'"
        )
        row = cursor.fetchone()
    default = None if row is None else row[0]
    return {
        'table_present': row is not None,
        'default': str(default) if default is not None else None,
    }


def _git_bytes(*args: str) -> bytes:
    result = subprocess.run(
        ['git', *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise LiveProofError(
            f'git command failed ({result.returncode}): '
            f'git {shlex.join(args)}: {result.stderr.decode("utf-8", "replace").strip()}'
        )
    return result.stdout


def _git_cutoff() -> dict:
    status = _git_bytes('status', '--porcelain=v1')
    return {
        'commit': _git_bytes('rev-parse', 'HEAD').decode('utf-8').strip(),
        'status_sha256': hashlib.sha256(status).hexdigest(),
        'status_empty': not status,
    }


def _materialize_pre_012_migrations(directory: Path) -> dict:
    names = _git_bytes(
        'ls-tree', '-r', '--name-only', BASELINE_MIGRATION_SHA,
        'docs/platform/migrations',
    ).decode('utf-8').splitlines()
    selected: list[str] = []
    for name in names:
        match = _MIGRATION_FILE_PATTERN.match(Path(name).name)
        if match and int(match.group('number')) <= 11:
            selected.append(name)
    selected.sort()
    numbers = [int(_MIGRATION_FILE_PATTERN.match(Path(name).name).group('number')) for name in selected]
    if numbers != list(range(1, 12)):
        raise LiveProofError(
            f'baseline pre-012 migration tree is incomplete: {numbers}'
        )

    directory.mkdir(parents=True, exist_ok=True)
    files = []
    for name in selected:
        contents = _git_bytes('show', f'{BASELINE_MIGRATION_SHA}:{name}')
        destination = directory / Path(name).name
        destination.write_bytes(contents)
        files.append({
            'repository_path': name,
            'sha256': hashlib.sha256(contents).hexdigest(),
            'bytes': len(contents),
        })
    return {
        'source_commit': BASELINE_MIGRATION_SHA,
        'version_range': '001..011',
        'file_count': len(files),
        'files': files,
        'temporary_bytes_removed_after_collection': False,
    }


def _materialize_migrations_through(directory: Path, last_version: str) -> dict:
    """Materialize the repository migration set ending before a target migration.

    This is only a temporary input directory for the existing
    ``platform_db_migrate.py`` runner.  It does not execute SQL or implement a
    second ledger/status path; the runner remains the sole migration authority.
    """
    discovered = []
    for path in sorted(DEFAULT_MIGRATIONS_DIR.glob('*.sql')):
        match = _MIGRATION_FILE_PATTERN.match(path.name)
        if match and path.stem <= last_version:
            discovered.append(path)
    if not discovered or discovered[-1].stem != last_version:
        raise LiveProofError(
            f'cannot materialize migrations through {last_version}: '
            f'{[path.stem for path in discovered[-3:]]}'
        )
    directory.mkdir(parents=True, exist_ok=True)
    files = []
    for source in discovered:
        contents = source.read_bytes()
        destination = directory / source.name
        destination.write_bytes(contents)
        files.append({
            'repository_path': str(source.relative_to(PROJECT_ROOT)),
            'sha256': hashlib.sha256(contents).hexdigest(),
            'bytes': len(contents),
        })
    return {
        'source_commit': _git_bytes('rev-parse', 'HEAD').decode('utf-8').strip(),
        'version_range': f'001..{last_version[:3]}',
        'file_count': len(files),
        'files': files,
        'temporary_bytes_removed_after_collection': False,
    }


def _runner_result(
    dsn: str,
    *,
    lane: str,
    command: str,
    migrations_dir: Path,
    migrations_label: str,
    applied_by: str,
) -> dict:
    actual_command = [
        sys.executable,
        str(DEFAULT_MIGRATION_RUNNER_PATH),
        command,
        '--dsn', dsn,
        '--migrations-dir', str(migrations_dir),
        '--applied-by', applied_by,
    ]
    result = subprocess.run(
        actual_command,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=False,
    )
    try:
        returned_json = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise LiveProofError(
            f'migration runner returned non-JSON for {lane}/{command}: '
            f'exit={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}'
        ) from exc
    invocation = [
        Path(sys.executable).name,
        'scripts/platform_db_migrate.py',
        command,
        '--dsn', f'<{lane}-dsn>',
        '--migrations-dir', migrations_label,
        '--applied-by', applied_by,
    ]
    evidence = {
        'command': shlex.join(invocation),
        'exit_code': result.returncode,
        'returned_json': returned_json,
    }
    if result.stderr.strip():
        evidence['stderr'] = result.stderr.strip()
    if result.returncode != 0 or returned_json.get('ok') is False:
        raise LiveProofError(
            f'migration runner failed for {lane}/{command}: {evidence}'
        )
    return evidence


def _migration_manifest(
    dsn: str,
    *,
    schema: dict,
    identity: dict,
    ledger: dict,
    collected_at: str,
) -> dict:
    last_row = ledger['rows'][-1] if ledger.get('rows') else {}
    manifest = collect_from_database(
        dsn=dsn,
        schema=schema,
        schema_contract_bytes=DEFAULT_SCHEMA_PATH.read_bytes(),
        db_schema_name='public',
        migration_id=EXPECTED_MIGRATION_ID,
        migration_status='applied',
        database_name=identity['database_name'],
        applied_at=last_row.get('applied_at') or collected_at,
        applied_by=last_row.get('applied_by') or 'platform_db_migrate',
    )
    migration_issues = [
        issue.to_dict()
        for issue in central_db_migration_evidence_errors(manifest, schema)
    ]
    if migration_issues:
        raise LiveProofError(f'migration evidence invalid: {migration_issues}')
    return manifest


def _seed_pre_029_witnesses(
    dsn: str, *, provider_code: str, proof_seed: str,
) -> dict:
    """Seed truthful legacy session states before migration 029 runs."""
    ids = _ids(f'{proof_seed}:pre-029')
    other_ids = _ids(f'{proof_seed}:pre-029-other')
    ts = _PROOF_TIMESTAMP
    with _connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO providers(id,provider_id,product_line,contract_family,'
                'contract_version,base_url,capabilities_json,enabled,created_at,updated_at) '
                "VALUES(%s,%s,'unlicensed-conducted','fcc-conducted-headless','v1',"
                "'http://localhost','{}',true,%s,%s) ON CONFLICT (provider_id) DO NOTHING",
                (ids['provider'], provider_code, ts, ts),
            )
            cursor.execute('SELECT id FROM providers WHERE provider_id = %s', (provider_code,))
            provider_row = cursor.fetchone()
            if provider_row is None:
                raise LiveProofError('pre-029 witness seed could not resolve provider id')
            provider_id = str(provider_row[0])
            cursor.execute(
                'INSERT INTO projects(id,project_code,name,created_at,updated_at) '
                "VALUES(%s,%s,'Pre-029 Primary Project',%s,%s)",
                (ids['project'], f'PROOF-PRE029-{proof_seed}', ts, ts),
            )
            cursor.execute(
                'INSERT INTO device_models(id,project_id,model_name,created_at,updated_at) '
                'VALUES(%s,%s,%s,%s,%s)',
                (ids['model'], ids['project'], f'PROOF-PRE029-MODEL-{proof_seed}', ts, ts),
            )
            cursor.execute(
                'INSERT INTO projects(id,project_code,name,created_at,updated_at) '
                "VALUES(%s,%s,'Pre-029 Other Project',%s,%s)",
                (other_ids['project'], f'PROOF-OTHER-{proof_seed}', ts, ts),
            )
            cursor.execute(
                'INSERT INTO device_models(id,project_id,model_name,created_at,updated_at) '
                'VALUES(%s,%s,%s,%s,%s)',
                (other_ids['model'], other_ids['project'],
                 f'PROOF-OTHER-MODEL-{proof_seed}', ts, ts),
            )
            cursor.execute(
                'INSERT INTO samples(id,project_id,model_id,sample_code,serial_number,'
                'sample_number,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                (ids['sample'], ids['project'], ids['model'],
                 f'PROOF-PRE029-SAMPLE-{proof_seed}',
                 f'PROOF-PRE029-SERIAL-{proof_seed}', 'PRE029-1', ts, ts),
            )
            cursor.execute(
                'INSERT INTO test_sessions(id,provider_id,provider_session_id,chamber_id,'
                'project_id,sample_id,status,created_at,updated_at,session_origin) '
                "VALUES(%s,%s,%s,%s,%s,%s,'active',%s,%s,%s)",
                (ids['session'], provider_id, f'PROOF-PRE029-COMPLETE-{proof_seed}',
                 'pre-029-complete', ids['project'], ids['sample'], ts, ts,
                 SessionOrigin.WEB_SESSION.value),
            )
            for session_id, provider_session_id, chamber_id, project_id, sample_id, origin in (
                (
                    ids['result'], f'PROOF-PRE029-INCOMPLETE-{proof_seed}',
                    'pre-029-incomplete', ids['project'], None,
                    SessionOrigin.WEB_SESSION.value,
                ),
                (
                    ids['attempt'], f'PROOF-PRE029-MISMATCHED-{proof_seed}',
                    'pre-029-mismatched', other_ids['project'], ids['sample'],
                    SessionOrigin.WEB_SESSION.value,
                ),
                (
                    ids['report_run'], f'PROOF-PRE029-LOCAL-{proof_seed}',
                    'pre-029-local', ids['project'], ids['sample'],
                    SessionOrigin.LOCAL_PROGRAM.value,
                ),
            ):
                cursor.execute(
                    'INSERT INTO test_sessions(id,provider_id,provider_session_id,chamber_id,'
                    'project_id,sample_id,status,created_at,updated_at,session_origin) '
                    "VALUES(%s,%s,%s,%s,%s,%s,'active',%s,%s,%s)",
                    (session_id, provider_id, provider_session_id, chamber_id,
                     project_id, sample_id, ts, ts, origin),
                )
        connection.commit()
    return {
        'provider_id': provider_id,
        'project_id': ids['project'],
        'sample_id': ids['sample'],
        'mismatched_project_id': other_ids['project'],
        'complete_session_id': ids['session'],
        'incomplete_session_id': ids['result'],
        'mismatched_session_id': ids['attempt'],
        'local_session_id': ids['report_run'],
    }


def _read_029_dispositions(dsn: str, witness: dict) -> dict:
    session_ids = {
        key.removesuffix('_session_id'): value
        for key, value in witness.items()
        if key.endswith('_session_id')
    }
    rows: dict[str, dict] = {}
    with _connect(dsn) as connection, connection.cursor() as cursor:
        for label, session_id in session_ids.items():
            cursor.execute(
                'SELECT id::text, project_id::text, sample_id::text, session_origin, '
                'sample_snapshot_json, sample_snapshot_schema_version '
                'FROM test_sessions WHERE id = %s',
                (session_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise LiveProofError(f'029 witness session disappeared: {label}')
            raw_snapshot = row[4]
            snapshot = None
            if raw_snapshot is not None:
                try:
                    snapshot = json.loads(str(raw_snapshot))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise LiveProofError(
                        f'029 witness snapshot is not JSON for {label}'
                    ) from exc
            rows[label] = {
                'session_id': str(row[0]),
                'project_id': str(row[1]) if row[1] is not None else None,
                'sample_id': str(row[2]) if row[2] is not None else None,
                'session_origin': row[3],
                'sample_snapshot_schema_version': row[5],
                'sample_snapshot_sha256': (
                    hashlib.sha256(str(raw_snapshot).encode('utf-8')).hexdigest()
                    if raw_snapshot is not None else None
                ),
                'sample_snapshot_bytes': len(str(raw_snapshot).encode('utf-8'))
                if raw_snapshot is not None else 0,
                'snapshot_project_id': (
                    snapshot.get('project', {}).get('project_id')
                    if isinstance(snapshot, dict) else None
                ),
                'snapshot_sample_id': (
                    snapshot.get('sample', {}).get('sample_id')
                    if isinstance(snapshot, dict) else None
                ),
            }
    return rows


def _029_constraint_state(dsn: str) -> dict:
    with _connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            'SELECT current_setting(%s), version()', ('server_version',),
        )
        server_setting, server_version = cursor.fetchone()
        cursor.execute(
            'SELECT c.convalidated, c.confdeltype, pg_get_constraintdef(c.oid) '
            'FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid '
            'WHERE t.relname = %s AND c.conname = %s',
            ('test_sessions', 'fk_test_sessions_sample_id'),
        )
        fk = cursor.fetchone()
        cursor.execute(
            'SELECT c.convalidated, pg_get_constraintdef(c.oid) '
            'FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid '
            'WHERE t.relname = %s AND c.conname = %s',
            ('test_sessions', 'ck_test_sessions_web_snapshot_complete'),
        )
        check = cursor.fetchone()
    if fk is None or check is None:
        raise LiveProofError('029 required PostgreSQL constraints are missing')
    state = {
        'postgresql_setting': str(server_setting),
        'postgresql_version': str(server_version).split(' on ')[0],
        'fk_sample_id': {
            'convalidated': bool(fk[0]),
            'confdeltype': str(fk[1]),
            'on_delete_set_null': str(fk[1]) == 'n',
            'definition': str(fk[2]),
        },
        'web_snapshot_check': {
            'convalidated': bool(check[0]),
            'definition': str(check[1]),
        },
    }
    if not state['fk_sample_id']['on_delete_set_null']:
        raise LiveProofError(f'029 sample FK is not ON DELETE SET NULL: {state}')
    if not state['web_snapshot_check']['convalidated']:
        raise LiveProofError(f'029 WEB snapshot constraint is not validated: {state}')
    return state


def _hard_delete_fk_proof(
    dsn: str,
    *,
    provider_code: str,
    proof_seed: str,
) -> dict:
    """Use the production hard-delete adapter against a real PostgreSQL FK."""
    ids = _ids(f'{proof_seed}:fk-delete')
    _provision_identity_graph(dsn, ids, provider_code, f'{proof_seed}-fk-delete')
    snapshot = {
        'schema_version': SNAPSHOT_SCHEMA_VERSION,
        'captured_at': _PROOF_TIMESTAMP,
        'project': {
            'project_id': ids['project'],
            'project_code': f'PROOF-PRJ-{proof_seed}-fk-delete',
            'model_name': f'PROOF-MODEL-{proof_seed}-fk-delete',
        },
        'sample': {
            'sample_id': ids['sample'],
            'sample_number': f'PROOF-FK-SAMPLE-{proof_seed}',
            'serial_number': f'PROOF-FK-SERIAL-{proof_seed}',
            'status': 'active',
        },
        'latest_intake': {'bl': f'PROOF-FK-BL-{proof_seed}', 'hw_rev': 'PROOF-FK-HW'},
        'sample_revision': 1,
        'row_version': 1,
    }
    snapshot_bytes = json.dumps(snapshot, sort_keys=True, separators=(',', ':'))
    ts = _PROOF_TIMESTAMP
    with _connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                'UPDATE samples SET sample_number=%s, sample_code=%s, serial_number=%s, '
                'status=%s, row_version=%s, note=%s WHERE id=%s',
                (
                    snapshot['sample']['sample_number'],
                    f'PROOF-FK-CODE-{proof_seed}',
                    snapshot['sample']['serial_number'], 'active', 1,
                    'PROOF-FK-NOTE', ids['sample'],
                ),
            )
            cursor.execute(
                'INSERT INTO sample_intakes('
                'id,sample_id,intake_date,bl,hw_rev,created_at,updated_at) '
                'VALUES(%s,%s,%s,%s,%s,%s,%s)',
                (
                    str(uuid.uuid5(uuid.NAMESPACE_URL, f'{proof_seed}:fk-intake')),
                    ids['sample'], ts, snapshot['latest_intake']['bl'],
                    snapshot['latest_intake']['hw_rev'], ts, ts,
                ),
            )
            cursor.execute(
                'INSERT INTO sample_inventory_revisions('
                'id,sample_id,project_id,revision_number,event_type,snapshot_json,'
                'changed_fields_json,actor_subject,occurred_at,created_at) '
                'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (
                    str(uuid.uuid5(uuid.NAMESPACE_URL, f'{proof_seed}:fk-revision')),
                    ids['sample'], ids['project'], 1, 'created', snapshot_bytes, '[]',
                    'system:029-proof', ts, ts,
                ),
            )
            cursor.execute(
                'UPDATE test_sessions SET project_id=%s, sample_id=%s, '
                'session_origin=%s, sample_snapshot_json=%s, '
                'sample_snapshot_schema_version=%s WHERE id=%s',
                (
                    ids['project'], ids['sample'], SessionOrigin.WEB_SESSION.value,
                    snapshot_bytes, SNAPSHOT_SCHEMA_VERSION, ids['session'],
                ),
            )
            cursor.execute(
                'SELECT count(*) FROM samples WHERE id=%s', (ids['sample'],),
            )
            samples_before = int(cursor.fetchone()[0])
            cursor.execute(
                'SELECT count(*) FROM sample_intakes WHERE sample_id=%s', (ids['sample'],),
            )
            intakes_before = int(cursor.fetchone()[0])
            cursor.execute(
                'SELECT count(*) FROM sample_inventory_revisions WHERE sample_id=%s',
                (ids['sample'],),
            )
            revisions_before = int(cursor.fetchone()[0])
        connection.commit()

    write = PostgresCentralSampleInventoryWriteAdapter(_connection_factory(dsn))
    receipt = write.hard_delete(
        ids['sample'], actor_subject='system:029-proof', occurred_at=ts,
    )
    with _connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM samples WHERE id=%s', (ids['sample'],))
        samples_after = int(cursor.fetchone()[0])
        cursor.execute('SELECT count(*) FROM sample_intakes WHERE sample_id=%s', (ids['sample'],))
        intakes_after = int(cursor.fetchone()[0])
        cursor.execute(
            'SELECT count(*) FROM sample_inventory_revisions WHERE sample_id=%s',
            (ids['sample'],),
        )
        revisions_after = int(cursor.fetchone()[0])
        cursor.execute(
            'SELECT sample_id, project_id, sample_snapshot_json, '
            'sample_snapshot_schema_version FROM test_sessions WHERE id=%s',
            (ids['session'],),
        )
        session_row = cursor.fetchone()
        cursor.execute(
            'SELECT event_type, detail_json FROM audit_events '
            'WHERE event_type=%s AND detail_json->>\'sample_id\'=%s '
            'ORDER BY created_at DESC LIMIT 1',
            ('sample.hard_deleted', ids['sample']),
        )
        audit_row = cursor.fetchone()
    if session_row is None or audit_row is None:
        raise LiveProofError('029 hard-delete proof lost session or audit evidence')
    audit_detail = audit_row[1]
    if isinstance(audit_detail, str):
        audit_detail = json.loads(audit_detail)
    if any(
        value in str(audit_detail)
        for value in (
            snapshot['sample']['serial_number'],
            snapshot['latest_intake']['bl'],
            'PROOF-FK-NOTE',
        )
    ):
        raise LiveProofError('029 hard-delete tombstone contains operational sample values')
    if session_row[0] is not None or str(session_row[1]) != ids['project']:
        raise LiveProofError(f'029 hard-delete FK/session identity mismatch: {session_row}')
    if str(session_row[2]) != snapshot_bytes or session_row[3] != SNAPSHOT_SCHEMA_VERSION:
        raise LiveProofError('029 hard-delete changed immutable session snapshot bytes')
    connection_factory = _connection_factory(dsn)
    report_service = CentralReportService(
        PostgresCentralReportReadAdapter(connection_factory),
        PostgresCentralReportWriteAdapter(connection_factory),
        PostgresCentralProjectReadAdapter(connection_factory),
        clock=lambda: _PROOF_TIMESTAMP,
    )
    citation = report_service.get_report_citation(
        ids['project'], edition='E2V1', session_id=ids['session'],
    )
    cited_samples = citation.get('samples') or []
    if len(cited_samples) != 1:
        raise LiveProofError(
            f'029 hard-delete report citation did not resolve one sample: {citation}'
        )
    cited_sample = cited_samples[0]
    expected_citation = {
        'sample_number': snapshot['sample']['sample_number'],
        'serial_number': snapshot['sample']['serial_number'],
        'latest_firmware': {
            'bl': snapshot['latest_intake']['bl'],
            'hw_rev': snapshot['latest_intake']['hw_rev'],
        },
    }
    actual_citation = {
        'sample_number': cited_sample.get('sample_number'),
        'serial_number': cited_sample.get('serial_number'),
        'latest_firmware': {
            'bl': (cited_sample.get('latest_firmware') or {}).get('bl'),
            'hw_rev': (cited_sample.get('latest_firmware') or {}).get('hw_rev'),
        },
    }
    if actual_citation != expected_citation:
        raise LiveProofError(
            f'029 hard-delete report citation changed immutable fields: '
            f'{actual_citation} != {expected_citation}'
        )
    counts = {
        'samples': {'before': samples_before, 'after': samples_after},
        'sample_intakes': {'before': intakes_before, 'after': intakes_after},
        'sample_inventory_revisions': {'before': revisions_before, 'after': revisions_after},
    }
    if any(value['before'] != 1 or value['after'] != 0 for value in counts.values()):
        raise LiveProofError(f'029 hard-delete operational rows did not reach zero: {counts}')
    return {
        'valid': True,
        'receipt': receipt,
        'session_id': ids['session'],
        'project_id': ids['project'],
        'sample_id': ids['sample'],
        'snapshot_sha256': hashlib.sha256(snapshot_bytes.encode('utf-8')).hexdigest(),
        'snapshot_bytes': len(snapshot_bytes.encode('utf-8')),
        'snapshot_schema_version': SNAPSHOT_SCHEMA_VERSION,
        'counts': counts,
        'fk_sample_id_after_delete': None,
        'audit': {
            'event_type': str(audit_row[0]),
            'detail_keys': sorted(str(key) for key in audit_detail),
            'pii_free': True,
        },
        'retained_session': {
            'project_id': str(session_row[1]),
            'sample_id': None,
            'snapshot_sha256': hashlib.sha256(
                str(session_row[2]).encode('utf-8')
            ).hexdigest(),
            'snapshot_schema_version': str(session_row[3]),
        },
        'report_citation': {
            'project_id': str(citation.get('project_id')),
            'session_id': ids['session'],
            'sample_number': cited_sample['sample_number'],
            'serial_number': cited_sample['serial_number'],
            'latest_firmware': {
                'bl': cited_sample['latest_firmware']['bl'],
                'hw_rev': cited_sample['latest_firmware']['hw_rev'],
            },
            'source': 'production_report_read_adapter_and_service',
        },
    }


def _029_state_snapshot(
    dsn: str,
    *,
    witness: dict | None,
    hard_delete: dict,
) -> dict:
    with _connect(dsn) as connection, connection.cursor() as cursor:
        counts = {}
        for table in ('samples', 'sample_intakes', 'sample_inventory_revisions'):
            cursor.execute(f'SELECT count(*) FROM "{table}"')
            counts[table] = int(cursor.fetchone()[0])
        cursor.execute('SELECT count(*) FROM test_sessions')
        counts['test_sessions'] = int(cursor.fetchone()[0])
    state = {
        'counts': counts,
        'dispositions': _read_029_dispositions(dsn, witness) if witness else {},
        'hard_delete_session': hard_delete.get('retained_session'),
        'hard_delete_counts': hard_delete.get('counts'),
    }
    state['sha256'] = hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()
    return state


def _run_029_migration_proof(
    dsn: str,
    *,
    lane: str,
    proof_seed: str,
    provider_code: str,
    witness: dict | None,
    final_status: dict,
) -> dict:
    constraint = _029_constraint_state(dsn)
    hard_delete = _hard_delete_fk_proof(
        dsn, provider_code=provider_code, proof_seed=f'{proof_seed}:{lane}',
    )
    dispositions = _read_029_dispositions(dsn, witness) if witness else {}
    if witness:
        complete = dispositions['complete']
        if (
            complete['session_origin'] != SessionOrigin.WEB_SESSION.value
            or complete['sample_snapshot_schema_version'] != SNAPSHOT_SCHEMA_VERSION
            or complete['project_id'] != witness['project_id']
            or complete['sample_id'] != witness['sample_id']
            or complete['snapshot_project_id'] != witness['project_id']
            or complete['snapshot_sample_id'] != witness['sample_id']
        ):
            raise LiveProofError(f'029 complete WEB_SESSION backfill is invalid: {complete}')
        for label in ('incomplete', 'mismatched'):
            row = dispositions[label]
            if row['session_origin'] is not None or row['sample_snapshot_sha256'] is not None:
                raise LiveProofError(f'029 {label} witness was fabricated instead of demoted: {row}')
        local = dispositions['local']
        if (
            local['session_origin'] != SessionOrigin.LOCAL_PROGRAM.value
            or local['sample_snapshot_sha256'] is not None
        ):
            raise LiveProofError(f'029 LOCAL_PROGRAM witness changed: {local}')
        with _connect(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                'SELECT count(*) FROM sample_inventory_revisions WHERE sample_id=%s',
                (witness['sample_id'],),
            )
            dispositions['baseline_revision_count'] = int(cursor.fetchone()[0])
        if dispositions['baseline_revision_count'] != 1:
            raise LiveProofError(
                f'029 sample baseline revision count is not one: {dispositions}'
            )

    ledger = _migration_ledger_state(dsn)
    ledger_029 = next(
        (row for row in ledger['rows'] if row['version'] == MIGRATION_029_VERSION),
        None,
    )
    repository_checksum = hashlib.sha256(DEFAULT_MIGRATION_029_PATH.read_bytes()).hexdigest()
    if ledger_029 is None or ledger_029['checksum'] != repository_checksum:
        raise LiveProofError(
            f'029 repository/ledger checksum mismatch: ledger={ledger_029} '
            f'repository={repository_checksum}'
        )
    state_before_rerun = _029_state_snapshot(
        dsn, witness=witness, hard_delete=hard_delete,
    )
    rerun = _runner_result(
        dsn, lane=lane, command='migrate', migrations_dir=DEFAULT_MIGRATIONS_DIR,
        migrations_label='docs/platform/migrations',
        applied_by=f'central-db-live-proof:{proof_seed}:{lane}:029-rerun',
    )
    rerun_status = _runner_result(
        dsn, lane=lane, command='status', migrations_dir=DEFAULT_MIGRATIONS_DIR,
        migrations_label='docs/platform/migrations',
        applied_by=f'central-db-live-proof:{proof_seed}:{lane}:029-rerun',
    )
    state_after_rerun = _029_state_snapshot(
        dsn, witness=witness, hard_delete=hard_delete,
    )
    if state_after_rerun['sha256'] != state_before_rerun['sha256']:
        raise LiveProofError(
            f'029 rerun changed relevant state: before={state_before_rerun} '
            f'after={state_after_rerun}'
        )
    if rerun_status['returned_json'] != final_status:
        raise LiveProofError(
            f'029 rerun changed migration status: before={final_status} '
            f'after={rerun_status["returned_json"]}'
        )
    ledger_after_rerun = _migration_ledger_state(dsn)
    ledger_029_after = next(
        (row for row in ledger_after_rerun['rows'] if row['version'] == MIGRATION_029_VERSION),
        None,
    )
    if ledger_029_after != ledger_029:
        raise LiveProofError(
            f'029 rerun changed ledger row: before={ledger_029} after={ledger_029_after}'
        )
    return {
        'valid': True,
        'lane': lane,
        'migration_version': MIGRATION_029_VERSION,
        'repository_checksum': repository_checksum,
        'ledger_checksum': ledger_029['checksum'],
        'postgresql': constraint,
        'pre_029_dispositions': dispositions or {'not_applicable': True},
        'hard_delete_fk_proof': hard_delete,
        'final_status': final_status,
        'rerun': rerun,
        'rerun_status': rerun_status,
        'state_before_rerun': state_before_rerun,
        'state_after_rerun': state_after_rerun,
        'state_checksum_stable': True,
        'ledger_checksum_stable': True,
    }


def _run_migration_lane(
    dsn: str,
    *,
    lane: str,
    proof_seed: str,
    provider_code: str,
    schema: dict,
    collected_at: str,
    pre_012_directory: Path | None = None,
    pre_012_metadata: dict | None = None,
    pre_029_directory: Path | None = None,
    pre_029_metadata: dict | None = None,
) -> dict:
    identity = _database_identity(dsn)
    preflight = _empty_database_preflight(dsn)
    candidate_label = 'docs/platform/migrations'
    runner: dict[str, dict] = {}
    ledger_before = _migration_ledger_state(dsn)
    default_before = _report_run_created_at_default(dsn)
    pre_012_ledger = None
    pre_012_default = None
    pre_029_witness = None

    if lane == 'fresh':
        runner['candidate_migrate'] = _runner_result(
            dsn, lane=lane, command='migrate', migrations_dir=DEFAULT_MIGRATIONS_DIR,
            migrations_label=candidate_label,
            applied_by=f'central-db-live-proof:{proof_seed}:fresh',
        )
    elif lane == 'upgrade':
        if (
            pre_012_directory is None or pre_012_metadata is None
            or pre_029_directory is None or pre_029_metadata is None
        ):
            raise LiveProofError(
                'upgrade lane requires materialized pre-012 and pre-029 migration trees'
            )
        runner['pre_012_migrate'] = _runner_result(
            dsn, lane=lane, command='migrate', migrations_dir=pre_012_directory,
            migrations_label='<temporary-pre-012-migrations>',
            applied_by=f'central-db-live-proof:{proof_seed}:upgrade-pre-012',
        )
        runner['pre_012_status_after'] = _runner_result(
            dsn, lane=lane, command='status', migrations_dir=pre_012_directory,
            migrations_label='<temporary-pre-012-migrations>',
            applied_by=f'central-db-live-proof:{proof_seed}:upgrade-pre-012',
        )
        pre_012_ledger = _migration_ledger_state(dsn)
        pre_012_default = _report_run_created_at_default(dsn)
        if pre_012_default.get('default') is not None:
            raise LiveProofError(
                f'pre-012 upgrade database unexpectedly has report_runs.created_at '
                f'default: {pre_012_default}'
            )
        runner['candidate_status_before_reconcile'] = _runner_result(
            dsn, lane=lane, command='status', migrations_dir=DEFAULT_MIGRATIONS_DIR,
            migrations_label=candidate_label,
            applied_by=f'central-db-live-proof:{proof_seed}:upgrade',
        )
        runner['candidate_reconcile'] = _runner_result(
            dsn, lane=lane, command='reconcile', migrations_dir=DEFAULT_MIGRATIONS_DIR,
            migrations_label=candidate_label,
            applied_by=f'central-db-live-proof:{proof_seed}:upgrade',
        )
        if runner['candidate_reconcile']['returned_json'].get('reconciled') != [
            '001_initial_central_db'
        ]:
            raise LiveProofError(
                'upgrade lane did not reconcile the exporter-owned 001 checksum'
            )
        runner['candidate_status_after_reconcile'] = _runner_result(
            dsn, lane=lane, command='status', migrations_dir=DEFAULT_MIGRATIONS_DIR,
            migrations_label=candidate_label,
            applied_by=f'central-db-live-proof:{proof_seed}:upgrade',
        )
        runner['pre_029_migrate'] = _runner_result(
            dsn, lane=lane, command='migrate', migrations_dir=pre_029_directory,
            migrations_label='<temporary-pre-029-migrations>',
            applied_by=f'central-db-live-proof:{proof_seed}:upgrade-pre-029',
        )
        runner['pre_029_status_after'] = _runner_result(
            dsn, lane=lane, command='status', migrations_dir=pre_029_directory,
            migrations_label='<temporary-pre-029-migrations>',
            applied_by=f'central-db-live-proof:{proof_seed}:upgrade-pre-029',
        )
        if runner['pre_029_status_after']['returned_json'].get('pending'):
            raise LiveProofError(
                'pre-029 upgrade lane did not settle before witness seed: '
                f"{runner['pre_029_status_after']['returned_json']}"
            )
        pre_029_witness = _seed_pre_029_witnesses(
            dsn, provider_code=provider_code, proof_seed=proof_seed,
        )
        runner['candidate_migrate'] = _runner_result(
            dsn, lane=lane, command='migrate', migrations_dir=DEFAULT_MIGRATIONS_DIR,
            migrations_label=candidate_label,
            applied_by=f'central-db-live-proof:{proof_seed}:upgrade',
        )
    else:
        raise LiveProofError(f'unknown migration proof lane: {lane}')

    runner['candidate_status_after'] = _runner_result(
        dsn, lane=lane, command='status', migrations_dir=DEFAULT_MIGRATIONS_DIR,
        migrations_label=candidate_label,
        applied_by=f'central-db-live-proof:{proof_seed}:{lane}',
    )
    final_status = runner['candidate_status_after']['returned_json']
    if final_status.get('pending') or final_status.get('drift'):
        raise LiveProofError(f'{lane} migration runner is not settled: {final_status}')
    ledger_after = _migration_ledger_state(dsn)
    expected_versions = [
        path.stem for path in sorted(DEFAULT_MIGRATIONS_DIR.glob('*.sql'))
        if _MIGRATION_FILE_PATTERN.match(path.name)
    ]
    if ledger_after['versions'] != expected_versions:
        raise LiveProofError(
            f'{lane} ledger versions {ledger_after["versions"]} != {expected_versions}'
        )
    default_after = _report_run_created_at_default(dsn)
    if default_after.get('default', '').replace(' ', '').lower() != 'now()':
        raise LiveProofError(
            f'{lane} report_runs.created_at is not DB-owned by now(): {default_after}'
        )
    migration_029 = _run_029_migration_proof(
        dsn,
        lane=lane,
        proof_seed=proof_seed,
        provider_code=provider_code,
        witness=pre_029_witness,
        final_status=final_status,
    )
    migration_manifest = _migration_manifest(
        dsn, schema=schema, identity=identity, ledger=ledger_after,
        collected_at=collected_at,
    )
    return {
        'valid': True,
        'lane': lane,
        'database_identity': identity,
        'empty_database_preflight': preflight,
        'runner': runner,
        'ledger_before': ledger_before,
        'ledger_after': ledger_after,
        'applied_version_count': ledger_after['count'],
        'applied_versions': ledger_after['versions'],
        'report_runs_created_at_default_before': default_before,
        'report_runs_created_at_default_after': default_after,
        'pre_012_ledger': pre_012_ledger,
        'pre_012_report_runs_created_at_default': pre_012_default,
        'migration_manifest': migration_manifest,
        'pre_012_migration_materialization': pre_012_metadata,
        'pre_029_migration_materialization': pre_029_metadata,
        'migration_029': migration_029,
    }


def _measurement_state(dsn: str, session_id: str) -> dict:
    with _connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM measurement_results WHERE session_id = %s', (session_id,))
        results = int(cursor.fetchone()[0])
        cursor.execute('SELECT count(*) FROM measurement_attempts WHERE session_id = %s', (session_id,))
        attempts = int(cursor.fetchone()[0])
        cursor.execute(
            'SELECT count(*) FROM measurement_attempts WHERE session_id = %s AND is_latest = true',
            (session_id,),
        )
        latest = int(cursor.fetchone()[0])
        # coverage_by_condition_hash aggregates by (project_id, technology,
        # condition_hash); the latest attempt's session is exposed as
        # latest_session_id (there is no plain session_id column).
        cursor.execute(
            'SELECT count(*) FROM coverage_by_condition_hash WHERE latest_session_id = %s',
            (session_id,),
        )
        coverage = int(cursor.fetchone()[0])
    return {'results': results, 'attempts': attempts, 'is_latest_true': latest, 'coverage': coverage}


def _report_ingestion_state(dsn: str, report_run_id: str) -> dict:
    with _connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            'SELECT "id", "provider_id", "session_id", "status", "created_at" '
            'FROM "report_runs" WHERE "id" = %s',
            (report_run_id,),
        )
        parent_row = cursor.fetchone()
        cursor.execute(
            'SELECT "relative_path" FROM "report_outputs" '
            'WHERE "report_run_id" = %s ORDER BY "relative_path"',
            (report_run_id,),
        )
        output_paths = [str(row[0]) for row in cursor.fetchall()]
    parent = None
    if parent_row is not None:
        parent = {
            'id': str(parent_row[0]),
            'provider_id': str(parent_row[1]),
            'session_id': str(parent_row[2]),
            'status': str(parent_row[3]),
            'created_at': parent_row[4].isoformat() if parent_row[4] is not None else '',
        }
    return {
        'parent_count': 1 if parent is not None else 0,
        'output_count': len(output_paths),
        'output_paths': output_paths,
        'parent': parent,
    }


def _ingest_report_outputs(
    dsn: str,
    *,
    proof_seed: str,
    provider_code: str,
    reconstruction: dict,
) -> dict:
    """Ingest generated report metadata through the production batch/plan/worker chain."""
    ids = _ids(f'{proof_seed}:demo-report')
    provider_id = str(reconstruction.get('provider_id') or '').strip()
    session_id = str(reconstruction.get('session_id') or '').strip()
    project_id = str(reconstruction.get('project_id') or '').strip()
    if not provider_id or not session_id or not project_id:
        raise LiveProofError(
            'report reconstruction did not retain resolved provider/session/project identities'
        )
    report_run_id = str(reconstruction.get('report_run_id') or '')
    if report_run_id != ids['report_run']:
        raise LiveProofError(
            f'reconstruction report_run_id is not deterministic for {proof_seed!r}'
        )
    generated_outputs = list(reconstruction.get('generated_outputs') or [])
    if not generated_outputs:
        raise LiveProofError('real report reconstruction produced no output metadata')

    output_records = []
    for output in generated_outputs:
        relative_path = str(output.get('relative_path') or '').strip()
        file_name = str(output.get('file_name') or Path(relative_path).name).strip()
        if not relative_path or not file_name:
            raise LiveProofError('generated report metadata is missing file_name or relative_path')
        output_records.append({
            'file_name': file_name,
            'relative_path': relative_path,
            'sha256': str(output.get('sha256') or ''),
            'byte_size': output.get('byte_size'),
            'storage_backend': output.get('storage_backend') or '',
        })
    relative_paths = [str(output['relative_path']) for output in output_records]
    if len(set(relative_paths)) != len(relative_paths):
        raise LiveProofError('generated report metadata contains duplicate relative paths')

    report_types = sorted({
        str(output.get('output_type') or '').strip()
        for output in generated_outputs
        if str(output.get('output_type') or '').strip()
    })
    batch = build_platform_ingestion_batch(
        provider_id=provider_id,
        session_id=session_id,
        result_envelopes=[],
        artifact_metadata=[],
        report_outputs=output_records,
        report_run_id=report_run_id,
        report_run_evidence={
            'status': str(reconstruction.get('status') or '').strip(),
            'report_types': report_types,
        },
        provider_session_id=f'PROOF-SESSION-{proof_seed}-demo',
        session_project_id=project_id,
    )
    plan = build_platform_ingestion_plan(batch)
    writer_factory = _connection_factory(dsn)

    def _ingest() -> object:
        return execute_platform_ingestion_plan(
            plan,
            PostgresIngestionWriter(writer_factory),
            retry_policy=IngestionRetryPolicy(max_attempts=3),
        )

    first = _ingest()
    state_after_first = _report_ingestion_state(dsn, report_run_id)
    second = _ingest()
    state_after_second = _report_ingestion_state(dsn, report_run_id)
    expected_output_count = len(output_records)
    if not (first.committed and second.committed):
        raise LiveProofError(
            f'report ingestion did not commit: first={first.errors} second={second.errors}'
        )
    if state_after_first['parent_count'] != 1:
        raise LiveProofError(f'report parent count after first ingest: {state_after_first}')
    if state_after_first['output_count'] != expected_output_count:
        raise LiveProofError(
            f'report output count after first ingest: {state_after_first} '
            f'expected={expected_output_count}'
        )
    if state_after_second != state_after_first:
        raise LiveProofError(
            f'report replay is not idempotent: first={state_after_first} '
            f'second={state_after_second}'
        )
    parent = state_after_first.get('parent') or {}
    expected_parent = {
        'id': report_run_id,
        'provider_id': provider_id,
        'session_id': session_id,
        'status': str(reconstruction.get('status') or '').strip(),
    }
    if {key: parent.get(key) for key in expected_parent} != expected_parent:
        raise LiveProofError(f'report parent evidence mismatch: {parent} != {expected_parent}')
    parent_step = next(
        step for step in plan.steps if step.target_table == 'report_runs'
    )
    if 'created_at' in parent_step.record:
        raise LiveProofError('report_runs mapper supplied created_at; DB ownership was bypassed')
    if not parent.get('created_at'):
        raise LiveProofError('report_runs.created_at was not created by PostgreSQL')

    return {
        'valid': True,
        'report_run_id': report_run_id,
        'provider_code': provider_code,
        'generated_output_paths': relative_paths,
        'expected_output_count': expected_output_count,
        'first_run': first.to_dict(),
        'second_run': second.to_dict(),
        'parent_count_after_first': state_after_first['parent_count'],
        'output_count_after_first': state_after_first['output_count'],
        'parent_count_after_second': state_after_second['parent_count'],
        'output_count_after_second': state_after_second['output_count'],
        'replay_idempotent': state_after_second == state_after_first,
        'parent_created_at_db_owned': True,
        'parent': parent,
        'provider_id': provider_id,
        'session_id': session_id,
        'project_id': project_id,
    }


def _run_ingestion_stages(
    dsn: str,
    *,
    proof_seed: str,
    provider_code: str,
    include_report: bool,
    migration_stage: dict,
    collected_at: str,
) -> dict:
    ids = _ids(proof_seed)

    # ---- Stage 2: ingestion ×2 → replay idempotency ----
    _provision_identity_graph(dsn, ids, provider_code, proof_seed)
    plan = build_platform_ingestion_plan(_representative_batch(ids, proof_seed))
    writer_factory = _connection_factory(dsn)

    def _ingest() -> object:
        return execute_platform_ingestion_plan(
            plan, PostgresIngestionWriter(writer_factory),
            retry_policy=IngestionRetryPolicy(max_attempts=3),
        )

    first = _ingest()
    state_after_first = _measurement_state(dsn, ids['session'])
    second = _ingest()
    state_after_second = _measurement_state(dsn, ids['session'])

    expected = {'results': 1, 'attempts': 1, 'is_latest_true': 1, 'coverage': 1}
    if not (first.committed and second.committed):
        raise LiveProofError(f'ingestion did not commit: first={first.errors} second={second.errors}')
    if state_after_first != expected:
        raise LiveProofError(f'state after first ingest {state_after_first} != {expected}')
    if state_after_second != state_after_first:
        raise LiveProofError(
            f'replay not idempotent: second {state_after_second} != first {state_after_first}'
        )
    if first.coverage_refresh != COVERAGE_REFRESH_SUCCEEDED:
        raise LiveProofError(
            f'coverage refresh not observable-succeeded: {first.coverage_refresh} '
            f'({first.coverage_refresh_error})'
        )

    ingestion_manifest = build_ingestion_execution_manifest(
        evidence_id=f'live-proof-{proof_seed}',
        provider_id=provider_code,
        session_id=ids['session'],
        database_name=_database_name(dsn),
        plan=plan,
        result=second,
        collected_at=collected_at,
    )
    ingestion_issues = [i.to_dict() for i in ingestion_execution_errors(ingestion_manifest)]
    if ingestion_issues:
        raise LiveProofError(f'ingestion evidence invalid: {ingestion_issues}')

    stages = {
        'migration': migration_stage,
        'ingestion_idempotency': {
            'valid': True,
            'first_run': first.to_dict(),
            'second_run': second.to_dict(),
            'state_after_first': state_after_first,
            'state_after_second': state_after_second,
            'replay_idempotent': True,
            'coverage_refresh': first.coverage_refresh,
            'manifest': ingestion_manifest,
        },
    }
    if include_report:
        # ---- Stage 3: seed the report axis and publish the provider handoff ----
        # Seeding the representative FCC demo dataset is platform ingestion (it
        # runs through the same batch/plan/worker chain stage 2 proves). What the
        # platform cannot do is turn those rows into an FCC report — that lives in
        # the provider lane, so this stage publishes the identities that proof
        # needs instead of importing it.
        demo = json.loads(DEMO_DATASET_PATH.read_text(encoding='utf-8'))
        demo_ids = _ingest_demo_measurements(
            dsn, proof_seed=proof_seed, provider_code=provider_code, demo=demo,
        )
        equipment_lists = _provision_report_equipment_lists(dsn, demo_ids, proof_seed)
        stages['report_axis'] = {
            'valid': True,
            # This proof never contains report_ingestion: that stage consumes
            # evidence only a provider can produce, and producing it needs the
            # session this stage just seeded. Naming both follow-up commands is
            # what keeps the absence a stated fact rather than a missing key.
            'report_ingestion_stage_is_a_separate_invocation': True,
            'provider_proof_command': PROVIDER_REPORT_PROOF_COMMAND,
            'report_ingestion_command': REPORT_INGESTION_FLAG,
            'confirmed_equipment_lists': equipment_lists,
            'demo_measurement_seed': {
                'proof_seed': proof_seed,
                'proof_timestamp': _PROOF_TIMESTAMP,
                'provider_code': provider_code,
                'provider_id': demo_ids['provider'],
                'project_id': demo_ids['project'],
                'session_id': demo_ids['session'],
                'report_run_id': demo_ids['report_run'],
                'measurement_result_count': demo_ids['_record_count'],
                # Platform vocabulary. Published rather than left for the
                # provider to spell, because `_ingest_report_outputs` compares
                # the ingested parent's status against whatever the provider
                # sends back — a second literal on that side would drift into a
                # mismatch that reads like a reconstruction failure.
                'report_run_status': REPORT_RUN_COMPLETED_STATUS,
                # Stage 2's session, kept separate on purpose: the DB-only
                # sourcing assertion is about the representative ingestion
                # session, exactly as it was before the axes were split.
                'ingestion_session_id': ids['session'],
                'migration_evidence_id': f'live-proof-{proof_seed}',
                'ingestion_batch_id': f'live-proof-demo-{proof_seed}',
            },
            'note': (
                'measurements are in the central DB; reconstructing the FCC report '
                f'from them is provider work — run {PROVIDER_REPORT_PROOF_COMMAND} '
                f'and feed its evidence back through {REPORT_INGESTION_FLAG}.'
            ),
        }

    return {
        'schema_version': 1,
        'proof': 'central-db-live-proof',
        'generated_at': collected_at,
        'dsn_target': _safe_dsn(dsn),
        'provider_code': provider_code,
        'session_id': ids['session'],
        'stages': stages,
        'verdict': 'PASS',
    }


def run_live_proof(
    dsn: str,
    *,
    upgrade_dsn: str,
    proof_seed: str,
    provider_code: str,
    include_report: bool = True,
) -> dict:
    """Run fresh and pre-012 upgrade lanes against two EMPTY isolated databases."""
    if not dsn or not upgrade_dsn:
        raise LiveProofError(
            f'{ENV_DSN} and {ENV_UPGRADE_DSN} are both required for separate proof lanes'
        )
    cutoff_before = _git_cutoff()
    schema = json.loads(DEFAULT_SCHEMA_PATH.read_text(encoding='utf-8'))
    fresh_identity = _database_identity(dsn)
    upgrade_identity = _database_identity(upgrade_dsn)
    if (
        fresh_identity['database_name'], fresh_identity['server'], fresh_identity['port']
    ) == (
        upgrade_identity['database_name'], upgrade_identity['server'], upgrade_identity['port']
    ):
        raise LiveProofError(
            f'fresh and upgrade proof lanes resolve to the same database identity: '
            f'{fresh_identity}'
        )
    collected_at = datetime.now(timezone.utc).isoformat()

    with tempfile.TemporaryDirectory(prefix='fcc-pre-029-') as temporary_root:
        temporary_root_path = Path(temporary_root)
        pre_012_directory = temporary_root_path / 'pre-012'
        pre_012_metadata = _materialize_pre_012_migrations(pre_012_directory)
        pre_029_directory = temporary_root_path / 'pre-029'
        pre_029_metadata = _materialize_migrations_through(
            pre_029_directory, PRE_029_LAST_VERSION,
        )
        fresh_migration = _run_migration_lane(
            dsn,
            lane='fresh',
            proof_seed=proof_seed,
            provider_code=provider_code,
            schema=schema,
            collected_at=collected_at,
        )
        upgrade_migration = _run_migration_lane(
            upgrade_dsn,
            lane='upgrade',
            proof_seed=proof_seed,
            provider_code=provider_code,
            schema=schema,
            collected_at=collected_at,
            pre_012_directory=pre_012_directory,
            pre_012_metadata=pre_012_metadata,
            pre_029_directory=pre_029_directory,
            pre_029_metadata=pre_029_metadata,
        )
        fresh_migration['pre_012_migration_materialization'] = dict(pre_012_metadata)
        upgrade_migration['pre_012_migration_materialization'] = dict(pre_012_metadata)
        fresh_migration['pre_029_migration_materialization'] = dict(pre_029_metadata)
        upgrade_migration['pre_029_migration_materialization'] = dict(pre_029_metadata)

        fresh_lane = _run_ingestion_stages(
            dsn,
            proof_seed=f'{proof_seed}-fresh',
            provider_code=provider_code,
            include_report=include_report,
            migration_stage=fresh_migration,
            collected_at=collected_at,
        )
        upgrade_lane = _run_ingestion_stages(
            upgrade_dsn,
            proof_seed=f'{proof_seed}-upgrade',
            provider_code=provider_code,
            include_report=include_report,
            migration_stage=upgrade_migration,
            collected_at=collected_at,
        )
    if pre_012_directory.exists() or pre_029_directory.exists():
        raise LiveProofError(
            'temporary pre-029 migration bytes remain after TemporaryDirectory cleanup'
        )
    cutoff_after = _git_cutoff()
    if cutoff_after != cutoff_before:
        raise LiveProofError(
            f'proof cutoff changed during live run: before={cutoff_before} '
            f'after={cutoff_after}'
        )
    pre_012_metadata['temporary_bytes_removed_after_collection'] = True
    pre_029_metadata['temporary_bytes_removed_after_collection'] = True
    fresh_lane['stages']['migration']['pre_012_migration_materialization'] = dict(
        pre_012_metadata
    )
    upgrade_lane['stages']['migration']['pre_012_migration_materialization'] = dict(
        pre_012_metadata
    )
    fresh_lane['stages']['migration']['pre_029_migration_materialization'] = dict(
        pre_029_metadata
    )
    upgrade_lane['stages']['migration']['pre_029_migration_materialization'] = dict(
        pre_029_metadata
    )

    return {
        'schema_version': 1,
        'proof': 'central-db-live-proof',
        'generated_at': collected_at,
        'dsn_target': fresh_identity['dsn_target'],
        'provider_code': provider_code,
        'database_identities': {
            'fresh': fresh_identity,
            'upgrade': upgrade_identity,
        },
        'cutoff': {
            'before': cutoff_before,
            'after': cutoff_after,
            'stable': True,
        },
        'session_id': fresh_lane['session_id'],
        'upgrade_session_id': upgrade_lane['session_id'],
        'lanes': {
            'fresh': fresh_lane,
            'upgrade': upgrade_lane,
        },
        # Compatibility alias for existing focused consumers; the two explicit
        # lane objects above are the authoritative evidence.
        'stages': fresh_lane['stages'],
        'verdict': 'PASS',
    }


#: Bundled equipment master used to stand in for what a tester records through
#: ``replace_test_equipment_list_items``. ``resources/`` is an out-of-scope root.
EQUIPMENT_RESOURCE_PATH = discover_tree_artifact(__file__, 'resources', 'fcc', 'equipment.json')


def _provision_report_equipment_lists(dsn: str, ids: dict, proof_seed: str) -> dict:
    """Idempotently seed a CONFIRMED §6 equipment list per test item.

    Represents platform-owned provisioning, the same way
    :func:`_provision_identity_graph` stands in for the operator registering
    providers and projects. Here the stand-in is for a tester recording the
    instruments actually used through ``replace_test_equipment_list_items``
    (``platform:claim``) — the rows live in central tables either way.

    Without it the report axis cannot be proven live at all: since the §6 central
    cutover (2026-08-08) report generation refuses a technology whose central
    equipment list is missing or unconfirmed, so a freshly migrated database
    blocks every reconstruction. Measured against ``origin/main`` on a pristine
    database, that refusal is identical with and without this wave's split.

    Seeds every :class:`TestItemKey`, not the subset the provider happens to
    reconstruct: which report editions a provider issues is the provider's
    knowledge, and the platform has no business encoding it. Item fields are
    relayed from the bundled equipment master verbatim — no interpretation, so
    no provider vocabulary lands here.
    """
    from fcc_test_kernel.domain.services.test_equipment_list_policy import ItemType, TestItemKey

    master = json.loads(EQUIPMENT_RESOURCE_PATH.read_text(encoding='utf-8'))
    equipment = list(master.get('equipment') or [])
    if not equipment:
        raise LiveProofError('bundled equipment master has no equipment rows')

    ts = _PROOF_TIMESTAMP
    seeded = {}
    with _connect(dsn) as connection:
        with connection.cursor() as cursor:
            for item_key in TestItemKey:
                list_id = str(uuid.uuid5(
                    uuid.NAMESPACE_URL, f'{proof_seed}:equipment-list:{item_key.value}',
                ))
                cursor.execute(
                    'INSERT INTO test_equipment_lists('
                    'id,project_id,test_item_key,test_item_name,status,confirmed_at,'
                    'created_at,updated_at) '
                    'VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING',
                    (list_id, ids['project'], item_key.value, item_key.value,
                     'confirmed', ts, ts, ts),
                )
                cursor.execute(
                    'DELETE FROM test_equipment_list_items WHERE list_id = %s', (list_id,),
                )
                for index, row in enumerate(equipment):
                    calibrations = list(row.get('calibrations') or [])
                    cursor.execute(
                        'INSERT INTO test_equipment_list_items('
                        'id,list_id,item_type,sort_order,description,manufacturer,'
                        'model_name,serial_number,calibration_due_date,created_at) '
                        'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (
                            str(uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f'{proof_seed}:equipment-item:{item_key.value}:{index}',
                            )),
                            list_id, ItemType.EQUIPMENT.value, index,
                            str(row.get('kind') or ''), str(row.get('manufacturer') or ''),
                            str(row.get('model') or ''), str(row.get('serial') or ''),
                            str((calibrations[0] if calibrations else {}).get('due_date') or ''),
                            ts,
                        ),
                    )
                seeded[item_key.value] = len(equipment)
        connection.commit()
    return seeded


def _ingest_demo_measurements(dsn: str, *, proof_seed: str, provider_code: str, demo: dict) -> dict:
    """Ingest the canonical demo measurement dataset into the central DB.

    The 39 representative rows become central ``measurement_results`` (the same
    dataset the local report demo uses). Reference config (equipment/frequency/
    antenna-gain) is supplied separately from canonical resources — NOT Excel —
    exactly as the local DB report path sources them.
    """
    ids = _ids(f'{proof_seed}:demo-report')
    _provision_identity_graph(dsn, ids, provider_code, f'{proof_seed}-demo')
    session_meta = demo.get('session') or {}
    metadata_json = json.dumps({
        'model_number': session_meta.get('model_number', ''),
        'file_structure': session_meta.get('file_structure', ''),
    }, sort_keys=True)
    records = []
    for index, raw in enumerate(demo.get('rows') or []):
        row = dict(raw)
        row['row_order'] = index
        payload = json.dumps(row, sort_keys=True)
        records.append({
            'id': str(uuid.uuid5(uuid.NAMESPACE_URL, f'{proof_seed}:demo-res:{index}')),
            'provider_id': ids['provider'], 'session_id': ids['session'], 'project_id': ids['project'],
            'provider_result_id': f'demo-{index}', 'test_name': str(raw.get('test_name') or 'measurement'),
            'technology': str(raw.get('technology') or 'BLE'), 'condition_hash': f'demo-{proof_seed}-{index}',
            'condition_json': payload, 'result_json': payload,
            'verdict': str(raw.get('pass_fail') or ''), 'operator': 'live-proof',
            'measured_at': _PROOF_TIMESTAMP, 'created_at': _PROOF_TIMESTAMP,
        })
    with _connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                'UPDATE test_sessions SET metadata_json = %s WHERE id = %s',
                (metadata_json, ids['session']),
            )
            cursor.execute('DELETE FROM measurement_results WHERE session_id = %s', (ids['session'],))
        connection.commit()
    result = execute_platform_ingestion_plan(
        build_platform_ingestion_plan({'measurement_results': records}),
        PostgresIngestionWriter(_connection_factory(dsn)),
        retry_policy=IngestionRetryPolicy(max_attempts=3),
    )
    if not result.committed:
        raise LiveProofError(f'demo measurement ingestion failed: {result.errors}')
    ids['_record_count'] = len(records)
    return ids


def _ooo_condition_hash(proof_seed: str) -> str:
    return f'{proof_seed}-OOO'


def _ooo_attempt_timestamp(number: int) -> str:
    """Give the out-of-order witness an explicit central recency sequence.

    ``attempt_number`` is a session-local idempotency key, not the central
    latest-order key. The witness therefore varies the canonical timestamp
    fields so the production writer's recency policy has a deterministic
    newer attempt to preserve when a stale attempt is replayed.
    """
    return (datetime.fromisoformat(_PROOF_TIMESTAMP) + timedelta(seconds=number)).isoformat()


def _ingest_numbered_attempt(dsn: str, ids: dict, proof_seed: str, number: int) -> object:
    result_json = json.dumps({'value': -3.5, 'unit': 'dBm'}, sort_keys=True)
    condition_hash = _ooo_condition_hash(proof_seed)
    provider_result_id = f'{proof_seed}-OOO-R{number}'
    occurred_at = _ooo_attempt_timestamp(number)
    batch = {
        'measurement_results': [{
            'id': str(uuid.uuid5(uuid.NAMESPACE_URL, f'{proof_seed}:ooo-res:{number}')),
            'provider_id': ids['provider'], 'session_id': ids['session'], 'project_id': ids['project'],
            'provider_result_id': provider_result_id, 'test_name': 'PSD', 'technology': 'BLE',
            'condition_hash': condition_hash, 'condition_json': result_json, 'result_json': result_json,
            'verdict': 'PASS', 'operator': 'live-proof', 'measured_at': occurred_at,
            'created_at': occurred_at,
        }],
        'measurement_attempts': [{
            'id': str(uuid.uuid5(uuid.NAMESPACE_URL, f'{proof_seed}:ooo-att:{number}')),
            'provider_id': ids['provider'], 'session_id': ids['session'], 'project_id': ids['project'],
            'test_name': 'PSD', 'technology': 'BLE', 'condition_hash': condition_hash,
            'attempt_number': str(number), 'is_latest': True, 'status': 'completed',
            'result_json': result_json, 'verdict': 'PASS', 'operator': 'live-proof',
            'measured_at': occurred_at, 'created_at': occurred_at,
            '_fk_provider_result_id': provider_result_id,
        }],
    }
    return execute_platform_ingestion_plan(
        build_platform_ingestion_plan(batch),
        PostgresIngestionWriter(_connection_factory(dsn)),
        retry_policy=IngestionRetryPolicy(max_attempts=3),
    )


def _latest_attempt_numbers(dsn: str, ids: dict, proof_seed: str) -> list:
    with _connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            'SELECT "attempt_number" FROM "measurement_attempts" '
            'WHERE "session_id" = %s AND "condition_hash" = %s AND "is_latest" = true '
            'ORDER BY "attempt_number"',
            (ids['session'], _ooo_condition_hash(proof_seed)),
        )
        return [row[0] for row in cursor.fetchall()]


def run_out_of_order_replay_proof(dsn: str, *, proof_seed: str, provider_code: str) -> dict:
    """Prove is_latest is order-independent under at-least-once delivery.

    Ingest attempts in the order [1, 2, 1, 2]: a newer attempt (2) becomes
    latest, then a STALE older attempt (1) is re-delivered out of order. The
    DB-authoritative recompute must keep the MAX(attempt_number)=2 row latest —
    a stale or replayed attempt must NOT demote a newer latest, and no rows
    duplicate.
    """
    ids = _ids(f'{proof_seed}:ooo')
    _provision_identity_graph(dsn, ids, provider_code, f'{proof_seed}-ooo')
    with _connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM measurement_attempts WHERE session_id = %s', (ids['session'],))
            cursor.execute('DELETE FROM measurement_results WHERE session_id = %s', (ids['session'],))
        connection.commit()

    sequence = []
    for number in (1, 2, 1, 2):
        result = _ingest_numbered_attempt(dsn, ids, proof_seed, number)
        if not result.committed:
            raise LiveProofError(f'out-of-order ingest of attempt {number} did not commit: {result.errors}')
        sequence.append({'ingested': number, 'latest': _latest_attempt_numbers(dsn, ids, proof_seed)})

    final_latest = _latest_attempt_numbers(dsn, ids, proof_seed)
    with _connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM measurement_attempts WHERE session_id = %s', (ids['session'],))
        attempt_count = int(cursor.fetchone()[0])
    if final_latest != [2]:
        raise LiveProofError(
            f'out-of-order regression: latest {final_latest} != [2] '
            '(a stale/out-of-order attempt demoted the newer latest)'
        )
    if attempt_count != 2:
        raise LiveProofError(f'out-of-order regression: attempt rows duplicated ({attempt_count} != 2)')
    return {
        'valid': True,
        'ingest_sequence': sequence,
        'final_latest': final_latest,
        'attempt_count': attempt_count,
        'note': 'MAX(attempt_number) is latest, order-independent; stale/replay cannot demote newer latest.',
    }


#: The key under which a provider proof publishes, per lane, the evidence object
#: that ``_ingest_report_outputs`` consumes.
RECONSTRUCTION_EVIDENCE_KEY = 'db_only_report_reconstruction'


def run_report_ingestion_proof(
    dsn: str,
    *,
    upgrade_dsn: str,
    proof_seed: str,
    provider_code: str,
    reconstruction_by_lane: dict,
) -> dict:
    """Ingest provider-generated report output metadata — the runbook's step 3.

    A separate entry point rather than a stage of :func:`run_live_proof`, and the
    reason is a hard precondition rather than taste: the full proof refuses to
    start against anything but an empty database, while this one requires the
    exact opposite — the database that proof already migrated and seeded. Folding
    them together would make one of the two preconditions unstatable.

    It re-derives nothing about identity. ``_ingest_report_outputs`` recomputes
    the deterministic ``report_run_id`` for the same ``--proof-seed`` and refuses
    a mismatch, so a bundle produced for one seed cannot be ingested under
    another.
    """
    if not dsn or not upgrade_dsn:
        raise LiveProofError(
            f'{ENV_DSN} and {ENV_UPGRADE_DSN} are both required for separate proof lanes'
        )
    dsn_by_lane = {'fresh': dsn, 'upgrade': upgrade_dsn}
    collected_at = datetime.now(timezone.utc).isoformat()
    lanes = {}
    for lane in PROOF_LANES:
        lanes[lane] = {'stages': {'report_ingestion': _ingest_report_outputs(
            dsn_by_lane[lane],
            proof_seed=f'{proof_seed}-{lane}',
            provider_code=provider_code,
            reconstruction=reconstruction_by_lane[lane],
        )}}
    return {
        'schema_version': 1,
        'proof': 'central-db-report-ingestion-proof',
        'generated_at': collected_at,
        'provider_code': provider_code,
        'lanes': lanes,
        'stages': lanes['fresh']['stages'],
        'verdict': 'PASS',
    }


def _load_report_reconstruction(path: Path) -> dict:
    """Read per-lane report reconstruction evidence produced by a provider proof.

    The handoff format is not a new schema: the per-lane object is exactly the
    ``reconstruction`` mapping :func:`_ingest_report_outputs` already took as a
    parameter, so that function is unchanged and keeps owning every rejection
    (missing identities, non-deterministic report_run_id, empty or duplicated
    output metadata, parent evidence mismatch).

    Every failure here is loud. A missing file or a missing lane is the operator
    running the runbook out of order, and skipping quietly would leave stage 4
    absent from a bundle that claims to include it.
    """
    if not path.is_file():
        raise LiveProofError(
            f'--report-reconstruction {path} does not exist — run '
            f'{PROVIDER_REPORT_PROOF_COMMAND} first'
        )
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except ValueError as exc:
        raise LiveProofError(f'--report-reconstruction {path} is not valid JSON: {exc}') from exc
    lanes = payload.get('lanes') if isinstance(payload, dict) else None
    if not isinstance(lanes, dict):
        raise LiveProofError(
            f'--report-reconstruction {path} has no "lanes" object — expected one '
            f'entry per proof lane {list(PROOF_LANES)}'
        )
    resolved: dict = {}
    for lane in PROOF_LANES:
        entry = lanes.get(lane)
        if not isinstance(entry, dict):
            raise LiveProofError(
                f'--report-reconstruction {path} has no {lane!r} lane; found '
                f'{sorted(lanes)}'
            )
        evidence = entry.get(RECONSTRUCTION_EVIDENCE_KEY)
        if not isinstance(evidence, dict):
            raise LiveProofError(
                f'--report-reconstruction {path} lane {lane!r} has no '
                f'{RECONSTRUCTION_EVIDENCE_KEY!r} object'
            )
        resolved[lane] = evidence
    return resolved


def _safe_dsn(dsn: str) -> str:
    # Never echo credentials into evidence — keep host/db only.
    tail = dsn.rsplit('@', 1)[-1] if '@' in dsn else dsn
    return f'postgresql://.../{tail}'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Central platform DB end-to-end live proof.')
    parser.add_argument('--dsn', default=os.environ.get(ENV_DSN, ''))
    parser.add_argument('--upgrade-dsn', default=os.environ.get(ENV_UPGRADE_DSN, ''))
    parser.add_argument('--proof-seed', default='default')
    parser.add_argument('--provider-code', default='')
    parser.add_argument('--registry', default=str(DEFAULT_REGISTRY_PATH))
    parser.add_argument('--output', default='')
    parser.add_argument('--skip-report', action='store_true',
                        help='skip the report axis entirely (no demo measurement seed, '
                             'no report output ingestion)')
    parser.add_argument(REPORT_INGESTION_FLAG, default='',
                        help='per-lane report reconstruction evidence produced by '
                             f'{PROVIDER_REPORT_PROOF_COMMAND}. Switches this script into '
                             'report-output ingestion against the ALREADY seeded databases '
                             '(the full proof requires empty ones), and is the runbook step 3')
    args = parser.parse_args(argv)

    if not args.dsn or not args.upgrade_dsn:
        print(json.dumps({
            'verdict': 'SKIPPED',
            'reason': f'{ENV_DSN} and {ENV_UPGRADE_DSN} / --dsn and --upgrade-dsn are required',
        }, indent=2))
        return 3
    provider_code = args.provider_code or _default_provider_code(Path(args.registry))

    try:
        if args.report_reconstruction:
            if args.skip_report:
                raise LiveProofError(
                    f'{REPORT_INGESTION_FLAG} and --skip-report contradict each other'
                )
            bundle = run_report_ingestion_proof(
                args.dsn,
                upgrade_dsn=args.upgrade_dsn,
                proof_seed=args.proof_seed,
                provider_code=provider_code,
                reconstruction_by_lane=_load_report_reconstruction(
                    Path(args.report_reconstruction)
                ),
            )
            payload = json.dumps(bundle, sort_keys=True, indent=2)
            if args.output:
                output_path = Path(args.output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(payload + '\n', encoding='utf-8')
            print(json.dumps({'verdict': bundle['verdict'], 'proof': bundle['proof']}, indent=2))
            return 0
        bundle = run_live_proof(
            args.dsn,
            upgrade_dsn=args.upgrade_dsn,
            proof_seed=args.proof_seed,
            provider_code=provider_code,
            include_report=not args.skip_report,
        )
        bundle['stages']['out_of_order_replay'] = run_out_of_order_replay_proof(
            args.dsn, proof_seed=args.proof_seed, provider_code=provider_code,
        )
    except Exception as exc:  # noqa: BLE001 — surface any live failure verbatim
        print(json.dumps({
            'verdict': 'FAIL',
            'error': {'type': type(exc).__name__, 'message': str(exc)},
        }, sort_keys=True, indent=2))
        return 1

    payload = json.dumps(bundle, sort_keys=True, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + '\n', encoding='utf-8')
    print(json.dumps({'verdict': bundle['verdict'], 'session_id': bundle['session_id']}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
