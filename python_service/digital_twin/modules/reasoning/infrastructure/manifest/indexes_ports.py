"""Explicit capabilities for manifest/indexes; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from typing import Dict, List, Tuple


class ManifestIndexesStore(Protocol):
    def graph_persistence_rows(
        self, graph: PortfolioOntology
    ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]: ...
