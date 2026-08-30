"""FE-P0c Phase B invariants — outbox payload → ingestion envelope SSOT.

Validates the boundary between local outbox (FE-P0b payload shape) and
central ingestion (FE-P0a record shape):

1. Production helper ``envelope_from_outbox_attempt_payload`` exists and is
   the SSOT — test fixtures NO LONGER simulate the conversion inline (Phase B
   defect #3 closure).
2. Hoisting rules: provenance.recorded_by → operator, context.project_id →
   project_id (after uuid resolution), context.sheet_name → test_name,
   payload.technology_code → technology, payload.condition_hash → condition_hash
   (verbatim).
3. Local int session_id → central uuid resolution via CentralIdResolverPort
   (loud-fail on missing mapping).
4. Local text project_code → central uuid resolution.
5. Provider result identity (test_result_id) is preserved for the writer's
   FK resolution step (Phase A side-band).
6. Result columns (result1/2/sum + units + dccf) normalize into one JSON envelope.
7. Cross-session batch detection — caller must slice events by session.
8. Dependency-free import boundary (AST guard).
"""
from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path


project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402


def _outbox_payload(**overrides) -> dict:
    payload = {
        'attempt_id': 42,
        'condition_id': 7,
        'session_id': 11,
        'test_result_id': 91,
        'sheet_name': 'OBW',
        'technology_code': 'BT',
        'row_order': 5,
        'condition_hash': 'CH-deadbeef-123456',
        'attempt_number': 2,
        'status': 'completed',
        'result1': '12.5',
        'result2': None,
        'result_sum': None,
        'result1_unit': 'MHz',
        'result2_unit': None,
        'result_sum_unit': None,
        'margin': '0.5',
        'pass_fail': 'Pass',
        'dccf': None,
        'idempotency_key': 'IDEM-XYZ',
        'provenance': {'recorded_by': 'station-pc-7'},
        'context': {
            'project_id': 'PRJ-LOCAL-1',
            'sheet_name': 'OBW',
            'model_id': 'M1',
            'row_order': 5,
        },
        'metadata': {},
    }
    # Allow deep overrides for nested provenance/context
    for key, value in overrides.items():
        if isinstance(value, dict) and key in ('provenance', 'context'):
            merged = dict(payload[key])
            merged.update(value)
            payload[key] = merged
        else:
            payload[key] = value
    return payload


def _resolver():
    from fcc_test_platform.central_id_resolver import InMemoryCentralIdResolver
    return InMemoryCentralIdResolver(
        session_uuid_by_local_id={11: 'central-session-uuid-AAA'},
        project_uuid_by_code={'PRJ-LOCAL-1': 'central-project-uuid-BBB'},
    )


class TestEnvelopeHoisting(unittest.TestCase):
    def test_reference_snapshot_is_forwarded_without_reencoding(self):
        from fcc_test_platform.outbox_envelope_builder import envelope_from_outbox_attempt_payload

        snapshot = '{"project_id":"p1","references":[]}'
        envelope = envelope_from_outbox_attempt_payload(
            _outbox_payload(
                project_result_reference_snapshot_json=snapshot,
                project_result_reference_snapshot_schema_version=(
                    'fcc.project-result-reference-session.v1'
                ),
            ),
            provider_id='provider-uuid',
            central_id_resolver=_resolver(),
        )

        self.assertEqual(
            envelope['project_result_reference_snapshot_json'], snapshot,
        )
        self.assertEqual(
            envelope['project_result_reference_snapshot_schema_version'],
            'fcc.project-result-reference-session.v1',
        )

    def test_hoists_provenance_to_operator(self):
        from fcc_test_platform.outbox_envelope_builder import envelope_from_outbox_attempt_payload

        envelope = envelope_from_outbox_attempt_payload(
            _outbox_payload(),
            provider_id='provider-uuid',
            central_id_resolver=_resolver(),
        )

        self.assertEqual(envelope['operator'], 'station-pc-7')

    def test_hoists_context_project_id_through_uuid_resolution(self):
        from fcc_test_platform.outbox_envelope_builder import envelope_from_outbox_attempt_payload

        envelope = envelope_from_outbox_attempt_payload(
            _outbox_payload(),
            provider_id='provider-uuid',
            central_id_resolver=_resolver(),
        )

        # Local 'PRJ-LOCAL-1' → resolved central uuid 'central-project-uuid-BBB'
        self.assertEqual(envelope['project_id'], 'central-project-uuid-BBB')

    def test_hoists_sheet_name_to_test_name(self):
        from fcc_test_platform.outbox_envelope_builder import envelope_from_outbox_attempt_payload

        envelope = envelope_from_outbox_attempt_payload(
            _outbox_payload(),
            provider_id='provider-uuid',
            central_id_resolver=_resolver(),
        )

        self.assertEqual(envelope['test_name'], 'OBW')

    def test_propagates_technology_code(self):
        from fcc_test_platform.outbox_envelope_builder import envelope_from_outbox_attempt_payload

        envelope = envelope_from_outbox_attempt_payload(
            _outbox_payload(technology_code='WLAN'),
            provider_id='provider-uuid',
            central_id_resolver=_resolver(),
        )

        self.assertEqual(envelope['technology'], 'WLAN')

    def test_propagates_condition_hash_verbatim(self):
        from fcc_test_platform.outbox_envelope_builder import envelope_from_outbox_attempt_payload

        envelope = envelope_from_outbox_attempt_payload(
            _outbox_payload(),
            provider_id='provider-uuid',
            central_id_resolver=_resolver(),
        )

        self.assertEqual(envelope['condition_hash'], 'CH-deadbeef-123456')


