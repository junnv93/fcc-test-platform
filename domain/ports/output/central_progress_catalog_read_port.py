"""Narrow output port for reading the central standard-time catalog (P6L-3).

``load_catalog`` returns the provider's ``StandardTimeCatalog`` (the
``MeasurementType -> planned_minutes`` SSOT + version + source) so the expectation
sync service can price published-plan conditions at ingest time (D-P6-SNAP). The
existing progress read port only reads the rollup; pricing ingest needs the
catalog itself, which had no read path — this is that narrow read.

``None`` when the provider has no catalog rows yet (un-seeded): the caller
surfaces it (every condition would be unpriced) rather than fabricating minutes.

Pure domain: returns a ``StandardTimeCatalog`` domain value object; no DB types
leak.
"""
from __future__ import annotations

from typing import Optional, Protocol

from domain.models.progress_time_catalog import StandardTimeCatalog


__all__ = ['CentralProgressCatalogReadError', 'CentralProgressCatalogReadPort']


class CentralProgressCatalogReadError(Exception):
    """Raised on a connection/query failure reading the catalog (loud-fail)."""


class CentralProgressCatalogReadPort(Protocol):
    def load_catalog(self, provider_id: str) -> Optional[StandardTimeCatalog]:
        ...
