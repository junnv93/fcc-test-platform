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


class TestTargetScopedSession(unittest.TestCase):
    """빌더→해소기 경로에서 **target 축이 실제로 실행되는지** 봉인한다.

    ⚠️ 이 클래스가 생기기 전까지 이 파일의 어떤 시험도 그 축을 실행한 적이 없었다.
    ``_outbox_payload`` 의 ``context`` 에 ``model_number`` 도 ``sample_code`` 도 없고,
    커널의 ``measurement_target_key`` 는 **둘 다** 있어야 비어있지 않은 값을 내기
    때문이다(실측: ``('M1', None) -> ''``). 그래서 빌더가 넘기는 ``target_identity``
    는 언제나 ``''`` 였고, 해소기의 ``if target:`` 가지는 «한 번도 실행되지 않았다».

    기존 픽스처를 고쳐 target 을 싣지 **않는다** — target 이 빈 세계도 생산에
    존재하고(model·sample 중 하나만 없어도 키는 ``''``), ``session_uuid_name`` 이
    그 경우 legacy 식별자 공간을 일부러 보존한다. 그 세계를 검증하던 시험들이
    사라지면 안 된다. 그래서 여기서는 **더한다**.
    """

    CHAMBER = 'CH-1'

    @staticmethod
    def _payload_for_target(model_number, sample_code):
        return _outbox_payload(
            context={'model_number': model_number, 'sample_code': sample_code},
        )

    def _build(self, resolver, model_number, sample_code):
        from fcc_test_platform.outbox_envelope_builder import (
            envelope_from_outbox_attempt_payload,
        )

        return envelope_from_outbox_attempt_payload(
            self._payload_for_target(model_number, sample_code),
            provider_id='provider-uuid',
            central_id_resolver=resolver,
            chamber_id=self.CHAMBER,
        )

    def test_the_builder_actually_carries_a_non_empty_target(self):
        """이 파일의 나머지가 검증하지 «못하던» 전제부터 세운다.

        아래 두 시험이 의미를 가지려면 빌더가 비어있지 않은 target 을 넘겨야 한다.
        그것이 참인지 먼저 묻지 않으면, 두 시험은 target 이 ``''`` 인 채로도
        초록일 수 있다 — 공허 통과의 둘째 모양(집합이 «틀린» 경우)이다.
        """
        from fcc_test_kernel.domain.services.central_session_identity import (
            measurement_target_key,
        )

        payload = self._payload_for_target('MODEL-X', 'SAMPLE-1')
        derived = measurement_target_key(
            payload['context']['model_number'], payload['context']['sample_code'],
        )
        self.assertTrue(
            derived,
            'measurement_target_key 가 빈 값을 냈다 — 아래 시험들이 target 축을 '
            '실행하지 못한다',
        )

    def test_two_targets_one_session_do_not_share_a_uuid(self):
        """한 chamber·한 로컬 세션이라도 측정 대상이 다르면 uuid 가 갈린다."""
        from fcc_test_platform.central_id_resolver import InMemoryCentralIdResolver
        from fcc_test_kernel.domain.services.central_session_identity import (
            measurement_target_key,
        )

        resolver = InMemoryCentralIdResolver(
            project_uuid_by_code={'PRJ-LOCAL-1': 'central-project-uuid-BBB'},
        )
        for model, sample, uuid_value in (
            ('MODEL-X', 'SAMPLE-1', 'central-session-uuid-X'),
            ('MODEL-Y', 'SAMPLE-2', 'central-session-uuid-Y'),
        ):
            resolver.register_session(
                11, uuid_value,
                chamber_id=self.CHAMBER,
                target_identity=measurement_target_key(model, sample),
            )

        first = self._build(resolver, 'MODEL-X', 'SAMPLE-1')
        second = self._build(resolver, 'MODEL-Y', 'SAMPLE-2')

        # 키 이름을 먼저 확인한다 — 바뀌면 아래 단언이 KeyError 로 죽고, KeyError 는
        # 「값이 같다」와 다른 명제인데 화면에서는 둘 다 그냥 red 로 보인다.
        for envelope in (first, second):
            self.assertIn('_central_session_uuid', envelope)

        self.assertNotEqual(
            first['_central_session_uuid'], second['_central_session_uuid'],
            '한 chamber 의 두 측정 대상이 세션 uuid 를 공유한다 — 포트가 금지하는 충돌',
        )

    def test_an_unregistered_target_is_loud_when_scope_was_declared(self):
        """등록부가 target 을 «말했는데» 그중에 없으면 조용히 넘어가지 않는다.

        ⚠️ **chamber 를 «일부러» 안 넘긴다.** 처음 쓴 판은 ``chamber_id='CH-1'`` 로
        구성했는데, 그러면 fallback 인 ``self._session_uuid[('CH-1', 11)]`` 도 어차피
        miss 해 ``KeyError`` 로 터진다. 즉 그 시험은 가드를 «제거해도» 초록이었다 —
        성실히 보고하며 통과하는 공허한 검사다(주입으로 잡았다).

        가드가 유일한 방어선이 되려면 **fallback 이 성공할 상황**이어야 한다:
        chamber 없이, 맨 정수 등록이 있는 채로. 그때 가드가 없으면 대역은
        ``'central-session-uuid-AAA'`` 를 조용히 돌려주고, 그것이 포트가 금지하는
        바로 그 충돌이다.
        """
        from fcc_test_platform.central_id_resolver import (
            CentralIdResolutionError,
            InMemoryCentralIdResolver,
        )
        from fcc_test_kernel.domain.services.central_session_identity import (
            measurement_target_key,
        )
        from fcc_test_platform.outbox_envelope_builder import (
            envelope_from_outbox_attempt_payload,
        )

        resolver = InMemoryCentralIdResolver(
            session_uuid_by_local_id={11: 'central-session-uuid-AAA'},
            project_uuid_by_code={'PRJ-LOCAL-1': 'central-project-uuid-BBB'},
        )
        resolver.register_session(
            11, 'central-session-uuid-X',
            target_identity=measurement_target_key('MODEL-X', 'SAMPLE-1'),
        )

        def _build_without_chamber(model_number, sample_code):
            return envelope_from_outbox_attempt_payload(
                self._payload_for_target(model_number, sample_code),
                provider_id='provider-uuid',
                central_id_resolver=resolver,
            )

        # 등록한 target 은 그대로 온다 — 가드가 정상 경로를 막지 않는다는 확인이다.
        self.assertEqual(
            _build_without_chamber('MODEL-X', 'SAMPLE-1')['_central_session_uuid'],
            'central-session-uuid-X',
        )
        # 등록하지 «않은» target 은 맨 정수 항목으로 미끄러지지 않는다.
        with self.assertRaises(CentralIdResolutionError):
            _build_without_chamber('MODEL-Y', 'SAMPLE-2')


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