class TestIdResolverIntegration(unittest.TestCase):
    def test_local_int_session_id_resolved_to_central_uuid(self):
        from fcc_test_platform.outbox_envelope_builder import envelope_from_outbox_attempt_payload

        envelope = envelope_from_outbox_attempt_payload(
            _outbox_payload(),
            provider_id='provider-uuid',
            central_id_resolver=_resolver(),
        )

        self.assertEqual(envelope['_central_session_uuid'], 'central-session-uuid-AAA')

    def test_missing_session_mapping_raises_loud(self):
        from fcc_test_platform.central_id_resolver import (
            CentralIdResolutionError,
            InMemoryCentralIdResolver,
        )
        from fcc_test_platform.outbox_envelope_builder import envelope_from_outbox_attempt_payload

        empty_resolver = InMemoryCentralIdResolver()
        with self.assertRaises(CentralIdResolutionError):
            envelope_from_outbox_attempt_payload(
                _outbox_payload(),
                provider_id='provider-uuid',
                central_id_resolver=empty_resolver,
            )

    def test_missing_project_mapping_raises_loud(self):
        from fcc_test_platform.central_id_resolver import (
            CentralIdResolutionError,
            InMemoryCentralIdResolver,
        )
        from fcc_test_platform.outbox_envelope_builder import envelope_from_outbox_attempt_payload

        # session mapped but project not mapped
        resolver = InMemoryCentralIdResolver(
            session_uuid_by_local_id={11: 'central-session-uuid-AAA'},
        )
        with self.assertRaises(CentralIdResolutionError):
            envelope_from_outbox_attempt_payload(
                _outbox_payload(),
                provider_id='provider-uuid',
                central_id_resolver=resolver,
            )

    def test_null_project_id_resolves_to_none(self):
        from fcc_test_platform.outbox_envelope_builder import envelope_from_outbox_attempt_payload

        payload = _outbox_payload(context={'project_id': None, 'sheet_name': 'OBW'})
        envelope = envelope_from_outbox_attempt_payload(
            payload,
            provider_id='provider-uuid',
            central_id_resolver=_resolver(),
        )

        self.assertNotIn('project_id', envelope)


class TestRequiredFields(unittest.TestCase):
    def test_missing_technology_code_raises_loud(self):
        from fcc_test_platform.outbox_envelope_builder import (
            OutboxEnvelopeBuildError,
            envelope_from_outbox_attempt_payload,
        )

        with self.assertRaisesRegex(OutboxEnvelopeBuildError, 'technology_code'):
            envelope_from_outbox_attempt_payload(
                _outbox_payload(technology_code=None),
                provider_id='provider-uuid',
                central_id_resolver=_resolver(),
            )

    def test_missing_condition_hash_raises_loud(self):
        from fcc_test_platform.outbox_envelope_builder import (
            OutboxEnvelopeBuildError,
            envelope_from_outbox_attempt_payload,
        )

        with self.assertRaisesRegex(OutboxEnvelopeBuildError, 'condition_hash'):
            envelope_from_outbox_attempt_payload(
                _outbox_payload(condition_hash=None),
                provider_id='provider-uuid',
                central_id_resolver=_resolver(),
            )

    def test_missing_sheet_name_and_test_name_raises_loud(self):
        from fcc_test_platform.outbox_envelope_builder import (
            OutboxEnvelopeBuildError,
            envelope_from_outbox_attempt_payload,
        )

        payload = _outbox_payload()
        # Replace context entirely so sheet_name is absent at both top-level
        # and inside context (test_name fallback path also fails).
        payload['context'] = {'project_id': 'PRJ-LOCAL-1'}
        payload['sheet_name'] = None
        payload.pop('test_name', None)

        with self.assertRaisesRegex(OutboxEnvelopeBuildError, 'test_name'):
            envelope_from_outbox_attempt_payload(
                payload,
                provider_id='provider-uuid',
                central_id_resolver=_resolver(),
            )


