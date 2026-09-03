"""Central registration of a published plan (plan-delivery, 2026-09-02).

``published_plan_expectation`` is the progress denominator **and** the answer to
*"does central know this plan id?"* — ``build_measurement_snapshot`` reads it and
refuses a measurement start with ``published_plan_id is unknown`` when it is
empty. These seals hold the second meaning, which is the one that was silently
unserved: every path that filled the table connected straight to PostgreSQL, and
the box that authors plans cannot reach that tier.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fcc_test_kernel.application.central_contract.api_contracts import (  # noqa: E402
    PLATFORM_API_OPERATIONS,
    PLATFORM_API_PERMISSIONS,
    PLATFORM_API_ROUTES,
    PLATFORM_API_SCHEMAS,
)
from fcc_test_platform.domain.models.progress_time_catalog import (  # noqa: E402
    CatalogSource, PricingStatus, StandardTimeCatalog,
)
from fcc_test_kernel.domain.models.enums import MeasurementType  # noqa: E402
from fcc_test_platform.application.progress_ingest_service import (  # noqa: E402
    ProgressIngestService,
)
from fcc_test_platform.application.published_plan_expectation_service import (  # noqa: E402
    PublishedPlanExpectationError,
    PublishedPlanExpectationNotFound,
    PublishedPlanExpectationService,
)
from fcc_test_platform.application.published_plan_identity_adapter import (  # noqa: E402
    PostgresPublishedPlanIdentityAdapter,
)


PROJECT = '11111111-2222-3333-4444-555555555555'
PROVIDER_UUID = '99999999-8888-7777-6666-555555555555'


class _RecordingWritePort:
    def __init__(self) -> None:
        self.atoms = []

    def write_expectations(self, atoms, now):
        from fcc_test_platform.domain.ports.output.central_progress_write_port import (
            ExpectationWriteSummary,
        )
        self.atoms.extend(atoms)
        return ExpectationWriteSummary(inserted=len(atoms), updated=0)

    def upsert_catalog(self, **kwargs):  # pragma: no cover — not this path
        raise AssertionError('the ingest path must not write the catalog')


class _Identity:
    def __init__(self, *, provider=PROVIDER_UUID, project=True) -> None:
        self._provider = provider
        self._project = project

    def resolve_provider_uuid(self, provider_id):
        return self._provider

    def project_exists(self, project_id):
        return self._project


class _Catalog:
    def __init__(self, catalog) -> None:
        self._catalog = catalog

    def load_catalog(self, provider_id):
        self.seen = provider_id
        return self._catalog


def _service(*, catalog=None, identity=None):
    port = _RecordingWritePort()
    service = PublishedPlanExpectationService(
        identity_reader=identity or _Identity(),
        ingest_service=ProgressIngestService(port, clock=lambda: 'NOW'),
        catalog_reader=_Catalog(catalog),
        progress_area='unlicensed_conducted',
    )
    return service, port


def _conditions(count=2):
    return [
        {
            'condition_hash': f'{index:064d}',
            'technology': 'BLE',
            'band': '2.4G',
            'raw_test_type': 'Pk power',
        }
        for index in range(count)
    ]


class TestAnUnseededCatalogDoesNotBlockRegistration(unittest.TestCase):
    """⚠️ The count is complete without a catalog; only the ETA is missing.

    ``ProgressExpectationSyncService.run_once`` fails the whole plan when the
    catalog is absent, which is defensible for a pure denominator. It is not
    defensible here: refusing would mean a deployment that never seeded the
    operator's minutes workbook cannot start a measurement at all.
    """

    def test_conditions_are_registered_as_unpriced(self) -> None:
        service, port = _service(catalog=None)
        result = service.ingest(
            PROJECT, 'fcc-unlicensed-conducted',
            plan_id='PL-1', plan_published_at='2026-09-02T00:00:00+00:00',
            conditions=_conditions(3),
        )
        self.assertEqual(result['conditions'], 3)
        self.assertEqual(result['inserted'], 3)
        self.assertEqual(result['unpriced'], 3)
        self.assertEqual(result['priced'], 0)
        # ``null``, not ``0`` — "no ETA" and "an ETA of zero" are different facts.
        self.assertIsNone(result['catalog_version'])
        self.assertEqual(len(port.atoms), 3)
        for atom in port.atoms:
            self.assertIs(atom.pricing_status, PricingStatus.UNPRICED)
            self.assertEqual(atom.provider_id, PROVIDER_UUID)
            self.assertEqual(atom.plan_published_at, '2026-09-02T00:00:00+00:00')

    def test_a_seeded_catalog_prices_and_reports_its_version(self) -> None:
        catalog = StandardTimeCatalog.from_mapping(
            {MeasurementType.PK_POWER: 4.0}, version=7,
            source=CatalogSource.WORKBOOK_SEED,
        )
        service, port = _service(catalog=catalog)
        result = service.ingest(
            PROJECT, 'fcc-unlicensed-conducted',
            plan_id='PL-1', plan_published_at=None, conditions=_conditions(2),
        )
        self.assertEqual(result['priced'], 2)
        self.assertEqual(result['catalog_version'], 7)


class TestTheEnvelopeIsRefusedRatherThanHalfWritten(unittest.TestCase):
    def test_zero_conditions_is_refused(self) -> None:
        service, port = _service(catalog=None)
        with self.assertRaises(PublishedPlanExpectationError):
            service.ingest(
                PROJECT, 'p', plan_id='PL-1', plan_published_at=None,
                conditions=[],
            )
        self.assertEqual(port.atoms, [])

    def test_a_condition_without_its_hash_is_refused(self) -> None:
        service, port = _service(catalog=None)
        with self.assertRaises(PublishedPlanExpectationError):
            service.ingest(
                PROJECT, 'p', plan_id='PL-1', plan_published_at=None,
                conditions=[{'technology': 'BLE', 'band': '2.4G',
                             'raw_test_type': 'Pk power'}],
            )
        self.assertEqual(port.atoms, [])

    def test_an_unknown_provider_is_a_not_found(self) -> None:
        service, port = _service(catalog=None, identity=_Identity(provider=None))
        with self.assertRaises(PublishedPlanExpectationNotFound):
            service.ingest(
                PROJECT, 'nope', plan_id='PL-1', plan_published_at=None,
                conditions=_conditions(1),
            )
        self.assertEqual(port.atoms, [])

    def test_an_unknown_project_is_a_not_found(self) -> None:
        service, port = _service(catalog=None, identity=_Identity(project=False))
        with self.assertRaises(PublishedPlanExpectationNotFound):
            service.ingest(
                PROJECT, 'p', plan_id='PL-1', plan_published_at=None,
                conditions=_conditions(1),
            )
        self.assertEqual(port.atoms, [])


class TestANonUuidProjectIsNotFoundNotAnOutage(unittest.TestCase):
    """``projects.id`` is a PostgreSQL ``uuid``; a bad path param must not 503."""

    def test_the_shape_is_checked_before_the_query(self) -> None:
        def _never():  # pragma: no cover — must not be called
            raise AssertionError('a non-uuid must not reach the database')

        adapter = PostgresPublishedPlanIdentityAdapter(_never)
        self.assertFalse(adapter.project_exists('not-a-uuid'))


class TestTheContractDeclaresTheOperation(unittest.TestCase):
    def test_route_permission_and_schemas_are_declared(self) -> None:
        method, path = PLATFORM_API_ROUTES['ingest_published_plan_expectation']
        self.assertEqual(method, 'POST')
        self.assertIn('{project_id}', path)
        self.assertIn('{provider_id}', path)
        operation = PLATFORM_API_OPERATIONS['ingest_published_plan_expectation']
        self.assertEqual(
            operation['permission'],
            PLATFORM_API_PERMISSIONS['ingest_published_plan_expectation'],
        )
        self.assertIn(operation['request'], PLATFORM_API_SCHEMAS)
        self.assertIn(operation['response'], PLATFORM_API_SCHEMAS)

    def test_the_condition_schema_carries_no_provider_vocabulary(self) -> None:
        """⚠️ Central must not learn this provider's words.

        A published row carries ``mode_family``/``packet``/``capability_path``;
        putting those in the central schema would make every new provider a
        central migration. The four tokens here are the ones
        ``published_plan_expectation`` already stores.
        """
        properties = PLATFORM_API_SCHEMAS['PublishedPlanCondition']['properties']
        self.assertEqual(
            set(properties),
            {'condition_hash', 'technology', 'band', 'raw_test_type'},
        )


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
