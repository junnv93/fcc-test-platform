"""Binary export port owned by the application, implemented by infrastructure."""
from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence


class SampleInventoryExportPort(Protocol):
    def render(self, template: str, records: Sequence[Mapping[str, Any]], *,
               project: Mapping[str, Any]) -> bytes: ...

