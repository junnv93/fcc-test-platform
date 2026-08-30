from __future__ import annotations

import json
import os
from uuid import UUID

import pytest

from fcc_test_platform.application.central_sample_inventory_read_adapter import (
    PostgresCentralSampleInventoryReadAdapter,
)
from fcc_test_platform.application.central_sample_inventory_service import (
    CentralSampleInventoryService,
    SampleInventoryConflictError,
)
from fcc_test_platform.application.central_sample_inventory_write_adapter import (
    PostgresCentralSampleInventoryWriteAdapter,
)
from tests.support.central_pg_sqlite_shim import QmarkConnection
from tests.support.sample_inventory_central import make_central_db, seed_project


PROJECT_ID = 'project-crud'


def _service(db_path: str) -> CentralSampleInventoryService:
    read = PostgresCentralSampleInventoryReadAdapter(lambda: QmarkConnection(db_path))
    write = PostgresCentralSampleInventoryWriteAdapter(lambda: QmarkConnection(db_path))
    return CentralSampleInventoryService(read, write)


def _create(service: CentralSampleInventoryService, **overrides):
    payload = {
        'sample_number': 'S-001',
        'sample_code': 'CODE-001',
        'test_category': 'Conduction',
        'serial_number': 'SYNTHETIC-SERIAL',
        'assigned_team': 'PM',
        'latest_intake': {'bl': 'BL-1', 'tech_group': 'G1'},
    }
    payload.update(overrides)
    return service.create_sample(PROJECT_ID, payload, actor_subject='user:pm')


