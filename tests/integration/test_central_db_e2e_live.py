"""Env-gated end-to-end live proof against a real central PostgreSQL database.

This test runs the *real* migration/ingestion/report-sourcing chain against a
live PostgreSQL instance addressed by ``FCC_CENTRAL_DB_URL``. It is SKIPPED when
that env var is unset, so the routine CI/regression suite never acquires a live
database dependency (no FakeConnection here — that is exactly the gap this proof
closes). To run it locally, start the dedicated proof cluster and export
``FCC_CENTRAL_DB_URL`` (see docs/platform/central_db_live_proof_readiness.md).

The repeatable proof logic lives in ``scripts/platform_central_db_live_proof.py``;
this test is a thin assertion wrapper so the orchestrator and the gate share one
SSOT implementation.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (PROJECT_ROOT, PROJECT_ROOT / 'src', PROJECT_ROOT / 'scripts'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fcc_test_kernel.domain.models.sample_inventory import SNAPSHOT_SCHEMA_VERSION  # noqa: E402
from fcc_test_kernel.domain.models.session_provenance import SessionOrigin  # noqa: E402

# Read through the conftest quarantine rather than `os.environ`. The suite
# moves every `FCC_CENTRAL_*` name out of the process environment before
# collection so that nothing picks one up by accident; this proof wants two of
# them on purpose, so it names them. Reading `os.environ` here would find
# nothing and this whole file would skip forever while the lane stayed green.
from support.ambient_env import ambient_config_env  # noqa: E402

_DSN = ambient_config_env('FCC_CENTRAL_DB_URL').strip()
_UPGRADE_DSN = ambient_config_env('FCC_CENTRAL_DB_UPGRADE_URL').strip()

pytestmark = pytest.mark.skipif(
    not _DSN or not _UPGRADE_DSN,
    reason=(
        'FCC_CENTRAL_DB_URL and FCC_CENTRAL_DB_UPGRADE_URL must be set — '
        'live central PostgreSQL proof skipped'
    ),
)


PROOF_SEED = 'pytest-e2e'


def _synthetic_reconstruction(bundle: dict) -> dict:
    """Report output metadata assembled from the proof's OWN seed handoff.

    Stage 4 is a *platform* contract — one report_runs parent before its
    report_outputs children, replayed idempotently. What it consumes is
    metadata, not DOCX bytes, so this file proves it without a provider
    repository present. That is deliberate: an extracted fcc-test-platform
    repository has no application/reporting/ at all, and a live proof it cannot
    run is not a proof of that repository.

    Building the metadata out of ``report_axis.demo_measurement_seed`` also
    asserts something real about the handoff — that the identities the platform
    publishes are sufficient to address the rows it will later ingest.

    The other half (that a REAL provider reconstruction produces metadata this
    shape) is proven by tests/integration/test_provider_report_reconstruction_live.py
    and, statically, by the derived key-agreement seal in
    tests/test_platform_provider_crossing_closure.py.
    """
    from platform_central_db_live_proof import RECONSTRUCTION_EVIDENCE_KEY

    lanes = {}
    for lane, lane_bundle in bundle['lanes'].items():
        seed = lane_bundle['stages']['report_axis']['demo_measurement_seed']
        lanes[lane] = {RECONSTRUCTION_EVIDENCE_KEY: {
            'valid': True,
            'report_run_id': seed['report_run_id'],
            'provider_id': seed['provider_id'],
            'session_id': seed['session_id'],
            'project_id': seed['project_id'],
            'status': seed['report_run_status'],
            'excel_source_used': False,
            'generated_outputs': [{
                'output_type': 'docx',
                'relative_path': f'{lane}/synthetic-report.docx',
                'file_name': 'synthetic-report.docx',
                'sha256': '0' * 64,
                'storage_backend': 'filesystem',
                'byte_size': 1024,
            }],
        }}
    return {'schema_version': 1, 'lanes': lanes}


@lru_cache(maxsize=1)
def _run():
    """Drive the documented runbook shape: full proof (1), then ingestion (3).

    Two calls, not one, and not an artefact of the test: the full proof refuses
    to start against anything but an empty database, while report ingestion
    requires the database that proof just seeded. Those preconditions are each
    other's negation, which is exactly why step 3 is a separate entry point.
    """
    from platform_central_db_live_proof import (
        DEFAULT_REGISTRY_PATH,
        RECONSTRUCTION_EVIDENCE_KEY,
        _default_provider_code,
        run_live_proof,
        run_report_ingestion_proof,
    )

    provider_code = _default_provider_code(Path(DEFAULT_REGISTRY_PATH))
    kwargs = {
        'upgrade_dsn': _UPGRADE_DSN,
        'proof_seed': PROOF_SEED,
        'provider_code': provider_code,
    }
    seeded = run_live_proof(_DSN, **kwargs)
    reconstruction = _synthetic_reconstruction(seeded)
    ingested = run_report_ingestion_proof(
        _DSN,
        reconstruction_by_lane={
            lane: body[RECONSTRUCTION_EVIDENCE_KEY]
            for lane, body in reconstruction['lanes'].items()
        },
        **kwargs,
    )
    return seeded, ingested


def test_central_db_live_proof_passes_end_to_end():
    bundle, _ = _run()
    assert bundle['verdict'] == 'PASS', bundle
    assert bundle['cutoff']['stable'] is True
    assert bundle['cutoff']['before'] == bundle['cutoff']['after']
    assert bundle['cutoff']['before']['commit']
    assert bundle['cutoff']['before']['status_sha256']


def test_migration_evidence_validates_against_schema_ssot():
    from fcc_test_platform.db_migration_evidence import (
        central_db_migration_evidence_errors,
    )

    bundle, _ = _run()
    schema = json.loads(
        (PROJECT_ROOT / 'docs' / 'platform' / 'central_db_schema.v1.json').read_text(encoding='utf-8')
    )
    manifest = bundle['lanes']['fresh']['stages']['migration']['migration_manifest']
    assert central_db_migration_evidence_errors(manifest, schema) == []


def test_upgrade_lane_uses_runner_ledger_and_db_owned_default():
    bundle, _ = _run()
    upgrade = bundle['lanes']['upgrade']['stages']['migration']
    assert upgrade['runner']['pre_012_migrate']['exit_code'] == 0
    assert upgrade['runner']['candidate_reconcile']['exit_code'] == 0
    assert upgrade['runner']['candidate_migrate']['exit_code'] == 0
    assert upgrade['pre_012_report_runs_created_at_default']['default'] is None
    assert upgrade['report_runs_created_at_default_after']['default'] == 'now()'
    # The upgrade lane starts before 012 and is migrated forward by the repository
    # runner, so 012 must be in the ledger. The ledger's LAST entry is whatever
    # migration is newest on disk — asserting the literal '012' there was true
    # when this proof was written and went stale the day 013 landed, which nothing
    # noticed because the assertion is only reachable with a live DSN. Derive it.
    versions = upgrade['ledger_after']['versions']
    assert '012_report_run_ingestion_parent' in versions
    newest_on_disk = sorted(
        path.stem for path in (PROJECT_ROOT / 'docs' / 'platform' / 'migrations').glob('*.sql')
    )[-1]
    assert versions[-1] == newest_on_disk


def test_migration_029_dispositions_fk_and_rerun_are_concrete():
    bundle, _ = _run()
    for lane in ('fresh', 'upgrade'):
        migration = bundle['lanes'][lane]['stages']['migration']
        proof = migration['migration_029']
        assert proof['migration_version'] == '029_web_sample_inventory'
        assert proof['repository_checksum'] == proof['ledger_checksum']
        assert proof['postgresql']['postgresql_version'].startswith('PostgreSQL')
        assert proof['postgresql']['fk_sample_id']['on_delete_set_null'] is True
        assert proof['postgresql']['web_snapshot_check']['convalidated'] is True
        assert proof['hard_delete_fk_proof']['fk_sample_id_after_delete'] is None
        assert proof['hard_delete_fk_proof']['counts'] == {
            'samples': {'before': 1, 'after': 0},
            'sample_intakes': {'before': 1, 'after': 0},
            'sample_inventory_revisions': {'before': 1, 'after': 0},
        }
        assert proof['state_checksum_stable'] is True
        assert proof['ledger_checksum_stable'] is True
        assert proof['rerun']['exit_code'] == 0
        assert proof['rerun_status']['exit_code'] == 0
        assert proof['final_status']['pending'] == []
        assert proof['final_status']['drift'] == []

    upgrade = bundle['lanes']['upgrade']['stages']['migration']['migration_029']
    dispositions = upgrade['pre_029_dispositions']
    assert dispositions['complete']['session_origin'] == SessionOrigin.WEB_SESSION.value
    assert dispositions['complete']['sample_snapshot_schema_version'] == SNAPSHOT_SCHEMA_VERSION
    assert dispositions['complete']['snapshot_project_id']
    assert dispositions['complete']['snapshot_sample_id']
    for label in ('incomplete', 'mismatched'):
        assert dispositions[label]['session_origin'] is None
        assert dispositions[label]['sample_snapshot_sha256'] is None
    assert dispositions['local']['session_origin'] == SessionOrigin.LOCAL_PROGRAM.value
    assert dispositions['local']['sample_snapshot_sha256'] is None
    assert dispositions['baseline_revision_count'] == 1


def test_hard_deleted_live_session_is_cited_from_production_snapshot_read_path():
    bundle, _ = _run()
    from fcc_test_platform.application.central_project_read_adapter import (
        PostgresCentralProjectReadAdapter,
    )
    from fcc_test_platform.application.central_report_read_adapter import (
        PostgresCentralReportReadAdapter,
    )
    from fcc_test_platform.application.central_report_service import CentralReportService
    from fcc_test_platform.application.central_report_write_adapter import (
        PostgresCentralReportWriteAdapter,
    )
    from platform_central_db_live_proof import _connect

    for lane, dsn in (('fresh', _DSN), ('upgrade', _UPGRADE_DSN)):
        proof = bundle['lanes'][lane]['stages']['migration']['migration_029']
        delete = proof['hard_delete_fk_proof']
        connection_factory = lambda dsn=dsn: _connect(dsn)
        service = CentralReportService(
            PostgresCentralReportReadAdapter(connection_factory),
            PostgresCentralReportWriteAdapter(connection_factory),
            PostgresCentralProjectReadAdapter(connection_factory),
            clock=lambda: '2026-08-25T00:00:00+00:00',
        )
        citation = service.get_report_citation(
            delete['project_id'], edition='E2V1', session_id=delete['session_id'],
        )
        assert citation['samples'][0]['sample_number'] == (
            f'PROOF-FK-SAMPLE-pytest-e2e:{lane}'
        )
        assert citation['samples'][0]['serial_number'] == (
            f'PROOF-FK-SERIAL-pytest-e2e:{lane}'
        )
        assert citation['samples'][0]['latest_firmware']['bl'] == (
            f'PROOF-FK-BL-pytest-e2e:{lane}'
        )


def test_fresh_and_upgrade_report_parent_replays_are_separate_and_idempotent():
    _, ingested = _run()
    fresh = ingested['lanes']['fresh']['stages']['report_ingestion']
    upgrade = ingested['lanes']['upgrade']['stages']['report_ingestion']
    for stage in (fresh, upgrade):
        assert stage['parent_count_after_first'] == 1
        assert stage['output_count_after_first'] == stage['expected_output_count']
        assert stage['parent_count_after_second'] == 1
        assert stage['output_count_after_second'] == stage['output_count_after_first']
        assert stage['replay_idempotent'] is True


def test_ingestion_replay_is_idempotent():
    bundle, _ = _run()
    stage = bundle['lanes']['fresh']['stages']['ingestion_idempotency']
    assert stage['first_run']['committed'] is True
    assert stage['second_run']['committed'] is True
    # Re-ingesting the same latest attempt must converge to the same state:
    # exactly one measurement, one is_latest=true attempt, and one coverage row.
    expected = {'results': 1, 'attempts': 1, 'is_latest_true': 1, 'coverage': 1}
    assert stage['state_after_first'] == expected
    assert stage['state_after_second'] == stage['state_after_first']
    assert stage['replay_idempotent'] is True


def test_ingestion_execution_evidence_validates():
    from fcc_test_platform.ingestion_execution_evidence import (
        ingestion_execution_errors,
    )

    bundle, _ = _run()
    manifest = bundle['lanes']['fresh']['stages']['ingestion_idempotency']['manifest']
    assert ingestion_execution_errors(manifest) == []


def test_report_run_parent_and_outputs_are_idempotent_and_db_timestamped():
    _, ingested = _run()
    stage = ingested['lanes']['fresh']['stages']['report_ingestion']

    assert stage['first_run']['committed'] is True
    assert stage['second_run']['committed'] is True
    assert stage['parent_count_after_first'] == 1
    assert stage['output_count_after_first'] == stage['expected_output_count']
    assert stage['parent_count_after_second'] == 1
    assert stage['output_count_after_second'] == stage['output_count_after_first']
    assert stage['replay_idempotent'] is True
    assert stage['parent_created_at_db_owned'] is True
    assert stage['generated_output_paths']


def test_the_report_axis_publishes_a_usable_provider_handoff():
    # Reconstructing an FCC report is provider work (see the module docstring of
    # scripts/provider_report_reconstruction_live_proof.py). What this proof owes
    # that provider is a seeded session plus the identities addressing it.
    #
    # This file does NOT import the provider proof to check that: it ships in the
    # fcc-test-platform box, which has no provider script in it, and an import
    # that only survives because the env gate skipped the test is a green that
    # means nothing. That the provider's own gatekeeper accepts these keys is
    # asserted where both files are readable —
    # tests/test_platform_provider_crossing_closure.py, by deriving the required
    # set from the provider source rather than restating it.
    bundle, _ = _run()
    for lane in ('fresh', 'upgrade'):
        stage = bundle['lanes'][lane]['stages']['report_axis']
        assert stage['report_ingestion_stage_is_a_separate_invocation'] is True
        assert stage['provider_proof_command']
        assert stage['report_ingestion_command']
        assert stage['confirmed_equipment_lists']
        seed = stage['demo_measurement_seed']
        assert seed['measurement_result_count'] >= 1
        assert seed['session_id'] and seed['project_id'] and seed['report_run_id']
        assert seed['ingestion_session_id'] == bundle['lanes'][lane]['session_id']
        assert seed['report_run_status']


def test_out_of_order_stale_attempt_does_not_demote_newer_latest():
    # Order-independent is_latest: re-ingesting a stale older attempt after a
    # newer one became latest must NOT crown the stale row (proven-then-fixed
    # live bug, 2026-06-13).
    from platform_central_db_live_proof import (
        DEFAULT_REGISTRY_PATH,
        _default_provider_code,
        run_out_of_order_replay_proof,
    )

    provider_code = _default_provider_code(Path(DEFAULT_REGISTRY_PATH))
    stage = run_out_of_order_replay_proof(_DSN, proof_seed='pytest-ooo', provider_code=provider_code)
    assert stage['valid'] is True
    assert stage['final_latest'] == [2]
    assert stage['attempt_count'] == 2


def test_a_missing_reconstruction_lane_is_refused_rather_than_skipped():
    # The seam's failure mode matters as much as its success: an operator running
    # the runbook out of order must get a named refusal, not a bundle that
    # quietly lacks stage 4 while claiming to include it.
    import json
    import tempfile

    from platform_central_db_live_proof import LiveProofError, _load_report_reconstruction

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'partial.json'
        # 'fresh' is well formed; only 'upgrade' is absent. A fixture that broke
        # both lanes would pass while the loader checked just the first one.
        from platform_central_db_live_proof import RECONSTRUCTION_EVIDENCE_KEY

        path.write_text(json.dumps({
            'lanes': {'fresh': {RECONSTRUCTION_EVIDENCE_KEY: {'report_run_id': 'x'}}},
        }), encoding='utf-8')
        with pytest.raises(LiveProofError) as excinfo:
            _load_report_reconstruction(path)
    assert 'upgrade' in str(excinfo.value)
