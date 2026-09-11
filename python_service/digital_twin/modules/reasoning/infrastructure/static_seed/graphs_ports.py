"""Capabilities used only by static-seed graphs."""

from typing import Dict, Iterable, List, Protocol, Tuple
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology


class GraphsStore(Protocol):
    def graph_persistence_rows(
        self, graph: PortfolioOntology
    ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]: ...

    def graph_with_static_seed_generation(
        self, graph: PortfolioOntology, boxes: Iterable[str], generation_id
    ) -> PortfolioOntology: ...

    def seed_static_box_names(self) -> List[str]: ...

    def seed_static_manifest_entity_id(self) -> str: ...

    def seed_static_manifest_metadata(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
        tbox_metadata: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def static_seed_generation_ids(
        self, metadata: Dict[str, object] = None
    ) -> Dict[str, str]: ...
