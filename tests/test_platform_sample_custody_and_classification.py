"""시료 도메인 재설계 — PM 축 custody · 분류 · 입고 이력 노출 (ADR-0002, 2026-09-04).

이 파일이 잡는 결함 넷은 전부 **실측된 것**이지 상상한 것이 아니다:

1. PM 축의 반입/반출이 단일 TEXT 라 짝지을 수 없었다 → custody 사건 표.
2. Device/Accessory 를 저장하는 곳이 없었다 → 엑셀 export 가 'Device' 를 하드코딩.
3. 시험 실무자 축의 1:N 이 DB 에만 쌓이고 화면에서 읽히지 않았다 → `latest_intake` 만
   노출되고 과거 행을 읽을 엔드포인트가 없었다.
4. PM/RF 엑셀 export 가 템플릿 경로 오타로 **항상 죽고 있었다** — 이 경로를 지나가는
   테스트가 한 건도 없었기 때문에 드러나지 않았다.
"""
from __future__ import annotations

import os
import re

import pytest

from fcc_test_kernel.domain.models.sample_inventory import (
    SAMPLE_EDITABLE_FIELDS,
    custody_state,
)
from fcc_test_kernel.domain.services.sample_inventory_policy import (
    SampleInvalidCustodyEvent,
    SampleUnknownField,
    validate_custody_event,
)
from fcc_test_platform.application.central_sample_inventory_read_adapter import (
    PostgresCentralSampleInventoryReadAdapter,
)
from fcc_test_platform.application.central_sample_inventory_service import (
    CentralSampleInventoryService,
    SampleInventoryNotFoundError,
)
from fcc_test_platform.application.central_sample_inventory_write_adapter import (
    PostgresCentralSampleInventoryWriteAdapter,
)
from tests.support.central_pg_sqlite_shim import QmarkConnection
from tests.support.sample_inventory_central import make_central_db, seed_project


PROJECT_ID = 'project-custody'


def _service(db_path: str) -> CentralSampleInventoryService:
    read = PostgresCentralSampleInventoryReadAdapter(lambda: QmarkConnection(db_path))
    write = PostgresCentralSampleInventoryWriteAdapter(lambda: QmarkConnection(db_path))
    return CentralSampleInventoryService(read, write)


class _Fixture:
    def setup_method(self):
        self.db_path = make_central_db()
        seed_project(self.db_path, PROJECT_ID, model_name='SM-F968U1')
        self.service = _service(self.db_path)

    def teardown_method(self):
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    def _sample(self, **overrides):
        payload = {
            'sample_number': '#2',
            'sample_kind': 'Device',
            'sample_description': 'SM-F968U1_Main Conduction #2',
            'test_category': 'Conduction',
            'label_number': 'YIP1252M',
            'serial_number': 'R3CY20KCHJM',
            'assigned_team': 'RF',
            'sender': '김용태 프로님 / 010-4762-4956',
            'receiver': '홍한나',
        }
        payload.update(overrides)
        return self.service.create_sample(
            PROJECT_ID, payload, actor_subject='user:pm')


