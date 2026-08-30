"""P6L-3 expectation sync domain value objects (pure).

The expectation sync derives the central progress denominator from local published
plans (read-only) and watermarks which plans are already synced. These are the
pure-domain carriers between the local sync-state store (infrastructure) and the
sync service (application):

- ``PendingExpectationPlan`` — one not-yet-synced published plan the store hands
  the service (plan identity + publish time + its materialized rows). The rows are
  ``TestPlanRow`` (condition_hash already materialized) so the application
  render-glue can derive (technology, band) and carry condition_hash verbatim.
- ``ExpectationSyncStatus`` — the watermark status the store persists.

Pure domain — stdlib + ``domain.models`` only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple

from domain.models.test_plan_authoring import TestPlanRow


class ExpectationSyncStatus(str, Enum):
    """Watermark status of a published plan's expectation sync.

    ``SYNCED`` is written ONLY after the central commit succeeds (the denominator
    rows are durably in ``published_plan_expectation``). ``FAILED`` records a
    resolve/catalog/ingest failure with its reason so the next tick retries and
    the read side can surface "last error" instead of a fake 0%.
    """

    SYNCED = 'synced'
    FAILED = 'failed'


@dataclass(frozen=True)
class PendingExpectationPlan:
    """One published plan awaiting expectation sync (store → service carrier).

    ``published_at`` is the plan's publish time (ISO-8601) or ``None`` (untracked);
    it is carried onto every expectation row for the read-side latest-wins window.
    ``rows`` are the materialized published rows (condition_hash non-None).
    """

    plan_id: str
    project_id: str
    published_at: Optional[str]
    rows: Tuple[TestPlanRow, ...] = field(default=())
