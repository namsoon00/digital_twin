"""Minimum capability of an owner participating in an existing transaction."""

from typing import Any, Mapping, Optional, Protocol, Sequence


class TransactionCursor(Protocol):
    rowcount: int

    def fetchone(self) -> Optional[Mapping[str, Any]]: ...

    def fetchall(self) -> Sequence[Mapping[str, Any]]: ...


class BoundWriteConnection(Protocol):
    """Execute only. The coordinator owns connection lifetime and commit."""

    def execute(
        self, statement: str, params: Sequence[Any] = ()
    ) -> TransactionCursor: ...
