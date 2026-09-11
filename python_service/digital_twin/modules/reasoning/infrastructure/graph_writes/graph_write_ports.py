"""Capabilities for graph write; no runtime construction."""

from __future__ import annotations
from digital_twin.domain.ontology_contracts import PortfolioOntology
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class GraphWritePort(Protocol):
    def abox_node_batch_size(self, settings: Dict[str, object] = None) -> int: ...

    def abox_relation_batch_size(self, settings: Dict[str, object] = None) -> int: ...

    def abox_write_transaction_query_count(
        self, settings: Dict[str, object] = None
    ) -> int: ...

    address: Any

    def batched_node_insert_queries(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        batch_size: int = 40,
        max_query_bytes: int = 0,
    ) -> List[str]: ...

    def batched_relation_insert_queries(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        batch_size: int = 25,
        max_query_bytes: int = 0,
    ) -> List[str]: ...

    def close_driver(self, driver) -> None: ...

    database: Any

    def delete_box_rows_in_batches(
        self, driver, imported, boxes: Iterable[str]
    ) -> Dict[str, object]: ...

    def delete_queries(self, boxes: Iterable[str]) -> List[str]: ...

    def driver_imports(self) -> Tuple[object, object]: ...

    def ensure_database(self, driver) -> None: ...

    def ensure_schema(self, driver, imported) -> None: ...

    def graph_insert_queries(self, graph: PortfolioOntology) -> List[str]: ...

    def graph_persistence_rows(
        self, graph: PortfolioOntology
    ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]: ...

    def graph_write_transaction_query_count(
        self, settings: Dict[str, object] = None
    ) -> int: ...

    def node_insert_query(self, row: Dict[str, object], updated_at: str) -> str: ...

    def node_rows(
        self,
        graph: PortfolioOntology,
        include_external_relation_endpoints: bool = False,
    ) -> List[Dict[str, object]]: ...

    def open_driver(self, imported, request_timeout_seconds: float = None): ...

    def relation_insert_query(self, row: Dict[str, object], updated_at: str) -> str: ...

    rows_for_relations: Any

    def static_graph_insert_queries(self, graph: PortfolioOntology) -> List[str]: ...

    def static_node_insert_batch_size(
        self, settings: Dict[str, object] = None
    ) -> int: ...

    def static_write_transaction_query_count(
        self, settings: Dict[str, object] = None
    ) -> int: ...

    def support_relation_rows(
        self, graph: PortfolioOntology
    ) -> List[Dict[str, object]]: ...

    def with_typedb_retries(self, operation, retry_if=None): ...

    def write_operation_timeout_seconds(self) -> float: ...

    def write_query_max_bytes(self, settings: Dict[str, object] = None) -> int: ...

    def write_transaction_options(self): ...


@dataclass(frozen=True)
class WriteGraphBindings:
    node_boxes: Callable[..., Any]
    typedb_operation_timeout: Callable[..., Any]


@dataclass(frozen=True)
class ClearInferenceboxBindings:
    typedb_error_code: Callable[..., Any]


@dataclass(frozen=True)
class InsertQueriesBindings:
    utc_now: Callable[..., Any]


@dataclass(frozen=True)
class GraphInsertQueriesBindings:
    runtime_settings: Callable[..., Any]
    utc_now: Callable[..., Any]


@dataclass(frozen=True)
class StaticGraphInsertQueriesBindings:
    runtime_settings: Callable[..., Any]
    utc_now: Callable[..., Any]
