from __future__ import annotations

import os

from fcc_test_platform.application.central_sample_inventory_read_adapter import (
    PostgresCentralSampleInventoryReadAdapter,
)
from fcc_test_platform.application.central_sample_inventory_service import (
    CentralSampleInventoryService,
)
from fcc_test_platform.application.central_sample_inventory_write_adapter import (
    PostgresCentralSampleInventoryWriteAdapter,
)
from tests.support.central_pg_sqlite_shim import QmarkConnection
from tests.support.sample_inventory_central import make_central_db, seed_project


PROJECT_ID = 'project-history'


def _service(db_path: str) -> CentralSampleInventoryService:
    return CentralSampleInventoryService(
        PostgresCentralSampleInventoryReadAdapter(lambda: QmarkConnection(db_path)),
        PostgresCentralSampleInventoryWriteAdapter(lambda: QmarkConnection(db_path)),
        clock=lambda: '2026-01-04T00:00:00Z',
    )


class TestPlatformSampleInventoryHistory:
    def setup_method(self):
        self.db_path = make_central_db()
        seed_project(self.db_path, PROJECT_ID, model_name='MODEL-HISTORY')
        self.service = _service(self.db_path)

    def teardown_method(self):
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    def test_history_is_keyset_paged_and_revision_snapshots_are_complete(self):
        sample = self.service.create_sample(
            PROJECT_ID, {'sample_number': 'S-H', 'test_category': 'Radiation'},
            actor_subject='user:1',
        )
        self.service.patch_sample(
            PROJECT_ID, sample['id'], {'note': 'one'},
            expected_version=1, actor_subject='user:2',
        )
        self.service.patch_sample(
            PROJECT_ID, sample['id'], {'assigned_team': 'RF'},
            expected_version=2, actor_subject='user:3',
        )

        first = self.service.list_history(PROJECT_ID, sample['id'], limit=2)
        assert [item['revision_number'] for item in first['items']] == [3, 2]
        assert first['next_cursor']
        assert 'offset' not in self.service._read.__class__.list_history.__code__.co_consts

        second = self.service.list_history(
            PROJECT_ID, sample['id'], after=first['next_cursor'], limit=2,
        )
        assert [item['revision_number'] for item in second['items']] == [1]
        assert second['next_cursor'] is None
        assert second['items'][0]['snapshot']['sample']['sample_number'] == 'S-H'

    def test_as_of_is_inclusive_and_returns_same_revision_contract(self):
        sample = self.service.create_sample(
            PROJECT_ID, {'sample_number': 'S-ASOF', 'note': 'before'},
            actor_subject='user:1',
        )
        write = PostgresCentralSampleInventoryWriteAdapter(lambda: QmarkConnection(self.db_path))
        write.patch_sample(
            PROJECT_ID, sample['id'], {'note': 'at-cutoff'}, expected_version=1,
            actor_subject='user:2', occurred_at='2026-01-02T00:00:00Z',
        )
        write.patch_sample(
            PROJECT_ID, sample['id'], {'note': 'after'}, expected_version=2,
            actor_subject='user:3', occurred_at='2026-01-03T00:00:00Z',
        )

        page = self.service.list_samples(
            project_id=PROJECT_ID, as_of='2026-01-02T00:00:00Z', limit=10,
        )
        assert page['items'][0]['note'] == 'at-cutoff'
        assert page['items'][0]['as_of'] == '2026-01-02T00:00:00Z'
        assert page['items'][0]['revision_number'] == 2

