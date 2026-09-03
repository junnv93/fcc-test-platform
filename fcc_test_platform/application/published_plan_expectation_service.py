"""Register a published plan centrally (plan-delivery, 2026-09-02).

``published_plan_expectation`` is the progress denominator **and**, less
obviously, the answer to *"does central know this plan id?"* —
``central_sample_inventory_service.build_measurement_snapshot`` reads it and
refuses a measurement start with ``published_plan_id is unknown`` when it comes
back empty. So an empty table does not merely hide a percentage; it makes a
browser-authored plan unmeasurable (measured 2026-09-01: ``400``).

Until now the only path that filled it (``ProgressExpectationSyncService``)
connected **directly to central PostgreSQL**. The box that authors plans is the
headless surface, and the deployment deliberately keeps it off the data tier —
``docker-compose.central.yml`` says so in as many words: *"Headless stays off
data-network and must never call platform-api:8002 or PostgreSQL directly."* This
service is the HTTP-side counterpart: the author pushes its conditions here, and
the pricing/bucketing/SQL stay where PostgreSQL is.

⚠️ **An unseeded catalog does not block registration.**
``ProgressExpectationSyncService.run_once`` fails a whole plan when
``load_catalog`` returns ``None``, and for a pure denominator that was defensible.
It is not defensible here: the *count* of conditions is complete without a
catalog — only the time estimate is missing — and refusing would mean a
deployment that never seeded the operator's minutes workbook cannot start a
measurement at all. Every atom is written with ``PricingStatus.UNPRICED``, the
response reports ``priced``/``unpriced`` separately, and ``catalog_version`` comes
back ``null`` so a caller can tell "no ETA" from "ETA of zero".
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Mapping, Optional, Sequence

from fcc_test_platform.domain.models.progress_time_catalog import CatalogSource, StandardTimeCatalog
from fcc_test_platform.application.progress_ingest_service import (
    ProgressIngestService,
    PublishedConditionRow,
)


__all__ = [
    'UNSEEDED_CATALOG_VERSION',
    'PublishedPlanExpectationError',
    'PublishedPlanExpectationNotFound',
    'PublishedPlanExpectationService',
]


#: Version stamped on atoms priced against an absent catalog. It is not a claim
#: that "catalog 0" exists — every such atom is ``UNPRICED`` and the API answers
#: ``catalog_version: null``. A sentinel is needed because the atom's field is a
#: plain ``int``; widening that model for this one case would ripple through the
#: rollup SQL for no gain.
UNSEEDED_CATALOG_VERSION = 0


class PublishedPlanExpectationError(ValueError):
    """The ingest envelope is not usable (malformed, or carries no conditions)."""


class PublishedPlanExpectationNotFound(LookupError):
    """The project or the provider does not exist centrally."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value) -> str:
    return str(value or '').strip()


class PublishedPlanExpectationService:
    def __init__(
        self,
        *,
        identity_reader,
        ingest_service: ProgressIngestService,
        catalog_reader,
        progress_area: str,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        if not _clean(progress_area):
            raise ValueError('progress_area is required for expectation ingest')
        self._identity = identity_reader
        self._ingest = ingest_service
        self._catalog_reader = catalog_reader
        self._progress_area = _clean(progress_area)
        self._clock = clock or _utc_now_iso

    def ingest(
        self,
        project_id: str,
        provider_id: str,
        *,
        plan_id: str,
        plan_published_at: Optional[str],
        conditions: Sequence[Mapping[str, str]],
    ) -> dict:
        """Register one published plan's conditions. Idempotent by natural key."""
        project = _clean(project_id)
        provider_key = _clean(provider_id)
        plan = _clean(plan_id)
        if not project or not provider_key:
            raise PublishedPlanExpectationError(
                'project_id and provider_id are required'
            )
        if not plan:
            raise PublishedPlanExpectationError('plan_id is required')
        rows = self._parse_conditions(conditions)

        provider_uuid = self._identity.resolve_provider_uuid(provider_key)
        if provider_uuid is None:
            raise PublishedPlanExpectationNotFound(
                f'provider {provider_key!r} is unknown or disabled centrally'
            )
        if not self._identity.project_exists(project):
            raise PublishedPlanExpectationNotFound(
                f'project {project!r} does not exist centrally'
            )

        catalog = self._catalog_reader.load_catalog(provider_uuid)
        seeded = catalog is not None
        if not seeded:
            catalog = StandardTimeCatalog.from_mapping(
                {},
                version=UNSEEDED_CATALOG_VERSION,
                source=CatalogSource.WORKBOOK_SEED,
            )
        report = self._ingest.ingest_published_plan(
            project_id=project,
            plan_id=plan,
            provider_id=provider_uuid,
            progress_area=self._progress_area,
            conditions=rows,
            catalog=catalog,
            plan_published_at=_clean(plan_published_at) or None,
        )
        return {
            'plan_id': plan,
            'conditions': report.condition_count,
            'inserted': report.write_summary.inserted,
            'updated': report.write_summary.updated,
            'priced': report.priced_count,
            'unpriced': report.unpriced_count,
            'unbucketable': report.unbucketable_count,
            'catalog_version': catalog.version if seeded else None,
        }

    @staticmethod
    def _parse_conditions(
        conditions: Sequence[Mapping[str, str]],
    ) -> list[PublishedConditionRow]:
        if not conditions:
            # A zero-condition registration would sit centrally as "this plan has
            # nothing to measure", and every downstream percentage would then read
            # 100% complete. A published plan has rows by construction.
            raise PublishedPlanExpectationError(
                'conditions must not be empty — an empty denominator reads as '
                '100% complete'
            )
        rows: list[PublishedConditionRow] = []
        for index, item in enumerate(conditions):
            if not isinstance(item, Mapping):
                raise PublishedPlanExpectationError(
                    f'conditions[{index}] must be an object'
                )
            condition_hash = _clean(item.get('condition_hash'))
            if not condition_hash:
                raise PublishedPlanExpectationError(
                    f'conditions[{index}].condition_hash is required — it is the '
                    'progress join key and is never recomputed centrally'
                )
            rows.append(PublishedConditionRow(
                condition_hash=condition_hash,
                technology=_clean(item.get('technology')),
                band=_clean(item.get('band')),
                raw_test_type=_clean(item.get('raw_test_type')),
            ))
        return rows
