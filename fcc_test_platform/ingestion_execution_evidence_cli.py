"""`scripts/platform_ingestion_execution_evidence.py` 의 **알맹이** (2026-08-31).

⚠️ `scripts/` 는 패키지가 아니라 **휠이 나르지 못한다** — 이 레인을 핀으로
받는 소비자(모노레포)에게 그 파일은 오지 않는다. 그래서 로직은 여기 살고
`scripts/` 에는 **3줄 진입점**만 남는다. 껍데기는 양쪽 레포에 둘 다 두되,
거기 담긴 것이 3줄뿐이라 **드리프트할 것이 없다.**
"""
from __future__ import annotations

"""Validate and template platform ingestion execution evidence manifests."""
import argparse
import importlib
import json
from pathlib import Path
import sys
from fcc_test_platform.ingestion_execution_evidence import (
    build_ingestion_execution_manifest,
    ingestion_execution_errors,
)
from fcc_test_platform.provider_ingestion_plan import INGESTION_TABLE_ORDER
from fcc_test_platform.provider_ingestion_worker import IngestionRetryPolicy, execute_platform_ingestion_plan
from fcc_test_platform.postgres_ingestion_writer import PostgresIngestionWriter
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Platform ingestion execution evidence helper.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    validate = subparsers.add_parser('validate')
    validate.add_argument('manifest')
    template = subparsers.add_parser('template')
    template.add_argument('--evidence-id', default='<ingestion execution evidence id>')
    template.add_argument('--provider-id', default='fcc-unlicensed-conducted')
    template.add_argument('--session-id', default='<platform session id>')
    template.add_argument('--database-name', default='fcc_platform')
    execute = subparsers.add_parser('execute')
    execute.add_argument('--plan', required=True)
    execute.add_argument('--dsn', required=True)
    execute.add_argument('--provider-id', required=True)
    execute.add_argument('--session-id', required=True)
    execute.add_argument('--database-name', required=True)
    execute.add_argument('--evidence-id', required=True)
    execute.add_argument('--collected-at', default='')
    execute.add_argument('--max-attempts', type=int, default=3)
    execute.add_argument('--output', default='')
    args = parser.parse_args(argv)

    if args.command == 'validate':
        return _validate(Path(args.manifest))
    if args.command == 'template':
        print(json.dumps(_template(args.evidence_id, args.provider_id, args.session_id, args.database_name), sort_keys=True, indent=2))
        return 0
    if args.command == 'execute':
        return _execute(args)
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
    issues = [issue.to_dict() for issue in ingestion_execution_errors(manifest)]
    print(json.dumps({'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    return 0 if not issues else 1
def _template(evidence_id: str, provider_id: str, session_id: str, database_name: str) -> dict:
    return {
        'schema_version': 1,
        'evidence_id': evidence_id,
        'collected_at': '<ISO-8601 timestamp>',
        'provider_id': provider_id,
        'session_id': session_id,
        'database_name': database_name,
        'writer_adapter': 'postgres',
        'plan_sha256': '<sha256>',
        'executed': False,
        'transaction_committed': False,
        'transaction_rolled_back': False,
        'attempts': 0,
        'attempted_steps': 0,
        'applied_steps': 0,
        'errors': ['replace with an empty list after execution succeeds'],
        'retry_errors': [],
        'steps': [
            {
                'order': index + 1,
                'target_table': table,
                'operation': 'upsert',
                'idempotency_key': ['<idempotency key values>'],
                'affected_rows': 0,
            }
            for index, table in enumerate(INGESTION_TABLE_ORDER)
        ],
    }
def _execute(args: argparse.Namespace) -> int:
    try:
        output_path = Path(args.output) if args.output else None
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        plan = _read_json(Path(args.plan))
        writer = PostgresIngestionWriter(_psycopg_connection_factory(args.dsn))
        result = execute_platform_ingestion_plan(
            plan,
            writer,
            retry_policy=IngestionRetryPolicy(max_attempts=args.max_attempts),
        )
        manifest = build_ingestion_execution_manifest(
            evidence_id=args.evidence_id,
            provider_id=args.provider_id,
            session_id=args.session_id,
            database_name=args.database_name,
            plan=plan,
            result=result,
            collected_at=args.collected_at,
        )
    except Exception as exc:
        print(json.dumps({
            'executed': False,
            'issues': [{
                'code': 'execution_error',
                'path': str(getattr(exc, 'filename', '') or ''),
                'message': str(exc),
            }],
        }, sort_keys=True, indent=2))
        return 2

    payload = json.dumps(manifest, sort_keys=True, indent=2)
    if output_path is not None:
        output_path.write_text(payload + '\n', encoding='utf-8')
    else:
        print(payload)
    return 0 if not ingestion_execution_errors(manifest) else 1
def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))
def _psycopg_connection_factory(dsn: str):
    def connect():
        psycopg = importlib.import_module('psycopg')
        return psycopg.connect(dsn)

    return connect
