"""Collect independent migration-030 / selection-evidence lane receipts.

This runner applies the repository migrations to each explicitly supplied
disposable lane, reruns the migration command, compares protected data and
ledger checksums, and executes the production selection/publication/ingestion
path against generated proof rows. It never guesses that a missing lane passed
and it never falls back to the operational Compose DSN. Fresh and exact
pre-030-upgrade databases are evaluated independently; a missing upgrade DSN
does not prevent a configured fresh lane from producing its own result, but the
combined receipt remains ``BLOCKED`` until both disposable lanes are available.

The runner is suitable for the SHA-scoped evidence directory once a code cutoff
has been frozen::

    FCC_CENTRAL_DB_URL=... FCC_CENTRAL_DB_UPGRADE_URL=... \
      PYTHONPATH=src:. python scripts/cross_session_result_selection_evidence.py \
      --json-output .claude/evidence/.../migration-030-fresh.json

DSNs are redacted in receipts. Migration and generated proof-row application are
limited to the supplied disposable databases and are cleaned up by generated
project/provider identity. A final run supplies ``--cutoff`` and
``--receipt-dir`` whose basename is that immutable code SHA; missing or dirty
cutoff state cannot produce a PASS receipt.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit
import uuid


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402

# Repository-relative artifacts are named the way the repository names them and
# located by asking the packager's own layout record where they went.
#
# ⚠️ **This used to call ``discover_tree_artifact`` and that was the defect, not
# a style choice.** The two functions answer different questions: the discovery
# walk answers DEPTH ("which ancestor holds ``docs/``"), while the packager
# *relocates* ``docs/platform/migrations/`` to ``migrations/`` at the box root.
# Once this file became platform-owned it travelled into a box where no ancestor
# holds ``docs/`` at all, so the walk fell back to the outermost ancestor and
# produced a path that is simply not there — ``EvidenceBlocked: migration file
# is missing`` for a file the box actually received.  ``resolve_repo_artifact``
# reads the record the packager wrote, which is the distinction this manifest's
# own ``delivered_layout_note`` draws and the repair its
# ``delivered_test_run_baseline`` note prescribed by name.
#
# Identity in the monorepo: with no layout record the resolver returns the
# joined repository-relative path, byte-identical to what this file computed
# before, which is why nothing about a local run changes.
MIGRATIONS_DIR = resolve_repo_artifact(__file__, 'docs/platform/migrations')
MIGRATION_PATH = MIGRATIONS_DIR / '030_cross_session_test_result_selection.sql'
SCHEMA_PATH = resolve_repo_artifact(
    __file__, 'docs/platform/central_db_schema.v1.json'
)
FRESH_ENV = 'FCC_CENTRAL_DB_URL'
UPGRADE_ENV = 'FCC_CENTRAL_DB_UPGRADE_URL'
EXPECTED_MIGRATION = '030_cross_session_test_result_selection'
PRE_030_MIGRATION = '029_web_sample_inventory'
EXPECTED_COLUMNS = {
    'test_sessions': {
        'project_result_reference_snapshot_json',
        'project_result_reference_snapshot_schema_version',
    },
}
PROTECTED_COLUMNS = {
    'projects': ('id', 'project_code', 'name', 'status', 'created_at', 'updated_at'),
    'providers': (
        'id', 'provider_id', 'product_line', 'contract_family',
        'contract_version', 'base_url', 'capabilities_json', 'enabled',
        'created_at', 'updated_at',
    ),
    'test_sessions': (
        'id', 'provider_id', 'provider_session_id', 'chamber_id', 'project_id',
        'sample_id', 'status', 'started_at', 'completed_at', 'metadata_json',
        'session_origin', 'workbook_handle',
    ),
    'measurement_results': (
        'id', 'provider_id', 'session_id', 'project_id', 'provider_result_id',
        'test_name', 'technology', 'condition_json', 'result_json', 'verdict',
        'measured_at', 'created_at', 'condition_hash', 'operator',
    ),
    'measurement_attempts': (
        'id', 'provider_id', 'session_id', 'project_id', 'measurement_result_id',
        'test_name', 'technology', 'condition_hash', 'attempt_number', 'is_latest',
        'operator', 'status', 'verdict', 'result_json', 'run_id',
        'idempotency_key', 'recorded_by', 'provenance_json', 'measured_at',
        'created_at',
    ),
}


class EvidenceBlocked(RuntimeError):
    """A required disposable lane is not available for evidence collection."""


def _safe_dsn(dsn: str) -> str:
    """Return a non-secret DSN identity suitable for a receipt."""
    try:
        parsed = urlsplit(dsn)
        if parsed.scheme and parsed.netloc:
            host = parsed.hostname or '<host>'
            port = f':{parsed.port}' if parsed.port else ''
            netloc = f'<user>@{host}{port}'
            # Query parameters can carry passwords/tokens for some PostgreSQL
            # drivers. Keep only the non-secret connection identity.
            return urlunsplit((parsed.scheme, netloc, parsed.path, '', ''))
    except ValueError:
        pass
    return '<redacted-dsn>'


def _connect(dsn: str):
    try:
        import psycopg  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        raise EvidenceBlocked('psycopg is not installed') from exc
    try:
        return psycopg.connect(
            dsn,
            connect_timeout=5,
            application_name='fcc-cross-session-result-selection-evidence',
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        raise EvidenceBlocked('disposable PostgreSQL evidence DSN is unreachable') from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _proof_uuid(namespace: uuid.UUID, label: str) -> str:
    return str(uuid.uuid5(namespace, label))


def _ledger_snapshot(connection) -> dict[str, Any]:
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT version, checksum FROM schema_migrations ORDER BY version'
            )
            rows = cursor.fetchall()
    except Exception:
        connection.rollback()
        rows = []
    return {str(version): str(checksum) for version, checksum in rows}


def _protected_data_snapshot(connection) -> dict[str, Any]:
    """Hash stable pre-030 columns, excluding the paired columns being added."""
    tables: dict[str, Any] = {}
    with connection.cursor() as cursor:
        for table, columns in PROTECTED_COLUMNS.items():
            quoted = ', '.join(f'"{column}"' for column in columns)
            try:
                cursor.execute(
                    f'SELECT COUNT(*), md5(COALESCE(string_agg('
                    'to_jsonb(snapshot_row)::text, E\'\\n\' ORDER BY snapshot_row."id"::text), \'\')) '
                    f'FROM (SELECT {quoted} FROM "{table}" ORDER BY "id"::text) snapshot_row'
                )
                count, digest = cursor.fetchone()
                tables[table] = {
                    'exists': True,
                    'count': int(count),
                    'sha256': hashlib.sha256(str(digest or '').encode('utf-8')).hexdigest(),
                }
            except Exception:
                connection.rollback()
                tables[table] = {
                    'exists': False,
                    'count': 0,
                    'sha256': hashlib.sha256(b'').hexdigest(),
                }
    rendered = json.dumps(tables, sort_keys=True, separators=(',', ':'))
    return {
        'tables': tables,
        'sha256': hashlib.sha256(rendered.encode('utf-8')).hexdigest(),
    }


#: Environment variables by which git decides *which repository it is looking
#: at*.  They beat ``cwd``, and that is the whole point of listing them here.
#:
#: ⚠️ **``cwd=ROOT`` is not isolation — it is isolation only while the
#: environment says nothing.** git hands ``GIT_DIR`` down to every hook it
#: runs, so any receipt this script builds from inside a hook (or from any
#: tool that exports one) describes *the ambient repository*, not ``ROOT``.
#: Measured 2026-08-31 in this tree: with ``GIT_DIR`` pointing at a linked
#: worktree, a directory that is **not a git repository at all** attested a
#: HEAD and a cleanliness — the exact silent-green shape ``_run`` below was
#: rewritten to forbid, reappearing one axis over.
#:
#: ⚠️ **The list is not "every GIT_* variable".**  ``GIT_AUTHOR_*`` and
#: ``GIT_CONFIG_*`` have nothing to do with location, and stripping broadly
#: would stop being "pin the repository" and start being "run a different
#: git".  What is removed here changes *which repository answers*, nothing
#: else.
GIT_REPO_LOCATION_ENV = (
    'GIT_DIR',
    'GIT_WORK_TREE',
    'GIT_COMMON_DIR',
    'GIT_INDEX_FILE',
    'GIT_OBJECT_DIRECTORY',
    'GIT_NAMESPACE',
)


def git_env_pinned_to_root() -> dict[str, str]:
    """An environment that does not inherit somebody else's repository.

    Outside a hook the stripped variables are absent, so this is a **no-op**
    and every receipt is byte-identical to before.
    """
    env = dict(os.environ)
    for name in GIT_REPO_LOCATION_ENV:
        env.pop(name, None)
    return env


def repository_metadata(cutoff: str | None) -> dict[str, Any]:
    """What a receipt must be able to say about the tree that produced it.

    Public because it is not this runner's private habit — it is the rule every
    receipt in this feature obeys. The benchmark receipt did not obey it and an
    independent reviewer caught exactly that: a receipt that reports numbers but
    cannot name the SHA it measured proves nothing about the frozen cutoff, no
    matter how good the numbers are. One definition, two callers.
    """

    def _run(*args: str) -> tuple[str, bool]:
        """``(stdout, answered)`` — the second half is the point of this shape.

        ⚠️ **This used to return ``''`` for both "git said nothing" and "git was
        never there", and those are not the same fact.** A delivered box is not
        a git repository, so every call raised, every answer was empty, and the
        receipt this function builds went out saying ``head: ''`` with
        ``clean: true`` — *a tree that could not be looked at attesting that it
        is clean*. That is the silent-green shape this repository names
        everywhere else, and it was reachable in production: a provider team
        running this script in its delivered tree got exactly that receipt.
        """
        try:
            return subprocess.run(
                args, cwd=ROOT, check=True, capture_output=True, text=True,
                env=git_env_pinned_to_root(),
            ).stdout, True
        except Exception:
            return '', False

    head_out, head_ok = _run('git', 'rev-parse', 'HEAD')
    status_out, status_ok = _run('git', 'status', '--short', '--untracked-files=all')
    head = head_out.strip()
    status = status_out
    observed = head_ok and status_ok
    status_hash = hashlib.sha256(status.encode('utf-8')).hexdigest()
    return {
        'head': head,
        'requested_cutoff': cutoff or 'UNFROZEN',
        'cutoff_matches_head': bool(cutoff and cutoff == head),
        # ⚠️ ``clean`` keeps its four-key contract and its meaning *in a git
        # tree*, byte-identical to before.  What changed is that it is no longer
        # the only thing a reader has: ``observed`` below says whether anyone
        # was able to look, so ``clean: true`` can never again be read as an
        # attestation when it is really an absence.
        'clean': observed and not status,
        'status_sha256': status_hash,
        'status_entries': len(status.splitlines()) if status else 0,
        # ⚠️ **Additive, like ``status_paths`` below it — the four keys above do
        # not change.** This one records whether the tree could be *read* at
        # all, which is the fact ``clean`` alone cannot carry: ``False`` here
        # means no git answered and every git-derived field is an absence rather
        # than an observation.  In every tree that ever produced a stored
        # receipt this is ``True``, so no existing receipt changes meaning.
        'repository_observed': observed,
        # ⚠️ **추가 키다. 위 넷은 바뀌지 않는다** — `status_sha256` 은 다른 receipt 이
        # 이미 싣고 있는 관측이고, 그 명령을 바꾸면 옛 영수증과 비교 불가능해진다.
        # 아래는 *분류* 이지 새 관측이 아니다: 같은 `git status` 를 경로 단위로 다시 물어,
        # **무엇이** 더러운지를 판정자가 볼 수 있게 한다.
        'status_paths': _status_paths(),
    }


def _status_paths() -> tuple[str, ...]:
    """작업 트리에서 더러운 경로 목록.

    ``-z`` 는 선택이 아니다 — git 기본값은 한글·공백 경로를 인용/이스케이프하고 이 저장소는
    그런 경로를 실제로 갖는다. 이스케이프된 이름은 접두사 비교에서 조용히 빗나가고, 그러면
    판정자가 «증거 디렉터리 밖은 깨끗하다» 고 답하면서 실제로는 그렇지 않다.
    """
    try:
        completed = subprocess.run(
            ['git', 'status', '--porcelain', '-z', '--untracked-files=all'],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
    except Exception:
        return ()
    paths: list[str] = []
    for entry in completed.stdout.split('\0'):
        if len(entry) > 3:
            paths.append(entry[3:])
    return tuple(paths)


# Retained so existing call sites keep reading the same name; the public
# spelling above is what other receipt producers import.
_repository_metadata = repository_metadata


def _redacted_command(argv: Sequence[str] | None, *, fresh_dsn: str, upgrade_dsn: str) -> str:
    values = {value for value in (fresh_dsn, upgrade_dsn) if value}
    raw = list(argv) if argv is not None else list(sys.argv[1:])
    redacted: list[str] = []
    redact_next = False
    for item in raw:
        if redact_next:
            redacted.append('<redacted-dsn>')
            redact_next = False
            continue
        if item in {'--fresh-dsn', '--upgrade-dsn'}:
            redacted.append(item)
            redact_next = True
            continue
        redacted.append('<redacted-dsn>' if item in values else item)
    return shlex.join(['python', 'scripts/cross_session_result_selection_evidence.py', *redacted])


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _migration_file_metadata() -> dict[str, Any]:
    if not MIGRATION_PATH.is_file():
        raise EvidenceBlocked(f'migration file is missing: {MIGRATION_PATH}')
    contents = MIGRATION_PATH.read_bytes()
    return {
        'path': str(MIGRATION_PATH.relative_to(ROOT)),
        'sha256': hashlib.sha256(contents).hexdigest(),
        'bytes': len(contents),
        'migration_id': EXPECTED_MIGRATION,
    }


def _schema_metadata() -> dict[str, Any]:
    if not SCHEMA_PATH.is_file():
        raise EvidenceBlocked(f'central schema contract is missing: {SCHEMA_PATH}')
    return {
        'path': str(SCHEMA_PATH.relative_to(ROOT)),
        'sha256': _file_hash(SCHEMA_PATH),
        'bytes': SCHEMA_PATH.stat().st_size,
    }


def _database_identity(connection) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT current_database(), '
            "COALESCE(inet_server_addr()::text, '<local-socket>'), "
            'COALESCE(inet_server_port(), 0), version()'
        )
        database, server, port, version = cursor.fetchone()
    return {
        'database_name': str(database),
        'server': str(server),
        'port': int(port),
        'postgres_version': str(version),
    }


def _catalog_fingerprint(catalog: Mapping[str, Any]) -> str:
    rendered = json.dumps(catalog, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(rendered.encode('utf-8')).hexdigest()


def _apply_migrations(*, dsn: str, lane: str, rerun: bool = False) -> dict[str, Any]:
    """Apply all repository migrations through the existing guarded runner."""
    try:
        from scripts.platform_db_migrate import migrate
    except ImportError:  # pragma: no cover - direct script invocation fallback
        from platform_db_migrate import migrate
    result = migrate(
        dsn=dsn,
        migrations_dir=MIGRATIONS_DIR,
        applied_by=(
            f'cross-session-result-selection-evidence:{lane}:'
            f'{"rerun" if rerun else "apply"}'
        ),
    )
    return dict(result)


def _ledger_row(connection) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        try:
            cursor.execute(
                'SELECT version, checksum, applied_at, applied_by '
                'FROM schema_migrations WHERE version = %s',
                (EXPECTED_MIGRATION,),
            )
        except Exception:
            connection.rollback()
            return None
        row = cursor.fetchone()
    if row is None:
        return None
    return {
        'version': str(row[0]),
        'checksum': str(row[1]),
        'applied_at': row[2].isoformat() if row[2] is not None else '',
        'applied_by': str(row[3]) if row[3] is not None else '',
    }


def _catalog_snapshot(connection) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT table_name, column_name, data_type, is_nullable '
            'FROM information_schema.columns '
            "WHERE table_schema = 'public' AND table_name IN "
            "('test_sessions', 'project_result_selection_events', "
            "'project_result_reference_revisions') "
            'ORDER BY table_name, ordinal_position'
        )
        columns = [
            {
                'table': str(row[0]),
                'column': str(row[1]),
                'data_type': str(row[2]),
                'is_nullable': str(row[3]),
            }
            for row in cursor.fetchall()
        ]
        cursor.execute(
            'SELECT tablename, indexname, indexdef FROM pg_indexes '
            "WHERE schemaname = 'public' AND tablename IN "
            "('project_result_selection_events', 'project_result_reference_revisions') "
            'ORDER BY tablename, indexname'
        )
        indexes = [
            {'table': str(row[0]), 'name': str(row[1]), 'definition': str(row[2])}
            for row in cursor.fetchall()
        ]
    return {'columns': columns, 'indexes': indexes}


def _jsonb(value: Mapping[str, Any]):
    from psycopg.types.json import Jsonb  # type: ignore

    return Jsonb(dict(value))


def _seed_live_rows(connection, *, lane: str, run_id: str) -> dict[str, str]:
    """Seed two cross-session conditions and their provider-scoped attempts."""
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f'fcc-cross-session-evidence:{lane}:{run_id}')
    ids = {
        'project_id': _proof_uuid(namespace, 'project'),
        'provider_uuid': _proof_uuid(namespace, 'provider'),
        'provider_id': f'evidence-{lane}-{run_id}',
        'session_a': _proof_uuid(namespace, 'session-a'),
        'session_b': _proof_uuid(namespace, 'session-b'),
        'attempt_a_old': _proof_uuid(namespace, 'attempt-a-old'),
        'attempt_a_new': _proof_uuid(namespace, 'attempt-a-new'),
        'attempt_b': _proof_uuid(namespace, 'attempt-b'),
        'event_a': _proof_uuid(namespace, 'event-a'),
        'event_b': _proof_uuid(namespace, 'event-b'),
        'event_clear': _proof_uuid(namespace, 'event-clear'),
        'reference_id': _proof_uuid(namespace, 'reference'),
    }
    started = datetime.now(timezone.utc).replace(microsecond=0)
    with connection.cursor() as cursor:
        cursor.execute(
            'INSERT INTO providers '
            '(id, provider_id, product_line, contract_family, contract_version, '
            'base_url, capabilities_json, enabled, created_at, updated_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)',
            (
                ids['provider_uuid'], ids['provider_id'], 'evidence-provider',
                'fcc-cross-session-evidence', '1.0.0',
                'http://cross-session-evidence.invalid',
                _jsonb({'evidence': True}), started, started,
            ),
        )
        cursor.execute(
            'INSERT INTO projects '
            '(id, project_code, name, status, created_at, updated_at) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (
                ids['project_id'], f'EVIDENCE-{lane}-{run_id}',
                'Cross-session result-selection evidence', 'active', started, started,
            ),
        )
        cursor.executemany(
            'INSERT INTO test_sessions '
            '(id, provider_id, provider_session_id, chamber_id, project_id, status, '
            'started_at, completed_at, metadata_json) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
            [
                (
                    ids['session_a'], ids['provider_uuid'], f'{run_id}-session-a',
                    f'evidence-chamber-{lane}', ids['project_id'], 'completed',
                    started, started, _jsonb({'evidence_run': run_id}),
                ),
                (
                    ids['session_b'], ids['provider_uuid'], f'{run_id}-session-b',
                    f'evidence-chamber-{lane}', ids['project_id'], 'completed',
                    started, started, _jsonb({'evidence_run': run_id}),
                ),
            ],
        )
        attempt_rows = [
            (
                ids['attempt_a_old'], ids['provider_uuid'], ids['session_a'],
                ids['project_id'], 'evidence-selection', 'FCC-EVIDENCE',
                'evidence-condition-a', 1, False, 'evidence-operator', 'completed',
                'pass', _jsonb({'condition': 'a', 'attempt': 'old'}),
                f'evidence:{run_id}', f'evidence:{run_id}:a:old', 'evidence-seed',
                _jsonb({'evidence_run': run_id}), started, started,
            ),
            (
                ids['attempt_a_new'], ids['provider_uuid'], ids['session_b'],
                ids['project_id'], 'evidence-selection', 'FCC-EVIDENCE',
                'evidence-condition-a', 2, True, 'evidence-operator', 'completed',
                'pass', _jsonb({'condition': 'a', 'attempt': 'new'}),
                f'evidence:{run_id}', f'evidence:{run_id}:a:new', 'evidence-seed',
                _jsonb({'evidence_run': run_id}), started, started,
            ),
            (
                ids['attempt_b'], ids['provider_uuid'], ids['session_a'],
                ids['project_id'], 'evidence-selection', 'FCC-EVIDENCE',
                'evidence-condition-b', 1, True, 'evidence-operator', 'completed',
                'pass', _jsonb({'condition': 'b', 'attempt': 'only'}),
                f'evidence:{run_id}', f'evidence:{run_id}:b:only', 'evidence-seed',
                _jsonb({'evidence_run': run_id}), started, started,
            ),
        ]
        cursor.executemany(
            'INSERT INTO measurement_attempts '
            '(id, provider_id, session_id, project_id, test_name, technology, '
            'condition_hash, attempt_number, is_latest, operator, status, verdict, '
            'result_json, run_id, idempotency_key, recorded_by, provenance_json, '
            'measured_at, created_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '
            '%s, %s, %s, %s, %s)',
            attempt_rows,
        )
    connection.commit()
    return ids


def _cleanup_live_rows(connection, ids: Mapping[str, str]) -> dict[str, int | str]:
    deleted: dict[str, int] = {}
    with connection.cursor() as cursor:
        for table, statement in (
            (
                'project_result_reference_revisions',
                'DELETE FROM project_result_reference_revisions WHERE project_id = %s',
            ),
            (
                'project_result_selection_events',
                'DELETE FROM project_result_selection_events WHERE project_id = %s',
            ),
            (
                'measurement_attempts',
                'DELETE FROM measurement_attempts WHERE project_id = %s',
            ),
            ('test_sessions', 'DELETE FROM test_sessions WHERE project_id = %s'),
            ('projects', 'DELETE FROM projects WHERE id = %s'),
            ('providers', 'DELETE FROM providers WHERE id = %s'),
        ):
            value = ids['provider_uuid'] if table == 'providers' else ids['project_id']
            cursor.execute(statement, (value,))
            deleted[table] = int(cursor.rowcount or 0)
    connection.commit()
    remaining: dict[str, int] = {}
    with connection.cursor() as cursor:
        for table, column, value in (
            ('project_result_reference_revisions', 'project_id', ids['project_id']),
            ('project_result_selection_events', 'project_id', ids['project_id']),
            ('measurement_attempts', 'project_id', ids['project_id']),
            ('test_sessions', 'project_id', ids['project_id']),
            ('projects', 'id', ids['project_id']),
            ('providers', 'id', ids['provider_uuid']),
        ):
            cursor.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = %s', (value,))
            remaining[table] = int(cursor.fetchone()[0])
    return {
        'status': 'PASS' if not any(remaining.values()) else 'FAIL',
        'deleted_rows': deleted,
        'remaining_rows': remaining,
    }


def _read_session_snapshot(connection, session_id: str) -> tuple[str | None, str | None]:
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT project_result_reference_snapshot_json, '
            'project_result_reference_snapshot_schema_version '
            'FROM test_sessions WHERE id = %s',
            (session_id,),
        )
        row = cursor.fetchone()
    return (None, None) if row is None else (row[0], row[1])


def _read_session_ingestion_identity(
    dsn: str,
    ids: Mapping[str, str],
) -> tuple[str, str]:
    """Read the seeded Session natural key before building the ingest plan.

    The live proof deliberately seeds the parent first. Reconstructing its
    ``provider_session_id`` or ``chamber_id`` in this helper would turn a
    primary-key replay into an attempted insert against a different natural
    key. The production writer remains the only write path; this read merely
    binds the proof plan to the parent row that central already owns.
    """
    connection = _connect(dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT provider_id, provider_session_id, chamber_id '
                'FROM test_sessions WHERE id = %s',
                (ids['session_a'],),
            )
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError('central test-session parent row is missing')
    if any(value is None for value in row):
        raise RuntimeError('central test-session parent natural identity is incomplete')
    provider_id, provider_session_id, chamber_id = (str(value) for value in row)
    if provider_id != str(ids['provider_uuid']):
        raise RuntimeError('central test-session parent provider identity mismatched')
    if not provider_session_id or not chamber_id:
        raise RuntimeError('central test-session parent natural identity is incomplete')
    return provider_session_id, chamber_id


def _ingest_reference_snapshot(dsn: str, ids: Mapping[str, str], snapshot_json: str,
                               snapshot_version: str) -> dict[str, Any]:
    from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch
    from fcc_test_platform.provider_ingestion_plan import build_platform_ingestion_plan
    from fcc_test_platform.provider_ingestion_worker import execute_platform_ingestion_plan
    from fcc_test_platform.postgres_ingestion_writer import PostgresIngestionWriter

    provider_session_id, chamber_id = _read_session_ingestion_identity(dsn, ids)
    opened = []

    def connection_factory():
        connection = _connect(dsn)
        opened.append(connection)
        return connection

    writer = PostgresIngestionWriter(connection_factory)

    def plan_for(value: str):
        batch = build_platform_ingestion_batch(
            provider_id=ids['provider_uuid'],
            session_id=ids['session_a'],
            result_envelopes=(),
            provider_session_id=provider_session_id,
            chamber_id=chamber_id,
            session_project_id=ids['project_id'],
            session_status='completed',
            session_project_result_reference_snapshot_json=value,
            session_project_result_reference_snapshot_schema_version=snapshot_version,
        )
        return build_platform_ingestion_plan(batch)

    def execute(plan):
        result = execute_platform_ingestion_plan(plan, writer)
        return result.to_dict()

    logger = logging.getLogger('application.headless.platform_postgres_ingestion_writer')
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        first = execute(plan_for(snapshot_json))
        retry = execute(plan_for(snapshot_json))
        conflicting = json.dumps(
            json.loads(snapshot_json), ensure_ascii=False, indent=2, sort_keys=True,
        )
        conflict = execute(plan_for(conflicting))
    finally:
        logger.removeHandler(handler)
        handler.close()
        for connection in opened:
            connection.close()
    return {
        'first': first,
        'retry': retry,
        'conflict': conflict,
        'conflict_warning_observed': 'central test-session reference snapshot replay conflicted'
        in stream.getvalue(),
        'conflicting_bytes_sha256': hashlib.sha256(conflicting.encode('utf-8')).hexdigest(),
    }


def _verdict(predicates: Mapping[str, Any]) -> str:
    """Derive PASS/FAIL from **every** predicate, and refuse anything that is not one.

    Two failure modes made this a function instead of an expression, and both were
    live in this file before it existed.

    * The receipt verdict used to name its eleven predicates by hand. A twelfth
      assertion added later would have been written into the receipt, read by a
      human as evidence, and **silently excluded from the verdict** — the seal
      would still say PASS. Deriving over the mapping means a new predicate
      participates the moment it exists; you cannot forget to enlist it.
    * The obvious repair — ``all(assertions.values())`` — was worse than the
      defect. ``missing_columns`` lived in that same mapping as a *diagnostic*
      (`{}` when nothing is missing, populated when something is), so ``all``
      would have read the healthy tree as ``False`` and the broken one as
      ``True``: the verdict inverted, in the safe-looking direction.

    So the type is the boundary. A non-``bool`` here is a programming error, not
    a datum, and it raises rather than voting — a truthy diagnostic must never be
    able to answer a question about correctness. Diagnostics belong in
    ``receipt['diagnostics']``, where nothing reads them as a verdict.
    """
    offenders = sorted(
        f'{name}={type(value).__name__}'
        for name, value in predicates.items()
        if not isinstance(value, bool)
    )
    if offenders:
        raise TypeError(
            'evidence predicates must be bool; diagnostics do not vote: '
            + ', '.join(offenders)
        )
    if not predicates:
        raise ValueError('an empty predicate set cannot answer PASS')
    return 'PASS' if all(predicates.values()) else 'FAIL'


def _snapshot_ingestion_assertions(
    ingest: Mapping[str, Any], *, stored_json: str | None, snapshot_json: str,
) -> dict[str, bool]:
    """Evaluate the required first/retry/conflict snapshot receipt semantics."""
    return {
        'first_snapshot_committed': bool(ingest['first']['committed']),
        'identical_retry_committed': bool(ingest['retry']['committed']),
        'conflicting_replay_preserved_first_bytes': (
            bool(ingest['conflict']['committed'])
            and bool(ingest['conflict_warning_observed'])
            and stored_json == snapshot_json
        ),
    }


def _run_live_proof(dsn: str, *, lane: str, run_id: str) -> dict[str, Any]:
    """Exercise selection, trusted publication, retirement, and snapshot ingest."""
    started = _now()
    ids: dict[str, str] | None = None
    cleanup: dict[str, Any] = {'status': 'NOT_RUN'}
    result: dict[str, Any] = {
        'lane': lane,
        'status': 'FAIL',
        'started_at': started,
        'fixture': {'run_id': run_id},
    }
    seed_connection = None
    try:
        seed_connection = _connect(dsn)
        ids = _seed_live_rows(seed_connection, lane=lane, run_id=run_id)
        result['fixture'] = {
            'run_id': run_id,
            'project_id': ids['project_id'],
            'provider_id': ids['provider_id'],
            'provider_uuid': ids['provider_uuid'],
            'session_count': 2,
            'attempt_count': 3,
            'condition_count': 2,
        }
        seed_connection.close()
        seed_connection = None

        from fcc_test_platform.application.central_project_reference_adapter import (
            PostgresCentralProjectReferenceAdapter,
        )
        from fcc_test_platform.application.central_project_reference_service import (
            CentralProjectReferenceService,
        )
        from fcc_test_platform.application.central_result_selection_adapter import (
            PostgresCentralResultSelectionAdapter,
        )
        from domain.models.project_result_reference import (
            REFERENCE_SESSION_SNAPSHOT_SCHEMA_VERSION,
            canonical_payload_hash,
            validate_reference_session_snapshot_json,
        )
        from domain.ports.output.central_project_reference_port import ReferenceRetiredError
        from domain.ports.output.central_result_selection_port import SelectionRevisionConflictError

        def connection_factory():
            return _connect(dsn)

        selection = PostgresCentralResultSelectionAdapter(connection_factory)
        reference_port = PostgresCentralProjectReferenceAdapter(connection_factory)

        def append(event_id: str):
            try:
                return ('won', selection.append_selection_event(
                    event_id=event_id,
                    project_id=ids['project_id'],
                    provider_id=ids['provider_id'],
                    condition_hash='evidence-condition-a',
                    action='selected',
                    attempt_id=ids['attempt_a_old'],
                    expected_revision=0,
                    actor_subject='evidence-examiner',
                    reason='live CAS proof',
                ))
            except SelectionRevisionConflictError:
                return ('conflict', None)

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(append, (ids['event_a'], ids['event_b'])))
        winners = [item for item in outcomes if item[0] == 'won']
        conflicts = [item for item in outcomes if item[0] == 'conflict']
        if len(winners) != 1 or len(conflicts) != 1:
            raise RuntimeError(f'live CAS did not produce one winner/one conflict: {outcomes}')
        winner = winners[0][1]

        first_page = selection.list_effective_results(
            ids['project_id'], ids['provider_id'], limit=1,
        )
        second_page = selection.list_effective_results(
            ids['project_id'], ids['provider_id'], limit=1,
            cursor=first_page['next_cursor'],
        )
        source = selection.selected_source(
            ids['project_id'], ids['provider_id'], 'evidence-condition-a',
        )
        if source is None:
            raise RuntimeError('live selected-source query returned no row')

        class EvidenceProvider:
            provider_id = ids['provider_id']

            @staticmethod
            def accepts(reference_type: str, schema_version: str) -> bool:
                return (reference_type, schema_version) == (
                    'fcc.evidence.reference', '1.0',
                )

            @staticmethod
            def export(selected_source: Mapping[str, Any]) -> Mapping[str, Any]:
                payload = {
                    'condition_hash': selected_source['condition_hash'],
                    'selected_attempt_id': str(selected_source['attempt_id']),
                    'source_session_id': str(selected_source['session_id']),
                }
                return {
                    'provider_id': ids['provider_id'],
                    'reference_type': 'fcc.evidence.reference',
                    'schema_version': '1.0',
                    'payload': payload,
                    'content_sha256': canonical_payload_hash(payload),
                    'attempt_id': str(selected_source['attempt_id']),
                }

        reference_service = CentralProjectReferenceService(
            reference_port,
            selection_port=selection,
            provider_resolver={ids['provider_id']: EvidenceProvider()},
            revision_id_factory=lambda: ids['reference_id'],
        )
        published = reference_service.publish(
            project_id=ids['project_id'], provider_id=ids['provider_id'],
            condition_hash='evidence-condition-a', actor_subject='evidence-examiner',
        )
        snapshot_json, snapshot_version = reference_service.build_session_reference_snapshot(
            project_id=ids['project_id'],
            consumer_provider_id=ids['provider_id'],
            requests=[{
                'revision_id': published['revision_id'],
                'reference_type': published['reference_type'],
                'schema_version': published['schema_version'],
            }],
        )
        validate_reference_session_snapshot_json(
            snapshot_json, project_id=ids['project_id'], schema_version=snapshot_version,
        )
        retired = reference_service.retire(
            published['revision_id'], actor_subject='evidence-examiner',
            reason='live retirement proof',
        )
        retirement_rejected = False
        try:
            reference_service.resolve(
                project_id=ids['project_id'], consumer_provider_id=ids['provider_id'],
                revision_id=published['revision_id'],
                reference_type=published['reference_type'],
                schema_version=published['schema_version'],
            )
        except ReferenceRetiredError:
            retirement_rejected = True

        ingest = _ingest_reference_snapshot(
            dsn, ids, snapshot_json, snapshot_version,
        )
        read_connection = _connect(dsn)
        try:
            stored_json, stored_version = _read_session_snapshot(
                read_connection, ids['session_a'],
            )
        finally:
            read_connection.close()

        ingestion_assertions = _snapshot_ingestion_assertions(
            ingest, stored_json=stored_json, snapshot_json=snapshot_json,
        )
        clear = selection.append_selection_event(
            event_id=ids['event_clear'], project_id=ids['project_id'],
            provider_id=ids['provider_id'], condition_hash='evidence-condition-a',
            action='cleared', attempt_id=None, expected_revision=1,
            actor_subject='evidence-examiner', reason='live clear proof',
        )
        source_after_clear = selection.selected_source(
            ids['project_id'], ids['provider_id'], 'evidence-condition-a',
        )
        result.update({
            'assertions': {
                'cas_one_winner': len(winners) == 1,
                'cas_one_conflict': len(conflicts) == 1,
                'effective_page_one_has_next_cursor': bool(first_page['next_cursor']),
                'effective_page_two_is_distinct_condition': (
                    len(second_page['items']) == 1
                    and second_page['items'][0]['condition_hash'] == 'evidence-condition-b'
                ),
                'selected_source_full_row': set(source) >= {
                    'selection_event_id', 'selection_action', 'selection_revision',
                    'attempt_id', 'project_id', 'provider_id', 'condition_hash',
                    'session_id', 'provider_session_id', 'sample_id', 'chamber_id',
                    'result_json', 'provenance_json', 'status',
                },
                'selected_source_is_manual_old_attempt': (
                    str(source['attempt_id']) == ids['attempt_a_old']
                    and str(source['selection_event_id']) == str(winner['id'])
                ),
                'trusted_publication_stored': (
                    str(published['source_attempt_id']) == ids['attempt_a_old']
                    and published['state'] == 'published'
                ),
                'retirement_stored': (
                    retired['state'] == 'retired' and retirement_rejected
                ),
                'snapshot_schema_version': snapshot_version == REFERENCE_SESSION_SNAPSHOT_SCHEMA_VERSION,
                'snapshot_bytes_canonical': (
                    isinstance(snapshot_json, str)
                    and len(snapshot_json.encode('utf-8')) > 0
                ),
                'central_snapshot_first_bytes': stored_json == snapshot_json,
                'central_snapshot_version': stored_version == snapshot_version,
                **ingestion_assertions,
                'clear_removed_selected_source': clear['revision'] == 2 and source_after_clear is None,
            },
            'winner': {'event_id': str(winner['id']), 'revision': winner['revision']},
            'paging': {
                'first_page': first_page,
                'second_page': second_page,
            },
            'published': {
                'revision_id': str(published['revision_id']),
                'source_attempt_id': str(published['source_attempt_id']),
                'content_sha256': str(published['content_sha256']),
            },
            'snapshot': {
                'schema_version': snapshot_version,
                'bytes': len(snapshot_json.encode('utf-8')),
                'sha256': hashlib.sha256(snapshot_json.encode('utf-8')).hexdigest(),
            },
            'ingestion': ingest,
        })
        result['status'] = _verdict(result['assertions'])
    except Exception as exc:
        result['status'] = 'FAIL'
        result['error'] = type(exc).__name__
        result['error_message'] = str(exc)[:300]
    finally:
        if seed_connection is not None:
            seed_connection.close()
        if ids is not None:
            cleanup_connection = None
            try:
                cleanup_connection = _connect(dsn)
                cleanup = _cleanup_live_rows(cleanup_connection, ids)
            except Exception as exc:
                cleanup = {'status': 'FAIL', 'error': type(exc).__name__}
            finally:
                if cleanup_connection is not None:
                    cleanup_connection.close()
        result['cleanup'] = cleanup
        result['finished_at'] = _now()
        if result['status'] == 'PASS' and cleanup.get('status') != 'PASS':
            result['status'] = 'FAIL'
    return result


def _lane(dsn: str, lane: str, *, cutoff: str | None = None,
          command: str = '') -> dict[str, Any]:
    started = _now()
    receipt: dict[str, Any] = {
        'lane': lane,
        'status': 'BLOCKED',
        'started_at': started,
        'dsn_target': _safe_dsn(dsn),
        'code_cutoff': cutoff or 'UNFROZEN',
        'command': command,
    }
    connection = None
    try:
        connection = _connect(dsn)
        receipt['database'] = _database_identity(connection)
        receipt['migration_file'] = _migration_file_metadata()
        receipt['schema_contract'] = _schema_metadata()
        ledger_before = _ledger_snapshot(connection)
        protected_before = _protected_data_snapshot(connection)
        receipt['ledger_before'] = ledger_before
        receipt['protected_data_before'] = protected_before
        receipt['migration_ledger_before'] = _ledger_row(connection)
        receipt['catalog_before'] = _catalog_snapshot(connection)
        receipt['catalog_before_sha256'] = _catalog_fingerprint(
            receipt['catalog_before']
        )
        if lane == 'fresh' and ledger_before:
            raise RuntimeError(
                'fresh lane is not empty; use a generated fresh disposable database'
            )
        if lane == 'upgrade' and (
            PRE_030_MIGRATION not in ledger_before
            or EXPECTED_MIGRATION in ledger_before
        ):
            raise RuntimeError(
                'upgrade lane must be an exact pre-030 database with migration 029 applied'
            )
        connection.close()
        connection = None
        receipt['migration_apply'] = _apply_migrations(dsn=dsn, lane=lane)
        receipt['migration_rerun'] = _apply_migrations(
            dsn=dsn, lane=lane, rerun=True,
        )
        connection = _connect(dsn)
        ledger_after = _ledger_snapshot(connection)
        protected_after = _protected_data_snapshot(connection)
        receipt['ledger_after'] = ledger_after
        receipt['protected_data_after'] = protected_after
        receipt['migration_ledger_after'] = _ledger_row(connection)
        receipt['catalog_after'] = _catalog_snapshot(connection)
        receipt['catalog_after_sha256'] = _catalog_fingerprint(
            receipt['catalog_after']
        )
        live = _run_live_proof(
            dsn, lane=lane, run_id=uuid.uuid4().hex[:12],
        )
        receipt['live_proof'] = live
        receipt['cleanup'] = live.get('cleanup', {'status': 'FAIL'})
        present = {
            table: {
                column['column']
                for column in receipt['catalog_after']['columns']
                if column['table'] == table
            }
            for table in EXPECTED_COLUMNS
        }
        missing = {
            table: sorted(columns - present.get(table, set()))
            for table, columns in EXPECTED_COLUMNS.items()
            if columns - present.get(table, set())
        }
        nullable_pair = {
            column['column']: column['is_nullable']
            for column in receipt['catalog_after']['columns']
            if column['table'] == 'test_sessions'
            and column['column'] in EXPECTED_COLUMNS['test_sessions']
        }
        expected_checksum = _file_hash(MIGRATION_PATH)
        preserved_ledger = all(
            ledger_after.get(version) == checksum
            for version, checksum in ledger_before.items()
        )
        protected_unchanged = (
            protected_before['sha256'] == protected_after['sha256']
            if lane == 'upgrade'
            else not any(
                table.get('count', 0) for table in protected_before['tables'].values()
            ) and not any(
                table.get('count', 0) for table in protected_after['tables'].values()
            )
        )
        receipt['assertions'] = {
            'migration_ledger_row_present': receipt['migration_ledger_after'] is not None,
            'migration_rerun_noop': not receipt['migration_rerun'].get('applied'),
            'migration_checksum_current': ledger_after.get(EXPECTED_MIGRATION) == expected_checksum,
            'preexisting_ledger_checksums_preserved': preserved_ledger,
            'protected_data_unchanged': protected_unchanged,
            'fresh_lane_empty_before_migration': (
                not ledger_before if lane == 'fresh' else True
            ),
            'upgrade_lane_exact_pre_030': (
                PRE_030_MIGRATION in ledger_before
                and EXPECTED_MIGRATION not in ledger_before
                if lane == 'upgrade' else True
            ),
            'paired_columns_nullable': nullable_pair == {
                'project_result_reference_snapshot_json': 'YES',
                'project_result_reference_snapshot_schema_version': 'YES',
            },
            **{
                f'selection_table_present.{table}': any(
                    row['table'] == table for row in receipt['catalog_after']['columns']
                )
                for table in (
                    'project_result_selection_events',
                    'project_result_reference_revisions',
                )
            },
            'reference_snapshot_columns_present': not missing,
            'live_selection_snapshot_proof': live.get('status') == 'PASS',
        }
        # `missing_columns` names *which* columns are absent, so it is evidence for a
        # reader, not a vote. It stays out of the predicate mapping — see `_verdict`.
        receipt['diagnostics'] = {'missing_columns': missing}
        receipt['status'] = _verdict(receipt['assertions'])
    except EvidenceBlocked as exc:
        receipt['blocked_reason'] = str(exc)
    except Exception as exc:  # pragma: no cover - database dependent
        receipt['status'] = 'FAIL'
        receipt['error'] = type(exc).__name__
        receipt['error_message'] = str(exc)[:300]
    finally:
        if connection is not None:
            connection.close()
        receipt['finished_at'] = _now()
    return receipt


#: ``_write`` 가 모든 receipt 에 붙이는 무결성 필드. **생성기가 붙이고 검증기가 다시 센다** —
#: 2026-08-27 독립 검토가 이 필드를 «생성기는 쓰는데 검증기가 읽지 않는다» 로 짚었고, 실측으로
#: *"살아남은 위조 14 건 중 13 건이 이미 이 해시를 깨고 있었다"* 를 보였다. 파일이 이미 들고
#: 있는 사실을 묻지 않는 검사는 그 사실을 없는 것과 같게 만든다.
RECEIPT_PAYLOAD_HASH_FIELD = 'receipt_payload_sha256'


def receipt_payload_hash(receipt: Mapping[str, Any]) -> str:
    """``receipt`` 에서 무결성 필드를 뺀 나머지의 정규 렌더 SHA-256.

    ⚠️ **쓰는 쪽과 검사하는 쪽이 같은 함수를 지나야 한다.** 두 벌로 두면 한쪽의 렌더 옵션이
    바뀌는 날 모든 문서가 «손 편집됨» 으로 red 가 되고, 그러면 사람들이 이 검사를 끈다.
    """
    payload = dict(receipt)
    payload.pop(RECEIPT_PAYLOAD_HASH_FIELD, None)
    rendered = json.dumps(payload, indent=2, sort_keys=True, default=str) + '\n'
    return hashlib.sha256(rendered.encode('utf-8')).hexdigest()


def _write(path: str | None, receipt: Mapping[str, Any]) -> None:
    payload = dict(receipt)
    payload.pop(RECEIPT_PAYLOAD_HASH_FIELD, None)
    payload[RECEIPT_PAYLOAD_HASH_FIELD] = receipt_payload_hash(payload)
    rendered = json.dumps(payload, indent=2, sort_keys=True, default=str) + '\n'
    if path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered, encoding='utf-8')
    else:
        print(rendered, end='')


# --------------------------------------------------------------------- manifest
#
# ⚠️ **왜 생성기가 필요한가.** MUST-9 의 manifest 는 *모든 receipt 을 하나의 불변 SHA 에
# 결박하는* 아티팩트다. 그런데 2026-08-26 까지 이 저장소에는 그것을 만드는 코드가 **한 줄도
# 없었다** — 두 개의 manifest 가 손으로 쓰여 있었고, 둘 다 ``schema_version: 1`` 을 선언하면서
# **스키마가 서로 완전히 달랐다**(하나는 ``receipts``, 다른 하나는 ``evidence_artifacts``).
# 손으로 쓰는 결박은 결박이 아니다: SHA 를 아무거나 적을 수 있고, receipt 을 **선언하지
# 않는 것만으로** 빠뜨릴 수 있으며, 아무도 그 해시를 다시 세지 않는다. 형식만 보는 게이트가
# 위조를 부른다는 것은 이 저장소가 자가점검 축에서 이미 이름 붙인 결론이다.
#
# 그래서 이 manifest 는 **선언이 아니라 관측**이다:
#
#   * 결박 대상은 **디렉터리에서 파생**한다 — receipt 을 안 적어서 빠뜨릴 수 없다.
#   * 자기 자신은 해시하지 않는다(자기 참조).
#   * 각 receipt 이 **스스로 말하는 컷오프**가 manifest 의 컷오프와 같아야 한다
#     (다른 SHA 에서 나온 receipt 을 모아 담는 것이 이 축의 대표 실패 모드다).
#   * 상태는 형제들의 상태와 트리 상태에서 **파생**하며 손으로 적을 수 없다.

MANIFEST_NAME = 'manifest.json'

#: ⚠️ 버전 1 은 **두 개의 서로 다른 손 저작 스키마**가 공유하던 번호다. 기계로 검증 가능한
#: 첫 스키마는 2 였고, 그 이전 것은 ``LEGACY_UNVERIFIABLE`` 로 이름 붙여 남긴다 — 지우면
#: 역사가 사라지고, 검증한 척하면 그것이 바로 이 축이 고치는 결함이다.
#:
#: ⚠️ **2026-08-27 에 3 으로 오른다. 판정이 바뀌었기 때문이다.** 버전은 «쓰는 쪽» 과
#: «검사하는 쪽» 사이의 계약이고, 재검증이 *저장된 입력으로 판정을 다시 파생해 대조하는*
#: 형태가 된 순간 그 계약이 달라졌다 — 버전을 그대로 두면 옛 문서를 새 판정으로 재파생해
#: **«손 편집됐다» 고 잘못 말한다**. 버전 2 문서도 버전 1 과 같은 대우를 받는다:
#: 이름 붙여 남기고, 지우지 않고, ``PASS`` 로 세지 않는다.
MANIFEST_SCHEMA_VERSION = 3

#: manifest 가 결박하는 형제. 확장자가 아니라 **디렉터리 내용**이 목록을 정한다.
_MANIFEST_SIBLING_SUFFIX = '.json'

#: manifest 필드 중 **재계산할 수 없는 것** — 실행이 그때 관측한 값이다.
#: 재검증은 이 값들을 저장된 문서에서 **그대로 다시 먹여** 판정을 재파생한다.
#:
#: ⚠️ **2026-08-27 에 다섯에서 둘로 줄었다.** 독립 검토가 실측으로 보였다 — 입력 필드는
#: 무엇이든 «그대로 되먹여» 지므로 재파생이 그 값에 대해 아무것도 묻지 않는다. 그래서
#: ``gates`` 의 ``exit: 1`` 을 ``0`` 으로 바꿔도, ``limitations`` 를 *"전부 실행했고 건너뛴
#: 것이 없다"* 로 다시 써도, ``base_sha`` 를 0 으로 채워도 전부 ``PASS`` 였다. 정공은 검사
#: 항목을 늘리는 것이 아니라 **그 값들을 증거 디렉터리 안으로 옮겨** 파생 대상으로 만드는
#: 것이다: 게이트와 한계는 이제 형제 영수증 ``gates.json`` 이 소유하고, base 커밋은 레인
#: 차분 영수증이 러너에게서 받아 싣는다. 남은 둘은 **원리적으로** 재계산 불가다 —
#: 컷오프는 질문 그 자체이고, 관측은 «그때 무엇을 봤나» 라는 과거형이다.
MANIFEST_INPUT_FIELDS = frozenset({'code_cutoff_sha', 'repository'})

#: manifest 필드 중 **디렉터리와 관측에서 다시 나와야 하는 것**. 저장된 값이 재파생과
#: 다르면 그 문서는 손을 탔다.
MANIFEST_DERIVED_FIELDS = frozenset({
    'schema_version', 'status', 'findings', 'artifacts', 'artifact_count',
    'manifest_self_hash', 'production_cutover', 'gates', 'limitations', 'base_sha',
})

#: ``_write`` 가 붙이는 무결성 필드 — :func:`build_manifest` 는 만들지 않고 검증기가 다시 센다.
#: 분할이 **세 갈래**인 이유가 이것이다: 생성 시점과 기록 시점 사이에 필드가 하나 추가되고,
#: 그 이음매를 두 갈래 분할은 볼 수 없었다(2026-08-27 독립 검토 실측).
MANIFEST_INTEGRITY_FIELDS = frozenset({'receipt_payload_sha256'})

#: 게이트와 한계를 소유하는 **형제 영수증**. manifest 밖에 있으면 재파생이 그것을 볼 수 없다.
GATES_RECEIPT_NAME = 'gates.json'


#: 증거가 사는 루트. 결박 판정에서 **«코드가 아닌 것»은 이 아래뿐**이다.
EVIDENCE_ROOT = '.claude/evidence'


class CarryOverUnobservable(RuntimeError):
    """cutoff 와 HEAD 의 관계를 이 트리에서 물을 수 없다. 부르는 쪽은 **거부**로 다룬다."""


def observe_code_changes(
    cutoff: str,
    head: str,
    *,
    evidence_root: str = EVIDENCE_ROOT,
) -> tuple[str, ...]:
    """``cutoff..head`` 사이에 바뀐 경로 중 **증거 밖의 것**.

    ``cutoff`` 가 ``head`` 의 조상이 아니면 :class:`CarryOverUnobservable` 이다 — 두 갈래
    사이의 diff 는 «옮겨왔는가» 라는 질문에 답하지 않는다. git 이 답하지 못해도 같다.
    """
    def _git(*argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ['git', *argv], cwd=ROOT, capture_output=True, text=True, check=True,
        )

    try:
        _git('merge-base', '--is-ancestor', cutoff, head)
    except subprocess.CalledProcessError as exc:
        raise CarryOverUnobservable(
            f'{cutoff[:12]} 는 HEAD {head[:12]} 의 조상이 아니다 — 옮겨오기가 아니라 다른 갈래'
        ) from exc
    except OSError as exc:  # pragma: no cover - git 부재
        raise CarryOverUnobservable(f'git 을 실행할 수 없다: {exc}') from exc

    try:
        completed = _git('diff', '--name-only', '-z', f'{cutoff}..{head}')
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CarryOverUnobservable(
            f'{cutoff[:12]}..{head[:12]} 를 물을 수 없다: {exc}'
        ) from exc

    prefix = f'{evidence_root}/'
    return tuple(
        path for path in completed.stdout.split('\0')
        if path and not path.startswith(prefix)
    )


def evidence_binding_findings(
    repository: Mapping[str, Any],
    *,
    cutoff: str,
    evidence_root: str = EVIDENCE_ROOT,
    code_changes: Any = None,
) -> list[str]:
    """cutoff 가 이 트리까지 **옮겨오는가**, 그리고 트리가 증거 밖에서 깨끗한가.

    ⚠️ **이 함수는 완화가 아니라 모순 제거다.** 옛 판정은 ``HEAD == cutoff`` 이면서 트리가
    완전히 깨끗할 것을 동시에 요구했는데, manifest 와 receipt 은 **그 트리 안에 산다**:
    커밋하지 않으면 더럽고, 커밋하면 HEAD 가 움직인다. 어느 쪽으로도 ``PASS`` 가 나올 수
    없었고, 그래서 계약이 명시적으로 허용하는 *evidence-only 커밋* 이 실제로는 불가능했다.

    ⚠️ **그러나 이 축이 막으라고 만들어진 것은 그대로 막는다.** 이 생성기의 착수 계기는
    *"receipt 이 `5738ae5e` 에 결박돼 있는데 머지로 SHA 가 `4439eb8d` 로 바뀌었다 —
    이름을 고치는 것은 위조다"* 였다. 그 형상에서는 **코드가 바뀌었으므로** 여기서도
    ``BLOCKED`` 이다. 갈라지는 것은 *위조* 와 *증거 기록 그 자체* 이지, 위조와 사면이 아니다.

    두 축, 둘 다 좁히는 쪽으로 보수적이다:

    1. **옮겨오기** — ``cutoff`` 가 HEAD 이거나, HEAD 의 조상이면서 그 사이에 **증거 밖의
       코드가 하나도 바뀌지 않아야** 한다. 물을 수 없으면 거부한다.
    2. **트리 청결** — 증거 루트 **밖** 이 더러우면 거부한다. 증거 루트 안의 더러움은
       *지금 쓰고 있는 그 파일들* 이므로 허용하되, 몇 건인지 findings 에 남기지 않고
       manifest 의 ``repository`` 관측에 그대로 실어 판정자가 직접 본다.
    """
    findings: list[str] = []
    head = str(repository.get('head') or '')
    if not cutoff or not head:
        findings.append('cutoff 또는 HEAD 를 관측하지 못했다 — 결박을 확인할 수 없다')
        return findings

    if cutoff != head:
        observer = code_changes or (
            lambda a, b: observe_code_changes(a, b, evidence_root=evidence_root)
        )
        try:
            moved = observer(cutoff, head)
        except CarryOverUnobservable as exc:
            findings.append(f'cutoff {cutoff[:12]} 의 결박을 확인할 수 없다: {exc}')
        else:
            if moved:
                named = ', '.join(sorted(moved)[:10])
                more = '' if len(moved) <= 10 else f' 외 {len(moved) - 10} 건'
                findings.append(
                    f'the code cutoff is not HEAD and {len(moved)} non-evidence path(s) '
                    f'changed since — evidence and tree disagree: {named}{more}'
                )

    status_paths = repository.get('status_paths')
    if status_paths is None:
        # ⚠️ 관측이 «더럽다» 고만 말하고 **어디가** 더러운지 말하지 못하면 증거 안팎을 가릴 수
        # 없다. 가릴 수 없는 것을 «증거 안이겠지» 로 읽으면 그것이 거짓 통과 채널이다 —
        # 옛 관측 형태를 주입하는 호출자가 조용히 사면되는 자리이기도 하다.
        if not repository.get('clean', True):
            findings.append(
                f'the tree is dirty ({repository.get("status_entries")} entries) at evidence '
                f'time and the observation does not name the paths — cannot tell whether the '
                f'dirt is confined to {evidence_root}'
            )
        return findings

    outside = tuple(
        path for path in status_paths
        if not path.startswith(f'{evidence_root}/')
    )
    if outside:
        named = ', '.join(sorted(outside)[:10])
        more = '' if len(outside) <= 10 else f' 외 {len(outside) - 10} 건'
        findings.append(
            f'the tree is dirty outside {evidence_root} ({len(outside)} entries) '
            f'at evidence time: {named}{more}'
        )
    return findings


def _receipt_declared_cutoff(payload: Mapping[str, Any]) -> str | None:
    """receipt 이 **스스로** 말하는 컷오프. 없으면 ``None``(결박을 주장하지 않는 것)."""
    repository = payload.get('repository')
    if isinstance(repository, Mapping):
        requested = repository.get('requested_cutoff')
        if isinstance(requested, str) and requested and requested != 'UNFROZEN':
            return requested
    declared = payload.get('code_cutoff')
    if isinstance(declared, str) and declared and declared != 'UNFROZEN':
        return declared
    return None


def gate_findings(gates: Mapping[str, Any] | None) -> list[str]:
    """게이트 기록이 MUST-9 의 «각 명령과 그 종료 코드» 요구를 **모양으로** 만족하는가.

    ⚠️ **이름 목록을 갖지 않는다.** 어떤 게이트가 돌아야 하는지는 웨이브마다 다르고, 목록을
    여기 적으면 그 목록이 곧 낡아 «없는 게이트를 요구하는 검사» 가 된다. 물을 수 있는 것은
    두 가지뿐이고 둘 다 웨이브 무관하게 참이다:

    1. **게이트가 0 개인 manifest 는 그 조항을 원리적으로 만족할 수 없다.** 2026-08-27 5차
       독립 검토가 지적한 ``gates: {}`` 가 정확히 이 형태였고, 그때의 수리는 *채우는 통로*를
       뚫는 것이었지 *비어 있음을 red 로 만드는 것*이 아니었다 — 그래서 다음 실행이 다시
       비워도 아무도 몰랐다.
    2. **종료 코드 없는 게이트 기록은 게이트가 아니다.** 명령을 돌렸다는 주장만 있고 그 답이
       없으면, 그 줄은 관측이 아니라 문장이다.

    ⚠️ 종료 코드가 **0 이 아닌 것** 자체는 finding 이 아니다. 이 저장소의 차분 판정은
    선재 실패가 있는 레인을 정당하게 ``exit=1`` 로 기록한다(규칙 5항). 0 을 요구하면 그 규율과
    정면으로 충돌하고, 충돌하는 게이트는 꺼진다.
    """
    findings: list[str] = []
    if not gates:
        findings.append(
            'the manifest records no gate — MUST-9 requires each command with its exit code'
        )
        return findings
    for name in sorted(gates):
        entry = gates[name]
        if not isinstance(entry, Mapping):
            findings.append(f'gate {name!r}: not a mapping, so it records no exit code')
            continue
        exit_code = entry.get('exit')
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            findings.append(f'gate {name!r}: records no integer exit code')
        # ⚠️ MUST-9 는 «각 **명령**, **시작/종료**, 실제 종료 코드, 출력 해시와 개수» 를
        # 요구한다. 2026-08-27 독립 검토 실측: 27 개 게이트 중 명령 문자열도 타임스탬프도
        # 가진 것이 **0 개**였다. 종료 코드만 있는 줄은 «무엇을 돌렸는지» 를 말하지 않으므로
        # 재현 불가이고, 재현 불가한 관측은 다음 세션에게 주장과 구별되지 않는다.
        for field in ('command', 'started_at', 'ended_at'):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                findings.append(f'gate {name!r}: records no {field}')
    return findings


def manifest_siblings(receipt_dir: Path) -> list[Path]:
    """이 디렉터리가 **실제로 담고 있는 모든 파일**. manifest 자신만 뺀다.

    ⚠️ **확장자로 거르지 않는다.** 옛 구현은 ``.json`` 만 봤고, 2026-08-27 독립 검토가 그
    틈으로 영수증을 숨겼다 — ``lane-differential-routine.json`` 을 ``.jsonx`` 로 개명하고
    manifest 에서 지우면, 디스크에는 그대로 있는데 «없는 파일» 이 되어 판정이 통과했다.
    그리고 같은 필터가 **원시 출력 로그를 결박에서 제외**했다: MUST-9 는 *"manifest 와 그가
    참조하는 원시 출력"* 을 요구하는데, 로그가 형제로 세어지지 않으면 그 요구를 만족할 방법
    자체가 없었다. 디렉터리에 있는 것은 전부 결박한다 — 그것이 «선언으로 빠뜨릴 수 없다» 의 뜻이다.
    """
    return sorted(
        path for path in receipt_dir.iterdir()
        if path.is_file() and path.name != MANIFEST_NAME
    )


def _sibling_is_receipt(path: Path) -> bool:
    """이 형제가 **컷오프를 주장해야 하는 영수증**인가, 아니면 원시 출력인가.

    영수증(JSON)은 자기 컷오프와 상태를 말해야 한다. 로그는 말할 수 없고, 말하라고 요구하면
    로그를 커밋하지 못하게 되며, 그러면 MUST-9 의 «참조된 원시 출력» 이 영원히 미충족이다.
    """
    return path.suffix == _MANIFEST_SIBLING_SUFFIX


def derive_gates_and_limitations(
    receipt_dir: Path,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """게이트·한계를 **형제 영수증에서** 읽는다. ``(gates, limitations, findings)``.

    ⚠️ **왜 인자가 아니라 형제인가.** 인자로 받으면 그 값은 manifest 의 *입력* 이 되고,
    입력은 재파생이 그대로 되먹이므로 **어떤 값이든 자기 자신과 일치한다**. 2026-08-27
    독립 검토가 그것으로 ``web:format:check`` 의 ``exit: 1`` 을 ``0`` 으로 바꾸고, 27 개
    게이트 중 26 개를 지우고, 한계 목록을 *"전부 실행했다"* 로 다시 썼다 — 셋 다 ``PASS``.
    증거 디렉터리 안으로 옮기면 그 값은 **파생**이 되고, manifest 를 고치는 것만으로는
    바꿀 수 없다(형제 파일이 반증한다).
    """
    findings: list[str] = []
    path = receipt_dir / GATES_RECEIPT_NAME
    if not path.is_file():
        findings.append(
            f'{GATES_RECEIPT_NAME} is absent — MUST-9 requires each command with its exit code, '
            f'and the manifest cannot invent one'
        )
        return {}, [], findings
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        findings.append(f'{GATES_RECEIPT_NAME}: unreadable ({error.__class__.__name__})')
        return {}, [], findings
    if not isinstance(payload, Mapping):
        findings.append(f'{GATES_RECEIPT_NAME}: not a JSON object')
        return {}, [], findings
    gates = payload.get('gates')
    limitations = payload.get('limitations')
    if not isinstance(gates, Mapping):
        findings.append(f'{GATES_RECEIPT_NAME}: gates is not a mapping')
        gates = {}
    if not isinstance(limitations, Sequence) or isinstance(limitations, (str, bytes)):
        findings.append(f'{GATES_RECEIPT_NAME}: limitations is not a list')
        limitations = []
    return dict(gates), [str(item) for item in limitations], findings


def gate_output_findings(
    gates: Mapping[str, Any], artifacts: Mapping[str, Any],
) -> list[str]:
    """게이트가 이름 대는 원시 출력이 **이 디렉터리에 있고 그 해시인가**.

    ⚠️ 2026-08-27 독립 검토 실측: 27 개 게이트가 전부 ``command_log`` 와 ``output_sha256``
    을 실었는데 그 파일들은 **저장소 어디에도 없었다**. *"아무도 가지고 있지 않은 파일의
    해시는 해시가 없는 것보다 나쁘다"* — 있는 것처럼 보이기 때문이다.
    """
    findings: list[str] = []
    for name in sorted(gates):
        entry = gates[name]
        if not isinstance(entry, Mapping):
            continue
        log_name = entry.get('command_log')
        if log_name is None:
            continue
        if not isinstance(log_name, str) or not log_name:
            findings.append(f'gate {name!r}: command_log is not a file name')
            continue
        artifact = artifacts.get(log_name)
        if artifact is None:
            findings.append(
                f'gate {name!r}: names raw output {log_name!r} which is not in this directory'
            )
            continue
        recorded = entry.get('output_sha256')
        if recorded is not None and recorded != artifact.get('sha256'):
            findings.append(
                f'gate {name!r}: output_sha256 does not match {log_name!r} on disk'
            )
    return findings


def derive_base_sha(receipt_dir: Path) -> tuple[str | None, list[str]]:
    """차분 영수증이 말하는 **base 커밋**. 손으로 적는 자리가 없다.

    레인 러너가 리포트에 트리 SHA 를 찍고, ``lane_baseline_diff`` 가 그것을 영수증에
    싣는다. 여기서는 그 값들을 모아 **하나로 일치하는지**만 묻는다 — 서로 다른 base 에서
    잰 차분들을 한 manifest 로 묶는 것이 이 축의 대표 실패 모드이기 때문이다.
    """
    observed: set[str] = set()
    findings: list[str] = []
    for path in manifest_siblings(receipt_dir):
        if not _sibling_is_receipt(path):
            continue
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        value = payload.get('base_tree_sha')
        if isinstance(value, str) and value:
            observed.add(value)
    if len(observed) > 1:
        findings.append(
            'sibling receipts name more than one differential base: '
            + ', '.join(sorted(observed))
        )
        return None, findings
    return (next(iter(observed)) if observed else None), findings


def build_manifest(
    receipt_dir: Path,
    *,
    cutoff: str,
    repository: Mapping[str, Any] | None = None,
    code_changes: Any = None,
) -> dict[str, Any]:
    """디렉터리를 **관측해** manifest 를 조립한다. 어떤 필드도 손 입력에서 오지 않는다.

    ⚠️ ``repository`` 는 **주입 가능한 관측**이다(기본값은 실제 git). 이 저장소가 자가점검
    값 축에서 낸 결론 — *관측은 인자다* — 을 그대로 따른다. 판정 함수가 git 을 직접 열면
    그 함수는 **깨끗한 트리에서만** 시험할 수 있고, 개발 중에는 그런 트리가 없다. 그러면
    검사가 환경에 의존하고, 환경에 의존하는 검사는 꺼진다.
    """
    if repository is None:
        repository = repository_metadata(cutoff or None)
    artifacts: dict[str, Any] = {}
    findings: list[str] = []
    for path in manifest_siblings(receipt_dir):
        entry: dict[str, Any] = {
            'sha256': _file_hash(path),
            'bytes': path.stat().st_size,
        }
        if not _sibling_is_receipt(path):
            # 원시 출력(로그 등). 컷오프를 «말할 수» 없으므로 요구하지 않는다 — 대신 게이트가
            # 자기 로그를 이름으로 지목하고, 그 해시가 여기 실린 값과 대조된다.
            entry['status'] = 'RAW_OUTPUT'
            entry['declared_cutoff'] = None
            entry['binds_cutoff'] = None
            artifacts[path.name] = entry
            continue
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            entry['status'] = 'UNREADABLE'
            entry['binds_cutoff'] = False
            findings.append(f'{path.name}: unreadable ({error.__class__.__name__})')
        else:
            entry['status'] = payload.get('status', 'UNDECLARED')
            declared = _receipt_declared_cutoff(payload)
            entry['declared_cutoff'] = declared
            entry['binds_cutoff'] = declared == cutoff
            if declared is None:
                findings.append(f'{path.name}: names no code cutoff')
            elif declared != cutoff:
                findings.append(
                    f'{path.name}: names cutoff {declared} but this manifest is for {cutoff}')
            # ⚠️ 게이트 기록은 **판정 영수증이 아니라 관측 기록**이다. 게이트는 정당하게
            # 0 이 아닌 종료 코드를 담고(차분 판정의 선재 실패 레인이 그렇다), 그러므로
            # 이 형제에게 ``PASS`` 를 요구하면 정직한 기록이 red 가 된다. 대신 그 내용은
            # ``gate_findings``/``gate_output_findings`` 가 모양과 실물로 판정한다.
            # 컷오프 결박 요구는 **면제하지 않는다** — 다른 SHA 의 게이트 기록을 끌어오는
            # 것이 정확히 이 축의 대표 실패 모드다.
            if path.name != GATES_RECEIPT_NAME and entry['status'] != 'PASS':
                findings.append(f'{path.name}: status is {entry["status"]}')
        artifacts[path.name] = entry

    if not artifacts:
        findings.append('the evidence directory contains no receipt to bind')
    if receipt_dir.name != cutoff:
        findings.append(
            f'directory basename {receipt_dir.name!r} is not the code cutoff SHA')
    # ⚠️ 옛 판정은 `HEAD == cutoff` 와 «완전 청결» 을 **동시에** 요구했고, 그 둘은 manifest 가
    # 트리 안에 사는 한 함께 만족될 수 없다(커밋 전 = 더러움 / 커밋 후 = HEAD 이동). 그래서
    # 계약이 명시적으로 허용하는 evidence-only 커밋이 실제로는 불가능했다. 판정은
    # `evidence_binding_findings` 로 옮겼고, 그것은 **위조는 그대로 막는다** — 이 생성기를
    # 만든 사건(머지로 SHA 가 움직인 receipt 을 개명)에서는 코드가 바뀌므로 여전히 BLOCKED 다.
    findings.extend(
        evidence_binding_findings(repository, cutoff=cutoff, code_changes=code_changes)
    )
    gates, limitations, gate_receipt_findings = derive_gates_and_limitations(receipt_dir)
    findings.extend(gate_receipt_findings)
    findings.extend(gate_findings(gates))
    findings.extend(gate_output_findings(gates, artifacts))
    base_sha, base_findings = derive_base_sha(receipt_dir)
    findings.extend(base_findings)

    return {
        'schema_version': MANIFEST_SCHEMA_VERSION,
        'status': 'PASS' if not findings else 'BLOCKED',
        'findings': findings,
        'code_cutoff_sha': cutoff,
        'base_sha': base_sha,
        'repository': repository,
        # ⚠️ **키가 곧 디렉터리 내용이다.** receipt 을 선언하지 않는 방법으로 빠뜨릴 수 없다.
        'artifacts': artifacts,
        'artifact_count': len(artifacts),
        'gates': dict(gates or {}),
        'limitations': list(limitations),
        # manifest 는 자기 자신을 해시하지 않는다 — 자기 참조는 계산 불가이고, 결박은
        # evidence 커밋/트리가 한다(계약 MUST-9).
        'manifest_self_hash': 'EXCLUDED_BY_CONSTRUCTION',
        'production_cutover': 'NOT_READY',
    }


def validate_manifest(receipt_dir: Path, *, code_changes: Any = None) -> dict[str, Any]:
    """디스크의 manifest 를 **판정을 다시 파생해** 대조한다. 손 편집은 여기서 red 다.

    ⚠️ **옛 판정은 해시만 다시 셌고, 그래서 판정 자체의 위조를 전부 놓쳤다.** 형제 receipt 을
    재해시하고 ``production_cutover`` 리터럴과 디렉터리 이름을 보는 것이 전부였는데, 이 축이
    막으라고 만들어진 위조는 **해시가 아니라 판정** 쪽에 있다: ``status`` 를 ``PASS`` 로
    바꾸고 ``findings`` 를 비우거나, ``repository`` 관측을 «더러웠다» 에서 «깨끗했다» 로
    바꾸거나, ``gates`` 를 통째로 비우거나, ``artifact_count`` 를 실제와 다르게 적거나,
    형제의 ``binds_cutoff`` 를 뒤집는 것 — 다섯 전부 파일 해시를 하나도 건드리지 않는다.

    정공은 **검사 목록을 늘리는 것이 아니라 판정을 다시 돌리는 것**이다. manifest 가 스스로
    기록한 *입력*(:data:`MANIFEST_INPUT_FIELDS`)을 :func:`build_manifest` 에 그대로 다시
    먹이고, 나온 문서의 *파생 필드*(:data:`MANIFEST_DERIVED_FIELDS`)를 저장된 것과 대조한다.
    판정의 **두 번째 사본이 생기지 않고**, 앞으로 생기는 필드도 분할에 등록되는 순간
    자동으로 재검증 대상이 된다 — 손으로 유지하는 검사 목록은 반드시 낡는다.

    ⚠️ 개별 사유 메시지는 그대로 남긴다. 재파생 대조는 *무엇이 다른가* 를 필드 단위로만 알고,
    ``alpha.json 의 해시가 다르다`` 를 ``artifacts 가 다르다`` 로 뭉개면 진단이 사라진다.
    """
    manifest_path = receipt_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return {'status': 'MISSING', 'findings': [f'{manifest_path} does not exist']}
    try:
        stored = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return {'status': 'UNREADABLE', 'findings': [str(error)]}
    if not isinstance(stored, Mapping):
        return {'status': 'UNREADABLE', 'findings': ['the manifest is not a JSON object']}
    if stored.get('schema_version') != MANIFEST_SCHEMA_VERSION:
        return {
            'status': 'LEGACY_UNVERIFIABLE',
            'findings': [
                f'schema_version {stored.get("schema_version")!r} predates the generator; '
                f'only version {MANIFEST_SCHEMA_VERSION} is machine-checkable'
            ],
        }
    # ⚠️ 버전만 맞추면 되는 것이 아니다. 실제로 이 저장소에는 **손으로 쓴 문서가 생성기의
    # 버전 번호를 참칭한** 사례가 있다(`2169dc28…/manifest.json` — ``artifacts`` 매핑이 아예
    # 없는데 ``schema_version: 2``). 그것을 «artifacts 가 매핑이 아니다» 로 답하면 사유가
    # 엉뚱해서, 다음 세션이 생성물 결함을 찾다가 손 저작물을 발견한다.
    absent = sorted(
        (MANIFEST_INPUT_FIELDS | MANIFEST_DERIVED_FIELDS | MANIFEST_INTEGRITY_FIELDS)
        - set(stored)
    )
    if absent:
        return {
            'status': 'LEGACY_UNVERIFIABLE',
            'findings': [
                f'the document declares version {MANIFEST_SCHEMA_VERSION} but was not produced '
                f'by this generator — missing field(s): {", ".join(absent)}'
            ],
        }

    findings: list[str] = []
    # ⚠️ **선언되지 않은 최상위 필드는 거부한다.** 2026-08-27 독립 검토가 manifest 에
    # ``reviewer_verdict: APPROVED`` 와 ``hardware: all green`` 을 심고 ``PASS`` 를 받아냈다.
    # 분할이 «빠진 필드» 만 묻고 «남는 필드» 를 묻지 않으면, 문서에 아무 주장이나 덧붙일 수
    # 있고 판정자는 그것을 이 생성기가 말한 것으로 읽는다.
    unexpected = sorted(
        set(stored)
        - (MANIFEST_INPUT_FIELDS | MANIFEST_DERIVED_FIELDS | MANIFEST_INTEGRITY_FIELDS)
    )
    if unexpected:
        findings.append(
            'the manifest carries field(s) this generator never writes: '
            + ', '.join(unexpected)
        )
    observed = {path.name: _file_hash(path) for path in manifest_siblings(receipt_dir)}
    declared = stored.get('artifacts')
    if not isinstance(declared, Mapping):
        return {'status': 'FAIL', 'findings': ['artifacts is not a mapping']}
    for name in sorted(set(observed) - set(declared)):
        findings.append(f'{name}: present on disk but absent from the manifest')
    for name in sorted(set(declared) - set(observed)):
        findings.append(f'{name}: declared by the manifest but absent from disk')
    for name in sorted(set(observed) & set(declared)):
        entry = declared[name]
        recorded = entry.get('sha256') if isinstance(entry, Mapping) else None
        if recorded != observed[name]:
            findings.append(f'{name}: sha256 does not match the file on disk')
    if MANIFEST_NAME in declared:
        findings.append('the manifest hashes itself')
    if stored.get('production_cutover') != 'NOT_READY':
        findings.append('production_cutover is not the literal NOT_READY')
    cutoff = stored.get('code_cutoff_sha')
    if receipt_dir.name != cutoff:
        findings.append(f'directory basename {receipt_dir.name!r} is not {cutoff!r}')

    # ⚠️ **파일이 이미 들고 있는 사실을 먼저 묻는다.** ``_write`` 가 붙인 무결성 해시는
    # 2026-08-27 까지 아무도 다시 세지 않았고, 그 사이 살아남은 위조 14 건 중 13 건이
    # **이미 이 해시를 깨고 있었다**(독립 검토 실측). 묻지 않는 사실은 없는 사실과 같다.
    recorded_payload_hash = stored.get(RECEIPT_PAYLOAD_HASH_FIELD)
    if recorded_payload_hash != receipt_payload_hash(stored):
        findings.append(
            f'{RECEIPT_PAYLOAD_HASH_FIELD} does not match the document it is attached to — '
            f'the manifest was edited after it was written'
        )
    findings.extend(_rederivation_findings(
        receipt_dir, stored, code_changes=code_changes,
    ))
    return {'status': 'PASS' if not findings else 'FAIL', 'findings': findings}


def _rederivation_findings(
    receipt_dir: Path,
    stored: Mapping[str, Any],
    *,
    code_changes: Any = None,
) -> list[str]:
    """저장된 **입력**으로 판정을 다시 파생해, 파생 필드가 저장된 값과 같은지 묻는다.

    ``repository`` 는 «그때 무엇을 봤는가» 라는 기록이라 재계산할 수 없고, 재계산해서도 안
    된다 — 지금 다시 git 을 열면 evidence 커밋 이후의 HEAD 를 보게 되고 그 답은 그때의
    판정에 대해 아무것도 말하지 않는다. 그러므로 대조하는 명제는 **«그때 본 것이 그때 적은
    답을 뒷받침하는가»** 이다.

    ⚠️ **이것은 자기 일관성 검사이지 위조 봉인이 아니다.** 2026-08-27 이전 판본의 이 자리에
    *"관측 자체를 위조하면 그 방향으로도 red 다"* 라고 적혀 있었고 **그것은 거짓이었다** —
    참인 것은 ``repository`` **하나만** 고쳤을 때이고, 독립 검토가 관측·``findings``·``status``
    **셋을 함께** 고쳐 ``PASS`` 를 받아냈다. 남은 입력 필드는 둘뿐이며(컷오프와 관측) 둘 다
    문서 밖의 무엇과도 대조되지 않는다. 그 조합을 막는 것은 이 함수가 아니라 (a) 무결성
    해시 재계산과 (b) **evidence 커밋 자체**다. 산문이 봉인보다 강하게 말하면 다음 세션이
    없는 보호를 믿는다.
    """
    repository = stored.get('repository')
    if not isinstance(repository, Mapping):
        return ['repository observation is not a mapping — the verdict cannot be re-derived']
    try:
        rebuilt = build_manifest(
            receipt_dir,
            cutoff=str(stored.get('code_cutoff_sha') or ''),
            repository=repository,
            code_changes=code_changes,
        )
    except Exception as error:  # noqa: BLE001 - 재파생 불가를 «차이 없음» 으로 읽으면 false PASS
        return [f'the verdict could not be re-derived: {error.__class__.__name__}: {error}']

    findings: list[str] = []
    for field_name in sorted(MANIFEST_DERIVED_FIELDS):
        if stored.get(field_name) != rebuilt.get(field_name):
            findings.append(
                f'{field_name}: the manifest disagrees with a fresh derivation from its own '
                f'recorded inputs (stored {stored.get(field_name)!r}, derived '
                f'{rebuilt.get(field_name)!r})'
            )
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fresh-dsn', default=os.environ.get(FRESH_ENV, ''))
    parser.add_argument('--upgrade-dsn', default=os.environ.get(UPGRADE_ENV, ''))
    parser.add_argument('--json-output')
    parser.add_argument('--receipt-dir', help='SHA-scoped directory for lane/live receipts')
    parser.add_argument('--cutoff', default=os.environ.get('FCC_CODE_CUTOFF_SHA', ''))
    parser.add_argument(
        '--write-manifest', action='store_true',
        help='observe --receipt-dir and write manifest.json; no database is touched',
    )
    parser.add_argument(
        '--gates', default='',
        help=(
            f'게이트 실행 산출물 JSON. 이 파일은 manifest 안으로 들어가지 않고 '
            f'--receipt-dir/{GATES_RECEIPT_NAME} **형제 영수증**으로 기록된 뒤 거기서 '
            f'파생된다. 인자로 실으면 manifest 의 «입력» 이 되고, 입력은 재파생이 '
            f'그대로 되먹이므로 어떤 값이든 자기 자신과 일치한다.'
        ),
    )
    parser.add_argument(
        '--check-manifest', action='store_true',
        help='re-observe --receipt-dir and compare it to the manifest already on disk',
    )
    args = parser.parse_args(argv)

    # manifest 모드는 **데이터베이스를 열지 않는다** — 결박은 디스크 관측이지 실행이 아니다.
    if args.write_manifest or args.check_manifest:
        if not args.receipt_dir:
            parser.error('--write-manifest / --check-manifest require --receipt-dir')
        receipt_directory = Path(args.receipt_dir)
        if args.check_manifest:
            result = validate_manifest(receipt_directory)
            print(json.dumps(result, indent=2, sort_keys=True) + '\n', end='')
            return 0 if result['status'] == 'PASS' else 1
        # ⚠️ ``gates`` 는 손으로 적는 자리가 아니다. 계약 MUST-9 는 각 명령·종료 코드·출력
        # 개수를 요구하는데, 그것을 사람이 타이핑하면 그 순간 manifest 는 관측이 아니라
        # 주장이 된다. 여기서는 **산출물 파일**을 읽어 넣는다 — 비워 두는 것보다 낫고,
        # 지어내는 것보다는 훨씬 낫다. (2026-08-27 5차 독립 검토가 `gates: {}` 를 지적했다.)
        if args.gates:
            try:
                loaded = json.loads(Path(args.gates).read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError) as error:
                parser.error(f'--gates 를 읽을 수 없다: {error}')
            else:
                # ⚠️ 형제로 **기록**한다. manifest 인자로 실으면 재파생이 그것을 보지 못한다.
                _write(str(receipt_directory / GATES_RECEIPT_NAME), {
                    'receipt': 'gate-run',
                    'schema_version': 1,
                    'status': 'RECORDED',
                    'code_cutoff': args.cutoff.strip() or 'UNFROZEN',
                    'production_cutover': 'NOT_READY',
                    'gates': loaded.get('gates', loaded),
                    'limitations': list(loaded.get('limitations', ())),
                })
        manifest = build_manifest(receipt_directory, cutoff=args.cutoff.strip())
        _write(str(receipt_directory / MANIFEST_NAME), manifest)
        return 0 if manifest['status'] == 'PASS' else 2

    repository = _repository_metadata(args.cutoff.strip() or None)
    command = _redacted_command(
        argv, fresh_dsn=str(args.fresh_dsn or ''), upgrade_dsn=str(args.upgrade_dsn or ''),
    )
    receipt_dir = Path(args.receipt_dir) if args.receipt_dir else None
    cutoff_valid = len(args.cutoff) == 40 and all(
        character in '0123456789abcdefABCDEF' for character in args.cutoff
    )
    receipt_scope_ok = receipt_dir is None or (
        cutoff_valid and receipt_dir.name == args.cutoff
    )

    receipt: dict[str, Any] = {
        'schema_version': 1,
        'status': 'BLOCKED',
        'migration': _migration_file_metadata(),
        'schema_contract': _schema_metadata(),
        'lanes': {},
        'writes': 'disposable PostgreSQL migration application plus protected-data, CAS, publication, retirement, and snapshot-ingestion proof',
        'command': command,
        'code_cutoff': args.cutoff.strip() or 'UNFROZEN',
        'repository': repository,
        'receipt_scope': {
            'directory': str(receipt_dir) if receipt_dir else None,
            'basename_matches_cutoff': receipt_scope_ok,
        },
    }
    for lane, dsn in (('fresh', args.fresh_dsn), ('upgrade', args.upgrade_dsn)):
        if not str(dsn or '').strip():
            receipt['lanes'][lane] = {
                'lane': lane,
                'status': 'BLOCKED',
                'blocked_reason': (
                    f'{FRESH_ENV if lane == "fresh" else UPGRADE_ENV} is not configured; '
                    'no disposable PostgreSQL lane was silently skipped'
                ),
            }
        else:
            receipt['lanes'][lane] = _lane(
                str(dsn).strip(), lane, cutoff=args.cutoff.strip() or None,
                command=command,
            )

    if receipt_dir is not None:
        _write(str(receipt_dir / 'migration-030-fresh.json'), receipt['lanes']['fresh'])
        _write(str(receipt_dir / 'migration-030-upgrade.json'), receipt['lanes']['upgrade'])
        live_lanes = {
            lane: {
                'status': value.get('live_proof', {}).get('status', value.get('status')),
                'proof': value.get('live_proof'),
            }
            for lane, value in receipt['lanes'].items()
        }
        live_statuses = [value['status'] for value in live_lanes.values()]
        live_receipt = {
            'schema_version': 1,
            'status': (
                'PASS' if live_statuses and all(status == 'PASS' for status in live_statuses)
                else 'BLOCKED' if any(status == 'BLOCKED' for status in live_statuses)
                else 'FAIL'
            ),
            'command': command,
            'code_cutoff': args.cutoff.strip() or 'UNFROZEN',
            'repository': repository,
            'lanes': live_lanes,
            'production_cutover': 'NOT_READY',
        }
        _write(str(receipt_dir / 'selection-live.json'), live_receipt)

    lane_statuses = [lane['status'] for lane in receipt['lanes'].values()]
    receipt['status'] = (
        'PASS' if lane_statuses and all(status == 'PASS' for status in lane_statuses)
        else 'BLOCKED' if any(status == 'BLOCKED' for status in lane_statuses)
        else 'FAIL'
    )
    # 판정은 manifest 와 **같은 함수**가 한다. 두 벌로 두면 한쪽만 고쳐지고, 이 저장소는
    # 그 형태에 이미 이름을 붙였다. ⚠️ 이 라이브 경로는 이번 세션에서 실행되지 않았다
    # (disposable PostgreSQL 레인 미구동) — 장부에 그 사실을 적는다.
    binding_findings = evidence_binding_findings(repository, cutoff=args.cutoff.strip())
    if binding_findings:
        receipt['status'] = 'BLOCKED'
        receipt['blocked_reason'] = '; '.join(binding_findings)
    if not receipt_scope_ok:
        receipt['status'] = 'BLOCKED'
        receipt['blocked_reason'] = 'receipt directory must be named by the requested code cutoff SHA'
    _write(args.json_output, receipt)
    return {'PASS': 0, 'FAIL': 1, 'BLOCKED': 2}[receipt['status']]


if __name__ == '__main__':
    raise SystemExit(main())
