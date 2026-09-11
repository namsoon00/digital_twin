"""Capabilities for node rows; no runtime construction."""

from __future__ import annotations
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class NodeRowsPort(Protocol):
    def belief_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]: ...

    def evidence_node_rows(
        self, graph: PortfolioOntology
    ) -> List[Dict[str, object]]: ...

    def external_relation_endpoint_ids(self, graph: PortfolioOntology) -> set: ...

    def opinion_node_rows(
        self, graph: PortfolioOntology
    ) -> List[Dict[str, object]]: ...

    def reasoning_card_node_rows(
        self, graph: PortfolioOntology
    ) -> List[Dict[str, object]]: ...

    rows_for_beliefs: Any

    rows_for_entities: Any

    rows_for_evidence: Any

    rows_for_opinions: Any

    rows_for_reasoning_cards: Any


@dataclass(frozen=True)
class BeliefNodeRowsBindings:
    rule_id_from_value: Callable[..., Any]


@dataclass(frozen=True)
class SupportRelationRowsBindings:
    rule_id_from_value: Callable[..., Any]
