"""Incremental central-DB migration runner (central-db-migration-runner, 2026-06-26).

Applies `docs/platform/migrations/NNN_*.sql` to the central PostgreSQL in version
order, recording each in the `schema_migrations` ledger (defined in the central
schema SSOT + 001). Idempotent: already-applied versions are skipped; a checksum
change on an already-applied version is a LOUD drift error (Codex review §1).

Design (Codex review):
  * one transaction per migration; a SESSION-level advisory lock around the whole
    run serialises concurrent runners (reuses `advisory_lock_id` from the evidence
    runner — no second lock scheme).
  * `migrate` bootstraps naturally: on an empty DB, 001 creates `schema_migrations`
    among the base tables, then its own ledger row is inserted in the same tx.
  * `baseline --through NNN` records existing migrations as applied WITHOUT running
    their SQL — for DBs that already have the schema (e.g. applied out-of-band)
    so non-idempotent future migrations are never blindly re-run. 001 (idempotent,
    `CREATE TABLE IF NOT EXISTS`) is run to bootstrap the ledger, then 002..NNN are
    recorded only after an introspection gate confirms the schema looks applied.
  * `rollback --target NNN_slug` executes Liquibase-style `--rollback <stmt>`
    annotations from the target migration, then removes that ledger row. Rollback
    is restricted to the latest applied migration in discovery order so the ledger
    cannot develop gaps.

Pure helpers (`discover_migrations`, `checksum_sql`, `plan_migrations`) take no DB
and are unit-tested without PostgreSQL. SSOT / no hardcoding: the migrations dir
and DSN come from args/env; nothing is literal beyond the lock key + ledger name.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sys
from typing import Mapping, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402

# The record, not a directory walk: this lane delivers docs/platform/migrations/
# to migrations/ at the box root, so the box has a docs/ an ancestor walk finds
# and no docs/platform/migrations under it.
DEFAULT_MIGRATIONS_DIR = resolve_repo_artifact(__file__, 'docs/platform/migrations')
#: Whole-run serialisation lock (session-scoped; held across every per-migration tx).
RUN_LOCK_KEY = 'fcc-platform:central-db-migrate'
LEDGER_TABLE = 'schema_migrations'


class MigrationsDirectoryUnavailable(RuntimeError):
    """마이그레이션을 찾지 못했다. **0건으로 강등하지 않는다** (2026-09-03)."""
#: NNN_<slug>.sql — the numeric prefix orders; the full stem is the ledger version.
_MIGRATION_RE = re.compile(r'^(?P<num>\d+)_.+\.sql$')
# Liquibase formatted-SQL convention. The keyword is the contiguous token
# `--rollback` (no space after comment dashes), case-insensitive.
_ROLLBACK_RE = re.compile(r'^\s*--rollback\s+(?P<sql>.+?)\s*$', re.IGNORECASE)
# A migration whose executable body needs to run OUTSIDE a transaction block.
# Primary signal: PostgreSQL `CONCURRENTLY` index ops (auto-detected). Escape
# hatch for other non-transactional DDL that lacks that keyword (e.g.
# `ALTER TYPE ... ADD VALUE`): an explicit `-- migrate:no-transaction` marker.
_CONCURRENTLY_RE = re.compile(r'\bCONCURRENTLY\b', re.IGNORECASE)
_NON_TRANSACTIONAL_MARKER_RE = re.compile(
    r'^\s*--\s*migrate:no-transaction\b', re.IGNORECASE | re.MULTILINE
)


# Self-contained connection + advisory-lock primitives so this deploy-critical
# runner is a SINGLE file in the central image (no evidence-tooling import chain).
# The lock-id algorithm is byte-identical to the evidence runner's and is sealed
# against drift by tests/test_central_db_migration_runner.py::TestLockIdParity.
def _connect(dsn: str):
    try:
        import psycopg

        return psycopg.connect(dsn)
    except ModuleNotFoundError:
        import psycopg2

        return psycopg2.connect(dsn)


def advisory_lock_id(lock_key: str) -> int:
    """Deterministic 63-bit PostgreSQL advisory-lock id from a lock key."""
    digest = hashlib.sha256(lock_key.encode('utf-8')).digest()
    return int.from_bytes(digest[:8], byteorder='big', signed=False) & ((1 << 63) - 1)


# --------------------------------------------------------------------------- #
# Pure helpers (no DB — unit-testable).
# --------------------------------------------------------------------------- #
class MigrationDriftError(RuntimeError):
    """An already-applied version's file checksum no longer matches the ledger."""


