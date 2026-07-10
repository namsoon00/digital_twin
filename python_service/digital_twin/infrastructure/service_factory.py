from typing import Iterable

from ..application.flow_lens_service import FlowLensService
from ..application.market_data_collection_service import MarketDataCollectionRunner
from ..application.model_review_service import ModelReviewRunner
from ..application.news_collection_service import NewsCollectionRunner
from ..application.monitoring_service import MonitorRunner
from ..application.notification_service import (
    CompositeNotificationContextEnricher,
    DisclosureAnalysisNotificationEnricher,
    NotificationAIValidatedGateEnricher,
    NotificationAIOpinionEnricher,
    NotificationHoldingSnapshotEnricher,
    NotificationQueueRunner,
)
from ..application.ontology_reasoning_service import OntologyReasoningRunner
from ..application.ontology_rule_candidate_service import RuleChangeCandidateProposalService
from ..application.symbol_universe_service import SymbolUniverseService
from ..domain.accounts import AccountConfig
from ..domain.market_data import number
from ..domain.monitoring import RealtimeMonitor
from .event_bus import EventBus, default_event_bus
from .disclosure_analyzer import disclosure_analyzer_from_settings
from .model_review_queue import ModelReviewEnqueuer
from .model_reviewer import reviewer_from_settings
from .notification_ai_reviewer import notification_ai_reviewer_from_settings
from .neo4j_ontology import ontology_repository_from_settings
from .ontology_projection import PortfolioOntologyProjectionRecorder
from .rule_change_candidate_ai import rule_change_candidate_advisor_from_settings
from .notifications import queued_notifier_for_account
from .notifications import send_events
from .notifications import notifier_for_account
from .news_sources import NewsSourceGateway
from .settings import currency_rates, runtime_settings
from .sqlite_model_review import SQLiteModelReviewJobStore
from .sqlite_monitoring import SQLiteMonitorStore
from .sqlite_monitoring import SQLiteEventLog
from .sqlite_monitoring import SQLiteMarketQuoteCache
from .sqlite_monitoring import SQLiteMonitoringCycleRecorder
from .sqlite_monitoring import SQLiteOntologyReasoningCursorStore
from .sqlite_monitoring import SQLiteOntologyQualitySampleStore
from .sqlite_monitoring import SQLiteResearchEvidenceStore
from .sqlite_notifications import SQLiteNotificationJobStore, SQLiteNotificationTemplateStore
from .sqlite_symbols import SQLiteSymbolUniverseStore
from .sqlite_accounts import AccountRegistry
from .symbol_sources import RemoteSymbolSourceGateway
from .toss_snapshots import TossProvider, build_snapshot, demo_positions


def monitor_event_bus() -> EventBus:
    bus = default_event_bus()
    bus.subscribe_all(ModelReviewEnqueuer(SQLiteModelReviewJobStore()).handle)
    return bus


def build_monitor_runner(accounts: Iterable[AccountConfig], event_publisher=None) -> MonitorRunner:
    settings = runtime_settings()
    store = SQLiteMonitorStore()
    ontology_quality_store = SQLiteOntologyQualitySampleStore()
    return MonitorRunner(
        accounts,
        store=store,
        monitor=RealtimeMonitor(settings),
        snapshot_builder=build_snapshot,
        event_sender=send_events,
        event_publisher=event_publisher or monitor_event_bus(),
        cycle_recorder=SQLiteMonitoringCycleRecorder(monitor_store=store),
        ontology_projection_recorder=PortfolioOntologyProjectionRecorder(
            ontology_repository_from_settings(settings),
            quality_store=ontology_quality_store,
            settings=settings,
        ),
    )


def build_model_review_runner(dry_run: bool = False) -> ModelReviewRunner:
    settings = runtime_settings()
    return ModelReviewRunner(
        queue=SQLiteModelReviewJobStore(),
        reviewer=reviewer_from_settings(settings),
        account_repository=AccountRegistry(),
        notifier_factory=lambda account: queued_notifier_for_account(account, message_type="modelReview"),
        dry_run=dry_run,
        settings=settings,
    )


def build_notification_queue_runner(dry_run: bool = False) -> NotificationQueueRunner:
    settings = runtime_settings()
    monitor_store = SQLiteMonitorStore()
    return NotificationQueueRunner(
        queue=SQLiteNotificationJobStore(),
        account_repository=AccountRegistry(),
        notifier_factory=notifier_for_account,
        dry_run=dry_run,
        send_gap_seconds=float(settings.get("notificationSendGapSeconds") or 0),
        stale_after_minutes=int(settings.get("notificationProcessingStaleMinutes") or 30),
        template_renderer=SQLiteNotificationTemplateStore().render_job,
        context_enricher=CompositeNotificationContextEnricher(
            NotificationHoldingSnapshotEnricher(
                monitor_store.load_previous,
                RealtimeMonitor(settings),
            ),
            DisclosureAnalysisNotificationEnricher(
                disclosure_analyzer_from_settings(settings),
                settings,
            ),
            NotificationAIValidatedGateEnricher(
                notification_ai_reviewer_from_settings(settings),
                settings,
            ),
            NotificationAIOpinionEnricher(settings),
        ),
    )


