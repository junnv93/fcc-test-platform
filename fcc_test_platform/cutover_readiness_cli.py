"""Validate platform cutover readiness evidence bundles.

⚠️ 이것은 `scripts/platform_cutover_readiness.py` 의 **알맹이**다. `scripts/` 는 패키지가 아니라
**휠이 나르지 못한다** — 이 레인을 핀으로 받는 소비자에게 그 파일은 오지 않는다.
그래서 로직은 여기 살고 `scripts/` 에는 진입점만 남는다. 껍데기는 양쪽 레포에
두되, 담긴 것이 그뿐이라 **갈라질 것이 없다.**
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


from fcc_test_platform.cutover_readiness import (
    CUTOVER_REQUIRED_EVIDENCE,
    cutover_readiness_errors,
)
from fcc_test_platform.application.platform_cutover_catalog import catalog_entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Validate platform cutover readiness evidence.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    validate_parser = subparsers.add_parser('validate')
    validate_parser.add_argument('bundle')
    validate_parser.add_argument('--central-db-schema', default='')
    validate_parser.add_argument('--extraction-manifest', default='')
    template_parser = subparsers.add_parser('template')
    template_parser.add_argument('--provider-id', default='fcc-unlicensed-conducted')
    template_parser.add_argument('--cutover-candidate-id', default='cutover-candidate')
    template_parser.add_argument('--evaluated-at', default='<ISO-8601 timestamp>')
    args = parser.parse_args(argv)

    if args.command == 'validate':
        return _validate(
            Path(args.bundle),
            _optional_path(args.central_db_schema),
            _optional_path(args.extraction_manifest),
        )
    if args.command == 'template':
        print(json.dumps(_template(args.provider_id, args.cutover_candidate_id, args.evaluated_at), sort_keys=True, indent=2))
        return 0
    return 2


def _validate(
    bundle_path: Path,
    central_db_schema_path: Path | None,
    extraction_manifest_path: Path | None,
) -> int:
    try:
        bundle = _read_json(bundle_path)
        central_db_schema = _read_json(central_db_schema_path) if central_db_schema_path else None
        extraction_manifest = _read_json(extraction_manifest_path) if extraction_manifest_path else None
    except Exception as exc:
        print(json.dumps({
            'ready': False,
            'issues': [{
                'code': 'read_error',
                'path': str(getattr(exc, 'filename', '') or bundle_path),
                'message': str(exc),
                'evidence_key': '',
            }],
        }, sort_keys=True, indent=2))
        return 2

    issues = [
        issue.to_dict()
        for issue in cutover_readiness_errors(
            bundle,
            central_db_schema=central_db_schema,
            extraction_manifest=extraction_manifest,
        )
    ]
    print(json.dumps({
        'ready': not issues,
        'issues': issues,
    }, sort_keys=True, indent=2))
    return 0 if not issues else 1


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _optional_path(value: str) -> Path | None:
    text = str(value or '').strip()
    return Path(text) if text else None


def _template(provider_id: str, cutover_candidate_id: str, evaluated_at: str) -> dict:
    return {
        'schema_version': 1,
        'provider_id': provider_id,
        'cutover_candidate_id': cutover_candidate_id,
        'evaluated_at': evaluated_at,
        'evidence_catalog': [
            {
                'key': entry.key,
                'canonical_filename': entry.canonical_filename,
                'cli_argument': entry.cli_argument,
                'validator': entry.validator_name,
                'required_contexts': list(entry.required_contexts),
                'completion_group': entry.completion_group,
            }
            for entry in catalog_entries()
        ],
        'evidence': {
            key: {
                'evidence_id': f'<{key} evidence id>',
                'collected_at': '<ISO-8601 timestamp>',
                'validated': False,
                'issues': ['replace this placeholder with validated evidence'],
                'manifest': {},
            }
            for key in CUTOVER_REQUIRED_EVIDENCE
        },
    }


if __name__ == '__main__':
    raise SystemExit(main())
