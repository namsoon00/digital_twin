"""Capabilities for legacy activation; no runtime construction."""

from __future__ import annotations
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class LegacyActivationPort(Protocol):
    def abox_active_pointer_graph(
        self,
        graph: PortfolioOntology,
        previous_snapshot_id: str = "",
        pending_activation: bool = True,
    ) -> PortfolioOntology: ...

    def abox_metadata_from_marker(
        self, marker: Dict[str, object]
    ) -> Dict[str, object]: ...

    def abox_projection_marker_rows(
        self, world_id: str = "", snapshot_id: str = "", limit: int = 0
    ) -> List[Dict[str, object]]: ...

    def abox_snapshot_id_from_graph(self, graph: PortfolioOntology) -> str: ...

    def activate_abox_generation(
        self, snapshot_id: str, world_id: str = ""
    ) -> Dict[str, object]: ...

    def activate_scoped_abox_manifest(
        self,
        manifest_id: str,
        previous_metadata: Dict[str, object] = None,
        pending_activation: bool = False,
        inference_target_symbols: Iterable[str] = None,
        world_id: str = "",
    ) -> Dict[str, object]: ...

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    address: Any

    def box_snapshot_row_counts(
        self, box: str, snapshot_id: str, world_id: str = ""
    ) -> Dict[str, int]: ...

    def close_driver(self, driver) -> None: ...

    database: Any

    def delete_world_abox_control_rows(
        self, driver, imported, world_id: str = ""
    ) -> Dict[str, object]: ...

    def driver_imports(self) -> Tuple[object, object]: ...

    def driver_missing_result(
        self, error: Exception, graph: PortfolioOntology
    ) -> Dict[str, object]: ...

    def ensure_database(self, driver) -> None: ...

    def finalize_scoped_abox_manifest(
        self,
        active_manifest_id: str,
        previous_manifest_id: str = "",
        world_id: str = "",
    ) -> Dict[str, object]: ...

    def graph_for_boxes(
        self,
        graph: PortfolioOntology,
        boxes: Iterable[str],
        retain_cross_box_relations: bool = False,
    ) -> PortfolioOntology: ...

    def open_driver(self, imported, request_timeout_seconds: float = None): ...

    def read_rows(
        self,
        query: str,
        columns: Iterable[str],
        label: str = "typedb.read",
        timeout_seconds: float = None,
    ) -> List[Dict[str, object]]: ...

    def read_rows_in_transaction(
        self,
        tx,
        query: str,
        columns: Iterable[str],
        label: str = "typedb.read",
        timeout_seconds: float = None,
    ) -> List[Dict[str, object]]: ...

    def scoped_manifest_metadata(
        self, manifest_id: str, world_id: str = ""
    ) -> Dict[str, object]: ...

    def with_typedb_retries(self, operation, retry_if=None): ...

    def write_graph(
        self,
        driver,
        imported,
        graph: PortfolioOntology,
        delete_boxes: Iterable[str] = None,
    ) -> None: ...


@dataclass(frozen=True)
class AboxActivePointerGraphBindings:
    utc_now: Callable[..., Any]


@dataclass(frozen=True)
class ActivateAboxGenerationBindings:
    typedb_error_code: Callable[..., Any]
    utc_now: Callable[..., Any]


@dataclass(frozen=True)
class AboxProjectionMarkerGraphBindings:
    utc_now: Callable[..., Any]
