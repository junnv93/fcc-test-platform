"""Validate and template platform identity-provider policy manifests.

⚠️ 이것은 `scripts/platform_identity_policy.py` 의 **알맹이**다. `scripts/` 는 패키지가 아니라
**휠이 나르지 못한다** — 이 레인을 핀으로 받는 소비자에게 그 파일은 오지 않는다.
그래서 로직은 여기 살고 `scripts/` 에는 진입점만 남는다. 껍데기는 양쪽 레포에
두되, 담긴 것이 그뿐이라 **갈라질 것이 없다.**
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


from fcc_test_platform.identity_policy import identity_policy_errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Platform identity policy evidence helper.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    validate = subparsers.add_parser('validate')
    validate.add_argument('manifest')
    publish = subparsers.add_parser('publish')
    publish.add_argument('--manifest', required=True)
    publish.add_argument('--output', required=True)
    publish.add_argument('--require-valid', action='store_true')
    template = subparsers.add_parser('template')
    template.add_argument('--provider-key', default='<identity provider key>')
    template.add_argument('--audience', default='fcc-test-platform')
    args = parser.parse_args(argv)

    if args.command == 'validate':
        return _validate(Path(args.manifest))
    if args.command == 'publish':
        return _publish(Path(args.manifest), Path(args.output), args.require_valid)
    if args.command == 'template':
        print(json.dumps(_template(args.provider_key, args.audience), sort_keys=True, indent=2))
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
    issues = [issue.to_dict() for issue in identity_policy_errors(manifest)]
    print(json.dumps({'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    return 0 if not issues else 1


def _publish(manifest_path: Path, output_path: Path, require_valid: bool) -> int:
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except Exception as exc:
        print(json.dumps({
            'published': False,
            'valid': False,
            'issues': [{
                'code': 'read_error',
                'path': str(manifest_path),
                'message': str(exc),
            }],
        }, sort_keys=True, indent=2))
        return 2
    issues = [issue.to_dict() for issue in identity_policy_errors(manifest)]
    if not issues or not require_valid:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'published': not issues or not require_valid, 'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    if require_valid and issues:
        return 1
    return 0


def _template(provider_key: str, audience: str) -> dict:
    return {
        'schema_version': 1,
        'provider_key': provider_key,
        'oidc': {
            'issuer': '<https issuer URL>',
            'jwks_uri': '<https JWKS URL>',
            'audiences': [audience],
            'algorithm': 'RS256',
        },
        'session': {
            'idle_timeout_seconds': 3600,
            'refresh_token_rotation': False,
            'refresh_rotation_seconds': 300,
        },
        'claim_mapping': {
            'subject_claim': 'sub',
            'display_name_claim': 'name',
            'email_claim': 'email',
            'role_claim': 'groups',
            'admin_group_allowlist': [],
            'role_map': {
                '<external viewer group>': 'viewer',
                '<external operator group>': 'operator',
                '<external report manager group>': 'report_manager',
                '<external admin group>': 'admin',
            },
        },
    }


if __name__ == '__main__':
    raise SystemExit(main())
