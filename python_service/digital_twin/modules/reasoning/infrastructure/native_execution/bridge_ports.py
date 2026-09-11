"""Explicit capabilities for native_execution/bridge; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol, Dict, Iterable, List


class NativeExecutionBridgeStore(Protocol):
    def close_native_rule_read_driver(self, driver) -> None: ...

    database: Any

    def ensure_database(self, driver) -> None: ...

    def native_rule_query_timeout_seconds(self) -> float: ...

    def open_native_rule_read_driver(self, imported, request_timeout_seconds: float = None): ...

    def read_rows_in_transaction(
        self,
        tx,
        query: str,
        columns: Iterable[str],
        label: str = "typedb.read",
        timeout_seconds: float = None,
    ) -> List[Dict[str, object]]: ...

    def read_transaction_options(self, timeout_seconds: float = None): ...


@dataclass(frozen=True)
class NativeExecutionBridgeRuntime:
    typedb_error_code: Callable[..., Any]
