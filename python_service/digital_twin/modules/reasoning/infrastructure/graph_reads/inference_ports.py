"""Explicit capabilities for graph_reads/inference; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.domain.ontology_contracts import PortfolioOntology
from typing import Dict, Iterable, List


class GraphReadsInferenceStore(Protocol):
    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    address: Any

    def hypothesis_calibration_snapshot(
        self,
        symbols: Iterable[str] = None,
        limit: int = 40,
        world_id: str = "",
        source_abox_snapshot_id: str = "",
        generation_aligned: bool = False,
    ) -> Dict[str, object]: ...

    def inferencebox_snapshot_from_typedb(
        self,
        clean_symbols: List[str],
        safe_limit: int,
        world_id: str = "",
        inference_generation_id: str = "",
        source_abox_snapshot_id: str = "",
    ) -> Dict[str, object]: ...

    def node_rows(
        self, graph: PortfolioOntology, include_external_relation_endpoints: bool = False
    ) -> List[Dict[str, object]]: ...

    def query_metrics_snapshot(self) -> Dict[str, object]: ...

    def read_entity_rows(
        self, boxes: Iterable[str] = None, limit: int = 0, world_id: str = "", snapshot_id: str = ""
    ) -> List[Dict[str, object]]: ...

    def read_inference_generation_records(
        self, published_only: bool = True, world_id: str = ""
    ) -> List[Dict[str, object]]: ...

    def read_inferencebox_entity_rows(
        self,
        generation_id: str = "",
        symbols: Iterable[str] = None,
        limit: int = 0,
        world_id: str = "",
    ) -> List[Dict[str, object]]: ...

    def read_inferencebox_relation_rows(
        self,
        generation_id: str = "",
        symbols: Iterable[str] = None,
        limit: int = 0,
        world_id: str = "",
    ) -> List[Dict[str, object]]: ...

    def read_relation_rows(
        self, boxes: Iterable[str] = None, limit: int = 0, world_id: str = "", snapshot_id: str = ""
    ) -> List[Dict[str, object]]: ...

    def reset_query_metrics(self) -> None: ...

    def rows_for_entities(self, graph: PortfolioOntology) -> List[Dict[str, object]]: ...

    def rows_for_relations(self, graph: PortfolioOntology) -> List[Dict[str, object]]: ...

    def support_relation_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]: ...


@dataclass(frozen=True)
class GraphReadsInferenceRuntime:
    NullTypeDBOntologyGraphRepository: Callable[..., Any]
    apply_inference_target_coverage: Callable[..., Any]
    inference_generation_records: Callable[..., Any]
    inference_rulebox_metadata: Callable[..., Any]
    matched_condition_ids: Callable[..., Any]
    row_inference_generation_id: Callable[..., Any]
    select_inference_generation_record: Callable[..., Any]
    typedb_error_code: Callable[..., Any]
