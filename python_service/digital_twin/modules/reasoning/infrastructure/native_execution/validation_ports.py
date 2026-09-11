"""Explicit capabilities for native_execution/validation; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_rulebox_contracts import GraphInferenceRule
from typing import Dict, Iterable, List


class NativeExecutionValidationStore(Protocol):
    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    address: Any

    def has_box_rows(self, box: str, world_id: str = "") -> bool: ...

    def inferencebox_snapshot_from_typedb(
        self,
        clean_symbols: List[str],
        safe_limit: int,
        world_id: str = "",
        inference_generation_id: str = "",
        source_abox_snapshot_id: str = "",
    ) -> Dict[str, object]: ...

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

    def query_metrics_snapshot(self) -> Dict[str, object]: ...

    def reset_query_metrics(self) -> None: ...


@dataclass(frozen=True)
class NativeExecutionValidationRuntime:
    NullTypeDBOntologyGraphRepository: Callable[..., Any]
    materialization_preview_diff_payload: Callable[..., Any]
    typedb_error_code: Callable[..., Any]
