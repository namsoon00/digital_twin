"""Capabilities for graph save; no runtime construction."""

from __future__ import annotations
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class GraphSavePort(Protocol):
    _fresh_candidate_rebuild: Any

    _last_graph: Any

    def abox_active_pointer_graph(
        self,
        graph: PortfolioOntology,
        previous_snapshot_id: str = "",
        pending_activation: bool = True,
    ) -> PortfolioOntology: ...

    def abox_candidate_graph(self, graph: PortfolioOntology) -> PortfolioOntology: ...

    def abox_projection_marker_graph(
        self,
        graph: PortfolioOntology,
        expected_entity_count: int,
        expected_relation_count: int,
        box: str = "ABox",
    ) -> PortfolioOntology: ...

    def abox_snapshot_id_from_graph(self, graph: PortfolioOntology) -> str: ...

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    address: Any

    def close_driver(self, driver) -> None: ...

    database: Any

    def delete_box_snapshot_rows_in_batches(
        self,
        driver,
        imported,
        box: str,
        snapshot_id: str,
        batch_size: int = None,
        max_batches: int = None,
        deadline_monotonic: float = None,
    ) -> Dict[str, object]: ...

    def drain_inactive_abox_generations_incrementally(
        self,
        driver,
        imported,
        active_snapshot_id: str = "",
        excluded_snapshot_ids: Iterable[str] = None,
    ) -> Dict[str, object]: ...

    def driver_imports(self) -> Tuple[object, object]: ...

    def driver_missing_result(
        self, error: Exception, graph: PortfolioOntology
    ) -> Dict[str, object]: ...

    def ensure_database(self, driver) -> None: ...

    def ensure_schema(self, driver, imported) -> None: ...

    def graph_for_boxes(
        self,
        graph: PortfolioOntology,
        boxes: Iterable[str],
        retain_cross_box_relations: bool = False,
    ) -> PortfolioOntology: ...

    def graph_persistence_rows(
        self, graph: PortfolioOntology
    ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]: ...

    is_scoped_abox_graph: Any

    def open_driver(self, imported, request_timeout_seconds: float = None): ...

    def save_scoped_abox_graph(
        self,
        graph: PortfolioOntology,
        boxes: Iterable[str] = None,
        adopted_write_lease: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def verify_abox_projection(
        self,
        graph: PortfolioOntology,
        expected_entity_count: int,
        expected_relation_count: int,
        box: str = "ABox",
    ) -> Dict[str, object]: ...

    def with_typedb_retries(self, operation, retry_if=None): ...

    def write_graph(
        self,
        driver,
        imported,
        graph: PortfolioOntology,
        delete_boxes: Iterable[str] = None,
    ) -> None: ...

    def write_operation_timeout_seconds(self) -> float: ...


@dataclass(frozen=True)
class SaveGraphBindings:
    NullTypeDBOntologyGraphRepository: Callable[..., Any]
    node_boxes: Callable[..., Any]
    typedb_operation_timeout: Callable[..., Any]
    utc_now: Callable[..., Any]
