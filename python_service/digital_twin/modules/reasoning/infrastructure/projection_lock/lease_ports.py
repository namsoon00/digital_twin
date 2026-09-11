"""Explicit capabilities for projection_lock/lease; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.domain.ontology_contracts import PortfolioOntology
from typing import Dict, Iterable, List, Tuple


class ProjectionLockLeaseStore(Protocol):
    def close_driver(self, driver) -> None: ...

    database: Any

    def delete_scoped_abox_write_lease(
        self, driver, imported, lease: Dict[str, object]
    ) -> Dict[str, object]: ...

    def driver_imports(self) -> Tuple[object, object]: ...

    def ensure_database(self, driver) -> None: ...

    def ensure_schema(self, driver, imported) -> None: ...

    def node_rows(
        self, graph: PortfolioOntology, include_external_relation_endpoints: bool = False
    ) -> List[Dict[str, object]]: ...

    def open_driver(self, imported, request_timeout_seconds: float = None): ...

    def read_rows(
        self,
        query: str,
        columns: Iterable[str],
        label: str = "typedb.read",
        timeout_seconds: float = None,
    ) -> List[Dict[str, object]]: ...

    def recover_dead_local_scoped_abox_write_lease(
        self, world_id: str = "", recover_untracked_current_process: bool = False
    ) -> Dict[str, object]: ...

    def scoped_abox_write_lease_graph(
        self, owner: str, manifest_id: str = "", lease_seconds: int = 0, world_id: str = ""
    ) -> Tuple[PortfolioOntology, Dict[str, object]]: ...

    def scoped_abox_write_lease_rows(self, world_id: str = "") -> List[Dict[str, object]]: ...

    def scoped_abox_write_lease_seconds(self, settings: Dict[str, object] = None) -> int: ...

    def scoped_abox_write_lease_status(self, world_id: str = "") -> Dict[str, object]: ...

    def scoped_abox_write_lease_storage_id(self, world_id: str = "") -> str: ...

    def with_typedb_retries(self, operation, retry_if=None): ...

    def write_graph(
        self, driver, imported, graph: PortfolioOntology, delete_boxes: Iterable[str] = None
    ) -> None: ...

    def write_operation_timeout_seconds(self) -> float: ...

    def write_transaction_options(self): ...


@dataclass(frozen=True)
class ProjectionLockLeaseRuntime:
    typedb_operation_timeout: Callable[..., Any]