class MigrationRollbackError(RuntimeError):
    """Rollback cannot be planned or executed safely."""


class MigrationApplyError(RuntimeError):
    """A migration executed but left the database in an unsafe state, so the
    runner refuses to record it as applied (keeping the version re-queued + loud).

    Concrete case: a failed ``CREATE INDEX CONCURRENTLY`` leaves an INVALID index
    (``pg_index.indisvalid = false``) carrying the target name. A bare
    ``IF NOT EXISTS`` retry would then SKIP re-creating it and the runner would
    otherwise mark the migration applied with a non-enforcing index. Detecting
    the leftover and raising here converts that silent corruption into a loud
    failure (`docs/development/central-db-migrations.md` — non-transactional
    authoring: `DROP INDEX CONCURRENTLY IF EXISTS` before the CREATE self-heals).
    """


def checksum_sql(text: str) -> str:
    """sha256 of the migration file text (newline-normalised) — ledger checksum."""
    return hashlib.sha256(text.replace('\r\n', '\n').encode('utf-8')).hexdigest()


def discover_migrations(migrations_dir: Path = DEFAULT_MIGRATIONS_DIR) -> list[tuple[str, Path]]:
    """Return ``[(version, path)]`` sorted by numeric prefix.

    ``version`` is the filename stem (e.g. ``001_initial_central_db``). Files that
    do not match ``NNN_*.sql`` are ignored. Duplicate numeric prefixes raise.

    ⚠️ **없는/빈 디렉터리는 «마이그레이션 0건» 이 아니라 «찾지 못했다» 다** (봉인
    2026-09-03). 이 함수는 그 둘을 같은 ``[]`` 로 답하고 있었고, 그 위의 ``migrate`` 는
    그것을 ``pending=[]`` — 즉 **성공** — 으로 보고했다. 실측 2026-09-03: 컨테이너
    이미지에 마이그레이션 파일을 하나도 싣지 않은 빌드에서 ``central-migrate`` 가
    성공으로 끝났다. 새 중앙 PC 에서 그 답의 뜻은 «빈 데이터베이스인데 스키마는
    최신» 이고, 뒤따르는 모든 실패가 원인과 무관해 보인다.

    진짜 0건은 실재하지 않는다 — 이 레인은 ``001_initial_central_db.sql`` 을 항상
    갖는다(그것이 ``schema_migrations`` 원장 자체를 만든다). 그러므로 0건은 언제나
    **경로가 틀렸다**는 뜻이고, 그렇게 말한다.
    """
    if not migrations_dir.is_dir():
        raise MigrationsDirectoryUnavailable(
            f'마이그레이션 디렉터리가 없다: {migrations_dir}\n'
            '  이것은 «적용할 것이 없다» 가 아니라 «찾지 못했다» 다.\n'
            '  --migrations-dir 로 경로를 명시하라(컨테이너 이미지는 상자 표식이 '
            '없어 자동 해소가 성립하지 않는다).'
        )
    found: list[tuple[int, str, Path]] = []
    seen_nums: dict[int, str] = {}
    for path in sorted(migrations_dir.glob('*.sql')):
        match = _MIGRATION_RE.match(path.name)
        if not match:
            continue
        num = int(match.group('num'))
        if num in seen_nums:
            raise ValueError(
                f'duplicate migration prefix {num:03d}: {seen_nums[num]} vs {path.name}'
            )
        seen_nums[num] = path.name
        found.append((num, path.stem, path))
    found.sort(key=lambda item: item[0])
    if not found:
        raise MigrationsDirectoryUnavailable(
            f'{migrations_dir} 에 NNN_*.sql 이 하나도 없다.\n'
            '  이 레인은 001_initial_central_db.sql 을 항상 갖는다 — 0건은 «적용할 '
            '것이 없다» 가 아니라 경로가 틀렸다는 뜻이다.'
        )
    return [(stem, path) for _num, stem, path in found]


