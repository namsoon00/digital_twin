"""Capabilities used only by static-seed preflight."""

from typing import Dict, List, Protocol
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology


class PreflightStore(Protocol):
    def legacy_static_seed_preflight(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
        expected: Dict[str, object],
    ) -> Dict[str, object]: ...

    def read_seed_static_manifest(self) -> Dict[str, object]: ...

    def seed_static_box_names(self) -> List[str]: ...

    def seed_static_manifest_metadata(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
        tbox_metadata: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def seed_static_node_properties(
        self, graph: PortfolioOntology, entity_id_value: str
    ) -> Dict[str, object]: ...

    def seed_static_sentinels_present(
        self, graph: PortfolioOntology, generation_ids=None
    ) -> Dict[str, object]: ...

    def static_seed_generation_ids(
        self, metadata: Dict[str, object] = None
    ) -> Dict[str, str]: ...
