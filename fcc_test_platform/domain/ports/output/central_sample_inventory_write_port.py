"""Atomic write port for current samples, intake/custody history, revisions, and tombstones."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Protocol


class CentralSampleInventoryWriteError(RuntimeError):
    """Central inventory write failed."""


class CentralSampleInventoryNotFoundError(LookupError):
    """The requested project or sample does not exist."""


class CentralSampleInventoryWritePort(Protocol):
    def create_sample(self, project_id: str, payload: Mapping[str, Any], *, actor_subject: str,
                      occurred_at: str) -> dict: ...

    def patch_sample(self, project_id: str, sample_id: str, payload: Mapping[str, Any], *,
                     expected_version: int, actor_subject: str, occurred_at: str) -> dict: ...

    def change_status(self, project_id: str, sample_id: str, status: str, *,
                      expected_version: int, actor_subject: str, occurred_at: str) -> dict: ...

    def append_custody_event(self, project_id: str, sample_id: str,
                             payload: Mapping[str, Any], *, actor_subject: str,
                             occurred_at: str) -> dict:
        """Append one 반입/반출 사건 (ADR-0002).

        Implementations must not bump ``samples.row_version`` or write a sample
        revision: the custody axis carries its own actor and timestamps, and the
        revision snapshot shape is shared with measurement sessions.
        """
        ...

    def delete_custody_event(self, project_id: str, sample_id: str, event_id: str, *,
                             actor_subject: str, occurred_at: str) -> dict:
        """Remove one wrongly recorded custody event (ADR-0002 정정 수단)."""
        ...

    def hard_delete(self, sample_id: str, *, actor_subject: str, occurred_at: str) -> dict: ...

