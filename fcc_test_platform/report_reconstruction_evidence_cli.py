"""`scripts/platform_report_reconstruction_evidence.py` 의 **알맹이** (2026-08-31).

⚠️ `scripts/` 는 패키지가 아니라 **휠이 나르지 못한다** — 이 레인을 핀으로
받는 소비자(모노레포)에게 그 파일은 오지 않는다. 그래서 로직은 여기 살고
`scripts/` 에는 **3줄 진입점**만 남는다. 껍데기는 양쪽 레포에 둘 다 두되,
거기 담긴 것이 3줄뿐이라 **드리프트할 것이 없다.**
"""
from __future__ import annotations

"""Validate and template DB-only report reconstruction evidence manifests."""
import argparse
import json
from pathlib import Path
import sys
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
