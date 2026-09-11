"""Explicit capabilities for native_execution/profile; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_rulebox_contracts import GraphInferenceRule
from typing import Dict, Iterable, List


class NativeExecutionProfileStore(Protocol):
    def abox_generation_identity(self, metadata: Dict[str, object]) -> Dict[str, object]: ...

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    address: Any

    def compact_native_rule_profile_rows(
        self, rows: Iterable[Dict[str, object]]
    ) -> List[Dict[str, object]]: ...

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

    def query_metrics_snapshot(self) -> Dict[str, object]: ...

    def reset_query_metrics(self) -> None: ...

    def rulebox_snapshot(self) -> Dict[str, object]: ...


@dataclass(frozen=True)
class NativeExecutionProfileRuntime:
    materialize_typedb_native_matches: Callable[..., Any]
    typedb_inferencebox_graph: Callable[..., Any]
