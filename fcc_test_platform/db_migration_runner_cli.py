"""Apply the central DB migration to PostgreSQL and emit migration evidence.

⚠️ 이것은 `scripts/platform_db_migration_runner.py` 의 **알맹이**다. `scripts/` 는 패키지가 아니라
**휠이 나르지 못한다** — 이 레인을 핀으로 받는 소비자에게 그 파일은 오지 않는다.
그래서 로직은 여기 살고 `scripts/` 에는 진입점만 남는다. 껍데기는 양쪽 레포에
두되, 담긴 것이 그뿐이라 **갈라질 것이 없다.**
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping


from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402
from fcc_test_platform.db_migration_evidence import (  # noqa: E402
    EXPECTED_MIGRATION_ID,
    central_db_migration_evidence_errors,
)
from fcc_test_platform.db_migration_collect_cli import (  # noqa: E402
    _connect,
    _current_database,
    _fetch_columns,
    _fetch_indexes,
    build_manifest_from_introspection,
)

# Both through the record rather than a directory walk. Only the second is
# relocated today (docs/platform/migrations/ -> migrations/ at the box root) but
# they are the same question about the same tree, and a file that answers it two
# ways invites the next reader to pick the wrong one.
DEFAULT_SCHEMA_PATH = resolve_repo_artifact(__file__, 'docs/platform/central_db_schema.v1.json')
DEFAULT_MIGRATION_PATH = resolve_repo_artifact(
    __file__, 'docs/platform/migrations/001_initial_central_db.sql',
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Apply central DB migration SQL and collect PostgreSQL evidence.')
    parser.add_argument('--dsn', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--migration-sql', default=str(DEFAULT_MIGRATION_PATH))
    parser.add_argument('--central-db-schema', default=str(DEFAULT_SCHEMA_PATH))
    parser.add_argument('--db-schema-name', default='public')
    parser.add_argument('--migration-id', default=EXPECTED_MIGRATION_ID)
    parser.add_argument('--database-name', default='')
    parser.add_argument('--applied-at', default='')
    parser.add_argument('--applied-by', required=True)
    parser.add_argument('--lock-key', default='fcc-platform:001_initial_central_db')
    parser.add_argument('--require-valid', action='store_true')
    args = parser.parse_args(argv)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        schema_path = Path(args.central_db_schema)
        migration_path = Path(args.migration_sql)
        schema = json.loads(schema_path.read_text(encoding='utf-8'))
        migration_sql = migration_path.read_text(encoding='utf-8')
        manifest = apply_migration_and_collect(
            dsn=args.dsn,
            schema=schema,
            schema_contract_bytes=schema_path.read_bytes(),
            migration_sql=migration_sql,
            db_schema_name=args.db_schema_name,
            migration_id=args.migration_id,
            database_name=args.database_name,
            applied_at=args.applied_at or datetime.now(timezone.utc).isoformat(),
            applied_by=args.applied_by,
            lock_key=args.lock_key,
        )
    except Exception as exc:
        print(
            json.dumps({'applied': False, 'collected': False, 'error': str(exc)}, sort_keys=True, indent=2),
            file=sys.stderr,
        )
        return 2

    output_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8')

    issues = [issue.to_dict() for issue in central_db_migration_evidence_errors(manifest, schema)]
    print(json.dumps({'applied': True, 'collected': True, 'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    if args.require_valid and issues:
        return 1
    return 0


def apply_migration_and_collect(
    *,
    dsn: str,
    schema: Mapping,
    schema_contract_bytes: bytes,
    migration_sql: str,
    db_schema_name: str,
    migration_id: str,
    database_name: str,
    applied_at: str,
    applied_by: str,
    lock_key: str,
) -> dict:
    lock_id = advisory_lock_id(lock_key)
    connection = _connect(dsn)
    try:
        _apply_sql(connection, migration_sql, lock_id)
        actual_database_name = database_name or _current_database(connection)
        columns = _fetch_columns(connection, db_schema_name)
        indexes = _fetch_indexes(connection, db_schema_name)
    finally:
        connection.close()
    return build_manifest_from_introspection(
        schema=schema,
        schema_contract_bytes=schema_contract_bytes,
        database_name=actual_database_name,
        migration_id=migration_id,
        migration_status='applied',
        applied_at=applied_at,
        applied_by=applied_by,
        columns=columns,
        indexes=indexes,
    )


def advisory_lock_id(lock_key: str) -> int:
    digest = hashlib.sha256(lock_key.encode('utf-8')).digest()
    return int.from_bytes(digest[:8], byteorder='big', signed=False) & ((1 << 63) - 1)


def _apply_sql(connection, migration_sql: str, lock_id: int) -> None:
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', (lock_id,))
            cursor.execute(migration_sql)
        connection.commit()
    except Exception:
        connection.rollback()
        raise


if __name__ == '__main__':
    raise SystemExit(main())
