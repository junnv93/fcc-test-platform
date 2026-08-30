"""Assemble deployment evidence manifests into a cutover evidence directory."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Callable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
for path in (PROJECT_ROOT, SRC_ROOT):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from fcc_test_platform.frontend_deployment_evidence import frontend_deployment_errors
from fcc_test_platform.frontend_qa_evidence import frontend_qa_errors
from fcc_test_platform.idp_deployment_evidence import idp_deployment_errors
from fcc_test_contracts.common.provider_service_evidence import provider_service_deployment_errors


EVIDENCE_SLOTS: dict[str, tuple[str, Callable[[Mapping], list]]] = {
    'service_deployment': ('provider_service_deployment.json', provider_service_deployment_errors),
    'idp_deployment': ('idp_deployment.json', idp_deployment_errors),
    'frontend_deployment': ('frontend_deployment.json', frontend_deployment_errors),
    'frontend_browser_qa': ('frontend_browser_qa.json', frontend_qa_errors),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Assemble deployment evidence for platform cutover.')
    parser.add_argument('--evidence-root', required=True)
    parser.add_argument('--service-deployment', required=True)
    parser.add_argument('--idp-deployment', required=True)
    parser.add_argument('--frontend-deployment', required=True)
    parser.add_argument('--frontend-browser-qa', required=True)
    parser.add_argument('--summary-output', default='')
    parser.add_argument('--require-valid', action='store_true')
    args = parser.parse_args(argv)

    source_paths = {
        'service_deployment': Path(args.service_deployment),
        'idp_deployment': Path(args.idp_deployment),
        'frontend_deployment': Path(args.frontend_deployment),
        'frontend_browser_qa': Path(args.frontend_browser_qa),
    }
    evidence_root = Path(args.evidence_root)
    summary = assemble_deployment_evidence(evidence_root=evidence_root, source_paths=source_paths)
    payload = json.dumps(summary, sort_keys=True, indent=2)
    if args.summary_output:
        output_path = Path(args.summary_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + '\n', encoding='utf-8')
    print(payload)
    if args.require_valid and not summary['valid']:
        return 1
    return 0 if summary['readable'] else 2


def assemble_deployment_evidence(*, evidence_root: Path, source_paths: Mapping[str, Path]) -> dict:
    evidence_root.mkdir(parents=True, exist_ok=True)
    slots: dict[str, dict] = {}
    all_readable = True
    all_valid = True

    for key, (filename, validator) in EVIDENCE_SLOTS.items():
        source_path = Path(source_paths.get(key) or '')
        destination = evidence_root / filename
        slot = {
            'source_path': str(source_path),
            'destination_path': str(destination),
            'source_sha256': '',
            'destination_sha256': '',
            'copied': False,
            'valid': False,
            'issues': [],
        }
        if not source_path.is_file():
            all_readable = False
            all_valid = False
            slot['issues'] = [{
                'code': 'missing_evidence_file',
                'path': str(source_path),
                'message': f'{key} evidence file is missing',
            }]
            slots[key] = slot
            continue
        try:
            manifest = json.loads(source_path.read_text(encoding='utf-8'))
        except Exception as exc:
            all_readable = False
            all_valid = False
            slot['issues'] = [{
                'code': 'manifest_read_error',
                'path': str(source_path),
                'message': str(exc),
            }]
            slots[key] = slot
            continue

        issues = [issue.to_dict() for issue in validator(manifest)]
        if issues:
            all_valid = False
        slot.update({
            'source_sha256': _sha256_file(source_path),
            'valid': not issues,
            'issues': issues,
        })
        slots[key] = slot

    promotion_issues: list[dict] = []
    promoted = False
    if all_readable and all_valid:
        promotion_issues = _promote_sources(evidence_root, source_paths, slots)
        all_valid = not promotion_issues
        promoted = all_valid

    return {
        'schema_version': 1,
        'evidence_root': str(evidence_root),
        'readable': all_readable,
        'valid': all_readable and all_valid,
        'promotion_ready': all_readable and not promotion_issues and all(slot['valid'] for slot in slots.values()),
        'promoted': promoted,
        'promotion_issues': promotion_issues,
        'expected_files': {
            key: str(evidence_root / filename)
            for key, (filename, _validator) in EVIDENCE_SLOTS.items()
        },
        'slots': slots,
    }


def _promote_sources(evidence_root: Path, source_paths: Mapping[str, Path], slots: dict[str, dict]) -> list[dict]:
    temp_paths: dict[str, Path] = {}
    issues: list[dict] = []
    for key, (filename, _validator) in EVIDENCE_SLOTS.items():
        source = Path(source_paths[key])
        temp = evidence_root / f'.{filename}.tmp'
        temp_paths[key] = temp
        try:
            shutil.copyfile(source, temp)
            copied_sha256 = _sha256_file(temp)
        except Exception as exc:
            issues.append({
                'code': 'copy_error',
                'path': str(temp),
                'message': str(exc),
                'evidence_key': key,
            })
            continue
        if copied_sha256 != slots[key]['source_sha256']:
            issues.append({
                'code': 'copy_verification_failed',
                'path': str(temp),
                'message': f'{key} copied sha256 does not match source sha256',
                'evidence_key': key,
            })
    if issues:
        _remove_temps(temp_paths.values())
        return issues

    for key, (filename, _validator) in EVIDENCE_SLOTS.items():
        destination = evidence_root / filename
        temp = temp_paths[key]
        try:
            temp.replace(destination)
            slots[key].update({
                'copied': True,
                'destination_sha256': _sha256_file(destination),
            })
        except Exception as exc:
            issues.append({
                'code': 'promotion_error',
                'path': str(destination),
                'message': str(exc),
                'evidence_key': key,
            })
    _remove_temps(temp_paths.values())
    return issues


def _remove_temps(paths) -> None:
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == '__main__':
    raise SystemExit(main())
