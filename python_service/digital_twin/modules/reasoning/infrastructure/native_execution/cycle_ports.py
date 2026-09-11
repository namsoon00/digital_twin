"""Explicit capabilities for native_execution/cycle; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_rulebox_contracts import GraphInferenceRule
from typing import Dict, Iterable, List


class NativeExecutionCycleStore(Protocol):
    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    address: Any

    def clear_inferencebox(self, world_id: str = "") -> Dict[str, object]: ...

    def has_box_rows(self, box: str, world_id: str = "") -> bool: ...

    def hypothesis_calibration_snapshot_for_native_result(
        self,
        matched_graph: PortfolioOntology,
        symbols: Iterable[str],
        source_abox_snapshot_id: str,
        generation_aligned: bool,
        scoped_active_abox: bool,
        limit: int = 40,
        world_id: str = "",
    ) -> Dict[str, object]: ...

    inference_generation_keep_count: Any

    def inferencebox_snapshot(
        self,
        symbols: List[str] = None,
        limit: int = 80,
        reset_metrics: bool = True,
        world_id: str = "",
        inference_generation_id: str = "",
        source_abox_snapshot_id: str = "",
    ) -> Dict[str, object]: ...

    def inferencebox_snapshot_from_graph(
        self, graph: PortfolioOntology, symbols: List[str] = None, limit: int = 80
    ) -> Dict[str, object]: ...

    def load_graph_for_native_matches(
        self,
        native_match_result: Dict[str, object],
        rules: Iterable[GraphInferenceRule] = None,
        include_all_rule_relation_types: bool = False,
        include_incoming_relations: bool = True,
        evidence_read_index: Dict[str, object] = None,
        world_id: str = "",
    ) -> PortfolioOntology: ...

    def match_typedb_native_rules(
        self,
        rules: Iterable[GraphInferenceRule],
        target_symbols: Iterable[str] = None,
        world_id: str = "",
        planner_topology: Dict[str, object] = None,
        preflight_graph: PortfolioOntology = None,
        preflight_incoming_relations_complete: bool = False,
        native_rule_parallelism: int = 1,
        native_rule_target_parallelism: int = 1,
        adaptive_target_sharding_profile: Dict[str, object] = None,
        stable_abox_write_lease_held: bool = False,
        evidence_read_index: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def native_rule_durable_preflight_fallback_enabled(self) -> bool: ...

    def native_rule_execution_enabled(self) -> bool: ...

    def native_rule_parallelism(self) -> int: ...

    def native_rule_target_parallelism(self) -> int: ...

    def projection_graph_for_native_matches(
        self,
        projection_graph: PortfolioOntology,
        native_match_result: Dict[str, object],
        rules: Iterable[GraphInferenceRule] = None,
        evidence_read_index: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def prune_inferencebox_generations(
        self, active_generation_id: str, keep_count: int = 2, world_id: str = ""
    ) -> Dict[str, object]: ...

    def query_metrics_snapshot(self) -> Dict[str, object]: ...

    def reset_query_metrics(self) -> None: ...

    def rulebox_snapshot(self) -> Dict[str, object]: ...

    def write_inferencebox_graph(self, graph: PortfolioOntology) -> Dict[str, object]: ...


@dataclass(frozen=True)
class NativeExecutionCycleRuntime:
    NullTypeDBOntologyGraphRepository: Callable[..., Any]
    inference_generation_id: Callable[..., Any]
    materialize_typedb_native_matches: Callable[..., Any]
    rulebox_runtime_metadata: Callable[..., Any]
    typedb_abox_inference_generation_id: Callable[..., Any]
    typedb_error_code: Callable[..., Any]
    typedb_inferencebox_graph: Callable[..., Any]
    typedb_native_profile_metadata: Callable[..., Any]
    utc_now: Callable[..., Any]
