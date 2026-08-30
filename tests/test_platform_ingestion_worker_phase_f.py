"""FE-P0c Phase F invariants — worker drives the FE-P0a ingestion contract.

External review (2026-05-26) identified 3 P0/P1 defects:

  1. ``platform_ingestion_worker.execute_platform_ingestion_plan`` only called
     generic ``tx.upsert(...)``, bypassing ``upsert_attempt`` +
     ``project_results_from_latest_attempt``. Phase A/B/C SSOT changes had no
     runtime effect on the production execution path.
  2. ``SET TRANSACTION ISOLATION LEVEL SERIALIZABLE`` was issued inside
     ``upsert_attempt``. Plan order writes ``measurement_results`` first, so by
     the time attempts arrived a SQL statement had already executed —
     PostgreSQL rejects SET TRANSACTION at that point.
  3. ``REFRESH MATERIALIZED VIEW CONCURRENTLY`` was issued inside the same
     transaction block (psycopg default starts implicit transaction on execute)
     — PostgreSQL disallows REFRESH CONCURRENTLY inside a transaction.

This module pins the Phase F worker contract: worker pre-scans the plan,
sets SERIALIZABLE BEFORE the first statement, dispatches attempts to
``upsert_attempt`` + projection, and triggers ``refresh_coverage_materialized_view``
ON A SEPARATE AUTOCOMMIT CONNECTION after commit.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))


class _RecordingTransaction:
    """Phase F worker contract — records the dispatch sequence."""

    def __init__(self, owner: '_RecordingWriter'):
        self._owner = owner
        self.dispatch_log: list[tuple[str, dict]] = []
        self.committed = False
        self.rolled_back = False

    def set_serializable_isolation(self):
        self.dispatch_log.append(('set_serializable_isolation', {}))

    def upsert(self, table, record, idempotency_key):
        self.dispatch_log.append(('upsert', {'table': table, 'key': tuple(idempotency_key)}))
        return 1

    def upsert_attempt(self, record, idempotency_key, *, fk_resolution_hint=None):
        self.dispatch_log.append((
            'upsert_attempt',
            {'key': tuple(idempotency_key), 'hint': dict(fk_resolution_hint or {})},
        ))
        return 1

    def project_results_from_latest_attempt(self, **kwargs):
        self.dispatch_log.append(('project_results_from_latest_attempt', dict(kwargs)))
        return 1

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _RecordingWriter:
    def __init__(self, *, refresh_raises: str | None = None):
        self.transactions: list[_RecordingTransaction] = []
        self.refresh_invocations = 0
        self._refresh_raises = refresh_raises

    def begin_transaction(self):
        tx = _RecordingTransaction(self)
        self.transactions.append(tx)
        return tx

    def refresh_coverage_materialized_view(self):
        self.refresh_invocations += 1
        if self._refresh_raises:
            raise RuntimeError(self._refresh_raises)


def _attempt_envelope(**overrides):
    base = {
        'result_id': 'r1',
        'test_name': 'OBW',
        'technology': 'BT',
        'condition_hash': 'CH-1',
        'attempt_number': 1,
        'is_latest': True,
        'operator': 'op-1',
        'project_id': 'proj-uuid',
        'status': 'completed',
        'verdict': 'Pass',
        'margin': '0.5',
        'result': {'result1': '12.5'},
        'idempotency_key': 'IDEM-1',
        'provenance': {'recorded_by': 'op-1'},
        'measured_at': '2026-05-26T00:00:00Z',
    }
    base.update(overrides)
    return base


def _build_plan_with_latest_attempt():
    from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch
    from fcc_test_platform.provider_ingestion_plan import build_platform_ingestion_plan

    result_envelopes = [{
        'result_id': 'r1',
        'test_name': 'OBW',
        'technology': 'BT',
        'condition': {},
        'result': {'result1': '12.5'},
        'verdict': 'Pass',
        'measured_at': '2026-05-26T00:00:00Z',
        'condition_hash': 'CH-1',
        'project_id': 'proj-uuid',
        'operator': 'op-1',
    }]
    batch = build_platform_ingestion_batch(
        provider_id='provider-uuid',
        session_id='session-uuid',
        result_envelopes=result_envelopes,
        attempt_envelopes=[_attempt_envelope()],
    )
    return build_platform_ingestion_plan(batch)


def _build_artifacts_only_plan():
    """Plan with only generic upsert steps (no is_latest attempts)."""
    from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch
    from fcc_test_platform.provider_ingestion_plan import build_platform_ingestion_plan

    batch = build_platform_ingestion_batch(
        provider_id='provider-uuid',
        session_id='session-uuid',
        result_envelopes=[{
            'result_id': 'r1',
            'test_name': 'OBW',
            'technology': 'BT',
            'condition': {},
            'result': {},
        }],
        artifact_metadata=[{
            'artifact_type': 'plot_png',
            'relative_path': 'sessions/s/plot.png',
        }],
    )
    return build_platform_ingestion_plan(batch)


class TestWorkerDispatchesAttemptCalls(unittest.TestCase):
    """P0 #1 fix — worker calls upsert_attempt + projection for is_latest=true."""

    def test_attempt_step_dispatches_upsert_attempt_not_generic_upsert(self):
        from fcc_test_platform.provider_ingestion_worker import execute_platform_ingestion_plan

        writer = _RecordingWriter()
        plan = _build_plan_with_latest_attempt()
        execute_platform_ingestion_plan(plan, writer)

        tx = writer.transactions[0]
        call_names = [name for name, _ in tx.dispatch_log]
        # Phase F worker dispatch — attempt-specific methods called instead of
        # bare ``upsert('measurement_attempts', ...)``.
        self.assertIn('upsert_attempt', call_names)
        self.assertIn('project_results_from_latest_attempt', call_names)
        self.assertNotIn(
            'upsert',
            [name for name, args in tx.dispatch_log
             if name == 'upsert' and args.get('table') == 'measurement_attempts'],
        )

    def test_attempt_dispatch_passes_fk_resolution_hint(self):
        from fcc_test_platform.provider_ingestion_worker import execute_platform_ingestion_plan

        writer = _RecordingWriter()
        plan = _build_plan_with_latest_attempt()
        execute_platform_ingestion_plan(plan, writer)

        tx = writer.transactions[0]
        attempt_call = next(
            args for name, args in tx.dispatch_log if name == 'upsert_attempt'
        )
        self.assertEqual(attempt_call['hint'].get('provider_result_id'), 'r1')

    def test_projection_call_carries_canonical_fields(self):
        from fcc_test_platform.provider_ingestion_worker import execute_platform_ingestion_plan

        writer = _RecordingWriter()
        plan = _build_plan_with_latest_attempt()
        execute_platform_ingestion_plan(plan, writer)

        tx = writer.transactions[0]
        projection_call = next(
            args for name, args in tx.dispatch_log if name == 'project_results_from_latest_attempt'
        )
        self.assertEqual(projection_call['provider_id'], 'provider-uuid')
        self.assertEqual(projection_call['provider_result_id'], 'r1')
        self.assertEqual(projection_call['condition_hash'], 'CH-1')
        self.assertEqual(projection_call['operator'], 'op-1')
        self.assertEqual(projection_call['verdict'], 'Pass')


