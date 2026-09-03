"""Narrow output port for central Phase 6 progress writes (P6-B.3).

Two write paths against the central progress tables:

* ``upsert_catalog`` — write/refresh a provider's ``standard_time_catalog``
  (test_type_canonical → planned_minutes), keyed by (provider, test type). The
  catalog is the DEFAULT-minutes SSOT; editing it never touches already-ingested
  expectations (those carry a snapshot — D-P6-SNAP).
* ``write_expectations`` — upsert ``published_plan_expectation`` denominator rows
  by their natural key (project, provider, plan, condition_hash). Re-ingesting
  the same plan/provider pair is idempotent (the snapshot/derived fields refresh;
  identity is stable).

Pure domain: references only domain DTOs (``StandardTimeCatalog``,
``ProgressExpectationAtom``); no DB types leak across the port.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from fcc_test_platform.domain.models.progress_expectation import ProgressExpectationAtom
from fcc_test_platform.domain.models.progress_time_catalog import StandardTimeCatalog


__all__ = [
    'CentralProgressWriteError',
    'ExpectationWriteSummary',
    'CentralProgressWritePort',
]


class CentralProgressWriteError(Exception):
    """Raised on a connection/query failure in the progress write path (loud-fail)."""


@dataclass(frozen=True)
class ExpectationWriteSummary:
    inserted: int = 0
    updated: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.updated


class CentralProgressWritePort(Protocol):
    def upsert_catalog(
        self,
        *,
        provider_id: str,
        catalog: StandardTimeCatalog,
        now: str,
    ) -> int:
        """Upsert the provider's catalog rows; returns the number written."""
        ...

    def write_expectations(
        self,
        atoms: Sequence[ProgressExpectationAtom],
        *,
        now: str,
    ) -> ExpectationWriteSummary:
        """Upsert expectation rows by natural key; returns insert/update counts."""
        ...
