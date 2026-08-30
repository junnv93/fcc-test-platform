"""Validate and template platform backup/restore drill evidence manifests."""
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

from fcc_test_platform.backup_restore_drill import backup_restore_drill_errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Platform backup/restore drill evidence helper.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    validate = subparsers.add_parser('validate')
    validate.add_argument('manifest')
    template = subparsers.add_parser('template')
    template.add_argument('--restore-point-id', default='<restore point id>')
    template.add_argument('--database-name', default='fcc_platform')
    template.add_argument('--created-at', default='<ISO-8601 timestamp>')
    args = parser.parse_args(argv)

    if args.command == 'validate':
        return _validate(Path(args.manifest))
    if args.command == 'template':
        print(json.dumps(
            _template(args.restore_point_id, args.database_name, args.created_at),
            sort_keys=True,
            indent=2,
        ))
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
    issues = [issue.to_dict() for issue in backup_restore_drill_errors(manifest)]
    print(json.dumps({'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    return 0 if not issues else 1


def _template(restore_point_id: str, database_name: str, created_at: str) -> dict:
    return {
        'schema_version': 1,
        'restore_point_id': restore_point_id,
        'db_backup': {
            'backup_point_id': restore_point_id,
            'database_name': database_name,
            'created_at': created_at,
            'sha256': '<sha256>',
        },
        'artifact_backup': {
            'backup_point_id': restore_point_id,
            'relative_path': '<safe relative backup archive path>',
            'created_at': created_at,
            'sha256': '<sha256>',
        },
        'artifacts': [
            {
                'relative_path': '<safe relative artifact path>',
                'sha256': '<sha256>',
                'storage_backend': 'filesystem',
                'restored': False,
                'exists_after_restore': False,
            }
        ],
        'report_outputs': [
            {
                'relative_path': '<safe relative report output path>',
                'sha256': '<sha256>',
                'storage_backend': 'filesystem',
                'restored': False,
                'exists_after_restore': False,
            }
        ],
    }


if __name__ == '__main__':
    raise SystemExit(main())