class TestProviderResultIdSidebandPath(unittest.TestCase):
    """Phase A x Phase B — provider_result_id (envelope.result_id) flows to the
    writer's FK resolution step. Builder MUST emit it so the mapper preserves
    it as ``_fk_provider_result_id`` side-band — never as the uuid FK column.
    """

    def test_prefers_test_result_id_as_provider_result_id(self):
        from fcc_test_platform.outbox_envelope_builder import envelope_from_outbox_attempt_payload

        envelope = envelope_from_outbox_attempt_payload(
            _outbox_payload(test_result_id=91),
            provider_id='provider-uuid',
            central_id_resolver=_resolver(),
        )

        self.assertEqual(envelope['result_id'], '91')

    def test_falls_back_to_attempt_id_when_test_result_id_absent(self):
        from fcc_test_platform.outbox_envelope_builder import envelope_from_outbox_attempt_payload

        envelope = envelope_from_outbox_attempt_payload(
            _outbox_payload(test_result_id=None),
            provider_id='provider-uuid',
            central_id_resolver=_resolver(),
        )

        self.assertEqual(envelope['result_id'], '42')

    def test_chamber_scope_qualifies_session_and_result_identity(self):
        from fcc_test_platform.outbox_envelope_builder import envelope_from_outbox_attempt_payload
        from fcc_test_platform.postgres_central_id_resolver import PostgresCentralIdResolver

        resolver = PostgresCentralIdResolver(
            provider_id='provider-uuid', connection_factory=lambda: None,  # type: ignore[arg-type]
        )
        payload = _outbox_payload(context={'project_id': None})
        chamber_a = envelope_from_outbox_attempt_payload(
            payload,
            provider_id='provider-uuid',
            central_id_resolver=resolver,
            chamber_id='chamber-a',
        )
        chamber_b = envelope_from_outbox_attempt_payload(
            payload,
            provider_id='provider-uuid',
            central_id_resolver=resolver,
            chamber_id='chamber-b',
        )
        self.assertNotEqual(chamber_a['_central_session_uuid'], chamber_b['_central_session_uuid'])
        self.assertNotEqual(chamber_a['result_id'], chamber_b['result_id'])


class TestResultJsonNormalization(unittest.TestCase):
    def test_normalizes_result_columns_to_single_envelope(self):
        from fcc_test_platform.outbox_envelope_builder import normalize_result_json_payload

        result = normalize_result_json_payload(_outbox_payload())

        self.assertEqual(result['result1'], '12.5')
        self.assertEqual(result['result1_unit'], 'MHz')
        # None columns omitted
        self.assertNotIn('result2', result)
        self.assertNotIn('result_sum', result)
        self.assertNotIn('dccf', result)

    def test_includes_dccf_when_present(self):
        from fcc_test_platform.outbox_envelope_builder import normalize_result_json_payload

        result = normalize_result_json_payload(_outbox_payload(dccf=2.5))

        self.assertEqual(result['dccf'], 2.5)


