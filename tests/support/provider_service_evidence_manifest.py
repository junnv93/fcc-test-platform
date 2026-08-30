"""A valid provider service deployment evidence manifest, built once.

Two suites need one: the provider CLI tests that round-trip it through
``scripts/provider_service_deployment_evidence.py``, and the platform workflow
test that assembles it into a deployment evidence bundle. Until 2026-08-16 the
second borrowed the first's private builder across files, which made a
*platform* test depend on a *provider* test file — and that dependency is what
broke when the provider CLI stopped being platform-owned.

Deliberately importing nothing first-party. A test-root helper is attributed to
the lane of what it imports, and a helper that imports nothing belongs to no
lane: it travels into whichever box the delivery closure pulls it into, and
carries no lane's dependencies with it. That is the distinction the 2026-08-15
``tests/support/`` attempt missed — it folded four call sites into a helper that
imported a contracts-lane installer, so the helper followed the installer into
the contracts box and took FastAPI with it.

Importers must spell it ``from support.provider_service_evidence_manifest
import ...``, rooted at the test root, not ``from tests.support...``. Both work
at runtime; only the first is a name the delivery closure's module index
carries, so the ``tests.``-prefixed spelling ships a box that cannot import its
own suite. That blind spot is real and repository-wide (86 sites) and is
recorded in the tech-debt ledger rather than repaired here.
"""
from __future__ import annotations

import sys


def valid_provider_service_deployment_manifest() -> dict:
    """Evidence that satisfies every rule in the v1 schema."""
    return {
        'schema_version': 1,
        'provider_id': 'fcc-unlicensed-conducted',
        'host_id': 'lab-pc-01',
        'deployed_at': '2026-05-15T09:50:00+09:00',
        'service': {
            'service_name': 'fcc-unlicensed-headless-api',
            'service_manager': 'nssm',
            'start_mode': 'automatic',
            'command': [
                sys.executable,
                '-m',
                'uvicorn',
                '--factory',
                'headless_api_app:create_app',
                '--host',
                '127.0.0.1',
                '--port',
                '8000',
            ],
        },
        'environment': {
            'FCC_HEADLESS_DB_PATH': 'C:/FCCData/unlicensed/headless.fcc.db',
            'FCC_HEADLESS_ARTIFACT_ROOTS': 'C:/FCCData/unlicensed/artifacts',
            'FCC_HEADLESS_REPORT_OUTPUT_DIR': 'C:/FCCData/unlicensed/reports',
            'FCC_HEADLESS_LOG_DIR': 'C:/FCCData/unlicensed/logs/headless-api',
            'FCC_HEADLESS_HEALTH_URL': 'http://127.0.0.1:8000/health',
            'FCC_HEADLESS_AUTH_TOKEN': '<redacted>',
        },
        'health_check': {
            'url': 'http://127.0.0.1:8000/health',
            'status_code': 200,
            'checked_at': '2026-05-15T09:51:00+09:00',
        },
        'log_collection': {
            'enabled': True,
            'log_dir': 'C:/FCCData/unlicensed/logs/headless-api',
            'collector': 'company-log-agent',
            'retention_days': 30,
        },
        'monitoring_alert': {
            'enabled': True,
            'health_url': 'http://127.0.0.1:8000/health',
            'alert_channel': 'Teams',
            'alert_target': 'fcc-lab-ops',
            'interval_seconds': 60,
        },
        'backup_scope': {
            'database_path': 'C:/FCCData/unlicensed/headless.fcc.db',
            'artifact_roots': ['C:/FCCData/unlicensed/artifacts'],
            'report_output_dir': 'C:/FCCData/unlicensed/reports',
        },
    }