def plan_migrations(
    migrations: Sequence[tuple[str, Path]],
    applied: Mapping[str, str],
) -> tuple[list[tuple[str, Path, str]], list[str]]:
    """Split discovered migrations into (to_apply, drift_errors).

    ``applied`` maps version → recorded checksum. For each migration:
      * already applied with matching checksum → skip.
      * already applied with DIFFERENT checksum → drift error (do NOT re-apply).
      * not applied → queue (version, path, fresh checksum).
    Returns the apply queue (in order) and a list of human-readable drift errors.
    """
    to_apply: list[tuple[str, Path, str]] = []
    drift: list[str] = []
    for version, path in migrations:
        digest = checksum_sql(path.read_text(encoding='utf-8'))
        recorded = applied.get(version)
        if recorded is None:
            to_apply.append((version, path, digest))
        elif recorded != digest:
            drift.append(
                f'{version}: file checksum {digest[:12]}… != ledger {recorded[:12]}… '
                '(already-applied migration was edited — refusing to re-apply)'
            )
    return to_apply, drift


def plan_reconcile(
    migrations: Sequence[tuple[str, Path]],
    applied: Mapping[str, str],
) -> tuple[Optional[tuple[str, str]], list[str]]:
    """Plan an operator-asserted reconcile of the exporter-owned bootstrap ledger row.

    The bootstrap migration (discovery-order first, i.e. ``001``) is REGENERATED by
    the DDL exporter (``export_platform_central_db_ddl.py``) whenever the central
    schema SSOT (``central_db_schema.v1.json``) changes, so its file checksum
    legitimately drifts from the checksum recorded when an existing DB was first
    migrated. That bootstrap drift is BENIGN: ``001`` is idempotent
    (``CREATE ... IF NOT EXISTS``) and every schema delta a re-render introduces is
    ALSO shipped as an incremental migration (``007``/``008``/...), which existing
    DBs still apply. Reconcile therefore re-stamps ONLY the bootstrap ledger
    checksum to the current file, and REFUSES if any NON-bootstrap already-applied
    migration drifted — that is a genuine append-only violation (an incremental
    ``NNN_*.sql`` was edited in place) and must be investigated, never silently
    rewritten.

    Returns ``(bootstrap_update, blocking)`` where:
      * ``bootstrap_update`` is ``(version, fresh_checksum)`` when the bootstrap
        ledger row needs re-stamping, or ``None`` if it already matches / is not yet
        applied (reconcile is then a no-op — idempotent).
      * ``blocking`` lists non-bootstrap drift messages that make reconcile unsafe.
    """
    if not migrations:
        return None, []
    bootstrap_version = migrations[0][0]
    bootstrap_update: Optional[tuple[str, str]] = None
    blocking: list[str] = []
    for version, path in migrations:
        recorded = applied.get(version)
        if recorded is None:
            continue
        digest = checksum_sql(path.read_text(encoding='utf-8'))
        if recorded == digest:
            continue
        if version == bootstrap_version:
            bootstrap_update = (version, digest)
        else:
            blocking.append(
                f'{version}: file checksum {digest[:12]}… != ledger {recorded[:12]}… '
                '(non-bootstrap already-applied migration drifted — reconcile refuses; '
                'this is an append-only violation on an incremental migration)'
            )
    return bootstrap_update, blocking


def parse_rollback_statements(sql_text: str) -> list[str]:
    """Return DOWN statements declared by `--rollback <stmt>` annotation lines.

    The annotations are inert SQL comments during forward execution, but this
    helper strips the prefix to recover executable rollback SQL. Statement order
    is preserved.
    """
    return [
        match.group('sql')
        for line in sql_text.splitlines()
        if (match := _ROLLBACK_RE.match(line))
    ]


def rollback_annotation_lines_are_inert_comments(sql_text: str) -> bool:
    """Every rollback annotation must be a SQL line comment."""
    return all(
        line.lstrip().startswith('--')
        for line in sql_text.splitlines()
        if _ROLLBACK_RE.match(line)
    )


def _strip_sql_line_comments(sql_text: str) -> str:
    """Drop `--` line comments so only executable SQL body remains.

    Line-based (each line is truncated at its first ``--``): this deliberately
    matches the runner's other line-oriented comment handling
    (`parse_rollback_statements`, `rollback_annotation_lines_are_inert_comments`).
    It keeps `--rollback` DOWN annotations, the `-- migrate:no-transaction` marker,
    and prose comments (which may mention CONCURRENTLY, e.g. 008's header) out of
    both non-transactional detection and statement splitting.
    """
    out: list[str] = []
    for line in sql_text.splitlines():
        idx = line.find('--')
        out.append(line if idx == -1 else line[:idx])
    return '\n'.join(out)


