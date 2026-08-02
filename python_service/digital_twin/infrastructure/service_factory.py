import os
import time
import uuid
from typing import Callable, Dict, Iterable

from ..application.flow_lens_service import FlowLensService
from ..application.data_pipeline_health_service import DataPipelineHealthNotificationEnqueuer, DataPipelineHealthService
from ..application.ontology_reasoning_queue_health_service import (
    OntologyReasoningQueueHealthNotificationEnqueuer,
    OntologyReasoningQueueHealthService,
)
from ..application.operational_storage_capacity_service import (
    OperationalStorageCapacityNotificationEnqueuer,
    OperationalStorageCapacityService,
)
from ..application.investment_analysis_service import InvestmentAnalysisService
from ..application.investment_brain_service import InvestmentBrainService
from ..application.investment_research_orchestration_service import InvestmentResearchOrchestrationService, InvestmentResearchQueueRunner
from ..application.hypothesis_proposal_service import HypothesisProposalService
from ..application.hypothesis_lifecycle_service import HypothesisLifecycleService
from ..application.hypothesis_lifecycle_policy_service import HypothesisLifecyclePolicyService
from ..application.hypothesis_policy_governance_service import HypothesisPolicyGovernanceService
from ..application.hypothesis_research_planner_service import HypothesisResearchPlanningService
from ..application.hypothesis_review_service import HypothesisReviewService
from ..application.hypothesis_quality_review_service import HypothesisQualityReviewService
from ..application.hypothesis_outcome_replay_service import HypothesisOutcomeReplayService
from ..application.investment_strategy_proposal_service import InvestmentStrategyProposalService
from ..application.investment_calendar_candidate_service import InvestmentCalendarCandidateService
from ..application.investment_calendar_discovery_service import InvestmentCalendarDiscoveryService
from ..application.investment_calendar_extraction_service import InvestmentCalendarExtractionService
from ..application.investment_calendar_research_service import InvestmentCalendarResearchRecommendationService
from ..application.investment_calendar_service import InvestmentCalendarRunner, InvestmentCalendarService
from ..application.kis_realtime_service import KISRealtimeWebSocketRunner
from ..application.market_data_collection_service import MarketDataCollectionRunner
from ..application.model_review_service import ModelReviewRunner
from ..application.news_collection_service import NewsCollectionRunner
from ..application.news_ai_analysis_service import NewsAiAnalysisService
from ..application.news_analysis_enrichment_service import NewsAnalysisEnrichmentRunner
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
from ..application.ontology_reasoning_service import (
    OntologyReasoningRunner,
    lightweight_ontology_reasoning_queue_state,
)
from ..application.ontology_maintenance_service import OntologyMaintenanceRunner
from ..application.ontology_inference_detail_service import OntologyInferenceDetailRunner
from ..application.ontology_rulebox_prewarm_service import OntologyRuleboxPrewarmRunner
from ..application.ontology_world_projection_service import OntologyWorldProjectionRunner
from ..application.ontology_lab_service import OntologyLabService
from ..application.ontology_rule_candidate_service import RuleChangeCandidateProposalService
from ..application.symbol_universe_service import SymbolUniverseService
from ..domain.accounts import AccountConfig
from ..domain.events import (
    DATA_PIPELINE_HEALTH_CHANGED,
    ONTOLOGY_REASONING_QUEUE_HEALTH_CHANGED,
    OPERATIONAL_STORAGE_CAPACITY_CHANGED,
    RESEARCH_EVIDENCE_COLLECTED,
)
from ..domain.market_data import number
from ..domain.monitoring import RealtimeMonitor
from ..domain.ontology_worlds import portfolio_world_id
from .event_bus import EventBus, default_event_bus
from .bok_calendar_source import BokPolicyDecisionCalendarSource
from .opendart_calendar_source import OpenDartEarningsCalendarSource
from .samsung_ir_calendar_source import SamsungIrEarningsCalendarSource
from .disclosure_analyzer import disclosure_analyzer_from_settings
from .model_review_queue import ModelReviewEnqueuer
from .model_reviewer import reviewer_from_settings
from .notification_ai_reviewer import notification_ai_reviewer_from_settings
from .hypothesis_proposal_ai import hypothesis_proposal_advisor_from_settings
from .hypothesis_research_planner_ai import hypothesis_research_planning_advisor_from_settings
from .investment_research_gateway import CompositeInvestmentResearchGateway, ExistingApiResearchGateway
from .ontology_graph_store import ontology_repository_from_settings
from . import operational_store as stores
from .ontology_projection import PortfolioOntologyProjectionRecorder
from .typedb_storage_guard import typedb_storage_health
from .operational_storage_guard import operational_storage_health, operational_storage_inventory
from .kis_realtime_ws import KISRealtimeSymbolSelector, KISRealtimeWebSocketClient
from .rule_change_candidate_ai import rule_change_candidate_advisor_from_settings
from .notifications import queued_notifier_for_account
from .notifications import send_events
from .notifications import notifier_for_account
from .notifications import notifier_for_operations
from .news_sources import NewsSourceGateway
from .news_ai_analyzer import news_ai_analyzer_from_settings
from .external_signals import ExternalSignalProvider
from .settings import currency_rates, runtime_settings
from .symbol_sources import RemoteSymbolSourceGateway
from .toss_snapshots import TossProvider, build_snapshot, demo_positions
from .reasoning_snapshot_source import LatestMonitorSnapshotReasoningSource


