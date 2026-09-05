"""Executable completion audit guard for Unlicensed platform cutover.

⚠️ 이것은 `scripts/platform_cutover_completion_audit.py` 의 **알맹이**다. `scripts/` 는 패키지가 아니라
**휠이 나르지 못한다** — 이 레인을 핀으로 받는 소비자에게 그 파일은 오지 않는다.
그래서 로직은 여기 살고 `scripts/` 에는 진입점만 남는다. 껍데기는 양쪽 레포에
두되, 담긴 것이 그뿐이라 **갈라질 것이 없다.**
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping


from fcc_test_platform.cutover_readiness import cutover_readiness_errors
from fcc_test_platform.application.platform_cutover_catalog import (
    catalog_completion_groups,
    catalog_entries,
    catalog_keys,
)
from fcc_test_platform.application.platform_cutover_context import build_context_read_issue
from fcc_test_platform.cutover_bundle_cli import EVIDENCE_FILENAMES
from fcc_test_platform.cutover_workflow_hints import (
    attach_issue_hints,
    load_workflow_hints,
    next_commands,
    workflow_hints_from_config,
)


_REQUIREMENT_METADATA = (
    {
        'id': 'live_postgresql_ingestion_execution',
        'completion_group': 'live_postgresql_ingestion_execution',
        'description': 'Live PostgreSQL ingestion execution evidence runner produced committed transaction evidence.',
        'next_action': 'run scripts/platform_ingestion_execution_evidence.py execute against live PostgreSQL',
    },
    {
        'id': 'db_only_report_reconstruction',
        'completion_group': 'db_only_report_reconstruction',
        'description': 'DB-only report reconstruction evidence proves central DB plus artifact metadata report generation without Excel source.',
        'next_action': 'run scripts/unlicensed_report_reconstruction_evidence.py execute against central DB snapshot or live DSN',
    },
    {
        'id': 'deployment_idp_frontend_browser_qa',
        'completion_group': 'deployment_idp_frontend_browser_qa',
        'description': 'Provider service, IdP deployment, frontend deployment, and browser QA evidence are collected into the cutover root.',
        'next_action': 'run collectors and scripts/platform_deployment_evidence_workflow.py',
    },
    {
        'id': 'live_lab_and_platform_operations',
        'completion_group': 'live_lab_and_platform_operations',
        'description': 'Real lab/platform operation evidence exists for hardware, DB migration, artifact sync, backup/restore, RBAC, extraction, and performance.',
        'next_action': 'run the remaining live lab/platform evidence collectors and validators',
    },
)


def _requirements() -> tuple[dict, ...]:
    groups = catalog_completion_groups()
    requirements = [
        {
            **metadata,
            'evidence_keys': groups[metadata['completion_group']],
        }
        for metadata in _REQUIREMENT_METADATA
    ]
    requirements.append({
        'id': 'final_cutover_bundle_gate',
        'description': 'Final cutover bundle validates with zero readiness issues.',
        'evidence_keys': catalog_keys(),
        'next_action': 'run scripts/platform_cutover_bundle.py --evidence-root ... --output ... and fix reported diagnostics',
    })
    return tuple(requirements)


REQUIREMENTS = _requirements()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Audit active Unlicensed cutover completion evidence.')
    parser.add_argument('--evidence-root', required=True)
    parser.add_argument('--provider-id', required=True)
    parser.add_argument('--cutover-candidate-id', required=True)
    parser.add_argument('--evaluated-at', required=True)
    parser.add_argument('--collected-at', required=True)
    parser.add_argument('--central-db-schema', default='')
    parser.add_argument('--extraction-manifest', default='')
    parser.add_argument('--workflow-config', default='')
    parser.add_argument('--output', default='')
    parser.add_argument('--next-commands-output', default='')
    args = parser.parse_args(argv)

    audit = build_completion_audit(
        evidence_root=Path(args.evidence_root),
        provider_id=args.provider_id,
        cutover_candidate_id=args.cutover_candidate_id,
        evaluated_at=args.evaluated_at,
        collected_at=args.collected_at,
        central_db_schema_path=Path(args.central_db_schema) if args.central_db_schema else None,
        extraction_manifest_path=Path(args.extraction_manifest) if args.extraction_manifest else None,
        workflow_config_path=Path(args.workflow_config) if args.workflow_config else None,
    )
    payload = json.dumps(audit, sort_keys=True, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + '\n', encoding='utf-8')
    if args.next_commands_output:
        output_path = Path(args.next_commands_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(audit['next_commands'], sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print(payload)
    return 0 if audit['complete'] else 1


def build_completion_audit(
    *,
    evidence_root: Path,
    provider_id: str,
    cutover_candidate_id: str,
    evaluated_at: str,
    collected_at: str,
    central_db_schema_path: Path | None = None,
    extraction_manifest_path: Path | None = None,
    workflow_config_path: Path | None = None,
    workflow_config: Mapping | None = None,
) -> dict:
    manifests, read_issues = _load_manifests(evidence_root)
    bundle = _bundle(
        provider_id=provider_id,
        cutover_candidate_id=cutover_candidate_id,
        evaluated_at=evaluated_at,
        collected_at=collected_at,
        manifests=manifests,
    )
    context_issues: list[dict] = []
    central_db_schema = _read_optional_json(central_db_schema_path, 'central_db_schema', context_issues)
    extraction_manifest = _read_optional_json(extraction_manifest_path, 'extraction_manifest', context_issues)
    workflow_hints = (
        workflow_hints_from_config(
            workflow_config,
            EVIDENCE_FILENAMES,
            str(workflow_config_path or '<inline>'),
            context_issues,
        )
        if workflow_config is not None
        else load_workflow_hints(workflow_config_path, EVIDENCE_FILENAMES, context_issues)
    )
    gate_issues = _dedupe_gate_issues(read_issues, [
        issue.to_dict()
        for issue in cutover_readiness_errors(
            bundle,
            central_db_schema=central_db_schema,
            extraction_manifest=extraction_manifest,
        )
    ])
    all_issues = attach_issue_hints(read_issues + context_issues + gate_issues, workflow_hints)
    checklist = [
        _check_requirement(requirement, all_issues, workflow_hints)
        for requirement in REQUIREMENTS
    ]
    command_handoff = next_commands(all_issues, workflow_hints, EVIDENCE_FILENAMES, evidence_root=evidence_root)
    complete = not all_issues and all(item['status'] == 'pass' for item in checklist)
    return {
        'schema_version': 1,
        'objective': 'Unlicensed web/platform cutover is complete only when real evidence and the final cutover bundle pass.',
        'evidence_root': str(evidence_root),
        'complete': complete,
        'decision': 'complete' if complete else 'not_complete',
        'issue_count': len(all_issues),
        'issues': all_issues,
        'diagnostics': _diagnostics(all_issues),
        'checklist': checklist,
        'workflow_hints': workflow_hints,
        'next_commands': command_handoff,
        'missing_blockers': [
            item
            for item in checklist
            if item['status'] != 'pass'
        ],
        **({'bundle': bundle} if complete else {}),
    }


def _load_manifests(evidence_root: Path) -> tuple[dict[str, dict], list[dict]]:
    manifests: dict[str, dict] = {}
    issues: list[dict] = []
    for key, filename in EVIDENCE_FILENAMES.items():
        path = evidence_root / filename
        if not path.is_file():
            issues.append({
                'code': 'missing_evidence_file',
                'path': str(path),
                'message': f'{key} evidence file is missing',
                'evidence_key': key,
            })
            continue
        try:
            manifests[key] = json.loads(path.read_text(encoding='utf-8'))
        except Exception as exc:
            issues.append({
                'code': 'manifest_read_error',
                'path': str(path),
                'message': str(exc),
                'evidence_key': key,
            })
    return manifests, issues


def _dedupe_gate_issues(read_issues: list[dict], gate_issues: list[dict]) -> list[dict]:
    missing_files = {
        str(issue.get('evidence_key') or '')
        for issue in read_issues
        if issue.get('code') == 'missing_evidence_file'
    }
    if not missing_files:
        return gate_issues
    return [
        issue
        for issue in gate_issues
        if not (
            issue.get('code') == 'missing_manifest'
            and str(issue.get('evidence_key') or '') in missing_files
        )
    ]


def _read_optional_json(path: Path | None, key: str, issues: list[dict]) -> Mapping | None:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        issues.append(build_context_read_issue(key))
        return None


def _bundle(
    *,
    provider_id: str,
    cutover_candidate_id: str,
    evaluated_at: str,
    collected_at: str,
    manifests: Mapping[str, dict],
) -> dict:
    return {
        'schema_version': 1,
        'provider_id': provider_id,
        'cutover_candidate_id': cutover_candidate_id,
        'evaluated_at': evaluated_at,
        'evidence': {
            key: {
                'evidence_id': _manifest_text(manifests.get(key, {}), 'evidence_id', key),
                'collected_at': _manifest_text(manifests.get(key, {}), 'collected_at', collected_at),
                'validated': _manifest_validated(manifests.get(key, {})),
                'issues': _manifest_issues(manifests.get(key, {})),
                'manifest': manifests.get(key, {}),
            }
            for key in EVIDENCE_FILENAMES
        },
    }


def _manifest_validated(manifest: Mapping) -> bool:
    """Honor a manifest's self-reported validation status.

    A collector may stamp ``validated: false`` on evidence it knows is bad; the
    audit must not override that to true. Absence defers to the per-key
    validator + placeholder gate (legacy behavior preserved as ``True``).
    """
    if isinstance(manifest, Mapping) and 'validated' in manifest:
        return bool(manifest.get('validated'))
    return True


def _manifest_issues(manifest: Mapping) -> list:
    if isinstance(manifest, Mapping) and isinstance(manifest.get('issues'), list):
        return list(manifest['issues'])
    return []


def _manifest_text(manifest: Mapping, key: str, fallback: str) -> str:
    value = manifest.get(key) if isinstance(manifest, Mapping) else ''
    text = str(value or '').strip()
    return text if text else fallback


def _check_requirement(requirement: Mapping, issues: list[dict], workflow_hints: Mapping[str, dict]) -> dict:
    keys = set(requirement['evidence_keys'])
    blocking = [
        issue
        for issue in issues
        if not issue.get('evidence_key') or str(issue.get('evidence_key')) in keys
    ]
    return {
        'id': requirement['id'],
        'description': requirement['description'],
        'evidence_keys': list(requirement['evidence_keys']),
        'status': 'pass' if not blocking else 'fail',
        'blocking_issue_count': len(blocking),
        'next_action': 'none' if not blocking else requirement['next_action'],
        'workflow_hints': {
            key: workflow_hints[key]
            for key in requirement['evidence_keys']
            if key in workflow_hints
        },
    }


def _diagnostics(issues: list[dict]) -> dict:
    by_code: dict[str, int] = {}
    by_evidence_key: dict[str, int] = {}
    missing: list[str] = []
    workflow_codes: list[str] = []
    for issue in issues:
        code = str(issue.get('code') or 'issue')
        key = str(issue.get('evidence_key') or '')
        by_code[code] = by_code.get(code, 0) + 1
        if key:
            by_evidence_key[key] = by_evidence_key.get(key, 0) + 1
        if code == 'missing_evidence_file' and key:
            missing.append(key)
        if code.startswith('workflow_config_'):
            workflow_codes.append(code)
    return {
        'issue_count': len(issues),
        'by_code': by_code,
        'by_evidence_key': by_evidence_key,
        'missing_evidence_keys': sorted(set(missing)),
        'workflow_issue_codes': sorted(set(workflow_codes)),
        'production_blocked': bool(issues),
    }


if __name__ == '__main__':
    raise SystemExit(main())
