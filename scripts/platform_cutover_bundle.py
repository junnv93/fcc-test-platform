"""Assemble and validate a platform cutover readiness bundle from evidence manifests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
for path in (PROJECT_ROOT, SRC_ROOT):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from fcc_test_platform.cutover_readiness import cutover_readiness_errors
from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact
from fcc_test_platform.application.platform_cutover_catalog import (
    catalog_cli_arguments,
    catalog_entries,
    catalog_filenames,
    catalog_keys,
)
from fcc_test_platform.application.platform_cutover_context import build_context_read_issue
from scripts.platform_cutover_workflow_hints import (
    attach_issue_hints,
    load_workflow_hints,
    next_commands,
)


EVIDENCE_ARGUMENTS = tuple(
    (entry.key, catalog_cli_arguments()[entry.key]) for entry in catalog_entries()
)
EVIDENCE_FILENAMES = catalog_filenames()
DEFAULT_CATALOG_OUTPUT = resolve_repo_artifact(
    __file__, 'docs/platform/cutover_readiness_evidence.schema.v1.json'
)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'export-catalog':
        return _export_catalog_main(argv[1:])

    parser = argparse.ArgumentParser(description='Assemble platform cutover evidence bundle.')
    parser.add_argument('--provider-id', required=True)
    parser.add_argument('--cutover-candidate-id', required=True)
    parser.add_argument('--evaluated-at', required=True)
    parser.add_argument('--collected-at', required=True)
    parser.add_argument('--central-db-schema', default='')
    parser.add_argument('--extraction-manifest', default='')
    parser.add_argument('--evidence-root', default='')
    parser.add_argument('--workflow-config', default='')
    parser.add_argument('--output', default='')
    parser.add_argument('--next-commands-output', default='')
    for _, option in EVIDENCE_ARGUMENTS:
        parser.add_argument(option, default='')
    args = parser.parse_args(argv)

    paths = _resolve_paths(args)
    workflow_issues: list[dict] = []
    workflow_hints = load_workflow_hints(
        Path(args.workflow_config) if args.workflow_config else None,
        EVIDENCE_FILENAMES,
        workflow_issues,
    )
    missing_paths = _missing_paths(paths, args)
    if missing_paths:
        issues = attach_issue_hints(workflow_issues + [
            {
                'code': 'missing_evidence_file',
                'path': str(path),
                'message': f'{key} evidence file is missing',
                'evidence_key': key,
            }
            for key, path in missing_paths
        ], workflow_hints)
        payload = {
            'ready': False,
            'issues': issues,
            'diagnostics': _diagnostics(issues, workflow_hints),
            'expected_files': _expected_files(args.evidence_root),
            'workflow_hints': workflow_hints,
            'next_commands': next_commands(issues, workflow_hints, EVIDENCE_FILENAMES, evidence_root=args.evidence_root),
        }
        _write_next_commands(args.next_commands_output, payload['next_commands'])
        print(json.dumps(payload, sort_keys=True, indent=2))
        return 2
    manifests, read_issues = _read_manifests(paths)
    if read_issues:
        issues = attach_issue_hints(workflow_issues + read_issues, workflow_hints)
        payload = {
            'ready': False,
            'issues': issues,
            'diagnostics': _diagnostics(issues, workflow_hints),
            'workflow_hints': workflow_hints,
            'next_commands': next_commands(issues, workflow_hints, EVIDENCE_FILENAMES, evidence_root=args.evidence_root),
        }
        _write_next_commands(args.next_commands_output, payload['next_commands'])
        print(json.dumps(payload, sort_keys=True, indent=2))
        return 2
    context_issues: list[dict] = []
    central_db_schema = _read_context(
        args.central_db_schema,
        'central_db_schema',
        context_issues,
    )
    extraction_manifest = _read_context(
        args.extraction_manifest,
        'extraction_manifest',
        context_issues,
    )
    if context_issues:
        issues = attach_issue_hints(workflow_issues + context_issues, workflow_hints)
        payload = {
            'ready': False,
            'issues': issues,
            'diagnostics': _diagnostics(issues, workflow_hints),
            'workflow_hints': workflow_hints,
            'next_commands': next_commands(issues, workflow_hints, EVIDENCE_FILENAMES, evidence_root=args.evidence_root),
        }
        _write_next_commands(args.next_commands_output, payload['next_commands'])
        print(json.dumps(payload, sort_keys=True, indent=2))
        return 2

    bundle = _bundle(args.provider_id, args.cutover_candidate_id, args.evaluated_at, args.collected_at, manifests, paths)
    issues = attach_issue_hints(workflow_issues + [
        issue.to_dict()
        for issue in cutover_readiness_errors(
            bundle,
            central_db_schema=central_db_schema,
            extraction_manifest=extraction_manifest,
        )
    ], workflow_hints)
    payload = {
        'ready': not issues,
        'issues': issues,
        'diagnostics': _diagnostics(issues, workflow_hints),
        'workflow_hints': workflow_hints,
        'next_commands': next_commands(issues, workflow_hints, EVIDENCE_FILENAMES, evidence_root=args.evidence_root),
        'bundle': bundle,
    }
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(bundle, sort_keys=True, indent=2) + '\n', encoding='utf-8')
        payload['output'] = str(output_path)
    _write_next_commands(args.next_commands_output, payload['next_commands'])
    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0 if not issues else 1


def load_document(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('cutover readiness contract must be a JSON object')
    return value


def _read_context(path_value: str, context_field: str, issues: list[dict]) -> Mapping | None:
    if not path_value:
        return None
    try:
        return _read_json(Path(path_value))
    except Exception:
        issues.append(build_context_read_issue(context_field))
        return None


def render_document(document: Mapping) -> dict:
    """Return ``document`` with every catalog-owned field derived afresh."""
    entries = [
        {
            'key': entry.key,
            'canonical_filename': entry.canonical_filename,
            'cli_argument': entry.cli_argument,
            'validator': entry.validator_name,
            'required_contexts': list(entry.required_contexts),
            'completion_group': entry.completion_group,
        }
        for entry in catalog_entries()
    ]
    by_key = {entry['key']: entry for entry in entries}
    groups: dict[str, list[str]] = {}
    for entry in entries:
        groups.setdefault(entry['completion_group'], []).append(entry['key'])
    rendered = dict(document)
    rendered['required_evidence_keys'] = list(catalog_keys())
    rendered['manifest_required_evidence_keys'] = list(catalog_keys())
    rendered['nested_manifest_validators'] = {
        entry['key']: entry['validator']
        for entry in entries
    }
    rendered['evidence_catalog'] = entries
    rendered['canonical_filenames'] = {
        key: by_key[key]['canonical_filename']
        for key in catalog_keys()
    }
    rendered['cli_arguments'] = {
        key: by_key[key]['cli_argument']
        for key in catalog_keys()
    }
    rendered['required_validation_contexts'] = {
        key: by_key[key]['required_contexts']
        for key in catalog_keys()
    }
    rendered['completion_groups'] = groups
    rendered['receipt_policy'] = {
        'metadata_suffix': '.receipt.json',
        'resume_requires_matching_output_size_and_sha256': True,
        'resume_requires_current_catalog_validator_pass': True,
        'resume_metadata_is_not_evidence': True,
        'receipts_never_satisfy_required_evidence_keys': True,
    }
    return rendered


def render_text(path: Path = DEFAULT_CATALOG_OUTPUT) -> str:
    return json.dumps(render_document(load_document(path)), sort_keys=True, indent=2) + '\n'


def _export_catalog_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description='Export FCC cutover evidence catalog contract sections.')
    parser.add_argument('--schema', type=Path, default=DEFAULT_CATALOG_OUTPUT, help='source contract artifact')
    parser.add_argument('--output', type=Path, default=DEFAULT_CATALOG_OUTPUT)
    parser.add_argument('--write', action='store_true', help='write the derived contract to --output')
    parser.add_argument('--check', action='store_true', help='fail if --output differs from the derived contract')
    args = parser.parse_args(argv)

    source = args.schema if args.schema.is_absolute() else resolve_repo_artifact(__file__, str(args.schema))
    output = args.output if args.output.is_absolute() else resolve_repo_artifact(__file__, str(args.output))
    rendered = render_text(source)
    if args.check:
        existing = output.read_text(encoding='utf-8') if output.exists() else ''
        if existing != rendered:
            print(f'{output} is out of date')
            return 1
        return 0
    if args.write:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding='utf-8', newline='\n')
        return 0
    print(rendered, end='')
    return 0


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _write_next_commands(output: str, next_commands: list[dict]) -> None:
    if not output:
        return
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(next_commands, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def _read_manifests(paths: Mapping[str, Path]) -> tuple[dict[str, dict], list[dict]]:
    manifests: dict[str, dict] = {}
    issues: list[dict] = []
    for key, path in paths.items():
        try:
            manifests[key] = _read_json(path)
        except Exception as exc:
            issues.append({
                'code': 'read_error',
                'path': str(path),
                'message': str(exc),
                'evidence_key': key,
            })
    return manifests, issues


def _resolve_paths(args: argparse.Namespace) -> dict[str, Path]:
    root = Path(args.evidence_root) if args.evidence_root else None
    paths: dict[str, Path] = {}
    for key, _ in EVIDENCE_ARGUMENTS:
        explicit = str(getattr(args, key) or '').strip()
        if explicit:
            paths[key] = Path(explicit)
        elif root is not None:
            paths[key] = root / EVIDENCE_FILENAMES[key]
        else:
            paths[key] = Path('')
    return paths


def _missing_paths(paths: dict[str, Path], args: argparse.Namespace) -> list[tuple[str, Path]]:
    missing: list[tuple[str, Path]] = []
    for key, path in paths.items():
        explicit = str(getattr(args, key) or '').strip()
        if not explicit and (not str(path) or not path.is_file()):
            missing.append((key, path))
    return missing


def _expected_files(evidence_root: str) -> dict[str, str]:
    root = Path(evidence_root) if evidence_root else Path('<evidence-root>')
    return {key: str(root / filename) for key, filename in EVIDENCE_FILENAMES.items()}


def _diagnostics(issues: list[dict], workflow_hints: Mapping[str, dict] | None = None) -> dict:
    by_code: dict[str, int] = {}
    by_evidence_key: dict[str, int] = {}
    missing: list[str] = []
    invalid: list[str] = []
    context: list[str] = []
    workflow: list[str] = []
    for issue in issues:
        code = str(issue.get('code') or '')
        key = str(issue.get('evidence_key') or '')
        by_code[code] = by_code.get(code, 0) + 1
        if key:
            by_evidence_key[key] = by_evidence_key.get(key, 0) + 1
        if code in {'missing_evidence_file', 'missing_required_evidence', 'missing_manifest'}:
            if key:
                missing.append(key)
        elif code in {'missing_central_db_schema', 'context_read_error'}:
            context.append(code)
        elif code.startswith('workflow_config_'):
            workflow.append(code)
        else:
            if key:
                invalid.append(key)
    return {
        'production_blocked': bool(issues),
        'issue_count': len(issues),
        'by_code': by_code,
        'by_evidence_key': by_evidence_key,
        'missing_evidence_keys': sorted(set(missing)),
        'invalid_evidence_keys': sorted(set(invalid)),
        'context_issues': sorted(set(context)),
        'workflow_issue_codes': sorted(set(workflow)),
        'next_action': _next_action(missing, invalid, context, workflow),
        'workflow_hint_keys': sorted(set(workflow_hints or {})),
    }


def _next_action(missing: list[str], invalid: list[str], context: list[str], workflow: list[str] | None = None) -> str:
    if missing:
        return 'collect_missing_required_evidence'
    if context:
        return 'provide_required_validation_context'
    if workflow:
        return 'repair_workflow_config'
    if invalid:
        return 'repair_invalid_evidence_manifests'
    return 'ready_for_cutover'


def _bundle(
    provider_id: str,
    cutover_candidate_id: str,
    evaluated_at: str,
    collected_at: str,
    manifests: dict[str, dict],
    paths: dict[str, Path],
) -> dict:
    return {
        'schema_version': 1,
        'provider_id': provider_id,
        'cutover_candidate_id': cutover_candidate_id,
        'evaluated_at': evaluated_at,
        'evidence': {
            key: {
                'evidence_id': _manifest_text(manifests[key], 'evidence_id', _evidence_id(key, paths[key])),
                'collected_at': _manifest_text(manifests[key], 'collected_at', collected_at),
                'validated': _manifest_validated(manifests[key]),
                'issues': _manifest_issues(manifests[key]),
                'source_path': str(paths[key]),
                'manifest': manifests[key],
            }
            for key, _ in EVIDENCE_ARGUMENTS
        },
    }


def _manifest_text(manifest: Mapping, key: str, fallback: str) -> str:
    value = manifest.get(key) if isinstance(manifest, Mapping) else ''
    text = str(value or '').strip()
    return text if text else fallback


def _manifest_validated(manifest: Mapping) -> bool:
    """Honor a manifest's self-reported validation status (no silent override)."""
    if isinstance(manifest, Mapping) and 'validated' in manifest:
        return bool(manifest.get('validated'))
    return True


def _manifest_issues(manifest: Mapping) -> list:
    if isinstance(manifest, Mapping) and isinstance(manifest.get('issues'), list):
        return list(manifest['issues'])
    return []


def _evidence_id(key: str, path: Path) -> str:
    stem = str(path.stem or '').strip()
    return stem if stem else f'{key}-evidence'


if __name__ == '__main__':
    raise SystemExit(main())
