"""Investment Calendar runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.modules.investment_calendar.public import (
        InvestmentCalendarCandidateService,
        InvestmentCalendarDiscoveryService,
        InvestmentCalendarResearchRecommendationService,
        InvestmentCalendarRunner,
        InvestmentCalendarService,
        OfficialCalendarSyncService,
    )


def build_investment_calendar_service(settings=None, event_publisher=None) -> InvestmentCalendarService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.event_bus import default_event_bus
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.investment_calendar.public import InvestmentCalendarService

    configured_settings = settings or runtime_settings()
    return InvestmentCalendarService(
        repository=stores.investment_calendar_store(configured_settings),
        account_repository=stores.account_reader(configured_settings),
        notification_queue=stores.notification_job_store(configured_settings),
        settings=configured_settings,
        event_publisher=event_publisher or default_event_bus(),
        symbol_repository=stores.symbol_universe_store(configured_settings),
        reasoning_source_fact_store=stores.reasoning_source_fact_store(configured_settings),
        release_reader=stores.external_data_store(configured_settings).calendar_release_facts,
    )


def build_official_calendar_sync_service(settings=None, event_publisher=None) -> OfficialCalendarSyncService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.instruments import ontology_reasoning_priority_symbols
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.investment_calendar.infrastructure.bok_calendar_source import BokPolicyDecisionCalendarSource
    from digital_twin.modules.investment_calendar.infrastructure.opendart_calendar_source import OpenDartEarningsCalendarSource
    from digital_twin.modules.investment_calendar.infrastructure.samsung_ir_calendar_source import SamsungIrEarningsCalendarSource
    from digital_twin.modules.investment_calendar.infrastructure.us_macro_calendar_source import (
        BeaMacroReleaseCalendarSource,
        BlsMacroReleaseCalendarSource,
        FederalReserveFomcCalendarSource,
    )
    from digital_twin.modules.investment_calendar.public import (
        InvestmentCalendarCandidateService,
        OfficialCalendarSyncService,
    )

    configured_settings = settings or runtime_settings()
    calendar_service = build_investment_calendar_service(configured_settings, event_publisher)
    account_repository = stores.account_reader(configured_settings)
    priority_symbols = ontology_reasoning_priority_symbols(account_repository, configured_settings)
    calendar_symbols = list(priority_symbols.get("holdingSymbols") or []) + list(priority_symbols.get("watchlistSymbols") or [])
    candidate_service = InvestmentCalendarCandidateService(
        candidate_repository=stores.investment_calendar_candidate_store(configured_settings),
        calendar_service=calendar_service,
        settings=configured_settings,
        symbol_repository=stores.symbol_universe_store(configured_settings),
    )
    return OfficialCalendarSyncService(
        calendar_service=calendar_service,
        sources=[
            BokPolicyDecisionCalendarSource(configured_settings),
            FederalReserveFomcCalendarSource(configured_settings),
            BlsMacroReleaseCalendarSource(configured_settings),
            BeaMacroReleaseCalendarSource(configured_settings),
            OpenDartEarningsCalendarSource(configured_settings, target_symbols=calendar_symbols),
            SamsungIrEarningsCalendarSource(configured_settings),
        ],
        candidate_service=candidate_service,
        settings=configured_settings,
        checkpoint_store=stores.runtime_checkpoint_store(configured_settings),
    )


def build_investment_calendar_candidate_service(settings=None, event_publisher=None) -> InvestmentCalendarCandidateService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.investment_calendar.public import InvestmentCalendarCandidateService

    configured_settings = settings or runtime_settings()
    return InvestmentCalendarCandidateService(
        candidate_repository=stores.investment_calendar_candidate_store(configured_settings),
        calendar_service=build_investment_calendar_service(configured_settings, event_publisher),
        settings=configured_settings,
        symbol_repository=stores.symbol_universe_store(configured_settings),
    )


def build_investment_calendar_research_service(settings=None) -> InvestmentCalendarResearchRecommendationService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.news_intelligence import build_news_collection_runner
    from digital_twin.infrastructure.event_bus import default_event_bus
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.investment_calendar.public import InvestmentCalendarResearchRecommendationService

    configured_settings = settings or runtime_settings()
    return InvestmentCalendarResearchRecommendationService(
        candidate_repository=stores.investment_calendar_candidate_store(configured_settings),
        evidence_repository=stores.research_evidence_store(configured_settings),
        account_repository=stores.account_reader(configured_settings),
        news_collection_runner_factory=lambda: build_news_collection_runner(
            configured_settings,
            event_publisher=default_event_bus(),
        ),
        settings=configured_settings,
    )


def build_investment_calendar_discovery_service(settings=None, event_publisher=None) -> InvestmentCalendarDiscoveryService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.investment_research_gateway import ExistingApiResearchGateway
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.investment_calendar.public import InvestmentCalendarDiscoveryService

    configured_settings = settings or runtime_settings()
    return InvestmentCalendarDiscoveryService(
        calendar_service=build_investment_calendar_service(configured_settings, event_publisher),
        candidate_repository=stores.investment_calendar_candidate_store(configured_settings),
        evidence_repository=stores.research_evidence_store(configured_settings),
        account_repository=stores.account_reader(configured_settings),
        research_gateway=ExistingApiResearchGateway(configured_settings),
        settings=configured_settings,
        checkpoint_store=stores.runtime_checkpoint_store(configured_settings),
    )


def build_investment_calendar_runner(settings=None, event_publisher=None) -> InvestmentCalendarRunner:
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.investment_calendar.public import InvestmentCalendarRunner

    configured_settings = settings or runtime_settings()
    return InvestmentCalendarRunner(
        build_investment_calendar_service(configured_settings, event_publisher),
        official_sync_service=build_official_calendar_sync_service(configured_settings, event_publisher),
        discovery_service=build_investment_calendar_discovery_service(configured_settings, event_publisher),
    )