def migration_requires_non_transactional(sql_text: str) -> bool:
    """True if a migration must be applied OUTSIDE a transaction block.

    Two independent signals (either is sufficient):
      * auto-detect: the executable body (comments stripped) contains the
        PostgreSQL ``CONCURRENTLY`` keyword — `CREATE/DROP INDEX CONCURRENTLY`
        cannot run inside a transaction. Comment-only mentions (prose or
        ``--rollback ... CONCURRENTLY`` DOWN annotations) never trigger, because
        they are stripped first.
      * explicit opt-in: a ``-- migrate:no-transaction`` marker comment, for
        non-transactional DDL that lacks the CONCURRENTLY keyword.
    """
    if _NON_TRANSACTIONAL_MARKER_RE.search(sql_text):
        return True
    return bool(_CONCURRENTLY_RE.search(_strip_sql_line_comments(sql_text)))


def split_sql_statements(sql_text: str) -> list[str]:
    """Split a migration body into individually executable statements.

    Line comments are stripped first (so annotations/markers/prose never become
    statements), then the body is split on ``;``. Blank fragments are dropped and
    each returned statement keeps its terminating ``;``. Used only for the
    non-transactional (autocommit, per-statement) apply path — the transactional
    path executes the whole file in one cursor call as before.

    Limitation: this is a naive ``;`` splitter. Non-transactional migrations must
    therefore be simple statements (the real cases — ``CREATE/DROP INDEX
    CONCURRENTLY`` and ``ALTER TYPE ... ADD VALUE`` — never use ``;`` inside
    string/dollar-quoted bodies). Function/DO bodies belong in the transactional
    path, which executes them whole.
    """
    body = _strip_sql_line_comments(sql_text)
    return [f'{stmt};' for chunk in body.split(';') if (stmt := chunk.strip())]


def plan_rollback(
    migrations: Sequence[tuple[str, Path]],
    applied: Mapping[str, str],
    *,
    target: str,
) -> tuple[Path, list[str]]:
    """Validate a safe latest-only rollback and return its SQL statements."""
    by_version = {version: path for version, path in migrations}
    path = by_version.get(target)
    if path is None:
        raise MigrationRollbackError(f'unknown migration target: {target}')
    recorded = applied.get(target)
    if recorded is None:
        raise MigrationRollbackError(f'migration is not applied: {target}')

    applied_in_order = [version for version, _path in migrations if version in applied]
    if not applied_in_order or applied_in_order[-1] != target:
        latest = applied_in_order[-1] if applied_in_order else '<none>'
        raise MigrationRollbackError(
            f'rollback target must be latest applied migration; target={target}, latest={latest}'
        )

    text = path.read_text(encoding='utf-8')
    digest = checksum_sql(text)
    if recorded != digest:
        raise MigrationDriftError(
            f'{target}: file checksum {digest[:12]}… != ledger {recorded[:12]}… '
            '(already-applied migration was edited — refusing to rollback)'
        )

    statements = parse_rollback_statements(text)
    if not statements:
        raise MigrationRollbackError(f'migration has no rollback annotations: {target}')
    if not rollback_annotation_lines_are_inert_comments(text):
        raise MigrationRollbackError(f'rollback annotations are not forward-safe comments: {target}')
    return path, statements


# --------------------------------------------------------------------------- #
# DB-facing run (PostgreSQL).
# --------------------------------------------------------------------------- #
_READ_APPLIED_SQL = f'SELECT version, checksum FROM "{LEDGER_TABLE}"'
_RECORD_SQL = (
    f'INSERT INTO "{LEDGER_TABLE}" (id, version, checksum, applied_at, applied_by) '
    'VALUES (gen_random_uuid(), %s, %s, %s, %s) ON CONFLICT (version) DO NOTHING'
)
_DELETE_LEDGER_SQL = f'DELETE FROM "{LEDGER_TABLE}" WHERE version = %s'
#: Names of indexes PostgreSQL currently marks INVALID — a failed
#: `CREATE INDEX CONCURRENTLY` leaves one behind. Read as an ABSOLUTE post-condition
#: after a non-transactional apply to detect a leftover the retry's `IF NOT EXISTS`
#: would silently skip. `::regclass::text` renders the (schema-qualified) index name.
_INVALID_INDEX_SQL = 'SELECT indexrelid::regclass::text FROM pg_index WHERE NOT indisvalid'
#: Operator-asserted re-stamp of the exporter-owned bootstrap ledger row (reconcile
#: subcommand ONLY — the single sanctioned exception to the append-only ledger).
_RECONCILE_LEDGER_SQL = (
    f'UPDATE "{LEDGER_TABLE}" SET checksum = %s, applied_at = %s, applied_by = %s '
    'WHERE version = %s'
)


