"""Phase 6 progress rollup — per (area, bucket) time-weighted aggregate (P6-C).

One ``ProgressBucketRollup`` is the read-side aggregate the dashboard renders:
time-weighted completion for a progress bucket within a workbench area, plus the
coverage-quality counts that keep the percent honest.

Honest-percent rules:
  - ``percent`` is ``None`` when ``planned_minutes`` is 0 (no priced time in the
    bucket) — the UI shows "시간 미설정" / Stand-by, never a fake 0% / 100%
    (mirrors the operator workbook's ``IF(E=0, "")``).
  - unpriced / unbucketable conditions are surfaced as **counts**, not minutes:
    an unpriced condition has no time estimate at all (its catalog entry is
    missing), so there is no minute value to fold in — counting them exposes the
    coverage gap instead of silently distorting the denominator (spike §6
    ``unpriced_minutes`` refined to a count — unpriced rows carry NULL minutes).

Pure domain — stdlib only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProgressBucketRollup:
    progress_area: str
    progress_bucket_id: Optional[str]
    planned_minutes: float
    completed_minutes: float
    total_conditions: int
    priced_conditions: int
    unpriced_conditions: int
    unbucketable_conditions: int

    @property
    def percent(self) -> Optional[float]:
        """Time-weighted completion %, or ``None`` when there is no priced time."""
        if self.planned_minutes <= 0:
            return None
        return 100.0 * self.completed_minutes / self.planned_minutes

    @property
    def has_unpriced(self) -> bool:
        return self.unpriced_conditions > 0

    def to_dict(self) -> dict:
        return {
            'progress_area': self.progress_area,
            'progress_bucket_id': self.progress_bucket_id,
            'planned_minutes': self.planned_minutes,
            'completed_minutes': self.completed_minutes,
            'percent': self.percent,
            'total_conditions': self.total_conditions,
            'priced_conditions': self.priced_conditions,
            'unpriced_conditions': self.unpriced_conditions,
            'unbucketable_conditions': self.unbucketable_conditions,
        }