class TestExample2LandsWithoutLoss(_Fixture):
    """운영자가 준 예시 2 — 한 시료가 4번 들어오고 2번 나간 이력 — 이 손실 없이 들어간다.

    ⚠️ 원래 데이터는 이 여섯 사건이 `intake_cert`·`received_date`·`released_date`·
    `note` 네 칸에 줄바꿈으로 쌓여 있었고, **칸마다 줄 개수가 달라** 짝지을 수 없었다
    (반입증 4 · 수령 2 · 반출 2 · Note 가 가리키는 반입 3).
    """

    #: Note 와 각 칸을 사람이 읽어 복원한 실제 사건 순서.
    EVENTS = (
        ('received', '2025-09-30', None, '20250930-1031009813', None),
        ('released', '2025-10-17', '김용태프로님', None, None),
        ('received', '2025-10-21', None, '20251017-1827080742', '10/21일 재 반입'),
        ('released', '2025-10-23', '김용태 프로님', None,
         'NR n41/48 CEM 디버깅건으로 임시 반출'),
        ('received', '2025-10-28', None, '20251027-1724065293', '10/28일 재 반입'),
        ('received', '2025-11-04', None, '20251104-1432333773', '11/4일 재 반입'),
    )

    def _append_all(self, sample_id):
        for event_type, occurred_on, counterparty, cert, reason in self.EVENTS:
            self.service.append_custody_event(
                PROJECT_ID, sample_id,
                {'event_type': event_type, 'occurred_on': occurred_on,
                 'counterparty': counterparty, 'intake_cert_number': cert,
                 'reason': reason},
                actor_subject='user:pm',
            )

    def test_every_event_is_stored_and_readable_as_a_first_class_row(self):
        sample = self._sample()
        self._append_all(sample['id'])

        events = self.service.list_custody_events(PROJECT_ID, sample['id'])['items']
        assert len(events) == len(self.EVENTS)
        # 목록은 최신이 먼저다 — 화면이 '지금 어떤 상태인가'를 맨 위에서 읽는다.
        assert events[0]['occurred_on'] == '2025-11-04'
        assert events[-1]['occurred_on'] == '2025-09-30'
        # 반출 사유가 자유 텍스트에서 나와 자기 칸을 갖는다.
        released = [e for e in events if e['event_type'] == 'released']
        assert [e['reason'] for e in released] == [
            'NR n41/48 CEM 디버깅건으로 임시 반출', None,
        ]
        # 날짜와 사람이 분리돼 있다 ('2025-10-17 김용태프로님' 이 한 칸이었다).
        assert {(e['occurred_on'], e['counterparty']) for e in released} == {
            ('2025-10-23', '김용태 프로님'), ('2025-10-17', '김용태프로님'),
        }

    def test_current_custody_is_computable_and_was_not_before(self):
        sample = self._sample()
        self._append_all(sample['id'])

        current = self.service.get_sample(PROJECT_ID, sample['id'])
        assert current['custody_state'] == 'in_custody'
        assert current['custody_event_count'] == len(self.EVENTS)

        self.service.append_custody_event(
            PROJECT_ID, sample['id'],
            {'event_type': 'released', 'occurred_on': '2025-11-20'},
            actor_subject='user:pm',
        )
        assert self.service.get_sample(
            PROJECT_ID, sample['id'])['custody_state'] == 'released'

    def test_a_sample_with_no_events_is_unknown_not_released(self):
        """기존 시료는 사건 없이 넘어온다 (결정 9: 자동 변환하지 않는다).

        그것을 '반출됨'으로 읽으면 사람이 적지 않은 사실을 시스템이 지어내는 것이다.
        """
        sample = self._sample()
        current = self.service.get_sample(PROJECT_ID, sample['id'])
        assert current['custody_state'] is None
        assert current['custody_event_count'] == 0

    def test_the_original_text_columns_are_untouched(self):
        """결정 9 — 원문을 보존한다. 한 줄도 잃지 않는다."""
        raw_note = '11/4일 재 반입\n10/28일 재 반입\n10/23일 NR n41/48 CEM 디버깅건으로 임시 반출'
        sample = self._sample(note=raw_note, intake_cert='20251104-1432333773\n20251027-1724065293')
        self._append_all(sample['id'])

        current = self.service.get_sample(PROJECT_ID, sample['id'])
        assert current['note'] == raw_note
        assert current['intake_cert'].startswith('20251104-1432333773\n')