def _read_applied(connection) -> dict[str, str]:
    """version → checksum from the ledger; empty dict if the ledger doesn't exist yet."""
    try:
        with connection.cursor() as cursor:
            cursor.execute(_READ_APPLIED_SQL)
            rows = cursor.fetchall()
        connection.commit()
        return {row[0]: row[1] for row in rows}
    except Exception:
        # Ledger absent on a brand-new DB (001 creates it). Reset the aborted tx.
        connection.rollback()
        return {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _invalid_index_names(connection) -> set[str]:
    """Names of indexes PostgreSQL currently marks INVALID (failed CONCURRENTLY).

    NOT fail-open: this runner only ever talks to PostgreSQL (advisory locks,
    ``gen_random_uuid``, ``ON CONFLICT``), where ``pg_index`` always exists. A
    query error here is a real fault and must surface loudly — swallowing it would
    silently disable the guard and let a migration record over an INVALID index.
    """
    with connection.cursor() as cursor:
        cursor.execute(_INVALID_INDEX_SQL)
        rows = cursor.fetchall()
    return {row[0] for row in rows}


def _apply_non_transactional(
    connection,
    *,
    sql_text: str,
    version: str,
    digest: str,
    applied_by: str,
) -> None:
    """Apply one migration OUTSIDE a transaction (autocommit), then record it.

    Mirrors ``rollback()``'s autocommit lifecycle (the SSOT for "runs outside a
    transaction"): save the previous autocommit flag, switch to autocommit=True,
    execute each statement so it self-commits (required by ``CONCURRENTLY`` /
    ``ALTER TYPE ... ADD VALUE``), record the ledger row last, and restore
    autocommit in a ``finally``. The migration DDL is guarded with
    ``IF [NOT] EXISTS``, so a mid-run failure is safe to re-run: the ledger row is
    written only after every statement succeeds, leaving the version un-recorded
    (and thus re-queued) on failure.

    INVALID-index guard (defense-in-depth): a failed ``CREATE INDEX CONCURRENTLY``
    leaves an INVALID index whose name a bare ``IF NOT EXISTS`` retry would skip —
    marking the migration applied with a non-enforcing index. AFTER applying we
    assert **no INVALID index exists at all** and raise ``MigrationApplyError``
    (ledger NOT written → the version stays re-queued and loud) otherwise. This is
    an ABSOLUTE post-condition, not a before/after diff: on the retry that follows
    a failed CONCURRENTLY build the leftover INVALID index is present both before
    and after (the ``IF NOT EXISTS`` create is a no-op), so a diff would see "no
    new invalid" and wrongly record — the exact hole this guard must close. The
    self-healing fix lives in the migration SQL (`DROP INDEX CONCURRENTLY IF EXISTS`
    before the CREATE): the retry then drops the leftover and rebuilds it VALID, so
    the post-condition passes and the migration records. Absolute also means an
    unrelated pre-existing INVALID index blocks — correct: a DB carrying an invalid
    index is broken and must be repaired before more schema is layered on.
    """
    previous_autocommit = getattr(connection, 'autocommit', None)
    if previous_autocommit is not None:
        connection.autocommit = True
    try:
        for statement in split_sql_statements(sql_text):
            with connection.cursor() as cursor:
                cursor.execute(statement)
        leftover_invalid = _invalid_index_names(connection)
        if leftover_invalid:
            raise MigrationApplyError(
                f'{version}: INVALID index(es) present after apply: '
                f'{sorted(leftover_invalid)} (a failed CREATE INDEX CONCURRENTLY '
                'leaves one, and IF NOT EXISTS would skip rebuilding it) — refusing '
                'to record as applied. Author the CONCURRENTLY index as DROP INDEX '
                'CONCURRENTLY IF EXISTS <name>; CREATE ... IF NOT EXISTS <name>; so '
                'the retry self-heals, then re-run migrate.'
            )
        with connection.cursor() as cursor:
            cursor.execute(_RECORD_SQL, (version, digest, _now_iso(), applied_by))
    finally:
        if previous_autocommit is not None:
            connection.autocommit = previous_autocommit


def migrate(
    *,
    dsn: str,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
    applied_by: str,
    record_only: bool = False,
    through: Optional[str] = None,
) -> dict:
    """Apply pending migrations in order under a session advisory lock.

    ``record_only`` (baseline): record versions as applied WITHOUT executing their
    SQL — except 001, which is always executed (idempotent `CREATE … IF NOT EXISTS`)
    so the ledger table itself exists before any row is recorded. ``through`` caps
    the highest version considered (inclusive, by stem).
    """
    migrations = discover_migrations(migrations_dir)
    if through is not None:
        migrations = [(v, p) for (v, p) in migrations if v <= through]
    lock_id = advisory_lock_id(RUN_LOCK_KEY)
    connection = _connect(dsn)
    applied_versions: list[str] = []
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_lock(%s)', (lock_id,))
        connection.commit()

        applied = _read_applied(connection)
        pre_applied = set(applied)
        to_apply, drift = plan_migrations(migrations, applied)
        if drift:
            raise MigrationDriftError('; '.join(drift))

        for version, path, digest in to_apply:
            is_bootstrap = version == migrations[0][0] if migrations else False
            run_sql = (not record_only) or is_bootstrap
            sql_text = path.read_text(encoding='utf-8')
            # A CONCURRENTLY / marked migration cannot run inside a transaction; it
            # takes the autocommit path. Baseline record-only rows (run_sql=False)
            # never execute SQL, so they always take the transactional record path.
            non_transactional = run_sql and migration_requires_non_transactional(sql_text)
            try:
                if non_transactional:
                    _apply_non_transactional(
                        connection,
                        sql_text=sql_text,
                        version=version,
                        digest=digest,
                        applied_by=applied_by,
                    )
                else:
                    with connection.cursor() as cursor:
                        if run_sql:
                            cursor.execute(sql_text)
                        cursor.execute(_RECORD_SQL, (version, digest, _now_iso(), applied_by))
                    connection.commit()
                applied_versions.append(version)
            except Exception:
                connection.rollback()
                raise
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', (lock_id,))
            connection.commit()
        finally:
            connection.close()
    return {
        'applied': applied_versions,
        'skipped': sorted(pre_applied),
        'mode': 'baseline' if record_only else 'migrate',
    }


def reconcile(
    *,
    dsn: str,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
    applied_by: str,
) -> dict:
    """Re-stamp the exporter-owned bootstrap ledger checksum (operator-asserted).

    The single sanctioned exception to the append-only ledger: when the DDL
    exporter regenerates ``001`` (schema SSOT changed), an existing DB's ledger
    still records the old bootstrap checksum, so ``migrate`` would loud-fail with
    drift and block every subsequent incremental migration. ``reconcile`` updates
    ONLY the bootstrap row's checksum to the current file (recording who/when for
    audit), and REFUSES (``MigrationDriftError``) if any NON-bootstrap applied
    migration drifted — a real append-only violation is never rewritten. It never
    executes migration SQL. Run ``reconcile`` then ``migrate`` to unblock
    ``007``/``008`` on an existing DB. Idempotent: a no-op when already matching.
    """
    migrations = discover_migrations(migrations_dir)
    lock_id = advisory_lock_id(RUN_LOCK_KEY)
    connection = _connect(dsn)
    reconciled: list[str] = []
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_lock(%s)', (lock_id,))
        connection.commit()

        applied = _read_applied(connection)
        bootstrap_update, blocking = plan_reconcile(migrations, applied)
        if blocking:
            raise MigrationDriftError('; '.join(blocking))
        if bootstrap_update is not None:
            version, digest = bootstrap_update
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        _RECONCILE_LEDGER_SQL, (digest, _now_iso(), applied_by, version)
                    )
                connection.commit()
                reconciled.append(version)
            except Exception:
                connection.rollback()
                raise
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', (lock_id,))
            connection.commit()
        finally:
            connection.close()
    return {
        'reconciled': reconciled,
        'mode': 'reconcile',
        'applied_by': applied_by,
    }