def build_symbol_universe_service(settings=None) -> SymbolUniverseService:
    return SymbolUniverseService(
        store=SQLiteSymbolUniverseStore(),
        source_gateway=RemoteSymbolSourceGateway(),
        settings=settings or runtime_settings(),
        quote_cache=SQLiteMarketQuoteCache(),
    )


def build_market_data_collection_runner(settings=None, event_publisher=None) -> MarketDataCollectionRunner:
    configured_settings = settings or runtime_settings()
    return MarketDataCollectionRunner(
        account_repository=AccountRegistry(),
        symbol_service=build_symbol_universe_service(configured_settings),
        quote_cache=SQLiteMarketQuoteCache(),
        settings=configured_settings,
        provider_factory=lambda account, quote_cache: TossProvider(account, quote_cache=quote_cache),
        event_publisher=event_publisher or default_event_bus(),
    )


def build_news_collection_runner(settings=None, event_publisher=None) -> NewsCollectionRunner:
    configured_settings = settings or runtime_settings()
    return NewsCollectionRunner(
        account_repository=AccountRegistry(),
        monitor_store=SQLiteMonitorStore(),
        symbol_store=SQLiteSymbolUniverseStore(),
        evidence_store=SQLiteResearchEvidenceStore(),
        gateway=NewsSourceGateway(configured_settings),
        settings=configured_settings,
        event_publisher=event_publisher or default_event_bus(),
    )


def build_ontology_reasoning_runner(settings=None, event_publisher=None) -> OntologyReasoningRunner:
    configured_settings = settings or runtime_settings()
    registry = AccountRegistry()
    event_log = SQLiteEventLog()
    ontology_repository = ontology_repository_from_settings(configured_settings)
    return OntologyReasoningRunner(
        event_reader=event_log,
        cursor_store=SQLiteOntologyReasoningCursorStore(),
        monitor_runner_factory=lambda: build_monitor_runner(registry.load()),
        event_publisher=event_publisher or default_event_bus(),
        settings=configured_settings,
        rule_candidate_service=RuleChangeCandidateProposalService(
            ontology_repository=ontology_repository,
            advisor=rule_change_candidate_advisor_from_settings(configured_settings),
            event_reader=event_log,
            settings=configured_settings,
        ),
    )


def build_rule_change_candidate_service(settings=None) -> RuleChangeCandidateProposalService:
    configured_settings = settings or runtime_settings()
    return RuleChangeCandidateProposalService(
        ontology_repository=ontology_repository_from_settings(configured_settings),
        advisor=rule_change_candidate_advisor_from_settings(configured_settings),
        event_reader=SQLiteEventLog(),
        settings=configured_settings,
    )


def build_flow_lens_service(settings=None) -> FlowLensService:
    configured_settings = settings or runtime_settings()
    flow_lens_external_settings = dict(configured_settings)
    def capped_int(key: str, fallback: int, cap: int) -> str:
        return str(min(cap, int(number(flow_lens_external_settings.get(key)) or fallback)))

    flow_lens_external_settings["externalApiRetryAttempts"] = "1"
    flow_lens_external_settings["externalApiTimeoutSeconds"] = str(min(2.0, number(flow_lens_external_settings.get("externalApiTimeoutSeconds")) or 2.0))
    flow_lens_external_settings["externalAlphaMaxSymbols"] = capped_int("externalAlphaMaxSymbols", 1, 1)
    flow_lens_external_settings["externalSecMaxSymbols"] = capped_int("externalSecMaxSymbols", 1, 1)
    flow_lens_external_settings["externalDartMaxSymbols"] = capped_int("externalDartMaxSymbols", 1, 1)
    flow_lens_external_settings["externalNewsMaxSymbols"] = capped_int("externalNewsMaxSymbols", 1, 1)
    flow_lens_external_settings["externalCryptoMaxIds"] = capped_int("externalCryptoMaxIds", 2, 2)
    flow_lens_external_settings["externalFredMaxSeries"] = capped_int("externalFredMaxSeries", 2, 2)
    symbol_service = build_symbol_universe_service(configured_settings)
    return FlowLensService(
        account_repository=AccountRegistry(),
        snapshot_builder=lambda account: build_snapshot(account, external_settings=flow_lens_external_settings),
        demo_positions_provider=demo_positions,
        settings_provider=lambda: configured_settings,
        fx_rates_provider=currency_rates,
        symbol_enricher=symbol_service.enrich,
    )


def flow_lens_snapshot(mock: bool = False, watchlist_symbols: str = ""):
    return build_flow_lens_service().snapshot(mock=mock, watchlist_symbols=watchlist_symbols)
