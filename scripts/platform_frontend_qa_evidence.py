"""Validate and template platform frontend browser QA evidence manifests."""
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

from fcc_test_platform.frontend_qa_evidence import (
    FRONTEND_REQUIRED_VIEWS,
    frontend_qa_errors,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Platform frontend browser QA evidence helper.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    validate = subparsers.add_parser('validate')
    validate.add_argument('manifest')
    template = subparsers.add_parser('template')
    template.add_argument('--evidence-id', default='<frontend QA evidence id>')
    template.add_argument('--app-url', default='<production frontend URL>')
    args = parser.parse_args(argv)

    if args.command == 'validate':
        return _validate(Path(args.manifest))
    if args.command == 'template':
        print(json.dumps(_template(args.evidence_id, args.app_url), sort_keys=True, indent=2))
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
    issues = [issue.to_dict() for issue in frontend_qa_errors(manifest)]
    print(json.dumps({'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    return 0 if not issues else 1


def _template(evidence_id: str, app_url: str) -> dict:
    return {
        'schema_version': 1,
        'evidence_id': evidence_id,
        'collected_at': '<ISO-8601 timestamp>',
        'app_url': app_url,
        'browser': '<browser and version>',
        'auth_flow_verified': False,
        'central_backend_verified': False,
        'provider_contract_routes_verified': False,
        'viewport_results': [
            {
                'name': 'desktop',
                'width': 1440,
                'height': 900,
                'rendered': False,
                'responsive_pass': False,
                'views_verified': list(FRONTEND_REQUIRED_VIEWS),
                'console_errors': ['replace with an empty list after QA'],
                'failed_requests': ['replace with an empty list after QA'],
            }
        ],
        'screenshots': [
            {
                'viewport': 'desktop',
                'relative_path': '<safe relative screenshot path>',
                'sha256': '<sha256>',
            }
        ],
    }


if __name__ == '__main__':
    raise SystemExit(main())
