"""Portfolio runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.modules.portfolio.public import (
        DecisionActionPlanningService,
        InvestmentDomainService,
        TradeExecutionService,
    )


def build_investment_domain_service(settings=None) -> InvestmentDomainService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.events import ontology_reasoning_event_bus
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.portfolio.public import InvestmentDomainService

    configured_settings = settings or runtime_settings()
    return InvestmentDomainService(
        repository=stores.investment_domain_store(configured_settings),
        event_publisher=ontology_reasoning_event_bus(configured_settings),
    )


def build_decision_action_planning_service(settings=None) -> DecisionActionPlanningService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.portfolio.public import DecisionActionPlanningService

    configured_settings = settings or runtime_settings()
    return DecisionActionPlanningService(
        repository=stores.investment_domain_store(configured_settings),
        monitor_store=stores.monitor_store(configured_settings),
        settings=configured_settings,
    )


def build_trade_execution_service(settings=None) -> TradeExecutionService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.events import ontology_reasoning_event_bus
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.portfolio.public import InvestmentDomainService, TradeExecutionService

    configured_settings = settings or runtime_settings()
    return TradeExecutionService(
        stores.investment_domain_store(configured_settings),
        monitor_store=stores.monitor_store(configured_settings),
        settings=configured_settings,
        investment_domain_service=InvestmentDomainService(
            stores.investment_domain_store(configured_settings),
            ontology_reasoning_event_bus(configured_settings),
        ),
    )
