"""Narrow output port for the local expectation-sync watermark (P6L-3).

The expectation sync derives the central progress denominator from local published
plans. This port is the application's read/watermark boundary over the local
``test_plan_publications`` (read-only) + ``expectation_sync_state`` (watermark)
tables — the application never issues SQL, it asks for pending plans and records
sync outcomes through this Protocol.

- ``list_pending`` returns, per project, the LATEST published plan (by
  ``published_at``) that is not yet synced — older plans are skipped because the
  read side counts only the latest plan anyway (read-side latest-wins).
- ``mark_synced`` is called ONLY after the central commit succeeds.
- ``mark_failed`` records a failure reason for retry + read-side surfacing.

Pure domain: trades ``PendingExpectationPlan`` value objects; no DB types leak.
"""
from __future__ import annotations

from typing import Protocol, Sequence

from domain.models.expectation_sync import PendingExpectationPlan


__all__ = ['ExpectationSyncStateError', 'ExpectationSyncStatePort']


class ExpectationSyncStateError(Exception):
    """Raised on a connection/query failure in the sync-state path (loud-fail)."""


class ExpectationSyncStatePort(Protocol):
    def list_pending(self) -> Sequence[PendingExpectationPlan]:
        ...

    def mark_synced(
        self, *, plan_id: str, project_id: str, synced_at: str, catalog_version: int
    ) -> None:
        ...

    def mark_failed(
        self, *, plan_id: str, project_id: str, synced_at: str, error: str
    ) -> None:
        ...
