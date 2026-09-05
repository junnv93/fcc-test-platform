"""Collect central DB migration evidence from a PostgreSQL database.

The validator in platform_db_migration_evidence.py remains dependency-free.
This collector is the runtime bridge that introspects PostgreSQL metadata and
emits a manifest compatible with the cutover readiness gate.

⚠️ 이것은 `scripts/platform_db_migration_collect.py` 의 **알맹이**다. `scripts/` 는 패키지가 아니라
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
import re
import sys
from typing import Mapping, Sequence


from fcc_test_contracts.common.tree_artifacts import discover_tree_artifact  # noqa: E402
from fcc_test_platform.db_migration_evidence import (  # noqa: E402
    EXPECTED_DATABASE_ENGINE,
    EXPECTED_MIGRATION_ID,
    _normalise_predicate,
    central_db_migration_evidence_errors,
)
from fcc_test_platform.export_central_db_ddl_cli import render_ddl  # noqa: E402

DEFAULT_SCHEMA_PATH = discover_tree_artifact(__file__, 'docs', 'platform', 'central_db_schema.v1.json')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Collect central DB migration evidence from PostgreSQL.')
    parser.add_argument('--dsn', required=True, help='PostgreSQL DSN for a read-only metadata connection')
    parser.add_argument('--output', required=True)
    parser.add_argument('--central-db-schema', default=str(DEFAULT_SCHEMA_PATH))
    parser.add_argument('--db-schema-name', default='public')
    parser.add_argument('--migration-id', default=EXPECTED_MIGRATION_ID)
    parser.add_argument('--migration-status', default='applied')
    parser.add_argument('--database-name', default='')
    parser.add_argument('--applied-at', default='')
    parser.add_argument('--applied-by', required=True)
    parser.add_argument('--require-valid', action='store_true')
    args = parser.parse_args(argv)

    try:
        schema_path = Path(args.central_db_schema)
        schema = json.loads(schema_path.read_text(encoding='utf-8'))
        manifest = collect_from_database(
            dsn=args.dsn,
            schema=schema,
            schema_contract_bytes=schema_path.read_bytes(),
            db_schema_name=args.db_schema_name,
            migration_id=args.migration_id,
            migration_status=args.migration_status,
            database_name=args.database_name,
            applied_at=args.applied_at or datetime.now(timezone.utc).isoformat(),
            applied_by=args.applied_by,
        )
    except Exception as exc:
        print(json.dumps({'collected': False, 'error': str(exc)}, sort_keys=True, indent=2), file=sys.stderr)
        return 2

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8')

    issues = [issue.to_dict() for issue in central_db_migration_evidence_errors(manifest, schema)]
    print(json.dumps({'collected': True, 'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    if args.require_valid and issues:
        return 1
    return 0


def collect_from_database(
    *,
    dsn: str,
    schema: Mapping,
    schema_contract_bytes: bytes,
    db_schema_name: str,
    migration_id: str,
    migration_status: str,
    database_name: str,
    applied_at: str,
    applied_by: str,
) -> dict:
    connection = _connect(dsn)
    try:
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
        migration_status=migration_status,
        applied_at=applied_at,
        applied_by=applied_by,
        columns=columns,
        indexes=indexes,
    )


def build_manifest_from_introspection(
    *,
    schema: Mapping,
    schema_contract_bytes: bytes,
    database_name: str,
    migration_id: str,
    migration_status: str,
    applied_at: str,
    applied_by: str,
    columns: Sequence[Mapping],
    indexes: Sequence[Mapping],
) -> dict:
    tables: dict[str, dict] = {}
    for row in columns:
        table_name = str(row['table_name'])
        tables.setdefault(table_name, {'columns': [], 'indexes': []})['columns'].append({
            'name': str(row['column_name']),
            'type': _evidence_type(row),
            'nullable': str(row.get('is_nullable', '')).upper() == 'YES',
        })
    for row in indexes:
        table_name = str(row['table_name'])
        definition = str(row.get('index_definition', ''))
        columns = _index_columns(definition)
        tables.setdefault(table_name, {'columns': [], 'indexes': []})['indexes'].append({
            'name': str(row['index_name']),
            'columns': columns,
            'unique': _index_unique(definition),
            'orders': _index_orders(definition, columns),
            'where': _index_predicate(definition),
        })
    ddl = render_ddl(schema)
    return {
        'schema_version': 1,
        'migration_id': migration_id,
        'database_engine': EXPECTED_DATABASE_ENGINE,
        'database_name': database_name,
        'migration_status': migration_status,
        'applied_at': applied_at,
        'applied_by': applied_by,
        'schema_contract_sha256': hashlib.sha256(schema_contract_bytes).hexdigest(),
        'ddl_sha256': hashlib.sha256(ddl.encode('utf-8')).hexdigest(),
        'tables': tables,
    }


def _connect(dsn: str):
    try:
        import psycopg

        return psycopg.connect(dsn)
    except ModuleNotFoundError:
        import psycopg2

        return psycopg2.connect(dsn)


def _current_database(connection) -> str:
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database()')
        return str(cursor.fetchone()[0])


def _fetch_columns(connection, db_schema_name: str) -> list[dict]:
    # Restrict to BASE TABLE objects so the evidence 'tables' set matches the
    # schema SSOT 'tables' (which lists base tables only; views and
    # materialized views are separate top-level keys). Without this join,
    # information_schema.columns also yields view columns, which the validator
    # then rejects as unknown_table.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.table_name, c.column_name, c.data_type, c.udt_name, c.is_nullable
            FROM information_schema.columns AS c
            JOIN information_schema.tables AS t
              ON t.table_schema = c.table_schema
             AND t.table_name = c.table_name
            WHERE c.table_schema = %s
              AND t.table_type = 'BASE TABLE'
            ORDER BY c.table_name, c.ordinal_position
            """,
            (db_schema_name,),
        )
        return [_row_dict(cursor, row) for row in cursor.fetchall()]


