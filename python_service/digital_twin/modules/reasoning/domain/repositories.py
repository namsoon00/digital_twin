"""Repository capabilities owned by reasoning."""

import inspect
from typing import Dict, List, Protocol, Tuple, runtime_checkable
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_projection_audit import OntologyProjectionRun
from digital_twin.domain.portfolio import AccountSnapshot


ONTOLOGY_GRAPH_REPOSITORY_CONTRACT: Dict[str, Tuple[str, ...]] = {
    "active_tbox_metadata": (),
    "save_graph": ("graph",),
    "seed_ontology": ("payload",),
    "rulebox_snapshot": (),
    "save_rulebox": ("payload",),
    "run_rulebox": ("payload",),
    "inferencebox_snapshot": ("symbols", "limit"),
    "save_rule_change_candidates": ("candidates", "context"),
}


@runtime_checkable
class OntologyGraphRepository(Protocol):
    def active_tbox_metadata(self) -> Dict[str, object]:
        ...

    def save_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        ...

    def seed_ontology(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        ...

    def rulebox_snapshot(self) -> Dict[str, object]:
        ...

    def save_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        ...

    def run_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        ...

    def inferencebox_snapshot(
        self,
        symbols: List[str] = None,
        limit: int = 80,
        world_id: str = "",
        inference_generation_id: str = "",
        source_abox_snapshot_id: str = "",
    ) -> Dict[str, object]:
        ...

    def save_rule_change_candidates(self, candidates: List[Dict[str, object]], context: Dict[str, object] = None) -> Dict[str, object]:
        ...


def ontology_graph_repository_contract_errors(repository: object) -> List[str]:
    errors: List[str] = []
    for method_name, expected_parameters in ONTOLOGY_GRAPH_REPOSITORY_CONTRACT.items():
        method = getattr(repository, method_name, None)
        if not callable(method):
            errors.append(method_name + " is missing or not callable")
            continue
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            continue
        actual_parameters = [
            parameter.name
            for parameter in signature.parameters.values()
            if parameter.name != "self"
            and parameter.kind in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
        ]
        if tuple(actual_parameters[:len(expected_parameters)]) != expected_parameters:
            errors.append(
                method_name
                + " signature mismatch: expected "
                + ", ".join(expected_parameters)
                + " got "
                + ", ".join(actual_parameters)
            )
    return errors


def ensure_ontology_graph_repository_contract(repository: object, label: str = "ontology graph repository"):
    errors = ontology_graph_repository_contract_errors(repository)
    if errors:
        raise TypeError(label + " does not satisfy OntologyGraphRepository contract: " + "; ".join(errors))
    return repository


class OntologyProjectionRecorder(Protocol):
    def record_snapshot(self, snapshot: AccountSnapshot) -> Dict[str, object]:
        ...


class OntologyProjectionAuditRepository(Protocol):
    """Durable source/result audit for one material ABox generation."""

    def begin(self, run: OntologyProjectionRun) -> OntologyProjectionRun:
        ...

    def complete(self, run: OntologyProjectionRun) -> OntologyProjectionRun:
        ...

    def latest(self, account_id: str = "", limit: int = 50) -> List[Dict[str, object]]:
        ...
