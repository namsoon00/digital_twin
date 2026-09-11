"""Capabilities for record; no runtime construction."""

from __future__ import annotations
from digital_twin.domain.ontology_contracts import PortfolioOntology
from typing import Any, Dict, Protocol


class ScheduleFollowupsPort(Protocol):

    def async_quality_record_enabled(self) -> bool: ...

    quality_store: Any

    def schedule_knowledge_world_projection(
        self, portfolio_graph: PortfolioOntology, shared_world, source_world=None
    ) -> Dict[str, object]: ...

    def schedule_market_world_projection(
        self, portfolio_graph: PortfolioOntology, shared_world, source_world=None
    ) -> Dict[str, object]: ...

    source: Any
