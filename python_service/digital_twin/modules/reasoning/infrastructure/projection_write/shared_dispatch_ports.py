"""Capabilities for shared dispatch; no runtime construction."""

from __future__ import annotations
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class SharedDispatchPort(Protocol):
    def active_graph_store_key(self, result: Dict[str, object] = None) -> str: ...

    def project_knowledge_world(
        self, portfolio_graph: PortfolioOntology, shared_world
    ) -> Dict[str, object]: ...

    def project_market_world(
        self, portfolio_graph: PortfolioOntology, shared_world
    ) -> Dict[str, object]: ...

    def project_shared_world_update(
        self, update: PortfolioOntology, shared_world, projection_kind: str = "market"
    ) -> Dict[str, object]: ...

    def schedule_shared_world_projection(
        self,
        projection_kind: str,
        portfolio_graph: PortfolioOntology,
        shared_world,
        source_world=None,
    ) -> Dict[str, object]: ...

    def shared_market_world_async_projection_enabled(self) -> bool: ...

    def shared_world_projection_input(
        self, projection_kind: str, portfolio_graph: PortfolioOntology, shared_world
    ) -> PortfolioOntology: ...

    world_projection_outbox: Any


@dataclass(frozen=True)
class ScheduleSharedWorldProjectionBindings:
    SHARED_MARKET_WORLD_PROJECTION_COORDINATOR: Any
