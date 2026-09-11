"""Explicit capabilities for graph_reads/execution; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from typing import Dict
from typing import Iterable
from typing import List
from typing import Tuple


class GraphReadsExecutionStore(Protocol):
    address: Any

    def close_driver(self, driver) -> None:
        ...

    database: Any

    def driver_imports(self) -> Tuple[object, object]:
        ...

    def ensure_database(self, driver) -> None:
        ...

    def open_driver(self, imported, request_timeout_seconds: float=None):
        ...

    def query_timeout_seconds(self) -> float:
        ...

    def read_rows_in_transaction(self, tx, query: str, columns: Iterable[str], label: str='typedb.read', timeout_seconds: float=None) -> List[Dict[str, object]]:
        ...

    def read_transaction_options(self, timeout_seconds: float=None):
        ...

    def record_query_metric(self, label: str, query: str, row_count: int, duration_ms: float, status: str='ok') -> None:
        ...

    def with_typedb_retries(self, operation, retry_if=None):
        ...


@dataclass(frozen=True)
class GraphReadsExecutionRuntime:
    typedb_operation_timeout: Callable[..., Any]
    typedb_row_value: Callable[..., Any]
