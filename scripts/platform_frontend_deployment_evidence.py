"""Validate and template platform frontend deployment evidence manifests."""
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

from fcc_test_platform.frontend_deployment_evidence import frontend_deployment_errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Platform frontend deployment evidence helper.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    validate = subparsers.add_parser('validate')
    validate.add_argument('manifest')
    template = subparsers.add_parser('template')
    template.add_argument('--evidence-id', default='<frontend deployment evidence id>')
    template.add_argument('--app-url', default='<production frontend URL>')
    template.add_argument('--backend-base-url', default='<production backend URL>')
    args = parser.parse_args(argv)

    if args.command == 'validate':
        return _validate(Path(args.manifest))
    if args.command == 'template':
        print(json.dumps(_template(args.evidence_id, args.app_url, args.backend_base_url), sort_keys=True, indent=2))
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
    issues = [issue.to_dict() for issue in frontend_deployment_errors(manifest)]
    print(json.dumps({'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    return 0 if not issues else 1


def _template(evidence_id: str, app_url: str, backend_base_url: str) -> dict:
    return {
        'schema_version': 1,
        'evidence_id': evidence_id,
        'collected_at': '<ISO-8601 timestamp>',
        'app_url': app_url,
        'backend_base_url': backend_base_url,
        'hosting_provider': '<hosting provider>',
        'build_version': '<frontend build version>',
        'build_sha256': '<sha256>',
        'deployed': False,
        'tls_valid': False,
        'asset_cache_policy': {
            'immutable_assets': False,
            'html_cache_control': '<must include no-store>',
        },
        'environment': {
            'name': '<production environment>',
            'backend_base_url': backend_base_url,
            'variables': [
                {
                    'name': 'API_BASE_URL',
                    'value': backend_base_url,
                },
                {
                    'name': 'OIDC_CLIENT_SECRET',
                    'value': '<redacted>',
                },
            ],
        },
        'secret_scan': {
            'status': '<pass|fail>',
            'findings': ['replace with an empty list after secret scan passes'],
        },
    }


if __name__ == '__main__':
    raise SystemExit(main())
