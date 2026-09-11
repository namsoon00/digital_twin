"""Explicit capabilities for native_execution/fanout; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.model_registry.contracts import GraphInferenceRule
from typing import Dict, Iterable, List


class NativeExecutionFanoutStore(Protocol):
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

    def merge_subject_fanout_matches(
        self, results: Iterable[Dict[str, object]]
    ) -> List[Dict[str, object]]: ...

    def native_rule_parallelism(self) -> int: ...

    def native_rule_subject_parallelism(self) -> int: ...

    def native_rule_total_read_parallelism(self) -> int: ...

    def query_metrics_snapshot(self) -> Dict[str, object]: ...
