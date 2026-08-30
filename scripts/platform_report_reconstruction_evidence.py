"""Validate and template DB-only report reconstruction evidence manifests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
for path in (PROJECT_ROOT, SRC_ROOT):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from fcc_test_platform.report_reconstruction_evidence import (
    db_only_report_reconstruction_errors,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='DB-only report reconstruction evidence helper.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    validate = subparsers.add_parser('validate')
    validate.add_argument('manifest')
    template = subparsers.add_parser('template')
    template.add_argument('--provider-id', default='fcc-unlicensed-conducted')
    template.add_argument('--session-id', default='<session id>')
    template.add_argument('--report-run-id', default='<report run id>')
    args = parser.parse_args(argv)

    if args.command == 'validate':
        return _validate(Path(args.manifest))
    if args.command == 'template':
        print(json.dumps(_template(args.provider_id, args.session_id, args.report_run_id), sort_keys=True, indent=2))
        return 0
    return 2


def _validate(path: Path) -> int:
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        print(json.dumps({
            'valid': False,
            'issues': [{
                'code': 'read_error',
                'path': str(path),
                'message': str(exc),
            }],
        }, sort_keys=True, indent=2))
        return 2
    issues = [issue.to_dict() for issue in db_only_report_reconstruction_errors(manifest)]
    print(json.dumps({'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    return 0 if not issues else 1


def _template(provider_id: str, session_id: str, report_run_id: str) -> dict:
    return {
        'schema_version': 1,
        'provider_id': provider_id,
        'session_id': session_id,
        'report_run_id': report_run_id,
        'source_mode': 'db_primary',
        'excel_source_used': False,
        'excel_export_only': True,
        'source_snapshot': {
            'central_db_migration_evidence_id': '<migration evidence id>',
            'ingestion_batch_id': '<ingestion batch id>',
            'snapshot_sha256': '<sha256>',
            'measurement_result_count': 0,
        },
        'generated_outputs': [
            {
                'output_type': 'docx',
                'relative_path': '<safe relative DOCX or PDF path>',
                'storage_backend': 'filesystem',
                'sha256': '<sha256>',
            }
        ],
        'artifact_resolution': {
            'required_count': 0,
            'resolved_count': 0,
            'missing': [],
        },
        'acceptance_audit': {
            'status': '<pass|approved_with_findings>',
            'reviewed_by': '<reviewer>',
            'reviewed_at': '<ISO-8601 timestamp>',
        },
    }

if __name__ == '__main__':
    raise SystemExit(main())
