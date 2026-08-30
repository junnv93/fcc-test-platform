"""Export PostgreSQL DDL from the platform central DB schema contract."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping

# Package-internal "where am I", not artifact resolution: the delivered box keeps
# scripts/ at the same depth (asserted by tests/test_repository_artifact_depth_axis.py
# ::test_every_planned_script_entrypoint_keeps_its_depth), which is what makes this
# bootstrap safe and is why the depth detector does not — and should not — flag it.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402

# This module used to carry a private byte-identical copy of the nearest-ancestor
# resolver, because the import boundary below forbade ``application.*`` outright.
# The copy is gone and the boundary now judges what it always meant — whether an
# import drags in a runtime this exporter must not require — by measuring the
# imported module's transitive first-party closure instead of its spelling.
# ``tree_artifacts`` declares itself standard-library-only, and that declaration
# is now checked rather than trusted.
#
# The repair is not a style preference: the copy resolved *depth* while the
# delivered box *relocates* docs/platform/migrations/ to migrations/ at its root,
# so the exporter wrote to a directory the box does not have. Making the copy
# record-aware would have meant duplicating three more functions, which is the
# drift the SSOT's own docstring exists to refuse.
DEFAULT_SCHEMA = resolve_repo_artifact(__file__, 'docs/platform/central_db_schema.v1.json')
DEFAULT_OUTPUT = resolve_repo_artifact(
    __file__, 'docs/platform/migrations/001_initial_central_db.sql',
)


TYPE_MAP = {
    'uuid': 'UUID',
    'text': 'TEXT',
    'json': 'JSONB',
    'boolean': 'BOOLEAN',
    'timestamp': 'TIMESTAMPTZ',
    'integer': 'INTEGER',
    'numeric': 'NUMERIC',
}


#: Extensions an empty DB needs before the tables/indexes are created. Declared in
#: the schema SSOT (``required_extensions``); the literal default keeps a schema
#: that predates the key rendering byte-identically.
DEFAULT_REQUIRED_EXTENSIONS: tuple[str, ...] = ('pgcrypto',)


def render_ddl(schema: Mapping) -> str:
    tables = schema['tables']
    lines: list[str] = [
        '-- Generated from docs/platform/central_db_schema.v1.json.',
        '-- PostgreSQL initial migration planning artifact for fcc-test-platform.',
        '-- Do not edit table definitions here without updating the JSON schema SSOT.',
        '',
    ]
    for extension in schema.get('required_extensions') or DEFAULT_REQUIRED_EXTENSIONS:
        lines.append(f'CREATE EXTENSION IF NOT EXISTS {extension};')
    lines.append('')
    for table_name, table in tables.items():
        lines.extend(_render_table(table_name, table))
        lines.append('')
    for table_name, table in tables.items():
        indexes = table.get('indexes') or []
        if not indexes:
            continue
        lines.append(f'-- Indexes: {table_name}')
        for index in indexes:
            lines.append(_index_sql(table_name, index))
        lines.append('')

    # Additive column upgrades MUST be emitted AFTER the tables/indexes but BEFORE
    # the views — an existing DB that predates an additive column needs the column
    # added before the CREATE OR REPLACE VIEW that references it is (re)run.
    lines.extend(_render_additive_upgrades(tables))

    materialized_views = schema.get('materialized_views') or {}
    if materialized_views:
        lines.extend(_render_materialized_views(materialized_views))

    views = schema.get('views') or {}
    if views:
        lines.extend(_render_views(views))

    refresh_policies = schema.get('refresh_policies') or {}
    if refresh_policies:
        lines.extend(_render_refresh_policies(refresh_policies))

    ingestion_contract = schema.get('ingestion_contract') or {}
    if ingestion_contract:
        lines.extend(_render_ingestion_contract(ingestion_contract))

    mappings = schema.get('local_central_field_mappings') or {}
    if mappings:
        lines.extend(_render_local_central_field_mappings(mappings))

    rbac = schema.get('rbac_role_grants') or {}
    if rbac:
        lines.extend(_render_rbac_role_grants(rbac))

    return '\n'.join(lines).rstrip() + '\n'


def _index_sql(table_name: str, index: Mapping) -> str:
    """Render one CREATE INDEX.

    Two shapes:

    * ``columns`` — plain column list, each quoted (the historical form).
    * ``expressions`` — VERBATIM SQL expressions, used for functional indexes the
      column form cannot express (e.g. ``LOWER("customer") gin_trgm_ops``). They
      are emitted as written, so the schema JSON must carry them already quoted;
      that is deliberate — inventing a mini expression language here would be a
      second SQL dialect to maintain.

    ``using`` selects a non-default access method (``gin`` for trigram search).
    """
    unique = 'UNIQUE ' if index.get('unique') else ''
    expressions = index.get('expressions')
    if expressions:
        body = ', '.join(expressions)
    else:
        body = _ordered_columns_sql(index)
    using = index.get('using')
    using_sql = f'USING {using} ' if using else ''
    where_clause = index.get('where')
    where_sql = f' WHERE {where_clause}' if where_clause else ''
    return (
        f'CREATE {unique}INDEX IF NOT EXISTS {_quote_name(index["name"])} '
        f'ON {_quote_name(table_name)} {using_sql}({body}){where_sql};'
    )


def load_schema(path: Path = DEFAULT_SCHEMA) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _render_table(table_name: str, table: Mapping) -> list[str]:
    column_lines = [
        f'    {_quote_name(name)} {_column_sql(table_name, name, spec)}'
        for name, spec in table['columns'].items()
    ]
    # Table-level CHECKs (2026-08-11). Column ``allowed_values`` covers "this
    # column is one of these"; a constraint that relates TWO columns has nowhere
    # to live on either of them. The first case is the reference snapshot link:
    # a WEB_AUTHORED revision legitimately has no workbook snapshot, every other
    # kind must still carry one. Expressions are emitted verbatim, exactly like
    # index ``where`` clauses above — inventing a mini expression language here
    # would be a second SQL dialect to maintain.
    #
    # This exists so a FRESH database and an UPGRADED one end up with the same
    # constraints. Leaving the CHECK only in the incremental migration would mean
    # the guarantee holds on deployed DBs and silently does not on new ones.
    for check in table.get('checks') or []:
        column_lines.append(
            f'    CONSTRAINT {_quote_name(check["name"])} CHECK ({check["expression"]})'
        )
    header_lines = [f'-- {table.get("purpose", table_name)}']
    if table.get('append_only'):
        header_lines.append('-- append-only: writers must INSERT only; updates of historical rows are forbidden.')
    reserved = table.get('reserved_for')
    if reserved:
        header_lines.append(f'-- reserved for {reserved}: stub columns only; populate in the dedicated sprint.')
    return [
        *header_lines,
        f'CREATE TABLE IF NOT EXISTS {_quote_name(table_name)} (',
        ',\n'.join(column_lines),
        ');',
    ]


def _column_sql(table_name: str, column_name: str, spec: Mapping) -> str:
    db_type = TYPE_MAP[spec['type']]
    parts = [db_type]
    if column_name == 'id' and spec['type'] == 'uuid':
        parts.append('PRIMARY KEY')
    if spec.get('required') and not (column_name == 'id' and spec['type'] == 'uuid'):
        parts.append('NOT NULL')
    if spec.get('unique') and not (column_name == 'id' and spec['type'] == 'uuid'):
        parts.append('UNIQUE')
    # A ``default`` is a raw SQL expression (e.g. ``gen_random_uuid()`` / ``now()``)
    # rendered verbatim — the DB, not the caller, owns the value. This lets the
    # shared ingestion mapper (which omits id/created_at) INSERT without stamping,
    # so both dev_seed and the production sync adapter rely on the same DB owner.
    default = spec.get('default')
    if default is not None:
        parts.append(f'DEFAULT {default}')
    reference = spec.get('references')
    if reference:
        ref_table, ref_column = reference.split('.', 1)
        parts.append(f'REFERENCES {_quote_name(ref_table)}({_quote_name(ref_column)})')
        on_delete = spec.get('on_delete')
        if on_delete:
            parts.append(f'ON DELETE {on_delete}')
    allowed_values = spec.get('allowed_values')
    if allowed_values:
        quoted = ', '.join(_quote_value(value) for value in allowed_values)
        check_name = f'ck_{table_name}_{column_name}'
        parts.append(f'CONSTRAINT {_quote_name(check_name)} CHECK ({_quote_name(column_name)} IN ({quoted}))')
    return ' '.join(parts)


def _column_alter_sql(table_name: str, column_name: str, spec: Mapping) -> str:
    """Type (+ optional FK / CHECK) clause for an additive ``ADD COLUMN``.

    Deliberately omits PRIMARY KEY / NOT NULL / UNIQUE: an additive column added to
    a populated table must be nullable (existing rows have no value), so the caller
    guards ``required`` away. CHECK/REFERENCES stay forward-safe for future additive
    columns; ``last_error_json`` / ``progress_json`` are plain nullable JSONB.
    """
    db_type = TYPE_MAP[spec['type']]
    parts = [db_type]
    reference = spec.get('references')
    if reference:
        ref_table, ref_column = reference.split('.', 1)
        parts.append(f'REFERENCES {_quote_name(ref_table)}({_quote_name(ref_column)})')
        on_delete = spec.get('on_delete')
        if on_delete:
            parts.append(f'ON DELETE {on_delete}')
    allowed_values = spec.get('allowed_values')
    if allowed_values:
        quoted = ', '.join(_quote_value(value) for value in allowed_values)
        check_name = f'ck_{table_name}_{column_name}'
        parts.append(
            f'CONSTRAINT {_quote_name(check_name)} '
            f'CHECK ({_quote_name(column_name)} IN ({quoted}))'
        )
    return ' '.join(parts)


def _render_additive_upgrades(tables: Mapping) -> list[str]:
    """Idempotent ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` for columns marked
    ``added_in`` (introduced after the table's initial shape).

    Fresh DBs already have the column from ``CREATE TABLE`` above, so these are
    no-ops there. An EXISTING central DB provisioned before the column was added
    receives it here — and because this section precedes the views, re-running this
    single committed migration against an older DB stays additive-safe (the
    ``CREATE OR REPLACE VIEW chamber_availability`` that references the column no
    longer fails on a missing column). ``ADD COLUMN IF NOT EXISTS`` is native to
    PostgreSQL 9.6+ and idempotent.
    """
    upgrades: list[tuple[str, str, Mapping]] = []
    for table_name, table in tables.items():
        for column_name, spec in table['columns'].items():
            if not spec.get('added_in'):
                continue
            if spec.get('required'):
                raise ValueError(
                    f'additive column {table_name}.{column_name} marked added_in must '
                    'be nullable (required=false) — a NOT NULL ADD COLUMN breaks on '
                    'existing rows with no default'
                )
            upgrades.append((table_name, column_name, spec))
    if not upgrades:
        return []
    lines: list[str] = [
        '-- Additive column upgrades (idempotent, PostgreSQL 9.6+).',
        '-- Existing central DBs created before a column was introduced receive it via',
        '-- ALTER ... ADD COLUMN IF NOT EXISTS BEFORE the views below are (re)created,',
        '-- so re-running this single migration against an older DB stays additive-safe.',
        '-- Fresh DBs already have the column from CREATE TABLE above (these are no-ops).',
        '',
    ]
    for table_name, column_name, spec in upgrades:
        lines.append(
            f'-- {table_name}.{column_name}: added in {spec["added_in"]}.'
        )
        lines.append(
            f'ALTER TABLE {_quote_name(table_name)} ADD COLUMN IF NOT EXISTS '
            f'{_quote_name(column_name)} {_column_alter_sql(table_name, column_name, spec)};'
        )
    lines.append('')
    return lines


def _quote_value(value: object) -> str:
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _render_materialized_views(materialized_views: Mapping) -> list[str]:
    lines: list[str] = [
        '-- Materialized views (FE-P0a): coverage read model derived from append-only attempts.',
        '',
    ]
    for view_name, view in materialized_views.items():
        purpose = view.get('purpose', view_name)
        select_sql = str(view.get('select', '')).strip()
        lines.append(f'-- {purpose}')
        policy_ref = view.get('refresh_policy_ref')
        if policy_ref:
            lines.append(f'-- refresh policy: see refresh_policies.{policy_ref}.')
        lines.append(f'CREATE MATERIALIZED VIEW IF NOT EXISTS {_quote_name(view_name)} AS')
        lines.append(f'    {select_sql};')
        lines.append('')
        for index in view.get('indexes') or []:
            unique = 'UNIQUE ' if index.get('unique') else ''
            columns = _ordered_columns_sql(index)
            lines.append(
                f'CREATE {unique}INDEX IF NOT EXISTS {_quote_name(index["name"])} '
                f'ON {_quote_name(view_name)} ({columns});'
            )
        lines.append('')
    return lines


def _ordered_columns_sql(index: Mapping) -> str:
    """Render ordinary index columns with optional PostgreSQL order metadata.

    The historical ``columns`` form remains byte-identical when ``orders`` is
    absent. ``orders`` is deliberately a small declarative extension rather
    than embedding SQL in column names, so schema consumers can still inspect
    the logical leading columns while the generated DDL preserves the exact
    NULLS-last recency seek required by result selection.
    """
    orders = index.get('orders') or {}
    rendered: list[str] = []
    for column in index['columns']:
        suffix = str(orders.get(column) or '').strip()
        if suffix and suffix not in {'ASC', 'DESC', 'ASC NULLS FIRST', 'ASC NULLS LAST',
                                     'DESC NULLS FIRST', 'DESC NULLS LAST'}:
            raise ValueError(f'unsupported index order for {column!r}: {suffix!r}')
        rendered.append(f'{_quote_name(column)} {suffix}'.rstrip())
    return ', '.join(rendered)


def _render_views(views: Mapping) -> list[str]:
    lines: list[str] = [
        '-- Plain views (FE-P0a): active claim projection over append-only claim_events.',
        '',
    ]
    for view_name, view in views.items():
        purpose = view.get('purpose', view_name)
        select_sql = str(view.get('select', '')).strip()
        lines.append(f'-- {purpose}')
        lines.append(f'CREATE OR REPLACE VIEW {_quote_name(view_name)} AS')
        lines.append(f'    {select_sql};')
        lines.append('')
    return lines


def _render_refresh_policies(refresh_policies: Mapping) -> list[str]:
    lines: list[str] = [
        '-- Refresh policies (FE-P0a). These are operational contracts implemented by',
        '-- the ingestion writer (FE-P0c) and a cron fallback; not Postgres objects.',
        '-- Declaring them here resolves the open question at ADR-0005:158-162.',
        '',
    ]
    for view_name, policy in refresh_policies.items():
        lines.append(f'-- refresh_policies.{view_name}:')
        lines.append(f'--   trigger: {policy.get("trigger")}')
        lines.append(f'--   fallback_interval: {policy.get("fallback_interval")}')
        lines.append(f'--   concurrent_refresh: {policy.get("concurrent_refresh")}')
        rationale = policy.get('rationale')
        if rationale:
            for chunk in _wrap_comment(rationale):
                lines.append(f'--   rationale: {chunk}' if chunk == _wrap_comment(rationale)[0] else f'--             {chunk}')
        lines.append('')
    return lines


def _render_ingestion_contract(contract: Mapping) -> list[str]:
    lines: list[str] = [
        '-- Ingestion contract (FE-P0a). Atomicity/ordering rules the FE-P0c',
        '-- writer MUST satisfy. Documentation contract; enforced by',
        '-- tests/test_platform_central_db_schema_contract.py::TestFeP0aIngestionContract',
        '-- and by the application-level transaction in FE-P0c.',
        '',
    ]
    purpose = contract.get('purpose')
    if purpose:
        for chunk in _wrap_comment(purpose):
            lines.append(f'-- {chunk}')
        lines.append('')
    for rule in contract.get('rules') or []:
        rule_id = rule.get('id', '')
        lines.append(f'-- rule {rule_id}:')
        description = rule.get('description', '')
        if description:
            for chunk in _wrap_comment(description):
                lines.append(f'--   {chunk}')
        enforced_by = rule.get('enforced_by') or []
        if enforced_by:
            lines.append('--   enforced_by:')
            for item in enforced_by:
                for chunk in _wrap_comment(str(item)):
                    lines.append(f'--     - {chunk}' if chunk == _wrap_comment(str(item))[0] else f'--       {chunk}')
        lines.append('')
    return lines


def _render_local_central_field_mappings(mappings: Mapping) -> list[str]:
    lines: list[str] = [
        '-- Local SQLite ↔ central PostgreSQL field mappings (FE-P0a, Codex 2차 drift 위험 해소).',
        '-- Not a Postgres object — documentation contract enforced by',
        '-- tests/test_platform_central_db_schema_contract.py::TestFeP0aLocalCentralFieldMapping.',
        '',
    ]
    purpose = mappings.get('purpose')
    if purpose:
        for chunk in _wrap_comment(purpose):
            lines.append(f'-- {chunk}')
        lines.append('')
    for table_name, table_mapping in (mappings.get('tables') or {}).items():
        lines.append(f'-- {table_name}: {table_mapping.get("local_module", "")} -> {table_mapping.get("central_table", table_name)}')
        for entry in table_mapping.get('mappings') or []:
            local_col = entry.get('local_column', '')
            central_col = entry.get('central_column', '')
            note = entry.get('note', '')
            lines.append(f'--   {local_col} -> {central_col}')
            if note:
                for chunk in _wrap_comment(note):
                    lines.append(f'--     note: {chunk}' if chunk == _wrap_comment(note)[0] else f'--           {chunk}')
        lines.append('')
    return lines


def _render_rbac_role_grants(rbac: Mapping) -> list[str]:
    """Render the FE-P8 role catalog as idempotent seed INSERTs.

    Produces three blocks (permissions / roles / role_permissions) backed by
    natural-key ``ON CONFLICT DO NOTHING`` so re-running the migration is a
    no-op. Each surrogate uuid id is generated by ``gen_random_uuid()`` (pgcrypto,
    already enabled by the migration prologue). The role_permissions block uses
    INSERT...SELECT to resolve role_id/permission_id from natural keys without
    storing them in the schema JSON — keeping the SSOT a plain ``role_key ->
    [permission_key]`` mapping. Output ordering is deterministic (alphabetical by
    role / permission key) so the artifact stays byte-stable.
    """
    grants = rbac.get('grants') or {}
    global_catalog = rbac.get('global_role_grants') or {}
    global_grants = global_catalog.get('grants') or {}
    role_descriptions = {
        **(rbac.get('role_descriptions') or {}),
        **(global_catalog.get('role_descriptions') or {}),
    }
    permission_descriptions = rbac.get('permission_descriptions') or {}

    # Collect every permission referenced by any grant — single source of truth
    # for the permissions table seed (no separate permissions list to drift).
    permission_keys: set[str] = set()
    for role_permissions in list(grants.values()) + list(global_grants.values()):
        for permission_key in role_permissions:
            permission_keys.add(str(permission_key))

    lines: list[str] = [
        '-- RBAC role grants (FE-P8). Idempotent seed INSERTs derived from',
        '-- rbac_role_grants in central_db_schema.v1.json. The role catalog is data,',
        '-- not code — every role/permission consumed by project_member_permissions',
        '-- and PlatformApiAdapter.authorize flows from this single declaration.',
        '',
    ]
    purpose = rbac.get('purpose')
    if purpose:
        for chunk in _wrap_comment(purpose):
            lines.append(f'-- {chunk}')
        lines.append('')

    lines.append('-- permissions')
    for permission_key in sorted(permission_keys):
        description = permission_descriptions.get(permission_key, '')
        lines.append(
            'INSERT INTO "permissions" ("id", "permission_key", "description") '
            f'VALUES (gen_random_uuid(), {_quote_value(permission_key)}, {_quote_value(description)}) '
            'ON CONFLICT ("permission_key") DO NOTHING;'
        )
    lines.append('')

    lines.append('-- roles')
    for role_key in sorted(set(grants) | set(global_grants)):
        description = role_descriptions.get(role_key, '')
        lines.append(
            'INSERT INTO "roles" ("id", "role_key", "description") '
            f'VALUES (gen_random_uuid(), {_quote_value(role_key)}, {_quote_value(description)}) '
            'ON CONFLICT ("role_key") DO NOTHING;'
        )
    lines.append('')

    lines.append('-- role_permissions (role_id, permission_id resolved by natural-key JOIN)')
    for role_key in sorted(grants):
        for permission_key in sorted(grants[role_key]):
            lines.append(
                'INSERT INTO "role_permissions" ("role_id", "permission_id") '
                'SELECT r."id", p."id" FROM "roles" r, "permissions" p '
                f'WHERE r."role_key" = {_quote_value(role_key)} '
                f'AND p."permission_key" = {_quote_value(permission_key)} '
                'ON CONFLICT DO NOTHING;'
            )
    lines.append('')

    if global_grants:
        lines.append(
            '-- global_role_grants (global system roles, separate from project membership)'
        )
        for role_key in sorted(global_grants):
            for permission_key in sorted(global_grants[role_key]):
                lines.append(
                    'INSERT INTO "global_role_grants" ("role_key", "permission_key") '
                    f'VALUES ({_quote_value(role_key)}, {_quote_value(permission_key)}) '
                    'ON CONFLICT DO NOTHING;'
                )
                lines.append(
                    'INSERT INTO "role_permissions" ("role_id", "permission_id") '
                    'SELECT r."id", p."id" FROM "roles" r, "permissions" p '
                    f'WHERE r."role_key" = {_quote_value(role_key)} '
                    f'AND p."permission_key" = {_quote_value(permission_key)} '
                    'ON CONFLICT DO NOTHING;'
                )
    lines.append('')
    return lines


def _wrap_comment(text: str, width: int = 100) -> list[str]:
    text = ' '.join(str(text).split())
    if len(text) <= width:
        return [text]
    chunks: list[str] = []
    current = ''
    for word in text.split(' '):
        if current and len(current) + 1 + len(word) > width:
            chunks.append(current)
            current = word
        else:
            current = f'{current} {word}'.strip() if current else word
    if current:
        chunks.append(current)
    return chunks


def _quote_name(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Export central DB PostgreSQL DDL from schema JSON.')
    parser.add_argument('--schema', type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--write', action='store_true', help='write DDL to --output instead of stdout')
    parser.add_argument('--check', action='store_true', help='fail if --output differs from generated DDL')
    args = parser.parse_args(argv)

    ddl = render_ddl(load_schema(args.schema))
    output = args.output
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    if args.check:
        existing = output.read_text(encoding='utf-8') if output.exists() else ''
        if existing != ddl:
            print(f'{output} is out of date')
            return 1
        return 0
    if args.write:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(ddl, encoding='utf-8', newline='\n')
        return 0
    print(ddl, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
