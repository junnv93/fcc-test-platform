"""Collect applied RBAC assignment evidence from a PostgreSQL platform DB.

⚠️ 이것은 `scripts/platform_rbac_assignment_collect.py` 의 **알맹이**다. `scripts/` 는 패키지가 아니라
**휠이 나르지 못한다** — 이 레인을 핀으로 받는 소비자에게 그 파일은 오지 않는다.
그래서 로직은 여기 살고 `scripts/` 에는 진입점만 남는다. 껍데기는 양쪽 레포에
두되, 담긴 것이 그뿐이라 **갈라질 것이 없다.**
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence


from fcc_test_platform.rbac_assignment_evidence import rbac_assignment_evidence_errors  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Collect RBAC assignment evidence from PostgreSQL.')
    parser.add_argument('--dsn', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--evidence-id', required=True)
    parser.add_argument('--db-schema-name', default='public')
    parser.add_argument('--require-valid', action='store_true')
    args = parser.parse_args(argv)

    try:
        manifest = collect_from_database(
            dsn=args.dsn,
            evidence_id=args.evidence_id,
            db_schema_name=args.db_schema_name,
        )
    except Exception as exc:
        print(json.dumps({'collected': False, 'error': str(exc)}, sort_keys=True, indent=2), file=sys.stderr)
        return 2

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    issues = [issue.to_dict() for issue in rbac_assignment_evidence_errors(manifest)]
    print(json.dumps({'collected': True, 'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    if args.require_valid and issues:
        return 1
    return 0


def collect_from_database(*, dsn: str, evidence_id: str, db_schema_name: str) -> dict:
    connection = _connect(dsn)
    try:
        rows = {
            'permissions': _fetch(connection, db_schema_name, 'permissions', ('permission_key', 'description')),
            'roles': _fetch(connection, db_schema_name, 'roles', ('role_key', 'description')),
            'role_permissions': _fetch_role_permissions(connection, db_schema_name),
            'users': _fetch(connection, db_schema_name, 'users', ('subject', 'display_name', 'email', 'enabled')),
            'user_roles': _fetch_user_roles(connection, db_schema_name),
        }
    finally:
        connection.close()
    return build_manifest_from_rows(evidence_id=evidence_id, **rows)


def build_manifest_from_rows(
    *,
    evidence_id: str,
    permissions: Sequence[Mapping],
    roles: Sequence[Mapping],
    role_permissions: Sequence[Mapping],
    users: Sequence[Mapping],
    user_roles: Sequence[Mapping],
) -> dict:
    permissions_by_role: dict[str, list[str]] = {}
    for row in role_permissions:
        role_key = _text(row.get('role_key'))
        permission_key = _text(row.get('permission_key'))
        if role_key and permission_key:
            permissions_by_role.setdefault(role_key, []).append(permission_key)
    return {
        'schema_version': 1,
        'evidence_id': evidence_id,
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'rbac_source': 'docs/platform/rbac_policy.v1.json',
        'permissions': [
            {
                'permission_key': _text(row.get('permission_key')),
                'description': _text(row.get('description')),
            }
            for row in permissions
        ],
        'roles': [
            {
                'role_key': _text(row.get('role_key')),
                'description': _text(row.get('description')),
                'permission_keys': sorted(set(permissions_by_role.get(_text(row.get('role_key')), []))),
            }
            for row in roles
        ],
        'users': [
            {
                'subject': _text(row.get('subject')),
                'display_name': _text(row.get('display_name')),
                'email': _text(row.get('email')),
                'enabled': row.get('enabled') is True,
            }
            for row in users
        ],
        'user_roles': [
            {
                'subject': _text(row.get('subject')),
                'role_key': _text(row.get('role_key')),
            }
            for row in user_roles
        ],
    }


def _connect(dsn: str):
    try:
        import psycopg

        return psycopg.connect(dsn)
    except ModuleNotFoundError:
        import psycopg2

        return psycopg2.connect(dsn)


def _fetch(connection, db_schema_name: str, table_name: str, columns: tuple[str, ...]) -> list[dict]:
    schema = _quote_ident(db_schema_name)
    table = _quote_ident(table_name)
    column_sql = ', '.join(_quote_ident(column) for column in columns)
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {column_sql} FROM {schema}.{table} ORDER BY 1')
        return [_row_dict(cursor, row) for row in cursor.fetchall()]


def _fetch_role_permissions(connection, db_schema_name: str) -> list[dict]:
    schema = _quote_ident(db_schema_name)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT r.role_key, p.permission_key
            FROM {schema}.role_permissions rp
            JOIN {schema}.roles r ON r.id = rp.role_id
            JOIN {schema}.permissions p ON p.id = rp.permission_id
            ORDER BY r.role_key, p.permission_key
            """
        )
        return [_row_dict(cursor, row) for row in cursor.fetchall()]


def _fetch_user_roles(connection, db_schema_name: str) -> list[dict]:
    schema = _quote_ident(db_schema_name)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT u.subject, r.role_key
            FROM {schema}.user_roles ur
            JOIN {schema}.users u ON u.id = ur.user_id
            JOIN {schema}.roles r ON r.id = ur.role_id
            ORDER BY u.subject, r.role_key
            """
        )
        return [_row_dict(cursor, row) for row in cursor.fetchall()]


def _row_dict(cursor, row) -> dict:
    if isinstance(row, Mapping):
        return dict(row)
    names = [description[0] for description in cursor.description]
    return dict(zip(names, row))


def _text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _quote_ident(value: str) -> str:
    text = _text(value)
    if not text:
        raise ValueError('identifier is required')
    return '"' + text.replace('"', '""') + '"'


if __name__ == '__main__':
    raise SystemExit(main())
