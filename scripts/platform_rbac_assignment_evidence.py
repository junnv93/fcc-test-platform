"""Validate and template applied platform RBAC assignment evidence."""
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

from fcc_test_platform.rbac import build_platform_rbac_seed  # noqa: E402
from fcc_test_platform.rbac_assignment_evidence import rbac_assignment_evidence_errors  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Platform RBAC assignment evidence helper.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    validate = subparsers.add_parser('validate')
    validate.add_argument('manifest')
    template = subparsers.add_parser('template')
    template.add_argument('--evidence-id', default='<rbac assignment evidence id>')
    template.add_argument('--collected-at', default='<ISO-8601 timestamp>')
    args = parser.parse_args(argv)

    if args.command == 'validate':
        return _validate(Path(args.manifest))
    if args.command == 'template':
        print(json.dumps(_template(args.evidence_id, args.collected_at), sort_keys=True, indent=2))
        return 0
    return 2


def _validate(path: Path) -> int:
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        print(json.dumps({
            'valid': False,
            'issues': [{'code': 'read_error', 'path': str(path), 'message': str(exc)}],
        }, sort_keys=True, indent=2))
        return 2
    issues = [issue.to_dict() for issue in rbac_assignment_evidence_errors(manifest)]
    print(json.dumps({'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    return 0 if not issues else 1


def _template(evidence_id: str, collected_at: str) -> dict:
    seed = build_platform_rbac_seed()
    records = seed.to_records()
    return {
        'schema_version': 1,
        'evidence_id': evidence_id,
        'collected_at': collected_at,
        'rbac_source': 'docs/platform/rbac_policy.v1.json',
        'permissions': records['permissions'],
        'roles': records['roles'],
        'users': [
            {'subject': '<admin subject>', 'enabled': False},
        ],
        'user_roles': [
            {'subject': '<admin subject>', 'role_key': 'admin'},
        ],
    }


if __name__ == '__main__':
    raise SystemExit(main())
