"""Validate and template platform extraction evidence manifests.

⚠️ 이것은 `scripts/platform_extraction_evidence.py` 의 **알맹이**다. `scripts/` 는 패키지가 아니라
**휠이 나르지 못한다** — 이 레인을 핀으로 받는 소비자에게 그 파일은 오지 않는다.
그래서 로직은 여기 살고 `scripts/` 에는 진입점만 남는다. 껍데기는 양쪽 레포에
두되, 담긴 것이 그뿐이라 **갈라질 것이 없다.**
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


from fcc_test_platform.extraction_evidence import (
    EXTRACTION_REQUIRED_REPOSITORIES,
    extraction_package_errors,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Platform extraction evidence helper.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    validate = subparsers.add_parser('validate')
    validate.add_argument('manifest')
    validate.add_argument('--extraction-manifest', default='')
    template = subparsers.add_parser('template')
    template.add_argument('--evidence-id', default='<extraction evidence id>')
    args = parser.parse_args(argv)

    if args.command == 'validate':
        return _validate(Path(args.manifest), _optional_path(args.extraction_manifest))
    if args.command == 'template':
        print(json.dumps(_template(args.evidence_id), sort_keys=True, indent=2))
        return 0
    return 2


def _validate(path: Path, extraction_manifest_path: Path | None) -> int:
    try:
        manifest = _read_json(path)
        extraction_manifest = _read_json(extraction_manifest_path) if extraction_manifest_path else None
    except Exception as exc:
        print(json.dumps({
            'valid': False,
            'issues': [{
                'code': 'read_error',
                'path': str(getattr(exc, 'filename', '') or path),
                'message': str(exc),
            }],
        }, sort_keys=True, indent=2))
        return 2
    issues = [
        issue.to_dict()
        for issue in extraction_package_errors(manifest, extraction_manifest=extraction_manifest)
    ]
    print(json.dumps({'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    return 0 if not issues else 1


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _optional_path(value: str) -> Path | None:
    text = str(value or '').strip()
    return Path(text) if text else None


def _template(evidence_id: str) -> dict:
    return {
        'schema_version': 1,
        'evidence_id': evidence_id,
        'collected_at': '<ISO-8601 timestamp>',
        'manifest_path': 'docs/api/headless_contract_extraction_manifest.v1.json',
        'compatible': False,
        'issues': ['replace this placeholder with a compatible extraction plan'],
        'repositories': {
            repo_name: {
                'target_repository': repo_name,
                'target_ref': '<commit sha or release ref>',
                'extracted_at': '<ISO-8601 timestamp>',
                'package_compatible': False,
                'extracted': False,
                'entries': [
                    {
                        'current_path': '<source path from extraction manifest>',
                        'future_path': '<safe target path from extraction manifest>',
                        'kind': '<entry kind>',
                        'byte_size': 0,
                        'source_sha256': '<source sha256>',
                        'destination_sha256': '<destination sha256>',
                        'copied': False,
                    }
                ],
            }
            for repo_name in EXTRACTION_REQUIRED_REPOSITORIES
        },
    }


if __name__ == '__main__':
    raise SystemExit(main())
