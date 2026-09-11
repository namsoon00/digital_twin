"""Capabilities used only by static-seed repair."""

from dataclasses import dataclass
from typing import Callable, ContextManager, Dict, Iterable, List, Protocol, Tuple
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology


class RepairStore(Protocol):
    def abox_relation_batch_size(self, settings: Dict[str, object] = None) -> int: ...

    address: str

    def batched_relation_insert_queries(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        batch_size: int = 25,
        max_query_bytes: int = 0,
    ) -> List[str]: ...

    def close_driver(self, driver) -> None: ...

    database: str

    def driver_imports(self) -> Tuple[object, object]: ...

    def driver_missing_result(
        self, error: Exception, graph: PortfolioOntology
    ) -> Dict[str, object]: ...

    def ensure_database(self, driver) -> None: ...

    def ensure_schema(self, driver, imported) -> None: ...

    def graph_write_transaction_query_count(
        self, settings: Dict[str, object] = None
    ) -> int: ...

    def missing_seed_relation_rows(
        self, graph: PortfolioOntology
    ) -> List[Dict[str, object]]: ...

    def open_driver(self, imported, request_timeout_seconds: float = None): ...

    def with_typedb_retries(self, operation, retry_if=None): ...

    def write_operation_timeout_seconds(self) -> float: ...

    def write_query_max_bytes(self, settings: Dict[str, object] = None) -> int: ...

    def write_transaction_options(self): ...


@dataclass(frozen=True)
class RepairBindings:
    runtime_settings: Callable[[], Dict[str, object]]
    typedb_operation_timeout: Callable[[float, str], ContextManager[None]]
    utc_now: Callable[[], str]
