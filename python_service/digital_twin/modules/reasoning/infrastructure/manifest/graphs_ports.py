"""Explicit capabilities for manifest/graphs; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from typing import Dict, Iterable, List, Tuple


class ManifestGraphsStore(Protocol):
    def graph_persistence_rows(
        self, graph: PortfolioOntology
    ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]: ...

    def scoped_manifest_pointer_graph(
        self,
        graph: PortfolioOntology,
        scope_plan: List[Dict[str, object]],
        previous_metadata: Dict[str, object] = None,
        pending_activation: bool = True,
        inference_target_symbols: Iterable[str] = None,
        scope_ids: Iterable[str] = None,
    ) -> PortfolioOntology: ...


@dataclass(frozen=True)
class ManifestGraphsRuntime:
    utc_now: Callable[..., Any]
