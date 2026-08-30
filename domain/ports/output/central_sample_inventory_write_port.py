"""Atomic write port for current samples, intake history, revisions, and tombstones."""
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

    def hard_delete(self, sample_id: str, *, actor_subject: str, occurred_at: str) -> dict: ...

