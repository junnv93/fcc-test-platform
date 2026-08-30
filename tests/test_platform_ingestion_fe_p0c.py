"""FE-P0c invariants — ingestion writer extension for FE-P0a central columns.

Self-audit axes:
    1. payload→central record mapping (5 columns) is preserved byte-identical.
    2. operator reaches BOTH ``operator`` and ``recorded_by`` central columns
       from the same source (FE-P0b req5).
    3. condition_hash is propagated verbatim (negative case: a different hash
       in the central record means central recomputed — must NEVER happen).
    4. coverage_by_condition_hash has zero direct write paths in the ingestion
       layer (AST guard) — it is a materialized VIEW (FE-P0a).
    5. is_latest derivation: within a batch, only the row with MAX(attempt_number)
       per (project_id, condition_hash) group retains is_latest=True.
    6. idempotency: same (session_id, condition_hash, attempt_number) cannot
       appear twice in a single batch plan.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
import unittest
from pathlib import Path


project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))


def _attempt_envelope(**overrides):
    base = {
        'result_id': 'provider-result-1',
        'test_name': 'OBW',
        'technology': 'DTS',
        'condition_hash': 'hash-abc-123',
        'attempt_number': 1,
        'is_latest': True,
        'operator': 'station-pc-7',
        'status': 'completed',
        'verdict': 'Pass',
        'margin': '0.5',
        'result': {'result1': '12.5', 'unit': 'MHz'},
        'idempotency_key': 'idem-abc-1',
        'provenance': {'recorded_by': 'station-pc-7'},
        'project_id': 'proj-uuid-1',
        'measured_at': '2026-05-26T00:00:00Z',
    }
    base.update(overrides)
    return base


class TestMappingPreservesFiveCentralColumns(unittest.TestCase):
    """Axis 1 — 5 FE-P0a central columns reach the central record."""

    def test_measurement_results_record_includes_three_optional_columns(self):
        from fcc_test_platform.provider_ingestion import map_measurement_result_record

        record = map_measurement_result_record(
            provider_id='provider-uuid',
            session_id='session-uuid',
            envelope={
                'result_id': 'r1',
                'test_name': 'OBW',
                'technology': 'DTS',
                'condition': {'channel': '36'},
                'result': {'value': '12.5'},
                'condition_hash': 'hash-abc',
                'project_id': 'proj-uuid-1',
                'operator': 'op-1',
            },
        )

        self.assertEqual(record['condition_hash'], 'hash-abc')
        self.assertEqual(record['project_id'], 'proj-uuid-1')
        self.assertEqual(record['operator'], 'op-1')

    def test_legacy_nine_column_envelope_yields_byte_identical_record(self):
        """Axis 1 negative — without FE-P0a keys the result is the legacy 9-column shape."""
        from fcc_test_platform.provider_ingestion import map_measurement_result_record

        legacy_record = map_measurement_result_record(
            provider_id='provider-uuid',
            session_id='session-uuid',
            envelope={
                'result_id': 'r1',
                'test_name': 'OBW',
                'technology': 'DTS',
                'condition': {'channel': '36'},
                'result': {'value': '12.5'},
                'verdict': 'Pass',
                'measured_at': '2026-05-14T00:00:00Z',
            },
        )

        self.assertEqual(set(legacy_record.keys()), {
            'provider_id', 'session_id', 'provider_result_id', 'test_name',
            'technology', 'condition_json', 'result_json', 'verdict', 'measured_at',
        })
        self.assertNotIn('condition_hash', legacy_record)
        self.assertNotIn('project_id', legacy_record)
        self.assertNotIn('operator', legacy_record)

    def test_measurement_attempts_record_includes_attempt_columns(self):
        from fcc_test_platform.provider_ingestion import map_measurement_attempt_record

        record = map_measurement_attempt_record(
            provider_id='provider-uuid',
            session_id='session-uuid',
            envelope=_attempt_envelope(),
        )

        self.assertEqual(record['condition_hash'], 'hash-abc-123')
        self.assertEqual(record['project_id'], 'proj-uuid-1')
        self.assertEqual(record['attempt_number'], 1)
        self.assertTrue(record['is_latest'])
        self.assertEqual(record['operator'], 'station-pc-7')


class TestOperatorBothColumns(unittest.TestCase):
    """Axis 2 — operator reaches BOTH operator and recorded_by from same source."""

    def test_operator_envelope_populates_both_central_columns(self):
        from fcc_test_platform.provider_ingestion import map_measurement_attempt_record

        record = map_measurement_attempt_record(
            provider_id='provider-uuid',
            session_id='session-uuid',
            envelope=_attempt_envelope(operator='same-source-op'),
        )

        self.assertEqual(record['operator'], 'same-source-op')
        self.assertEqual(record['recorded_by'], 'same-source-op')
        self.assertEqual(record['operator'], record['recorded_by'])

    def test_empty_operator_omits_both_columns(self):
        """Negative — missing provenance must NOT populate either column."""
        from fcc_test_platform.provider_ingestion import map_measurement_attempt_record

        record = map_measurement_attempt_record(
            provider_id='provider-uuid',
            session_id='session-uuid',
            envelope=_attempt_envelope(operator=''),
        )

        self.assertNotIn('operator', record)
        self.assertNotIn('recorded_by', record)


class TestConditionHashByteIdenticalPropagation(unittest.TestCase):
    """Axis 3 — condition_hash is propagated verbatim, never recomputed."""

    def test_byte_identical_hash_from_local_payload_to_central_record(self):
        from fcc_test_platform.provider_ingestion import map_measurement_attempt_record

        local_hash = hashlib.sha256(b'arbitrary-condition-bytes').hexdigest()
        record = map_measurement_attempt_record(
            provider_id='provider-uuid',
            session_id='session-uuid',
            envelope=_attempt_envelope(condition_hash=local_hash),
        )

        self.assertEqual(record['condition_hash'], local_hash)
        # Bit-level identity
        self.assertEqual(len(record['condition_hash']), 64)
        self.assertEqual(record['condition_hash'].encode('ascii'), local_hash.encode('ascii'))

    def test_negative_case_recomputed_hash_would_differ(self):
        """Negative case — a different hash means central recomputed.

        We pass a sentinel hash and verify the central record contains EXACTLY
        that value (not a hash of the result_json or any other column).
        """
        from fcc_test_platform.provider_ingestion import map_measurement_attempt_record

        sentinel = 'NOT-A-REAL-HASH-just-a-sentinel-string-1234567890'
        record = map_measurement_attempt_record(
            provider_id='provider-uuid',
            session_id='session-uuid',
            envelope=_attempt_envelope(condition_hash=sentinel),
        )

        self.assertEqual(record['condition_hash'], sentinel)
        # Negative: if anyone recomputes from result_json it would be a hex digest,
        # which the sentinel is not.
        self.assertNotEqual(record['condition_hash'], hashlib.sha256(b'').hexdigest())
        self.assertNotEqual(
            record['condition_hash'],
            hashlib.sha256(record['result_json'].encode('utf-8')).hexdigest(),
        )

    def test_missing_condition_hash_is_rejected(self):
        from fcc_test_platform.provider_ingestion import map_measurement_attempt_record

        with self.assertRaisesRegex(ValueError, 'condition_hash is required'):
            map_measurement_attempt_record(
                provider_id='provider-uuid',
                session_id='session-uuid',
                envelope=_attempt_envelope(condition_hash=''),
            )


class TestCoverageDirectWriteForbidden(unittest.TestCase):
    """Axis 4 — coverage_by_condition_hash MUST NOT have a direct write path."""

    INGESTION_MODULES = (
        'src/application/headless/platform_ingestion.py',
        'src/application/headless/platform_ingestion_plan.py',
        'src/application/headless/platform_postgres_ingestion_writer.py',
        'src/application/headless/platform_ingestion_worker.py',
    )

    def test_no_coverage_table_string_in_ingestion_modules(self):
        for relative in self.INGESTION_MODULES:
            with self.subTest(module=relative):
                path = project_root / relative
                source = path.read_text(encoding='utf-8')
                tree = ast.parse(source, filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        self.assertNotEqual(
                            node.value, 'coverage_by_condition_hash',
                            f"{relative} must not reference coverage table by name — "
                            "it is a central materialized VIEW derived from "
                            "measurement_attempts (FE-P0a).",
                        )

    def test_coverage_table_absent_from_table_order_and_idempotency(self):
        from fcc_test_platform.provider_ingestion_plan import (
            IDEMPOTENCY_KEYS_BY_TABLE,
            INGESTION_TABLE_ORDER,
        )

        self.assertNotIn('coverage_by_condition_hash', INGESTION_TABLE_ORDER)
        self.assertNotIn('coverage_by_condition_hash', IDEMPOTENCY_KEYS_BY_TABLE)


class TestIsLatestDerivation(unittest.TestCase):
    """Axis 5 — within (project_id, condition_hash) group, only MAX attempt is latest."""

    def test_single_attempt_in_group_keeps_is_latest_true(self):
        from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch

        batch = build_platform_ingestion_batch(
            provider_id='provider-uuid',
            session_id='session-uuid',
            result_envelopes=[],
            attempt_envelopes=[_attempt_envelope()],
        ).to_dict()

        self.assertEqual(len(batch['measurement_attempts']), 1)
        self.assertTrue(batch['measurement_attempts'][0]['is_latest'])

    def test_multiple_attempts_only_max_is_latest(self):
        from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch

        envelopes = [
            _attempt_envelope(attempt_number=1, idempotency_key='idem-1', is_latest=True),
            _attempt_envelope(attempt_number=2, idempotency_key='idem-2', is_latest=True),
            _attempt_envelope(attempt_number=3, idempotency_key='idem-3', is_latest=True),
        ]

        batch = build_platform_ingestion_batch(
            provider_id='provider-uuid',
            session_id='session-uuid',
            result_envelopes=[],
            attempt_envelopes=envelopes,
        ).to_dict()

        attempts = batch['measurement_attempts']
        latest_flags = [a['is_latest'] for a in attempts]
        self.assertEqual(latest_flags.count(True), 1)
        latest = next(a for a in attempts if a['is_latest'])
        self.assertEqual(latest['attempt_number'], 3)

    def test_different_groups_each_keep_one_latest(self):
        from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch

        envelopes = [
            _attempt_envelope(condition_hash='hash-A', attempt_number=1, idempotency_key='a-1'),
            _attempt_envelope(condition_hash='hash-A', attempt_number=2, idempotency_key='a-2'),
            _attempt_envelope(condition_hash='hash-B', attempt_number=1, idempotency_key='b-1'),
        ]

        batch = build_platform_ingestion_batch(
            provider_id='provider-uuid',
            session_id='session-uuid',
            result_envelopes=[],
            attempt_envelopes=envelopes,
        ).to_dict()

        by_hash = {a['condition_hash']: [] for a in batch['measurement_attempts']}
        for attempt in batch['measurement_attempts']:
            by_hash[attempt['condition_hash']].append(attempt)
        for group_attempts in by_hash.values():
            self.assertEqual(
                sum(1 for a in group_attempts if a['is_latest']), 1,
                f'each (project_id, condition_hash) group must have exactly one is_latest=true row',
            )


class TestForeignKeyIntegrity(unittest.TestCase):
    """Phase A — measurement_result_id (central uuid FK) MUST NOT be populated
    from provider_result_id (string). The writer resolves it inside the
    SAME-transaction via RETURNING id (FE-P0a ingestion_contract Rule 2).
    """

    def test_attempt_record_does_not_carry_measurement_result_id_column(self):
        from fcc_test_platform.provider_ingestion import map_measurement_attempt_record

        record = map_measurement_attempt_record(
            provider_id='provider-uuid',
            session_id='session-uuid',
            envelope=_attempt_envelope(),
        )

        self.assertNotIn(
            'measurement_result_id', record,
            'measurement_result_id is a central uuid FK and must be resolved by '
            'the SAME-transaction writer (RETURNING id), never mapped from the '
            'provider envelope.',
        )

    def test_envelope_result_id_is_preserved_as_side_band_hint_only(self):
        from fcc_test_platform.provider_ingestion import map_measurement_attempt_record

        record = map_measurement_attempt_record(
            provider_id='provider-uuid',
            session_id='session-uuid',
            envelope=_attempt_envelope(),  # result_id='provider-result-1'
        )

        self.assertEqual(record['_fk_provider_result_id'], 'provider-result-1')
        self.assertNotIn('measurement_result_id', record)

    def test_plan_pops_fk_side_band_into_fk_resolution_hint(self):
        """Plan layer separates side-band keys from schema record so build_postgres_upsert
        sees only central-schema columns.
        """
        from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch
        from fcc_test_platform.provider_ingestion_plan import build_platform_ingestion_plan

        batch = build_platform_ingestion_batch(
            provider_id='provider-uuid',
            session_id='session-uuid',
            result_envelopes=[],
            attempt_envelopes=[_attempt_envelope()],
        )
        plan = build_platform_ingestion_plan(batch)
        attempt_step = next(s for s in plan.steps if s.target_table == 'measurement_attempts')

        # Schema record contains only central-schema columns:
        self.assertNotIn('_fk_provider_result_id', attempt_step.record)
        self.assertNotIn('measurement_result_id', attempt_step.record)
        # Hint preserved for the writer's FK resolution step:
        self.assertEqual(
            attempt_step.fk_resolution_hint.get('provider_result_id'),
            'provider-result-1',
        )

    def test_plan_step_record_never_contains_side_band_keys(self):
        """Across the full batch, no step.record should leak a _fk_* key into
        build_postgres_upsert (those would fail SQL identifier validation OR
        worse, write into a nonexistent column silently).
        """
        from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch
        from fcc_test_platform.provider_ingestion_plan import (
            SIDE_BAND_KEY_PREFIX,
            build_platform_ingestion_plan,
        )

        batch = build_platform_ingestion_batch(
            provider_id='provider-uuid',
            session_id='session-uuid',
            result_envelopes=[{
                'result_id': 'r1',
                'test_name': 'OBW',
                'technology': 'DTS',
                'condition': {},
                'result': {},
            }],
            attempt_envelopes=[_attempt_envelope()],
        )

        plan = build_platform_ingestion_plan(batch)
        for step in plan.steps:
            for column in step.record:
                self.assertFalse(
                    column.startswith(SIDE_BAND_KEY_PREFIX),
                    f'{step.target_table} step.record leaked side-band column {column!r}',
                )


class TestIdempotencyKeyAndPlan(unittest.TestCase):
    """Axis 6 — measurement_attempts idempotency key is composite + plan-level dedup works."""

    def test_idempotency_key_is_composite_three_columns(self):
        from fcc_test_platform.provider_ingestion_plan import IDEMPOTENCY_KEYS_BY_TABLE

        self.assertEqual(
            IDEMPOTENCY_KEYS_BY_TABLE['measurement_attempts'],
            ('session_id', 'condition_hash', 'attempt_number'),
        )

    def test_plan_rejects_duplicate_attempt_within_batch(self):
        from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch
        from fcc_test_platform.provider_ingestion_plan import build_platform_ingestion_plan

        batch = build_platform_ingestion_batch(
            provider_id='provider-uuid',
            session_id='session-uuid',
            result_envelopes=[],
            attempt_envelopes=[
                _attempt_envelope(attempt_number=1, idempotency_key='dup-1'),
                _attempt_envelope(attempt_number=1, idempotency_key='dup-2'),
            ],
        )

        with self.assertRaisesRegex(ValueError, 'duplicate idempotency key'):
            build_platform_ingestion_plan(batch)

    def test_plan_includes_attempts_in_dependency_order(self):
        from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch
        from fcc_test_platform.provider_ingestion_plan import build_platform_ingestion_plan

        batch = build_platform_ingestion_batch(
            provider_id='provider-uuid',
            session_id='session-uuid',
            result_envelopes=[{
                'result_id': 'r1',
                'test_name': 'OBW',
                'technology': 'DTS',
                'condition': {},
                'result': {},
            }],
            attempt_envelopes=[_attempt_envelope()],
        )

        plan = build_platform_ingestion_plan(batch).to_dict()
        tables = [step['target_table'] for step in plan['steps']]
        # measurement_results -> measurement_attempts ordering (FK ref)
        self.assertEqual(tables, ['measurement_results', 'measurement_attempts'])

    def test_postgres_upsert_accepts_attempts_idempotency_key(self):
        """Round-trip — measurement_attempts record + composite key build a valid upsert.

        Phase A — record MUST NOT contain ``measurement_result_id`` (uuid FK
        resolved inside the SAME-transaction via RETURNING id, not from this
        record) NOR any ``_fk_*`` side-band keys.
        """
        from fcc_test_platform.postgres_ingestion_writer import build_postgres_upsert

        record = {
            'provider_id': 'provider-uuid',
            'session_id': 'session-uuid',
            'test_name': 'OBW',
            'technology': 'DTS',
            'condition_hash': 'hash-abc-123',
            'attempt_number': 1,
            'is_latest': True,
            'status': 'completed',
            'result_json': '{}',
            'operator': 'op-1',
            'recorded_by': 'op-1',
        }
        statement, parameters = build_postgres_upsert(
            'measurement_attempts',
            record,
            ('session-uuid', 'hash-abc-123', 1),
        )

        self.assertIn('INSERT INTO "measurement_attempts"', statement)
        self.assertIn(
            'ON CONFLICT ("session_id", "condition_hash", "attempt_number")',
            statement,
        )
        self.assertIn('hash-abc-123', parameters)

    def test_idempotency_key_envelope_carries_to_record(self):
        """Phase A — envelope.idempotency_key reaches the central record so the
        schema's UX index ``ux_measurement_attempts_idempotency_key UNIQUE``
        provides retry-safe dedup alongside the composite (session, hash, attempt#) key.
        """
        from fcc_test_platform.provider_ingestion import map_measurement_attempt_record

        record = map_measurement_attempt_record(
            provider_id='provider-uuid',
            session_id='session-uuid',
            envelope=_attempt_envelope(idempotency_key='IDEM-XYZ-12345'),
        )

        self.assertEqual(record['idempotency_key'], 'IDEM-XYZ-12345')

    def test_attempt_envelope_without_idempotency_key_omits_column(self):
        """Phase A — ux_measurement_attempts_idempotency_key has WHERE NOT NULL,
        so omitted idempotency_key must NOT be persisted as an empty string
        (would collapse all NULL-keyed rows into a UNIQUE collision).
        """
        from fcc_test_platform.provider_ingestion import map_measurement_attempt_record

        record = map_measurement_attempt_record(
            provider_id='provider-uuid',
            session_id='session-uuid',
            envelope=_attempt_envelope(idempotency_key=''),
        )

        self.assertNotIn('idempotency_key', record)


class TestPayloadEnvelopeFlowEndToEnd(unittest.TestCase):
    """Realistic flow — outbox payload dict shape exercises the mapping pipeline."""

    def test_outbox_payload_shape_round_trips_to_central_records(self):
        """An outbox payload (FE-P0b) supplies condition_hash + provenance.recorded_by + context.project_id;
        the higher layer hoists them into envelope keys before calling map_measurement_attempt_record."""
        from fcc_test_platform.provider_ingestion import map_measurement_attempt_record

        # Mimic the exact shape of measurement_history_store._enqueue_attempt_outbox
        outbox_payload_json = json.dumps({
            'attempt_id': 42,
            'session_id': 7,
            'test_result_id': 12,
            'condition_hash': 'CH-deadbeef',
            'attempt_number': 3,
            'status': 'completed',
            'result1': '12.5',
            'result2': None,
            'result_sum': None,
            'result1_unit': 'MHz',
            'margin': '0.5',
            'pass_fail': 'Pass',
            'idempotency_key': 'ik-1',
            'provenance': {'recorded_by': 'pc-station-7'},
            'context': {'project_id': 'PRJ-1', 'model_id': 'M1', 'sheet_name': 'OBW', 'row_order': 5},
            'metadata': {},
        }, sort_keys=True)
        payload = json.loads(outbox_payload_json)

        # The hoisting (outbox payload → ingestion envelope) is the boundary
        # ingestion-side callers own. Here we model it explicitly so the
        # invariant validates the round-trip pipeline.
        envelope = {
            'result_id': str(payload.get('test_result_id') or ''),
            'test_name': 'OBW',
            'technology': 'DTS',
            'condition_hash': payload['condition_hash'],
            'attempt_number': payload['attempt_number'],
            'is_latest': True,
            'status': payload['status'],
            'verdict': payload['pass_fail'],
            'margin': payload['margin'],
            'result': {
                'result1': payload['result1'],
                'result1_unit': payload['result1_unit'],
            },
            'idempotency_key': payload['idempotency_key'],
            'operator': payload['provenance']['recorded_by'],
            'provenance': payload['provenance'],
            'project_id': payload['context']['project_id'],
        }

        record = map_measurement_attempt_record(
            provider_id='provider-uuid',
            session_id='central-session-uuid',
            envelope=envelope,
        )

        self.assertEqual(record['condition_hash'], 'CH-deadbeef')
        self.assertEqual(record['attempt_number'], 3)
        self.assertEqual(record['operator'], 'pc-station-7')
        self.assertEqual(record['recorded_by'], 'pc-station-7')
        self.assertEqual(record['project_id'], 'PRJ-1')
        self.assertIn('provenance_json', record)
        self.assertEqual(
            json.loads(record['provenance_json'])['recorded_by'],
            'pc-station-7',
        )


if __name__ == '__main__':
    unittest.main()
