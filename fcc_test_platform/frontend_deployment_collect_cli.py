"""Collect platform frontend deployment evidence from a deployed shell.

⚠️ 이것은 `scripts/platform_frontend_deployment_collect.py` 의 **알맹이**다. `scripts/` 는 패키지가 아니라
**휠이 나르지 못한다** — 이 레인을 핀으로 받는 소비자에게 그 파일은 오지 않는다.
그래서 로직은 여기 살고 `scripts/` 에는 진입점만 남는다. 껍데기는 양쪽 레포에
두되, 담긴 것이 그뿐이라 **갈라질 것이 없다.**
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import ssl
import sys
from typing import Callable
from urllib import request
from urllib.parse import urlparse


from fcc_test_platform.frontend_deployment_evidence import frontend_deployment_errors  # noqa: E402


UrlProbe = Callable[[str, float], 'ProbeResult']


@dataclass(frozen=True)
class ProbeResult:
    url: str
    ok: bool
    status_code: int
    tls_valid: bool
    headers: dict[str, str]
    error: str = ''


def main(argv: list[str] | None = None, *, url_probe: UrlProbe | None = None) -> int:
    parser = argparse.ArgumentParser(description='Collect frontend deployment evidence.')
    parser.add_argument('--evidence-id', required=True)
    parser.add_argument('--app-url', required=True)
    parser.add_argument('--backend-base-url', required=True)
    parser.add_argument('--hosting-provider', required=True)
    parser.add_argument('--build-version', required=True)
    parser.add_argument('--build-root', required=True)
    parser.add_argument('--environment-name', required=True)
    parser.add_argument('--environment-variable', action='append', default=[], help='NAME=VALUE; secret-like values are redacted')
    parser.add_argument('--immutable-asset-url', action='append', default=[], help='deployed hashed asset URL expected to use immutable caching')
    parser.add_argument('--secret-scan-json', default='', help='optional JSON object with status and findings')
    parser.add_argument('--timeout-seconds', type=float, default=5.0)
    parser.add_argument('--output', required=True)
    parser.add_argument('--require-valid', action='store_true')
    args = parser.parse_args(argv)

    try:
        manifest = collect_frontend_deployment_evidence(
            evidence_id=args.evidence_id,
            app_url=args.app_url,
            backend_base_url=args.backend_base_url,
            hosting_provider=args.hosting_provider,
            build_version=args.build_version,
            build_root=Path(args.build_root),
            environment_name=args.environment_name,
            environment_variables=_parse_environment_variables(args.environment_variable),
            immutable_asset_urls=list(args.immutable_asset_url),
            secret_scan_json=Path(args.secret_scan_json) if args.secret_scan_json else None,
            timeout_seconds=args.timeout_seconds,
            url_probe=url_probe or probe_url,
        )
    except Exception as exc:
        print(json.dumps({'collected': False, 'error': str(exc)}, sort_keys=True, indent=2), file=sys.stderr)
        return 2

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    issues = [issue.to_dict() for issue in frontend_deployment_errors(manifest)]
    print(json.dumps({'collected': True, 'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    if args.require_valid and issues:
        return 1
    return 0


def collect_frontend_deployment_evidence(
    *,
    evidence_id: str,
    app_url: str,
    backend_base_url: str,
    hosting_provider: str,
    build_version: str,
    build_root: Path,
    environment_name: str,
    environment_variables: dict[str, str],
    immutable_asset_urls: list[str],
    secret_scan_json: Path | None,
    timeout_seconds: float,
    url_probe: UrlProbe,
) -> dict:
    if not build_root.is_dir():
        raise FileNotFoundError(str(build_root))
    app_probe = url_probe(app_url, timeout_seconds)
    backend_probe = url_probe(backend_base_url, timeout_seconds)
    asset_probes = [url_probe(url, timeout_seconds) for url in immutable_asset_urls]
    html_cache_control = _header(app_probe.headers, 'cache-control')
    immutable_assets = bool(asset_probes) and all(_is_immutable_cache(_header(probe.headers, 'cache-control')) for probe in asset_probes)
    secret_scan = _load_secret_scan(secret_scan_json) if secret_scan_json else _scan_build_for_secrets(build_root)
    return {
        'schema_version': 1,
        'evidence_id': evidence_id,
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'app_url': app_url,
        'backend_base_url': backend_base_url,
        'hosting_provider': hosting_provider,
        'build_version': build_version,
        'build_sha256': _sha256_tree(build_root),
        'deployed': app_probe.ok and backend_probe.ok,
        'tls_valid': app_probe.tls_valid and backend_probe.tls_valid,
        'asset_cache_policy': {
            'immutable_assets': immutable_assets,
            'html_cache_control': html_cache_control,
        },
        'environment': {
            'name': environment_name,
            'backend_base_url': backend_base_url,
            'variables': _environment_variable_records(environment_variables),
        },
        'secret_scan': secret_scan,
        'probe_results': {
            'app': _probe_record(app_probe),
            'backend': _probe_record(backend_probe),
            'immutable_assets': [_probe_record(probe) for probe in asset_probes],
        },
    }


def probe_url(url: str, timeout_seconds: float) -> ProbeResult:
    tls_valid = urlparse(url).scheme == 'https'
    try:
        req = request.Request(url, method='GET')
        with request.urlopen(req, timeout=timeout_seconds) as response:
            return ProbeResult(
                url=url,
                ok=200 <= int(response.status) < 400,
                status_code=int(response.status),
                tls_valid=tls_valid,
                headers={str(key).lower(): str(value) for key, value in response.headers.items()},
            )
    except ssl.SSLError as exc:
        return ProbeResult(url=url, ok=False, status_code=0, tls_valid=False, headers={}, error=str(exc))
    except Exception as exc:
        return ProbeResult(url=url, ok=False, status_code=0, tls_valid=tls_valid, headers={}, error=str(exc))


def _sha256_tree(root: Path) -> str:
    files = sorted(path for path in root.rglob('*') if path.is_file())
    if not files:
        raise ValueError('build-root must contain at least one file')
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode('utf-8'))
        digest.update(b'\0')
        digest.update(_sha256_file(path).encode('ascii'))
        digest.update(b'\0')
        digest.update(str(path.stat().st_size).encode('ascii'))
        digest.update(b'\0')
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _scan_build_for_secrets(root: Path) -> dict:
    findings: list[dict[str, str]] = []
    pattern = re.compile(r'(?i)(secret|token|password|api[_-]?key)\s*[:=]\s*[\'"][^\'"]{8,}[\'"]')
    for path in sorted(item for item in root.rglob('*') if item.is_file()):
        if path.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        if pattern.search(text):
            findings.append({
                'path': path.relative_to(root).as_posix(),
                'kind': 'secret_like_literal',
            })
    return {
        'status': 'pass' if not findings else 'fail',
        'findings': findings,
    }


def _load_secret_scan(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError('secret-scan-json must contain an object')
    return payload


def _environment_variable_records(values: dict[str, str]) -> list[dict[str, str]]:
    return [
        {
            'name': name,
            'value': '<redacted>' if _is_secret_name(name) else value,
        }
        for name, value in sorted(values.items())
    ]


def _parse_environment_variables(values: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        name, separator, raw = value.partition('=')
        if not separator or not name.strip():
            raise ValueError('environment-variable must use NAME=VALUE')
        parsed[name.strip()] = raw
    return parsed


def _is_secret_name(value: str) -> bool:
    normalized = value.lower().replace('-', '_')
    return any(token in normalized for token in ('secret', 'token', 'password', 'passwd', 'private_key', 'client_secret', 'api_key'))


def _is_immutable_cache(value: str) -> bool:
    normalized = value.lower()
    return 'immutable' in normalized and 'max-age=' in normalized


def _header(headers: dict[str, str], key: str) -> str:
    return str(headers.get(key.lower()) or headers.get(key) or '').strip()


def _probe_record(probe: ProbeResult) -> dict:
    return {
        'url': probe.url,
        'ok': probe.ok,
        'status_code': probe.status_code,
        'tls_valid': probe.tls_valid,
        'cache_control': _header(probe.headers, 'cache-control'),
        'error': probe.error,
    }


if __name__ == '__main__':
    raise SystemExit(main())