def rollback(
    *,
    dsn: str,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
    target: str,
    applied_by: str,
) -> dict:
    """Rollback the latest applied migration using its `--rollback` annotations.

    Rollback statements may contain PostgreSQL `CONCURRENTLY`, so they are
    executed without an outer transaction. The session advisory lock still
    serialises the whole operation.
    """
    migrations = discover_migrations(migrations_dir)
    lock_id = advisory_lock_id(RUN_LOCK_KEY)
    connection = _connect(dsn)
    previous_autocommit = getattr(connection, 'autocommit', None)
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_lock(%s)', (lock_id,))
        connection.commit()

        applied = _read_applied(connection)
        _path, statements = plan_rollback(migrations, applied, target=target)

        if previous_autocommit is not None:
            connection.autocommit = True
        for statement in statements:
            with connection.cursor() as cursor:
                cursor.execute(statement)
        with connection.cursor() as cursor:
            cursor.execute(_DELETE_LEDGER_SQL, (target,))
    finally:
        if previous_autocommit is not None:
            connection.autocommit = previous_autocommit
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', (lock_id,))
            connection.commit()
        finally:
            connection.close()
    return {
        'rolled_back': [target],
        'mode': 'rollback',
        'applied_by': applied_by,
    }


def _read_applied_safe(dsn: str) -> dict[str, str]:
    connection = _connect(dsn)
    try:
        return _read_applied(connection)
    finally:
        connection.close()


