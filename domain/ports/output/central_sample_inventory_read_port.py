"""Read port for the web sample inventory."""
from __future__ import annotations

from typing import Optional, Protocol, Tuple


class CentralSampleInventoryReadError(RuntimeError):
    """Central inventory read failed."""


class CentralSampleInventoryReadPort(Protocol):
    def list_samples(self, *, project_id: Optional[str] = None, team: Optional[str] = None,
                     status: Optional[str] = None, as_of: Optional[str] = None,
                     after: Optional[Tuple] = None, limit: int = 100,
                     include_deleted: bool = False) -> dict: ...

    def get_sample(self, project_id: str, sample_id: str, *, as_of: Optional[str] = None) -> Optional[dict]: ...

    def list_history(self, project_id: str, sample_id: str, *, after: Optional[Tuple] = None,
                     limit: int = 100) -> dict: ...

    def list_intakes(self, project_id: str, sample_ids: list[str], *,
                     as_of: Optional[str] = None) -> list[dict]: ...

    def get_project(self, project_id: str) -> Optional[dict]: ...

    def get_published_plan_project_id(self, plan_id: str) -> Optional[str]:
        """Return the central project identity for a published plan."""
        ...

    def get_measurement_snapshot_inputs(
        self, project_id: str, sample_id: str, *,
        published_plan_id: Optional[str] = None,
    ) -> dict:
        """Read the complete pre-hardware snapshot source in one DB transaction.

        The returned mapping contains ``sample``, ``project``,
        ``sample_revision``, and the optional published-plan project identity.
        Implementations must not compose these values from separate database
        connections or snapshots.
        """
        ...
