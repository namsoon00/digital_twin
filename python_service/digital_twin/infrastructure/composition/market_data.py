"""Market Data runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import Callable, Dict, Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.modules.accounts.domain.accounts import AccountConfig
    from digital_twin.modules.market_data.public import (
        ExternalDataCollectionService,
        KISRealtimeWebSocketRunner,
        MarketDataCollectionRunner,
        MonitorRunner,
    )


def monitor_account_job_store_from_settings(settings):
    """Enable durable account scheduling only when it is explicitly configured.

    The monitor runner treats the presence of this store as the queue-mode
    switch. Constructing it unconditionally made ``monitorAccountQueueEnabled``
    ineffective and delayed normal monitor refreshes behind the job cadence.
    """
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.runtime_support import setting_truthy

    configured_settings = dict(settings or {})
    if not setting_truthy(configured_settings.get("monitorAccountQueueEnabled"), default=False):
        return None
    return stores.monitor_account_job_store(configured_settings)


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
    import os
    import uuid
    from digital_twin.modules.market_data.domain.monitoring import RealtimeMonitor
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.events import monitor_event_bus
    from digital_twin.infrastructure.composition.outcomes import build_hypothesis_lifecycle_service
    from digital_twin.infrastructure.composition.runtime_support import setting_truthy
    from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
    from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.infrastructure.statistical_signal_factory import build_statistical_signal_pipeline_service
    from digital_twin.infrastructure.toss_snapshots import build_snapshot
    from digital_twin.modules.market_data.public import MonitorRunner
    from digital_twin.modules.notifications.infrastructure.notification.ingress import send_events
    from digital_twin.modules.outcomes.infrastructure.mysql_outcome_evidence import MySQLOutcomeEvidenceSource
    from digital_twin.modules.outcomes.public import InvestmentOutcomeObservationService
    from digital_twin.modules.portfolio.public import InvestmentDomainService, PortfolioAccountingService

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
    investment_domain_store = stores.investment_domain_store(configured_settings)
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
            investment_domain_store=investment_domain_store,
            world_projection_outbox=stores.ontology_world_projection_outbox_store(configured_settings),
            inference_detail_outbox=stores.ontology_inference_detail_outbox_store(configured_settings),
            graph_assembly_cache_store=stores.ontology_graph_assembly_cache_store(configured_settings),
            statistical_signal_service=build_statistical_signal_pipeline_service(configured_settings),
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
        portfolio_lifecycle_observer=(
            None
            if source_snapshot_replay
            else PortfolioAccountingService(
                investment_domain_store,
                stores.account_reader(configured_settings),
                InvestmentDomainService(investment_domain_store, publisher),
                market_time_series_store,
                configured_settings,
            )
        ),
        investment_outcome_observer=(
            None
            if source_snapshot_replay
            else InvestmentOutcomeObservationService(
                decision_episode_store=stores.investment_decision_episode_store(configured_settings),
                market_time_series_store=market_time_series_store,
                settings=configured_settings,
                investment_domain_store=investment_domain_store,
                outcome_evidence_source=MySQLOutcomeEvidenceSource(configured_settings),
            )
        ),
    )


def build_market_data_collection_runner(settings=None, event_publisher=None) -> MarketDataCollectionRunner:
    from digital_twin.platform.application.data_pipeline_health_service import DataPipelineHealthService
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.events import data_pipeline_health_event_bus
    from digital_twin.infrastructure.composition.instruments import build_symbol_universe_service
    from digital_twin.infrastructure.external_signals import ExternalSignalProvider
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.infrastructure.toss_snapshots import TossProvider
    from digital_twin.modules.market_data.public import MarketDataCollectionRunner

    configured_settings = settings or runtime_settings()
    return MarketDataCollectionRunner(
        account_repository=stores.account_reader(configured_settings),
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


def build_external_data_collection_runner(settings=None) -> ExternalDataCollectionService:
    import os
    from digital_twin.modules.market_data.domain.market_data import number
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.events import news_event_bus
    from digital_twin.infrastructure.disclosure_analyzer import disclosure_analyzer_from_settings
    from digital_twin.infrastructure.external_api.adapters import default_external_dataset_registry
    from digital_twin.infrastructure.external_api.adapters.base import legacy_provider
    from digital_twin.modules.market_data.application.external_data.document_recovery_service import OfficialDocumentRecoveryService
    from digital_twin.infrastructure.external_api.legacy_import import LegacyExternalSignalImporter
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.market_data.public import (
        ExternalDataCollectionService,
        ExternalFactResearchEvidenceReconciler,
        ExternalOfficialEvidenceProjectionService,
    )

    configured_settings = dict(settings or runtime_settings())
    store = stores.external_data_store(configured_settings)
    registry = default_external_dataset_registry(
        configured_settings,
        opendart_corp_code_lookup=store.opendart_corp_code_assignments,
    )
    evidence_projector = ExternalOfficialEvidenceProjectionService(
        fact_store=store,
        evidence_store=stores.research_evidence_store(configured_settings),
        event_publisher=news_event_bus(configured_settings),
        settings=configured_settings,
        disclosure_analyzer=disclosure_analyzer_from_settings(configured_settings),
    )
    return ExternalDataCollectionService(
        settings=configured_settings,
        registry=registry,
        store=store,
        legacy_importer=LegacyExternalSignalImporter(
            stores.external_signal_cache(configured_settings),
            store,
            registry,
            configured_settings,
        ),
        evidence_reconciler=ExternalFactResearchEvidenceReconciler(
            event_reader=stores.event_log(configured_settings),
            projector=evidence_projector,
            cursor_store=stores.external_evidence_projection_state_store(configured_settings),
            batch_size=int(number(configured_settings.get("externalEvidenceProjectionBatchSize")) or 100),
            initial_lookback_minutes=int(number(configured_settings.get("externalEvidenceProjectionInitialLookbackMinutes")) or 10),
            max_replay_age_minutes=int(number(configured_settings.get("externalEvidenceProjectionMaxReplayAgeMinutes")) or 180),
        ),
        worker_id="external-data-" + str(os.getpid()),
        document_recovery=OfficialDocumentRecoveryService(
            configured_settings, evidence_projector.evidence_store.document_recovery_candidates,
            store, registry, evidence_projector,
            access_ready=lambda dataset: legacy_provider(configured_settings).sec_document_access_configured()
            if dataset == "sec.document" else bool(configured_settings.get("opendartApiKey")),
        ),
    )


def build_kis_realtime_websocket_runner(settings=None, event_publisher=None) -> KISRealtimeWebSocketRunner:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.event_bus import default_event_bus
    from digital_twin.infrastructure.kis_realtime_ws import (
        KISRealtimeSymbolSelector,
        KISRealtimeWebSocketClient,
    )
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.market_data.public import KISRealtimeWebSocketRunner

    configured_settings = settings or runtime_settings()
    quote_cache = stores.market_quote_cache(configured_settings)
    monitor_store = stores.monitor_store(configured_settings)
    return KISRealtimeWebSocketRunner(
        client=KISRealtimeWebSocketClient(configured_settings, quote_cache=quote_cache),
        symbol_selector=KISRealtimeSymbolSelector(
            stores.account_reader(configured_settings),
            monitor_store,
            quote_cache,
            configured_settings,
        ),
        quote_cache=quote_cache,
        settings=configured_settings,
        event_publisher=event_publisher or default_event_bus(),
    )
