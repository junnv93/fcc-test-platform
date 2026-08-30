"""Validate and template artifact/report sync evidence manifests."""
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

from fcc_test_platform.artifact_sync_evidence import artifact_sync_evidence_errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Artifact/report sync evidence helper.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    validate = subparsers.add_parser('validate')
    validate.add_argument('manifest')
    template = subparsers.add_parser('template')
    template.add_argument('--provider-id', default='fcc-unlicensed-conducted')
    template.add_argument('--session-id', default='<session id>')
    template.add_argument('--sync-job-id', default='<sync job id>')
    template.add_argument('--synced-at', default='<ISO-8601 timestamp>')
    args = parser.parse_args(argv)

    if args.command == 'validate':
        return _validate(Path(args.manifest))
    if args.command == 'template':
        print(json.dumps(_template(args.provider_id, args.session_id, args.sync_job_id, args.synced_at), sort_keys=True, indent=2))
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
    issues = [issue.to_dict() for issue in artifact_sync_evidence_errors(manifest)]
    print(json.dumps({'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    return 0 if not issues else 1


def _template(provider_id: str, session_id: str, sync_job_id: str, synced_at: str) -> dict:
    return {
        'schema_version': 1,
        'provider_id': provider_id,
        'session_id': session_id,
        'sync_job_id': sync_job_id,
        'source_root_id': '<lab artifact root id>',
        'destination_root_id': '<platform file-server root id>',
        'synced_at': synced_at,
        'artifact_count': 1,
        'report_output_count': 1,
        'files': [
            {
                'record_type': 'artifact',
                'relative_path': '<safe relative artifact path>',
                'storage_backend': 'filesystem',
                'sha256': '<sha256>',
                'byte_size': 0,
                'sync_status': '<synced>',
                'source_exists': False,
                'destination_exists': False,
            },
            {
                'record_type': 'report_output',
                'relative_path': '<safe relative report output path>',
                'storage_backend': 'filesystem',
                'sha256': '<sha256>',
                'byte_size': 0,
                'sync_status': '<synced>',
                'source_exists': False,
                'destination_exists': False,
            },
        ],
    }


if __name__ == '__main__':
    raise SystemExit(main())
