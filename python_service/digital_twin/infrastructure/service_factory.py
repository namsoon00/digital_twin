import os
import uuid
from typing import Callable, Dict, Iterable

from ..application.flow_lens_service import FlowLensService
from ..application.investment_analysis_service import InvestmentAnalysisService
from ..application.investment_brain_service import InvestmentBrainService
from ..application.investment_research_orchestration_service import InvestmentResearchOrchestrationService
from ..application.hypothesis_proposal_service import HypothesisProposalService
from ..application.investment_strategy_proposal_service import InvestmentStrategyProposalService
from ..application.investment_calendar_candidate_service import InvestmentCalendarCandidateService
from ..application.investment_calendar_extraction_service import InvestmentCalendarExtractionService
from ..application.investment_calendar_research_service import InvestmentCalendarResearchRecommendationService
from ..application.investment_calendar_service import InvestmentCalendarRunner, InvestmentCalendarService
from ..application.kis_realtime_service import KISRealtimeWebSocketRunner
from ..application.market_data_collection_service import MarketDataCollectionRunner
from ..application.model_review_service import ModelReviewRunner
from ..application.news_collection_service import NewsCollectionRunner
from ..application.news_ai_analysis_service import NewsAiAnalysisService
from ..application.news_digest_service import NewsDigestEnqueuer
from ..application.monitoring_service import MonitorRunner
from ..application.notification_service import (
    CompositeNotificationContextEnricher,
    DisclosureAnalysisNotificationEnricher,
    NotificationAIValidatedGateEnricher,
    NotificationAIOpinionEnricher,
    NotificationHoldingSnapshotEnricher,
    NotificationHypothesisResearchEnricher,
    NotificationQueueRunner,
)
from ..application.official_calendar_sync_service import OfficialCalendarSyncService
from ..application.ontology_reasoning_service import OntologyReasoningRunner
from ..application.ontology_lab_service import OntologyLabService
from ..application.ontology_rule_candidate_service import RuleChangeCandidateProposalService
from ..application.symbol_universe_service import SymbolUniverseService
from ..domain.accounts import AccountConfig
from ..domain.events import RESEARCH_EVIDENCE_COLLECTED
from ..domain.market_data import number
from ..domain.monitoring import RealtimeMonitor
from .event_bus import EventBus, default_event_bus
from .bok_calendar_source import BokPolicyDecisionCalendarSource
from .disclosure_analyzer import disclosure_analyzer_from_settings
from .model_review_queue import ModelReviewEnqueuer
from .model_reviewer import reviewer_from_settings
from .notification_ai_reviewer import notification_ai_reviewer_from_settings
from .hypothesis_proposal_ai import hypothesis_proposal_advisor_from_settings
from .investment_research_gateway import CompositeInvestmentResearchGateway, ExistingApiResearchGateway
from .ontology_graph_store import ontology_repository_from_settings
from . import operational_store as stores
from .ontology_projection import PortfolioOntologyProjectionRecorder
from .kis_realtime_ws import KISRealtimeSymbolSelector, KISRealtimeWebSocketClient
from .rule_change_candidate_ai import rule_change_candidate_advisor_from_settings
from .notifications import queued_notifier_for_account
from .notifications import send_events
from .notifications import notifier_for_account
from .news_sources import NewsSourceGateway
from .news_ai_analyzer import news_ai_analyzer_from_settings
from .settings import currency_rates, runtime_settings
from .symbol_sources import RemoteSymbolSourceGateway
from .toss_snapshots import TossProvider, build_snapshot, demo_positions


DISABLED_SETTING_VALUES = {"0", "false", "no", "off", "disabled"}


def setting_truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_SETTING_VALUES


def monitor_event_bus() -> EventBus:
    bus = default_event_bus()
    bus.subscribe_all(ModelReviewEnqueuer(stores.model_review_job_store()).handle)
    return bus


