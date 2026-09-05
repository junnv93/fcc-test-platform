"""Collect platform IdP deployment evidence from OIDC discovery metadata.

⚠️ 이것은 `scripts/platform_idp_deployment_collect.py` 의 **알맹이**다. `scripts/` 는 패키지가 아니라
**휠이 나르지 못한다** — 이 레인을 핀으로 받는 소비자에게 그 파일은 오지 않는다.
그래서 로직은 여기 살고 `scripts/` 에는 진입점만 남는다. 껍데기는 양쪽 레포에
두되, 담긴 것이 그뿐이라 **갈라질 것이 없다.**
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Callable
from urllib import request


from fcc_test_platform.idp_deployment_evidence import idp_deployment_errors  # noqa: E402


JsonFetcher = Callable[[str, float], dict]


@dataclass(frozen=True)
class ClientRegistration:
    client_id: str
    redirect_uris: list[str]
    post_logout_redirect_uris: list[str]
    scopes: list[str]
    public_client: bool
    client_secret_present: bool
    pkce_required: bool


def main(argv: list[str] | None = None, *, json_fetcher: JsonFetcher | None = None) -> int:
    parser = argparse.ArgumentParser(description='Collect platform IdP deployment evidence.')
    parser.add_argument('--evidence-id', required=True)
    parser.add_argument('--provider-key', required=True)
    parser.add_argument('--issuer', required=True)
    parser.add_argument('--client-id', required=True)
    parser.add_argument('--redirect-uri', action='append', required=True)
    parser.add_argument('--post-logout-redirect-uri', action='append', default=[])
    parser.add_argument('--scope', action='append', required=True)
    parser.add_argument('--client-secret-present', action='store_true')
    parser.add_argument('--public-client', action='store_true')
    parser.add_argument('--pkce-required', action='store_true')
    parser.add_argument('--browser-login-verified', action='store_true')
    parser.add_argument('--redirect-state-verified', action='store_true')
    parser.add_argument('--token-exchange-verified', action='store_true')
    parser.add_argument('--access-token-audience-verified', action='store_true')
    parser.add_argument('--role-claim-verified', action='store_true')
    parser.add_argument('--login-error', action='append', default=[])
    parser.add_argument('--access-token-storage', default='sessionStorage')
    parser.add_argument('--local-storage-used', action='store_true')
    parser.add_argument('--logout-clears-session', action='store_true')
    parser.add_argument('--timeout-seconds', type=float, default=5.0)
    parser.add_argument('--output', required=True)
    parser.add_argument('--require-valid', action='store_true')
    args = parser.parse_args(argv)

    try:
        registration = ClientRegistration(
            client_id=args.client_id,
            redirect_uris=list(args.redirect_uri),
            post_logout_redirect_uris=list(args.post_logout_redirect_uri or args.redirect_uri),
            scopes=list(args.scope),
            public_client=args.public_client,
            client_secret_present=args.client_secret_present,
            pkce_required=args.pkce_required,
        )
        manifest = collect_idp_deployment_evidence(
            evidence_id=args.evidence_id,
            provider_key=args.provider_key,
            issuer=args.issuer,
            client=registration,
            login_flow={
                'browser_login_verified': args.browser_login_verified,
                'redirect_state_verified': args.redirect_state_verified,
                'token_exchange_verified': args.token_exchange_verified,
                'access_token_audience_verified': args.access_token_audience_verified,
                'role_claim_verified': args.role_claim_verified,
                'errors': list(args.login_error),
            },
            session_persistence={
                'access_token_storage': args.access_token_storage,
                'local_storage_used': args.local_storage_used,
                'logout_clears_session': args.logout_clears_session,
            },
            timeout_seconds=args.timeout_seconds,
            json_fetcher=json_fetcher or fetch_json,
        )
    except Exception as exc:
        print(json.dumps({'collected': False, 'error': str(exc)}, sort_keys=True, indent=2), file=sys.stderr)
        return 2

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    issues = [issue.to_dict() for issue in idp_deployment_errors(manifest)]
    print(json.dumps({'collected': True, 'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    if args.require_valid and issues:
        return 1
    return 0


def collect_idp_deployment_evidence(
    *,
    evidence_id: str,
    provider_key: str,
    issuer: str,
    client: ClientRegistration,
    login_flow: dict,
    session_persistence: dict,
    timeout_seconds: float,
    json_fetcher: JsonFetcher,
) -> dict:
    normalized_issuer = issuer.rstrip('/')
    discovery_url = f'{normalized_issuer}/.well-known/openid-configuration'
    discovery = json_fetcher(discovery_url, timeout_seconds)
    jwks_uri = str(discovery.get('jwks_uri') or '')
    jwks = json_fetcher(jwks_uri, timeout_seconds) if jwks_uri else {'keys': []}
    return {
        'schema_version': 1,
        'evidence_id': evidence_id,
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'provider_key': provider_key,
        'issuer': normalized_issuer,
        'discovery_document': {
            'issuer': str(discovery.get('issuer') or ''),
            'authorization_endpoint': str(discovery.get('authorization_endpoint') or ''),
            'token_endpoint': str(discovery.get('token_endpoint') or ''),
            'jwks_uri': jwks_uri,
            'response_types_supported': _string_list(discovery.get('response_types_supported')),
            'code_challenge_methods_supported': _string_list(discovery.get('code_challenge_methods_supported')),
        },
        'jwks': {
            'fetched': bool(jwks_uri),
            'key_count': len(jwks.get('keys') or []) if isinstance(jwks, dict) else 0,
            'algorithms': _jwks_algorithms(jwks),
        },
        'clients': [
            {
                'client_id': client.client_id,
                'public_client': client.public_client,
                'client_secret_present': client.client_secret_present,
                'pkce_required': client.pkce_required,
                'redirect_uris': client.redirect_uris,
                'post_logout_redirect_uris': client.post_logout_redirect_uris,
                'scopes': client.scopes,
            }
        ],
        'login_flow': login_flow,
        'session_persistence': session_persistence,
    }


def fetch_json(url: str, timeout_seconds: float) -> dict:
    req = request.Request(url, headers={'Accept': 'application/json'})
    with request.urlopen(req, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode('utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'{url} did not return a JSON object')
    return payload


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or '').strip()]


def _jwks_algorithms(jwks: dict) -> list[str]:
    algorithms: set[str] = set()
    for key in jwks.get('keys') or []:
        if not isinstance(key, dict):
            continue
        algorithm = str(key.get('alg') or '').strip()
        if algorithm:
            algorithms.add(algorithm)
    return sorted(algorithms)


if __name__ == '__main__':
    raise SystemExit(main())
