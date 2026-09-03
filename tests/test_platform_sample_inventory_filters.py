from __future__ import annotations

import os

import pytest

from fcc_test_platform.application.central_sample_inventory_read_adapter import (
    PostgresCentralSampleInventoryReadAdapter,
)
from fcc_test_platform.application.central_sample_inventory_service import (
    CentralSampleInventoryService,
)
from fcc_test_platform.application.central_sample_inventory_write_adapter import (
    PostgresCentralSampleInventoryWriteAdapter,
)
from fcc_test_kernel.domain.services.sample_inventory_policy import SampleInvalidFilter
from tests.support.central_pg_sqlite_shim import QmarkConnection
from tests.support.sample_inventory_central import make_central_db, seed_project


PROJECT_ID = 'project-filters'


def _service(db_path: str) -> CentralSampleInventoryService:
    return CentralSampleInventoryService(
        PostgresCentralSampleInventoryReadAdapter(lambda: QmarkConnection(db_path)),
        PostgresCentralSampleInventoryWriteAdapter(lambda: QmarkConnection(db_path)),
    )


class TestPlatformSampleInventoryFilters:
    def setup_method(self):
        self.db_path = make_central_db()
        seed_project(self.db_path, PROJECT_ID, model_name='MODEL-FILTERS')
        self.service = _service(self.db_path)
        self.pm = self.service.create_sample(
            PROJECT_ID, {'sample_number': 'PM-1', 'assigned_team': 'PM', 'test_category': 'Conduction'},
            actor_subject='seed',
        )
        self.rf = self.service.create_sample(
            PROJECT_ID, {'sample_number': 'RF-1', 'assigned_team': 'RF', 'test_category': 'Radiation'},
            actor_subject='seed',
        )
        self.service.soft_delete(
            PROJECT_ID, self.rf['id'], expected_version=1, actor_subject='seed',
        )

    def teardown_method(self):
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    def test_combined_current_filters_are_server_side_and_deleted_is_opt_in(self):
        page = self.service.list_samples(project_id=PROJECT_ID, team='pm', status='active')
        assert [item['sample_number'] for item in page['items']] == ['PM-1']
        assert self.service.list_samples(project_id=PROJECT_ID)['items'][0]['sample_number'] == 'PM-1'
        assert [item['sample_number'] for item in self.service.list_samples(
            project_id=PROJECT_ID, status='deleted', include_deleted=True,
        )['items']] == ['RF-1']
        assert {item['sample_number'] for item in self.service.list_samples(
            project_id=PROJECT_ID, status='all', include_deleted=True,
        )['items']} == {'PM-1', 'RF-1'}

    def test_filter_cursor_arity_is_rejected_before_database_access(self):
        with pytest.raises(SampleInvalidFilter):
            self.service.list_samples(project_id=PROJECT_ID, after='not-base64')
        with pytest.raises(SampleInvalidFilter):
            self.service.list_samples(
                project_id=PROJECT_ID, as_of='2026-01-01T00:00:00Z', after='WzFd',
            )