def news_event_bus(settings=None) -> EventBus:
    configured_settings = settings or runtime_settings()
    bus = default_event_bus()
    bus.subscribe(
        RESEARCH_EVIDENCE_COLLECTED,
        NewsDigestEnqueuer(
            account_repository=stores.account_registry(configured_settings),
            monitor_store=stores.monitor_store(configured_settings),
            queue=stores.notification_job_store(configured_settings),
            settings=configured_settings,
            max_items=int(number(configured_settings.get("newsDigestMaxItems")) or 3),
        ).handle,
    )
    calendar_service = build_investment_calendar_service(configured_settings)
    bus.subscribe(
        RESEARCH_EVIDENCE_COLLECTED,
        InvestmentCalendarExtractionService(
            calendar_service=calendar_service,
            account_repository=stores.account_registry(configured_settings),
            candidate_repository=stores.investment_calendar_candidate_store(configured_settings),
            settings=configured_settings,
        ).handle,
    )
    return bus


def monitor_account_job_store_from_settings(settings):
    return stores.monitor_account_job_store(settings)


def build_monitor_runner(
    accounts: Iterable[AccountConfig],
    event_publisher=None,
    progress_callback: Callable[[str, Dict[str, object]], None] = None,
    settings=None,
    typedb_native_rule_execution_enabled: bool = False,
) -> MonitorRunner:
    configured_settings = dict(settings or runtime_settings())
    configured_settings["typedbNativeRuleExecutionEnabled"] = "1" if typedb_native_rule_execution_enabled else "0"
    store = stores.monitor_store(configured_settings)
    ontology_quality_store = stores.ontology_quality_sample_store(configured_settings)
    interval_seconds = int(os.environ.get("PYTHON_REALTIME_INTERVAL_SECONDS") or os.environ.get("REALTIME_NOTIFY_INTERVAL_SECONDS") or configured_settings.get("monitorAccountIntervalSeconds") or 180)
    return MonitorRunner(
        accounts,
        store=store,
        monitor=RealtimeMonitor(configured_settings),
        snapshot_builder=build_snapshot,
        event_sender=send_events,
        event_publisher=event_publisher or monitor_event_bus(),
        cycle_recorder=stores.monitoring_cycle_recorder(configured_settings, store),
        ontology_projection_recorder=PortfolioOntologyProjectionRecorder(
            ontology_repository_from_settings(configured_settings),
            quality_store=ontology_quality_store,
            decision_episode_store=stores.investment_decision_episode_store(configured_settings),
            hypothesis_proposal_store=stores.investment_research_store(configured_settings),
            settings=configured_settings,
        ),
        account_job_store=monitor_account_job_store_from_settings(configured_settings),
        account_job_batch_size=int(configured_settings.get("monitorAccountBatchSize") or os.environ.get("MONITOR_ACCOUNT_BATCH_SIZE") or 10),
        account_job_interval_seconds=interval_seconds,
        account_job_lock_seconds=int(configured_settings.get("monitorAccountLockSeconds") or os.environ.get("MONITOR_ACCOUNT_LOCK_SECONDS") or max(600, interval_seconds * 4)),
        worker_id=os.environ.get("MONITOR_WORKER_ID") or ("monitor-" + uuid.uuid4().hex[:12]),
        progress_callback=progress_callback,
    )


def build_model_review_runner(dry_run: bool = False) -> ModelReviewRunner:
    settings = runtime_settings()
    return ModelReviewRunner(
        queue=stores.model_review_job_store(settings),
        reviewer=reviewer_from_settings(settings),
        account_repository=stores.account_registry(settings),
        notifier_factory=lambda account: queued_notifier_for_account(account, message_type="modelReview"),
        dry_run=dry_run,
        settings=settings,
    )