class TestIntakeCertIsAnEventNotASampleAttribute(_Fixture):
    """반입증은 고객사가 한 번의 납품에 한 장 발행하고 시료 여럿(12대)이 공유한다.

    그래서 배치는 별도 표 없이 ``(project_id, intake_cert_number)`` 로 복원된다.
    """

    def test_one_certificate_groups_the_whole_shipment(self):
        cert = '20251104-1432333773'
        shipment = [self._sample(sample_number=f'#{index}',
                                 sample_description=f'SM-F968U1_Main Conduction #{index}')
                    for index in range(1, 5)]
        for sample in shipment:
            self.service.append_custody_event(
                PROJECT_ID, sample['id'],
                {'event_type': 'received', 'occurred_on': '2025-11-04',
                 'intake_cert_number': cert},
                actor_subject='user:pm',
            )
        # 다른 납품으로 들어온 시료 하나는 그 배치에 속하지 않는다.
        other = self._sample(sample_number='#9', sample_description='SM-F968U1_Main Conduction #9')
        self.service.append_custody_event(
            PROJECT_ID, other['id'],
            {'event_type': 'received', 'occurred_on': '2025-09-30',
             'intake_cert_number': '20250930-1031009813'},
            actor_subject='user:pm',
        )

        read = PostgresCentralSampleInventoryReadAdapter(
            lambda: QmarkConnection(self.db_path))
        ids = [sample['id'] for sample in shipment] + [other['id']]
        rows = read.list_custody_events(PROJECT_ID, ids)
        batch = {row['sample_id'] for row in rows if row['intake_cert_number'] == cert}
        assert batch == {sample['id'] for sample in shipment}


class TestCorrectionIsDeleteNotEdit(_Fixture):
    def test_a_wrongly_recorded_event_can_be_removed(self):
        sample = self._sample()
        wrong = self.service.append_custody_event(
            PROJECT_ID, sample['id'],
            {'event_type': 'released', 'occurred_on': '2025-10-77'},
            actor_subject='user:pm',
        )
        receipt = self.service.delete_custody_event(
            PROJECT_ID, sample['id'], wrong['id'], actor_subject='user:pm')
        assert receipt == {'custody_event_id': wrong['id'], 'deleted': True}
        assert self.service.list_custody_events(PROJECT_ID, sample['id'])['items'] == []
        # 지운 뒤 보유 상태는 '알 수 없음'으로 돌아간다 — 남은 사건이 없기 때문이다.
        assert self.service.get_sample(PROJECT_ID, sample['id'])['custody_state'] is None

    def test_deleting_an_unknown_event_is_not_found(self):
        sample = self._sample()
        with pytest.raises(SampleInventoryNotFoundError):
            self.service.delete_custody_event(
                PROJECT_ID, sample['id'], 'no-such-event', actor_subject='user:pm')

    def test_a_custody_write_does_not_bump_row_version(self):
        """편집 화면이 열려 있어도 헛된 409 가 나지 않아야 한다."""
        sample = self._sample()
        before = self.service.get_sample(PROJECT_ID, sample['id'])['row_version']
        self.service.append_custody_event(
            PROJECT_ID, sample['id'], {'event_type': 'received'}, actor_subject='user:pm')
        after = self.service.get_sample(PROJECT_ID, sample['id'])['row_version']
        assert before == after


class TestCustodyEventValidation:
    def test_event_type_is_required(self):
        """나머지는 아는 만큼만 적지만, 이것이 없으면 보유 상태를 계산할 수 없다."""
        with pytest.raises(SampleInvalidCustodyEvent):
            validate_custody_event({'occurred_on': '2025-10-23'})

    def test_an_unknown_event_type_is_refused(self):
        with pytest.raises(SampleInvalidCustodyEvent):
            validate_custody_event({'event_type': 'borrowed'})

    def test_a_field_outside_the_contract_is_refused(self):
        with pytest.raises(SampleUnknownField):
            validate_custody_event({'event_type': 'received', 'smsn': 'X'})

    def test_partial_events_are_allowed(self):
        """실제 엑셀에는 날짜만 있는 행, 반입증만 있는 행이 섞여 있다."""
        value = validate_custody_event({'event_type': 'received'})
        assert value['event_type'] == 'received'
        assert value['occurred_on'] is None

    def test_custody_state_rule_lives_in_one_place(self):
        assert custody_state({'event_type': 'received'}) == 'in_custody'
        assert custody_state({'event_type': 'released'}) == 'released'
        assert custody_state(None) is None


