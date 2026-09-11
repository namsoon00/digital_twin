"""Capabilities used only by static-seed schema."""

from typing import Protocol


class SchemaStore(Protocol):
    def schema_query(self) -> str: ...
