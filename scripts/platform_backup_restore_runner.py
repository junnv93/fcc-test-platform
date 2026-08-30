"""Run a filesystem backup/restore drill and emit platform evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
for path in (PROJECT_ROOT, SRC_ROOT):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from fcc_test_platform.artifact_storage import normalize_relative_path  # noqa: E402
from fcc_test_platform.backup_restore_drill import backup_restore_drill_errors  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run a platform backup/restore drill.')
    parser.add_argument('--restore-point-id', required=True)
    parser.add_argument('--database-name', required=True)
    parser.add_argument('--db-backup-file', required=True)
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--backup-root', required=True)
    parser.add_argument('--restore-root', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--artifact', action='append', default=[], help='safe relative artifact path')
    parser.add_argument('--report-output', action='append', default=[], help='safe relative report output path')
    parser.add_argument('--require-valid', action='store_true')
    args = parser.parse_args(argv)

    try:
        manifest = run_backup_restore_drill(
            restore_point_id=args.restore_point_id,
            database_name=args.database_name,
            db_backup_file=Path(args.db_backup_file),
            source_root=Path(args.source_root),
            backup_root=Path(args.backup_root),
            restore_root=Path(args.restore_root),
            artifact_paths=[normalize_relative_path(value) for value in args.artifact],
            report_output_paths=[normalize_relative_path(value) for value in args.report_output],
        )
    except Exception as exc:
        print(json.dumps({'completed': False, 'error': str(exc)}, sort_keys=True, indent=2), file=sys.stderr)
        return 2

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    issues = [issue.to_dict() for issue in backup_restore_drill_errors(manifest)]
    print(json.dumps({'completed': True, 'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    if args.require_valid and issues:
        return 1
    return 0


def run_backup_restore_drill(
    *,
    restore_point_id: str,
    database_name: str,
    db_backup_file: Path,
    source_root: Path,
    backup_root: Path,
    restore_root: Path,
    artifact_paths: list[str],
    report_output_paths: list[str],
) -> dict:
    if not artifact_paths:
        raise ValueError('at least one artifact path is required')
    if not report_output_paths:
        raise ValueError('at least one report output path is required')
    if not db_backup_file.is_file():
        raise FileNotFoundError(str(db_backup_file))

    created_at = datetime.now(timezone.utc).isoformat()
    archive_relative = normalize_relative_path(f'backups/{restore_point_id}/artifacts.zip')
    archive_path = backup_root / archive_relative
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    all_paths = artifact_paths + report_output_paths
    _write_archive(source_root, archive_path, all_paths)
    _restore_archive(archive_path, restore_root)

    return {
        'schema_version': 1,
        'restore_point_id': restore_point_id,
        'db_backup': {
            'backup_point_id': restore_point_id,
            'database_name': database_name,
            'created_at': created_at,
            'sha256': _sha256_file(db_backup_file),
        },
        'artifact_backup': {
            'backup_point_id': restore_point_id,
            'relative_path': archive_relative,
            'created_at': created_at,
            'sha256': _sha256_file(archive_path),
        },
        'artifacts': [
            _restored_record(source_root, restore_root, relative_path)
            for relative_path in artifact_paths
        ],
        'report_outputs': [
            _restored_record(source_root, restore_root, relative_path)
            for relative_path in report_output_paths
        ],
    }


def _write_archive(source_root: Path, archive_path: Path, relative_paths: list[str]) -> None:
    with zipfile.ZipFile(archive_path, mode='w', compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path in relative_paths:
            source_path = source_root / relative_path
            if not source_path.is_file():
                raise FileNotFoundError(relative_path)
            archive.write(source_path, arcname=relative_path)


def _restore_archive(archive_path: Path, restore_root: Path) -> None:
    with zipfile.ZipFile(archive_path, mode='r') as archive:
        for name in archive.namelist():
            relative_path = normalize_relative_path(name)
            destination = restore_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, destination.open('wb') as target:
                target.write(source.read())


def _restored_record(source_root: Path, restore_root: Path, relative_path: str) -> dict:
    source_path = source_root / relative_path
    restored_path = restore_root / relative_path
    source_hash = _sha256_file(source_path) if source_path.is_file() else ''
    restored_hash = _sha256_file(restored_path) if restored_path.is_file() else ''
    return {
        'relative_path': relative_path,
        'sha256': source_hash,
        'storage_backend': 'filesystem',
        'restored': bool(source_hash and restored_hash == source_hash),
        'exists_after_restore': restored_path.is_file(),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == '__main__':
    raise SystemExit(main())