def status(*, dsn: str, migrations_dir: Path = DEFAULT_MIGRATIONS_DIR) -> dict:
    """Report applied vs pending without mutating anything."""
    migrations = discover_migrations(migrations_dir)
    applied = _read_applied_safe(dsn)
    to_apply, drift = plan_migrations(migrations, applied)
    return {
        'applied': sorted(applied),
        'pending': [v for (v, _p, _c) in to_apply],
        'drift': drift,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Incremental central DB migration runner.')
    parser.add_argument('command', choices=('migrate', 'baseline', 'status', 'rollback', 'reconcile'))
    parser.add_argument('--dsn', default='', help='PostgreSQL DSN (default: FCC_CENTRAL_DB_URL env)')
    parser.add_argument('--migrations-dir', default=str(DEFAULT_MIGRATIONS_DIR))
    parser.add_argument('--applied-by', default='migration-runner')
    parser.add_argument('--through', default=None, help='baseline: highest version stem to record (inclusive)')
    parser.add_argument('--target', default=None, help='rollback: migration version stem to rollback')
    args = parser.parse_args(argv)

    import json
    import os

    dsn = args.dsn or os.environ.get('FCC_CENTRAL_DB_URL', '')
    if not dsn:
        print('error: --dsn or FCC_CENTRAL_DB_URL required', file=sys.stderr)
        return 2
    migrations_dir = Path(args.migrations_dir)
    try:
        if args.command == 'status':
            result = status(dsn=dsn, migrations_dir=migrations_dir)
        elif args.command == 'reconcile':
            result = reconcile(
                dsn=dsn,
                migrations_dir=migrations_dir,
                applied_by=args.applied_by,
            )
        elif args.command == 'rollback':
            if not args.target:
                print(json.dumps({'ok': False, 'error': '--target required for rollback'}, indent=2), file=sys.stderr)
                return 2
            result = rollback(
                dsn=dsn,
                migrations_dir=migrations_dir,
                target=args.target,
                applied_by=args.applied_by,
            )
        else:
            result = migrate(
                dsn=dsn,
                migrations_dir=migrations_dir,
                applied_by=args.applied_by,
                record_only=(args.command == 'baseline'),
                through=args.through,
            )
    except MigrationDriftError as exc:
        print(json.dumps({'ok': False, 'error': 'drift', 'detail': str(exc)}, indent=2), file=sys.stderr)
        return 3
    except MigrationRollbackError as exc:
        print(json.dumps({'ok': False, 'error': 'rollback', 'detail': str(exc)}, indent=2), file=sys.stderr)
        return 4
    except MigrationApplyError as exc:
        print(json.dumps({'ok': False, 'error': 'apply', 'detail': str(exc)}, indent=2), file=sys.stderr)
        return 5
    except Exception as exc:  # noqa: BLE001 — surface loudly with non-zero exit
        print(json.dumps({'ok': False, 'error': str(exc)}, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({'ok': True, **result}, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
