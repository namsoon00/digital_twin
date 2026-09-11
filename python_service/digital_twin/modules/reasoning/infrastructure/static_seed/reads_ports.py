"""Capabilities used only by static-seed reads."""

from typing import Dict, Iterable, List, Protocol, Tuple
from digital_twin.domain.ontology_contracts import PortfolioOntology


class ReadsStore(Protocol):
    def graph_persistence_rows(
        self, graph: PortfolioOntology
    ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]: ...

    def node_rows(
        self,
        graph: PortfolioOntology,
        include_external_relation_endpoints: bool = False,
    ) -> List[Dict[str, object]]: ...

    def read_rows(
        self,
        query: str,
        columns: Iterable[str],
        label: str = "typedb.read",
        timeout_seconds: float = None,
    ) -> List[Dict[str, object]]: ...

    def seed_static_manifest_storage_id(self) -> str: ...

    def seed_static_sentinels(
        self, graph: PortfolioOntology, generation_ids=None
    ) -> List[Dict[str, str]]: ...
