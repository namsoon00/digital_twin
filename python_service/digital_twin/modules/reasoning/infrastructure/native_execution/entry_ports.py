"""Explicit capabilities for native_execution/entry; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.domain.ontology_rulebox_contracts import GraphInferenceRule
from typing import Dict, Iterable, List


class NativeExecutionEntryStore(Protocol):
    def close_native_rule_read_driver(self, driver) -> None: ...

    def condition_detail_queries_enabled(self) -> bool: ...

    database: Any

    def ensure_database(self, driver) -> None: ...

    def native_rule_indexed_any_condition_query_timeout_seconds(self) -> float: ...

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

    def verify_typedb_native_any_conditions(
        self,
        driver,
        transaction_type,
        rule: GraphInferenceRule,
        source_id: str,
        timeout_seconds: float,
        scoped_manifest_only: bool,
        tx=None,
        world_id: str = "",
        evidence_read_index: Dict[str, object] = None,
    ) -> Dict[str, object]: ...


@dataclass(frozen=True)
class NativeExecutionEntryRuntime:
    typedb_error_code: Callable[..., Any]
