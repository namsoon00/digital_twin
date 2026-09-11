"""Explicit capabilities for manifest/read_index; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol, Dict, Iterable, List


class ManifestReadIndexStore(Protocol):
    def native_rule_execution_budget_seconds(self) -> float: ...

    def native_rule_query_timeout_seconds(self) -> float: ...

    def read_rows(
        self,
        query: str,
        columns: Iterable[str],
        label: str = "typedb.read",
        timeout_seconds: float = None,
    ) -> List[Dict[str, object]]: ...

    def write_operation_timeout_seconds(self) -> float: ...


@dataclass(frozen=True)
class ManifestReadIndexRuntime:
    typedb_error_code: Callable[..., Any]