class TestTesterAxisNowHasAWindow(_Fixture):
    """시험 실무자 축은 스키마가 이미 옳았다 — 없던 것은 그것을 읽을 창이다."""

    def test_the_full_intake_history_is_readable_not_only_the_latest(self):
        sample = self._sample(latest_intake={'bl': 'BL-1', 'intake_date': '2025-09-30'})
        for index, bl in enumerate(('BL-2', 'BL-3'), start=1):
            current = self.service.get_sample(PROJECT_ID, sample['id'])
            self.service.patch_sample(
                PROJECT_ID, sample['id'],
                {'latest_intake': {'bl': bl, 'intake_date': f'2025-10-2{index}'}},
                expected_version=current['row_version'], actor_subject='user:rf',
            )

        history = self.service.list_intakes(PROJECT_ID, sample['id'])['items']
        assert [row['bl'] for row in history] == ['BL-1', 'BL-2', 'BL-3']

    def test_the_intake_envelope_does_not_leak_sample_columns(self):
        """sample_number/test_category 는 입고 행의 값이 아니라 시료의 값이다.

        읽기 어댑터가 그 둘을 join 해 오는 것은 엑셀 export 가 여러 시료를 한 번에
        훑기 때문이며, 계약이 ``additionalProperties: False`` 이므로 그대로 흘리면
        위반이다.
        """
        sample = self._sample(latest_intake={'bl': 'BL-1'})
        row = self.service.list_intakes(PROJECT_ID, sample['id'])['items'][0]
        assert 'sample_number' not in row
        assert 'test_category' not in row
        assert row['bl'] == 'BL-1'
        assert row['sample_id'] == sample['id']

    def test_an_unknown_sample_is_not_found(self):
        with pytest.raises(SampleInventoryNotFoundError):
            self.service.list_intakes(PROJECT_ID, 'no-such-sample')
        with pytest.raises(SampleInventoryNotFoundError):
            self.service.list_custody_events(PROJECT_ID, 'no-such-sample')


class TestClassificationIsStored(_Fixture):
    def test_device_accessory_and_description_survive_a_round_trip(self):
        sample = self._sample(sample_kind='Accessory', test_category=None,
                              sample_description='SM-F968U1_Dummy Batt')
        current = self.service.get_sample(PROJECT_ID, sample['id'])
        assert current['sample_kind'] == 'Accessory'
        assert current['sample_description'] == 'SM-F968U1_Dummy Batt'
        # Accessory 는 Conducted/Radiated 를 갖지 않는다 (결정 8).
        assert current['test_category'] is None


class TestTheWriteSqlCannotSilentlyShift:
    """SAMPLE_EDITABLE_FIELDS 의 순서는 UPDATE 문의 컬럼 순서와 묶여 있다.

    ``_sample_update_values`` 가 그 튜플을 훑어 값을 만들기 때문에, 한쪽만 고치면
    **오류 없이** 값이 옆 칸으로 밀린다 — 라벨넘버가 시료종류 칸에 들어가고 아무도
    모른다. 이 결함 형태는 예외를 던지지 않으므로 사람이 눈으로 지킬 수 없다.
    """

    def test_update_sql_column_order_matches_the_domain_tuple(self):
        from fcc_test_platform.application import central_sample_inventory_write_adapter as mod

        set_clause = mod.SAMPLE_UPDATE_SQL.split('WHERE')[0]
        columns = re.findall(r'"(\w+)" = %s', set_clause)
        assert columns == list(SAMPLE_EDITABLE_FIELDS) + [
            'status', 'row_version', 'deleted_at', 'deleted_by', 'updated_at',
        ]

    def test_insert_sql_column_order_matches_the_domain_tuple(self):
        from fcc_test_platform.application import central_sample_inventory_write_adapter as mod

        head = mod.SAMPLE_INSERT_SQL.split('VALUES')[0]
        columns = [name for name in re.findall(r'"(\w+)"', head) if name != 'samples']
        editable = [name for name in columns if name in SAMPLE_EDITABLE_FIELDS]
        assert editable == list(SAMPLE_EDITABLE_FIELDS)
        # 컬럼 수와 값 자리 수가 어긋나면 드라이버가 거부하므로 여기서 함께 센다.
        # row_version 만 리터럴 1 이고 나머지는 전부 %s 다.
        values = mod.SAMPLE_INSERT_SQL.split('VALUES')[1]
        assert len(columns) == values.count('%s') + 1
