"""Validate platform/contract extraction evidence metadata."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from fcc_test_platform.evidence_primitives import is_sha256_hex


__all__ = [
    'EXTRACTION_REQUIRED_REPOSITORIES',
    'ExtractionEvidenceIssue',
    'extraction_package_errors',
    'extraction_target_lanes',
    'is_extraction_package_valid',
]


#: Which lanes the extraction runner packages, when no manifest is supplied.
#:
#: Kept only as the no-manifest fallback. The authority is the manifest, which
#: declares ``extraction_target`` per lane — because "is this lane extracted"
#: is an ownership decision, and encoding it as a Python tuple made every new
#: lane a code change. That is what kept the chamber node runtime waiting: the
#: policy already handled N lanes and this constant only ever named two.
EXTRACTION_REQUIRED_REPOSITORIES = (
    'fcc-test-contracts',
    'fcc-test-platform',
)


def extraction_target_lanes(extraction_manifest: Mapping | None) -> tuple[str, ...]:
    """Lanes the manifest declares as extraction targets, in manifest order.

    A lane is a target only when it says so. Two lanes deliberately say no and
    for unrelated reasons, which is why this cannot be derived from anything
    else in the document: ``fcc-unlicensed-headless`` has relocation entries but
    stays private under ADR-0018 D-5, and ``fcc-chamber-node`` is a shared lane
    that owns source but has not been scheduled to move yet. Inferring from
    "has entries" would extract the first; inferring from "is shared" would
    extract the second before anyone decided how.
    """
    repositories = _mapping((extraction_manifest or {}).get('repositories'))
    if not repositories:
        return EXTRACTION_REQUIRED_REPOSITORIES
    declared = tuple(
        name for name, spec in repositories.items()
        if _mapping(spec).get('extraction_target') is True
    )
    # A manifest that declares none is a manifest predating the field, not a
    # decision to extract nothing — falling through to the constant keeps an
    # older document working rather than silently packaging zero lanes.
    return declared or tuple(
        repo for repo in EXTRACTION_REQUIRED_REPOSITORIES if repo in repositories
    )


@dataclass(frozen=True)
class ExtractionEvidenceIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'path': self.path,
            'message': self.message,
        }


def is_extraction_package_valid(
    manifest: Mapping,
    *,
    extraction_manifest: Mapping | None = None,
) -> bool:
    return not extraction_package_errors(manifest, extraction_manifest=extraction_manifest)


def extraction_package_errors(
    manifest: Mapping,
    *,
    extraction_manifest: Mapping | None = None,
) -> list[ExtractionEvidenceIssue]:
    issues: list[ExtractionEvidenceIssue] = []
    if manifest.get('schema_version') != 1:
        issues.append(_issue('invalid_schema_version', 'schema_version', 'schema_version must be 1'))
    for key in ('evidence_id', 'collected_at', 'manifest_path'):
        _require_text(manifest, key, key, issues)
    if manifest.get('compatible') is not True:
        issues.append(_issue('not_compatible', 'compatible', 'compatible must be true'))
    raw_issues = manifest.get('issues')
    if not isinstance(raw_issues, list):
        issues.append(_issue('missing_issues', 'issues', 'issues must be a list'))
    elif raw_issues:
        issues.append(_issue('has_issues', 'issues', 'issues must be empty'))

    repositories = _mapping(manifest.get('repositories'))
    if not repositories:
        issues.append(_issue('missing_repositories', 'repositories', 'repositories are required'))
        return issues
    expected_repos = _expected_repositories(extraction_manifest)
    for repo_name in expected_repos:
        repo = _mapping(repositories.get(repo_name))
        if not repo:
            issues.append(_issue('missing_repository', f'repositories.{repo_name}', f'{repo_name} evidence is required'))
            continue
        _validate_repository(repo_name, repo, issues, extraction_manifest=extraction_manifest)

    extra = set(repositories) - set(expected_repos)
    for repo_name in sorted(extra):
        issues.append(_issue('unknown_repository', f'repositories.{repo_name}', 'unknown extraction repository'))
    return issues


def _validate_repository(
    repo_name: str,
    repo: Mapping,
    issues: list[ExtractionEvidenceIssue],
    *,
    extraction_manifest: Mapping | None,
) -> None:
    base_path = f'repositories.{repo_name}'
    if _text(repo.get('target_repository')) != repo_name:
        issues.append(_issue('target_repository_mismatch', f'{base_path}.target_repository', 'target_repository must match repository key'))
    for key in ('target_ref', 'extracted_at'):
        _require_text(repo, key, f'{base_path}.{key}', issues)
    if repo.get('package_compatible') is not True:
        issues.append(_issue('package_not_compatible', f'{base_path}.package_compatible', 'package_compatible must be true'))
    if repo.get('extracted') is not True:
        issues.append(_issue('not_extracted', f'{base_path}.extracted', 'extracted must be true'))

    entries = repo.get('entries')
    if not isinstance(entries, list):
        issues.append(_issue('missing_entries', f'{base_path}.entries', 'entries must be a list'))
        return
    if not entries:
        issues.append(_issue('empty_entries', f'{base_path}.entries', 'entries must not be empty'))
        return

    expected_entries = _expected_entries(repo_name, extraction_manifest)
    seen_futures: set[str] = set()
    for index, raw in enumerate(entries):
        entry_path = f'{base_path}.entries[{index}]'
        entry = _mapping(raw)
        if not entry:
            issues.append(_issue('invalid_entry', entry_path, 'entry must be an object'))
            continue
        current_path = _text(entry.get('current_path'))
        future_path = _text(entry.get('future_path'))
        seen_futures.add(future_path)
        for key in ('current_path', 'future_path', 'kind', 'source_sha256', 'destination_sha256'):
            _require_text(entry, key, f'{entry_path}.{key}', issues)
        if _int(entry.get('byte_size')) is None or _int(entry.get('byte_size')) <= 0:
            issues.append(_issue('invalid_byte_size', f'{entry_path}.byte_size', 'byte_size must be positive'))
        if entry.get('copied') is not True:
            issues.append(_issue('entry_not_copied', f'{entry_path}.copied', 'copied must be true'))
        source_sha256 = _text(entry.get('source_sha256'))
        destination_sha256 = _text(entry.get('destination_sha256'))
        if source_sha256 and not is_sha256_hex(source_sha256):
            issues.append(_issue('invalid_sha256', f'{entry_path}.source_sha256', 'source_sha256 must be 64 lowercase hex characters'))
        if destination_sha256 and not is_sha256_hex(destination_sha256):
            issues.append(_issue('invalid_sha256', f'{entry_path}.destination_sha256', 'destination_sha256 must be 64 lowercase hex characters'))
        if source_sha256 != destination_sha256 and not _has_declared_transform(entry):
            issues.append(_issue(
                'hash_mismatch',
                f'{entry_path}.destination_sha256',
                'destination_sha256 must match source_sha256 unless a supported transform is declared',
            ))
        _validate_transforms(entry, entry_path, issues)
        if future_path and _unsafe_relative_path(future_path):
            issues.append(_issue('unsafe_future_path', f'{entry_path}.future_path', 'future_path must be safe relative path'))
        declared_future, expected = _resolve_expected(future_path, expected_entries)
        if expected:
            declared_current = _text(expected.get('current_path'))
            if not _path_agrees(current_path, declared_current, declared_future):
                issues.append(_issue('current_path_mismatch', f'{entry_path}.current_path', 'current_path does not match extraction manifest'))
            if expected.get('kind') != _text(entry.get('kind')):
                issues.append(_issue('kind_mismatch', f'{entry_path}.kind', 'kind does not match extraction manifest'))

    missing = sorted(
        declared for declared in expected_entries
        if not _is_witnessed(declared, seen_futures)
    )
    for future_path in missing:
        issues.append(_issue('missing_manifest_entry', f'{base_path}.entries', f'{future_path} evidence is required'))


def _expected_repositories(extraction_manifest: Mapping | None) -> tuple[str, ...]:
    return extraction_target_lanes(extraction_manifest)


def _expected_entries(repo_name: str, extraction_manifest: Mapping | None) -> dict[str, Mapping]:
    repo = _mapping(_mapping((extraction_manifest or {}).get('repositories')).get(repo_name))
    entries = repo.get('entries')
    if not isinstance(entries, list):
        return {}
    return {
        _text(entry.get('future_path')): entry
        for entry in entries
        if isinstance(entry, Mapping) and _text(entry.get('future_path'))
    }


def _resolve_expected(
    future_path: str, expected_entries: dict[str, Mapping]
) -> tuple[str, Mapping | None]:
    """Find the manifest row a piece of evidence answers to.

    A **directory** relocation declares a prefix and is answered by evidence for
    each file beneath it, so an exact-key lookup finds nothing and the
    cross-checks below stop firing — evidence for an expanded tree would agree
    with nothing at all. Longest declared prefix wins, so a file that also has
    its own row is checked against that row rather than the tree containing it.
    """
    exact = expected_entries.get(future_path)
    if exact is not None:
        return future_path, exact
    best_key = ''
    for declared in expected_entries:
        if declared.endswith('/') and future_path.startswith(declared):
            if len(declared) > len(best_key):
                best_key = declared
    return (best_key, expected_entries[best_key]) if best_key else ('', None)


def _path_agrees(current_path: str, declared_current: str, declared_future: str) -> bool:
    """Whether an evidence ``current_path`` matches what the manifest declared.

    Exact for a file row; for a directory row the evidence names a file *inside*
    the declared tree, and the two sides must place it at the same depth — a
    tree that lands its files somewhere other than the declared future prefix is
    the mistake this check exists to catch.
    """
    if not declared_future.endswith('/'):
        return declared_current == current_path
    return current_path.startswith(declared_current)


def _is_witnessed(declared_future: str, seen_futures: set[str]) -> bool:
    if declared_future in seen_futures:
        return True
    if not declared_future.endswith('/'):
        return False
    return any(seen.startswith(declared_future) for seen in seen_futures)


def _unsafe_relative_path(value: str) -> bool:
    text = value.strip().replace('\\', '/')
    if not text:
        return True
    return text.startswith('/') or ':' in text.split('/')[0] or '..' in text.split('/')


def _has_declared_transform(entry: Mapping) -> bool:
    transforms = entry.get('transforms')
    if not isinstance(transforms, list) or not transforms:
        return False
    return any(
        isinstance(transform, Mapping)
        and transform.get('type') == 'python_import_rewrite'
        and _int(transform.get('count')) is not None
        and _int(transform.get('count')) > 0
        for transform in transforms
    )


def _validate_transforms(
    entry: Mapping,
    entry_path: str,
    issues: list[ExtractionEvidenceIssue],
) -> None:
    transforms = entry.get('transforms')
    if transforms is None:
        return
    if not isinstance(transforms, list):
        issues.append(_issue('invalid_transforms', f'{entry_path}.transforms', 'transforms must be a list'))
        return
    for index, raw in enumerate(transforms):
        transform = _mapping(raw)
        transform_path = f'{entry_path}.transforms[{index}]'
        if not transform:
            issues.append(_issue('invalid_transform', transform_path, 'transform must be an object'))
            continue
        if _text(transform.get('type')) != 'python_import_rewrite':
            issues.append(_issue('unknown_transform', f'{transform_path}.type', 'unsupported extraction transform'))
        if _int(transform.get('count')) is None or _int(transform.get('count')) <= 0:
            issues.append(_issue('invalid_transform_count', f'{transform_path}.count', 'transform count must be positive'))


def _require_text(mapping: Mapping, key: str, path: str, issues: list[ExtractionEvidenceIssue]) -> None:
    if not _text(mapping.get(key)):
        issues.append(_issue('missing_required_field', path, f'{key} is required'))


def _mapping(value) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _issue(code: str, path: str, message: str) -> ExtractionEvidenceIssue:
    return ExtractionEvidenceIssue(code=code, path=path, message=message)
