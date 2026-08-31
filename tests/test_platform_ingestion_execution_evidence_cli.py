import ast
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

from tests.test_platform_ingestion_execution_evidence import _valid_manifest
# ⚠️ 2026-08-31 — 알맹이가 패키지로 갔다(`scripts/` 는 휠이 나르지 못한다).
#    `scripts/…` 는 이제 진입점 3줄뿐이라 patch 대상이 없다. 테스트는
#    **로직이 사는 곳**을 잡아야 한다 — 껍데기를 잡으면 「이름이 사라졌다」와
#    「로직이 사라졌다」가 같은 AttributeError 가 된다.
import fcc_test_platform.ingestion_execution_evidence_cli as cli


project_root = Path(__file__).parent.parent
CLI_PATH = project_root / 'scripts' / 'platform_ingestion_execution_evidence.py'


class TestPlatformIngestionExecutionEvidenceCli(unittest.TestCase):
    def test_valid_manifest_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'ingestion-execution.json'
            path.write_text(json.dumps(_valid_manifest()), encoding='utf-8')

            completed = _run_cli('validate', str(path))

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertEqual(json.loads(completed.stdout), {'valid': True, 'issues': []})

    def test_invalid_manifest_returns_one(self):
        manifest = _valid_manifest()
        manifest['transaction_committed'] = False
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'ingestion-execution.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')

            completed = _run_cli('validate', str(path))

        self.assertEqual(completed.returncode, 1)
        self.assertIn('transaction_not_committed', {issue['code'] for issue in json.loads(completed.stdout)['issues']})

    def test_read_error_returns_two(self):
        completed = _run_cli('validate', 'missing.json')

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)['issues'][0]['code'], 'read_error')

    def test_template_does_not_claim_execution(self):
        completed = _run_cli('template', '--evidence-id', 'ingest-template', '--session-id', 'session-1')

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['evidence_id'], 'ingest-template')
        self.assertFalse(payload['executed'])
        self.assertFalse(payload['transaction_committed'])
        self.assertEqual(payload['attempted_steps'], 0)
        self.assertEqual(payload['applied_steps'], 0)
        self.assertEqual(payload['retry_errors'], [])
        self.assertIn('replace with an empty list after execution succeeds', payload['errors'])

    def test_execute_runs_plan_through_postgres_writer_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plan_path = root / 'plan.json'
            output_path = root / 'nested' / 'evidence' / 'manifest.json'
            plan_path.write_text(json.dumps(_plan_payload()), encoding='utf-8')
            connection = FakeConnection()

            with patch.object(cli, '_psycopg_connection_factory', return_value=lambda: connection):
                with redirect_stdout(io.StringIO()):
                    exit_code = cli.main([
                        'execute',
                        '--plan', str(plan_path),
                        '--dsn', 'postgresql://example.invalid/fcc_platform',
                        '--provider-id', 'fcc-unlicensed-conducted',
                        '--session-id', 'session-uuid',
                        '--database-name', 'fcc_platform',
                        '--evidence-id', 'ingest-live-1',
                        '--collected-at', '2026-05-15T10:55:00+09:00',
                        '--output', str(output_path),
                    ])

            self.assertEqual(exit_code, 0)
            manifest = json.loads(output_path.read_text(encoding='utf-8'))
        self.assertTrue(manifest['transaction_committed'])
        self.assertEqual(manifest['writer_adapter'], 'postgres')
        self.assertEqual(manifest['attempted_steps'], 1)
        self.assertEqual(manifest['applied_steps'], 1)
        self.assertEqual(manifest['retry_errors'], [])
        self.assertEqual(manifest['steps'][0]['affected_rows'], 1)
        self.assertTrue(connection.committed)

    def test_cli_import_boundary_excludes_runtime_dependencies(self):
        tree = ast.parse(CLI_PATH.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden = {'requests', 'httpx', 'fastapi', 'subprocess', 'sqlite3', 'sqlalchemy', 'psycopg', 'os'}
        self.assertFalse(sorted(forbidden.intersection(imports)))


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, 'scripts/platform_ingestion_execution_evidence.py', *args],
        cwd=str(project_root),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def _plan_payload() -> dict:
    return {
        'steps': [{
            'order': 1,
            'target_table': 'measurement_results',
            'operation': 'upsert',
            'idempotency_key': ['provider-uuid', 'result-1'],
            'record': {
                'provider_id': 'provider-uuid',
                'session_id': 'session-uuid',
                'provider_result_id': 'result-1',
                'test_name': 'OBW',
                'technology': 'DTS',
                'condition_json': '{}',
                'result_json': '{}',
            },
        }],
    }


class FakeCursor:
    rowcount = 1

    def execute(self, statement, parameters):
        self.statement = statement
        self.parameters = parameters

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.committed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


if __name__ == '__main__':
    unittest.main()