class TestPlatformSampleInventoryCrud:
    def setup_method(self):
        self.db_path = make_central_db()
        seed_project(self.db_path, PROJECT_ID, model_name='MODEL-CRUD')
        self.service = _service(self.db_path)

    def teardown_method(self):
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    def test_create_writes_complete_revision_and_latest_intake(self):
        sample = _create(self.service)

        assert sample['status'] == 'active'
        assert sample['row_version'] == 1
        assert sample['latest_intake']['bl'] == 'BL-1'

        history = self.service.list_history(PROJECT_ID, sample['id'])
        assert len(history['items']) == 1
        revision = history['items'][0]
        assert revision['revision_number'] == 1
        assert revision['event_type'] == 'created'
        assert revision['actor_subject'] == 'user:pm'
        assert set(revision['changed_fields']) == {
            'sample_number', 'sample_code', 'test_category', 'label_number',
            'smsn', 'serial_number', 'intake_cert', 'assigned_team', 'sender',
            'receiver', 'received_date', 'released_date', 'note', 'status',
            'row_version', 'latest_intake',
        }
        assert revision['snapshot']['sample']['sample_id'] == sample['id']
        assert revision['snapshot']['sample']['serial_number'] == 'SYNTHETIC-SERIAL'

    def test_patch_is_atomic_append_only_and_uses_expected_version(self):
        sample = _create(self.service)
        updated = self.service.patch_sample(
            PROJECT_ID,
            sample['id'],
            {'note': 'operator note', 'latest_intake': {'ap': 'AP-2'}},
            expected_version=1,
            actor_subject='user:tester',
        )

        assert updated['row_version'] == 2
        assert updated['note'] == 'operator note'
        assert updated['latest_intake']['bl'] == 'BL-1'
        assert updated['latest_intake']['ap'] == 'AP-2'
        assert self.service.list_history(PROJECT_ID, sample['id'])['items'][0]['revision_number'] == 2

        with pytest.raises(SampleInventoryConflictError):
            self.service.patch_sample(
                PROJECT_ID, sample['id'], {'note': 'stale'},
                expected_version=1, actor_subject='user:tester',
            )

        history = self.service.list_history(PROJECT_ID, sample['id'])['items']
        assert len(history) == 2
        assert history[0]['changed_fields'] == ['note', 'latest_intake']

    def test_sample_edit_does_not_duplicate_an_unchanged_intake(self):
        sample = _create(self.service)
        self.service.patch_sample(
            PROJECT_ID,
            sample['id'],
            {'note': 'sample-only-edit', 'latest_intake': {'bl': 'BL-1'}},
            expected_version=1,
            actor_subject='user:tester',
        )

        current = self.service.list_samples(project_id=PROJECT_ID)['items'][0]
        assert current['note'] == 'sample-only-edit'
        assert current['intake_count'] == 1

    def test_soft_delete_restore_and_hard_delete_preserve_only_tombstone_audit(self):
        sample = _create(self.service, serial_number='PII-FREE-SYNTHETIC')
        snapshot = self.service.build_measurement_snapshot(PROJECT_ID, sample['id'])

        def fk_connection():
            connection = QmarkConnection(self.db_path)
            cursor = connection.cursor()
            cursor.execute('PRAGMA foreign_keys = ON')
            cursor.close()
            return connection

        # Complement the live PostgreSQL proof with a real SQLite FK action. The
        # production adapter SQL still runs through the shared %s shim; only the
        # disposable fixture adds the equivalent ON DELETE SET NULL constraint.
        connection = fk_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                'CREATE UNIQUE INDEX "ux_samples_id_fk" ON "samples" ("id")',
            )
            cursor.execute(
                'CREATE TABLE "test_sessions" ('
                '"id" TEXT, "project_id" TEXT, "sample_id" TEXT, '
                '"session_origin" TEXT, "sample_snapshot_json" TEXT, '
                '"sample_snapshot_schema_version" TEXT, '
                'FOREIGN KEY ("sample_id") REFERENCES "samples"("id") '
                'ON DELETE SET NULL)',
            )
            snapshot_bytes = json.dumps(
                snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            )
            cursor.execute(
                'INSERT INTO "test_sessions" '
                '("id", "project_id", "sample_id", "session_origin", '
                '"sample_snapshot_json", "sample_snapshot_schema_version") '
                'VALUES (%s, %s, %s, %s, %s, %s)',
                (
                    'session-fk-proof', PROJECT_ID, sample['id'], 'WEB_SESSION',
                    snapshot_bytes, snapshot.get('schema_version'),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        deleted = self.service.soft_delete(
            PROJECT_ID, sample['id'], expected_version=1, actor_subject='user:pm',
        )
        assert deleted['status'] == 'deleted'
        assert deleted['deleted_at']
        assert deleted['deleted_by'] == 'user:pm'
        assert self.service.list_samples(project_id=PROJECT_ID)['items'] == []
        assert self.service.list_samples(
            project_id=PROJECT_ID, include_deleted=True,
        )['items'][0]['status'] == 'deleted'

        restored = self.service.restore(
            PROJECT_ID, sample['id'], expected_version=2, actor_subject='user:pm',
        )
        assert restored['status'] == 'active'
        assert restored['deleted_at'] is None
        assert restored['deleted_by'] is None

        fk_service = CentralSampleInventoryService(
            PostgresCentralSampleInventoryReadAdapter(fk_connection),
            PostgresCentralSampleInventoryWriteAdapter(fk_connection),
            clock=lambda: '2026-07-28T00:00:00+00:00',
        )
        receipt = fk_service.hard_delete(sample['id'], actor_subject='system-admin')
        assert receipt == {'sample_id': sample['id'], 'hard_deleted': True}

        connection = fk_connection()
        try:
            cursor = connection.cursor()
            cursor.execute('SELECT COUNT(*) FROM samples WHERE id = %s', (sample['id'],))
            assert cursor.fetchone()[0] == 0
            cursor.execute(
                'SELECT sample_id, sample_snapshot_json, sample_snapshot_schema_version '
                'FROM test_sessions WHERE id = %s',
                ('session-fk-proof',),
            )
            sample_id, retained_snapshot, retained_version = cursor.fetchone()
            assert sample_id is None
            assert retained_snapshot == snapshot_bytes
            assert retained_version == snapshot.get('schema_version')
            cursor.execute(
                'SELECT event_type, detail_json FROM audit_events WHERE event_type = %s',
                ('sample.hard_deleted',),
            )
            event_type, detail_json = cursor.fetchone()
            assert event_type == 'sample.hard_deleted'
            assert 'PII-FREE-SYNTHETIC' not in detail_json
            assert json.loads(detail_json) == {
                'project_id': PROJECT_ID,
                'reason': 'system_admin_request',
                'revision_count': 3,
                'sample_id': sample['id'],
            }
        finally:
            connection.close()

    def test_missing_actor_fails_before_write_port(self):
        class MustNotWrite:
            def create_sample(self, *args, **kwargs):  # pragma: no cover - failure guard
                raise AssertionError('write port was touched')

        read = PostgresCentralSampleInventoryReadAdapter(lambda: QmarkConnection(self.db_path))
        service = CentralSampleInventoryService(read, MustNotWrite())
        with pytest.raises(PermissionError):
            service.create_sample(PROJECT_ID, {'sample_number': 'S'}, actor_subject='anonymous')

    def test_hard_delete_serializes_postgres_uuid_project_id_in_tombstone(self):
        class Cursor:
            def __init__(self):
                self.fetchone_value = None
                self.audit_parameters = None

            def execute(self, statement, parameters=()):
                if 'SELECT "id", "project_id"' in statement:
                    self.fetchone_value = (
                        UUID('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
                        UUID('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'),
                    )
                elif 'SELECT COUNT(*) AS "revision_count"' in statement:
                    self.fetchone_value = (0,)
                elif 'INSERT INTO "audit_events"' in statement:
                    self.audit_parameters = tuple(parameters)
                    self.fetchone_value = None
                else:
                    self.fetchone_value = None

            def fetchone(self):
                return self.fetchone_value

            def close(self):
                return None

        class Connection:
            def __init__(self):
                self.cursor_instance = Cursor()
                self.committed = False

            def cursor(self):
                return self.cursor_instance

            def commit(self):
                self.committed = True

            def rollback(self):
                return None

            def close(self):
                return None

        connection = Connection()
        write = PostgresCentralSampleInventoryWriteAdapter(lambda: connection)
        result = write.hard_delete(
            'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            actor_subject='system-admin',
            occurred_at='2026-08-25T00:00:00+00:00',
        )

        assert result['hard_deleted'] is True
        assert connection.committed is True
        assert connection.cursor_instance.audit_parameters is not None
        audit_detail = json.loads(connection.cursor_instance.audit_parameters[4])
        assert audit_detail['project_id'] == 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
