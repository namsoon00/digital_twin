"""News Intelligence runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.modules.news_intelligence.public import (
        HypothesisResearchPlanningService,
        InvestmentResearchOrchestrationService,
        InvestmentResearchQueueRunner,
        NewsAnalysisEnrichmentRunner,
        NewsCollectionRunner,
        NewsPipelineRepairService,
    )


def build_investment_research_orchestrator(settings=None, research_store=None) -> InvestmentResearchOrchestrationService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.event_bus import default_event_bus
    from digital_twin.infrastructure.investment_research_gateway import (
        CompositeInvestmentResearchGateway,
        ExistingApiResearchGateway,
    )
    from digital_twin.infrastructure.news_ai_analyzer import news_ai_analyzer_from_settings
    from digital_twin.infrastructure.news_sources import NewsSourceGateway
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.news_intelligence.public import (
        InvestmentResearchOrchestrationService,
        NewsAiAnalysisService,
    )

    configured_settings = settings or runtime_settings()
    evidence_store = stores.research_evidence_store(configured_settings)
    return InvestmentResearchOrchestrationService(
        evidence_repository=evidence_store,
        research_gateway=CompositeInvestmentResearchGateway([
            ExistingApiResearchGateway(configured_settings),
            NewsSourceGateway(configured_settings),
        ]),
        research_store=research_store or stores.investment_research_store(configured_settings),
        event_publisher=default_event_bus(),
        article_analysis_service=NewsAiAnalysisService(
            news_ai_analyzer_from_settings(configured_settings),
            configured_settings,
        ),
        hypothesis_research_planner=build_hypothesis_research_planning_service(configured_settings),
        settings=configured_settings,
    )


def build_investment_research_queue_runner(settings=None) -> InvestmentResearchQueueRunner:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.model_registry import build_hypothesis_proposal_service
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.model_registry.public import HypothesisProposalQueueRunner
    from digital_twin.modules.news_intelligence.public import InvestmentResearchQueueRunner

    configured_settings = settings or runtime_settings()
    research_store = stores.investment_research_store(configured_settings)
    return InvestmentResearchQueueRunner(
        store=research_store,
        orchestrator=build_investment_research_orchestrator(configured_settings, research_store),
        hypothesis_proposal_runner=HypothesisProposalQueueRunner(
            research_store,
            build_hypothesis_proposal_service(configured_settings, research_store),
        ),
    )


def build_hypothesis_research_planning_service(settings=None) -> HypothesisResearchPlanningService:
    from digital_twin.infrastructure.hypothesis_research_planner_ai import hypothesis_research_planning_advisor_from_settings
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.news_intelligence.public import HypothesisResearchPlanningService

    configured_settings = settings or runtime_settings()
    return HypothesisResearchPlanningService(
        advisor=hypothesis_research_planning_advisor_from_settings(configured_settings),
        settings=configured_settings,
    )


def build_news_collection_runner(settings=None, event_publisher=None) -> NewsCollectionRunner:
    from digital_twin.platform.application.data_pipeline_health_service import DataPipelineHealthService
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.events import news_event_bus
    from digital_twin.infrastructure.news_ai_analyzer import news_ai_analyzer_from_settings
    from digital_twin.infrastructure.news_sources import NewsSourceGateway
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.news_intelligence.public import NewsAiAnalysisService, NewsCollectionRunner

    configured_settings = settings or runtime_settings()
    return NewsCollectionRunner(
        account_repository=stores.account_reader(configured_settings),
        monitor_store=stores.monitor_store(configured_settings),
        symbol_store=stores.symbol_universe_store(configured_settings),
        evidence_store=stores.research_evidence_store(configured_settings),
        gateway=NewsSourceGateway(configured_settings),
        settings=configured_settings,
        event_publisher=event_publisher or news_event_bus(configured_settings),
        article_analysis_service=NewsAiAnalysisService(
            news_ai_analyzer_from_settings(configured_settings),
            configured_settings,
        ),
        health_service=DataPipelineHealthService(
            stores.data_pipeline_health_store(configured_settings),
            configured_settings,
        ),
    )


def build_news_analysis_enrichment_runner(settings=None, event_publisher=None) -> NewsAnalysisEnrichmentRunner:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.events import news_event_bus
    from digital_twin.infrastructure.news_ai_analyzer import news_ai_analyzer_from_settings
    from digital_twin.infrastructure.operational_storage_guard import operational_storage_inventory
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.news_intelligence.public import (
        NewsAiAnalysisService,
        NewsAnalysisEnrichmentRunner,
    )

    configured_settings = settings or runtime_settings()
    return NewsAnalysisEnrichmentRunner(
        evidence_store=stores.research_evidence_store(configured_settings),
        analysis_service=NewsAiAnalysisService(
            news_ai_analyzer_from_settings(configured_settings),
            configured_settings,
        ),
        settings=configured_settings,
        event_publisher=event_publisher or news_event_bus(configured_settings),
        storage_guard=lambda: operational_storage_inventory(configured_settings),
    )


def build_news_pipeline_repair_service(settings=None) -> NewsPipelineRepairService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.news_intelligence.public import NewsPipelineRepairService

    configured_settings = settings or runtime_settings()
    return NewsPipelineRepairService(
        evidence_store=stores.research_evidence_store(configured_settings),
        notification_store=stores.notification_job_store(configured_settings),
    )