def build_notification_queue_runner(dry_run: bool = False) -> NotificationQueueRunner:
    settings = runtime_settings()
    monitor_store = stores.monitor_store(settings)
    investment_brain_service = build_investment_brain_service(settings)
    return NotificationQueueRunner(
        queue=stores.notification_job_store(settings),
        account_repository=stores.account_registry(settings),
        notifier_factory=notifier_for_account,
        dry_run=dry_run,
        send_gap_seconds=float(settings.get("notificationSendGapSeconds") or 0),
        stale_after_minutes=int(settings.get("notificationProcessingStaleMinutes") or 30),
        template_renderer=stores.notification_template_store(settings).render_job,
        context_enricher=CompositeNotificationContextEnricher(
            NotificationHoldingSnapshotEnricher(
                monitor_store.load_previous,
                RealtimeMonitor(settings),
            ),
            DisclosureAnalysisNotificationEnricher(
                disclosure_analyzer_from_settings(settings),
                settings,
            ),
            NotificationHypothesisResearchEnricher(
                investment_brain_service,
                settings,
            ),
            NotificationAIValidatedGateEnricher(
                notification_ai_reviewer_from_settings(settings),
                settings,
                stores.investment_decision_episode_store(settings),
            ),
            NotificationAIOpinionEnricher(settings),
        ),
        operator_reports_enabled=str(settings.get("operatorReasoningReportEnabled", "1")).strip().lower() not in {"0", "false", "no", "off"},
    )


def build_investment_brain_service(settings=None) -> InvestmentBrainService:
    configured_settings = settings or runtime_settings()
    research_store = stores.investment_research_store(configured_settings)
    return InvestmentBrainService(
        monitor_store=stores.monitor_store(configured_settings),
        ontology_repository=ontology_repository_from_settings(configured_settings),
        reviewer=notification_ai_reviewer_from_settings(configured_settings),
        decision_episode_store=stores.investment_decision_episode_store(configured_settings),
        research_orchestrator=build_investment_research_orchestrator(configured_settings, research_store),
        reasoning_refresher=build_investment_reasoning_refresher(configured_settings),
        hypothesis_proposal_service=build_hypothesis_proposal_service(configured_settings, research_store),
        research_store=research_store,
        settings=configured_settings,
    )


def build_investment_research_orchestrator(settings=None, research_store=None) -> InvestmentResearchOrchestrationService:
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
        settings=configured_settings,
    )


def build_hypothesis_proposal_service(settings=None, research_store=None) -> HypothesisProposalService:
    configured_settings = settings or runtime_settings()
    return HypothesisProposalService(
        store=research_store or stores.investment_research_store(configured_settings),
        advisor=hypothesis_proposal_advisor_from_settings(configured_settings),
        event_publisher=default_event_bus(),
        settings=configured_settings,
    )


def build_investment_reasoning_refresher(settings=None):
    configured_settings = settings or runtime_settings()
    repository = ontology_repository_from_settings(configured_settings)
    recorder = PortfolioOntologyProjectionRecorder(
        repository,
        quality_store=stores.ontology_quality_sample_store(configured_settings),
        decision_episode_store=stores.investment_decision_episode_store(configured_settings),
        hypothesis_proposal_store=stores.investment_research_store(configured_settings),
        settings={**configured_settings, "typedbNativeRuleExecutionEnabled": "1"},
    )
    account_repository = stores.account_registry(configured_settings)

    def refresh(account_id: str, symbol: str) -> Dict[str, object]:
        accounts = account_repository.load_all() if hasattr(account_repository, "load_all") else account_repository.load()
        account = next((item for item in accounts or [] if str(getattr(item, "account_id", "")) == str(account_id or "")), None)
        if not account:
            return {"status": "account-not-found", "refreshed": False}
        snapshot = build_snapshot(account)
        projection = recorder.record_snapshot(snapshot)
        state = snapshot.to_monitor_state()
        position = (state.get("positions") or {}).get(str(symbol or "").upper()) or (state.get("watchlist") or {}).get(str(symbol or "").upper()) or {}
        inference = projection.get("inferenceBox") if isinstance(projection, dict) and isinstance(projection.get("inferenceBox"), dict) else {}
        execution = projection.get("ruleboxExecution") if isinstance(projection, dict) and isinstance(projection.get("ruleboxExecution"), dict) else {}
        refreshed = bool(projection.get("saved")) and str(execution.get("status") or inference.get("status") or "") == "ok"
        return {
            "status": "completed" if refreshed else str(projection.get("status") or execution.get("status") or "error"),
            "refreshed": refreshed,
            "projection": projection,
            "inferenceGenerationId": inference.get("inferenceGenerationId"),
            "position": position,
            "state": state,
        }

    return refresh