class TestSerializableTiming(unittest.TestCase):
    """P0/P1 #2 fix — SERIALIZABLE issued BEFORE any other statement."""

    def test_serializable_is_first_call_when_plan_has_latest_attempt(self):
        from fcc_test_platform.provider_ingestion_worker import execute_platform_ingestion_plan

        writer = _RecordingWriter()
        plan = _build_plan_with_latest_attempt()
        execute_platform_ingestion_plan(plan, writer)

        tx = writer.transactions[0]
        first_call_name = tx.dispatch_log[0][0]
        self.assertEqual(
            first_call_name, 'set_serializable_isolation',
            'Phase F constraint — set_serializable_isolation MUST be the first '
            'call on the transaction (PostgreSQL rejects SET TRANSACTION after '
            'the first statement). Plan order writes measurement_results before '
            'measurement_attempts so the worker must call it BEFORE the loop.',
        )

    def test_no_serializable_when_plan_has_only_generic_upserts(self):
        from fcc_test_platform.provider_ingestion_worker import execute_platform_ingestion_plan

        writer = _RecordingWriter()
        plan = _build_artifacts_only_plan()
        execute_platform_ingestion_plan(plan, writer)

        tx = writer.transactions[0]
        names = [name for name, _ in tx.dispatch_log]
        # No SERIALIZABLE cost for pure artifact/report batches
        self.assertNotIn('set_serializable_isolation', names)
        self.assertNotIn('upsert_attempt', names)


class TestPostCommitRefreshGating(unittest.TestCase):
    """P1 #3 fix — REFRESH on a SEPARATE autocommit connection, gated on attempt write."""

    def test_refresh_invoked_when_plan_has_latest_attempt(self):
        from fcc_test_platform.provider_ingestion_worker import execute_platform_ingestion_plan

        writer = _RecordingWriter()
        plan = _build_plan_with_latest_attempt()
        execute_platform_ingestion_plan(plan, writer)

        self.assertEqual(writer.refresh_invocations, 1)
        # Refresh happens AFTER commit
        self.assertTrue(writer.transactions[0].committed)

    def test_refresh_skipped_for_artifacts_only_plan(self):
        from fcc_test_platform.provider_ingestion_worker import execute_platform_ingestion_plan

        writer = _RecordingWriter()
        plan = _build_artifacts_only_plan()
        execute_platform_ingestion_plan(plan, writer)

        self.assertEqual(writer.refresh_invocations, 0)

    def test_refresh_failure_does_not_revoke_committed_result(self):
        from fcc_test_platform.provider_ingestion_worker import execute_platform_ingestion_plan

        writer = _RecordingWriter(refresh_raises='materialized view busy')
        plan = _build_plan_with_latest_attempt()
        result = execute_platform_ingestion_plan(plan, writer)

        # Commit happened; refresh raised but worker swallowed
        self.assertTrue(result.committed)
        self.assertFalse(result.rolled_back)
        self.assertEqual(writer.refresh_invocations, 1)


if __name__ == '__main__':
    unittest.main()
