"""Narrow output port for central Phase 6 progress reads (P6-C).

``get_project_progress`` returns the per-(area, bucket) time-weighted rollup for
a project — the denominator (``published_plan_expectation``) LEFT JOINed to
measurement coverage on the F1 provider-scoped key (project, provider,
condition_hash) and grouped. The platform read API consumes this read-only; it
never derives buckets/areas (those are stored provider-computed values — the view
only GROUP BYs).

Pure domain: returns ``ProgressBucketRollup`` domain DTOs; no DB types leak.
"""
from __future__ import annotations

from typing import Protocol, Sequence

from fcc_test_platform.domain.models.progress_rollup import ProgressBucketRollup


__all__ = ['CentralProgressReadError', 'CentralProgressReadPort']


class CentralProgressReadError(Exception):
    """Raised on a connection/query failure in the progress read path (loud-fail)."""


class CentralProgressReadPort(Protocol):
    def get_project_progress(
        self, project_id: str
    ) -> Sequence[ProgressBucketRollup]:
        ...