def build_symbol_universe_service(settings=None) -> SymbolUniverseService:
    configured_settings = settings or runtime_settings()
    return SymbolUniverseService(
        store=stores.symbol_universe_store(configured_settings),
        source_gateway=RemoteSymbolSourceGateway(configured_settings),
        settings=configured_settings,
        quote_cache=stores.market_quote_cache(configured_settings),
    )


def build_market_data_collection_runner(settings=None, event_publisher=None) -> MarketDataCollectionRunner:
    configured_settings = settings or runtime_settings()
    return MarketDataCollectionRunner(
        account_repository=stores.account_registry(configured_settings),
        symbol_service=build_symbol_universe_service(configured_settings),
        quote_cache=stores.market_quote_cache(configured_settings),
        settings=configured_settings,
        provider_factory=lambda account, quote_cache: TossProvider(account, quote_cache=quote_cache),
        event_publisher=event_publisher or default_event_bus(),
    )


def build_kis_realtime_websocket_runner(settings=None, event_publisher=None) -> KISRealtimeWebSocketRunner:
    configured_settings = settings or runtime_settings()
    quote_cache = stores.market_quote_cache(configured_settings)
    monitor_store = stores.monitor_store(configured_settings)
    return KISRealtimeWebSocketRunner(
        client=KISRealtimeWebSocketClient(configured_settings, quote_cache=quote_cache),
        symbol_selector=KISRealtimeSymbolSelector(
            stores.account_registry(configured_settings),
            monitor_store,
            quote_cache,
            configured_settings,
        ),
        quote_cache=quote_cache,
        settings=configured_settings,
        event_publisher=event_publisher or default_event_bus(),
    )


def build_news_collection_runner(settings=None, event_publisher=None) -> NewsCollectionRunner:
    configured_settings = settings or runtime_settings()
    return NewsCollectionRunner(
        account_repository=stores.account_registry(configured_settings),
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
    )


def build_investment_calendar_service(settings=None, event_publisher=None) -> InvestmentCalendarService:
    configured_settings = settings or runtime_settings()
    return InvestmentCalendarService(
        repository=stores.investment_calendar_store(configured_settings),
        account_repository=stores.account_registry(configured_settings),
        notification_queue=stores.notification_job_store(configured_settings),
        settings=configured_settings,
        event_publisher=event_publisher or default_event_bus(),
    )


def build_official_calendar_sync_service(settings=None, event_publisher=None) -> OfficialCalendarSyncService:
    configured_settings = settings or runtime_settings()
    return OfficialCalendarSyncService(
        calendar_service=build_investment_calendar_service(configured_settings, event_publisher),
        sources=[
            BokPolicyDecisionCalendarSource(configured_settings),
        ],
        settings=configured_settings,
    )


def build_investment_calendar_candidate_service(settings=None, event_publisher=None) -> InvestmentCalendarCandidateService:
    configured_settings = settings or runtime_settings()
    return InvestmentCalendarCandidateService(
        candidate_repository=stores.investment_calendar_candidate_store(configured_settings),
        calendar_service=build_investment_calendar_service(configured_settings, event_publisher),
    )


def build_investment_calendar_research_service(settings=None) -> InvestmentCalendarResearchRecommendationService:
    configured_settings = settings or runtime_settings()
    return InvestmentCalendarResearchRecommendationService(
        candidate_repository=stores.investment_calendar_candidate_store(configured_settings),
        evidence_repository=stores.research_evidence_store(configured_settings),
        account_repository=stores.account_registry(configured_settings),
        news_collection_runner_factory=lambda: build_news_collection_runner(
            configured_settings,
            event_publisher=default_event_bus(),
        ),
        settings=configured_settings,
    )


def build_investment_calendar_runner(settings=None, event_publisher=None) -> InvestmentCalendarRunner:
    configured_settings = settings or runtime_settings()
    return InvestmentCalendarRunner(
        build_investment_calendar_service(configured_settings, event_publisher),
        official_sync_service=build_official_calendar_sync_service(configured_settings, event_publisher),
    )


