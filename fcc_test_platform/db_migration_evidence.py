"""Validate central platform DB migration evidence against the schema SSOT.

The validator checks an evidence manifest captured from a deployed database.
It does not connect to databases, apply migrations, roll back migrations, or
write files.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
import re

from fcc_test_platform.evidence_primitives import is_sha256_hex


__all__ = [
    'DbMigrationEvidenceIssue',
    'central_db_migration_evidence_errors',
    'is_central_db_migration_evidence_valid',
]


EXPECTED_MIGRATION_ID = '001_initial_central_db'
EXPECTED_DATABASE_ENGINE = 'postgresql'

_POSTGRES_CAST_RE = re.compile(
    r'::\s*(?:(?:double\s+precision)|(?:character\s+varying)|'
    r'(?:(?:timestamp|time)\s+(?:with|without)\s+time\s+zone)|'
    r'(?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*'
    r'(?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_]*))?))'
    r'(?:\s*\[\s*\])*',
    re.IGNORECASE,
)
_SIMPLE_IDENTIFIER_RE = re.compile(r'^[a-z_][a-z0-9_]*$')


@dataclass(frozen=True)
class DbMigrationEvidenceIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'path': self.path,
            'message': self.message,
        }


def is_central_db_migration_evidence_valid(manifest: Mapping, schema: Mapping) -> bool:
    return not central_db_migration_evidence_errors(manifest, schema)


def central_db_migration_evidence_errors(
    manifest: Mapping,
    schema: Mapping,
) -> list[DbMigrationEvidenceIssue]:
    issues: list[DbMigrationEvidenceIssue] = []
    if manifest.get('schema_version') != 1:
        issues.append(_issue('invalid_schema_version', 'schema_version', 'schema_version must be 1'))
    if _text(manifest.get('migration_id')) != EXPECTED_MIGRATION_ID:
        issues.append(
            _issue(
                'invalid_migration_id',
                'migration_id',
                f'migration_id must be {EXPECTED_MIGRATION_ID}',
            )
        )
    if _text(manifest.get('database_engine')).lower() != EXPECTED_DATABASE_ENGINE:
        issues.append(
            _issue(
                'invalid_database_engine',
                'database_engine',
                f'database_engine must be {EXPECTED_DATABASE_ENGINE}',
            )
        )
    if _text(manifest.get('migration_status')).lower() != 'applied':
        issues.append(_issue('migration_not_applied', 'migration_status', 'migration_status must be applied'))

    _require_text(manifest, 'database_name', 'database_name', issues)
    _require_text(manifest, 'applied_at', 'applied_at', issues)
    _require_text(manifest, 'applied_by', 'applied_by', issues)
    _require_hash(manifest, 'schema_contract_sha256', 'schema_contract_sha256', issues)
    _require_hash(manifest, 'ddl_sha256', 'ddl_sha256', issues)

    schema_tables = _mapping(schema.get('tables'))
    if not schema_tables:
        issues.append(_issue('missing_schema_tables', 'schema.tables', 'schema tables are required'))
        return issues

    evidence_tables = _mapping(manifest.get('tables'))
    if not evidence_tables:
        issues.append(_issue('missing_evidence_tables', 'tables', 'tables evidence is required'))
        return issues

    for table_name, table_spec in schema_tables.items():
        table_path = f'tables.{table_name}'
        evidence_table = _mapping(evidence_tables.get(table_name))
        if not evidence_table:
            issues.append(_issue('missing_table', table_path, f'{table_name} table evidence is required'))
            continue
        _validate_table(
            table_name=str(table_name),
            schema_table=_mapping(table_spec),
            evidence_table=evidence_table,
            issues=issues,
        )

    extra_tables = set(evidence_tables) - set(schema_tables)
    for table_name in sorted(extra_tables):
        issues.append(_issue('unknown_table', f'tables.{table_name}', 'table is not in central DB schema SSOT'))

    return issues


def _validate_table(
    *,
    table_name: str,
    schema_table: Mapping,
    evidence_table: Mapping,
    issues: list[DbMigrationEvidenceIssue],
) -> None:
    columns = _columns_by_name(evidence_table.get('columns'))
    if not columns:
        issues.append(_issue('missing_columns', f'tables.{table_name}.columns', 'columns evidence is required'))
    for column_name, column_spec in _mapping(schema_table.get('columns')).items():
        column_path = f'tables.{table_name}.columns.{column_name}'
        evidence_column = columns.get(column_name)
        if not evidence_column:
            issues.append(_issue('missing_column', column_path, f'{column_name} column evidence is required'))
            continue
        required = bool(_mapping(column_spec).get('required'))
        if required and evidence_column.get('nullable') is True:
            issues.append(_issue('required_column_nullable', f'{column_path}.nullable', 'required column must be not nullable'))
        expected_type = _expected_evidence_type(str(_mapping(column_spec).get('type')))
        actual_type = _text(evidence_column.get('type')).lower()
        if expected_type and expected_type not in actual_type:
            issues.append(
                _issue(
                    'column_type_mismatch',
                    f'{column_path}.type',
                    f'expected type containing {expected_type}',
                )
            )

    indexes = _indexes_by_name(evidence_table.get('indexes'))
    for index in schema_table.get('indexes') or []:
        index_name = _text(_mapping(index).get('name'))
        if not index_name:
            continue
        index_path = f'tables.{table_name}.indexes.{index_name}'
        evidence_index = indexes.get(index_name)
        if not evidence_index:
            issues.append(_issue('missing_index', index_path, f'{index_name} index evidence is required'))
            continue
        expected_columns = tuple(_mapping(index).get('columns') or ())
        actual_columns = tuple(evidence_index.get('columns') or ())
        if expected_columns and actual_columns != expected_columns:
            issues.append(_issue('index_columns_mismatch', f'{index_path}.columns', 'index columns must match schema'))
        expected_unique = bool(_mapping(index).get('unique'))
        actual_unique = bool(evidence_index.get('unique'))
        if expected_unique != actual_unique:
            issues.append(_issue('index_unique_mismatch', f'{index_path}.unique', 'index unique flag must match schema'))
        expected_orders = _normalise_orders(_mapping(index).get('orders'))
        actual_orders = _normalise_orders(_mapping(evidence_index).get('orders'))
        if expected_orders != actual_orders:
            issues.append(_issue('index_orders_mismatch', f'{index_path}.orders', 'index order/null semantics must match schema'))
        expected_where = _normalise_predicate(_mapping(index).get('where'))
        actual_where = _normalise_predicate(_mapping(evidence_index).get('where'))
        if expected_where != actual_where:
            issues.append(_issue('index_predicate_mismatch', f'{index_path}.where', 'partial index predicate must match schema'))


def _columns_by_name(value) -> dict[str, Mapping]:
    if isinstance(value, Mapping):
        return {str(name): _mapping(spec) for name, spec in value.items()}
    if not isinstance(value, list):
        return {}
    columns: dict[str, Mapping] = {}
    for raw in value:
        column = _mapping(raw)
        name = _text(column.get('name'))
        if name:
            columns[name] = column
    return columns


def _indexes_by_name(value) -> dict[str, Mapping]:
    if isinstance(value, Mapping):
        return {str(name): _mapping(spec) for name, spec in value.items()}
    if not isinstance(value, list):
        return {}
    indexes: dict[str, Mapping] = {}
    for raw in value:
        index = _mapping(raw)
        name = _text(index.get('name'))
        if name:
            indexes[name] = index
    return indexes


def _expected_evidence_type(schema_type: str) -> str:
    return {
        'uuid': 'uuid',
        'text': 'text',
        'json': 'json',
        'boolean': 'boolean',
        'timestamp': 'timestamp',
        'integer': 'integer',
    }.get(schema_type, '')


def _require_text(
    mapping: Mapping,
    key: str,
    path: str,
    issues: list[DbMigrationEvidenceIssue],
) -> None:
    if not _text(mapping.get(key)):
        issues.append(_issue('missing_required_field', path, f'{key} is required'))


def _require_hash(
    mapping: Mapping,
    key: str,
    path: str,
    issues: list[DbMigrationEvidenceIssue],
) -> None:
    value = _text(mapping.get(key))
    if not value:
        issues.append(_issue('missing_required_field', path, f'{key} is required'))
    elif not is_sha256_hex(value):
        issues.append(_issue('invalid_sha256', path, f'{key} must be a SHA-256 hex digest'))


def _mapping(value) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _normalise_orders(value) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        _normalise_order_key(key): _normalise_order_value(raw)
        for key, raw in value.items()
        if _text(raw)
    }


def _normalise_predicate(value) -> str | None:
    if not _text(value):
        return None
    result = _strip_postgresql_casts(_text(value))
    result = _normalise_simple_identifiers(result)
    result = _collapse_sql_whitespace(result).strip().rstrip(';').strip()
    return _normalise_boolean_expression(result)[0]


def _normalise_order_key(value) -> str:
    text = _text(value)
    if len(text) >= 2 and text[0] == text[-1] == '"':
        candidate = text[1:-1].replace('""', '"')
        if _SIMPLE_IDENTIFIER_RE.fullmatch(candidate) and candidate == candidate.lower():
            return candidate
    return text


def _normalise_order_value(value) -> str:
    return ' '.join(_text(value).upper().split())


def _strip_postgresql_casts(value: str) -> str:
    parts: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char in "'\"":
            end = _quoted_end(value, index, char)
            parts.append(value[index:end])
            index = end
            continue
        if value.startswith('::', index):
            match = _POSTGRES_CAST_RE.match(value, index)
            if match:
                index = match.end()
                continue
        parts.append(char)
        index += 1
    return ''.join(parts)


def _normalise_simple_identifiers(value: str) -> str:
    parts: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "'":
            end = _quoted_end(value, index, char)
            parts.append(value[index:end])
            index = end
            continue
        if char == '"':
            end = _quoted_end(value, index, char)
            quoted = value[index:end]
            candidate = quoted[1:-1].replace('""', '"') if quoted.endswith('"') else ''
            if (
                _SIMPLE_IDENTIFIER_RE.fullmatch(candidate)
                and candidate == candidate.lower()
            ):
                parts.append(candidate)
            else:
                parts.append(quoted)
            index = end
            continue
        parts.append(char)
        index += 1
    return ''.join(parts)


def _collapse_sql_whitespace(value: str) -> str:
    parts: list[str] = []
    index = 0
    pending_space = False
    while index < len(value):
        char = value[index]
        if char in "'\"":
            if pending_space and parts:
                parts.append(' ')
            pending_space = False
            end = _quoted_end(value, index, char)
            parts.append(value[index:end])
            index = end
            continue
        if char.isspace():
            pending_space = True
            index += 1
            continue
        if pending_space and parts:
            parts.append(' ')
        pending_space = False
        parts.append(char)
        index += 1
    return ''.join(parts)


def _normalise_boolean_expression(value: str) -> tuple[str, int]:
    text = value.strip()
    inner = _fully_wrapped_predicate(text)
    if inner is not None:
        return _normalise_boolean_expression(inner)

    for operator, precedence in (('OR', 1), ('AND', 2)):
        parts = _split_top_level_boolean(text, operator)
        if len(parts) <= 1:
            continue
        children = [_normalise_boolean_expression(part) for part in parts]
        rendered: list[str] = []
        for child, child_precedence in children:
            if child_precedence < precedence:
                rendered.append(f'({child})')
            else:
                rendered.append(child)
        return f' {operator} '.join(rendered), precedence
    return text, 3


def _split_top_level_boolean(value: str, operator: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    index = 0
    between_pending = False
    operator_length = len(operator)
    while index < len(value):
        char = value[index]
        if char in "'\"":
            index = _quoted_end(value, index, char)
            continue
        if char == '(':
            depth += 1
            index += 1
            continue
        if char == ')':
            depth = max(0, depth - 1)
            index += 1
            continue
        if depth == 0 and value[index:index + operator_length].upper() == operator:
            before = value[index - 1] if index else ''
            after_index = index + operator_length
            after = value[after_index] if after_index < len(value) else ''
            if not _is_sql_word_char(before) and not _is_sql_word_char(after):
                if operator == 'AND' and between_pending:
                    between_pending = False
                    index = after_index
                    continue
                parts.append(value[start:index].strip())
                start = after_index
                index = after_index
                continue
        if depth == 0 and (char.isalpha() or char == '_'):
            word_match = re.match(r'[A-Za-z_][A-Za-z0-9_]*', value[index:])
            if word_match:
                word = word_match.group(0).upper()
                if word == 'BETWEEN':
                    between_pending = True
                index += len(word_match.group(0))
                continue
        index += 1
    if not parts:
        return [value]
    parts.append(value[start:].strip())
    return parts


def _fully_wrapped_predicate(value: str) -> str | None:
    if not value.startswith('('):
        return None
    inner = _balanced_predicate_group(value, 0)
    if inner is None:
        return None
    opening_length = len(value) - len(value.lstrip())
    if opening_length or not value.endswith(')'):
        return None
    return inner.strip()


def _balanced_predicate_group(value: str, start: int) -> str | None:
    if start >= len(value) or value[start] != '(':
        return None
    depth = 1
    index = start + 1
    while index < len(value):
        char = value[index]
        if char in "'\"":
            index = _quoted_end(value, index, char)
            continue
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 0:
                if value[index + 1:].strip():
                    return None
                return value[start + 1:index]
        index += 1
    return None


def _quoted_end(value: str, start: int, quote: str) -> int:
    index = start + 1
    while index < len(value):
        char = value[index]
        if char == quote:
            if index + 1 < len(value) and value[index + 1] == quote:
                index += 2
                continue
            return index + 1
        if quote == "'" and char == '\\' and index + 1 < len(value):
            index += 2
            continue
        index += 1
    return len(value)


def _is_sql_word_char(value: str) -> bool:
    return bool(value) and (value.isalnum() or value == '_')


def _issue(code: str, path: str, message: str) -> DbMigrationEvidenceIssue:
    return DbMigrationEvidenceIssue(code=code, path=path, message=message)
