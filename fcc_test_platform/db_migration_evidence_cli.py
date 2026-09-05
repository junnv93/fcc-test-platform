"""Validate and template central DB migration evidence manifests.

⚠️ 이것은 `scripts/platform_db_migration_evidence.py` 의 **알맹이**다. `scripts/` 는 패키지가 아니라
**휠이 나르지 못한다** — 이 레인을 핀으로 받는 소비자에게 그 파일은 오지 않는다.
그래서 로직은 여기 살고 `scripts/` 에는 진입점만 남는다. 껍데기는 양쪽 레포에
두되, 담긴 것이 그뿐이라 **갈라질 것이 없다.**
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


from fcc_test_contracts.common.tree_artifacts import discover_tree_artifact  # noqa: E402
from fcc_test_platform.db_migration_evidence import central_db_migration_evidence_errors  # noqa: E402

DEFAULT_SCHEMA_PATH = discover_tree_artifact(__file__, 'docs', 'platform', 'central_db_schema.v1.json')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Central DB migration evidence helper.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    validate = subparsers.add_parser('validate')
    validate.add_argument('manifest')
    validate.add_argument('--central-db-schema', default=str(DEFAULT_SCHEMA_PATH))
    template = subparsers.add_parser('template')
    template.add_argument('--central-db-schema', default=str(DEFAULT_SCHEMA_PATH))
    template.add_argument('--migration-id', default='001_initial_central_db')
    template.add_argument('--database-name', default='fcc_platform')
    template.add_argument('--applied-at', default='<ISO-8601 timestamp>')
    args = parser.parse_args(argv)

    if args.command == 'validate':
        return _validate(Path(args.manifest), Path(args.central_db_schema))
    if args.command == 'template':
        return _print_template(Path(args.central_db_schema), args.migration_id, args.database_name, args.applied_at)
    return 2


def _validate(manifest_path: Path, schema_path: Path) -> int:
    try:
        manifest = _read_json(manifest_path)
        schema = _read_json(schema_path)
    except Exception as exc:
        print(json.dumps(_read_error(exc), sort_keys=True, indent=2))
        return 2
    issues = [issue.to_dict() for issue in central_db_migration_evidence_errors(manifest, schema)]
    print(json.dumps({'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    return 0 if not issues else 1


def _print_template(schema_path: Path, migration_id: str, database_name: str, applied_at: str) -> int:
    try:
        schema = _read_json(schema_path)
    except Exception as exc:
        print(json.dumps(_read_error(exc), sort_keys=True, indent=2))
        return 2
    print(json.dumps(_template(schema, migration_id, database_name, applied_at), sort_keys=True, indent=2))
    return 0


def _template(schema: dict, migration_id: str, database_name: str, applied_at: str) -> dict:
    return {
        'schema_version': 1,
        'migration_id': migration_id,
        'database_engine': 'postgresql',
        'database_name': database_name,
        'migration_status': '<applied>',
        'applied_at': applied_at,
        'applied_by': '<operator or automation id>',
        'schema_contract_sha256': '<schema contract sha256>',
        'ddl_sha256': '<ddl sha256>',
        'tables': {
            table_name: {
                'columns': [
                    {
                        'name': column_name,
                        'type': _evidence_type(column['type']),
                        'nullable': not bool(column.get('required')),
                    }
                    for column_name, column in table.get('columns', {}).items()
                ],
                'indexes': [
                    {
                        'name': index['name'],
                        # An expression index (e.g. a trigram GIN on
                        # lower(col)) has `expressions` instead of `columns`.
                        # The schema SSOT stores them in PostgreSQL's own
                        # canonical rendering, so the projected key list is
                        # byte-identical to what pg_indexes introspection yields.
                        'columns': list(index.get('columns') or index.get('expressions') or ()),
                        'unique': bool(index.get('unique')),
                        'orders': dict(index.get('orders') or {}),
                        'where': index.get('where'),
                    }
                    for index in table.get('indexes', [])
                ],
            }
            for table_name, table in schema.get('tables', {}).items()
        },
    }


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _read_error(exc: Exception) -> dict:
    return {
        'valid': False,
        'issues': [{
            'code': 'read_error',
            'path': str(getattr(exc, 'filename', '') or ''),
            'message': str(exc),
        }],
    }


def _evidence_type(schema_type: str) -> str:
    return {
        'uuid': 'uuid',
        'text': 'text',
        'json': 'jsonb',
        'boolean': 'boolean',
        'timestamp': 'timestamp with time zone',
        'integer': 'integer',
        'numeric': 'numeric',
    }[schema_type]


if __name__ == '__main__':
    raise SystemExit(main())