class TestBatchEnvelopeExtraction(unittest.TestCase):
    def test_events_with_same_session_produce_envelopes_and_shared_session_uuid(self):
        from fcc_test_platform.outbox_envelope_builder import envelopes_from_outbox_events

        events = [
            {'id': 1, 'payload_json': json.dumps(_outbox_payload(attempt_number=1))},
            {'id': 2, 'payload_json': json.dumps(_outbox_payload(attempt_number=2))},
        ]

        session_uuid, envelopes = envelopes_from_outbox_events(
            events,
            provider_id='provider-uuid',
            central_id_resolver=_resolver(),
            payload_parser=json.loads,
        )

        self.assertEqual(session_uuid, 'central-session-uuid-AAA')
        self.assertEqual(len(envelopes), 2)

    def test_cross_session_batch_raises_loud(self):
        from fcc_test_platform.central_id_resolver import InMemoryCentralIdResolver
        from fcc_test_platform.outbox_envelope_builder import (
            OutboxEnvelopeBuildError,
            envelopes_from_outbox_events,
        )

        resolver = InMemoryCentralIdResolver(
            session_uuid_by_local_id={11: 'central-AAA', 22: 'central-BBB'},
            project_uuid_by_code={'PRJ-LOCAL-1': 'proj-uuid'},
        )
        events = [
            {'id': 1, 'payload_json': json.dumps(_outbox_payload(session_id=11))},
            {'id': 2, 'payload_json': json.dumps(_outbox_payload(session_id=22))},
        ]

        with self.assertRaisesRegex(OutboxEnvelopeBuildError, 'cross-session batch'):
            envelopes_from_outbox_events(
                events,
                provider_id='provider-uuid',
                central_id_resolver=resolver,
                payload_parser=json.loads,
            )


class TestEnvelopeRoundTripsThroughMapper(unittest.TestCase):
    """End-to-end: outbox payload (real JSON) → builder → mapper → record.

    Verifies the production builder produces an envelope that map_measurement_attempt_record
    accepts byte-identical to the explicit-envelope test fixtures.
    """

    def test_full_pipeline_outbox_to_central_record(self):
        from fcc_test_platform.outbox_envelope_builder import envelope_from_outbox_attempt_payload
        from fcc_test_platform.provider_ingestion import map_measurement_attempt_record

        outbox_payload_json = json.dumps(_outbox_payload())
        payload = json.loads(outbox_payload_json)

        envelope = envelope_from_outbox_attempt_payload(
            payload,
            provider_id='provider-uuid',
            central_id_resolver=_resolver(),
        )
        envelope.pop('_central_session_uuid', None)
        record = map_measurement_attempt_record(
            provider_id='provider-uuid',
            session_id='central-session-uuid-AAA',
            envelope=envelope,
        )

        self.assertEqual(record['condition_hash'], 'CH-deadbeef-123456')
        self.assertEqual(record['attempt_number'], 2)
        self.assertEqual(record['operator'], 'station-pc-7')
        self.assertEqual(record['recorded_by'], 'station-pc-7')
        self.assertEqual(record['project_id'], 'central-project-uuid-BBB')
        self.assertEqual(record['technology'], 'BT')
        self.assertEqual(record['test_name'], 'OBW')
        self.assertEqual(record['idempotency_key'], 'IDEM-XYZ')
        # FK side-band — provider_result_id preserved
        self.assertEqual(record['_fk_provider_result_id'], '91')
        # uuid FK NOT directly populated (Phase A invariant)
        self.assertNotIn('measurement_result_id', record)


class TestDependencyFreeBoundary(unittest.TestCase):
    """Phase B SSOT — both helpers must be dependency-free (AST guard)."""

    MODULES = (
        'src/application/headless/central_id_resolver.py',
        'src/application/headless/outbox_envelope_builder.py',
    )

    FORBIDDEN_PREFIXES = (
        'infrastructure',
        'reporting',
        'measurements',
        'fastapi',
        'PySide6',
        'sqlalchemy',
        'pandas',
        'openpyxl',
        'pyvisa',
        'sqlite3',
        'psycopg',
        'asyncpg',
    )

    def test_helpers_have_no_forbidden_imports(self):
        for relative in self.MODULES:
            with self.subTest(module=relative):
                # Repository name in, this tree's location out. Both modules ship
                # in the platform box (2026-08-15) as ``fcc_test_platform/*.py``;
                # a raw ``project_root / relative`` join names a file that is not
                # there. Byte-identical in the monorepo, where no layout record
                # exists and the join is the answer.
                path = resolve_repo_artifact(__file__, relative)
                tree = ast.parse(path.read_text(encoding='utf-8'))
                imports: list[str] = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imports.extend(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imports.append(node.module)
                for imported in imports:
                    self.assertFalse(
                        imported.startswith(self.FORBIDDEN_PREFIXES),
                        f'{relative}: forbidden import {imported}',
                    )


if __name__ == '__main__':
    unittest.main()
