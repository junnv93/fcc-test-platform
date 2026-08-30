import ast
import json
import unittest
from pathlib import Path

from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch
from fcc_test_platform.provider_ingestion_plan import build_platform_ingestion_plan
from fcc_test_platform.provider_ingestion_worker import (
    COVERAGE_REFRESH_FAILED,
    COVERAGE_REFRESH_NOT_REQUIRED,
    COVERAGE_REFRESH_SUCCEEDED,
    IngestionRetryPolicy,
    PermanentIngestionError,
    TransientIngestionError,
    execute_platform_ingestion_plan,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_ingestion_worker.py')
POLICY_PATH = project_root / 'docs' / 'platform' / 'provider_ingestion_execution_policy.v1.json'


class FakeWriter:
    def __init__(self, failures=None, refresh_error=None):
        self.failures = list(failures or [])
        self.transactions = []
        self.refresh_error = refresh_error
        self.refresh_calls = 0

    def begin_transaction(self):
        tx = FakeTransaction(self)
        self.transactions.append(tx)
        return tx

    def refresh_coverage_materialized_view(self) -> None:
        """``PlatformIngestionWriter`` 의 둘째 메서드 — 있어야 한다.

        ⚠️ 이 메서드가 없으면 worker 의 ``except Exception``
        (``platform_ingestion_worker.py`` coverage-refresh 블록)이 ``AttributeError`` 를
        삼켜 ``COVERAGE_REFRESH_FAILED`` 라는 **정상 도메인 신호로 위장**한다. 즉 대역의
        결함이 프로덕션 실패처럼 보이고, 그 반대도 참이다 — 진짜 refresh 실패와
        구분되지 않는다. 대역은 드라이버만큼 엄격해야 한다.
        """
        self.refresh_calls += 1
        if self.refresh_error is not None:
            raise self.refresh_error


class FakeTransaction:
    def __init__(self, writer):
        self.writer = writer
        self.upserts = []
        self.attempt_upserts = []
        self.projections = []
        self.isolation_calls = 0
        self.committed = False
        self.rolled_back = False

    def upsert(self, table, record, idempotency_key):
        self.upserts.append((table, dict(record), tuple(idempotency_key)))
        if self.writer.failures:
            failure = self.writer.failures.pop(0)
            if failure is not None:
                raise failure
        return 1

    # ⚠️ 아래 셋은 ``PlatformIngestionTransaction`` 이 선언하는데 이 대역에 **없었다**
    # (독립 평가 2026-08-26, F1 의 후속 실측 — conformance 단언을 붙이자 그 자리에서
    # 드러났다). 대역이 드라이버보다 느슨하면 그 대역을 받는 어떤 봉인도 실 어댑터가
    # 바인딩할 수 없는 객체를 통과시킨다.
    def set_serializable_isolation(self) -> None:
        self.isolation_calls += 1

    def upsert_attempt(self, record, idempotency_key, *, fk_resolution_hint=None):
        self.attempt_upserts.append(
            (dict(record), tuple(idempotency_key),
             dict(fk_resolution_hint) if fk_resolution_hint is not None else None)
        )
        return 1

    def project_results_from_latest_attempt(self, **kwargs):
        self.projections.append(dict(kwargs))
        return 1

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _plan():
    batch = build_platform_ingestion_batch(
        provider_id='provider-uuid',
        session_id='session-uuid',
        result_envelopes=[{
            'result_id': 'result-1',
            'test_name': 'OBW',
            'technology': 'DTS',
            'condition': {},
            'result': {},
        }],
        artifact_metadata=[{
            'artifact_type': 'plot_png',
            'relative_path': 'sessions/s/results/r/plot.png',
            'storage_backend': 'filesystem',
        }],
    )
    return build_platform_ingestion_plan(batch)


class _LatestAttemptTx:
    def __init__(self):
        self.committed = False
        self.rolled_back = False

    def set_serializable_isolation(self):
        pass

    def upsert_attempt(self, record, idempotency_key, *, fk_resolution_hint=None):
        return 1

    def project_results_from_latest_attempt(self, **kwargs):
        return 1

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _RefreshWriter:
    def __init__(self, refresh_exc=None):
        self.refresh_exc = refresh_exc
        self.refresh_called = False

    def begin_transaction(self):
        return _LatestAttemptTx()

    def refresh_coverage_materialized_view(self):
        self.refresh_called = True
        if self.refresh_exc is not None:
            raise self.refresh_exc


def _latest_attempt_plan():
    return {
        'steps': [{
            'order': 1,
            'target_table': 'measurement_attempts',
            'operation': 'upsert',
            'idempotency_key': ['s', 'h', '1'],
            'record': {
                'session_id': 's', 'condition_hash': 'h', 'attempt_number': '1',
                'is_latest': True, 'provider_id': 'p', 'result_json': '{}',
                'test_name': 't', 'technology': 'BLE', 'status': 'completed',
            },
            'fk_resolution_hint': {'provider_result_id': 'r1'},
        }],
    }


class TestCoverageRefreshObservability(unittest.TestCase):
    """Coverage refresh outcome is recorded (was a silent swallow)."""

    def test_refresh_success_is_recorded(self):
        writer = _RefreshWriter()
        result = execute_platform_ingestion_plan(_latest_attempt_plan(), writer)
        self.assertTrue(result.committed)
        self.assertTrue(writer.refresh_called)
        self.assertEqual(result.coverage_refresh, COVERAGE_REFRESH_SUCCEEDED)
        self.assertEqual(result.coverage_refresh_error, '')

    def test_refresh_failure_is_recorded_but_fact_stays_committed(self):
        writer = _RefreshWriter(refresh_exc=RuntimeError('view locked'))
        result = execute_platform_ingestion_plan(_latest_attempt_plan(), writer)
        # The measurement fact is durable regardless of refresh failure.
        self.assertTrue(result.committed)
        self.assertFalse(result.rolled_back)
        self.assertEqual(result.errors, ())
        # ...but the refresh failure is now an auditable signal, not swallowed.
        self.assertEqual(result.coverage_refresh, COVERAGE_REFRESH_FAILED)
        self.assertIn('view locked', result.coverage_refresh_error)

    def test_no_latest_attempt_marks_refresh_not_required(self):
        result = execute_platform_ingestion_plan(_plan(), FakeWriter())
        self.assertTrue(result.committed)
        self.assertEqual(result.coverage_refresh, COVERAGE_REFRESH_NOT_REQUIRED)

    def test_manifest_surfaces_coverage_refresh_and_validates(self):
        from fcc_test_platform.ingestion_execution_evidence import (
            build_ingestion_execution_manifest,
            ingestion_execution_errors,
        )

        writer = _RefreshWriter()
        result = execute_platform_ingestion_plan(_latest_attempt_plan(), writer)
        manifest = build_ingestion_execution_manifest(
            evidence_id='ev', provider_id='p', session_id='s', database_name='db',
            plan=_latest_attempt_plan(), result=result, collected_at='2026-06-13T00:00:00+00:00',
        )
        self.assertEqual(manifest['coverage_refresh'], COVERAGE_REFRESH_SUCCEEDED)
        self.assertEqual(ingestion_execution_errors(manifest), [])
        # An unrecognised token is the only coverage_refresh validation error.
        manifest['coverage_refresh'] = 'bogus'
        codes = {issue.code for issue in ingestion_execution_errors(manifest)}
        self.assertIn('invalid_coverage_refresh', codes)


class TestPlatformIngestionWorker(unittest.TestCase):
    def test_executes_all_upserts_in_one_committed_transaction(self):
        writer = FakeWriter()

        result = execute_platform_ingestion_plan(_plan(), writer)

        self.assertTrue(result.committed)
        self.assertFalse(result.rolled_back)
        self.assertEqual(result.applied_steps, 2)
        self.assertEqual([step.affected_rows for step in result.steps], [1, 1])
        self.assertEqual(len(writer.transactions), 1)
        self.assertTrue(writer.transactions[0].committed)
        self.assertEqual(
            [item[0] for item in writer.transactions[0].upserts],
            ['measurement_results', 'artifacts'],
        )

    def test_retries_transient_failure_from_new_transaction(self):
        writer = FakeWriter([TransientIngestionError('deadlock'), None, None])
        sleeps = []

        result = execute_platform_ingestion_plan(
            _plan(),
            writer,
            retry_policy=IngestionRetryPolicy(max_attempts=2, retry_backoff_seconds=(0.25,)),
            sleeper=sleeps.append,
        )

        self.assertTrue(result.committed)
        self.assertFalse(result.rolled_back)
        self.assertEqual(result.errors, ())
        self.assertEqual(result.retry_errors, ('deadlock',))
        self.assertEqual(result.attempts, 2)
        self.assertEqual(sleeps, [0.25])
        self.assertTrue(writer.transactions[0].rolled_back)
        self.assertTrue(writer.transactions[1].committed)

    def test_does_not_retry_permanent_failure(self):
        writer = FakeWriter([PermanentIngestionError('schema mismatch')])

        result = execute_platform_ingestion_plan(_plan(), writer)

        self.assertFalse(result.committed)
        self.assertTrue(result.rolled_back)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(writer.transactions), 1)
        self.assertIn('schema mismatch', result.errors)

    def test_retry_history_stays_separate_from_final_failure(self):
        writer = FakeWriter([
            TransientIngestionError('deadlock'),
            PermanentIngestionError('schema mismatch'),
        ])

        result = execute_platform_ingestion_plan(
            _plan(),
            writer,
            retry_policy=IngestionRetryPolicy(max_attempts=2),
        )

        self.assertFalse(result.committed)
        self.assertTrue(result.rolled_back)
        self.assertEqual(result.errors, ('schema mismatch',))
        self.assertEqual(result.retry_errors, ('deadlock',))
        self.assertEqual(result.attempts, 2)

    def test_accepts_plan_payload_dict(self):
        writer = FakeWriter()

        result = execute_platform_ingestion_plan(_plan().to_dict(), writer)

        self.assertTrue(result.committed)
        self.assertEqual(result.attempted_steps, 2)
        self.assertEqual(result.to_dict()['steps'][0]['target_table'], 'measurement_results')

    def test_execution_policy_document_matches_worker_contract(self):
        policy = json.loads(POLICY_PATH.read_text(encoding='utf-8'))

        self.assertEqual(policy['owner_repository'], 'fcc-test-platform')
        self.assertIn('Every ingestion plan executes inside a single platform transaction.', policy['transaction_rules'])
        self.assertIn('Retry rollback history is recorded separately from the final transaction outcome.', policy['transaction_rules'])
        self.assertEqual(policy['retry_defaults']['max_attempts'], 3)
        self.assertIn('upsert(table, record, idempotency_key)', policy['port_contract']['transaction_methods'])

    def test_module_import_boundary_excludes_db_and_provider_runtime_dependencies(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden_prefixes = (
            'application.reporting',
            'reporting',
            'infrastructure',
            'fastapi',
            'sqlalchemy',
            'pandas',
            'openpyxl',
            'sqlite3',
            'psycopg',
            'subprocess',
            'os',
        )
        self.assertFalse([
            module for module in imports
            if module == 'infrastructure' or module.startswith(forbidden_prefixes)
        ])


class TestTheDoublesSatisfyTheirPorts(unittest.TestCase):
    """``FakeWriter``/``FakeTransaction`` 이 ``PlatformIngestionWriter``/
    ``PlatformIngestionTransaction`` 계약 전량을 만족하는가.

    ⚠️ **이 봉인이 없던 것이 결함의 기전이었다**(독립 평가 2026-08-26, F1). 이 대역에
    ``refresh_coverage_materialized_view`` 가 없던 동안 worker 의 ``except Exception`` 이
    ``AttributeError`` 를 ``COVERAGE_REFRESH_FAILED`` 라는 **정상 도메인 신호로 위장**했고,
    아무 테스트도 red 가 아니었다. 메서드를 손으로 더하는 것만으로는 그 기전이 남는다 —
    Port 가 내일 세 번째 메서드를 얻으면 위장이 그대로 돌아온다. 이 단언이 그것을 막는다.
    """

    def test_fake_writer_satisfies_the_writer_port(self):
        from domain.ports.output.platform_ingestion_port import PlatformIngestionWriter

        self.assertIsInstance(FakeWriter(), PlatformIngestionWriter)

    def test_fake_transaction_satisfies_the_transaction_port(self):
        from domain.ports.output.platform_ingestion_port import PlatformIngestionTransaction

        self.assertIsInstance(FakeTransaction(FakeWriter()), PlatformIngestionTransaction)


if __name__ == '__main__':
    unittest.main()
