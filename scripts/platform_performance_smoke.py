"""Validate and template platform performance smoke evidence manifests."""
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

from fcc_test_platform.performance_smoke import (
    PERFORMANCE_SMOKE_ROUTES,
    performance_smoke_errors,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Platform performance smoke evidence helper.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    validate = subparsers.add_parser('validate')
    validate.add_argument('manifest')
    template = subparsers.add_parser('template')
    template.add_argument('--provider-id', default='fcc-unlicensed-conducted')
    template.add_argument('--benchmark-id', default='<benchmark id>')
    template.add_argument('--target-base-url', default='<target base URL>')
    template.add_argument('--max-p95-ms', type=float, default=500.0)
    args = parser.parse_args(argv)

    if args.command == 'validate':
        return _validate(Path(args.manifest))
    if args.command == 'template':
        print(json.dumps(
            _template(args.provider_id, args.benchmark_id, args.target_base_url, args.max_p95_ms),
            sort_keys=True,
            indent=2,
        ))
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
    issues = [issue.to_dict() for issue in performance_smoke_errors(manifest)]
    print(json.dumps({'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    return 0 if not issues else 1


def _template(provider_id: str, benchmark_id: str, target_base_url: str, max_p95_ms: float) -> dict:
    return {
        'schema_version': 1,
        'provider_id': provider_id,
        'benchmark_id': benchmark_id,
        'collected_at': '<ISO-8601 timestamp>',
        'target_base_url': target_base_url,
        'iterations': 0,
        'workers': 0,
        'max_p95_ms': max_p95_ms,
        'compatible': False,
        'within_budget': False,
        'total_requests': 0,
        'total_failures': 0,
        'routes': [
            {
                'route': route,
                'requests': 0,
                'successes': 0,
                'failures': 0,
                'min_ms': 0,
                'mean_ms': 0,
                'p95_ms': max_p95_ms + 1,
                'max_ms': 0,
            }
            for route in PERFORMANCE_SMOKE_ROUTES
        ],
    }


if __name__ == '__main__':
    raise SystemExit(main())
