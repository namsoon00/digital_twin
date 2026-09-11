"""Capabilities used only by static-seed bootstrap."""

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Protocol
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology


class BootstrapStore(Protocol):
    _last_rules: List[object]

    def clear_inferencebox(self, world_id: str = "") -> Dict[str, object]: ...

    def clear_rulebox_snapshot_cache(self) -> None: ...

    def recover_scoped_abox_write_lease_after_server_start(
        self,
    ) -> Dict[str, object]: ...

    def repair_seed_relations(self, graph: PortfolioOntology) -> Dict[str, object]: ...

    def rulebox_snapshot(self) -> Dict[str, object]: ...

    def save_seed_static_manifest(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
        schema_prepared: bool = False,
        tbox_metadata: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def save_static_seed_boxes(
        self,
        graph: PortfolioOntology,
        boxes: Iterable[str],
        rules_payload: List[Dict[str, object]] = None,
        schema_prepared: bool = False,
        tbox_metadata: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def seed_graph_preflight(
        self, graph: PortfolioOntology, rules_payload: List[Dict[str, object]]
    ) -> Dict[str, object]: ...

    def seed_relation_repair_eligible(self, preflight: Dict[str, object]) -> bool: ...

    def seed_static_boxes_requiring_refresh(
        self, preflight: Dict[str, object]
    ) -> List[str]: ...

    def static_seed_schema_prepared(self, preflight: Dict[str, object]) -> bool: ...

    def sync_base_schema_contract(self) -> Dict[str, object]: ...


@dataclass(frozen=True)
class BootstrapBindings:
    runtime_settings: Callable[[], Dict[str, object]]
