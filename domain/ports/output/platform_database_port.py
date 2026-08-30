"""DB-API compatible output ports for platform adapters."""
from __future__ import annotations

from typing import Protocol


class DbCursor(Protocol):
    rowcount: int

    def execute(self, statement: str, parameters: tuple) -> None:
        ...

    def close(self) -> None:
        ...


class DbConnection(Protocol):
    def cursor(self) -> DbCursor:
        ...

    def commit(self) -> None:
        ...

    def rollback(self) -> None:
        ...
