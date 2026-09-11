"""Explicit capabilities for graph_maintenance/current_state; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol, Dict


class GraphMaintenanceCurrentStateStore(Protocol):
    def abox_write_transaction_query_count(self, settings: Dict[str, object] = None) -> int: ...

    def current_state_inventory_batch_size(self, settings: Dict[str, object] = None) -> int: ...

    database: Any

    def with_typedb_retries(self, operation, retry_if=None): ...

    def write_operation_timeout_seconds(self) -> float: ...

    def write_transaction_options(self): ...


@dataclass(frozen=True)
class GraphMaintenanceCurrentStateRuntime:
    runtime_settings: Callable[..., Any]
    typedb_operation_timeout: Callable[..., Any]
