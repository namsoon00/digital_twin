"""Capabilities used only by static-seed persistence."""

from dataclasses import dataclass
from typing import Callable, ContextManager, Dict, Iterable, List, Protocol, Tuple
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology


class PersistenceStore(Protocol):
    _last_rules: List[object]

    address: str

    def close_driver(self, driver) -> None: ...

    database: str

    def driver_imports(self) -> Tuple[object, object]: ...

    def driver_missing_result(
        self, error: Exception, graph: PortfolioOntology
    ) -> Dict[str, object]: ...

    def ensure_database(self, driver) -> None: ...

    def ensure_schema(self, driver, imported) -> None: ...

    def external_relation_endpoint_ids(self, graph: PortfolioOntology) -> set: ...

    def graph_for_boxes(
        self,
        graph: PortfolioOntology,
        boxes: Iterable[str],
        retain_cross_box_relations: bool = False,
    ) -> PortfolioOntology: ...

    def graph_persistence_rows(
        self, graph: PortfolioOntology
    ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]: ...

    def graph_with_static_seed_generation(
        self, graph: PortfolioOntology, boxes: Iterable[str], generation_id
    ) -> PortfolioOntology: ...

    def open_driver(self, imported, request_timeout_seconds: float = None): ...

    def seed_static_box_names(self) -> List[str]: ...

    def seed_static_manifest_graph(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
        tbox_metadata: Dict[str, object] = None,
    ) -> PortfolioOntology: ...

    def seed_static_manifest_metadata(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
        tbox_metadata: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def seed_static_manifest_storage_id(self) -> str: ...

    def static_seed_generation_ids(
        self, metadata: Dict[str, object] = None
    ) -> Dict[str, str]: ...

    def static_graph_insert_queries(self, graph: PortfolioOntology) -> List[str]: ...

    def with_typedb_retries(self, operation, retry_if=None): ...

    def write_graph(
        self,
        driver,
        imported,
        graph: PortfolioOntology,
        delete_boxes: Iterable[str] = None,
    ) -> None: ...

    def write_operation_timeout_seconds(self) -> float: ...

    def write_transaction_options(self): ...


@dataclass(frozen=True)
class PersistenceBindings:
    typedb_error_code: Callable[[object], str]
    typedb_operation_timeout: Callable[[float, str], ContextManager[None]]