def _fetch_indexes(connection, db_schema_name: str) -> list[dict]:
    # Same BASE TABLE restriction as _fetch_columns. Materialized views (e.g.
    # coverage_by_condition_hash) carry their own unique index for CONCURRENT
    # refresh; that index must not leak into per-table evidence. Materialized
    # views are absent from information_schema.tables, so the join drops them.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT i.tablename AS table_name, i.indexname AS index_name, i.indexdef AS index_definition
            FROM pg_indexes AS i
            JOIN information_schema.tables AS t
              ON t.table_schema = i.schemaname
             AND t.table_name = i.tablename
            WHERE i.schemaname = %s
              AND t.table_type = 'BASE TABLE'
            ORDER BY i.tablename, i.indexname
            """,
            (db_schema_name,),
        )
        return [_row_dict(cursor, row) for row in cursor.fetchall()]


def _row_dict(cursor, row) -> dict:
    if isinstance(row, Mapping):
        return dict(row)
    names = [description[0] for description in cursor.description]
    return dict(zip(names, row))


def _evidence_type(row: Mapping) -> str:
    data_type = str(row.get('data_type') or '').lower()
    udt_name = str(row.get('udt_name') or '').lower()
    if data_type == 'jsonb' or udt_name == 'jsonb':
        return 'jsonb'
    if udt_name == 'uuid':
        return 'uuid'
    return data_type or udt_name


def _index_unique(index_definition: str) -> bool:
    return index_definition.upper().startswith('CREATE UNIQUE INDEX')


def _index_columns(index_definition: str) -> list[str]:
    # The key-column list is the balanced parenthesis group that immediately
    # follows "USING <method>". A trailing WHERE predicate (partial index, e.g.
    # "... (idempotency_key) WHERE (idempotency_key IS NOT NULL)") lives in a
    # separate parenthesis group and MUST NOT be parsed as columns. Balancing
    # nested parens also keeps expression indexes (e.g. "lower(name)") intact.
    anchor = re.search(r'\bUSING\s+\w+\s*\(', index_definition, re.IGNORECASE)
    start = anchor.end() if anchor else index_definition.find('(') + 1
    if start <= 0:
        return []
    inner = _balanced_group(index_definition, start)
    columns = []
    for raw_column in _split_top_level(inner):
        column = raw_column.strip()
        if not column:
            continue
        # pg_indexes.indexdef includes sort direction and NULL placement in
        # the key expression (for example ``measured_at DESC NULLS LAST``).
        # The schema SSOT stores column identity separately from ordering, so
        # remove only those PostgreSQL index-order decorations here. Keep
        # expressions such as ``lower(name)`` intact.
        column = re.sub(
            r'\s+(?:(?:ASC|DESC)(?:\s+NULLS\s+(?:FIRST|LAST))?|'
            r'NULLS\s+(?:FIRST|LAST))\s*$',
            '',
            column,
            flags=re.IGNORECASE,
        )
        columns.append(column.strip().strip('"'))
    return columns


def _index_orders(index_definition: str, columns: Sequence[str]) -> dict[str, str]:
    """Capture PostgreSQL key ordering/null placement separately from columns."""
    anchor = re.search(r'\bUSING\s+\w+\s*\(', index_definition, re.IGNORECASE)
    start = anchor.end() if anchor else index_definition.find('(') + 1
    if start <= 0:
        return {}
    keys = [part.strip() for part in _split_top_level(_balanced_group(index_definition, start))]
    orders: dict[str, str] = {}
    for column, key in zip(columns, keys):
        match = re.search(
            r'\s+(?P<direction>ASC|DESC)(?:\s+NULLS\s+(?P<nulls>FIRST|LAST))?\s*$',
            key,
            re.IGNORECASE,
        )
        if not match:
            match = re.search(r'\s+NULLS\s+(?P<nulls>FIRST|LAST)\s*$', key, re.IGNORECASE)
        if not match:
            continue
        direction = (match.groupdict().get('direction') or 'ASC').upper()
        nulls = match.groupdict().get('nulls')
        value = direction
        if nulls:
            value += f' NULLS {nulls.upper()}'
        orders[column] = value
    return orders


def _index_predicate(index_definition: str) -> str | None:
    match = re.search(r'\s+WHERE\s+(.+)$', index_definition, re.IGNORECASE)
    if not match:
        return None
    return _normalise_predicate(match.group(1))


def _index_metadata(index_definition: str) -> dict[str, object]:
    """Return the legacy grouped view without rejoining semantic fields.

    Older collector consumers imported this helper before index order and
    predicate became separate manifest fields. Keep that import-compatible
    view as a thin adapter over the current helpers so callers receive the
    same normalized ``orders``/``where`` semantics as the manifest builder.
    """
    columns = _index_columns(index_definition)
    orders = _index_orders(index_definition, columns)
    predicate = _index_predicate(index_definition)
    metadata: dict[str, object] = {}
    if orders:
        metadata['orders'] = orders
    if predicate is not None:
        metadata['where'] = predicate
    return metadata


def _balanced_group(text: str, start: int) -> str:
    depth = 1
    index = start
    while index < len(text) and depth > 0:
        char = text[index]
        if char in "'\"":
            index = _quoted_end(text, index, char)
            continue
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 0:
                return text[start:index]
        index += 1
    return text[start:index]


def _split_top_level(inner: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current = ''
    index = 0
    while index < len(inner):
        char = inner[index]
        if char in "'\"":
            end = _quoted_end(inner, index, char)
            current += inner[index:end]
            index = end
            continue
        if char == '(':
            depth += 1
            current += char
        elif char == ')':
            depth -= 1
            current += char
        elif char == ',' and depth == 0:
            parts.append(current)
            current = ''
        else:
            current += char
        index += 1
    parts.append(current)
    return parts


def _quoted_end(text: str, start: int, quote: str) -> int:
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == quote:
            if index + 1 < len(text) and text[index + 1] == quote:
                index += 2
                continue
            return index + 1
        if quote == "'" and char == '\\' and index + 1 < len(text):
            index += 2
            continue
        index += 1
    return len(text)


if __name__ == '__main__':
    raise SystemExit(main())