def build_ontology_reasoning_runner(settings=None, event_publisher=None) -> OntologyReasoningRunner:
    configured_settings = settings or runtime_settings()
    reasoning_store_settings = dict(configured_settings)
    reasoning_store_settings["_skipOperationalHistoryRetention"] = "1"
    reasoning_monitor_settings = dict(configured_settings)
    reasoning_monitor_settings["_skipOperationalHistoryRetention"] = "1"
    reasoning_native_rule_execution_enabled = setting_truthy(
        configured_settings.get("ontologyReasoningTypeDbNativeRuleExecutionEnabled"),
        True,
    )
    reasoning_monitor_settings["typedbNativeRuleExecutionEnabled"] = "1" if reasoning_native_rule_execution_enabled else "0"
    registry = stores.account_registry(reasoning_store_settings)
    event_log = stores.event_log(reasoning_store_settings)
    ontology_repository = ontology_repository_from_settings(configured_settings)
    return OntologyReasoningRunner(
        event_reader=event_log,
        cursor_store=stores.ontology_reasoning_cursor_store(reasoning_store_settings),
        monitor_runner_factory=lambda: build_monitor_runner(
            registry.load(),
            settings=reasoning_monitor_settings,
            typedb_native_rule_execution_enabled=reasoning_native_rule_execution_enabled,
        ),
        event_publisher=event_publisher or default_event_bus(),
        settings=configured_settings,
        rule_candidate_service=RuleChangeCandidateProposalService(
            ontology_repository=ontology_repository,
            advisor=rule_change_candidate_advisor_from_settings(configured_settings),
            event_reader=event_log,
            settings=configured_settings,
            strategy_proposal_service=build_investment_strategy_proposal_service(configured_settings, event_publisher=event_publisher),
        ),
    )


def build_rule_change_candidate_service(settings=None) -> RuleChangeCandidateProposalService:
    configured_settings = settings or runtime_settings()
    return RuleChangeCandidateProposalService(
        ontology_repository=ontology_repository_from_settings(configured_settings),
        advisor=rule_change_candidate_advisor_from_settings(configured_settings),
        event_reader=stores.event_log(configured_settings),
        settings=configured_settings,
        strategy_proposal_service=build_investment_strategy_proposal_service(configured_settings),
    )


def build_investment_strategy_proposal_service(settings=None, event_publisher=None) -> InvestmentStrategyProposalService:
    configured_settings = settings or runtime_settings()
    return InvestmentStrategyProposalService(
        proposal_store=stores.investment_strategy_proposal_store(configured_settings),
        ontology_repository=ontology_repository_from_settings(configured_settings),
        event_publisher=event_publisher or default_event_bus(),
        settings=configured_settings,
    )


def build_ontology_lab_service(settings=None) -> OntologyLabService:
    configured_settings = settings or runtime_settings()
    return OntologyLabService(
        ontology_repository=ontology_repository_from_settings(configured_settings),
        experiment_store=stores.ontology_experiment_store(configured_settings),
        monitor_store=stores.monitor_store(configured_settings),
        rule_candidate_service=build_rule_change_candidate_service(configured_settings),
        strategy_proposal_service=build_investment_strategy_proposal_service(configured_settings),
        notification_queue=stores.notification_job_store(configured_settings),
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
        account_repository=stores.account_registry(configured_settings),
        snapshot_builder=lambda account: build_snapshot(account, external_settings=flow_lens_external_settings),
        demo_positions_provider=demo_positions,
        settings_provider=lambda: configured_settings,
        fx_rates_provider=currency_rates,
        symbol_enricher=symbol_service.enrich,
    )


def flow_lens_snapshot(mock: bool = False, watchlist_symbols: str = ""):
    return build_flow_lens_service().snapshot(mock=mock, watchlist_symbols=watchlist_symbols)


def build_investment_analysis_service(settings=None) -> InvestmentAnalysisService:
    flow_service = build_flow_lens_service(settings)
    return InvestmentAnalysisService(
        snapshot_provider=lambda mock=False, watchlist_symbols="": flow_service.snapshot(
            mock=mock,
            watchlist_symbols=watchlist_symbols,
        ),
    )


def investment_analysis_snapshot(mock: bool = False, watchlist_symbols: str = ""):
    return build_investment_analysis_service().snapshot(mock=mock, watchlist_symbols=watchlist_symbols)
