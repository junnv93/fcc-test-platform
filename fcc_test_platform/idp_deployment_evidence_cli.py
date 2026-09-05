"""Validate and template platform IdP deployment evidence manifests.

⚠️ 이것은 `scripts/platform_idp_deployment_evidence.py` 의 **알맹이**다. `scripts/` 는 패키지가 아니라
**휠이 나르지 못한다** — 이 레인을 핀으로 받는 소비자에게 그 파일은 오지 않는다.
그래서 로직은 여기 살고 `scripts/` 에는 진입점만 남는다. 껍데기는 양쪽 레포에
두되, 담긴 것이 그뿐이라 **갈라질 것이 없다.**
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


from fcc_test_platform.idp_deployment_evidence import idp_deployment_errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Platform IdP deployment evidence helper.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    validate = subparsers.add_parser('validate')
    validate.add_argument('manifest')
    template = subparsers.add_parser('template')
    template.add_argument('--provider-key', default='<identity provider key>')
    template.add_argument('--issuer', default='<https issuer URL>')
    template.add_argument('--client-id', default='<frontend client id>')
    template.add_argument('--redirect-uri', default='<production frontend redirect URI>')
    args = parser.parse_args(argv)

    if args.command == 'validate':
        return _validate(Path(args.manifest))
    if args.command == 'template':
        print(json.dumps(_template(args.provider_key, args.issuer, args.client_id, args.redirect_uri), sort_keys=True, indent=2))
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
    issues = [issue.to_dict() for issue in idp_deployment_errors(manifest)]
    print(json.dumps({'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    return 0 if not issues else 1


def _template(provider_key: str, issuer: str, client_id: str, redirect_uri: str) -> dict:
    return {
        'schema_version': 1,
        'evidence_id': '<IdP deployment evidence id>',
        'collected_at': '<ISO-8601 timestamp>',
        'provider_key': provider_key,
        'issuer': issuer,
        'discovery_document': {
            'issuer': issuer,
            'authorization_endpoint': '<https authorization endpoint>',
            'token_endpoint': '<https token endpoint>',
            'jwks_uri': '<https JWKS endpoint>',
            'response_types_supported': ['code'],
            'code_challenge_methods_supported': ['S256'],
        },
        'jwks': {
            'fetched': False,
            'key_count': 0,
            'algorithms': [],
        },
        'clients': [
            {
                'client_id': client_id,
                'public_client': True,
                'client_secret_present': False,
                'pkce_required': True,
                'redirect_uris': [redirect_uri],
                'post_logout_redirect_uris': [redirect_uri],
                'scopes': ['openid', 'profile', '<platform API scope>'],
            }
        ],
        'login_flow': {
            'browser_login_verified': False,
            'redirect_state_verified': False,
            'token_exchange_verified': False,
            'access_token_audience_verified': False,
            'role_claim_verified': False,
            'errors': ['replace with an empty list after login flow passes'],
        },
        'session_persistence': {
            'access_token_storage': 'sessionStorage',
            'local_storage_used': False,
            'logout_clears_session': False,
        },
    }


if __name__ == '__main__':
    raise SystemExit(main())
