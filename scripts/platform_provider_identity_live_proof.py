"""Live-PostgreSQL proof for provider-identity-coherence (2026-08-25).

The SQLite shim cannot answer this: the defect is that a JOIN filter and a
missing provider produce the same empty result, and any store reproduces that.
What the live run adds is the counterfactual measured on the SAME database the
screen talks to — the old read path answering 0 rows where the new one names the
reason.
"""
import json, sys
sys.path.insert(0, 'src')
import psycopg
from fcc_test_platform.application.central_reference_read_adapter import PostgresCentralReferenceReadAdapter
from fcc_test_platform.application.central_reference_write_adapter import PostgresCentralReferenceWriteAdapter
from fcc_test_platform.application.central_reference_service import CentralReferenceService
from fcc_test_platform.domain.ports.output.central_reference_port import (
    CentralReferenceError, ReferenceProviderNotFoundError,
    ReferenceProviderNotRegisteredError,
)

def offered_provider_ids():
    """What this deployment's picker offers, asked of the composed registry.

    Through the composition root rather than by importing the provider builder:
    the root is the single place allowed to know that builder, and this script
    belongs to the platform lane. Going through it also makes the proof use the
    very registry the running API uses, which is the fact under test.
    """
    from fcc_test_contracts.common.auth_config import HttpAuthConfig
    from fcc_test_platform.central_db_config import CentralDbConfig
    from fcc_test_platform.application.runtime_config import PlatformApiConfig
    from fcc_test_platform.api_composition import create_platform_runtime

    def _unused():  # pragma: no cover — the registry needs no database
        raise AssertionError('the registry must not need a database')

    runtime = create_platform_runtime(
        PlatformApiConfig(
            central=CentralDbConfig(
                database_url='postgresql://unused/registry-only',
                provider_id='unused-for-this-probe',
            ),
            auth=HttpAuthConfig(),
            allow_insecure=True,
        ),
        connection_factory=_unused,
    )
    return runtime.api_adapter._provider_ui_descriptor_registry.provider_ids()  # noqa: SLF001


DSN = sys.argv[1]
UNLICENSED_PROVIDER_ID = offered_provider_ids()[0]
factory = lambda: psycopg.connect(DSN)
read = PostgresCentralReferenceReadAdapter(factory)
OFFERED = (UNLICENSED_PROVIDER_ID, 'fcc-mmwave-conducted')
svc = CentralReferenceService(
    read, PostgresCentralReferenceWriteAdapter(factory),
    bundle_provider_id=UNLICENSED_PROVIDER_ID,
    offered_provider_ids=lambda: OFFERED,
)
out = {}
with psycopg.connect(DSN) as c:
    out['server_version'] = c.execute('SHOW server_version').fetchone()[0]
    out['registered_providers'] = [r[0] for r in c.execute(
        'SELECT provider_id FROM providers').fetchall()]
out['offered_by_picker'] = list(OFFERED)
rows, _ = svc.list_revisions(UNLICENSED_PROVIDER_ID)
out['coherent_listing_rows'] = len(rows)
out['coherent_families'] = len(svc.list_reference_families(UNLICENSED_PROVIDER_ID))

def probe(label, fn):
    try:
        fn(); out[label] = 'NOT REFUSED (defect)'
    except ReferenceProviderNotRegisteredError:
        out[label] = 'ReferenceProviderNotRegisteredError -> 404 REFERENCE_PROVIDER_NOT_REGISTERED'
    except ReferenceProviderNotFoundError:
        out[label] = 'ReferenceProviderNotFoundError -> 404 NOT_FOUND'

probe('offered_but_unregistered__list', lambda: svc.list_revisions('fcc-mmwave-conducted'))
probe('offered_but_unregistered__families', lambda: svc.list_reference_families('fcc-mmwave-conducted'))
probe('offered_but_unregistered__bundle', lambda: svc.build_bundle('fcc-mmwave-conducted', 'chamber-1'))
probe('neither_side_knows_it', lambda: svc.list_revisions('nobody-knows-this'))
out['counterfactual_old_read_path'] = (
    f'{len(read.list_revisions("fcc-mmwave-conducted"))} rows == HTTP 200 [] '
    '(indistinguishable from "registered, nothing published")')
def boom(): raise OSError('connection refused')
try:
    PostgresCentralReferenceReadAdapter(boom).provider_exists('x')
    out['outage_is_not_absence'] = 'answered False (defect)'
except CentralReferenceError:
    out['outage_is_not_absence'] = 'CentralReferenceError (loud, not False)'
out['ok'] = (
    out['coherent_listing_rows'] > 0
    and out['offered_but_unregistered__list'].startswith('ReferenceProviderNotRegistered')
    and out['neither_side_knows_it'].startswith('ReferenceProviderNotFound')
    and out['counterfactual_old_read_path'].startswith('0 rows')
)
print(json.dumps(out, indent=2))
