"""Execute approved artifact/report retention plans.

The policy module only creates metadata plans. This runtime worker consumes an
approved plan and either dry-runs, archives, or deletes files under injected
storage roots. Destructive actions require an explicit --execute flag.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
for path in (PROJECT_ROOT, SRC_ROOT):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from fcc_test_platform.artifact_storage import normalize_relative_path  # noqa: E402


@dataclass(frozen=True)
class RetentionActionResult:
    record_type: str
    relative_path: str
    action: str
    executed: bool
    source_exists_before: bool
    source_exists_after: bool
    archive_path: str = ''

    def to_dict(self) -> dict:
        return {
            'record_type': self.record_type,
            'relative_path': self.relative_path,
            'action': self.action,
            'executed': self.executed,
            'source_exists_before': self.source_exists_before,
            'source_exists_after': self.source_exists_after,
            'archive_path': self.archive_path,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Execute an approved retention plan.')
    parser.add_argument('--plan', required=True)
    parser.add_argument('--storage-root', required=True)
    parser.add_argument('--archive-root', default='')
    parser.add_argument('--output', required=True)
    parser.add_argument('--mode', choices=('archive', 'delete'), default='archive')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)

    try:
        plan = json.loads(Path(args.plan).read_text(encoding='utf-8'))
        result = execute_retention_plan(
            plan=plan,
            storage_root=Path(args.storage_root),
            archive_root=Path(args.archive_root) if args.archive_root else None,
            mode=args.mode,
            execute=args.execute,
        )
    except Exception as exc:
        print(json.dumps({'completed': False, 'error': str(exc)}, sort_keys=True, indent=2), file=sys.stderr)
        return 2

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'completed': True, 'executed': result['executed'], 'actions': len(result['actions'])}, sort_keys=True, indent=2))
    return 0


def execute_retention_plan(
    *,
    plan: Mapping,
    storage_root: Path,
    archive_root: Path | None,
    mode: str,
    execute: bool,
) -> dict:
    _validate_plan(plan, execute=execute)
    source_root = storage_root.resolve()
    resolved_archive_root = archive_root.resolve() if archive_root is not None else None
    if mode == 'archive' and resolved_archive_root is None:
        raise ValueError('archive_root is required for archive mode')
    actions = [
        _process_candidate(
            candidate=candidate,
            storage_root=source_root,
            archive_root=resolved_archive_root,
            mode=mode,
            execute=execute,
        ).to_dict()
        for candidate in plan.get('candidates', [])
    ]
    return {
        'schema_version': 1,
        'executed': execute,
        'mode': mode,
        'approved_by': str(plan.get('approved_by') or ''),
        'approved_at': str(plan.get('approved_at') or ''),
        'executed_at': datetime.now(timezone.utc).isoformat(),
        'actions': actions,
    }


def _process_candidate(
    *,
    candidate: Mapping,
    storage_root: Path,
    archive_root: Path | None,
    mode: str,
    execute: bool,
) -> RetentionActionResult:
    record_type = str(candidate.get('record_type') or '').strip()
    relative_path = normalize_relative_path(str(candidate.get('relative_path') or ''))
    source_path = (storage_root / relative_path).resolve()
    _require_within_root(source_path, storage_root)
    source_exists_before = source_path.is_file()
    archive_path_text = ''
    if execute and source_exists_before:
        if mode == 'archive':
            assert archive_root is not None
            destination = (archive_root / relative_path).resolve()
            _require_within_root(destination, archive_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_path), str(destination))
            archive_path_text = destination.relative_to(archive_root).as_posix()
        elif mode == 'delete':
            source_path.unlink()
        else:
            raise ValueError('mode must be archive or delete')
    elif mode == 'archive' and archive_root is not None:
        archive_path_text = (archive_root / relative_path).resolve().relative_to(archive_root).as_posix()
    return RetentionActionResult(
        record_type=record_type,
        relative_path=relative_path,
        action=mode,
        executed=execute and source_exists_before,
        source_exists_before=source_exists_before,
        source_exists_after=source_path.exists(),
        archive_path=archive_path_text,
    )


def _validate_plan(plan: Mapping, *, execute: bool) -> None:
    if not str(plan.get('approved_by') or '').strip():
        raise ValueError('approved_by is required')
    if not str(plan.get('approved_at') or '').strip():
        raise ValueError('approved_at is required')
    if not str(plan.get('reason') or '').strip():
        raise ValueError('reason is required')
    candidates = plan.get('candidates')
    if not isinstance(candidates, list) or not candidates:
        raise ValueError('plan must include candidates')
    if execute and plan.get('dry_run') is True:
        raise ValueError('dry_run plans cannot be executed')


def _require_within_root(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError('resolved path escapes storage root') from exc


if __name__ == '__main__':
    raise SystemExit(main())
