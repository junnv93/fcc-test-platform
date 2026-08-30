"""Sync artifact/report files between roots and emit cutover evidence.

The evidence validator remains read-only. This runtime worker can copy files
when explicitly requested and always records source/destination existence plus
hash equality in the output manifest.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
for path in (PROJECT_ROOT, SRC_ROOT):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from fcc_test_platform.artifact_storage import normalize_relative_path  # noqa: E402
from fcc_test_platform.artifact_sync_evidence import artifact_sync_evidence_errors  # noqa: E402


@dataclass(frozen=True)
class SyncRecord:
    record_type: str
    relative_path: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Sync artifact/report files and emit evidence.')
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--destination-root', required=True)
    parser.add_argument('--source-root-id', required=True)
    parser.add_argument('--destination-root-id', required=True)
    parser.add_argument('--provider-id', required=True)
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--sync-job-id', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--record', action='append', default=[], help='record_type=relative/path')
    parser.add_argument('--records-json', default='', help='JSON list of {record_type, relative_path}')
    parser.add_argument('--copy-missing', action='store_true')
    parser.add_argument('--require-valid', action='store_true')
    args = parser.parse_args(argv)

    try:
        records = _load_records(args.record, args.records_json)
        manifest = build_sync_manifest(
            source_root=Path(args.source_root),
            destination_root=Path(args.destination_root),
            source_root_id=args.source_root_id,
            destination_root_id=args.destination_root_id,
            provider_id=args.provider_id,
            session_id=args.session_id,
            sync_job_id=args.sync_job_id,
            records=records,
            copy_missing=args.copy_missing,
        )
    except Exception as exc:
        print(json.dumps({'synced': False, 'error': str(exc)}, sort_keys=True, indent=2), file=sys.stderr)
        return 2

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8')

    issues = [issue.to_dict() for issue in artifact_sync_evidence_errors(manifest)]
    print(json.dumps({'synced': True, 'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    if args.require_valid and issues:
        return 1
    return 0


def build_sync_manifest(
    *,
    source_root: Path,
    destination_root: Path,
    source_root_id: str,
    destination_root_id: str,
    provider_id: str,
    session_id: str,
    sync_job_id: str,
    records: list[SyncRecord],
    copy_missing: bool,
) -> dict:
    files = [
        _sync_one(source_root, destination_root, record, copy_missing=copy_missing)
        for record in records
    ]
    return {
        'schema_version': 1,
        'provider_id': provider_id,
        'session_id': session_id,
        'sync_job_id': sync_job_id,
        'source_root_id': source_root_id,
        'destination_root_id': destination_root_id,
        'synced_at': datetime.now(timezone.utc).isoformat(),
        'artifact_count': sum(1 for record in records if record.record_type == 'artifact'),
        'report_output_count': sum(1 for record in records if record.record_type == 'report_output'),
        'files': files,
    }


def _sync_one(
    source_root: Path,
    destination_root: Path,
    record: SyncRecord,
    *,
    copy_missing: bool,
) -> dict:
    relative_path = normalize_relative_path(record.relative_path)
    source_path = source_root / relative_path
    destination_path = destination_root / relative_path
    source_exists = source_path.is_file()
    if copy_missing and source_exists and _needs_copy(source_path, destination_path):
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)
    destination_exists = destination_path.is_file()
    source_hash = _sha256_file(source_path) if source_exists else ''
    destination_hash = _sha256_file(destination_path) if destination_exists else ''
    synced = source_exists and destination_exists and source_hash == destination_hash
    return {
        'record_type': record.record_type,
        'relative_path': relative_path,
        'storage_backend': 'filesystem',
        'sha256': source_hash if source_hash else destination_hash,
        'byte_size': source_path.stat().st_size if source_exists else 0,
        'sync_status': 'synced' if synced else 'pending',
        'source_exists': source_exists,
        'destination_exists': destination_exists,
    }


def _needs_copy(source_path: Path, destination_path: Path) -> bool:
    if not destination_path.is_file():
        return True
    if source_path.stat().st_size != destination_path.stat().st_size:
        return True
    return _sha256_file(source_path) != _sha256_file(destination_path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _load_records(raw_records: list[str], records_json: str) -> list[SyncRecord]:
    records = [_parse_record(value) for value in raw_records]
    if records_json:
        payload = json.loads(Path(records_json).read_text(encoding='utf-8'))
        if not isinstance(payload, list):
            raise ValueError('records-json must contain a list')
        for item in payload:
            records.append(SyncRecord(
                record_type=str(item['record_type']),
                relative_path=normalize_relative_path(str(item['relative_path'])),
            ))
    if not records:
        raise ValueError('at least one --record or --records-json entry is required')
    return records


def _parse_record(value: str) -> SyncRecord:
    record_type, separator, relative_path = value.partition('=')
    if not separator:
        raise ValueError('record must use record_type=relative/path')
    return SyncRecord(
        record_type=record_type.strip(),
        relative_path=normalize_relative_path(relative_path),
    )


if __name__ == '__main__':
    raise SystemExit(main())