DISABLED_SETTING_VALUES = {"0", "false", "no", "off", "disabled"}


def setting_truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_SETTING_VALUES


def monitor_event_bus(settings=None) -> EventBus:
    configured_settings = settings or runtime_settings()
    bus = default_event_bus()
    bus.subscribe_all(ModelReviewEnqueuer(stores.model_review_job_store(configured_settings)).handle)
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
    bus.subscribe(
        DATA_PIPELINE_HEALTH_CHANGED,
        DataPipelineHealthNotificationEnqueuer(
            account_repository=stores.account_registry(configured_settings),
            queue=stores.notification_job_store(configured_settings),
            settings=configured_settings,
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


def data_pipeline_health_event_bus(settings=None) -> EventBus:
    configured_settings = settings or runtime_settings()
    bus = default_event_bus()
    bus.subscribe(
        DATA_PIPELINE_HEALTH_CHANGED,
        DataPipelineHealthNotificationEnqueuer(
            account_repository=stores.account_registry(configured_settings),
            queue=stores.notification_job_store(configured_settings),
            settings=configured_settings,
        ).handle,
    )
    return bus


def operational_storage_event_bus(settings=None) -> EventBus:
    configured_settings = settings or runtime_settings()
    bus = default_event_bus()
    bus.subscribe(
        OPERATIONAL_STORAGE_CAPACITY_CHANGED,
        OperationalStorageCapacityNotificationEnqueuer(
            queue=stores.notification_job_store(configured_settings),
            fallback_notifier_factory=notifier_for_operations,
        ).handle,
    )
    return bus


def build_operational_storage_capacity_service(settings=None) -> OperationalStorageCapacityService:
    configured_settings = settings or runtime_settings()
    return OperationalStorageCapacityService(
        store=stores.operational_storage_capacity_state_store(configured_settings),
        settings=configured_settings,
    )


def observe_operational_storage_capacity(
    settings=None,
    snapshot=None,
    force_alert: bool = False,
):
    """Record one bounded capacity observation and dispatch any state alert.

    The helper is intentionally usable from error paths.  When MySQL cannot
    accept the event or notification job, the subscribed enqueuer sends the
    operations notifier directly instead.
    """

    configured_settings = settings or runtime_settings()
    observed = dict(snapshot or operational_storage_inventory(configured_settings))
    health, event = build_operational_storage_capacity_service(configured_settings).record(
        observed,
        force_alert=force_alert,
    )
    if event:
        operational_storage_event_bus(configured_settings).publish(event)
    return health


def ontology_reasoning_event_bus(settings=None) -> EventBus:
    configured_settings = settings or runtime_settings()
    bus = default_event_bus()
    bus.subscribe(
        ONTOLOGY_REASONING_QUEUE_HEALTH_CHANGED,
        OntologyReasoningQueueHealthNotificationEnqueuer(
            queue=stores.notification_job_store(configured_settings),
            settings=configured_settings,
        ).handle,
    )
    return bus


def monitor_account_job_store_from_settings(settings):
    """Enable durable account scheduling only when it is explicitly configured.

    The monitor runner treats the presence of this store as the queue-mode
    switch. Constructing it unconditionally made ``monitorAccountQueueEnabled``
    ineffective and delayed normal monitor refreshes behind the job cadence.
    """
    configured_settings = dict(settings or {})
    if not setting_truthy(configured_settings.get("monitorAccountQueueEnabled"), default=False):
        return None
    return stores.monitor_account_job_store(configured_settings)


def build_hypothesis_lifecycle_service(settings=None, event_publisher=None) -> HypothesisLifecycleService:
    configured_settings = settings or runtime_settings()
    return HypothesisLifecycleService(
        store=stores.hypothesis_lifecycle_store(configured_settings),
        event_publisher=event_publisher,
        settings=configured_settings,
    )


def build_monitor_runner(
    accounts: Iterable[AccountConfig],
    event_publisher=None,
    progress_callback: Callable[[str, Dict[str, object]], None] = None,
    settings=None,
    typedb_native_rule_execution_enabled: bool = False,
    snapshot_builder: Callable = None,
    ontology_projection_enabled: bool = None,
    ontology_repository=None,
    monitor_store=None,
    source_snapshot_replay: bool = False,
) -> MonitorRunner:
    configured_settings = dict(settings or runtime_settings())
    configured_settings["typedbNativeRuleExecutionEnabled"] = "1" if typedb_native_rule_execution_enabled else "0"
    if ontology_projection_enabled is None:
        # Normal monitoring commits source data and publishes one verified
        # snapshot request.  The durable reasoning worker is then the only
        # TypeDB ABox/InferenceBox writer.  Operators can re-enable the old
        # synchronous path explicitly for a diagnostic run.
        ontology_projection_enabled = setting_truthy(
            configured_settings.get("ontologyMonitorInlineProjectionEnabled"),
            False,
        )
    monitor_snapshot_settings = dict(configured_settings)
    # The dedicated market-data worker owns slow external refreshes. The
    # realtime monitor consumes its cache so a vendor timeout cannot hold a
    # price/technical alert behind yfinance, news, or disclosure collection.
    monitor_snapshot_settings["_externalSignalsCacheOnly"] = "1"
    # The normal monitor owns the full research archive.  Isolated TypeDB
    # replay injects a read-only, target-scoped source store so selecting one
    # mailbox symbol does not deserialize every provider document first.
    store = monitor_store or stores.monitor_store(configured_settings)
    market_time_series_store = stores.market_time_series_store(configured_settings)
    ontology_quality_store = stores.ontology_quality_sample_store(configured_settings)
    projection_repository = ontology_repository or ontology_repository_from_settings(configured_settings)
    interval_seconds = int(os.environ.get("PYTHON_REALTIME_INTERVAL_SECONDS") or os.environ.get("REALTIME_NOTIFY_INTERVAL_SECONDS") or configured_settings.get("monitorAccountIntervalSeconds") or 120)
    publisher = event_publisher or monitor_event_bus(configured_settings)
    return MonitorRunner(
        accounts,
        store=store,
        monitor=RealtimeMonitor(configured_settings),
        snapshot_builder=snapshot_builder or (
            lambda account: build_snapshot(account, external_settings=monitor_snapshot_settings)
        ),
        event_sender=send_events,
        event_publisher=publisher,
        cycle_recorder=stores.monitoring_cycle_recorder(
            configured_settings,
            store,
            market_time_series_store,
        ),
        ontology_projection_recorder=PortfolioOntologyProjectionRecorder(
            projection_repository,
            quality_store=ontology_quality_store,
            projection_run_store=stores.ontology_projection_run_store(configured_settings),
            decision_episode_store=stores.investment_decision_episode_store(configured_settings),
            hypothesis_proposal_store=stores.investment_research_store(configured_settings),
            hypothesis_lifecycle_store=stores.hypothesis_lifecycle_store(configured_settings),
            data_pipeline_health_store=stores.data_pipeline_health_store(configured_settings),
            market_time_series_store=market_time_series_store,
            world_projection_outbox=stores.ontology_world_projection_outbox_store(configured_settings),
            inference_detail_outbox=stores.ontology_inference_detail_outbox_store(configured_settings),
            graph_assembly_cache_store=stores.ontology_graph_assembly_cache_store(configured_settings),
            settings=configured_settings,
        ),
        hypothesis_lifecycle_service=build_hypothesis_lifecycle_service(configured_settings, publisher),
        ontology_projection_enabled=bool(ontology_projection_enabled),
        account_job_store=monitor_account_job_store_from_settings(configured_settings),
        account_job_batch_size=int(configured_settings.get("monitorAccountBatchSize") or os.environ.get("MONITOR_ACCOUNT_BATCH_SIZE") or 10),
        account_job_interval_seconds=interval_seconds,
        account_job_lock_seconds=int(configured_settings.get("monitorAccountLockSeconds") or os.environ.get("MONITOR_ACCOUNT_LOCK_SECONDS") or max(600, interval_seconds * 4)),
        worker_id=os.environ.get("MONITOR_WORKER_ID") or ("monitor-" + uuid.uuid4().hex[:12]),
        progress_callback=progress_callback,
        source_snapshot_replay=source_snapshot_replay,
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
    reasoning_queue_probe = build_ontology_reasoning_queue_probe(settings)
    queue_health_service = OntologyReasoningQueueHealthService(
        store=stores.ontology_reasoning_cursor_store(settings),
        settings=settings,
    )

    def queue_health_at_dispatch():
        # Do not trust the event payload alone: an operations job can wait
        # behind outbound delivery work while the reasoning queue already
        # recovered. This deliberately avoids the full runner.status() path:
        # delivery must not contend with account-priority or TypeDB diagnostics
        # while a live projection is writing.
        return queue_health_service.observe(reasoning_queue_probe())

    return NotificationQueueRunner(
        queue=stores.notification_job_store(settings),
        account_repository=stores.account_registry(settings),
        notifier_factory=notifier_for_account,
        operations_notifier_factory=notifier_for_operations,
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
        settings=settings,
        operational_state_resolver=queue_health_at_dispatch,
    )


def build_investment_brain_service(settings=None) -> InvestmentBrainService:
    configured_settings = settings or runtime_settings()
    research_store = stores.investment_research_store(configured_settings)
    ontology_repository = ontology_repository_from_settings(configured_settings)
    decision_episode_store = stores.investment_decision_episode_store(configured_settings)
    lifecycle_store = stores.hypothesis_lifecycle_store(configured_settings)
    hypothesis_review_service = HypothesisReviewService(
        hypothesis_lifecycle_store=lifecycle_store,
        decision_episode_store=decision_episode_store,
        ontology_repository=ontology_repository,
        settings=configured_settings,
    )
    hypothesis_lifecycle_policy_service = HypothesisLifecyclePolicyService(ontology_repository)
    hypothesis_quality_review_service = HypothesisQualityReviewService(decision_episode_store=decision_episode_store)
    return InvestmentBrainService(
        monitor_store=stores.monitor_store(configured_settings),
        ontology_repository=ontology_repository,
        reviewer=notification_ai_reviewer_from_settings(configured_settings),
        decision_episode_store=decision_episode_store,
        research_orchestrator=build_investment_research_orchestrator(configured_settings, research_store),
        hypothesis_proposal_service=build_hypothesis_proposal_service(configured_settings, research_store),
        research_store=research_store,
        settings=configured_settings,
        hypothesis_lifecycle_store=lifecycle_store,
        hypothesis_review_service=hypothesis_review_service,
        hypothesis_lifecycle_policy_service=hypothesis_lifecycle_policy_service,
        hypothesis_quality_review_service=hypothesis_quality_review_service,
        hypothesis_policy_governance_service=HypothesisPolicyGovernanceService(
            ontology_repository=ontology_repository,
            lifecycle_policy_service=hypothesis_lifecycle_policy_service,
        ),
        hypothesis_outcome_replay_service=HypothesisOutcomeReplayService(
            decision_episode_store=decision_episode_store,
            hypothesis_review_service=hypothesis_review_service,
            quality_review_service=hypothesis_quality_review_service,
        ),
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
        hypothesis_research_planner=build_hypothesis_research_planning_service(configured_settings),
        settings=configured_settings,
    )


def build_investment_research_queue_runner(settings=None) -> InvestmentResearchQueueRunner:
    configured_settings = settings or runtime_settings()
    research_store = stores.investment_research_store(configured_settings)
    return InvestmentResearchQueueRunner(
        store=research_store,
        orchestrator=build_investment_research_orchestrator(configured_settings, research_store),
    )


def build_hypothesis_proposal_service(settings=None, research_store=None) -> HypothesisProposalService:
    configured_settings = settings or runtime_settings()
    return HypothesisProposalService(
        store=research_store or stores.investment_research_store(configured_settings),
        advisor=hypothesis_proposal_advisor_from_settings(configured_settings),
        event_publisher=default_event_bus(),
        settings=configured_settings,
    )


def build_hypothesis_research_planning_service(settings=None) -> HypothesisResearchPlanningService:
    configured_settings = settings or runtime_settings()
    return HypothesisResearchPlanningService(
        advisor=hypothesis_research_planning_advisor_from_settings(configured_settings),
        settings=configured_settings,
    )


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
        provider_factory=lambda account, quote_cache: TossProvider(account, quote_cache=quote_cache, settings=configured_settings),
        event_publisher=event_publisher or data_pipeline_health_event_bus(configured_settings),
        time_series_store=stores.market_time_series_store(configured_settings),
        health_service=DataPipelineHealthService(
            stores.data_pipeline_health_store(configured_settings),
            configured_settings,
        ),
        decision_episode_store=stores.investment_decision_episode_store(configured_settings),
        external_signal_refresher=lambda positions: ExternalSignalProvider(
            settings=configured_settings,
        ).signals_for_positions(positions, cache_scope="account-snapshot"),
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
        health_service=DataPipelineHealthService(
            stores.data_pipeline_health_store(configured_settings),
            configured_settings,
        ),
    )


def build_news_analysis_enrichment_runner(settings=None, event_publisher=None) -> NewsAnalysisEnrichmentRunner:
    configured_settings = settings or runtime_settings()
    return NewsAnalysisEnrichmentRunner(
        evidence_store=stores.research_evidence_store(configured_settings),
        analysis_service=NewsAiAnalysisService(
            news_ai_analyzer_from_settings(configured_settings),
            configured_settings,
        ),
        settings=configured_settings,
        event_publisher=event_publisher or news_event_bus(configured_settings),
        storage_guard=lambda: operational_storage_health(configured_settings),
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
    calendar_service = build_investment_calendar_service(configured_settings, event_publisher)
    account_repository = stores.account_registry(configured_settings)
    priority_symbols = ontology_reasoning_priority_symbols(account_repository, configured_settings)
    calendar_symbols = list(priority_symbols.get("holdingSymbols") or []) + list(priority_symbols.get("watchlistSymbols") or [])
    candidate_service = InvestmentCalendarCandidateService(
        candidate_repository=stores.investment_calendar_candidate_store(configured_settings),
        calendar_service=calendar_service,
        settings=configured_settings,
    )
    return OfficialCalendarSyncService(
        calendar_service=calendar_service,
        sources=[
            BokPolicyDecisionCalendarSource(configured_settings),
            OpenDartEarningsCalendarSource(configured_settings, target_symbols=calendar_symbols),
            SamsungIrEarningsCalendarSource(configured_settings),
        ],
        candidate_service=candidate_service,
        settings=configured_settings,
    )


def build_investment_calendar_candidate_service(settings=None, event_publisher=None) -> InvestmentCalendarCandidateService:
    configured_settings = settings or runtime_settings()
    return InvestmentCalendarCandidateService(
        candidate_repository=stores.investment_calendar_candidate_store(configured_settings),
        calendar_service=build_investment_calendar_service(configured_settings, event_publisher),
        settings=configured_settings,
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


def build_investment_calendar_discovery_service(settings=None, event_publisher=None) -> InvestmentCalendarDiscoveryService:
    configured_settings = settings or runtime_settings()
    return InvestmentCalendarDiscoveryService(
        calendar_service=build_investment_calendar_service(configured_settings, event_publisher),
        candidate_repository=stores.investment_calendar_candidate_store(configured_settings),
        evidence_repository=stores.research_evidence_store(configured_settings),
        account_repository=stores.account_registry(configured_settings),
        research_gateway=ExistingApiResearchGateway(configured_settings),
        settings=configured_settings,
    )


def build_investment_calendar_runner(settings=None, event_publisher=None) -> InvestmentCalendarRunner:
    configured_settings = settings or runtime_settings()
    return InvestmentCalendarRunner(
        build_investment_calendar_service(configured_settings, event_publisher),
        official_sync_service=build_official_calendar_sync_service(configured_settings, event_publisher),
        discovery_service=build_investment_calendar_discovery_service(configured_settings, event_publisher),
    )


def ontology_reasoning_priority_symbols(account_repository, settings=None) -> Dict[str, list]:
    """Return the latest account focus set for worker scheduling only.

    Holdings are handled before watchlist names and both are handled before
    background universe ticks. The snapshot remains the source of truth for
    positions; this helper does not create investment facts or decisions.
    """
    configured_settings = settings or runtime_settings()
    roles = {"holdingSymbols": [], "watchlistSymbols": []}

    def add(role: str, value: object) -> None:
        symbol = str(value or "").upper().strip()
        if symbol and symbol not in roles[role]:
            roles[role].append(symbol)

    try:
        previous = stores.monitor_store(configured_settings).previous
    except Exception:  # noqa: BLE001 - a missing snapshot simply leaves account config priorities.
        previous = {}
    for state in (previous or {}).values():
        if not isinstance(state, dict):
            continue
        for container, role in (("positions", "holdingSymbols"), ("watchlist", "watchlistSymbols")):
            items = state.get(container)
            if isinstance(items, dict):
                for key, item in items.items():
                    add(role, item.get("symbol") if isinstance(item, dict) else key)
            elif isinstance(items, list):
                for item in items:
                    add(role, item.get("symbol") if isinstance(item, dict) else item)
    try:
        accounts = account_repository.load()
    except Exception:  # noqa: BLE001 - the live snapshot above is still sufficient when available.
        accounts = []
    for account in accounts:
        for symbol in getattr(account, "watchlist_symbols", []) or []:
            add("watchlistSymbols", symbol)
    return roles


def typedb_projection_recovery_health(ontology_repository, world_id: str) -> Dict[str, object]:
    """Check durable TypeDB availability before retrying a failed projection.

    The circuit guards the worker after an infrastructure failure. It must not
    require the pending event's future InferenceBox generation to already
    exist, because the normal projection path is responsible for producing it
    once this health probe succeeds.
    """
    clean_world_id = str(world_id or "").strip()
    active = ontology_repository.active_abox_metadata(world_id=clean_world_id)
    active_status = str((active or {}).get("status") or "").strip().lower()
    active_abox = str((active or {}).get("aboxSnapshotId") or "").strip()
    marker_reader = getattr(ontology_repository, "inferencebox_recovery_metadata", None)
    if callable(marker_reader):
        try:
            inference = marker_reader(world_id=clean_world_id)
        except Exception as error:  # noqa: BLE001 - active ABox availability is the recovery gate.
            inference = {
                "status": "error",
                "reason": "TypeDB active InferenceBox marker 조회 실패: " + str(error)[:180],
            }
    else:
        inference = {
            "status": "not-supported",
            "reason": "현재 저장소는 경량 InferenceBox 복구 표식을 제공하지 않습니다.",
        }
    inference = dict(inference or {}) if isinstance(inference, dict) else {"status": "invalid"}
    source_abox = str(inference.get("sourceAboxSnapshotId") or "").strip()
    aligned = bool(source_abox and source_abox == active_abox)
    ready = bool(active_status == "ok" and active_abox)
    return {
        "ready": ready,
        "recoveryMode": "active-abox-health-probe",
        "worldId": clean_world_id,
        "activeAboxSnapshotId": active_abox,
        "activeAboxStatus": active_status,
        "inferenceStatus": str(inference.get("status") or ""),
        "inferenceGenerationId": str(inference.get("inferenceGenerationId") or ""),
        "sourceAboxSnapshotId": source_abox,
        "inferenceGenerationAligned": aligned,
        "inferenceTargetSymbols": list(inference.get("targetSymbols") or []),
        "inferenceDiagnostic": str(inference.get("reason") or "")[:180],
        "requiresFreshProjection": not aligned,
        "reason": (
            "현재 활성 ABox를 읽을 수 있어 보류된 이벤트의 새 TypeDB 추론을 다시 시도합니다."
            if ready
            else str((active or {}).get("reason") or "현재 활성 ABox를 검증하지 못했습니다.")[:180]
        ),
    }


def build_ontology_reasoning_queue_probe(settings=None):
    """Build a low-cost read-only signal for lower-priority workers.

    The full reasoning status is an operator diagnostic; it evaluates account
    priority and TypeDB health.  That is intentionally not a dependency of
    shared-world projection, ABox retention, or notification dispatch.
    """
    configured_settings = settings or runtime_settings()
    store_settings = dict(configured_settings)
    # This probe is called by isolated low-priority workers.  Do not let its
    # construction run schema DDL or operational-history retention before it
    # can yield.
    store_settings["_skipOperationalHistoryRetention"] = "1"
    store_settings["_skipOperationalSchemaBootstrap"] = "1"
    event_reader = stores.event_log(store_settings)
    cursor_store = stores.ontology_reasoning_cursor_store(store_settings)
    mailbox_store = stores.ontology_reasoning_mailbox_store(store_settings)
    try:
        cache_seconds = float(str(configured_settings.get("ontologyReasoningQueueProbeCacheSeconds") or "5").strip())
    except ValueError:
        cache_seconds = 5.0
    cache_seconds = max(0.0, min(60.0, cache_seconds))
    cache = {"at": 0.0, "value": None}

    def probe():
        now = time.monotonic()
        cached = cache.get("value")
        age = now - float(cache.get("at") or 0.0)
        if cached is not None and age >= 0 and age < cache_seconds:
            value = dict(cached or {})
            value["probeCached"] = True
            value["probeCacheAgeMs"] = int(age * 1000)
            return value
        try:
            value = lightweight_ontology_reasoning_queue_state(
                event_reader,
                cursor_store,
                mailbox_store=mailbox_store,
                settings=configured_settings,
            )
            cache["at"] = time.monotonic()
            cache["value"] = dict(value or {})
            return value
        except Exception as error:  # noqa: BLE001 - the global TypeDB lease remains the final safety boundary.
            return {
                "status": "error",
                "effectivePendingCount": 0,
                "probeHealth": {"status": "degraded", "reason": str(error)[:180]},
                "queueHealth": {"status": "degraded", "reason": str(error)[:180], "scope": "probe-connectivity"},
            }

    return probe


def build_ontology_world_projection_runner(settings=None) -> OntologyWorldProjectionRunner:
    """Build the independent durable shared-world projection worker."""
    configured_settings = settings or runtime_settings()
    store_settings = dict(configured_settings)
    store_settings["_skipOperationalHistoryRetention"] = "1"
    store_settings["_skipOperationalSchemaBootstrap"] = "1"

    return OntologyWorldProjectionRunner(
        outbox=stores.ontology_world_projection_outbox_store(store_settings),
        projection_recorder=PortfolioOntologyProjectionRecorder(
            ontology_repository_from_settings(configured_settings),
            graph_assembly_cache_store=stores.ontology_graph_assembly_cache_store(store_settings),
            settings=configured_settings,
            source="ontology-world-projection",
        ),
        settings=configured_settings,
        worker_id=os.environ.get("ONTOLOGY_WORLD_PROJECTION_WORKER_ID") or "",
        reasoning_queue_probe=build_ontology_reasoning_queue_probe(configured_settings),
        storage_guard=lambda: typedb_storage_health(configured_settings),
    )


def build_ontology_inference_detail_runner(settings=None) -> OntologyInferenceDetailRunner:
    """Build the idle-only durable InferenceBox detail readback worker."""
    configured_settings = settings or runtime_settings()
    store_settings = dict(configured_settings)
    store_settings["_skipOperationalHistoryRetention"] = "1"
    store_settings["_skipOperationalSchemaBootstrap"] = "1"
    return OntologyInferenceDetailRunner(
        outbox=stores.ontology_inference_detail_outbox_store(store_settings),
        ontology_repository=ontology_repository_from_settings(configured_settings),
        settings=configured_settings,
        worker_id=os.environ.get("ONTOLOGY_INFERENCE_DETAIL_WORKER_ID") or "",
        reasoning_queue_probe=build_ontology_reasoning_queue_probe(configured_settings),
    )


def build_ontology_rulebox_prewarm_runner(settings=None) -> OntologyRuleboxPrewarmRunner:
    """Build the isolated compiler worker for the active TypeDB RuleBox."""
    configured_settings = settings or runtime_settings()
    store_settings = dict(configured_settings)
    store_settings["_skipOperationalHistoryRetention"] = "1"
    store_settings["_skipOperationalSchemaBootstrap"] = "1"
    return OntologyRuleboxPrewarmRunner(
        ontology_repository=ontology_repository_from_settings(configured_settings),
        settings=configured_settings,
        reasoning_queue_probe=build_ontology_reasoning_queue_probe(configured_settings),
        prewarm_state_store=stores.ontology_rulebox_prewarm_state_store(store_settings),
        storage_guard=lambda: typedb_storage_health(configured_settings),
    )


def build_ontology_maintenance_runner(settings=None) -> OntologyMaintenanceRunner:
    """Build the isolated, low-priority scoped ABox retention worker."""

    configured_settings = settings or runtime_settings()
    store_settings = dict(configured_settings)
    store_settings["_skipOperationalHistoryRetention"] = "1"
    store_settings["_skipOperationalSchemaBootstrap"] = "1"

    return OntologyMaintenanceRunner(
        ontology_repository=ontology_repository_from_settings(configured_settings),
        state_store=stores.ontology_maintenance_state_store(store_settings),
        settings=configured_settings,
        reasoning_queue_probe=build_ontology_reasoning_queue_probe(configured_settings),
    )


def build_ontology_reasoning_runner(settings=None, event_publisher=None) -> OntologyReasoningRunner:
    configured_settings = settings or runtime_settings()
    reasoning_store_settings = dict(configured_settings)
    reasoning_store_settings["_skipOperationalHistoryRetention"] = "1"
    reasoning_store_settings["_skipOperationalSchemaBootstrap"] = "1"
    reasoning_monitor_settings = dict(configured_settings)
    reasoning_monitor_settings["_skipOperationalHistoryRetention"] = "1"
    reasoning_monitor_settings["_skipOperationalSchemaBootstrap"] = "1"
    # External collection is owned by dedicated workers. Re-running it while
    # materializing an ABox can block TypeDB reasoning on a vendor response.
    reasoning_monitor_settings["_externalSignalsCacheOnly"] = "1"
    reasoning_native_rule_execution_enabled = setting_truthy(
        configured_settings.get("ontologyReasoningTypeDbNativeRuleExecutionEnabled"),
        True,
    )
    reasoning_monitor_settings["typedbNativeRuleExecutionEnabled"] = "1" if reasoning_native_rule_execution_enabled else "0"
    registry = stores.account_registry(reasoning_store_settings)
    event_log = stores.event_log(reasoning_store_settings)
    ontology_repository = ontology_repository_from_settings(configured_settings)
    cursor_store = stores.ontology_reasoning_cursor_store(reasoning_store_settings)
    rulebox_prewarm_state_store = stores.ontology_rulebox_prewarm_state_store(
        reasoning_store_settings,
    )
    snapshot_readiness_source = LatestMonitorSnapshotReasoningSource(
        stores.ontology_reasoning_monitor_store(reasoning_store_settings),
        settings=reasoning_monitor_settings,
    )

    def source_snapshot_preflight(reasoning_context):
        context = dict(reasoning_context or {})
        requested_accounts = {
            str(account_id or "").strip()
            for account_id in context.get("accountIds") or []
            if str(account_id or "").strip()
        }
        accounts = [
            account for account in (registry.load() or [])
            if not requested_accounts or str(getattr(account, "account_id", "") or "").strip() in requested_accounts
        ]
        if requested_accounts and len(accounts) != len(requested_accounts):
            return {
                "ready": False,
                "status": "deferred-source-snapshot",
                "reason": "The requested account has no registered monitor snapshot source.",
                "retryAfterSeconds": 30,
                "accounts": [
                    {"accountId": account_id, "status": "deferred"}
                    for account_id in sorted(requested_accounts)
                    if account_id not in {
                        str(getattr(account, "account_id", "") or "").strip()
                        for account in accounts
                    }
                ],
            }
        return snapshot_readiness_source.preflight(accounts, context)

    def projection_recovery_probe(account_ids, symbols):
        requested_accounts = {
            str(account_id or "").strip()
            for account_id in account_ids or []
            if str(account_id or "").strip()
        }
        accounts = list(registry.load() or [])
        selected_accounts = [
            account for account in accounts
            if not requested_accounts or str(getattr(account, "account_id", "") or "") in requested_accounts
        ]
        if requested_accounts and len(selected_accounts) != len(requested_accounts):
            return {
                "ready": False,
                "status": "account-not-found",
                "reason": "The interrupted projection account is no longer registered.",
                "accounts": [{"accountId": account_id, "ready": False} for account_id in sorted(requested_accounts)],
            }
        requested_symbols = {
            str(symbol or "").upper().strip()
            for symbol in symbols or []
            if str(symbol or "").strip()
        }
        rows = []
        tenant_id = str(configured_settings.get("ontologyTenantId") or configured_settings.get("tenantId") or "")
        for account in selected_accounts:
            account_id = str(getattr(account, "account_id", "") or "").strip()
            world_id = portfolio_world_id(account_id, tenant_id)
            try:
                health = typedb_projection_recovery_health(ontology_repository, world_id)
            except Exception as error:  # noqa: BLE001 - keep the circuit open until the normal retry can read TypeDB.
                rows.append({"accountId": account_id, "worldId": world_id, "ready": False, "reason": str(error)[:180]})
                continue
            rows.append({
                "accountId": account_id,
                "requestedSymbols": sorted(requested_symbols),
                **health,
            })
        return {
            "ready": bool(rows) and all(bool(row.get("ready")) for row in rows),
            "status": "ready" if rows and all(bool(row.get("ready")) for row in rows) else "not-ready",
            "recoveryMode": "active-abox-health-probe",
            "accounts": rows,
        }

    def projection_lease_recovery():
        """Recover only verified-dead local TypeDB writers after a timeout.

        The reasoning parent invokes this only when the coordinator is already
        held or a killable child has exceeded its hard timeout. The repository
        validates hostname and PID before it removes any durable lease.
        """
        recover = getattr(ontology_repository, "recover_all_dead_local_scoped_abox_write_leases", None)
        if not callable(recover):
            return {"status": "unsupported", "clearedCount": 0, "worldCount": 0}
        return dict(recover() or {})

    def projection_coordinator_lease_recovery():
        """Probe only the global writer lease while a live writer may run."""
        recover = getattr(ontology_repository, "recover_dead_projection_coordinator_lease", None)
        if not callable(recover):
            return {"status": "unsupported"}
        return dict(recover() or {})

    def reasoning_worker_maintenance():
        """Keep legacy reasoning cadence free of TypeDB physical deletes.

        The dedicated ``ontology-maintenance`` worker owns all immutable ABox
        retention across portfolio, market, and knowledge worlds.  The
        reasoning runner still invokes this compatibility hook so it can prune
        its durable mailbox on the existing cadence, but it must never take
        the TypeDB writer lease after a live investment projection.
        """
        return {
            "status": "delegated",
            "maintenanceMode": "dedicated-abox-worker",
            "reason": "Scoped ABox retention is owned by the ontology-maintenance worker.",
        }

    def reasoning_monitor_runner():
        reasoning_snapshot_store = stores.ontology_reasoning_monitor_store(
            reasoning_store_settings,
        )
        runner = build_monitor_runner(
            registry.load(),
            settings=reasoning_monitor_settings,
            typedb_native_rule_execution_enabled=reasoning_native_rule_execution_enabled,
            ontology_projection_enabled=True,
            ontology_repository=ontology_repository,
            monitor_store=reasoning_snapshot_store,
            source_snapshot_replay=True,
        )
        runner.snapshot_builder = LatestMonitorSnapshotReasoningSource(
            reasoning_snapshot_store,
            settings=reasoning_monitor_settings,
        )
        return runner

    def refresh_reasoning_monitor_settings(updated_settings, changed_keys, removed_keys):
        """Keep a warm sidecar's short-lived monitor runners in sync.

        The monitor runner is composed per durable turn, but its settings map
        is intentionally retained so it can reuse the TypeDB repository and
        source snapshot boundaries.  Only the application-owned operational
        keys are forwarded by ``OntologyReasoningRunner``.
        """
        updated = dict(updated_settings or {})
        for key in changed_keys or []:
            if key in updated:
                reasoning_monitor_settings[key] = updated[key]
        for key in removed_keys or []:
            reasoning_monitor_settings.pop(key, None)

    return OntologyReasoningRunner(
        event_reader=event_log,
        cursor_store=cursor_store,
        monitor_runner_factory=reasoning_monitor_runner,
        event_publisher=event_publisher or ontology_reasoning_event_bus(reasoning_store_settings),
        settings=configured_settings,
        rule_candidate_service=RuleChangeCandidateProposalService(
            ontology_repository=ontology_repository,
            advisor=rule_change_candidate_advisor_from_settings(configured_settings),
            event_reader=event_log,
            settings=reasoning_store_settings,
            strategy_proposal_service=build_investment_strategy_proposal_service(reasoning_store_settings, event_publisher=event_publisher),
        ),
        research_store=stores.investment_research_store(reasoning_store_settings),
        priority_symbols_provider=lambda: ontology_reasoning_priority_symbols(registry, reasoning_store_settings),
        projection_recovery_probe=projection_recovery_probe,
        maintenance_runner=reasoning_worker_maintenance,
        storage_guard=lambda: typedb_storage_health(configured_settings),
        mailbox_store=stores.ontology_reasoning_mailbox_store(reasoning_store_settings),
        queue_health_service=OntologyReasoningQueueHealthService(
            store=cursor_store,
            settings=configured_settings,
        ),
        projection_coordinator_probe=(
            getattr(ontology_repository, "projection_coordinator_lease_status")
            if callable(getattr(ontology_repository, "projection_coordinator_lease_status", None))
            else None
        ),
        projection_lease_recovery=projection_lease_recovery,
        projection_coordinator_lease_recovery=projection_coordinator_lease_recovery,
        snapshot_readiness_probe=source_snapshot_preflight,
        rulebox_prewarm_probe=(
            getattr(ontology_repository, "schema_function_prewarm_status")
            if callable(getattr(ontology_repository, "schema_function_prewarm_status", None))
            else None
        ),
        operational_settings_refresher=refresh_reasoning_monitor_settings,
        rulebox_prewarm_activity_probe=(
            getattr(rulebox_prewarm_state_store, "load")
            if callable(getattr(rulebox_prewarm_state_store, "load", None))
            else None
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
        reasoning_queue_probe=build_ontology_reasoning_queue_probe(configured_settings),
        settings=configured_settings,
    )


def build_flow_lens_service(settings=None) -> FlowLensService:
    configured_settings = settings or runtime_settings()
    flow_lens_external_settings = dict(configured_settings)
    def capped_int(key: str, fallback: int, cap: int) -> str:
        return str(min(cap, int(number(flow_lens_external_settings.get(key)) or fallback)))

    flow_lens_external_settings["externalApiRetryAttempts"] = "1"
    flow_lens_external_settings["externalApiTimeoutSeconds"] = str(min(2.0, number(flow_lens_external_settings.get("externalApiTimeoutSeconds")) or 2.0))
    flow_lens_external_settings["externalFredTimeoutSeconds"] = str(min(2.0, number(flow_lens_external_settings.get("externalFredTimeoutSeconds")) or 2.0))
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
        market_quote_cache=stores.market_quote_cache(configured_settings),
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
